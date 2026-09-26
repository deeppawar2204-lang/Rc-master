import os
import tempfile
import time

import pandas as pd
import streamlit as st

import auth
import crud
import database
import pdf_processor
import analytics


database.initialize_database()
st.set_page_config(page_title="RC Mastery Platform", layout="wide")
auth.initialize_session()
auth.bootstrap_admin_from_secrets()


def get_app_title() -> str:
    settings = crud.get_system_settings()
    return settings.get("app_title", "RC Mastery Platform")


def _clear_exam_state():
    """Removes an exam and its answers so later sessions start cleanly."""
    for key in list(st.session_state):
        if key.startswith("q_radio_") or key in {
            "active_exam_id",
            "exam_start_time",
            "exam_submitted",
            "exam_result",
        }:
            st.session_state.pop(key, None)


def _select_rc_library():
    st.session_state['go_to_rc_library_after_import'] = True


def login_page():
    """Renders the authentication portal for both Students and Admins."""
    st.title(f"{get_app_title()} - Authentication")
    tab1, tab2 = st.tabs(["Login", "Register"])

    with tab1:
        with st.form("login_form"):
            email = st.text_input("Email Address")
            password = st.text_input("Password", type="password")
            submit = st.form_submit_button("Login")

            if submit:
                if auth.authenticate_user(email, password):
                    st.rerun()
                else:
                    st.error("Invalid credentials. Please try again.")

    with tab2:
        with st.form("register_form"):
            reg_email = st.text_input("Email Address")
            reg_password = st.text_input("Password", type="password")
            reg_submit = st.form_submit_button("Register Account")

            if reg_submit:
                if auth.register_new_user(reg_email, reg_password):
                    st.success("Registration successful! You may now login.")
                else:
                    st.error("This email is already registered.")


def admin_dashboard():
    """Provides the interface for uploading PDFs and managing system configuration."""
    st.header("Admin Panel - Content Ingestion")
    combined_tab, separate_tab = st.tabs(["Combined PDF", "Separate PDFs"])

    with combined_tab:
        st.markdown("Upload one PDF containing each passage followed by its questions and options.")
        uploaded_file = st.file_uploader("Combined passage and questions PDF", type=["pdf"], key="combined_rc_pdf")
        if uploaded_file is not None and st.button("Process Combined PDF", key="process_combined_pdf"):
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(uploaded_file.getvalue())
                    tmp_path = tmp.name
                passages_count = pdf_processor.parse_and_ingest_pdf(tmp_path, source_name=uploaded_file.name)
                st.success(f"Imported {passages_count} passages from {uploaded_file.name}.")
                _select_rc_library()
                st.rerun()
            except Exception as exc:
                st.error(f"PDF import failed: {str(exc)}")
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

    with separate_tab:
        st.markdown(
            "Upload all three PDFs separately. "
            "Use matching passage numbers in all files (the passage PDF may include a heading, "
            "such as `Passage 1: Topic: Subtitle`). In the questions PDF, "
            "number each question (for example, `1.`) and label its options (A), (B), etc. "
            "In the answer-key PDF, write each question number and correct letter "
            "(for example, `1. A`) under the matching passage header."
        )
        passage_pdf = st.file_uploader("1. Passages PDF", type=["pdf"], key="separate_passages_pdf")
        questions_pdf = st.file_uploader("2. Questions PDF", type=["pdf"], key="separate_questions_pdf")
        answer_key_pdf = st.file_uploader("3. Answer key PDF", type=["pdf"], key="separate_answer_key_pdf")

        if st.button("Process Separate PDFs", key="process_separate_pdfs"):
            if not passage_pdf or not questions_pdf or not answer_key_pdf:
                st.error("Upload the passages, questions, and answer-key PDFs to continue.")
            else:
                temp_paths = []
                try:
                    uploads = [passage_pdf, questions_pdf, answer_key_pdf]
                    for upload in uploads:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                            tmp.write(upload.getvalue())
                            temp_paths.append(tmp.name)

                    with st.spinner("Importing passages, questions, and answers..."):
                        passage_count, question_count = pdf_processor.parse_and_ingest_separate_pdfs(
                            temp_paths[0],
                            temp_paths[1],
                            temp_paths[2],
                            source_name=passage_pdf.name,
                        )
                    st.success(f"Imported {passage_count} passages and {question_count} questions.")
                    _select_rc_library()
                    st.rerun()
                except Exception as exc:
                    st.error(f"Separate PDF import failed: {str(exc)}")
                finally:
                    for temp_path in temp_paths:
                        if os.path.exists(temp_path):
                            os.unlink(temp_path)

    st.markdown("---")
    st.header("System Benchmarks")
    benchmarks = crud.get_benchmarks()
    if benchmarks:
        benchmark_df = pd.DataFrame([dict(row) for row in benchmarks])
        st.dataframe(benchmark_df, use_container_width=True)

    with st.form("benchmark_form"):
        exam_name = st.text_input("Exam Name", value="IPMAT Indore")
        target_accuracy = st.number_input("Target Accuracy (%)", min_value=0.0, max_value=100.0, value=78.0, step=1.0)
        target_time = st.number_input("Target Time per Question (sec)", min_value=10, max_value=300, value=65, step=5)
        submitted = st.form_submit_button("Save Benchmark")
        if submitted:
            crud.upsert_benchmark(exam_name, float(target_accuracy), int(target_time))
            st.success("Benchmark updated successfully.")

    st.markdown("---")
    st.header("App Settings")
    settings = crud.get_system_settings()
    app_title = st.text_input("App Title", value=settings.get("app_title", "RC Mastery Platform"))
    if st.button("Apply Settings"):
        crud.set_system_setting("app_title", app_title)
        st.success("System settings updated.")


