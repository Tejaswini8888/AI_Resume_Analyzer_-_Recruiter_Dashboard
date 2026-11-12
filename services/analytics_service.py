"""Persistent, database-derived analytics for the Recruiter Analytics page.

All metrics are computed from persisted records (``Candidate``,
``ResumeAnalysis``, ``CandidateActivity``) using SQLAlchemy aggregation. There
is NO session-state data here: this service is the single source of truth for
recruiter KPIs, and the page never runs its own SQL.

Scope
-----
Every metric accepts two optional filters:

- ``jd_id``  — scope to candidates analyzed against ONE persisted job
  description, so the Backend pool and the ML pool never mix.
- ``since``  — a naive-UTC cutoff (timestamps are stored as aware ``utcnow()``
  but SQLite persists them as naive UTC strings); filters ``created_at`` on the
  persisted analysis / activity rows. ``None`` = all time.

Status model
------------
- ``Candidates Analyzed``       == distinct candidates with a persisted
  ``ResumeAnalysis`` row (optionally scoped).
- Pipeline status counts        == ``Candidate.status`` (GROUP BY), scoped to
  candidates that have an analysis row inside the scope.
- Averages                      == computed over each candidate's LATEST
  analysis snapshot, so a candidate analyzed repeatedly never skews the pool.
- Skills                        == normalized through
  ``modules.skills.normalize_skill`` so aliases ("Postgres" / "PostgreSQL",
  "reactjs" / "React") collapse to one canonical label.

Ownership (Phase 4)
-------------------
Every metric accepts ``user`` (an ``auth_service`` user dict): a RECRUITER only
sees analytics over candidates they created plus the legacy ``NULL``-owner
system pool; an ADMIN (or ``None`` for legacy callers) sees everything. Scope is
applied through ``Candidate.created_by_user_id`` at the query layer — pages never
post-filter.

The functions return plain dicts so the UI stays thin and testable.
"""

import datetime
from collections import Counter

from sqlalchemy import func

from core.db import session_scope
from core.models import ACTIVITY_TYPES, Candidate, CandidateActivity, JobDescription, ResumeAnalysis
from modules.skills import normalize_skill
from services.auth_service import owner_scope

# Successful progression order for the pipeline overview (Rejected is separate).
PIPELINE_ORDER = ("New", "Analyzed", "Shortlisted", "Interview", "Selected")

TIME_RANGES = {
    "All Time": None,
    "Last 7 Days": 7,
    "Last 30 Days": 30,
    "Last 90 Days": 90,
}

ACTIVITY_LABELS = {
    "RESUME_ANALYZED": "Analyzed",
    "SHORTLISTED": "Shortlisted",
    "REMOVED_FROM_SHORTLIST": "Removed from shortlist",
    "MOVED_TO_INTERVIEW": "Moved to Interview",
    "MARKED_SELECTED": "Marked Selected",
    "MARKED_REJECTED": "Marked Rejected",
    "STATUS_CHANGED": "Status Changed",
}

# Bucket label for each controlled activity type used in the "recent" summary.
_ACTIVITY_BUCKET = {
    "RESUME_ANALYZED": "analyzed",
    "SHORTLISTED": "shortlisted",
    "REMOVED_FROM_SHORTLIST": "removed_from_shortlist",
    "MOVED_TO_INTERVIEW": "moved_to_interview",
    "MARKED_SELECTED": "marked_selected",
    "MARKED_REJECTED": "marked_rejected",
    "STATUS_CHANGED": "status_changed",
}


def cutoff_for_days(days: int | None) -> datetime.datetime | None:
    """Naive-UTC cutoff ``days`` ago (None when unset = all time)."""
    if not days:
        return None
    return (
        datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        - datetime.timedelta(days=int(days))
    )


