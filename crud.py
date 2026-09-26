from database import get_db_connection, insert_and_get_id, is_unique_violation


# --- USER OPERATIONS ---
def create_user(email: str, password_hash: str, role: str = 'student') -> int:
    """Registers a new user and returns their ID."""
    with get_db_connection() as conn:
        try:
            user_id = insert_and_get_id(
                conn,
                "INSERT INTO users (email, password_hash, role) VALUES (?, ?, ?)",
                (email, password_hash, role),
            )
            conn.commit()
            return user_id
        except Exception as exc:
            if is_unique_violation(exc):
                return None
            raise


def get_user_by_email(email: str):
    """Fetches a user record by email for authentication."""
    with get_db_connection() as conn:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


# --- SYSTEM CONFIGURATION ---
def get_system_settings() -> dict:
    """Returns all editable app configuration values."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT setting_key, setting_value FROM system_settings").fetchall()
        return {row["setting_key"]: row["setting_value"] for row in rows}


def set_system_setting(setting_key: str, setting_value: str):
    """Creates or updates a single system setting."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO system_settings (setting_key, setting_value)
            VALUES (?, ?)
            ON CONFLICT(setting_key)
            DO UPDATE SET setting_value = excluded.setting_value, updated_at = CURRENT_TIMESTAMP
            """,
            (setting_key, setting_value),
        )
        conn.commit()


# --- CONTENT INGESTION OPERATIONS ---
def create_passage(custom_id: str, title: str, content: str, topic: str, difficulty: str, source: str) -> int:
    """Inserts a new RC passage."""
    with get_db_connection() as conn:
        passage_id = insert_and_get_id(
            conn,
            """
            INSERT INTO passages (custom_id, title, content, topic, difficulty, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (custom_id, title, content, topic, difficulty, source),
        )
        conn.commit()
        return passage_id


def create_question_with_options(passage_id: int, question_text: str, question_type: str, options: list) -> int:
    """Creates a question and all answer options in one transaction."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        question_id = insert_and_get_id(
            conn,
            """
            INSERT INTO questions (passage_id, question_text, question_type)
            VALUES (?, ?, ?)
            """,
            (passage_id, question_text, question_type),
        )

        for opt in options:
            cursor.execute(
                """
                INSERT INTO options (question_id, label, option_text, is_correct, explanation, trap_explanation, supporting_lines)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    question_id,
                    opt['label'],
                    opt['text'],
                    opt.get('is_correct', 0),
                    opt.get('explanation', ''),
                    opt.get('trap_explanation', ''),
                    opt.get('supporting_lines', ''),
                ),
            )
        conn.commit()
        return question_id


# --- FRONTEND READ OPERATIONS ---
def get_all_passages(published_only: bool = True):
    """Retrieves passages for the RC Library."""
    with get_db_connection() as conn:
        query = "SELECT * FROM passages"
        if published_only:
            query += " WHERE is_published = 1"
        query += " ORDER BY created_at DESC"
        return conn.execute(query).fetchall()


def get_passage_full_data(passage_id: int) -> dict:
    """Fetches a passage, its nested questions, and nested options for the Exam Engine."""
    with get_db_connection() as conn:
        passage_row = conn.execute("SELECT * FROM passages WHERE id = ?", (passage_id,)).fetchone()
        if not passage_row:
            return None

        full_data = dict(passage_row)
        questions = conn.execute("SELECT * FROM questions WHERE passage_id = ?", (passage_id,)).fetchall()
        full_data['questions'] = []

        for q in questions:
            q_dict = dict(q)
            options = conn.execute("SELECT * FROM options WHERE question_id = ? ORDER BY id", (q['id'],)).fetchall()
            q_dict['options'] = [dict(o) for o in options]
            full_data['questions'].append(q_dict)

        return full_data


def get_benchmarks():
    with get_db_connection() as conn:
        return conn.execute("SELECT * FROM benchmarks ORDER BY exam_name").fetchall()


