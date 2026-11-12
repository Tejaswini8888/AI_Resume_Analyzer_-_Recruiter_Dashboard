"""AI Resume Coach — personalized, JD-grounded resume improvement guidance.

Purpose
-------
Answer ONE question: "How can this candidate improve their resume specifically
for this Job Description?" The coach transforms the structured intelligence
(ResumeProfile + ATSScoreResult + JDMatchResult) into prioritized, actionable
recommendations instead of repeating scores or skill lists.

Anti-fabrication
----------------
- Only candidate facts supplied in the structured profile are verboten; the
  model is instructed to use ONLY supplied information, to phrase skill-gap
  advice conditionally ("if you genuinely have this experience..."), and never
  to invent metrics, titles, certifications, or experience.
- The deterministic fallback (used when the AI call fails) is generated purely
  from the actual signals (missing skills, ATS issues, matched skills) and
  cannot introduce facts the data does not contain.

Single AI request
-----------------
One hand-built prompt produces one JSON object for the whole coach result.
Results are cached per (resume, JD) by the page so reruns stay cheap.
"""

import json
import re
from dataclasses import dataclass, field

from modules.ai_analysis import _chat
from services.semantic_matcher import jd_representation

# Bounds keep the structured prompt compact and token-friendly.
_MAX_SUMMARY_CHARS = 600
_MAX_JD_CHARS = 2400


@dataclass
class PriorityImprovement:
    """One prioritized, specific recommendation with a reason."""

    priority: str
    issue: str
    recommendation: str
    reason: str


@dataclass
class ResumeCoachResult:
    """Structured AI Resume Coach output (parsed from the model JSON)."""

    overall_assessment: str = ""
    priority_improvements: list = field(default_factory=list)
    summary_improvement: dict = field(default_factory=dict)
    experience_improvements: list = field(default_factory=list)
    skill_gap_guidance: list = field(default_factory=list)
    keyword_recommendations: list = field(default_factory=list)
    formatting_recommendations: list = field(default_factory=list)
    interview_focus_areas: list = field(default_factory=list)
    ai_generated: bool = True
    note: str = ""


# ---------------------------------------------------------------------------
# Prompt construction (structured inputs only — never the whole raw resume)
# ---------------------------------------------------------------------------
def _clip(value, limit):
    text = (value or "").strip()
    if not text:
        return ""
    return text[:limit] + ("…" if len(text) > limit else "")


def _bullets(items, limit=8):
    if not items:
        return "(none)"
    lines = [f"- {item}" for item in items[:limit]]
    return "\n".join(lines)


def _profile_facts(profile, resume_text):
    """Jah-lite structured fact block for the prompt (privacy-safe, bounded)."""
    from modules.sections import section_content

    parts = []
    summary = _clip(section_content(resume_text, "summary"), _MAX_SUMMARY_CHARS)
    if summary:
        parts.append(f"Summary:\n{summary}")
    if profile.skills:
        parts.append(f"Skills:\n{_bullets(profile.skills)}")
    if profile.experience:
        parts.append(f"Experience:\n{_bullets(profile.experience)}")
    if profile.projects:
        parts.append(f"Projects:\n{_bullets(profile.projects)}")
    if profile.education:
        parts.append(f"Education:\n{_bullets(profile.education, 6)}")
    if profile.certifications:
        parts.append(f"Certifications:\n{_bullets(profile.certifications, 4)}")
    if profile.achievements:
        parts.append(f"Achievements:\n{_bullets(profile.achievements, 4)}")
    return "\n\n".join(parts) if parts else "(no content detected)"


def _ats_facts(ats_result):
    parts = [f"ATS compatibility score: {ats_result.overall_score}/100"]
    if getattr(ats_result, "strengths", None):
        parts.append("ATS strengths:\n" + _bullets(ats_result.strengths, 6))
    if getattr(ats_result, "issues", None):
        parts.append("ATS issues:\n" + _bullets(ats_result.issues, 10))
    return "\n".join(parts)


