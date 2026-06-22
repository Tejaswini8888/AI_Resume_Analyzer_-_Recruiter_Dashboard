import streamlit as st

st.set_page_config(
    page_title="Recruiter Dashboard",
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

left, center, right = st.columns([1, 3, 1])

with center:

    st.markdown(
        "<h1 style='text-align:center;'>👥 Recruiter Dashboard</h1>",
        unsafe_allow_html=True
    )

    uploaded_files = st.file_uploader(
        "📂 Upload Multiple Resumes",
        type=["pdf"],
        accept_multiple_files=True
    )

    st.markdown(
        "<br>",
        unsafe_allow_html=True
    )

    recruiter_jd = st.text_area(
        "📋 Job Description",
        height=220,
        placeholder="Paste the Job Description..."
    )

    st.markdown(
        "<br>",
        unsafe_allow_html=True
    )

    col1, col2 = st.columns(2)

    with col1:

        if st.button(
            "⬅ Back",
            use_container_width=True
        ):
            st.switch_page("app.py")

    with col2:

        if st.button(
            "📊 Analyze Candidates",
            use_container_width=True
        ):

            if not uploaded_files:

                st.warning("Upload resumes first.")
                st.stop()

            if not recruiter_jd:

                st.warning("Paste the Job Description.")
                st.stop()

            st.session_state.uploaded_files = uploaded_files
            st.session_state.recruiter_jd = recruiter_jd

            st.switch_page(
                "pages/4_Recruiter_Results.py"
            )