def rc_library():
    """Displays all available content and acts as the entry point for exams."""
    st.header("RC Library")
    passages = crud.get_all_passages(published_only=False)

    if not passages:
        st.info("The library is currently empty. Switch to the Admin Panel to ingest a PDF.")
        return

    df = pd.DataFrame([dict(p) for p in passages])
    st.dataframe(df[['custom_id', 'title', 'topic', 'difficulty', 'source']], use_container_width=True)

    st.markdown("### Start Practice Session")
    passage_by_id = df.set_index('id').to_dict('index')
    selected_id = st.selectbox(
        "Select Passage to Attempt",
        df['id'].tolist(),
        format_func=lambda passage_id: (
            f"{passage_by_id[passage_id]['title']} "
            f"({passage_by_id[passage_id]['topic']})"
        ),
    )

    if st.button("Launch Exam Mode"):
        _clear_exam_state()
        st.session_state['active_exam_id'] = selected_id
        st.session_state['exam_start_time'] = time.time()
        st.session_state['exam_submitted'] = False
        st.rerun()


def _render_question_widget(question: dict, key_prefix: str):
    """Renders question widgets for MCQ and short-answer formats."""
    q_id = question['id']
    question_type = question.get('question_type', 'Fact Based')
    options = question.get('options', [])

    if options:
        option_labels = [
            {"label": opt['label'], "text": opt['option_text'], "id": opt['id'], "is_correct": bool(opt['is_correct'])}
            for opt in options
        ]
        if question_type in {"Sentence Completion", "Para Summary"}:
            selection = st.selectbox(
                question['question_text'],
                options=[f"({opt['label']}) {opt['text']}" for opt in option_labels],
                index=None,
                key=f'{key_prefix}_{q_id}_select',
                placeholder='Choose the best answer',
            )
            return {
                'selected_option_id': None,
                'selected_text': selection,
                'option_data': option_labels,
            }

        selection = st.radio(
            question['question_text'],
            options=option_labels,
            format_func=lambda x: f"({x['label']}) {x['text']}",
            key=f'{key_prefix}_{q_id}_radio',
            index=None,
        )
        return {
            'selected_option_id': selection['id'] if selection else None,
            'selected_text': selection['text'] if selection else '',
            'option_data': option_labels,
        }

    answer_text = st.text_input(
        question['question_text'],
        key=f'{key_prefix}_{q_id}_text',
        placeholder='Type your answer here',
    )
    return {
        'selected_option_id': None,
        'selected_text': answer_text,
        'option_data': [],
    }