def _latest_analyses(*, jd_id=None, since=None, user=None):
    """Latest analysis snapshot per candidate, within scope.

    Returns ``(ordered_rows, latest_by_candidate)`` where ``latest_by_candidate``
    maps candidate_id -> the newest analysis row. Rows are SQLAlchemy ``Row``
    tuples fetched with only the fields the analytics need.
    """
    cols = (
        ResumeAnalysis.id,
        ResumeAnalysis.candidate_id,
        ResumeAnalysis.ats_score,
        ResumeAnalysis.jd_match_score,
        ResumeAnalysis.final_score,
        ResumeAnalysis.created_at,
        ResumeAnalysis.matched_skills,
        ResumeAnalysis.missing_required_skills,
        ResumeAnalysis.missing_preferred_skills,
    )
    with session_scope() as session:
        query = (
            session.query(*cols)
            .join(Candidate, Candidate.id == ResumeAnalysis.candidate_id)
            .order_by(
                ResumeAnalysis.created_at.desc(),
                ResumeAnalysis.id.desc(),
            )
        )
        scope = owner_scope(Candidate, user)
        if scope is not None:
            query = query.filter(scope)
        if jd_id is not None:
            query = query.filter(ResumeAnalysis.jd_id == jd_id)
        if since is not None:
            query = query.filter(ResumeAnalysis.created_at >= since)
        rows = query.all()
    latest = {}
    for row in rows:
        latest.setdefault(row.candidate_id, row)
    return rows, latest


def _scoped_status_counts(*, jd_id=None, since=None, user=None):
    """Current status counts, scoped to candidates with an analysis inside scope.

    Unscoped (all time, all JDs) == the raw ``Candidate.status`` GROUP BY, so
    brand-new (never analyzed) candidates still appear in the "New" bucket.
    """
    with session_scope() as session:
        scope = owner_scope(Candidate, user)

        if jd_id is None and since is None:
            query = session.query(Candidate.status, func.count(Candidate.id))
            if scope is not None:
                query = query.filter(scope)
            rows = query.group_by(Candidate.status).all()
            return {status: int(count) for status, count in rows}

        query = (
            session.query(Candidate.status, func.count(func.distinct(Candidate.id)))
            .join(ResumeAnalysis, ResumeAnalysis.candidate_id == Candidate.id)
        )
        if scope is not None:
            query = query.filter(scope)
        if jd_id is not None:
            query = query.filter(ResumeAnalysis.jd_id == jd_id)
        if since is not None:
            query = query.filter(ResumeAnalysis.created_at >= since)
        rows = query.group_by(Candidate.status).all()
    return {status: int(count) for status, count in rows}


def _scoped_analyzed_count(*, jd_id=None, since=None, user=None):
    """Distinct candidates with at least one persisted analysis row in scope."""
    with session_scope() as session:
        query = session.query(func.count(func.distinct(ResumeAnalysis.candidate_id))).join(
            Candidate, Candidate.id == ResumeAnalysis.candidate_id
        )
        scope = owner_scope(Candidate, user)
        if scope is not None:
            query = query.filter(scope)
        if jd_id is not None:
            query = query.filter(ResumeAnalysis.jd_id == jd_id)
        if since is not None:
            query = query.filter(ResumeAnalysis.created_at >= since)
        return int(query.scalar() or 0)


def _scoped_analysis_row_count(*, jd_id=None, since=None, user=None):
    with session_scope() as session:
        query = session.query(func.count(ResumeAnalysis.id)).join(
            Candidate, Candidate.id == ResumeAnalysis.candidate_id
        )
        scope = owner_scope(Candidate, user)
        if scope is not None:
            query = query.filter(scope)
        if jd_id is not None:
            query = query.filter(ResumeAnalysis.jd_id == jd_id)
        if since is not None:
            query = query.filter(ResumeAnalysis.created_at >= since)
        return int(query.scalar() or 0)


def _norm_skill(value: str | None):
    """Canonical form of a persisted skill label (None-safe)."""
    if not value:
        return None
    return normalize_skill(value) or str(value).strip()


