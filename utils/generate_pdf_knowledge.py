import os
import re
from pathlib import Path
from typing import Dict

from pdf_loader import load_pdfs_from_dir

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"
OUTPUT_SUFFIX = "_knowledge.txt"

# Mapping normalization rules if we want friendlier file names
NAME_NORMALIZATION: Dict[str, str] = {
    "prime": "prime",
    "hi": "hi",
    "portal": "portal",
}

def clean_text(text: str) -> str:
    # Normalize line endings
    text = text.replace('\r', '')
    # Remove leading/trailing spaces per line
    lines = [ln.strip() for ln in text.split('\n')]
    # Drop obviously empty noise lines at file start/end
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    # Collapse multiple blank lines to max 2
    cleaned_lines = []
    blank_run = 0
    for ln in lines:
        if not ln:
            blank_run += 1
            if blank_run <= 2:
                cleaned_lines.append(ln)
        else:
            blank_run = 0
            # Normalize multiple spaces inside line (but keep indentation gone already)
            ln = re.sub(r"\s+", " ", ln)
            cleaned_lines.append(ln)
    return "\n".join(cleaned_lines).strip() + "\n"

def pdf_filename_to_output_name(pdf_path: str) -> str:
    base = Path(pdf_path).name  # e.g. "Knowledge Prime.pdf"
    name = base.rsplit('.', 1)[0].lower()
    # remove 'knowledge' word
    name = name.replace('knowledge', '').strip()
    # replace spaces/dashes/underscores with single underscore
    name = re.sub(r"[\s\-]+", "_", name)
    if name in NAME_NORMALIZATION:
        name = NAME_NORMALIZATION[name]
    if not name:
        name = "pdf"
    return f"{name}{OUTPUT_SUFFIX}"  # e.g. prime_knowledge.txt

def main():
    if not KNOWLEDGE_DIR.exists():
        print(f"Knowledge directory not found: {KNOWLEDGE_DIR}")
        return
    pdfs = load_pdfs_from_dir(str(KNOWLEDGE_DIR))
    if not pdfs:
        print("No PDFs found to process.")
        return
    for path, raw_text in pdfs:
        out_name = pdf_filename_to_output_name(path)
        cleaned = clean_text(raw_text)
        out_path = KNOWLEDGE_DIR / out_name
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(cleaned)
        print(f"Wrote {out_path.relative_to(KNOWLEDGE_DIR.parent)} ({len(cleaned)} chars)")

if __name__ == "__main__":
    main()
