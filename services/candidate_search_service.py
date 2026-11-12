"""Persistence service: recruiter candidate search & discovery.

Recruiters search the persisted database (``Candidate`` + ``ResumeAnalysis``)
instead of only the current analysis session. The database is the source of
truth: every result row is recomputed from the current rows on every call —
there is no session-state pool and no cache.

Query strategy
--------------
Everything that SQLite can do is pushed into SQLAlchemy:

- name / email / resume filename  -> ``ILIKE`` per token (case-insensitive)
- recruitment status             -> ``Candidate.status`` equality
- job description                -> ``IN`` over persisted analyses for a JD
- score ranges                   -> compared against the candidate's LATEST
  analysis snapshot (window function ``ROW_NUMBER()`` picks the newest row per
  candidate, tie-broken by id). When a job description is selected the window
  is SCOPED to analyses for that JD (matching the analytics service), so the
  displayed/sorted scores and the min-score filters reflect the chosen role
  rather than an unrelated newer analysis.
- sorting + pagination           -> ``ORDER BY`` + ``OFFSET/LIMIT`` in SQL

Limitation (documented)
-----------------------
Persisted skills live in a JSON column that SQLite cannot index, and matching
must fold aliases ("Postgres"/"PostgreSQL", "reactjs"/"React", "Python 3"/
"Python3") through the centralized canonical skill system. A full schema
rewrite is out of scope, so skill matching loads ONLY the minimal
``candidate_id + matched_skills`` projection for the candidate ids that all
SQL-able filters already narrowed to, then normalizes each stored skill and
compares canonical labels exactly. This keeps Java != JavaScript and C !=
Cloud/CSS (exact canonical equality, never raw substring matching) while the
load is bounded by the already-filtered pool, not the whole table.

Skill semantics
---------------
- A candidate's persisted skill set = the ``matched_skills`` list across their
  ``ResumeAnalysis`` rows (scoped to the selected JD when one is chosen).
- Search "python" matches any candidate whose persisted skill set contains a
  spelling that normalizes to the canonical "Python".
- The explicit skill filter accepts a canonical label (from the same dataset).

Other notes
-----------
- Score-Eligible (latest final score >= ``score_eligible_min()``) is a computed,
  display-only property — it is NEVER a status and it never changes a
  candidate's status. Shortlisted remains a recruiter decision.
- Candidates are only ever created through the analysis flow, so the search
  pool equals "candidates with at least one persisted analysis".
- Status updates from search results reuse :mod:`services.recruitment_service`
  (one status-management system), which keeps creating activity history.
"""

import re

from sqlalchemy import and_, func, or_

from core.config import settings
from core.db import session_scope
from core.models import (
    RECRUITMENT_STATUSES,
    Candidate,
    JobDescription,
    ResumeAnalysis,
    ResumeVersion,
)
from modules.skills import canonical_skill
from services.auth_service import can_access, owner_scope

# Controls how many candidates are shown per page. Safe limit first; the
# service is structured (``offset/limit`` in SQL) so true pagination already
# works and can be surfaced with larger page sizes later.
PAGE_SIZE = 25

SORT_OPTIONS = (
    ("final_desc", "Final Score — High to Low"),
    ("ats_desc", "ATS Score — High to Low"),
    ("jd_desc", "JD Match — High to Low"),
    ("newest_analysis", "Newest Analysis"),
    ("recently_updated", "Recently Updated"),
)
DEFAULT_SORT = "final_desc"
_VALID_SORTS = {key for key, _label in SORT_OPTIONS}

_STATUS_BY_VALUE = {s: s for s in RECRUITMENT_STATUSES}
_SORT_BY_LABEL = {label: key for key, label in SORT_OPTIONS}

_TOKEN_RE = re.compile(r"[^a-z0-9.\-]+")


def score_eligible_min() -> float:
    """Display-only score threshold for the "Score-Eligible" tag.

    Resolved from centralized configuration on every call, so a change to
    ``RECRUITER_SCORE_THRESHOLD`` (or a settings reload) takes effect for new
    requests without a process restart.
    """
    return float(settings.recruiter_score_threshold)


