
from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path
from typing import Dict, List, Optional

import google.generativeai as genai
from dotenv import load_dotenv

PROMPT_TEMPLATE = (
    "You are a QA evaluator. Rate from 1 (irrelevant) to 5 (perfectly aligned) "
    "how well the test case satisfies the requirement. Respond with ONLY the number.\n\n"
    "Requirement (PRD):\n{prd}\n\n"
    "Test case:\n{testcase}\n"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gemini-based semantic evaluator")
    parser.add_argument("--input", type=Path, required=True, help="JSON file with evaluation cases")
    parser.add_argument("--limit", type=int, default=5, help="Number of test cases sampled per scenario")
    parser.add_argument("--csv", type=Path, help="Optional CSV file for per-scenario scores")
    return parser.parse_args()


def load_cases(path: Path) -> List[Dict[str, object]]:
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def ensure_gemini_model(model_name: str = "gemini-2.0-flash") -> genai.GenerativeModel:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY environment variable is not set.")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(model_name)


def score_pair(model: genai.GenerativeModel, requirement: str, testcase: str) -> Optional[float]:
    prompt = PROMPT_TEMPLATE.format(prd=requirement, testcase=testcase)
    try:
        result = model.generate_content(prompt)
    except Exception as exc:  # pragma: no cover - external service
        print(f"⚠️ Gemini failed to score test case: {exc}")
        return None

    text = (result.text or "").strip() if getattr(result, "text", None) else ""
    if not text and getattr(result, "candidates", None):
        text = "\n".join(
            part.text
            for candidate in result.candidates
            for part in getattr(candidate.content, "parts", [])
            if getattr(part, "text", None)
        ).strip()

    try:
        value = float(text)
    except ValueError:
        return None

    if math.isnan(value) or not (1.0 <= value <= 5.0):
        return None
    return value


def evaluate_cases(cases: List[Dict[str, object]], limit: int) -> List[Dict[str, object]]:
    model = ensure_gemini_model()

    rows: List[Dict[str, object]] = []
    for entry in cases:
        requirement = str(entry.get("requirement") or entry.get("prompt") or "")
        scenario = str(entry.get("scenario") or entry.get("case_id") or "Unknown Scenario")
        testcases = entry.get("generated_testcases") or []

        sampled = testcases[:limit]
        if not sampled:
            rows.append({"scenario": scenario, "scores": [], "average": None})
            continue

        per_scores: List[float] = []
        for tc in sampled:
            text = str(tc.get("body") or tc.get("text") or tc.get("raw_text") or "")
            if not text.strip():
                continue
            score = score_pair(model, requirement, text)
            if score is not None:
                per_scores.append(score)

        average = sum(per_scores) / len(per_scores) if per_scores else None
        rows.append({"scenario": scenario, "scores": per_scores, "average": average})

    return rows


def render_markdown(rows: List[Dict[str, object]]) -> str:
    header = "| No | Skenario | Skor LLM (rataan) | Sampel Dinilai |"
    underline = "| --- | --- | --- | --- |"
    lines = [header, underline]

    for idx, row in enumerate(rows, start=1):
        avg = f"{row['average']:.2f}" if row["average"] is not None else "N/A"
        count = len(row["scores"])
        lines.append(f"| {idx} | {row['scenario']} | {avg} | {count} |")

    return "\n".join(lines)


def maybe_write_csv(path: Optional[Path], rows: List[Dict[str, object]]) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["scenario", "average_score", "sample_count", "scores"])
        for row in rows:
            writer.writerow(
                [
                    row["scenario"],
                    f"{row['average']:.2f}" if row["average"] is not None else "",
                    len(row["scores"]),
                    ";".join(f"{score:.2f}" for score in row["scores"]),
                ]
            )


def main() -> None:
    load_dotenv()
    args = parse_args()
    cases = load_cases(args.input)
    rows = evaluate_cases(cases, limit=args.limit)
    table = render_markdown(rows)
    print(table)
    maybe_write_csv(args.csv, rows)


if __name__ == "__main__":
    main()
