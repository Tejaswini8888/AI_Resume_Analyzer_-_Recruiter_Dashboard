from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer
)

from reportlab.lib.styles import getSampleStyleSheet

import tempfile


def generate_pdf_report(
    candidate,
    summary,
    ats_score,
    skills,
    match_score,
    missing_skills,
    ai_analysis,
    questions
):

    temp_file = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=".pdf"
    )

    pdf = SimpleDocTemplate(
        temp_file.name
    )

    styles = getSampleStyleSheet()

    content = []

    content.append(
        Paragraph(
            "AI Resume Analysis Report",
            styles["Title"]
        )
    )

    content.append(
        Spacer(1, 12)
    )

    content.append(
        Paragraph(
            f"Name: {candidate['name']}",
            styles["Normal"]
        )
    )

    content.append(
        Paragraph(
            f"Email: {candidate['email']}",
            styles["Normal"]
        )
    )

    content.append(
        Spacer(1, 12)
    )

    content.append(
        Paragraph(
            f"ATS Score: {ats_score}/100",
            styles["Heading2"]
        )
    )

    content.append(
        Paragraph(
            f"JD Match Score: {match_score if match_score else 0}%",
            styles["Heading2"]
        )
    )

    content.append(
        Spacer(1, 12)
    )

    content.append(
        Paragraph(
            "Resume Summary",
            styles["Heading2"]
        )
    )

    content.append(
        Paragraph(
            summary,
            styles["BodyText"]
        )
    )

    content.append(
        Spacer(1, 12)
    )

    content.append(
        Paragraph(
            "Detected Skills",
            styles["Heading2"]
        )
    )

    content.append(
        Paragraph(
            ", ".join(skills),
            styles["BodyText"]
        )
    )

    content.append(
        Spacer(1, 12)
    )

    content.append(
        Paragraph(
            "Missing Skills",
            styles["Heading2"]
        )
    )

    content.append(
        Paragraph(
            ", ".join(missing_skills)
            if missing_skills
            else "None",
            styles["BodyText"]
        )
    )

    content.append(
        Spacer(1, 12)
    )

    content.append(
        Paragraph(
            "AI Analysis",
            styles["Heading2"]
        )
    )

    content.append(
        Paragraph(
            ai_analysis.replace("\n", "<br/>"),
            styles["BodyText"]
        )
    )

    content.append(
        Spacer(1, 12)
    )

    content.append(
        Paragraph(
            "Interview Questions",
            styles["Heading2"]
        )
    )

    content.append(
        Paragraph(
            questions.replace("\n", "<br/>"),
            styles["BodyText"]
        )
    )

    pdf.build(content)

    return temp_file.name