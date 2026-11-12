from modules.parser import extract_text_from_pdf
from modules.resume_profile import build_profile_from_text
from modules.ats_score import analyze_ats
from modules.jd_matcher import analyze_job_match


def rank_resumes(files, job_description, on_candidate=None):

    results = []

    for file in files:

        resume_text = extract_text_from_pdf(file)

        if resume_text.strip():
            profile = build_profile_from_text(resume_text)
            skills = profile.skills
            ats_result = analyze_ats(profile)
            ats_score = ats_result.overall_score
        else:
            profile = None
            skills = []
            ats_result = None
            ats_score = 0

        jd_match = (
            analyze_job_match(resume_text, skills, job_description, profile=profile)
            if job_description.strip()
            else None
        )

        # Recruiter surface: JD Match now reflects the layered combined score
        # (skill + semantic + TF-IDF); the Final Score formula stays unchanged.
        jd_score = jd_match.overall_match_score if jd_match else 0.0

        final_score = (
            ats_score * 0.4
            +
            jd_score * 0.6
        )

        results.append(
            {
                "Name": file.name,
                "ATS Score": ats_score,
                "JD Match": jd_score,
                "Final Score": round(
                    final_score,
                    2
                )
            }
        )

        if on_candidate is not None:
            # Optional side effect (e.g. persistence). Score math above is
            # untouched; the callback receives the structured signals.
            on_candidate(
                file=file,
                profile=profile,
                ats_result=ats_result,
                jd_match=jd_match,
                final_score=round(final_score, 2),
            )

    results.sort(
        key=lambda x: x["Final Score"],
        reverse=True
    )

    return results