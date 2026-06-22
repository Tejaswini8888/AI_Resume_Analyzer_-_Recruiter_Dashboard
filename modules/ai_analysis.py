from groq import Groq
from dotenv import load_dotenv
import os

load_dotenv()

client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)

def generate_resume_summary(resume_text):

    prompt = f"""
    Generate a concise professional resume summary
    in 4-5 lines.

    Resume:

    {resume_text}
    """

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.3
    )

    return response.choices[0].message.content

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

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.3
    )

    return response.choices[0].message.content