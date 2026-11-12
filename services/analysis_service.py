"""Persistence service: analysis result snapshots.

A ``ResumeAnalysis`` row stores ONE analysis snapshot for ONE candidate +
ONE resume version: scores, skill gaps and timestamp. Candidate identity and
JD identity live on their own tables and are referenced by id, so no identity
data is duplicated. ``resume_version_id`` pins the snapshot to the exact resume
document that produced it, keeping each version's analysis independent.

Duplicate handling
------------------
If the same resume version is analyzed against the same job description and an
analysis already exists, ``save_analysis`` returns the existing row instead of
inserting a duplicate (so no wasted Groq/embedding work is re-persisted and no
duplicate timeline event is created). Callers that explicitly support
re-analysis can pass ``allow_duplicate=True``.

The ATS/JD/semantic scoring math is never performed here — the service simply
persists whatever the existing pipeline produced.
"""

import logging

from core.db import session_scope
from core.models import Candidate, JobDescription, ResumeAnalysis, ResumeVersion
from services import audit_service
from services.auth_service import can_access

logger = logging.getLogger(__name__)


def _as_list(value):
    return list(value or [])


def _find_existing_analysis(
    session, *, candidate_id, resume_version_id, jd_id
):
    """Latest analysis for the same (candidate, resume version, JD) triple."""
    query = session.query(ResumeAnalysis).filter(
        ResumeAnalysis.candidate_id == candidate_id,
        ResumeAnalysis.resume_version_id == resume_version_id,
    )
    if jd_id is None:
        query = query.filter(ResumeAnalysis.jd_id.is_(None))
    else:
        query = query.filter(ResumeAnalysis.jd_id == jd_id)
    return query.order_by(ResumeAnalysis.created_at.desc()).first()


def save_analysis(
    *,
    candidate_id: int,
    jd_id: int | None = None,
    resume_version_id: int | None = None,
    user: dict | None = None,
    allow_duplicate: bool = False,
    ats_score=None,
    jd_match_score=None,
    final_score=None,
    semantic_match_score=None,
    matched_skills=None,
    missing_required_skills=None,
    missing_preferred_skills=None,
):
    """Insert a new analysis snapshot and return the row (id populated).

    With ``resume_version_id`` provided (the default flow) the analysis is
    deduplicated on (candidate, resume version, JD): an already-existing
    analysis for the same triple is returned and no duplicate row or timeline
    event is written.

    Relationship integrity is enforced on every insert:

    - the candidate must exist (and, when an authenticated ``user`` is given,
      must be visible to that user — cross-owner writes are rejected),
    - a ``resume_version_id`` must belong to that candidate,
    - a ``jd_id`` must reference an existing job description.
    """
    from services import activity_service

    with session_scope() as session:
        candidate = session.get(Candidate, candidate_id)
        if candidate is None:
            raise LookupError(f"No candidate with id {candidate_id}")
        if user is not None and not can_access(user, candidate.created_by_user_id):
            raise PermissionError("You do not have access to this candidate.")

        if resume_version_id is not None:
            version = session.get(ResumeVersion, resume_version_id)
            if version is None:
                raise LookupError(
                    f"No resume version with id {resume_version_id}"
                )
            if version.candidate_id != candidate_id:
                raise LookupError(
                    f"Resume version {resume_version_id} does not belong to "
                    f"candidate {candidate_id}"
                )
            if not allow_duplicate:
                existing = _find_existing_analysis(
                    session,
                    candidate_id=candidate_id,
                    resume_version_id=resume_version_id,
                    jd_id=jd_id,
                )
                if existing is not None:
                    return existing

        if jd_id is not None and session.get(JobDescription, jd_id) is None:
            raise LookupError(f"No job description with id {jd_id}")

        analysis = ResumeAnalysis(
            candidate_id=candidate_id,
            jd_id=jd_id,
            resume_version_id=resume_version_id,
            ats_score=float(ats_score) if ats_score is not None else None,
            jd_match_score=float(jd_match_score) if jd_match_score is not None else None,
            final_score=float(final_score) if final_score is not None else None,
            semantic_match_score=(
                float(semantic_match_score)
                if semantic_match_score is not None
                else None
            ),
            matched_skills=_as_list(matched_skills),
            missing_required_skills=_as_list(missing_required_skills),
            missing_preferred_skills=_as_list(missing_preferred_skills),
        )
        session.add(analysis)
        session.flush()

        # One RESUME_ANALYZED event per new analysis snapshot; the description
        # names the resume version so the timeline surfaces version history.
        # Best-effort so a timeline-logging hiccup can never sink the analysis.
        description = "Resume analyzed"
        if resume_version_id is not None:
            version = session.get(ResumeVersion, resume_version_id)
            if version is not None:
                description = (
                    f"Resume Version {version.version_number} uploaded and analyzed"
                )
        try:
            activity_service.log_activity(
                session,
                candidate_id=candidate_id,
                activity_type="RESUME_ANALYZED",
                description=description,
            )
        except Exception:
            logger.warning(
                "RESUME_ANALYZED activity not recorded for candidate %s", candidate_id
            )
        # System/security audit: same transaction as the analysis insert so a
        # failed CANDIDATE_ANALYZED record rolls the analysis back rather than
        # silently running unaudited. Actor = the candidate's owner (the person
        # whose pipeline analyzed it), NULL for the public/self-analysis flow.
        audit_service.record_audit_event(
            candidate.created_by_user_id,
            audit_service.CANDIDATE_ANALYZED,
            audit_service.RESOURCE_CANDIDATE,
            candidate_id,
            "Candidate analyzed.",
            session=session,
        )
        return analysis


