import fitz  # PyMuPDF
import os
import re
import uuid
from crud import create_passage, create_question_with_options, get_all_passages

def extract_raw_text(file_path: str) -> str:
    """Reads a PDF document from disk and extracts all text into a single string."""
    try:
        doc = fitz.open(file_path)
        full_text = []
        for page in doc:
            full_text.append(page.get_text("text"))
        return "\n".join(full_text)
    except Exception as e:
        raise RuntimeError(f"Failed to read PDF: {str(e)}")

def parse_and_ingest_pdf(file_path: str, source_name: str = "Bulk Upload") -> int:
    """
    Parses standardized test-prep PDFs and ingests them into the database.
    Expected Heuristic Format:
    'PASSAGE X' -> Passage Text -> '1.' -> Question Stem -> '(A)' -> Option Text
    """
    raw_text = extract_raw_text(file_path)
    
    # 1. Split text into massive chunks by Passage headers
    # Matches "PASSAGE 1:", "Passage 02", etc.
    passage_blocks = re.split(r"(?i)PASSAGE\s+\d+\s*:?", raw_text)
    
    ingested_passages_count = 0
    
    for idx, block in enumerate(passage_blocks):
        if not block.strip():
            continue
            
        # 2. Separate Passage Text from the Questions
        # Looks for the first question marker (e.g., "1." or "Q1.") at the start of a line
        q_split = re.split(r"(?m)^(?:Q)?1\.", block, maxsplit=1)
        
        passage_text = q_split[0].strip()
        
        # Skip junk headers or empty pages (heuristic: passage must be > 100 chars)
        if len(passage_text) < 100: 
            continue
            
        # 3. Insert Passage into Database
        custom_id = f"RC_{source_name[:3].upper()}_{idx:04d}"
        passage_id = create_passage(
            custom_id=custom_id,
            title=f"Extracted RC - {idx}",
            content=passage_text,
            topic="Uncategorized",
            difficulty="Medium",
            source=source_name
        )
        ingested_passages_count += 1
        
        # 4. Process Questions if they exist in this block
        if len(q_split) > 1:
            questions_text = "1." + q_split[1] # Re-add the split marker
            
            # Split into individual questions by looking for numbers at line starts
            q_blocks = re.split(r"(?m)^(?:Q)?\d+\.", questions_text)
            
            for q_block in q_blocks:
                if not q_block.strip():
                    continue
                    
                # 5. Extract Options (Matches (A), (B), (C), (D) at the start of lines)
                opt_split = re.split(r"(?m)^\s*\([A-D]\)", q_block)
                q_stem = opt_split[0].strip()
                
                options_data = []
                labels = ['A', 'B', 'C', 'D']
                
                # Assemble options. Skip index 0 (which is the question stem)
                for i in range(1, min(len(opt_split), 5)):
                    options_data.append({
                        'label': labels[i-1],
                        'text': opt_split[i].strip(),
                        'is_correct': 0, # Requires manual admin key mapping or separate answer key regex
                        'explanation': ''
                    })
                
                # 6. Insert Question and Options into Database
                if q_stem and len(options_data) >= 2:
                    create_question_with_options(
                        passage_id=passage_id,
                        question_text=q_stem,
                        question_type="Fact Based", # Default assignment
                        options=options_data
                    )
                    
    return ingested_passages_count


def _extract_passage_sections(raw_text: str, section_type: str = "passages") -> dict:
    """Selects the best occurrence of each numbered passage for its content type."""
    headers = list(re.finditer(r"(?im)^\s*(?:PASSAGE|Passage)\s+(\d+)\b[^\n]*", raw_text))
    occurrences = {}
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(raw_text)
        occurrences.setdefault(int(header.group(1)), []).append(raw_text[header.end():end].strip())
    if not occurrences:
        raise ValueError("No numbered PASSAGE headers found. Add headers like 'PASSAGE 1' or 'Passage 1' to each PDF.")

    sections = {}
    for passage_number, candidates in occurrences.items():
        if section_type == "questions":
            sections[passage_number] = max(
                candidates,
                key=lambda text: (len(_parse_question_section(text, {})), len(text)),
            )
        elif section_type == "answers":
            sections[passage_number] = max(
                candidates,
                key=lambda text: len(re.findall(r"(?is)Answer\s*:\s*\(?[A-E]\)?|<b>\s*(?:Q\s*)?\d+\s*[.)]", text)),
            )
        else:
            sections[passage_number] = max(candidates, key=len)
    return sections