def _jd_facts(jd_text, jd_match):
    if not jd_text or not jd_text.strip():
        return "Job description: (none provided — focus on general resume quality)"
    rep = _clip(jd_representation(jd_text), _MAX_JD_CHARS)
    parts = [f"Job description (condensed):\n{rep}"]
    if jd_match is not None:
        required_all = sorted(
            set(jd_match.matched_required_skills) | set(jd_match.missing_required_skills)
        )
        if required_all:
            parts.append(f"JD required skills: {', '.join(required_all)}")
        if getattr(jd_match, "matched_required_skills", None):
            parts.append(f"Matched required skills: {', '.join(jd_match.matched_required_skills)}")
        if getattr(jd_match, "missing_required_skills", None):
            parts.append(f"Missing required skills: {', '.join(jd_match.missing_required_skills)}")
        if getattr(jd_match, "matched_preferred_skills", None):
            parts.append(f"Matched preferred skills: {', '.join(jd_match.matched_preferred_skills)}")
        if getattr(jd_match, "missing_preferred_skills", None):
            parts.append(f"Missing preferred skills: {', '.join(jd_match.missing_preferred_skills)}")
        if getattr(jd_match, "semantic_match_score", None) is not None:
            parts.append(f"Semantic match score: {jd_match.semantic_match_score}/100")
    return "\n".join(parts)


_RESUME_COACH_SYSTEM = """
You are an expert resume coach helping a candidate tailor their resume TRUTHFULLY
to a specific job description. Carefully transform the structured signals below
into actionable recommendations.

STRICT RULES:
1. NEVER fabricate candidate facts. Use ONLY the supplied candidate information.
2. NEVER invent metrics, job titles, certifications, education, or technologies.
3. NEVER tell the candidate to claim a skill, tool, or experience the resume does
   not show. When a skill is merely missing, phrase advice conditionally, e.g.
   "If you genuinely have this experience, consider adding it" or "learn/practice
   X with a real project before listing it."
4. Do not treat a related technology as equivalent to a missing one (e.g. Flask
   is NOT FastAPI).
5. Recommendations must be grounded in THIS job description, not generic advice.
   When something generic is suggested (e.g. action verbs), tie it to a concrete
   reason from the candidate's actual content.
6. If metrics are absent from a bullet, suggest where the candidate could add a
   REAL metric (processing time, records handled, accuracy, etc.) — never invent
   a number yourself.
7. Be concise, specific, and explain WHY each recommendation matters.
8. Do not repeat the whole resume or the whole JD.

Output ONLY one JSON object with exactly these keys:
{
  "overall_assessment": "2-3 sentence assessment grounded in the signals.",
  "priority_improvements": [
    {"priority": "Critical|Important|Improvement",
     "issue": "...", "recommendation": "...", "reason": "..."}
  ],
  "summary_improvement": {
    "current_issue": "what is wrong with the current summary (or that none exists)",
    "suggested_rewrite": "an improved summary built ONLY from verified candidate facts, targeted to the JD"
  },
  "experience_improvements": ["specific, grounded bullet suggestions..."],
  "skill_gap_guidance": ["one item per missing skill: why it matters, whether it is critical, how to legitimately gain/show it..."],
  "keyword_recommendations": ["specific keywords/terminology to include, based on the JD and the candidate's real content..."],
  "formatting_recommendations": ["only when evidence in the ATS issues/Structure warrants it..."],
  "interview_focus_areas": ["topics the candidate should be ready to discuss, from matched skills and JD emphasis..."]
}
Be truthful. Do not add keys.
"""

_COACH_TEMPLATE = """
Candidate facts:
{facts}

Signals:
{ats_facts}

{jd_facts}

Current resume content to improve is described above. Do NOT repeat it in the
output. Generate the structured JSON object now.
"""


def _build_coach_prompt(profile, resume_text, ats_result, jd_match, jd_text):
    facts = _profile_facts(profile, resume_text)
    ats = _ats_facts(ats_result)
    jd = _jd_facts(jd_text, jd_match)
    body = _COACH_TEMPLATE.format(facts=facts, ats_facts=ats, jd_facts=jd)
    return _RESUME_COACH_SYSTEM + "\n\n" + body


# ---------------------------------------------------------------------------
# Robust JSON parsing (never lets malformed output crash the app)
# ---------------------------------------------------------------------------
def _extract_json_object(text):
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # Tolerate trailing commas ("value," before } or ]) which LLMs sometimes emit.
    fixed = re.sub(r",(\s*[}\]])", r"\1", candidate)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return None


