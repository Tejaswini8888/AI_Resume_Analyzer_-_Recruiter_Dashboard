"""Lightweight internal pipeline validation.

Produces human-readable warnings about suspicious extraction results. These are
intended for debugging / internal quality checks only and are deliberately NOT
surfaced in the normal user UI.
"""

import re

_EMAIL_RE = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")

# Thresholds for "suspiciously large" sections.
EDUCATION_MAX_LINES = 8
EDUCATION_MAX_CHARS = 800
EXPERIENCE_MAX_LINES = 80
EXPERIENCE_MAX_CHARS = 12000
PROJECTS_MAX_LINES = 60


def validate_text(text):
    """Warnings for the raw extracted text itself."""
    warnings = []
    if not text or not text.strip():
        warnings.append("empty extracted text")
    elif len(text.strip()) < 40:
        warnings.append("very short extracted text (possible parsing failure)")
    return warnings


def _malformed_email(email):
    if not email or email == "Not Found":
        return False
    return not _EMAIL_RE.match(email.strip())


def _suspicious_phone(phone):
    if not phone or phone == "Not Found":
        return False
    digits = re.sub(r"\D", "", phone)
    return len(digits) < 7 or len(digits) > 15


def validate_profile(profile):
    """Warnings about the extracted structured fields."""
    warnings = []
    if not profile.raw_text or not profile.raw_text.strip():
        warnings.append("empty extracted text")

    if not profile.name:
        warnings.append("missing candidate name")

    if profile.email:
        if profile.email == "Not Found":
            warnings.append("missing candidate email")
        elif _malformed_email(profile.email):
            warnings.append(f"malformed email: {profile.email}")

    if profile.phone and _suspicious_phone(profile.phone):
        warnings.append(f"invalid phone extraction: {profile.phone}")

    if not profile.skills:
        warnings.append("empty skill list")

    if len(profile.education) > EDUCATION_MAX_LINES or (
        profile.education and sum(len(e) for e in profile.education) > EDUCATION_MAX_CHARS
    ):
        warnings.append("suspiciously large education section")

    experience_text = "\n".join(profile.experience)
    if len(profile.experience) > EXPERIENCE_MAX_LINES or len(experience_text) > EXPERIENCE_MAX_CHARS:
        warnings.append("suspiciously large experience section")

    if len(profile.projects) > PROJECTS_MAX_LINES:
        warnings.append("suspiciously large projects section")

    return warnings