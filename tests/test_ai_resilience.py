"""AI-layer resilience tests. Every Groq call here is stubbed.

Why this file exists
--------------------
The application talks to a paid, rate-limited cloud API in the middle of two
user-facing pages. Those pages must degrade rather than crash, and they must
never surface a raw transport error to a recruiter or leak a credential into
the UI. None of that was covered by the unit suite: the only exercise of the
failure path was the operational harness ``tools/verify_failure_modes.py``,
which is not collected by pytest and needs a real environment.

So this module pins the five behaviours that matter, with no network:

1. a well-formed reply is parsed and used;
2. a malformed reply does not crash and is retried once;
3. an API error / timeout is converted to a clean, non-leaking error;
4. total failure returns the deterministic, evidence-based fallback;
5. the fallback can only reference facts the caller actually supplied.

Network safety
--------------
``conftest._no_live_ai_credentials`` (autouse) deletes ``GROQ_API_KEY`` and
resets ``ai_analysis.client`` before every test, so a test here physically
cannot make a billed request. ``test_the_suite_itself_has_no_api_key`` asserts
that invariant so a future change to the conftest cannot quietly reintroduce
live calls into CI.
"""

import json
from types import SimpleNamespace

import pytest

from core.config import MISSING_GROQ_API_KEY_MESSAGE
from modules import ai_analysis, resume_coach
from modules.ai_analysis import _chat
from modules.ats_score import ATSScoreResult
from modules.jd_matcher import JDMatchResult
from modules.resume_coach import (
    _extract_json_object,
    build_fallback_coach,
    generate_resume_coach,
    parse_coach_response,
)
from modules.resume_profile import ResumeProfile