def _as_list(value):
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _as_str(value):
    return str(value).strip() if value is not None else ""


def _result_from_dict(data, ai_generated=True, note=""):
    improvements = []
    for item in _as_list(data.get("priority_improvements") or []):
        if not isinstance(item, dict):
            continue
        improvements.append(
            PriorityImprovement(
                priority=_as_str(item.get("priority")) or "Improvement",
                issue=_as_str(item.get("issue")),
                recommendation=_as_str(item.get("recommendation")),
                reason=_as_str(item.get("reason")),
            )
        )
    summary = data.get("summary_improvement") or {}
    if not isinstance(summary, dict):
        summary = {}
    return ResumeCoachResult(
        overall_assessment=_as_str(data.get("overall_assessment")),
        priority_improvements=improvements,
        summary_improvement={
            "current_issue": _as_str(summary.get("current_issue")),
            "suggested_rewrite": _as_str(summary.get("suggested_rewrite")),
        },
        experience_improvements=[_as_str(x) for x in _as_list(data.get("experience_improvements")) if _as_str(x)],
        skill_gap_guidance=[_as_str(x) for x in _as_list(data.get("skill_gap_guidance")) if _as_str(x)],
        keyword_recommendations=[_as_str(x) for x in _as_list(data.get("keyword_recommendations")) if _as_str(x)],
        formatting_recommendations=[_as_str(x) for x in _as_list(data.get("formatting_recommendations")) if _as_str(x)],
        interview_focus_areas=[_as_str(x) for x in _as_list(data.get("interview_focus_areas")) if _as_str(x)],
        ai_generated=ai_generated,
        note=note,
    )


def parse_coach_response(text):
    """Return a ResumeCoachResult parsed from a model reply, or None."""
    data = _extract_json_object(text)
    if data is None:
        return None
    return _result_from_dict(data)


