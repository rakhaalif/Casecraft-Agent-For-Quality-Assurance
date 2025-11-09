"""Render evaluation metrics as a Markdown table.

Usage:
    python render_eval_table.py --input results/eval_report.csv --output results/eval_table.md

If --output is omitted the table is printed to stdout.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import List, Tuple


HEADERS = (
    "No",
    "Skenario",
    "Akurasi Kontekstual (Cosine)",
    "Validitas Semantik (LLM Score)",
    "Format BDD Valid (%)",
    "Waktu Generasi (detik/test case)",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render evaluation metrics as Markdown table")
    parser.add_argument("--input", type=Path, required=True, help="CSV file produced by evaluation_template.py")
    parser.add_argument("--output", type=Path, help="Optional destination markdown file")
    return parser.parse_args()


def load_rows(csv_path: Path) -> List[dict]:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def _fmt_float(value: str, digits: int = 2) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "N/A"
    return f"{num:.{digits}f}"


def build_table(rows: List[dict]) -> Tuple[str, List[List[str]]]:
    lines: List[List[str]] = []

    cosine_sum = 0.0
    cosine_count = 0
    semantic_sum = 0.0
    semantic_count = 0
    bdd_sum = 0.0
    bdd_count = 0
    duration_sum = 0.0
    duration_count = 0

    for idx, row in enumerate(rows, start=1):
        cosine = float(row["cosine_similarity_mean"]) if row.get("cosine_similarity_mean") else float("nan")
        semantic = float(row["semantic_score_avg"]) if row.get("semantic_score_avg") else float("nan")
        bdd = float(row["bdd_compliance"]) if row.get("bdd_compliance") else float("nan")
        duration = float(row["duration_seconds"]) if row.get("duration_seconds") else float("nan")

        lines.append(
            [
                str(idx),
                row.get("scenario", "Unknown"),
                _fmt_float(row.get("cosine_similarity_mean", "")),
                _fmt_float(row.get("semantic_score_avg", "")),
                f"{bdd * 100:.0f}%" if bdd == bdd else "N/A",
                _fmt_float(row.get("duration_seconds", ""), digits=1),
            ]
        )

        if cosine == cosine:
            cosine_sum += cosine
            cosine_count += 1
        if semantic == semantic:
            semantic_sum += semantic
            semantic_count += 1
        if bdd == bdd:
            bdd_sum += bdd
            bdd_count += 1
        if duration == duration:
            duration_sum += duration
            duration_count += 1

    avg_row = ["Rata-rata", "—"]
    avg_row.append(
        _fmt_float(str(cosine_sum / cosine_count)) if cosine_count else "N/A"
    )
    avg_row.append(
        _fmt_float(str(semantic_sum / semantic_count)) if semantic_count else "N/A"
    )
    avg_row.append(
        f"{(bdd_sum / bdd_count) * 100:.0f}%" if bdd_count else "N/A"
    )
    avg_row.append(
        _fmt_float(str(duration_sum / duration_count), digits=1) if duration_count else "N/A"
    )

    return avg_row, lines


def render_markdown(rows: List[dict]) -> str:
    avg_row, body_rows = build_table(rows)

    md_lines = ["| " + " | ".join(HEADERS) + " |", "|" + " --- |" * len(HEADERS)]
    for row in body_rows:
        md_lines.append("| " + " | ".join(row) + " |")
    md_lines.append("| " + " | ".join(avg_row) + " |")
    return "\n".join(md_lines)


def main() -> None:
    args = parse_args()
    rows = load_rows(args.input)
    table = render_markdown(rows)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(table, encoding="utf-8")
        print(f"Markdown table written to {args.output}")
    else:
        print(table)


if __name__ == "__main__":
    main()