def exam_engine():
    """Renders a single-page exam layout with persistent timer and post-submission grading."""
    passage_id = st.session_state['active_exam_id']
    data = crud.get_passage_full_data(passage_id)

    if not data:
        st.error("Fatal Error: Could not load passage data.")
        return

    st.header(f"Exam Mode: {data['title']}")
    st.caption(f"Topic: {data['topic']} | Passage ID: {data['custom_id']}")

    st.session_state.setdefault('exam_submitted', False)

    col1, col2 = st.columns([1.15, 1])

    with col1:
        st.subheader("Passage")
        st.write(data['content'])

    with col2:
        st.subheader("Questions")

        with st.form("exam_form"):
            answer_tracker = {}

            for q in data['questions']:
                st.markdown(f"**{q['question_text']}**")
                answer_tracker[q['id']] = st.radio(
                    "Select Answer",
                    options=q['options'],
                    format_func=lambda x: f"({x['label']}) {x['option_text']}",
                    key=f"q_radio_{q['id']}",
                    index=None,
                    disabled=st.session_state['exam_submitted'],
                )
                st.markdown("---")

            submit_button = st.form_submit_button("Submit Attempt", disabled=st.session_state['exam_submitted'])

            if submit_button:
                total_time = int(time.time() - st.session_state['exam_start_time'])
                correct_count = 0
                question_results = {}
                q_attempts = []
                has_complete_answer_key = all(
                    any(opt['is_correct'] for opt in q['options'])
                    for q in data['questions']
                )

                for q in data['questions']:
                    selected_opt = answer_tracker.get(q['id'])
                    correct_opt = next((opt for opt in q['options'] if opt['is_correct']), None)
                    is_correct = (
                        selected_opt['id'] == correct_opt['id']
                        if selected_opt and correct_opt else None
                    )
                    correct_count += int(is_correct is True)
                    question_results[q['id']] = {
                        'selected_option_id': selected_opt['id'] if selected_opt else None,
                        'is_correct': is_correct,
                    }
                    q_attempts.append({
                        'question_id': q['id'],
                        'selected_option_id': selected_opt['id'] if selected_opt else None,
                        'time_taken_seconds': 0,
                        'is_correct': int(is_correct) if is_correct is not None else None,
                    })

                total_score = (
                    correct_count / len(data['questions']) * 100
                    if data['questions'] and has_complete_answer_key else None
                )
                attempt_id = crud.record_attempt(
                    user_id=st.session_state['user_id'],
                    passage_id=passage_id,
                    total_score=total_score,
                    reading_time=total_time // 2,
                    total_time=total_time,
                    question_attempts=q_attempts,
                )
                st.session_state['exam_result'] = {
                    'attempt_id': attempt_id,
                    'answers': question_results,
                    'correct_count': correct_count,
                    'total_questions': len(data['questions']),
                    'total_time': total_time,
                    'total_score': total_score,
                }
                st.session_state['exam_submitted'] = True
                st.rerun()

        if st.session_state['exam_submitted']:
            result = st.session_state['exam_result']
            st.markdown("### Attempt Results")

            for q in data['questions']:
                answer = result['answers'][q['id']]
                selected_opt = next(
                    (opt for opt in q['options'] if opt['id'] == answer['selected_option_id']),
                    None,
                )
                correct_opt = next((opt for opt in q['options'] if opt['is_correct']), None)

                st.markdown(f"**Question:** {q['question_text']}")
                if answer['is_correct']:
                    st.success(f"Your answer: ({selected_opt['label']}) {selected_opt['option_text']} — Correct")
                else:
                    if selected_opt:
                        st.write(f"Your answer: ({selected_opt['label']}) {selected_opt['option_text']}")
                    else:
                        st.write("Your answer: No answer selected")
                    if correct_opt:
                        st.error(f"Correct answer: ({correct_opt['label']}) {correct_opt['option_text']}")
                    else:
                        st.warning("Correct answer unavailable. This passage has no imported answer key.")

                if correct_opt and correct_opt.get('explanation'):
                    st.info(f"**Explanation:** {correct_opt['explanation']}")
                st.markdown("---")

            if result['total_score'] is not None:
                st.metric("Final Score", f"{result['correct_count']} / {result['total_questions']}")
            else:
                st.info("This attempt was saved, but a score is unavailable because one or more answers are missing from the answer key.")
            total_time = result['total_time']
            st.metric("Time Taken", f"{total_time // 60}m {total_time % 60}s")

            st.button("Return to Library", on_click=_clear_exam_state)

