import pandas as pd
import streamlit as st

from crud import get_benchmarks, get_db_connection


def fetch_user_metrics(user_id: int):
    """Retrieves all attempts for a specific user and converts them into a Pandas DataFrame."""
    with get_db_connection() as conn:
        attempts = conn.execute(
            """
            SELECT
                a.id AS attempt_id,
                a.total_score,
                a.total_time_seconds,
                a.created_at AS attempt_date,
                p.topic,
                p.difficulty,
                p.custom_id
            FROM attempts a
            JOIN passages p ON a.passage_id = p.id
            WHERE a.user_id = ?
            ORDER BY a.created_at ASC
            """,
            (user_id,),
        ).fetchall()

    if not attempts:
        return pd.DataFrame()

    df = pd.DataFrame([dict(row) for row in attempts])
    if not df.empty:
        df['attempt_date'] = pd.to_datetime(df['attempt_date'])
    return df


def render_dashboard(user_id: int):
    """Renders the visual analytics dashboard in the Streamlit UI."""
    st.header("Performance Analytics & Reporting")

    df = fetch_user_metrics(user_id)
    if df.empty:
        st.info("No exam data available. Complete an RC passage to generate analytics.")
        return

    total_completed = len(df)
    scored_df = df.dropna(subset=['total_score'])
    avg_accuracy = float(scored_df['total_score'].mean()) if not scored_df.empty else None
    avg_time = float(df['total_time_seconds'].mean())

    col1, col2, col3 = st.columns(3)
    col1.metric("Total RCs Completed", total_completed)
    col2.metric("Average Accuracy", f"{avg_accuracy:.1f}%" if avg_accuracy is not None else "N/A")
    col3.metric("Avg Time per RC", f"{int(avg_time // 60)}m {int(avg_time % 60)}s")

    st.markdown("---")
    st.subheader("Accuracy Trend (Scored Attempts)")
    if scored_df.empty:
        st.info("Accuracy analytics are unavailable until you complete a passage with an answer key.")
    else:
        st.line_chart(scored_df.set_index('attempt_date')['total_score'])

    st.subheader("Performance by Topic")
    if scored_df.empty:
        st.info("Topic accuracy will appear after a scored attempt.")
    else:
        topic_group = scored_df.groupby('topic', dropna=False)['total_score'].mean().reset_index()
        st.bar_chart(topic_group.set_index('topic'))

    st.subheader("Time vs Score")
    if not scored_df.empty:
        time_score = scored_df[['total_time_seconds', 'total_score']].copy()
        time_score = time_score.rename(columns={'total_time_seconds': 'Time (s)', 'total_score': 'Score (%)'})
        st.scatter_chart(time_score)

    if len(scored_df) >= 10:
        st.markdown("---")
        st.subheader("Every 10-RC Benchmark Report")
        recent_10 = scored_df.tail(10)
        recent_acc = float(recent_10['total_score'].mean())
        recent_time = float(recent_10['total_time_seconds'].mean())

        st.write(f"**Last 10 Passages Average Score:** {recent_acc:.1f}%")
        st.write(f"**Last 10 Passages Average Time:** {int(recent_time // 60)}m {int(recent_time % 60)}s")

        benchmark_rows = get_benchmarks()
        if benchmark_rows:
            st.write("**Peer benchmark reference (IPMAT / JIPMAT / other RC formats):**")
            benchmark_df = pd.DataFrame([dict(row) for row in benchmark_rows])
            st.dataframe(benchmark_df, use_container_width=True)

        if avg_accuracy is not None and recent_acc > avg_accuracy:
            st.success("Trending Up: Your recent accuracy is better than your historical average.")
        else:
            st.warning("Plateau Detected: Focus on timed practice and passage mapping to improve RC accuracy.")

    st.markdown("---")
    st.subheader("Exam Benchmark Snapshot")
    benchmark_rows = get_benchmarks()
    if benchmark_rows:
        benchmark_df = pd.DataFrame([dict(row) for row in benchmark_rows])
        benchmark_df['target_accuracy'] = benchmark_df['target_accuracy'].astype(float)
        benchmark_df['target_time_per_question'] = benchmark_df['target_time_per_question'].astype(int)
        st.bar_chart(benchmark_df.set_index('exam_name')['target_accuracy'])
        st.dataframe(benchmark_df, use_container_width=True)
    else:
        st.info("No benchmark data available yet.")