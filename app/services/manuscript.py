"""Manuscript parsing service - extracts chapters and paragraphs from text files."""
import re


def parse_manuscript(text: str) -> list[dict]:
    """Parse manuscript text into structured sections.

    Returns a list of dicts with keys:
        - section_type: 'chapter' or 'paragraph'
        - section_index: ordering index
        - text_content: the text
        - children: list of paragraph dicts (for chapters)
    """
    lines = text.strip().split("\n")
    sections = []
    current_chapter = None
    chapter_idx = 0
    para_idx = 0
    current_para_lines = []

    def flush_paragraph():
        nonlocal para_idx, current_para_lines
        para_text = " ".join(current_para_lines).strip()
        if not para_text:
            return None
        para = {
            "section_type": "paragraph",
            "section_index": para_idx,
            "text_content": para_text,
        }
        para_idx += 1
        current_para_lines = []
        return para

    for line in lines:
        stripped = line.strip()

        # Detect chapter headings
        chapter_match = re.match(
            r"^(chapter\s+\w+|part\s+\w+|section\s+\w+|prologue|epilogue|introduction|foreword|afterword)\b",
            stripped,
            re.IGNORECASE,
        )

        if chapter_match or (
            stripped.isupper() and len(stripped) > 2 and len(stripped.split()) <= 10
        ):
            # Flush current paragraph
            para = flush_paragraph()
            if para and current_chapter:
                current_chapter["children"].append(para)
            elif para:
                sections.append(para)

            # Start new chapter
            para_idx = 0
            current_chapter = {
                "section_type": "chapter",
                "section_index": chapter_idx,
                "text_content": stripped,
                "children": [],
            }
            sections.append(current_chapter)
            chapter_idx += 1

        elif stripped == "":
            # Blank line = paragraph break
            para = flush_paragraph()
            if para and current_chapter:
                current_chapter["children"].append(para)
            elif para:
                sections.append(para)

        else:
            current_para_lines.append(stripped)

    # Flush final paragraph
    para = flush_paragraph()
    if para and current_chapter:
        current_chapter["children"].append(para)
    elif para:
        sections.append(para)

    # If no chapters found, treat whole text as a single chapter
    if not any(s["section_type"] == "chapter" for s in sections):
        paragraphs = [s for s in sections if s["section_type"] == "paragraph"]
        if paragraphs:
            sections = [
                {
                    "section_type": "chapter",
                    "section_index": 0,
                    "text_content": "Full Text",
                    "children": paragraphs,
                }
            ]

    return sections


def extract_words(text: str) -> list[str]:
    """Extract normalized words from text for alignment."""
    # Remove punctuation but keep apostrophes within words
    cleaned = re.sub(r"[^\w\s']", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.lower().split()
