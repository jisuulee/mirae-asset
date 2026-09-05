#!/usr/bin/env python3
"""Refresh small Phase 4 reports from published sidecars without rescanning content."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from disclosure_preprocess.correction_lineage import MetadataIndex  # noqa: E402
from disclosure_preprocess.correction_lineage import validate_lineage  # noqa: E402
from disclosure_preprocess.correction_pipeline import (  # noqa: E402
    _exception_analysis,
    _json_bytes,
    _load_metadata,
    _report_markdown,
    _resolution_summary,
)


def _rows(path: Path) -> list[dict[str, object]]:
    with path.open("rb") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def main() -> int:
    metadata_rows, _ = _load_metadata(PROJECT_ROOT / "data" / "processed" / "metadata_normalized.jsonl")
    index = MetadataIndex.build(metadata_rows)
    lineage = _rows(PROJECT_ROOT / "data" / "processed" / "correction_lineage.jsonl")
    chains = _rows(PROJECT_ROOT / "data" / "processed" / "correction_chains.jsonl")
    summary_path = PROJECT_ROOT / "reports" / "correction_summary.json"
    report_path = PROJECT_ROOT / "reports" / "correction_report.md"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    validation = validate_lineage(lineage, chains, index)
    if not validation["passed"]:
        raise RuntimeError(f"Published lineage failed validation: {validation['errors'][:5]}")
    summary["resolution"] = _resolution_summary(lineage, chains, validation)
    summary["exception_analysis"] = _exception_analysis(lineage, index)
    stage_dir = Path(tempfile.mkdtemp(prefix=".phase4-report-", dir=PROJECT_ROOT / "reports"))
    try:
        staged_summary = stage_dir / summary_path.name
        staged_report = stage_dir / report_path.name
        staged_summary.write_bytes(_json_bytes(summary, pretty=True))
        staged_report.write_text(_report_markdown(summary), encoding="utf-8", newline="\n")
        os.replace(staged_summary, summary_path)
        os.replace(staged_report, report_path)
    finally:
        stage_dir.rmdir()
    print(json.dumps({"refreshed": True, "lineage_count": len(lineage), "ambiguous_reason_counts": summary["exception_analysis"]["ambiguous_reason_counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
