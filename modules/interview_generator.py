from modules.ai_analysis import _chat


def generate_interview_questions(resume_text):
    """Generate 10 technical interview questions from a resume (Groq-powered)."""
    prompt = f"""
    Based on this resume generate
    10 technical interview questions.

    Resume:

    {resume_text}
    """

    return _chat(prompt)