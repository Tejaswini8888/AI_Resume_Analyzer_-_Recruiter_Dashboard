import streamlit as st

st.set_page_config(
    page_title="Resume Analysis",
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
        "<h1 style='text-align:center;'>📄 Resume Analysis</h1>",
        unsafe_allow_html=True
    )

    uploaded_file = st.file_uploader(
        "📂 Upload Resume",
        type=["pdf"]
    )

    st.markdown(
        "<br>",
        unsafe_allow_html=True
    )

    job_description = st.text_area(
        "📋 Job Description(Optional)",
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
            "🚀 Analyze Resume",
            use_container_width=True
        ):

            if uploaded_file is None:

                st.warning("Upload a resume.")
                st.stop()

            st.session_state.resume_file = uploaded_file
            st.session_state.job_description = job_description  # Optional

            st.switch_page("pages/2_Resume_Results.py")