VALID_COACH_JSON = json.dumps(
    {
        "overall_assessment": "Strong Python base; thin on cloud exposure.",
        "priority_improvements": [
            {
                "priority": "Critical",
                "issue": "No container experience listed.",
                "recommendation": "Add a Docker project section.",
                "reason": "The JD asks for Docker.",
            }
        ],
        "summary_improvement": {
            "current_issue": "Summary is generic.",
            "suggested_rewrite": "Backend engineer with Python and SQL experience.",
        },
        "skill_gap_guidance": ["Docker (required)"],
        "keyword_recommendations": ["Use 'Docker' explicitly."],
        "formatting_recommendations": ["Add an 'Experience' heading."],
        "interview_focus_areas": ["Walk through a Python data pipeline."],
    }
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------
class _StubCompletions:
    """Stands in for ``client.chat.completions``."""

    def __init__(self, content="", error=None):
        self._content = content
        self._error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


class _StubClient:
    def __init__(self, content="", error=None):
        self.chat = SimpleNamespace(completions=_StubCompletions(content, error))


@pytest.fixture()
def stub_client(monkeypatch):
    """Install a stub Groq client and hand it back for call assertions."""

    def _install(content="", error=None):
        client = _StubClient(content=content, error=error)
        monkeypatch.setattr(ai_analysis, "client", client)
        return client

    return _install


def _profile():
    return ResumeProfile(
        name="Ada Lovelace",
        email="ada@example.com",
        skills=["Python", "SQL"],
        experience=["Backend engineer, 2020-2024"],
        projects=["Pipeline tooling"],
        raw_text="SUMMARY\nBackend engineer.\n\nEXPERIENCE\nBackend engineer.",
    )


def _ats():
    return ATSScoreResult(
        overall_score=74,
        section_scores={"summary": 80, "experience": 70},
        strengths=["Clear section headings"],
        issues=["Experience section was not detected"],
        detected_sections=["summary"],
        missing_sections=["experience"],
        keyword_coverage={"Python": True},
    )


def _jd():
    return JDMatchResult(
        similarity_score=0.79,
        text_similarity_score=0.7,
        skill_match_score=0.8,
        semantic_match_score=0.0,
        overall_match_score=79.6,
        matched_skills=["Python", "SQL"],
        matched_required_skills=["Python"],
        matched_preferred_skills=["SQL"],
        missing_required_skills=["Docker"],
        missing_preferred_skills=["Kubernetes"],
        candidate_skills=["Python", "SQL"],
        jd_skills=["Python", "SQL", "Docker", "Kubernetes"],
        semantic_available=False,
    )


# ---------------------------------------------------------------------------
# Precondition: CI must never hold a real key
# ---------------------------------------------------------------------------
def test_the_suite_itself_has_no_api_key():
    """Guards the conftest fixture that makes this whole file network-free.

    If someone relaxes ``_no_live_ai_credentials`` so a developer's local
    ``.env`` leaks into the test session, tests would start making billed
    calls and passing/failing based on local secrets. Fail loudly instead.
    """
    from core.config import settings

    assert not (settings.groq_api_key or "").strip(), (
        "a Groq API key is visible to the test session; CI would make live "
        "billed calls. Check tests/conftest.py::_no_live_ai_credentials."
    )
    assert ai_analysis.client is None


# ---------------------------------------------------------------------------
# 1. Successful AI response handling
# ---------------------------------------------------------------------------
def test_chat_returns_model_content_on_success(stub_client):
    client = stub_client(content="a helpful summary")
    assert _chat("prompt", temperature=0.3) == "a helpful summary"
    assert len(client.chat.completions.calls) == 1


def test_chat_forwards_model_and_temperature(stub_client):
    from core.config import settings

    client = stub_client(content="ok")
    _chat("prompt", temperature=0.42)
    call = client.chat.completions.calls[0]
    assert call["model"] == settings.groq_model
    assert call["temperature"] == 0.42
    assert call["messages"] == [{"role": "user", "content": "prompt"}]


def test_chat_passes_response_format_only_when_requested(stub_client):
    """Models without JSON mode must not receive the kwarg at all."""
    client = stub_client(content="ok")
    _chat("p", response_format={"type": "json_object"})
    _chat("p")
    with_format, without_format = client.chat.completions.calls
    assert with_format["response_format"] == {"type": "json_object"}
    assert "response_format" not in without_format


@pytest.mark.parametrize("content", ["", None])
def test_chat_tolerates_empty_content_without_crashing(stub_client, content):
    stub_client(content=content)
    assert _chat("p") == content


# ---------------------------------------------------------------------------
# 2. Missing credential
# ---------------------------------------------------------------------------
def test_chat_without_a_key_raises_a_platform_neutral_error():
    """No key -> a clear error that does not name local files or a vendor."""
    with pytest.raises(RuntimeError) as excinfo:
        _chat("p")
    message = str(excinfo.value)
    assert message == MISSING_GROQ_API_KEY_MESSAGE
    assert ".env" not in message
    assert "gsk_" not in message


# ---------------------------------------------------------------------------
# 3. API failure / timeout -> sanitized error
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "error",
    [
        pytest.param(Exception("Connection reset by peer"), id="connection"),
        pytest.param(TimeoutError("read timed out after 600.0s"), id="timeout"),
        pytest.param(RuntimeError("rate limit exceeded, retry later"), id="rate-limit"),
        pytest.param(
            ValueError("401 unauthorized for key gsk_abcdefghijklmnopqrstuvwx"),
            id="auth-with-key-in-message",
        ),
    ],
)
def test_chat_sanitizes_transport_and_api_errors(stub_client, error):
    """Any client-side failure becomes one clean message with no internals.

    The raw exception is deliberately not propagated: a Groq error body can
    contain the request id, the endpoint, and occasionally the key, and this
    string is rendered in the UI.
    """
    stub_client(error=error)
    with pytest.raises(RuntimeError) as excinfo:
        _chat("p")
    message = str(excinfo.value)

    assert "AI request failed" in message
    # No transport internals.
    assert "Connection reset" not in message
    assert "timed out" not in message
    assert "rate limit" not in message
    assert "401" not in message
    # No credential echo, even when the upstream error contained one.
    assert "gsk_abcdefghijklmnopqrstuvwx" not in message
    assert "gsk_" not in message
    # The model id IS kept: a deprecated GROQ_MODEL is the single most common
    # cause of this error and the message must stay diagnosable.
    from core.config import settings

    assert settings.groq_model in message


def test_chat_logs_only_the_failure_type(stub_client, caplog):
    """The log records the model and exception class, never the message."""
    stub_client(error=Exception("internal detail that must not be logged"))
    with caplog.at_level("WARNING"):
        with pytest.raises(RuntimeError):
            _chat("p")
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "APIConnectionError" not in logged  # not the real class here
    assert "Exception" in logged
    assert "internal detail that must not be logged" not in logged


# ---------------------------------------------------------------------------
# 4. Malformed-response parsing
# ---------------------------------------------------------------------------
def test_parse_coach_response_accepts_clean_json():
    result = parse_coach_response(VALID_COACH_JSON)
    assert result is not None
    assert result.ai_generated is True
    assert result.overall_assessment.startswith("Strong Python base")
    assert result.priority_improvements[0].priority == "Critical"
    assert result.priority_improvements[0].issue == "No container experience listed."
    assert result.keyword_recommendations == ["Use 'Docker' explicitly."]
    assert result.interview_focus_areas == ["Walk through a Python data pipeline."]