# ---------------------------------------------------------------------------
# KPI / pipeline
# ---------------------------------------------------------------------------
def get_pipeline_metrics(*, jd_id=None, since=None, user=None) -> dict:
    """Status distribution + "Candidates Analyzed" KPI from the database."""
    with session_scope() as session:
        total_query = session.query(func.count(Candidate.id))
        scope = owner_scope(Candidate, user)
        if scope is not None:
            total_query = total_query.filter(scope)
        total_candidates = int(total_query.scalar() or 0)

    status_counts = {s: 0 for s in ("New", "Analyzed", "Shortlisted", "Interview", "Selected", "Rejected")}
    status_counts.update(_scoped_status_counts(jd_id=jd_id, since=since, user=user))
    analyzed = _scoped_analyzed_count(jd_id=jd_id, since=since, user=user)
    pool_total = sum(status_counts.values())

    return {
        "status_counts": status_counts,
        "analyzed": analyzed,
        "total_candidates": total_candidates,
        "pool_total": pool_total if (jd_id is not None or since is not None) else total_candidates,
        "has_data": pool_total > 0 or total_candidates > 0,
    }


# ---------------------------------------------------------------------------
# Score insights
# ---------------------------------------------------------------------------
def get_score_metrics(*, jd_id=None, since=None, user=None) -> dict:
    """Average ATS / JD match / final score over each candidate's latest analysis."""
    _rows, latest = _latest_analyses(jd_id=jd_id, since=since, user=user)

    def avg(key):
        values = [float(getattr(row, key)) for row in latest.values() if getattr(row, key) is not None]
        return round(sum(values) / len(values), 1) if values else None

    finals = [float(r.final_score) for r in latest.values() if r.final_score is not None]
    return {
        "candidate_count": len(latest),
        "avg_ats": avg("ats_score"),
        "avg_jd_match": avg("jd_match_score"),
        "avg_final": avg("final_score"),
        "highest_final": round(max(finals), 1) if finals else None,
        "lowest_final": round(min(finals), 1) if finals else None,
        "has_score_data": bool(latest),
    }


# ---------------------------------------------------------------------------
# Skill insights (canonical, alias-folded)
# ---------------------------------------------------------------------------
def get_skill_insights(*, jd_id=None, since=None, top_n: int = 10, user=None) -> dict:
    """Most common matched skills and most common gaps across the pool.

    Gaps = missing required + missing preferred skills from the persisted
    analysis records, both folded to canonical labels.
    """
    _rows, latest = _latest_analyses(jd_id=jd_id, since=since, user=user)

    matched = Counter()
    gaps = Counter()
    for row in latest.values():
        for skill in row.matched_skills or []:
            canonical = _norm_skill(skill)
            if canonical:
                matched[canonical] += 1
        for skill in list(row.missing_required_skills or []) + list(row.missing_preferred_skills or []):
            canonical = _norm_skill(skill)
            if canonical:
                gaps[canonical] += 1

    return {
        "top_skills": matched.most_common(top_n),
        "top_gaps": gaps.most_common(top_n),
        "skill_count": sum(matched.values()),
        "gap_count": sum(gaps.values()),
        "has_skill_data": bool(matched) or bool(gaps),
    }


