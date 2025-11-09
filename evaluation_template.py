"""Evaluation template for QA chatbot project.

This script defines a repeatable pipeline to compute:
- Contextual accuracy (cosine similarity between requirement and generated test cases).
- BDD format compliance (percent of steps following Given/When/Then/And).
- Generation latency (duration logged per case).
- Semantic validity (LLM scoring hook or manual rating placeholder).

Usage overview (adjust paths & services before running):
    python evaluation_template.py --input data/eval_cases.json --output results/eval_report.csv

Expected input JSON structure (list of cases):
[
  {
    "case_id": "TC-001",
    "product": "NetMonk Dashboard",
    "prompt": "Prompt lengkap yang dikirim ke bot...",
    "requirement": "Ringkasan requirement (opsional jika prompt sudah lengkap)",
    "generated_testcases": [
      {
        "id": "001",
        "title": "Verify ...",
        "body": "Given ...\nWhen ...\nThen ...",
        "generated_at": "2025-09-01T12:34:56Z"
      }
    ],
    "metrics": {
      "generation_start": "2025-09-01T12:34:48.000Z",
      "generation_end": "2025-09-01T12:34:56.500Z",
      "duration_seconds": 8.5
    }
  }
]

Populate missing fields if your telemetry differs. The script is defensive and will
compute durations when start/end timestamps are present.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Protocol, Sequence

import google.generativeai as genai
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Embedding + Semantic Judge interfaces (plug in real implementations)
# ---------------------------------------------------------------------------


class EmbeddingService(Protocol):
    """Interface for text embeddings used in cosine similarity."""

    def encode(self, text: str) -> Sequence[float]:
        """Return a numeric vector for the supplied text."""


class GeminiEmbeddingService:
    """Embedding provider backed by Gemini text-embedding models with fallback."""

    def __init__(self, model_name: str | None = None):
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY environment variable is not set")
        genai.configure(api_key=api_key)
        self.candidates: List[str] = []
        if model_name:
            self.candidates.append(model_name)
        for default in (
            "models/text-embedding-005",
            "text-embedding-005",
            "models/text-embedding-004",
            "text-embedding-004",
            "models/text-embedding-001",
            "text-embedding-001",
        ):
            if default not in self.candidates:
                self.candidates.append(default)
        self._active_model: Optional[str] = None

    def encode(self, text: str) -> Sequence[float]:
        if not text:
            return [0.0]

        tried: List[str] = []
        last_error: Optional[Exception] = None
        model_order = ([self._active_model] if self._active_model else []) + self.candidates
        for name in model_order:
            if not name or name in tried:
                continue
            tried.append(name)
            try:
                response = genai.embed_content(model=name, content=text)
                embedding = response.get("embedding") if isinstance(response, dict) else None
                if embedding is None:
                    raise RuntimeError("Gemini embedding API did not return an embedding vector")
                self._active_model = name
                return embedding
            except Exception as exc:  # pragma: no cover - depends on external API
                last_error = exc
                continue

        raise RuntimeError(f"All embedding model attempts failed: {last_error}")


class SemanticJudge(Protocol):
    """Interface for semantic quality scoring (LLM rubric)."""

    def score(self, requirement: str, test_case: str) -> float:
        """Return a score (e.g., 1.0-5.0) indicating semantic fidelity."""


class GeminiSemanticJudge:
    """Semantic scoring using Gemini 2.0 with a constrained rubric."""

    def __init__(self, model_name: str = "gemini-2.0-flash"):
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY environment variable is not set")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)

    def score(self, requirement: str, test_case: str) -> float:
        prompt = (
            "You are a QA reviewer. Rate from 1 (poor) to 5 (excellent) how well the "
            "test case aligns with the requirement. Respond with ONLY the number.\n\n"
            f"Requirement:\n{requirement}\n\n"
            f"Test Case:\n{test_case}\n"
        )
        try:
            result = self.model.generate_content(prompt)
        except Exception as exc:
            print(f"⚠️ Gemini semantic judge failed: {exc}")
            return math.nan

        text = (result.text or "").strip() if getattr(result, "text", None) else ""
        if not text and getattr(result, "candidates", None):
            text = "\n".join(
                c.content.parts[0].text for c in result.candidates if c.content.parts
            ).strip()

        match = re.search(r"([1-5](?:\.\d+)?)", text)
        if not match:
            return math.nan

        try:
            score = float(match.group(1))
        except ValueError:
            return math.nan

        return score if 1.0 <= score <= 5.0 else math.nan


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class TestCaseRecord:
    case_id: str
    product: str
    scenario: str
    requirement: str
    generated_testcases: List[str]
    generation_seconds: Optional[float]


@dataclass
class CaseMetrics:
    case_id: str
    product: str
    scenario: str
    cosine_similarity_mean: Optional[float]
    cosine_similarity_min: Optional[float]
    cosine_similarity_max: Optional[float]
    bdd_compliance: float
    duration_seconds: Optional[float]
    semantic_score_avg: Optional[float]


@dataclass
class AggregateMetrics:
    cosine_mean: Optional[float]
    bdd_mean: float
    duration_mean: Optional[float]
    semantic_mean: Optional[float]


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Compute cosine similarity between two numeric vectors."""

    if len(vec_a) != len(vec_b):
        raise ValueError("Vector lengths differ; ensure embedding dimensions match.")

    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def bdd_compliance_ratio(text: str) -> float:
    """Return the proportion of lines matching BDD step keywords."""

    if not text.strip():
        return 0.0

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0

    valid_keywords = ("given", "when", "then", "and")
    valid = sum(1 for line in lines if line.lower().startswith(valid_keywords))
    return valid / len(lines)