def test_parse_coach_response_returns_none_for_unusable_text():
    for junk in ["", "   ", "I am sorry, I cannot help with that.", "[]", "null"]:
        assert parse_coach_response(junk) is None, junk


def test_parse_coach_response_of_an_empty_object_is_empty_but_renderable():
    """Documents a real (if minor) upstream quirk.

    ``{}`` is valid JSON, so it parses to a result whose every field is empty
    and whose ``ai_generated`` is still True. It does not crash: the coach page
    guards each section individually (``if result.overall_assessment``,
    ``if not improvements``), so the user sees an empty coach rather than a
    traceback. Pinned here so a future change to the parser that starts
    raising on this shape is a visible decision, not a silent regression.
    """
    result = parse_coach_response("{}")
    assert result is not None
    assert result.overall_assessment == ""
    assert result.priority_improvements == []
    assert result.keyword_recommendations == []


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        (
            "```json\n" + VALID_COACH_JSON + "\n```",
            "fenced-json-block",
        ),
        ("Here is my answer:\n" + VALID_COACH_JSON + "\nHope that helps!",
         "prose-around-json"),
        (
            VALID_COACH_JSON.replace('"Docker."', '"Docker.",').replace(
                '"Cloud."', '"Cloud.",'
            ),
            "trailing-commas",
        ),
    ],
)
def test_extract_json_object_recovers_from_common_model_wrappers(raw, label):
    """LLMs wrap JSON in fences, prose and trailing commas; all are handled."""
    data = _extract_json_object(raw)
    assert data is not None, label
    assert data["overall_assessment"].startswith("Strong Python base"), label


@pytest.mark.parametrize("raw", ["", "   ", None, "no braces here", "{", "}"])
def test_extract_json_object_returns_none_when_there_is_no_object(raw):
    assert _extract_json_object(raw) is None


def test_parse_coach_response_ignores_non_dict_items():
    """A model that emits a list, or junk entries, must not raise."""
    raw = json.dumps(
        {
            "overall_assessment": "ok",
            "priority_improvements": ["not a dict", 42, None],
        }
    )
    result = parse_coach_response(raw)
    assert result is not None
    assert result.priority_improvements == []


# ---------------------------------------------------------------------------
# 5. Bounded retry + fallback
# ---------------------------------------------------------------------------
def test_coach_uses_a_well_formed_reply(monkeypatch):
    calls = []

    def fake_chat(prompt, temperature=None, response_format=None):
        calls.append(response_format)
        return VALID_COACH_JSON

    monkeypatch.setattr(resume_coach, "_chat", fake_chat)
    result = generate_resume_coach(_profile(), "text", _ats(), _jd(), "JD text")

    assert result.ai_generated is True
    assert result.note == ""
    assert len(calls) == 1
    assert calls[0] == {"type": "json_object"}


def test_coach_retries_once_when_the_json_reply_is_unparseable(monkeypatch):
    """Malformed first reply -> one plain-text retry, then success."""
    replies = ["I cannot produce JSON.", VALID_COACH_JSON]
    calls = []

    def fake_chat(prompt, temperature=None, response_format=None):
        calls.append(response_format)
        return replies[len(calls) - 1]

    monkeypatch.setattr(resume_coach, "_chat", fake_chat)
    result = generate_resume_coach(_profile(), "text", _ats(), _jd(), "JD text")

    assert result.ai_generated is True
    assert len(calls) == 2
    assert calls[0] == {"type": "json_object"}
    assert calls[1] is None  # plain-text retry drops JSON mode


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(Exception("500 internal server error"), id="api-error"),
        pytest.param(TimeoutError("timed out"), id="timeout"),
        pytest.param(RuntimeError(MISSING_GROQ_API_KEY_MESSAGE), id="no-key"),
    ],
)
def test_coach_degrades_to_the_deterministic_fallback_on_api_failure(
    monkeypatch, error
):
    """Requirement: the page must render, not crash, when AI is unavailable."""
    attempts = []

    def fake_chat(prompt, temperature=None, response_format=None):
        attempts.append(1)
        raise error

    monkeypatch.setattr(resume_coach, "_chat", fake_chat)
    result = generate_resume_coach(_profile(), "text", _ats(), _jd(), "JD text")

    assert result.ai_generated is False
    assert "temporarily unavailable" in result.note
    assert len(attempts) == 2, "retries must stay bounded at one extra attempt"
    assert result.priority_improvements, "fallback must still give advice"


