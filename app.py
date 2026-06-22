import streamlit as st

st.set_page_config(
    page_title="AI Resume Analyzer",
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
    <div style="
        padding:25px;
        border-radius:15px;
        border:1px solid #ddd;
        text-align:center;
        margin-bottom:30px;
    ">

    <h1>🤖 AI Resume Analyzer</h1>

    <p>
            AI Resume Analysis • ATS Scoring • JD Matching •
            AI Insights • Interview Questions • Recruiter Dashboard
    </p>

    </div>
    """,
    unsafe_allow_html=True
)

col1, col2 = st.columns(2)

with col1:

    st.subheader("📄 Single Resume Analysis")

    st.write(
        "ATS scoring, "
        "AI insights, JD matching and interview questions."
    )

    if st.button(
        "🚀 Start Resume Analysis",
        use_container_width=True
    ):
        st.switch_page("pages/1_Resume_Analysis.py")

with col2:

    st.subheader("👥 Recruiter Dashboard")

    st.write(
        "Compare multiple resumes and rank candidates."
    )

    if st.button(
        "📊 Open Recruiter Dashboard",
        use_container_width=True
    ):
        st.switch_page("pages/3_Recruiter_Dashboard.py")