# ---------------------------------------------------------------------------
# Recruitment activity (from the immutable activity timeline)
# ---------------------------------------------------------------------------
def get_recruitment_activity(
    *, since=None, limit: int = 8, user=None, jd_id=None
) -> dict:
    """Summarized recruitment activity from ``CandidateActivity`` rows.

    When ``jd_id`` is set, only activity for candidates that were analyzed
    against that job description is counted and shown, so a scoped analytics
    view never mixes another role's activity in.
    """
    buckets = ["analyzed", "shortlisted", "removed_from_shortlist", "moved_to_interview",
               "marked_selected", "marked_rejected", "status_changed"]
    counts = {b: 0 for b in buckets}

    # Candidate ids that have an analysis inside the JD scope (per-query filter).
    from sqlalchemy import select

    jd_candidate_ids = None
    if jd_id is not None:
        jd_candidate_ids = select(ResumeAnalysis.candidate_id).where(
            ResumeAnalysis.jd_id == jd_id
        )

    with session_scope() as session:
        scope = owner_scope(Candidate, user)

        type_counts = (
            session.query(CandidateActivity.activity_type, func.count(CandidateActivity.id))
            .join(Candidate, Candidate.id == CandidateActivity.candidate_id)
        )
        if scope is not None:
            type_counts = type_counts.filter(scope)
        if since is not None:
            type_counts = type_counts.filter(CandidateActivity.created_at >= since)
        if jd_candidate_ids is not None:
            type_counts = type_counts.filter(
                CandidateActivity.candidate_id.in_(jd_candidate_ids)
            )
        for activity_type, count in type_counts.group_by(CandidateActivity.activity_type).all():
            bucket = _ACTIVITY_BUCKET.get(activity_type)
            if bucket:
                counts[bucket] = int(count)

        analyzed_distinct = (
            session.query(func.count(func.distinct(CandidateActivity.candidate_id)))
            .join(Candidate, Candidate.id == CandidateActivity.candidate_id)
        )
        if scope is not None:
            analyzed_distinct = analyzed_distinct.filter(scope)
        if since is not None:
            analyzed_distinct = analyzed_distinct.filter(
                CandidateActivity.created_at >= since
            )
        if jd_candidate_ids is not None:
            analyzed_distinct = analyzed_distinct.filter(
                CandidateActivity.candidate_id.in_(jd_candidate_ids)
            )
        counts["analyzed"] = int(
            analyzed_distinct.filter(
                CandidateActivity.activity_type == "RESUME_ANALYZED"
            ).scalar()
            or 0
        )

        recent_query = (
            session.query(
                CandidateActivity.id,
                CandidateActivity.candidate_id,
                CandidateActivity.activity_type,
                CandidateActivity.description,
                CandidateActivity.created_at,
                Candidate.name,
            )
            .outerjoin(Candidate, Candidate.id == CandidateActivity.candidate_id)
        )
        if scope is not None:
            recent_query = recent_query.filter(scope)
        if since is not None:
            recent_query = recent_query.filter(CandidateActivity.created_at >= since)
        if jd_candidate_ids is not None:
            recent_query = recent_query.filter(
                CandidateActivity.candidate_id.in_(jd_candidate_ids)
            )
        recent = (
            recent_query.order_by(CandidateActivity.created_at.desc(), CandidateActivity.id.desc())
            .limit(limit)
            .all()
        )

    recent_list = [
        {
            "candidate_id": row.candidate_id,
            "candidate_name": row.name or f"Candidate #{row.candidate_id}",
            "activity_type": row.activity_type,
            "label": ACTIVITY_LABELS.get(row.activity_type, row.activity_type),
            "description": row.description,
            "created_at": row.created_at,
        }
        for row in recent
    ]

    return {
        "counts": counts,
        "recent": recent_list,
        "has_activity": any(counts.values()) or bool(recent_list),
    }


# ---------------------------------------------------------------------------
# Candidate statistics
# ---------------------------------------------------------------------------
def get_candidate_statistics(*, jd_id=None, since=None, user=None) -> dict:
    """Pool size, analysis coverage, and average snapshots per candidate."""
    analyzed = _scoped_analyzed_count(jd_id=jd_id, since=since, user=user)
    analysis_rows = _scoped_analysis_row_count(jd_id=jd_id, since=since, user=user)
    return {
        "total_candidates": analyzed if (jd_id is not None or since is not None)
        else _scoped_total_candidates(user=user),
        "analyzed_candidates": analyzed,
        "total_analysis_rows": analysis_rows,
        "avg_analyses_per_candidate": (
            round(analysis_rows / analyzed, 2) if analyzed else 0.0
        ),
    }


