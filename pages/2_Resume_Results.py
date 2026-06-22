import streamlit as st
import pandas as pd
import plotly.express as px

from modules.parser import extract_text_from_pdf

from modules.ai_analysis import (
    generate_resume_summary,
    analyze_resume
)

from modules.candidate_details import (
    extract_candidate_details
)

from modules.skills import extract_skills

from modules.ats_score import calculate_ats_score

from modules.jd_matcher import calculate_jd_match

from modules.missing_skills import (
    extract_jd_skills,
    find_missing_skills
)

from modules.interview_generator import (
    generate_interview_questions
)

from modules.pdf_report import generate_pdf_report


st.set_page_config(
    page_title="Resume Results",
    layout="wide"
)

st.markdown(
    """
    <style>
    [data-testid="stSidebar"] {
        display: none;
    }

    [data-testid="collapsedControl"] {
        display: none;
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.markdown(
    """
    <h1 style='text-align:center;'>📊 Resume Analysis Results</h1>
    <p style='text-align:center;color:gray;'>
        AI Resume Evaluation • ATS Score • AI Insights • Interview Preparation
    </p>
    """,
    unsafe_allow_html=True
)

col1, col2, col3 = st.columns([6, 2, 2])

with col3:
    if st.button(
        "⬅ Analyze Another Resume",
        use_container_width=True
    ):
        st.session_state.clear()
        st.switch_page("pages/1_Resume_Analysis.py")

if "resume_file" not in st.session_state:
    st.warning("No resume uploaded.")
    st.stop()

uploaded_file = st.session_state.resume_file
job_description = st.session_state.job_description

st.success("✅ Resume uploaded successfully!")

if uploaded_file:
    resume_text = extract_text_from_pdf(uploaded_file)
else:
    st.warning("Please upload a resume PDF.")
    st.stop()

candidate = extract_candidate_details(resume_text)

skills = extract_skills(resume_text)

ats_score, breakdown = calculate_ats_score(
    resume_text,
    skills
)

match_score = None
missing_skills = []

if job_description.strip():

    match_score = calculate_jd_match(
        resume_text,
        job_description
    )

    jd_skills = extract_jd_skills(
        job_description
    )

    missing_skills = find_missing_skills(
        skills,
        jd_skills
    )

with st.spinner("🤖 Reading resume and generating AI insights..."):

    summary = generate_resume_summary(
        resume_text
    )

    ai_analysis = analyze_resume(
        resume_text
    )

    questions = generate_interview_questions(
        resume_text
    )

st.toast("Analysis Complete ✅")

st.markdown("---")

st.markdown("## 📈 Resume Dashboard")
st.caption(f"📄 {uploaded_file.name}")

c1, c2, c3, c4 = st.columns(4)

with c1:
    st.metric(
        "🎯 ATS Score",
        f"{ats_score}/100"
    )

with c2:
    st.metric(
        "📄 JD Match",
        f"{match_score if match_score is not None else 'N/A'}%"
    )

with c3:
    st.metric(
        "🛠 Skills",
        len(skills)
    )

with c4:
    st.metric(
        "⚠ Missing Skills",
        len(missing_skills)
    )

st.markdown("---")

overview_tab, analytics_tab, ai_tab, interview_tab, report_tab = st.tabs(
    [
        "📄 Overview",
        "📊 Analytics",
        "🤖 AI Insights",
        "🎯 Interview",
        "📥 Report"
    ]
)
# =========================
# Overview Tab
# =========================

with overview_tab:

    # ===================================
    # AI SUMMARY
    # ===================================

    st.subheader("🤖 AI Professional Summary")

    with st.container(border=True):
        st.markdown("### AI Generated Summary")
        st.write(summary)

    # ===================================
    # CANDIDATE DETAILS
    # ===================================

    left_col, right_col = st.columns([1, 1])

    # ----------------------------
    # Candidate Information
    # ----------------------------

    with left_col:

        st.subheader("👤 Candidate Information")

        with st.container(border=True):

            st.write("### Personal")

            st.write(f"**Name:** {candidate['name']}")
            st.write(f"**Email:** {candidate['email']}")
            st.write(f"**Phone:** {candidate['phone']}")

            st.markdown("---")

            st.write("### Professional")

            st.write(f"**LinkedIn:** {candidate['linkedin']}")
            st.write(f"**GitHub:** {candidate['github']}")
            st.write(
                f"**Education:** {candidate.get('education', 'Not Found')}"
            )

    # ----------------------------
    # Skills
    # ----------------------------

    with right_col:

        st.subheader("🛠 Technical Skills")

        with st.container(border=True):

            if skills:

                col1, col2 = st.columns(2)

                midpoint = (len(skills) + 1) // 2

                left_skills = skills[:midpoint]
                right_skills = skills[midpoint:]

                with col1:
                    for skill in left_skills:
                        st.write(f"• {skill}")

                with col2:
                    for skill in right_skills:
                        st.write(f"• {skill}")

            else:
                st.warning("No skills detected.")

    # ===================================
    # ATS SCORE BREAKDOWN
    # ===================================

    st.subheader("📈 ATS Score Breakdown")

    score_df = pd.DataFrame(
        {
            "Category": list(breakdown.keys()),
            "Score": list(breakdown.values())
        }
    )

    fig = px.pie(
        score_df,
        values="Score",
        names="Category",
        hole=0.60
    )

    fig.update_layout(
        width=500,
        height=400,
        margin=dict(
            l=20,
            r=20,
            t=20,
            b=20
        ),
    )

    left, center, right = st.columns([2, 3, 2])

    with center:

        st.plotly_chart(
            fig,
            use_container_width=False
        )

    # ===================================
    # JD MATCH
    # ===================================

    st.subheader("🎯 Resume vs Job Description")

    if match_score is not None:

        st.progress(match_score / 100)

        st.metric(
            "Match Score",
            f"{match_score}%"
        )

        st.markdown("### ⚠ Missing Skills")

        if missing_skills:

            cols = st.columns(3)

            for i, skill in enumerate(missing_skills):

                with cols[i % 3]:
                    st.error(skill)

        else:
            st.success("✅ No Missing Skills Found")

    else:

        st.info("Upload a Job Description to calculate JD Match.")

    # ===================================
    # INTERVIEW QUESTIONS
    # ===================================

    st.divider()

    st.subheader("🤖 AI Resume Analysis")

    with st.container(border=True):
        st.markdown(ai_analysis)

    st.subheader("🎯 Personalized Interview Questions")

    with st.container(border=True):
        st.markdown(questions)

    # ===================================
    # DOWNLOAD REPORT
    # ===================================

    st.subheader("📥 Export Report")

    pdf_file = generate_pdf_report(
        candidate,
        summary,
        ats_score,
        skills,
        match_score,
        missing_skills,
        ai_analysis,
        questions
    )

    with open(pdf_file, "rb") as file:

        st.download_button(
            "📄 Download Resume Analysis Report",
            data=file,
            file_name="Resume_Analysis_Report.pdf",
            mime="application/pdf",
            use_container_width=True,
            key="overview_download"
        )
# =========================
# Analytics Tab
# =========================

with analytics_tab:

    st.progress(ats_score / 100)

    st.subheader("📈 ATS Score Breakdown")

    score_df = pd.DataFrame(
        {
            "Category": list(breakdown.keys()),
            "Score": list(breakdown.values())
        }
    )

    fig = px.pie(
        score_df,
        values="Score",
        names="Category"
    )

    left, center, right = st.columns([2, 3, 2])

    with center:

        st.plotly_chart(
            fig,
            use_container_width=False
        )

    if ats_score >= 85:
        st.success("Excellent Resume")

    elif ats_score >= 70:
        st.info("Good Resume")

    else:
        st.warning("Needs Improvement")

    if match_score is not None:

        st.subheader("Resume vs Job Description Match")

        st.progress(match_score / 100)

        st.metric(
            "Match Score",
            f"{match_score}%"
        )

    if missing_skills:

        st.subheader("Missing Skills")

        for skill in missing_skills:
            st.warning(f"• {skill}")

    else:
        st.success("No Missing Skills Found")


# =========================
# AI Analysis Tab
# =========================

with ai_tab:

    st.subheader("🤖 AI Resume Analysis")

    st.markdown(ai_analysis)


# =========================
# Interview Questions Tab
# =========================

with interview_tab:

    st.subheader("🎯 Personalized Interview Questions")

    st.markdown(questions)


# =========================
# Report Download Tab
# =========================

with report_tab:

    pdf_file = generate_pdf_report(
        candidate,
        summary,
        ats_score,
        skills,
        match_score,
        missing_skills,
        ai_analysis,
        questions
    )

    with open(pdf_file, "rb") as file:

        st.subheader("📥 Export Report")

        st.download_button(
            "📄 Download Resume Analysis Report",
            data=file,
            file_name="Resume_Analysis_Report.pdf",
            mime="application/pdf",
            use_container_width=True,
            key="report_download"
        )