def _extract_passage_titles(raw_text: str) -> dict:
    """Returns readable passage headings keyed by passage number."""
    titles = {}
    for match in re.finditer(
        r"(?im)^\s*(?:PASSAGE|Passage)\s+(\d+)\s*:\s*(.+?)\s*$",
        raw_text,
    ):
        title = re.sub(r"\s+", " ", match.group(2)).strip()
        if title:
            titles[int(match.group(1))] = title
    return titles


def _passage_topic(title: str) -> str:
    """Uses a heading's topic portion when it has a topic/subtitle separator."""
    return title.split(":", maxsplit=1)[0].strip() or title


def _passage_custom_id(source_name: str, passage_number: int, used_ids: set[str]) -> str:
    source_stem = os.path.splitext(os.path.basename(source_name))[0]
    source_slug = re.sub(r"[^A-Z0-9]+", "_", source_stem.upper()).strip("_")
    source_prefix = "_".join(source_slug.split("_")[:3])[:24].rstrip("_") or "RC"
    base_id = f"{source_prefix}_{passage_number:03d}"
    custom_id = base_id
    suffix = 2
    while custom_id in used_ids:
        custom_id = f"{base_id}_{suffix}"
        suffix += 1
    used_ids.add(custom_id)
    return custom_id


def _parse_question_section(section_text: str, answer_key: dict) -> list:
    questions = []
    question_matches = list(re.finditer(r"(?is)<b>\s*(?:Q\s*)?(\d+)\s*[.)]\s*(.*?)(?=</b>|$)", section_text))

    if not question_matches:
        question_headers = list(re.finditer(
            r"(?m)^\s*(?:[Qq]\s*)?(\d+)[.)](?=[ \t]*(?:[A-Z]|\r?$))\s*",
            section_text,
        ))
        for index, header in enumerate(question_headers):
            end = question_headers[index + 1].start() if index + 1 < len(question_headers) else len(section_text)
            question_block = section_text[header.end():end].strip()
            option_headers = list(re.finditer(r"(?im)^\s*(?:\(([A-E])\)|([A-E])[).])\s*", question_block))
            if len(option_headers) < 2:
                option_headers = list(re.finditer(
                    r"(?i)(?<!\S)(?:\(([A-E])\)|([A-E])[).])(?=\s)",
                    question_block,
                ))
            if len(option_headers) < 2:
                continue

            question_text = question_block[:option_headers[0].start()].strip()
            options = []
            for option_index, option_header in enumerate(option_headers):
                option_end = option_headers[option_index + 1].start() if option_index + 1 < len(option_headers) else len(question_block)
                label = (option_header.group(1) or option_header.group(2)).upper()
                options.append({
                    "label": label,
                    "text": question_block[option_header.end():option_end].strip(),
                    "is_correct": int(answer_key.get(int(header.group(1))) == label),
                })

            lowered_question = question_text.lower()
            if "summary" in lowered_question:
                question_type = "Para Summary"
            elif "complete" in lowered_question or "fill" in lowered_question:
                question_type = "Sentence Completion"
            else:
                question_type = "Fact Based"
            if question_text:
                questions.append({
                    "number": int(header.group(1)),
                    "question_text": question_text,
                    "question_type": question_type,
                    "options": options,
                })
        return questions

    for match in question_matches:
        question_number = int(match.group(1))
        question_block = match.group(2).strip()
        option_matches = list(re.finditer(r"(?is)\(?([A-E])\)\s*(.*?)(?=(?:\s*\(?[A-E]\)|\s*$))", question_block))
        if len(option_matches) < 2:
            continue

        question_text = question_block[:option_matches[0].start()].strip()
        options = []
        for option_index, option_match in enumerate(option_matches):
            option_end = option_matches[option_index + 1].start() if option_index + 1 < len(option_matches) else len(question_block)
            label = option_match.group(1).upper()
            text_value = option_match.group(2).strip()
            if not text_value and option_index + 1 < len(option_matches):
                text_value = question_block[option_match.end():option_matches[option_index + 1].start()].strip()
            options.append({
                "label": label,
                "text": text_value,
                "is_correct": int(answer_key.get(question_number) == label),
            })

        lowered_question = question_text.lower()
        if "summary" in lowered_question:
            question_type = "Para Summary"
        elif "complete" in lowered_question or "fill" in lowered_question:
            question_type = "Sentence Completion"
        else:
            question_type = "Fact Based"
        if question_text:
            questions.append({
                "number": question_number,
                "question_text": question_text,
                "question_type": question_type,
                "options": options,
            })
    return questions


