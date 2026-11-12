"""Local, offline semantic embedding service for TalentIQ.

This layer converts meaningful resume/JD text into embeddings and returns a
normalized semantic similarity score (0-100). It is deliberately isolated from
the UI and from the deterministic matching layers.

Guarantees
----------
- Embeddings come from a sentence-transformer model loaded ONCE per process and
  reused across Streamlit reruns and across candidates (module-level singleton +
  ``lru_cache`` on the normalized input text, so identical text is never
  re-embedded).
- The model is loaded from the local Hugging Face cache with
  ``local_files_only=True``: resume/JD content is never sent to any external
  service and no Groq/LLM API is used for embeddings.
- If the runtime or the model is unavailable, every entry point returns
  ``None`` / 0 and ``semantic_available()`` reports False so callers fall back
  to the deterministic skill + TF-IDF matching without crashing.
"""

import re
from functools import lru_cache

# all-MiniLM-L6-v2: fast, small (≈23 MB), 384-dim, good paraphrase/STS quality.
SEMANTIC_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Trim each "meaningful chunk" so the whole representation stays well under the
# model's 256-token window; the embedding then reflects the curated content rather
# than a giant raw blob.
_MAX_CHUNK_CHARS = 1600
_MAX_SECTION_ITEMS = 12

_embed_model = None
_embed_error = None
_embed_error_seen = False


def _get_model():
    """Return the cached SentenceTransformer instance or raise the stored error."""
    global _embed_model, _embed_error, _embed_error_seen
    if _embed_model is not None:
        return _embed_model
    if _embed_error_seen:
        raise _embed_error
    try:
        from sentence_transformers import SentenceTransformer

        _embed_model = SentenceTransformer(
            SEMANTIC_MODEL_NAME,
            local_files_only=True,
        )
        return _embed_model
    except Exception as exc:  # pragma: no cover - defensive
        _embed_error = exc
        _embed_error_seen = True
        raise


def semantic_available():
    """True when the local embedding model can produce embeddings."""
    global _embed_model, _embed_error_seen
    if _embed_model is not None:
        return True
    if _embed_error_seen:
        return False
    try:
        _get_model()
        return True
    except Exception:
        return False


def reset_semantic_state():
    """Test hook: forget the cached model/error so availability is re-probed."""
    global _embed_model, _embed_error, _embed_error_seen
    _embed_model = None
    _embed_error = None
    _embed_error_seen = False
    _embed_cached.cache_clear()


def _clean_text(text):
    """Normalize whitespace so cache keys and tokens are stable."""
    return re.sub(r"\s+", " ", (text or "")).strip()


