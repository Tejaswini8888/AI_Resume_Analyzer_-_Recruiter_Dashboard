from modules.skills import load_skills


def extract_jd_skills(jd_text):

    skills_db = load_skills()

    found = []

    for skill in skills_db:

        if skill.lower() in jd_text.lower():

            found.append(skill)

    return found


def find_missing_skills(
        resume_skills,
        jd_skills):

    missing = []

    for skill in jd_skills:

        if skill not in resume_skills:

            missing.append(skill)

    return missing