def _score_eligible(final_score) -> bool:
    return bool(
        final_score is not None and float(final_score) >= score_eligible_min()
    )


def _jd_visible(user: dict | None, owner_id: int | None) -> bool:
    """Access check for a JobDescription row."""
    return can_access(user, owner_id)


def _skill_key(term):
    """Exact canonical identity for a persisted/search skill, or the lowered
    spelling of the raw term when the dataset knows no canonical for it.

    Uses :func:`modules.skills.canonical_skill` — EXACT dataset matching with
    NO substring folding — so "Cloud" never collapses to "C", "CSS" never to
    "C", and "JavaScript" never to "Java". Unknown-but-identical spellings
    still compare equal to themselves (case-insensitively).
    """
    if not term:
        return None
    canonical = canonical_skill(term)
    if canonical:
        return canonical
    return re.sub(r"\s+", " ", str(term).strip().lower())


def get_status_options() -> list[str]:
    """Filter options: "All" plus every controlled recruitment status."""
    return ["All", *RECRUITMENT_STATUSES]


def get_sort_options() -> list[str]:
    """Human labels for the sort menu, in the order of ``SORT_OPTIONS``."""
    return [label for _key, label in SORT_OPTIONS]


def sort_key_for_label(label: str | None) -> str:
    """Map a sort label (from the UI selectbox) back to its key."""
    if not label:
        return DEFAULT_SORT
    return _SORT_BY_LABEL.get(label, DEFAULT_SORT)


def get_skill_options(user: dict | None = None) -> list[str]:
    """Distinct canonical skills present in analyses visible to ``user``, sorted.

    Derived from the database (source of truth) and folded through the exact
    canonical mapping (``canonical_skill``) so stored aliases collapse to the
    same canonical label while unsafe substring folds ("Cloud" -> "C") never
    happen. Duplicate spellings of the same canonical are collapsed.
    """
    with session_scope() as session:
        query = session.query(ResumeAnalysis.matched_skills).join(
            Candidate, Candidate.id == ResumeAnalysis.candidate_id
        )
        scope = owner_scope(Candidate, user)
        if scope is not None:
            query = query.filter(scope)
        rows = query.all()
    seen = {}
    for (skills,) in rows:
        for raw in skills or []:
            if not _skill_key(raw):
                continue
            display = canonical_skill(raw) or str(raw).strip()
            if display and _skill_key(display) not in seen:
                seen[_skill_key(display)] = display
    return [seen[k] for k in sorted(seen, key=str.lower)]


def get_job_description_options(user: dict | None = None) -> list[dict]:
    """Scope options: persisted job descriptions visible to ``user`` with
    analysis counts."""
    from services.analytics_service import get_job_description_options as _opts

    return _opts(user=user)


def get_candidate_pool(
    *,
    jd_id=None,
    status=None,
    limit: int = 500,
    user: dict | None = None,
) -> list[dict]:
    """Lightweight candidate pool (id/name/status) visible to ``user``."""
    with session_scope() as session:
        query = session.query(Candidate.id, Candidate.name, Candidate.status).join(
            ResumeAnalysis, ResumeAnalysis.candidate_id == Candidate.id
        )
        scope = owner_scope(Candidate, user)
        if scope is not None:
            query = query.filter(scope)
        if jd_id is not None:
            query = query.filter(ResumeAnalysis.jd_id == jd_id)
        if status and status != "All":
            query = query.filter(Candidate.status == _STATUS_BY_VALUE[status])
        rows = (
            query.order_by(Candidate.updated_at.desc())
            .limit(limit)
            .all()
        )
    return [{"id": r.id, "name": r.name, "status": r.status} for r in rows]


# ---------------------------------------------------------------------------
# Internal query building
# ---------------------------------------------------------------------------
def _latest_analysis_subquery():
    """Latest analysis snapshot per candidate via ``ROW_NUMBER()``.

    Window is ordered by ``(created_at DESC, id DESC)`` — the same "most recent
    analysis" definition the rest of the app uses (``get_latest_analysis``).
    """
    return (
        func.row_number()
        .over(
            partition_by=ResumeAnalysis.candidate_id,
            order_by=(ResumeAnalysis.created_at.desc(), ResumeAnalysis.id.desc()),
        )
        .label("rn")
    )


