import re


def extract_email(text):

    email_pattern = r'[\w\.-]+@[\w\.-]+\.\w+'

    emails = re.findall(email_pattern, text)

    return emails[0] if emails else "Not Found"


def extract_phone(text):

    phone_pattern = r'(\+?\d[\d\s\-\(\)]{8,18}\d)'

    phones = re.findall(phone_pattern, text)

    return phones[0] if phones else "Not Found"


def extract_linkedin(text):

    linkedin_pattern = r'(?:https?://)?(?:www\.)?linkedin\.com/in/[A-Za-z0-9_-]+/?'

    linkedin = re.findall(linkedin_pattern, text)

    if linkedin:
        return linkedin[0]

    return "Not Found"


def extract_github(text):

    github_pattern = r'(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_-]+/?'

    github = re.findall(github_pattern, text)

    if github:
        return github[0]

    return "Not Found"


def extract_name(text):

    lines = text.split("\n")

    ignore = {
        "resume",
        "curriculum vitae",
        "profile",
        "summary",
        "objective"
    }

    for line in lines[:5]:

        line = line.strip()

        if line.lower() in ignore:
            continue

        if 2 <= len(line.split()) <= 4 and line.replace(" ", "").isalpha():
            return line
        
    


def extract_education(text):

    patterns = [
        r'Bachelor of Technology.*',
        r'Bachelor of Engineering.*',
        r'Master of Technology.*',
        r'Master of Computer Applications.*',
        r'Bachelor of Computer Applications.*',
        r'Master of Business Administration.*',
        r'B\.Tech.*',
        r'B\.E.*',
        r'M\.Tech.*',
        r'MCA.*',
        r'MBA.*'
    ]

    for pattern in patterns:

        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            return match.group().strip()

    return "Not Found"


def extract_candidate_details(text):

    return {

        "name": extract_name(text),

        "email": extract_email(text),

        "phone": extract_phone(text),

        "linkedin": extract_linkedin(text),

        "github": extract_github(text),

        "education": extract_education(text)

    }