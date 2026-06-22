from modules.parser import extract_text_from_pdf
from modules.skills import extract_skills
from modules.ats_score import calculate_ats_score
from modules.jd_matcher import calculate_jd_match


def rank_resumes(files, job_description):

    results = []

    for file in files:

        resume_text = extract_text_from_pdf(file)

        skills = extract_skills(resume_text)

        ats_score, _ = calculate_ats_score(
            resume_text,
            skills
        )

        jd_score = calculate_jd_match(
            resume_text,
            job_description
        )

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

    results.sort(
        key=lambda x: x["Final Score"],
        reverse=True
    )

    return results