@lru_cache(maxsize=512)
def _embed_cached(text):
    """Embed one normalized text chunk. Not called when the model is down."""
    vector = _get_model().encode(
        _clean_text(text),
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return tuple(float(x) for x in vector)


def embed_text(text):
    """Embed a text chunk -> tuple[float] | None (None when unavailable)."""
    cleaned = _clean_text(text)
    if not cleaned:
        return None
    try:
        return _embed_cached(cleaned)
    except Exception:
        return None


def semantic_similarity(text_a, text_b):
    """Cosine similarity (normalized, clamped 0-100) or None when unavailable."""
    vec_a = embed_text(text_a)
    vec_b = embed_text(text_b)
    if vec_a is None or vec_b is None:
        return None
    cosine = sum(x * y for x, y in zip(vec_a, vec_b))
    cosine = max(0.0, min(1.0, cosine))
    return round(cosine * 100, 2)


# ---------------------------------------------------------------------------
# Meaningful text representations (NOT the raw full-text blob)
# ---------------------------------------------------------------------------

# JD heading vocabulary -> canonical section. Each candidate heading is matched
# on the normalized line (lowercased, trimmed). "Preferred ..." is matched before
# "requirements" so a "Preferred qualifications" heading lands in preferred.
_JD_SECTION_ALIASES = {
    "summary": (
        "about",
        "about us",
        "about the company",
        "about this role",
        "about the role",
        "the role",
        "job summary",
        "position summary",
        "overview",
        "role overview",
        "company overview",
        "who we are",
        "what we do",
        "introduction",
        "why join us",
        "join us",
    ),
    "responsibilities": (
        "responsibilities",
        "key responsibilities",
        "what you will do",
        "what you'll do",
        "what you will be doing",
        "the day to day",
        "day to day",
        "day-to-day",
        "duties",
        "role and responsibilities",
        "role & responsibilities",
        "what you will own",
        "you will",
        "you'll",
    ),
    "preferred": (
        "preferred qualifications",
        "preferred skills",
        "nice to have",
        "nice-to-have",
        "good to have",
        "good-to-have",
        "bonus points",
        "bonus",
        "is a plus",
        "a plus",
        "desired",
        "preferred",
    ),
    "requirements": (
        "requirements",
        "qualifications",
        "required qualifications",
        "minimum qualifications",
        "what we are looking for",
        "what we're looking for",
        "we are looking for",
        "we're looking for",
        "you have",
        "you'll have",
        "you will have",
        "your background",
        "skills and experience",
        "skills & experience",
        "experience required",
        "education and experience",
        "education & experience",
        "must have",
        "must-have",
        "what you need",
        "job requirements",
        "success criteria",
        "experience",
    ),
    "skills": (
        "skills",
        "technical skills",
        "required skills",
        "tech stack",
        "technologies",
        "tools",
        "skills and tools",
    ),
}

_JD_KEYS = tuple(_JD_SECTION_ALIASES.keys())
_JD_ALIAS_LOOKUP = {
    alias: canon
    for canon, aliases in _JD_SECTION_ALIASES.items()
    for alias in aliases
}

_HEADING_PUNCT_RE = re.compile(r"^[\s\-•◦▪·*#:]+|[\s:]+$")
_UPPER_RATIO_RE = re.compile(r"[A-Z]")
_LEADING_BULLET_RE = re.compile(r"^[\s\-•◦▪·*#]+")


def _looks_like_jd_heading(line):
    """Heuristic: short line with a heading shape (collect / colon / all-caps)."""
    stripped = line.strip()
    if not stripped or len(stripped) > 90:
        return False
    if stripped.rstrip().endswith(":"):
        return True
    letters = [ch for ch in stripped if ch.isalpha()]
    if not letters:
        return False
    upper = len(_UPPER_RATIO_RE.findall(stripped))
    return (upper / len(letters)) >= 0.7


def _normalize_jd_heading(line):
    text = re.sub(r"[\*_#]+", "", line)
    text = _HEADING_PUNCT_RE.sub("", text).strip()
    while "  " in text:
        text = text.replace("  ", " ")
    return text.lower()


def jd_sections(jd_text):
    """Split a job description into canonical buckets + a remainder roll-up.

    Returns dict[canonical_section -> list[str line]] ('' key never used).
    Unassigned lines roll into the "summary" bucket so generic one-paragraph
    JDs still get a meaningful representation.
    """
    buckets = {key: [] for key in _JD_KEYS}
    current = "summary"
    rollup = []
    for raw in jd_text.splitlines():
        line = _LEADING_BULLET_RE.sub("", raw).strip()
        if not line:
            continue
        norm = _normalize_jd_heading(line)
        matched = _JD_ALIAS_LOOKUP.get(norm)
        if matched is None and _looks_like_jd_heading(line):
            for alias, canon in sorted(
                _JD_ALIAS_LOOKUP.items(), key=lambda kv: len(kv[0]), reverse=True
            ):
                if norm.startswith(alias):
                    matched = canon
                    break
        if matched is not None:
            current = matched
            continue
        buckets[current].append(line)
        rollup.append(line)
    return buckets


def candidate_representation(profile):
    """Structured, meaningful text slice of a resume for embedding.

    Built from the ResumeProfile produced in earlier Phase 2 steps (summary,
    skills, experience, projects, certifications) — never the raw full text.
    """
    if profile is None:
        return _clean_text(getattr(profile, "raw_text", "") or "")
    from modules.resume_profile import ResumeProfile  # local: avoid import cycles

    if not isinstance(profile, ResumeProfile):
        return ""
    parts = []
    summary = _section_join(profile.raw_text, "summary")
    if summary:
        parts.append(f"Professional summary:\n{summary}")
    if profile.skills:
        parts.append(f"Technical skills:\n{', '.join(profile.skills)}")
    if profile.experience:
        items = "\n".join(f"- {line}" for line in profile.experience[:_MAX_SECTION_ITEMS])
        parts.append(f"Professional experience:\n{items}")
    if profile.projects:
        items = "\n".join(f"- {line}" for line in profile.projects[:_MAX_SECTION_ITEMS])
        parts.append(f"Projects:\n{items}")
    if profile.certifications:
        items = "\n".join(
            f"- {line}" for line in profile.certifications[:_MAX_SECTION_ITEMS]
        )
        parts.append(f"Certifications:\n{items}")
    if not parts:
        return _clean_text(profile.raw_text)[:_MAX_CHUNK_CHARS]
    return "\n\n".join(_truncate_chunk(part) for part in parts)


@lru_cache(maxsize=128)
def jd_representation(jd_text):
    """Meaningful, bucketed text slice of a job description for embedding."""
    buckets = jd_sections(jd_text or "")
    parts = []
    if buckets["summary"]:
        parts.append(f"Role summary:\n" + "\n".join(buckets["summary"]))
    if buckets["responsibilities"]:
        parts.append(
            "Responsibilities:\n"
            + "\n".join(f"- {line}" for line in buckets["responsibilities"])
        )
    if buckets["requirements"]:
        parts.append(
            "Requirements:\n"
            + "\n".join(f"- {line}" for line in buckets["requirements"])
        )
    if buckets["preferred"]:
        parts.append(
            "Preferred qualifications:\n"
            + "\n".join(f"- {line}" for line in buckets["preferred"])
        )
    if buckets["skills"]:
        parts.append(
            "Required skills:\n" + "\n".join(f"- {line}" for line in buckets["skills"])
        )
    if not parts:
        return _clean_text(jd_text)[:_MAX_CHUNK_CHARS]
    return "\n\n".join(_truncate_chunk(part) for part in parts)


def _section_join(resume_text, canonical):
    from modules.sections import section_content  # local import

    return _clean_text(section_content(resume_text, canonical))[:_MAX_CHUNK_CHARS]


def _truncate_chunk(part):
    if len(part) <= _MAX_CHUNK_CHARS:
        return part
    return part[:_MAX_CHUNK_CHARS].rstrip()