def review_history():
    """Displays past attempted passages with score and review links."""
    st.header("Attempt History")
    attempts = crud.get_user_attempt_history(st.session_state['user_id'])
    if not attempts:
        st.info("You have not completed any RC passages yet.")
        return

    df = pd.DataFrame(attempts)
    st.dataframe(
        df[['id', 'custom_id', 'title', 'total_score', 'total_time_seconds', 'created_at']],
        use_container_width=True,
    )

    attempt_id = st.selectbox(
        "Select an attempt to review",
        [attempt['id'] for attempt in attempts],
        format_func=lambda x: next(a['custom_id'] for a in attempts if a['id'] == x),
    )

    review_data = crud.get_attempt_review(st.session_state['user_id'], attempt_id)
    if review_data:
        st.markdown("### Detailed Review")
        for idx, item in enumerate(review_data, start=1):
            if item['is_correct'] is None:
                st.warning(f"Q{idx}: Not scored (answer key unavailable)")
            elif item['is_correct']:
                st.success(f"Q{idx}: Correct")
            else:
                st.error(f"Q{idx}: Incorrect")
            st.write(f"Question: {item['question_text']}")
            st.write(f"Selected: {item['user_selected'] or 'No answer'}")
            st.write(f"Correct: {item['correct_answer'] or 'Not available'}")
            st.write(f"Time: {item['time_taken_seconds']} seconds")
            st.write(f"Explanation: {item['reasoning']}")
            st.markdown("---")


def student_dashboard():
    """Shows detailed analytics and benchmark comparison for the student."""
    analytics.render_dashboard(st.session_state['user_id'])


def main():
    """Main routing function handling navigation and state logic."""
    if not st.session_state['is_authenticated']:
        login_page()
        return

    if st.session_state.pop('go_to_rc_library_after_import', False):
        st.session_state['navigation_choice'] = "RC Library"

    with st.sidebar:
        st.write(f"👤 **{st.session_state['user_email']}**")
        menu_options = ["RC Library", "Dashboard", "Attempt History"]
        if st.session_state['role'] == 'admin':
            menu_options.append("Admin Panel")
        choice = st.radio("Navigation", menu_options, key="navigation_choice")
        st.markdown("---")
        if st.button("Logout", use_container_width=True):
            _clear_exam_state()
            auth.logout_user()
            st.rerun()

    if 'active_exam_id' in st.session_state:
        exam_engine()
        return

    if choice == "RC Library":
        rc_library()
    elif choice == "Dashboard":
        analytics.render_dashboard(st.session_state['user_id'])

    elif choice == "Attempt History":
        review_history()
    elif choice == "Admin Panel":
        admin_dashboard()


if __name__ == "__main__":
    main()
