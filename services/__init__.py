"""Service layer: small, isolated, reusable services.

- semantic_matcher: local offline embeddings + meaningful text representations.
- skill_matcher:    deterministic canonical-skill gap analysis.
- candidate_service / analysis_service / recruitment_service: persistent
  SQLite-backed storage (identity, analysis snapshots, recruitment status).

Orchestration (combining skill + TF-IDF + semantic signals into a JD match)
lives in ``modules.jd_matcher`` so the pages/ranker import surfaces stay stable.
"""

# Convenience re-exports.
from services.skill_matcher import SkillMatchResult, analyze_skill_match  # noqa: E402,F401
from services.semantic_matcher import (  # noqa: E402,F401
    SEMANTIC_MODEL_NAME,
    candidate_representation,
    embed_text,
    jd_representation,
    semantic_available,
    semantic_similarity,
)