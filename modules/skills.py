"""Skill intelligence for TalentIQ.

Single source of truth: ``data/skills.json`` (canonical name, aliases,
category, and optional boundary guards). No skill definitions live in code;
aliases are folded here and nowhere else.

Matching is word-boundary aware and normalization-driven, so:

- "C" never matches "Cloud", "CSS", "C++" or "C#" (the "C" entry declares the
  suffix guard ``exclude_after`` = "+#")
- "Java" never matches "JavaScript"
- short spellings ("c", "js", "ai", "ml" ...) are only matched inside the
  SKILLS section of a resume when one exists, to avoid false positives in prose
- "ReactJS"/"react.js"/"React JS" -> "React", "Postgres" -> "PostgreSQL",
  "Python3" -> "Python" ... via the alias map in the dataset

All extraction is deterministic: input text in, sorted canonical labels out.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from modules.sections import skill_scope_text

# Absolute path anchored to the repository root, never cwd-dependent, so the
# skill dataset loads identically regardless of where the process is launched.
_SKILLS_PATH = str(Path(__file__).resolve().parent.parent / "data" / "skills.json")

_SHORT_SPELLING_RE = re.compile(r"^[a-z]{1,2}$")


def _load_dataset_file():
    """Load the canonical skill dataset.

    Raises a clear, actionable error when the file is missing. A bare
    ``FileNotFoundError`` here is deeply confusing because the first symptom is
    an empty skill list, which silently degrades ATS scoring, JD matching and
    recruiter search rather than crashing the page.
    """
    try:
        with open(_SKILLS_PATH, encoding="utf-8") as handle:
            dataset = json.load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Skill dataset not found at {_SKILLS_PATH}. It ships with the "
            "repository under data/skills.json and is required for skill "
            "canonicalization, ATS scoring and job-description matching. "
            "Restore the file (git checkout -- data/skills.json) and restart."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Skill dataset at {_SKILLS_PATH} is not valid JSON: {exc.msg} "
            f"(line {exc.lineno}). Restore the file and restart."
        ) from exc
    if not isinstance(dataset, dict) or not dataset:
        raise ValueError(
            f"Skill dataset at {_SKILLS_PATH} is empty or malformed; it must be "
            'a non-empty JSON object of "canonical name": {"category", "aliases"}.'
        )
    return dataset


@lru_cache(maxsize=1)
def _dataset():
    return _load_dataset_file()


def _norm_key(value):
    """Normalize a spelling for index lookups (lower, collapse whitespace)."""
    return re.sub(r"\s+", " ", value.strip().lower())


@lru_cache(maxsize=1)
def _detection_index():
    """normalized spelling -> canonical label (canonical + alias forms)."""
    index = {}
    for canonical, meta in _dataset().items():
        index[_norm_key(canonical)] = canonical
        for alias in meta.get("aliases", []):
            index.setdefault(_norm_key(alias), canonical)
    return index


@lru_cache(maxsize=1)
def _short_keys():
    """Spellings that are an ambiguous bare word (<= 2 chars)."""
    return {key for key in _detection_index() if _SHORT_SPELLING_RE.match(key)}


@lru_cache(maxsize=None)
def _boundary_pattern(key):
    """Word-boundary regex for a normalized skill spelling."""
    parts = [re.escape(piece) for piece in key.split(" ")]
    body = r"\s+".join(parts) if len(parts) > 1 else parts[0]

    exclude_after = None
    for canonical, meta in _dataset().items():
        if _norm_key(canonical) == key:
            exclude_after = meta.get("exclude_after") or ""
            break
    if exclude_after:
        suffix_class = "".join(re.escape(ch) for ch in exclude_after) + r"\w"
        return re.compile(
            r"(?<![\w])" + body + r"(?![" + suffix_class + r"])", re.IGNORECASE
        )
    return re.compile(r"(?<![\w])" + body + r"(?![\w])", re.IGNORECASE)


def _match_skills_in(text):
    """Canonical labels (as a set) detected in ``text``."""
    lower = text.lower()
    scope = skill_scope_text(lower)
    found = set()
    for key, canonical in _detection_index().items():
        if key in _short_keys():
            target = scope if scope is not None else lower
        else:
            target = lower
        if _boundary_pattern(key).search(target):
            found.add(canonical)
    return found


def load_skills():
    """Canonical skill labels, in dataset order."""
    return list(_dataset().keys())


def skill_categories():
    """canonical skill -> category for every skill in the dataset."""
    return {canonical: meta.get("category", "") for canonical, meta in _dataset().items()}


def skill_category(skill):
    """Category for one canonical skill ("" if unknown)."""
    meta = _dataset().get(skill)
    return meta.get("category", "") if meta else ""


def normalize_skill(term):
    """Map a raw spelling ("reactjs", "Postgres", "Python3" ...) to its
    canonical label, or None when the term is not a known skill."""
    if not term:
        return None
    key = _norm_key(term)
    index = _detection_index()
    if key in index:
        return index[key]
    # Partial forms ("Postgres 15", "javascript experience") still fold.
    for index_key in sorted(index, key=len, reverse=True):
        if index_key in key:
            return index[index_key]
    return None


def canonical_skill(term):
    """Exact canonical mapping for a known spelling (canonical or alias).

    Like ``normalize_skill`` but WITHOUT the substring-folding fallback, so a
    term that is not an exact dataset spelling is treated as unknown (returns
    ``None``) instead of being collapsed on a substring. This keeps closed-vocab
    comparisons safe: "Cloud" can never collapse to "C", "CSS" never to "C",
    and "JavaScript" never to "Java". Use this for equality checks against
    persisted skill tokens; use ``normalize_skill`` only where loose matching
    is acceptable.
    """
    if not term:
        return None
    return _detection_index().get(_norm_key(term))


def extract_skills(resume_text):
    """Word-boundary, normalization-aware skill detection.

    Returns a deterministic, alphabetically sorted list of canonical labels.
    """
    if not resume_text or not resume_text.strip():
        return []
    return sorted(_match_skills_in(resume_text))


# Backwards-compatible alias map (canonical <=> aliases come from the dataset).
SKILL_ALIASES = {
    _norm_key(alias): canonical
    for canonical, meta in _dataset().items()
    for alias in meta.get("aliases", [])
}