def _scoped_total_candidates(user=None):
    with session_scope() as session:
        query = session.query(func.count(Candidate.id))
        scope = owner_scope(Candidate, user)
        if scope is not None:
            query = query.filter(scope)
        return int(query.scalar() or 0)


# ---------------------------------------------------------------------------
# JD list for the analytics filter
# ---------------------------------------------------------------------------
def get_job_description_options(user=None) -> list[dict]:
    """Persisted job descriptions visible to ``user`` with analysis counts,
    newest first.

    Analysis counts are scoped to candidates visible to ``user``, so a
    recruiter never sees another recruiter's workload numbers attached to a JD
    (and legacy shared JDs only carry counts from candidates the recruiter can
    already see).
    """
    with session_scope() as session:
        jds_query = session.query(JobDescription)
        scope = owner_scope(JobDescription, user)
        if scope is not None:
            jds_query = jds_query.filter(scope)
        jds = jds_query.order_by(
            JobDescription.created_at.desc(), JobDescription.id.desc()
        ).all()

        counts_q = (
            session.query(ResumeAnalysis.jd_id, func.count(ResumeAnalysis.id))
            .join(Candidate, Candidate.id == ResumeAnalysis.candidate_id)
            .filter(ResumeAnalysis.jd_id.isnot(None))
        )
        cand_scope = owner_scope(Candidate, user)
        if cand_scope is not None:
            counts_q = counts_q.filter(cand_scope)
        counts = dict(counts_q.group_by(ResumeAnalysis.jd_id).all())
    return [
        {
            "id": jd.id,
            "title": jd.title,
            "preview": (jd.description or "").strip(),
            "analysis_count": int(counts.get(jd.id, 0)),
        }
        for jd in jds
    ]


def _global_has_data(user=None) -> bool:
    with session_scope() as session:
        scope = owner_scope(Candidate, user)

        cands_q = session.query(func.count(Candidate.id))
        if scope is not None:
            cands_q = cands_q.filter(scope)

        analyses_q = session.query(func.count(ResumeAnalysis.id)).join(
            Candidate, Candidate.id == ResumeAnalysis.candidate_id
        )
        if scope is not None:
            analyses_q = analyses_q.filter(scope)

        activities_q = session.query(func.count(CandidateActivity.id)).join(
            Candidate, Candidate.id == CandidateActivity.candidate_id
        )
        if scope is not None:
            activities_q = activities_q.filter(scope)

        cands = int(cands_q.scalar() or 0)
        analyses = int(analyses_q.scalar() or 0)
        activities = int(activities_q.scalar() or 0)
    return cands > 0 or analyses > 0 or activities > 0


# ---------------------------------------------------------------------------
# One-call bundle for the analytics page (a handful of SQL aggregations)
# ---------------------------------------------------------------------------
def get_analytics_bundle(*, jd_id=None, days: int | None = None, user=None) -> dict:
    """Everything the Recruiter Analytics page needs in one call per rerun.

    The page calls this ONCE per rerun — no permanent cache, so analytics
    always reflect the latest candidate/status/activity writes. Queries here
    are database aggregations, not full-record loads. ``user`` restricts every
    metric to the recruiter's owned + legacy records.
    """
    since = cutoff_for_days(days)
    return {
        "has_data": _global_has_data(user=user),
        "pipeline": get_pipeline_metrics(jd_id=jd_id, since=since, user=user),
        "scores": get_score_metrics(jd_id=jd_id, since=since, user=user),
        "skills": get_skill_insights(jd_id=jd_id, since=since, user=user),
        "activity": get_recruitment_activity(since=since, user=user, jd_id=jd_id),
        "stats": get_candidate_statistics(jd_id=jd_id, since=since, user=user),
    }