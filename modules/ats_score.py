def calculate_ats_score(
        resume_text,
        detected_skills):

    score = 0

    breakdown = {}

    # Skills Score (30)

    skills_score = min(
        len(detected_skills) * 2,
        30
    )

    score += skills_score

    breakdown["Skills"] = skills_score

    # Projects Score (20)

    project_keywords = [
        "project",
        "projects",
        "developed",
        "built",
        "implemented"
    ]

    project_score = 0

    for word in project_keywords:

        if word.lower() in resume_text.lower():

            project_score += 4

    project_score = min(project_score, 20)

    score += project_score

    breakdown["Projects"] = project_score

    # Experience Score (20)

    experience_keywords = [
        "intern",
        "experience",
        "worked",
        "developer",
        "engineer"
    ]

    experience_score = 0

    for word in experience_keywords:

        if word.lower() in resume_text.lower():

            experience_score += 4

    experience_score = min(
        experience_score,
        20
    )

    score += experience_score

    breakdown["Experience"] = experience_score

    # Education Score (10)

    education_keywords = [
        "b.tech",
        "b.e",
        "bca",
        "mca",
        "computer science",
        "engineering"
    ]

    education_score = 0

    for word in education_keywords:

        if word.lower() in resume_text.lower():

            education_score += 2

    education_score = min(
        education_score,
        10
    )

    score += education_score

    breakdown["Education"] = education_score

    # Keywords Score (15)

    keyword_score = min(
        len(detected_skills),
        15
    )

    score += keyword_score

    breakdown["Keywords"] = keyword_score

    # Formatting Score (5)

    formatting_score = 5

    score += formatting_score

    breakdown["Formatting"] = formatting_score

    return score, breakdown