import pandas as pd


def load_skills():

    skills_df = pd.read_csv(
        "data/skills.csv",
        header=None
    )

    skills = skills_df[0].tolist()

    return skills


def extract_skills(resume_text):

    resume_text = resume_text.lower()

    skills_db = load_skills()

    detected_skills = []

    for skill in skills_db:

        if skill.lower() in resume_text:

            detected_skills.append(skill)

    return sorted(list(set(detected_skills)))