def _latest_columns():
    return (
        ResumeAnalysis.id.label("analysis_id"),
        ResumeAnalysis.candidate_id.label("candidate_id"),
        ResumeAnalysis.ats_score.label("ats_score"),
        ResumeAnalysis.jd_match_score.label("jd_match_score"),
        ResumeAnalysis.final_score.label("final_score"),
        ResumeAnalysis.created_at.label("analysis_created_at"),
        ResumeAnalysis.matched_skills.label("matched_skills"),
    )


def _sql_filtered_ids(
    session, *, status, jd_id, user: dict | None = None
) -> set[int]:
    """Candidate ids with >=1 analysis, narrowed by status/JD + ownership (SQL)."""
    query = (
        session.query(Candidate.id)
        .join(ResumeAnalysis, ResumeAnalysis.candidate_id == Candidate.id)
    )
    scope = owner_scope(Candidate, user)
    if scope is not None:
        query = query.filter(scope)
    if status and status != "All":
        query = query.filter(Candidate.status == _STATUS_BY_VALUE[status])
    if jd_id is not None:
        query = query.filter(ResumeAnalysis.jd_id == jd_id)
    return {cid for (cid,) in query.all()}


def _text_matching_ids(session, pool_ids, tokens) -> set[int]:
    """Candidates in ``pool_ids`` whose name/email/filename contains a token."""
    conds = []
    for token in tokens:
        cols = [Candidate.name, Candidate.email, Candidate.resume_filename]
        conds.append(or_(*[col.ilike(f"%{token}%") for col in cols]))
    query = (
        session.query(Candidate.id)
        .filter(Candidate.id.in_(pool_ids), or_(*conds))
    )
    return {cid for (cid,) in query.all()}


def _pool_ids_with_skill(session, pool_ids, canonicals, *, jd_id=None) -> set[int]:
    """Candidates in ``pool_ids`` whose persisted skill set contains a canonical.

    Loads only the ``(candidate_id, matched_skills)`` projection for the id
    list already narrowed by SQL filters — never the whole table. When a job
    description is selected, only skill sets recorded in that JD's analyses are
    considered, so a skill from an unrelated role never leaks into the match.
    """
    if not pool_ids:
        return set()
    query = (
        session.query(Candidate.id, ResumeAnalysis.matched_skills)
        .join(ResumeAnalysis, ResumeAnalysis.candidate_id == Candidate.id)
        .filter(Candidate.id.in_(pool_ids))
    )
    if jd_id is not None:
        query = query.filter(ResumeAnalysis.jd_id == jd_id)
    rows = query.all()
    matching = set()
    for cid, skills in rows:
        skills = skills or []
        normalized = {key for key in (_skill_key(raw) for raw in skills) if key}
        if normalized & canonicals:
            matching.add(cid)
    return matching


def _skill_token_ids(session, pool_ids, tokens, full_query, *, jd_id=None) -> set[int]:
    """Candidates whose persisted skill set matches a searched skill token."""
    canonicals = set()
    for token in tokens:
        key = _skill_key(token)
        if key:
            canonicals.add(key)
    full = (full_query or "").strip()
    if full:
        key = _skill_key(full)
        if key:
            canonicals.add(key)
    if not canonicals:
        return set()
    return _pool_ids_with_skill(session, pool_ids, canonicals, jd_id=jd_id)