def safe_mean(values: Iterable[float]) -> Optional[float]:
    values_list = [v for v in values if v == v]  # drop NaN using v == v trick
    if not values_list:
        return None
    return statistics.mean(values_list)


# ---------------------------------------------------------------------------
# Core evaluation logic
# ---------------------------------------------------------------------------


def load_cases(path: Path) -> List[TestCaseRecord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records: List[TestCaseRecord] = []
    for raw in data:
        generated = raw.get("generated_testcases", [])
        prompt_text = raw.get("prompt") or ""
        requirement_text = raw.get("requirement") or prompt_text
        records.append(
            TestCaseRecord(
                case_id=str(raw.get("case_id", "UNSET")),
                product=str(raw.get("product", "Unknown")),
                scenario=str(raw.get("scenario") or raw.get("product", "Unknown")),
                requirement=str(requirement_text),
                generated_testcases=[
                    tc.get("body") or tc.get("text") or tc.get("raw_text", "")
                    for tc in generated
                ],
                generation_seconds=_resolve_duration(raw.get("metrics", {})),
            )
        )
    return records


def _resolve_duration(metrics: dict) -> Optional[float]:
    duration = metrics.get("duration_seconds")
    if isinstance(duration, (int, float)):
        return float(duration)
    # Optional: compute from start/end timestamps if provided
    start = metrics.get("generation_start")
    end = metrics.get("generation_end")
    if start and end:
        try:
            from datetime import datetime

            fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
            start_dt = datetime.strptime(start, fmt)
            end_dt = datetime.strptime(end, fmt)
            return (end_dt - start_dt).total_seconds()
        except Exception:
            pass
    return None


def evaluate_case(
    record: TestCaseRecord,
    embedding_service: EmbeddingService,
    semantic_judge: SemanticJudge,
) -> CaseMetrics:
    """Compute metrics for a single case."""

    cosine_scores: List[float] = []
    semantic_scores: List[float] = []

    try:
        requirement_vec = embedding_service.encode(record.requirement)
    except NotImplementedError:
        requirement_vec = []

    for tc_text in record.generated_testcases:
        if requirement_vec:
            tc_vec = embedding_service.encode(tc_text)
            cosine_scores.append(cosine_similarity(requirement_vec, tc_vec))
        else:
            cosine_scores.append(float("nan"))

        semantic_scores.append(semantic_judge.score(record.requirement, tc_text))

    bdd_scores = [bdd_compliance_ratio(tc) for tc in record.generated_testcases]
    bdd_mean = statistics.mean(bdd_scores) if bdd_scores else 0.0

    return CaseMetrics(
        case_id=record.case_id,
        product=record.product,
        scenario=record.scenario,
        cosine_similarity_mean=safe_mean(cosine_scores),
        cosine_similarity_min=min(cosine_scores) if cosine_scores else None,
        cosine_similarity_max=max(cosine_scores) if cosine_scores else None,
        bdd_compliance=bdd_mean,
        duration_seconds=record.generation_seconds,
        semantic_score_avg=safe_mean(semantic_scores),
    )


def summarize(metrics: List[CaseMetrics]) -> AggregateMetrics:
    return AggregateMetrics(
        cosine_mean=safe_mean(m.cosine_similarity_mean for m in metrics),
        bdd_mean=statistics.mean(m.bdd_compliance for m in metrics) if metrics else 0.0,
        duration_mean=safe_mean(m.duration_seconds for m in metrics),
        semantic_mean=safe_mean(m.semantic_score_avg for m in metrics),
    )


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


def export_csv(metrics: List[CaseMetrics], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "case_id",
        "product",
        "scenario",
        "cosine_similarity_mean",
        "cosine_similarity_min",
        "cosine_similarity_max",
        "bdd_compliance",
        "duration_seconds",
        "semantic_score_avg",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for metric in metrics:
            writer.writerow(metric.__dict__)


def print_summary(aggregate: AggregateMetrics) -> None:
    def _fmt(value: Optional[float]) -> str:
        if value is None or value != value:
            return "N/A"
        return f"{value:.3f}"

    print("\n=== Summary Metrics ===")
    print(f"Mean Cosine Similarity : {_fmt(aggregate.cosine_mean)}")
    print(f"Mean BDD Compliance    : {aggregate.bdd_mean:.3f}")
    print(f"Mean Duration (seconds): {_fmt(aggregate.duration_mean)}")
    print(f"Mean Semantic Score    : {_fmt(aggregate.semantic_mean)}")


def export_markdown_table(metrics: List[CaseMetrics], aggregate: AggregateMetrics, path: Path | None = None) -> str:
    """Render metrics as Markdown table (and optionally write to file)."""

    headers = [
        "No",
        "Skenario",
        "Akurasi Kontekstual (Cosine)",
        "Validitas Semantik (LLM Score)",
        "Format BDD Valid (%)",
        "Waktu Generasi (detik/test case)",
    ]

    lines = ["| " + " | ".join(headers) + " |", "|" + " --- |" * len(headers)]

    for idx, m in enumerate(metrics, start=1):
        cosine = f"{m.cosine_similarity_mean:.2f}" if m.cosine_similarity_mean == m.cosine_similarity_mean else "N/A"
        sem = f"{m.semantic_score_avg:.2f}" if m.semantic_score_avg == m.semantic_score_avg else "N/A"
        bdd_pct = f"{m.bdd_compliance * 100:.0f}%"
        duration = f"{m.duration_seconds:.1f}" if m.duration_seconds is not None else "N/A"
        lines.append(
            f"| {idx} | {m.scenario} | {cosine} | {sem} | {bdd_pct} | {duration} |"
        )

    # Append average row
    def _fmt_optional(value: Optional[float], decimals: int = 2) -> str:
        if value is None or value != value:
            return "N/A"
        return f"{value:.{decimals}f}"

    cosine_avg = _fmt_optional(aggregate.cosine_mean)
    sem_avg = _fmt_optional(aggregate.semantic_mean)
    bdd_avg = f"{aggregate.bdd_mean * 100:.0f}%"
    dur_avg = _fmt_optional(aggregate.duration_mean, decimals=1)
    lines.append(
        f"| Rata-rata | — | {cosine_avg} | {sem_avg} | {bdd_avg} | {dur_avg} |"
    )

    table = "\n".join(lines)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(table, encoding="utf-8")
    return table


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate QA chatbot outputs.")
    parser.add_argument("--input", type=Path, required=True, help="JSON file with evaluation cases")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/eval_report.csv"),
        help="Where to store per-case metrics CSV",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        help="Optional path to write Markdown table summarizing metrics",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    # Load environment variables from .env if present
    load_dotenv()
    records = load_cases(args.input)

    embedding_service: EmbeddingService = GeminiEmbeddingService()
    semantic_judge: SemanticJudge = GeminiSemanticJudge()

    metrics: List[CaseMetrics] = []
    for record in records:
        metrics.append(evaluate_case(record, embedding_service, semantic_judge))

    aggregate = summarize(metrics)
    export_csv(metrics, args.output)
    print_summary(aggregate)

    if args.markdown:
        md = export_markdown_table(metrics, aggregate, args.markdown)
        print("\nMarkdown table saved to", args.markdown)
    else:
        md = export_markdown_table(metrics, aggregate)
        print("\nCopy-friendly Markdown table:\n")
        print(md)

    print("\nDone. Fill semantic_judge / embedding_service and rerun for real metrics.")


if __name__ == "__main__":  # pragma: no cover
    main()