def parse_and_ingest_separate_pdfs(
    passages_path: str,
    questions_path: str,
    answer_key_path: str,
    source_name: str = "Separate PDFs",
) -> tuple[int, int]:
    """Imports passage, question, and answer-key PDFs linked by passage/question numbers."""
    passage_raw_text = extract_raw_text(passages_path)
    passage_sections = _extract_passage_sections(passage_raw_text, "passages")
    passage_titles = _extract_passage_titles(passage_raw_text)
    question_sections = _extract_passage_sections(extract_raw_text(questions_path), "questions")
    answer_sections = _extract_passage_sections(extract_raw_text(answer_key_path), "answers")

    missing_questions = sorted(set(passage_sections) - set(question_sections))
    if missing_questions:
        raise ValueError(f"Questions PDF is missing passage sections: {', '.join(map(str, missing_questions))}.")
    missing_answers = sorted(set(passage_sections) - set(answer_sections))
    if missing_answers:
        raise ValueError(f"Answer-key PDF is missing passage sections: {', '.join(map(str, missing_answers))}.")

    parsed_questions = {}
    for passage_number in passage_sections:
        key_answers = {}
        answer_text = answer_sections.get(passage_number, "")
        question_starts = list(re.finditer(r"(?is)<b>\s*(?:Q\s*)?(\d+)\s*[.)]", answer_text))
        answer_matches = list(re.finditer(r"(?is)Answer\s*:\s*\(?([A-E])\)?", answer_text))
        for idx, answer_match in enumerate(answer_matches):
            qnum = None
            for q_match in question_starts:
                if q_match.start() < answer_match.start():
                    qnum = int(q_match.group(1))
            if qnum is not None:
                key_answers[qnum] = answer_match.group(1).upper()
        for match in re.finditer(r"(?im)^\s*(?:Q\s*)?(\d+)\s*[.)]\s*\(?([A-E])\)?\b", answer_text):
            key_answers[int(match.group(1))] = match.group(2).upper()

        parsed_questions[passage_number] = _parse_question_section(
            question_sections[passage_number], key_answers
        )
        if not parsed_questions[passage_number]:
            raise ValueError(f"No questions with at least two options were parsed for PASSAGE {passage_number}.")
        missing_question_answers = sorted(
            question["number"] for question in parsed_questions[passage_number]
            if question["number"] not in key_answers
        )
        if missing_question_answers:
            missing_labels = ", ".join(map(str, missing_question_answers))
            raise ValueError(f"Answer key is missing question(s) {missing_labels} for PASSAGE {passage_number}.")
        for question in parsed_questions[passage_number]:
            answer_label = key_answers[question["number"]]
            option_labels = {option["label"] for option in question["options"]}
            if answer_label not in option_labels:
                raise ValueError(
                    f"Answer key label ({answer_label}) for question {question['number']} "
                    f"does not match an option in PASSAGE {passage_number}."
                )

    total_questions = 0
    used_ids = {
        passage["custom_id"]
        for passage in get_all_passages(published_only=False)
        if passage["custom_id"]
    }
    for passage_number, passage_text in passage_sections.items():
        if len(passage_text) < 50:
            raise ValueError(f"PASSAGE {passage_number} appears empty or too short to import.")
        title = passage_titles.get(passage_number, f"Reading Comprehension {passage_number}")
        custom_id = _passage_custom_id(source_name, passage_number, used_ids)
        passage_id = create_passage(
            custom_id=custom_id,
            title=title,
            content=passage_text,
            topic=_passage_topic(title),
            difficulty="Medium",
            source=source_name,
        )
        for question in parsed_questions[passage_number]:
            create_question_with_options(
                passage_id=passage_id,
                question_text=question["question_text"],
                question_type=question["question_type"],
                options=question["options"],
            )
            total_questions += 1

    return len(passage_sections), total_questions