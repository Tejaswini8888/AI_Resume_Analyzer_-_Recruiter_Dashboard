import logging

from core.config import MISSING_GROQ_API_KEY_MESSAGE, settings

logger = logging.getLogger(__name__)

# Client is built lazily on first use (and only when a key is configured), so
# the module stays importable without a key, a key configured AFTER startup is
# picked up on the first call, and an operator rotating ``GROQ_API_KEY`` can
# restart the process to swap in the new credential. Tests may assign ``client``
# directly to install a stub.
client = None


def _get_client():
    """Return the Groq client, building it lazily once a key is available."""
    global client
    if client is not None:
        return client
    if (settings.groq_api_key or "").strip():
        from groq import Groq

        client = Groq(api_key=settings.groq_api_key)
    return client


def _chat(prompt, temperature=None, response_format=None):
    """Run one chat completion with the configured Groq model.

    ``response_format`` (e.g. {"type": "json_object"}) is optional and passed
    through only when provided, so models that do not support JSON mode are
    unaffected.
    """
    _client = _get_client()
    if _client is None:
        raise RuntimeError(MISSING_GROQ_API_KEY_MESSAGE)

    try:
        kwargs = {
            "model": settings.groq_model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if response_format is not None:
            kwargs["response_format"] = response_format

        response = _client.chat.completions.create(**kwargs)

        return response.choices[0].message.content
    except Exception as exc:
        # Never expose credentials or internal failure details: the raw
        # exception may contain API/network internals and is NOT shown to the
        # user. Log only the failure type (never the message/body) and hint at
        # the active model so a wrong GROQ_MODEL (e.g. a deprecated id) is easy
        # to investigate.
        logger.warning(
            "AI request failed for model %r (%s)",
            settings.groq_model,
            type(exc).__name__,
        )
        raise RuntimeError(
            f"AI request failed for model {settings.groq_model!r}. "
            "Check the GROQ service status and credentials, then try again."
        )


def generate_resume_summary(resume_text):

    prompt = f"""
    Generate a concise professional resume summary
    in 4-5 lines.

    Resume:

    {resume_text}
    """

    return _chat(prompt, temperature=0.3)


def analyze_resume(resume_text):

    prompt = f"""
    Analyze the following resume.

    Return the response in this format:

    PROFESSIONAL SUMMARY:
    ...

    CAREER OBJECTIVE:
    ...

    STRENGTHS:
    - point 1
    - point 2

    WEAKNESSES:
    - point 1
    - point 2

    IMPROVEMENT SUGGESTIONS:
    - point 1
    - point 2

    Resume:

    {resume_text}
    """

    return _chat(prompt, temperature=0.3)