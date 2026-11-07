"""Evaluation harness: measure extraction quality on a labeled dataset.

    python -m tc.evaluation --extractor rules
    python -m tc.evaluation --extractor llm                      # model from .env
    python -m tc.evaluation --extractor llm --model qwen2.5:7b --base-url http://localhost:11434/v1

Writes evals/results/<name>.json and prints a markdown table for the report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from tc.config import settings
from tc.extraction.extractor import Extractor, LLMExtractor, RuleBasedExtractor, run_extraction
from tc.extraction.schema import Candidate, ChatLine, ExtractionInput
from tc.extraction.verify import normalize
from tc.llm import OpenAICompatibleModel


@dataclass
class Expected:
    owner: str
    kind: str
    deadline: str | None  # local date, YYYY-MM-DD
    task_keywords: list[str]


@dataclass
class Example:
    id: str
    inp: ExtractionInput
    expected: list[Expected]
    aliases: dict[str, str] = field(default_factory=dict)


@dataclass
class Metrics:
    examples: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    kind_correct: int = 0
    deadline_correct: int = 0
    negatives: int = 0
    negatives_with_false_alarm: int = 0
    errors: int = 0
    total_latency_ms: int = 0
    rejections: dict[str, int] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "precision": round(self.precision, 3),
            "recall": round(self.recall, 3),
            "f1": round(self.f1, 3),
            "owner_and_task_matched": self.tp,
            "kind_accuracy": round(self.kind_correct / self.tp, 3) if self.tp else 0.0,
            "deadline_accuracy": round(self.deadline_correct / self.tp, 3) if self.tp else 0.0,
            "false_alarm_rate_on_negatives": round(
                self.negatives_with_false_alarm / self.negatives, 3) if self.negatives else 0.0,
            "avg_latency_ms": int(self.total_latency_ms / self.examples) if self.examples else 0,
            "errors": self.errors,
            "verifier_rejections": self.rejections,
        }


def load_dataset(path: Path) -> list[Example]:
    examples = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        inp = ExtractionInput(
            speaker=row["speaker"],
            text=row["text"],
            sent_at=datetime.fromisoformat(row["sent_at"]),
            members=row["members"],
            context=[ChatLine(**c) for c in row.get("context", [])],
        )
        examples.append(
            Example(row["id"], inp, [Expected(**e) for e in row["expected"]],
                    row.get("aliases", {}))
        )
    return examples


def matches(pred: Candidate, exp: Expected) -> bool:
    """Same owner, and the task mentions at least one expected keyword."""
    task = normalize(pred.task)
    return pred.owner == exp.owner and any(normalize(k) in task for k in exp.task_keywords)


def score_example(preds: list[Candidate], expected: list[Expected], m: Metrics) -> list[str]:
    problems: list[str] = []
    unmatched = list(preds)
    for exp in expected:
        hit = next((p for p in unmatched if matches(p, exp)), None)
        if hit is None:
            m.fn += 1
            problems.append(f"missed {exp.owner}: {exp.task_keywords[0]}")
            continue
        unmatched.remove(hit)
        m.tp += 1
        if hit.kind == exp.kind:
            m.kind_correct += 1
        else:
            problems.append(f"kind {hit.kind} != {exp.kind}")
        local = hit.deadline_at.astimezone(settings.tz) if hit.deadline_at else None
        got = local.date().isoformat() if local else None
        if got == exp.deadline:
            m.deadline_correct += 1
        else:
            problems.append(f"deadline {got} != {exp.deadline}")
    for p in unmatched:
        m.fp += 1
        problems.append(f"false positive: {p.owner}: {p.task}")
    if not expected:
        m.negatives += 1
        if preds:
            m.negatives_with_false_alarm += 1
    return problems


async def evaluate(extractor: Extractor, examples: list[Example], *, use_prefilter: bool = True
                   ) -> Metrics:
    m = Metrics()
    for ex in examples:
        m.examples += 1
        try:
            result = await run_extraction(
                extractor, ex.inp, settings.tz, aliases=ex.aliases, use_prefilter=use_prefilter
            )
        except Exception as exc:  # a crashed call counts as missing every expected item
            m.errors += 1
            m.fn += len(ex.expected)
            m.negatives += 0 if ex.expected else 1
            m.failures.append({"id": ex.id, "text": ex.inp.text, "problems": [repr(exc)]})
            continue
        m.total_latency_ms += result.latency_ms
        for r in result.rejected:
            m.rejections[r.reason] = m.rejections.get(r.reason, 0) + 1
        problems = score_example(result.candidates, ex.expected, m)
        if problems:
            m.failures.append({"id": ex.id, "text": ex.inp.text, "problems": problems})
    return m


def to_markdown(name: str, summary: dict[str, Any]) -> str:
    keys = ["precision", "recall", "f1", "kind_accuracy", "deadline_accuracy",
            "false_alarm_rate_on_negatives", "avg_latency_ms"]
    header = "| extractor | " + " | ".join(keys) + " |"
    sep = "|" + "---|" * (len(keys) + 1)
    row = f"| {name} | " + " | ".join(str(summary[k]) for k in keys) + " |"
    return "\n".join([header, sep, row])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--extractor", choices=["rules", "llm"], default="rules")
    parser.add_argument("--dataset", default="evals/dataset.jsonl")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--no-prefilter", action="store_true")
    args = parser.parse_args()

    extractor: Extractor
    if args.extractor == "rules":
        extractor = RuleBasedExtractor()
    else:
        extractor = LLMExtractor(OpenAICompatibleModel(
            base_url=args.base_url, api_key=args.api_key, model=args.model))

    metrics = asyncio.run(evaluate(extractor, load_dataset(Path(args.dataset)),
                                   use_prefilter=not args.no_prefilter))
    summary = metrics.summary()
    name = extractor.name + ("" if not args.no_prefilter else "+no-prefilter")
    out_dir = Path("evals/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = name.replace(":", "_").replace("/", "_")
    (out_dir / f"{safe}.json").write_text(json.dumps(
        {"extractor": name, "summary": summary, "metrics": asdict(metrics)}, indent=2,
        default=str), encoding="utf-8")
    print(to_markdown(name, summary))
    print(f"\n{len(metrics.failures)} examples with problems → evals/results/{safe}.json")


if __name__ == "__main__":
    main()