# ---------------------------------------------------------------------------
# Deterministic fallback (AI unavailable) — built only from real signals
# ---------------------------------------------------------------------------
def build_fallback_coach(profile, ats_result, jd_match, jd_text, note=""):
    """Evidence-based coach result that can never invent facts."""
    jd_missing_required = list(getattr(jd_match, "missing_required_skills", None) or [])
    jd_missing_preferred = list(getattr(jd_match, "missing_preferred_skills", None) or [])
    matched = list(getattr(jd_match, "matched_skills", None) or [])
    ats_issues = list(getattr(ats_result, "issues", None) or [])
    ats_strengths = list(getattr(ats_result, "strengths", None) or [])
    experience = list(getattr(profile, "experience", None) or [])
    projects = list(getattr(profile, "projects", None) or [])
    has_jd = bool(jd_text and jd_text.strip())
    assessment = (
        f"ATS compatibility: {getattr(ats_result, 'overall_score', 0)}/100. "
        + (f"JD match: {jd_match.overall_match_score}/100." if jd_match is not None else "No JD provided.")
    )
    if ats_strengths:
        assessment += " " + " ".join(ats_strengths[:2])

    improvements = []
    for skill in jd_missing_required:
        improvements.append(
            PriorityImprovement(
                priority="Critical",
                issue=f"Missing required skill: {skill}",
                recommendation=(
                    f"If you genuinely have {skill} experience, mention it "
                    f"explicitly in the Skills or Experience section. Otherwise, "
                    f"gain real hands-on experience with {skill} before applying."
                ),
                reason=f"The job description lists {skill} as a required skill.",
            )
        )
    for skill in jd_missing_preferred:
        improvements.append(
            PriorityImprovement(
                priority="Important",
                issue=f"Missing preferred skill: {skill}",
                recommendation=(
                    f"The JD prefers {skill}. If you have used it, state it in "
                    f"standard terminology; if not, it is still valuable to show "
                    f"related work or learn it with a small project."
                ),
                reason="Preferred skills strengthen your fit but are not required.",
            )
        )
    if not experience:
        improvements.append(
            PriorityImprovement(
                priority="Important",
                issue="Experience section is weak or missing.",
                recommendation=(
                    "Add your internship/work experience with role, company, "
                    "dates, responsibilities, and outcomes."
                ),
                reason="Recruiters rely on an experience section to gauge scope and impact.",
            )
        )
    elif len(experience) < 3:
        improvements.append(
            PriorityImprovement(
                priority="Important",
                issue="Experience section has very few entries.",
                recommendation=(
                    "Add measurable responsibilities and outcomes to your "
                    "internship/project descriptions."
                ),
                reason="Short experience sections leave evaluate severity of impact unclear.",
            )
        )
    resume_text = getattr(profile, "raw_text", "") or ""
    from modules.sections import section_content
    summary_text = _clip(section_content(resume_text, "summary"), _MAX_SUMMARY_CHARS)
    if not summary_text:
        improvements.append(
            PriorityImprovement(
                priority="Improvement",
                issue="No professional summary detected.",
                recommendation=(
                    f"Add a 2-3 sentence summary that targets the role"
                    + (f" and emphasizes the JDs focus areas" if has_jd else "")
                    + ", based only on your real skills and experience."
                ),
                reason="A targeted summary helps reviewers connect your profile to the role.",
            )
        )

    skill_gap = []
    for skill in jd_missing_required:
        skill_gap.append(
            f"{skill} (required): This skill is required by the JD. If you have "
            f"real experience with it, list it using standard terminology. If you "
            f"do not, it is not the same as a related technology; practice it "
            f"hands-on first, then show it in a project."
        )
    for skill in jd_missing_preferred:
        skill_gap.append(
            f"{skill} (preferred): A plus for this JD. Include it only if you "
            f"really use it; otherwise a small real project is a legitimate way "
            f"to gain and show the skill."
        )

    keywords = []
    for skill in jd_missing_required + jd_missing_preferred:
        keywords.append(
            f"Use the exact standard term '{skill}' in Skills or Experience if "
            f"you genuinely have that experience."
        )
    for skill in matched[:6]:
        keywords.append(
            f"You already match '{skill}' — keep its standard spelling prominent"
            f" so keyword scanning does not miss it."
        )

    formatting = []
    for issue in ats_issues:
        low = issue.lower()
        if "section was not detected" in low:
            section_name = issue.split(" section was not detected", 1)[0]
            formatting.append(f"Add a clearly headed '{section_name}' section.")
        elif "empty" in low:
            formatting.append("The resume could not be read; provide a text-based PDF so sections can be parsed.")

    interview = []
    for skill in matched:
        interview.append(f"Be ready to discuss {skill} with concrete examples.")
    if has_jd and not interview:
        interview.append("Prepare examples for the JD's core responsibilities (no specific skills matched).")

    return ResumeCoachResult(
        overall_assessment=assessment,
        priority_improvements=improvements,
        summary_improvement={
            "current_issue": "AI is unavailable, so no suggested rewrite was generated."
            if summary_text
            else "No professional summary detected.",
            "suggested_rewrite": "",
        },
        experience_improvements=[],
        skill_gap_guidance=skill_gap,
        keyword_recommendations=keywords,
        formatting_recommendations=formatting,
        interview_focus_areas=interview,
        ai_generated=False,
        note=note,
    )


# ---------------------------------------------------------------------------
# Public entry point (never raises; falls back deterministically)
# ---------------------------------------------------------------------------
def generate_resume_coach(profile, resume_text, ats_result, jd_match, jd_text):
    """Generate a structured coach result for one resume + JD.

    Tries one JSON-mode Groq call, then one plain-text call as a parse fallback.
    If everything fails, returns the deterministic evidence-based fallback with
    ``ai_generated=False`` — the page never goes blank and never crashes.
    """
    prompt = _build_coach_prompt(profile, resume_text, ats_result, jd_match, jd_text)

    try:
        raw = _chat(prompt, temperature=0.4, response_format={"type": "json_object"})
        result = parse_coach_response(raw)
        if result is not None:
            return result
        # JSON-mode reply was not parseable; one plain-text retry.
        raw = _chat(prompt, temperature=0.4)
        result = parse_coach_response(raw)
        if result is not None:
            return result
    except Exception:
        # JSON mode unsupported or the request failed: plain-text fallback call.
        try:
            raw = _chat(prompt, temperature=0.4)
            result = parse_coach_response(raw)
            if result is not None:
                return result
        except Exception:
            pass

    return build_fallback_coach(
        profile, ats_result, jd_match, jd_text,
        note="The AI coach is temporarily unavailable. Showing deterministic recommendations based on your actual signals.",
    )