def search_candidates(
    *,
    query: str | None = None,
    status: str | None = None,
    skill: str | None = None,
    jd_id: int | None = None,
    min_ats: float = 0,
    min_jd: float = 0,
    min_final: float = 0,
    sort: str | None = None,
    page: int = 1,
    per_page: int = PAGE_SIZE,
    user: dict | None = None,
) -> dict:
    """Search the persisted candidate database.

    Returns a dict with ``results`` (list of row dicts, already sorted and
    paginated), ``total`` and ``total_pages`` (matching result count BEFORE
    pagination), and ``total_candidates`` (unfiltered candidates visible to
    ``user`` with an analysis). All filters combine with AND except the
    free-text token which matches name/email/filename OR persisted skills.
    ``user`` (an ``auth_service`` dict) restricts the pool to the recruiter's
    own records + legacy shared (``NULL``-owner) records.
    """
    page = max(1, int(page or 1))
    per_page = max(1, int(per_page or PAGE_SIZE))
    sort = sort or DEFAULT_SORT
    sort = sort if sort in _VALID_SORTS else DEFAULT_SORT

    raw = (query or "").strip()
    full_query = raw
    tokens = [
        t for t in _TOKEN_RE.split(raw.lower()) if len(t) >= 2
    ] if raw else []

    with session_scope() as session:
        scope = owner_scope(Candidate, user)
        total_count = session.query(func.count(Candidate.id))
        if scope is not None:
            total_count = total_count.filter(scope)
        total_candidates = int(total_count.scalar() or 0)

        if total_candidates == 0:
            return {
                "results": [],
                "total": 0,
                "page": page,
                "per_page": per_page,
                "total_pages": 0,
                "total_candidates": 0,
                "has_candidates": False,
                "has_results": False,
            }

        pool = _sql_filtered_ids(
            session, status=status, jd_id=jd_id, user=user
        )

        # Free-text: name/email/filename OR a searched skill token.
        if tokens:
            text_ids = _text_matching_ids(session, pool, tokens)
            skill_ids = _skill_token_ids(
                session, pool, tokens, full_query, jd_id=jd_id
            )
            pool = text_ids | skill_ids

        # Explicit canonical skill filter (combines with everything else).
        if skill:
            canonical = _skill_key(skill)
            if canonical:
                pool = _pool_ids_with_skill(
                    session, pool, {canonical}, jd_id=jd_id
                )

        if not pool:
            return {
                "results": [],
                "total": 0,
                "page": page,
                "per_page": per_page,
                "total_pages": 0,
                "total_candidates": total_candidates,
                "has_candidates": True,
                "has_results": False,
            }

        # Latest-analysis window + score ranks in SQL.
        window_query = session.query(
            *_latest_columns(),
            _latest_analysis_subquery(),
        ).filter(ResumeAnalysis.candidate_id.in_(pool))
        if jd_id is not None:
            window_query = window_query.filter(ResumeAnalysis.jd_id == jd_id)
        window = window_query.subquery()
        # The candidate's current (highest-numbered) resume version — a document
        # property, so it is always derived at candidate level regardless of the
        # selected JD scope.
        latest_version_subq = (
            session.query(func.max(ResumeVersion.version_number))
            .filter(ResumeVersion.candidate_id == Candidate.id)
            .correlate(Candidate)
            .scalar_subquery()
            .label("latest_version")
        )
        query = (
            session.query(
                Candidate.id.label("cid"),
                Candidate.name,
                Candidate.email,
                Candidate.resume_filename,
                Candidate.status,
                Candidate.created_at,
                Candidate.updated_at,
                window.c.analysis_id,
                window.c.ats_score,
                window.c.jd_match_score,
                window.c.final_score,
                window.c.analysis_created_at,
                window.c.matched_skills,
                latest_version_subq,
            )
            .join(
                window,
                and_(
                    window.c.candidate_id == Candidate.id,
                    window.c.rn == 1,
                ),
            )
        )
        if min_ats:
            query = query.filter(window.c.ats_score >= float(min_ats))
        if min_jd:
            query = query.filter(window.c.jd_match_score >= float(min_jd))
        if min_final:
            query = query.filter(window.c.final_score >= float(min_final))

        total = int(query.with_entities(func.count()).scalar() or 0)
        total_pages = max(1, -(-total // per_page))

        order_clause = {
            "final_desc": [window.c.final_score.desc().nullslast(), window.c.analysis_id.desc()],
            "ats_desc": [window.c.ats_score.desc().nullslast(), window.c.analysis_id.desc()],
            "jd_desc": [window.c.jd_match_score.desc().nullslast(), window.c.analysis_id.desc()],
            "newest_analysis": [window.c.analysis_created_at.desc(), window.c.analysis_id.desc()],
            "recently_updated": [Candidate.updated_at.desc(), window.c.analysis_id.desc()],
        }[sort]

        rows = (
            query.order_by(*order_clause)
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

    results = []
    for row in rows:
        final = row.final_score
        results.append(
            {
                "id": row.cid,
                "name": row.name,
                "email": row.email,
                "resume_filename": row.resume_filename,
                "status": row.status,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "ats_score": row.ats_score,
                "jd_match_score": row.jd_match_score,
                "final_score": final,
                "latest_analysis_at": row.analysis_created_at,
                "latest_analysis_id": row.analysis_id,
                "latest_version": row.latest_version,
                "score_eligible": _score_eligible(final),
                "matched_skill_count": len(row.matched_skills or []),
            }
        )

    return {
        "results": results,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "total_candidates": total_candidates,
        "has_candidates": True,
        "has_results": total > 0,
    }


def filter_candidates(**kwargs) -> list[dict]:
    """Filtered candidate rows without pagination (thin wrapper)."""
    return search_candidates(**kwargs, per_page=1_000_000, page=1)["results"]


# ---------------------------------------------------------------------------
# Candidate detail (reuses the existing candidate-detail + timeline code)
# ---------------------------------------------------------------------------
def get_candidate_details(candidate_id: int, user: dict | None = None) -> dict | None:
    """Full detail: identity, latest analysis, eligibility, resume versions,
    activity timeline.

    Thin composition over the existing services — no duplicate detail code:
    identity/status + latest analysis come from
    :func:`services.recruitment_service.get_candidate_detail`, resume versions
    from :func:`services.resume_version_service`, and the activity timeline
    from :func:`services.activity_service.get_candidate_activity`. Returns
    ``None`` for an unknown candidate or one invisible to ``user``.
    """
    from services import activity_service
    from services.recruitment_service import get_candidate_detail
    from services.resume_version_service import (
        get_resume_versions,
        resume_version_snapshot,
    )

    detail = get_candidate_detail(candidate_id, user=user)
    if detail is None:
        return None
    versions = get_resume_versions(candidate_id, user=user)
    latest_number = versions[0].version_number if versions else None
    detail["resume_versions"] = [
        resume_version_snapshot(version, latest_number=latest_number)
        for version in versions
    ]
    latest = detail.get("latest_analysis")
    final = getattr(latest, "final_score", None) if latest is not None else None
    detail["score_eligible"] = _score_eligible(final)
    detail["activities"] = activity_service.get_candidate_activity(
        candidate_id, user=user
    )
    return detail


# ---------------------------------------------------------------------------
# Recruiter Workspace (unified pool + detail)
# ---------------------------------------------------------------------------
def _candidate_is_visible(candidate_id: int, user: dict | None) -> bool:
    """Quick visibility check used by lightweight workspace helpers."""
    with session_scope() as session:
        candidate = session.get(Candidate, candidate_id)
        if candidate is None:
            return False
        return can_access(user, candidate.created_by_user_id)


def get_candidate_jd_contexts(candidate_id: int, user: dict | None = None) -> dict:
    """Distinct job descriptions a candidate has been analyzed against.

    Returns ``{"contexts": [...], "no_jd": bool}`` where ``contexts`` lists
    ``{id, title, preview, analysis_count}`` ordered by the most recently
    analyzed role first, and ``no_jd`` is True when the candidate also has an
    analysis with no job description. Used to label every analysis with the
    exact role that produced it.
    """
    from core.models import JobDescription

    if not _candidate_is_visible(candidate_id, user):
        return {"contexts": [], "no_jd": False}

    rows = []
    with session_scope() as session:
        jd_query = session.query(
            JobDescription.id,
            JobDescription.title,
            JobDescription.description,
            func.max(ResumeAnalysis.created_at).label("last_analyzed"),
            func.count(ResumeAnalysis.id).label("analysis_count"),
        ).join(ResumeAnalysis, ResumeAnalysis.jd_id == JobDescription.id)
        jd_scope = owner_scope(JobDescription, user)
        if jd_scope is not None:
            # A candidate's stored analyses can only surface JD contexts the
            # current recruiter actually owns (or legacy shared JDs).
            jd_query = jd_query.filter(jd_scope)
        rows = (
            jd_query.filter(ResumeAnalysis.candidate_id == candidate_id)
            .group_by(JobDescription.id, JobDescription.title, JobDescription.description)
            .order_by(
                func.max(ResumeAnalysis.created_at).desc(),
                JobDescription.id.desc(),
            )
            .all()
        )
        has_no_jd = (
            session.query(ResumeAnalysis.id)
            .filter(
                ResumeAnalysis.candidate_id == candidate_id,
                ResumeAnalysis.jd_id.is_(None),
            )
            .first()
            is not None
        )
    contexts = [
        {
            "id": jd_id,
            "title": title,
            "preview": (description or "").strip(),
            "analysis_count": int(count),
        }
        for jd_id, title, description, _last, count in rows
    ]
    return {"contexts": contexts, "no_jd": bool(has_no_jd)}


def get_analysis_for_context(
    candidate_id: int,
    resume_version_id: int | None,
    jd_id: int | None,
    user: dict | None = None,
):
    """Resolve the exact analysis for one (candidate, resume version, JD) triple.

    The workspace displays scores ONLY from this exact combination, so selecting
    Version 1 + JD A can never show Version 2's scores (or JD B's). Returns the
    newest matching ``ResumeAnalysis`` or ``None`` when that combination has not
    been analyzed yet (or the candidate is invisible to ``user``).
    """
    if not _candidate_is_visible(candidate_id, user):
        return None
    with session_scope() as session:
        if jd_id is not None:
            jd = session.get(JobDescription, jd_id)
            if jd is None or not can_access(user, jd.created_by_user_id):
                return None
        query = session.query(ResumeAnalysis).filter(
            ResumeAnalysis.candidate_id == candidate_id,
            ResumeAnalysis.resume_version_id == resume_version_id,
        )
        if jd_id is None:
            query = query.filter(ResumeAnalysis.jd_id.is_(None))
        else:
            query = query.filter(ResumeAnalysis.jd_id == jd_id)
        return query.order_by(
            ResumeAnalysis.created_at.desc(), ResumeAnalysis.id.desc()
        ).first()


def get_workspace_detail(candidate_id: int, user: dict | None = None) -> dict | None:
    """Everything the Workspace detail pane needs for ONE selected candidate.

    Thin composition over the existing services: identity/status + latest
    analysis from ``get_candidate_details`` (which already attaches resume
    versions, eligibility and activity), plus the distinct JD contexts. Loaded
    only when a candidate is selected — never per row in the pool table.
    """
    detail = get_candidate_details(candidate_id, user=user)
    if detail is None:
        return None
    jd_bundle = get_candidate_jd_contexts(candidate_id, user=user)
    detail["jd_contexts"] = jd_bundle["contexts"]
    detail["has_no_jd_analysis"] = jd_bundle["no_jd"]
    return detail


def candidate_pool_csv(results: list[dict]) -> str:
    """CSV for the CURRENT result set — one row per candidate, no duplicates.

    Reuses pandas (already a dependency) exactly like ``pages/4_Recruiter_Results``
    so the workspace export is consistent with the existing CSV mechanism. All
    text cells are run through :func:`services.file_security_service.csv_neutralize`
    so spreadsheet formula injection payloads never execute when the export is
    opened.
    """
    import pandas as pd

    from services.file_security_service import csv_neutralize

    frame = pd.DataFrame(
        [
            {
                "Candidate": csv_neutralize(row["name"] or f"Candidate #{row['id']}"),
                "Status": csv_neutralize(row["status"]),
                "Score-Eligible": csv_neutralize("Yes" if row["score_eligible"] else "No"),
                "ATS Score": row["ats_score"],
                "JD Match": row["jd_match_score"],
                "Final Score": row["final_score"],
                "Resume Version": csv_neutralize(
                    f"v{row['latest_version']}" if row.get("latest_version") else ""
                ),
                "Last Updated": row["updated_at"],
            }
            for row in results
        ]
    )
    return frame.to_csv(index=False)