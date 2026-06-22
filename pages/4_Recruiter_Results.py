import streamlit as st
import pandas as pd
import plotly.express as px

from modules.resume_ranker import rank_resumes

st.set_page_config(
    page_title="Recruiter Results",
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

st.title("👥 Recruiter Results")

if "uploaded_files" not in st.session_state:

    st.warning("No resumes uploaded.")
    st.stop()

uploaded_files = st.session_state.uploaded_files
job_description = st.session_state.recruiter_jd

if st.button("⬅ Compare New Candidates"):

    st.session_state.pop("uploaded_files", None)
    st.session_state.pop("recruiter_jd", None)

    st.switch_page("pages/3_Recruiter_Dashboard.py")

with st.spinner("Analyzing candidates..."):

    rankings = rank_resumes(
        uploaded_files,
        job_description
    )

    ranking_df = pd.DataFrame(rankings)

st.subheader("🏅 Candidate Ranking")

st.dataframe(
    ranking_df,
    use_container_width=True
)

fig = px.bar(
    ranking_df,
    x="Name",
    y="Final Score",
    title="Candidate Ranking Visualization",
)

st.plotly_chart(
    fig,
    use_container_width=True
)

fig = px.scatter(
    ranking_df,
    x="ATS Score",
    y="JD Match",
    size="Final Score",
    color="Final Score",
    hover_name="Name",
    title="ATS Score vs JD Match"
)

st.plotly_chart(
    fig,
    use_container_width=True
)

st.subheader("🥇 Top 3 Candidates")

st.dataframe(
    ranking_df.head(3),
    use_container_width=True
)

st.subheader("📋 Recruiter Insights")

col1, col2, col3 = st.columns(3)

with col1:

    st.metric(
        "Candidates",
        len(ranking_df)
    )

with col2:

    st.metric(
        "Average ATS",
        f"{ranking_df['ATS Score'].mean():.1f}"
    )

with col3:

    st.metric(
        "Average JD Match",
        f"{ranking_df['JD Match'].mean():.1f}%"
    )

winner = ranking_df.iloc[0]

st.markdown(
    f"""
    <div style="
        padding:10px;
        border-radius:10px;
        border:1px solid #ddd;
        text-align:center;
        margin-top:10px;
    ">

    <h3 style="margin-bottom:8px;">
        🏆 Best Candidate
    </h3>

    <h2 style="margin:10px 0;">
        {winner['Name']}
    </h2>

    <h4 style="margin-top:10px;">
        Final Score : {winner['Final Score']:.1f}
    </h4>

    </div>
    """,
    unsafe_allow_html=True
)

st.divider()

csv = ranking_df.to_csv(index=False)

st.download_button(
    "📥 Download Rankings CSV",
    csv,
    "candidate_rankings.csv",
    "text/csv",
    use_container_width=True
)