def upsert_benchmark(exam_name: str, target_accuracy: float, target_time_per_question: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO benchmarks (exam_name, target_accuracy, target_time_per_question)
            VALUES (?, ?, ?)
            ON CONFLICT(exam_name)
            DO UPDATE SET target_accuracy = excluded.target_accuracy,
                          target_time_per_question = excluded.target_time_per_question
            """,
            (exam_name, target_accuracy, target_time_per_question),
        )
        conn.commit()


def get_user_attempt_history(user_id: int):
    """Returns recent attempt summaries for a student."""
    with get_db_connection() as conn:
        attempts = conn.execute(
            """
            SELECT a.*, p.custom_id, p.title, p.topic, p.difficulty
            FROM attempts a
            JOIN passages p ON p.id = a.passage_id
            WHERE a.user_id = ?
            ORDER BY a.created_at DESC
            """,
            (user_id,),
        ).fetchall()
        return [dict(row) for row in attempts]


def get_attempt_review(user_id: int, attempt_id: int):
    """Returns detailed question-by-question review for a specific attempt."""
    with get_db_connection() as conn:
        attempt = conn.execute(
            "SELECT * FROM attempts WHERE id = ? AND user_id = ?",
            (attempt_id, user_id),
        ).fetchone()
        if not attempt:
            return None

        rows = conn.execute(
            """
            SELECT
                qa.id,
                q.id AS question_id,
                q.question_text,
                q.question_type,
                qa.selected_option_id,
                qa.selected_text,
                qa.time_taken_seconds,
                qa.is_correct,
                so.label AS selected_label,
                so.option_text AS selected_text_value,
                co.label AS correct_label,
                co.option_text AS correct_option_text,
                co.explanation,
                co.trap_explanation,
                co.supporting_lines
            FROM question_attempts qa
            JOIN questions q ON q.id = qa.question_id
            LEFT JOIN options so ON so.id = qa.selected_option_id
            LEFT JOIN options co ON co.question_id = q.id AND co.is_correct = 1
            WHERE qa.attempt_id = ?
            ORDER BY q.id
            """,
            (attempt_id,),
        ).fetchall()

        result = []
        for row in rows:
            item = dict(row)
            item['user_selected'] = row['selected_text'] if row['selected_text'] else row['selected_text_value']
            item['correct_answer'] = row['correct_option_text'] if row['correct_option_text'] else row['correct_label']
            item['reasoning'] = row['explanation'] or row['trap_explanation'] or row['supporting_lines'] or "No detailed explanation is provided for this question in the uploaded source."
            result.append(item)
        return result


# --- TELEMETRY & ATTEMPT TRACKING ---
def record_attempt(user_id: int, passage_id: int, total_score: float | None, reading_time: int, total_time: int, question_attempts: list) -> int:
    """Records a completed exam attempt and granular question timings."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        attempt_id = insert_and_get_id(
            conn,
            """
            INSERT INTO attempts (user_id, passage_id, total_score, reading_time_seconds, total_time_seconds)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, passage_id, total_score, reading_time, total_time),
        )

        for qa in question_attempts:
            cursor.execute(
                """
                INSERT INTO question_attempts (attempt_id, question_id, selected_option_id, selected_text, time_taken_seconds, is_correct)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    qa['question_id'],
                    qa.get('selected_option_id'),
                    qa.get('selected_text', ''),
                    qa.get('time_taken_seconds', 0),
                    qa.get('is_correct', 0),
                ),
            )

        conn.commit()
        return attempt_id


def get_student_analytics(user_id: int, limit: int = 10):
    """Collects recent metrics for grade and time analysis."""
    attempts = get_user_attempt_history(user_id)[:limit]
    if not attempts:
        return {"attempts": [], "avg_score": 0, "avg_time": 0, "last_10_scores": [], "last_10_times": []}

    score_series = [float(a["total_score"]) for a in attempts]
    time_series = [int(a["total_time_seconds"]) for a in attempts]
    return {
        "attempts": attempts,
        "avg_score": round(sum(score_series) / len(score_series), 2),
        "avg_time": round(sum(time_series) / len(time_series), 1),
        "last_10_scores": score_series,
        "last_10_times": time_series,
    }