def save_analysis_from_results(
    *,
    candidate_id: int,
    jd_id: int | None,
    resume_version_id: int | None = None,
    user: dict | None = None,
    allow_duplicate: bool = False,
    ats_result=None,
    jd_match=None,
    final_score=None,
):
    """Persist from the existing ``ATSScoreResult`` + ``JDMatchResult`` objects.

    Exactly mirrors how the recruiter pipeline surfaces scores; no scoring
    happens here.
    """
    ats_score = None
    if ats_result is not None:
        ats_score = getattr(ats_result, "overall_score", None)

    jd_match_score = None
    semantic_match_score = None
    matched_skills = []
    missing_required = []
    missing_preferred = []
    if jd_match is not None:
        jd_match_score = getattr(jd_match, "overall_match_score", None)
        semantic_match_score = getattr(jd_match, "semantic_match_score", None)
        matched_skills = getattr(jd_match, "matched_skills", []) or []
        missing_required = getattr(jd_match, "missing_required_skills", []) or []
        missing_preferred = getattr(jd_match, "missing_preferred_skills", []) or []

    if final_score is None:
        ats = ats_score if ats_score is not None else 0.0
        jd = jd_match_score if jd_match_score is not None else 0.0
        final_score = round(ats * 0.4 + jd * 0.6, 2)

    return save_analysis(
        candidate_id=candidate_id,
        jd_id=jd_id,
        resume_version_id=resume_version_id,
        user=user,
        allow_duplicate=allow_duplicate,
        ats_score=ats_score,
        jd_match_score=jd_match_score,
        final_score=final_score,
        semantic_match_score=semantic_match_score,
        matched_skills=matched_skills,
        missing_required_skills=missing_required,
        missing_preferred_skills=missing_preferred,
    )


def get_latest_analysis(candidate_id: int, resume_version_id: int | None = None):
    """Most recent analysis snapshot for a candidate, or ``None``.

    With ``resume_version_id`` set, returns the latest analysis for THAT resume
    version only — so Version 1 always shows Version 1's scores, never the
    current version's.
    """
    with session_scope() as session:
        query = session.query(ResumeAnalysis).filter(
            ResumeAnalysis.candidate_id == candidate_id
        )
        if resume_version_id is not None:
            query = query.filter(
                ResumeAnalysis.resume_version_id == resume_version_id
            )
        return query.order_by(ResumeAnalysis.created_at.desc()).first()


def get_analyses(
    candidate_id: int,
    *,
    jd_id: int | None = None,
    resume_version_id: int | None = None,
    include_legacy_unversioned: bool = False,
):
    """All analysis snapshots for a candidate, newest first.

    Optionally scoped to a job description and/or a resume version. Legacy
    rows that predate versioning carry ``resume_version_id = NULL``; they are
    excluded by default when version-scoping so a historical version never
    accidentally shows a newer/older unversioned analysis. Pass
    ``include_legacy_unversioned=True`` to mix them back in (e.g. the default,
    per-candidate history view).
    """
    with session_scope() as session:
        query = session.query(ResumeAnalysis).filter(
            ResumeAnalysis.candidate_id == candidate_id
        )
        if jd_id is not None:
            query = query.filter(ResumeAnalysis.jd_id == jd_id)
        if resume_version_id is not None:
            query = query.filter(
                ResumeAnalysis.resume_version_id == resume_version_id
            )
        elif not include_legacy_unversioned:
            query = query.filter(ResumeAnalysis.resume_version_id.isnot(None))
        return query.order_by(ResumeAnalysis.created_at.desc()).all()