def test_coach_falls_back_when_every_reply_is_malformed(monkeypatch):
    calls = []

    def fake_chat(prompt, temperature=None, response_format=None):
        calls.append(1)
        return "<html>gateway error</html>"

    monkeypatch.setattr(resume_coach, "_chat", fake_chat)
    result = generate_resume_coach(_profile(), "text", _ats(), _jd(), "JD text")

    assert result.ai_generated is False
    assert len(calls) == 2
    assert result.priority_improvements


# ---------------------------------------------------------------------------
# 6. The fallback is grounded in real evidence (anti-fabrication)
# ---------------------------------------------------------------------------
def test_fallback_only_references_skills_the_caller_supplied():
    result = build_fallback_coach(
        _profile(), _ats(), _jd(), "JD text", note="unavailable"
    )
    blob = json.dumps(
        {
            "assessment": result.overall_assessment,
            "priority": [i.__dict__ for i in result.priority_improvements],
            "skill_gap": result.skill_gap_guidance,
            "keywords": result.keyword_recommendations,
            "formatting": result.formatting_recommendations,
            "interview": result.interview_focus_areas,
        },
        default=str,
    )
    # Everything asserted must come from the inputs, not from the model's
    # imagination: the JD's two missing skills and the two matched ones.
    for expected in ("Docker", "Kubernetes", "Python", "SQL"):
        assert expected in blob, expected
    # And nothing that was never supplied.
    assert "Terraform" not in blob
    assert "Kubernetes" in " ".join(result.skill_gap_guidance)


def test_fallback_flags_missing_required_skill_as_critical():
    result = build_fallback_coach(_profile(), _ats(), _jd(), "JD text")
    critical = [i for i in result.priority_improvements if i.priority == "Critical"]
    assert any("Docker" in i.issue for i in critical)
    important = [i for i in result.priority_improvements if i.priority == "Important"]
    assert any("Kubernetes" in i.issue for i in important)


def test_fallback_reports_the_real_ats_score():
    result = build_fallback_coach(_profile(), _ats(), _jd(), "JD text")
    assert "74/100" in result.overall_assessment
    assert "79.6" in result.overall_assessment


def test_fallback_surfaces_ats_formatting_issues():
    result = build_fallback_coach(_profile(), _ats(), _jd(), "JD text")
    assert any("Experience" in item for item in result.formatting_recommendations)


def test_fallback_with_no_jd_still_returns_usable_advice():
    """A candidate can open the coach before any JD has been analysed.

    ``build_fallback_coach`` tolerates ``jd_match=None`` (every read is a
    ``getattr(..., None) or []``), which is the genuine "no JD yet" state.
    """
    result = build_fallback_coach(_profile(), _ats(), None, "")
    assert result.ai_generated is False
    assert "No JD provided" in result.overall_assessment
    assert result.priority_improvements


def test_fallback_with_an_empty_jd_result_still_returns_usable_advice():
    """A JDMatchResult with no signals at all (all scores zero) is not a crash."""
    empty_jd = JDMatchResult(similarity_score=0.0)
    result = build_fallback_coach(_profile(), _ats(), empty_jd, "")
    assert result.ai_generated is False
    assert "ATS compatibility: 74/100" in result.overall_assessment
    assert result.priority_improvements


# ---------------------------------------------------------------------------
# 7. Never raise, whatever comes in
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("profile", "resume_text", "ats", "jd", "jd_text"),
    [
        pytest.param(ResumeProfile(), "", _ats(), _jd(), "JD", id="empty-profile"),
        pytest.param(
            ResumeProfile(name="X"),
            "text",
            _ats(),
            JDMatchResult(similarity_score=0.0),
            "",
            id="no-jd",
        ),
        pytest.param(
            ResumeProfile(name="X"),
            "text",
            ATSScoreResult(overall_score=0, section_scores={}, strengths=[], issues=[],
                           detected_sections=[], missing_sections=[],
                           keyword_coverage={}),
            JDMatchResult(similarity_score=0.0),
            "JD",
            id="empty-signals",
        ),
    ],
)
def test_coach_fallback_never_raises_on_sparse_input(
    monkeypatch, profile, resume_text, ats, jd, jd_text
):
    def fake_chat(prompt, temperature=None, response_format=None):
        raise RuntimeError("AI down")

    monkeypatch.setattr(resume_coach, "_chat", fake_chat)
    result = generate_resume_coach(profile, resume_text, ats, jd, jd_text)
    assert result is not None
    assert isinstance(result.overall_assessment, str)
    assert result.ai_generated is False
