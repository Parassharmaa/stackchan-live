import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def summarize(paths: list[Path]) -> dict[str, Any]:
    durations: dict[str, list[float]] = defaultdict(list)
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                span = json.loads(line)
                durations[span["name"]].append(float(span["duration_ms"]))
    return {
        name: {
            "count": len(values),
            "p50_ms": statistics.median(values),
            "p95_ms": sorted(values)[max(0, math.ceil(len(values) * 0.95) - 1)],
            "mean_ms": statistics.fmean(values),
        }
        for name, values in durations.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize Stack-chan JSONL traces")
    parser.add_argument("traces", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize(args.traces)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
