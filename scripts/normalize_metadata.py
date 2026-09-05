#!/usr/bin/env python3
"""CLI for Phase 3 DART metadata normalization and validation."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from disclosure_preprocess.metadata_pipeline import (  # noqa: E402
    normalize_metadata_corpus,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate Phase 2 disclosure metadata against manifest authority, "
            "write an additive sidecar, and build a representative handoff."
        )
    )
    parser.add_argument(
        "--data-root",
        required=True,
        type=Path,
        help="Dataset directory or ZIP archive containing manifest.jsonl",
    )
    parser.add_argument(
        "--parsed",
        type=Path,
        default=PROJECT_ROOT / "data" / "processed" / "parsed_disclosures.jsonl",
        help="Phase 2 parsed JSONL (default: data/processed/parsed_disclosures.jsonl)",
    )
    parser.add_argument(
        "--parse-warnings",
        type=Path,
        default=PROJECT_ROOT / "data" / "processed" / "parse_warnings.jsonl",
        help="Phase 2 warning JSONL used for mismatch reconciliation",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "processed",
        help="Canonical metadata sidecar directory",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Phase 3 report directory",
    )
    parser.add_argument(
        "--handoff-dir",
        type=Path,
        default=PROJECT_ROOT / "handoff",
        help="Role 2 handoff directory",
    )
    parser.add_argument(
        "--data-contract",
        type=Path,
        default=PROJECT_ROOT / "docs" / "data_contract.md",
        help="Canonical Data Contract copied byte-for-byte into the handoff",
    )
    parser.add_argument(
        "--expected-document-count",
        type=int,
        default=4204,
        help="Expected logical disclosures; use 0 to disable the assertion",
    )
    parser.add_argument(
        "--expected-mismatch-count",
        type=int,
        default=745,
        help="Expected Phase 2 mismatch occurrences; use -1 to disable",
    )
    parser.add_argument(
        "--sample-count",
        type=int,
        default=80,
        help="Representative handoff sample size (default: 80)",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="Log progress every N documents; 0 disables progress logs",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 50 <= args.sample_count <= 100:
        parser.error("--sample-count must be between 50 and 100")
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    expected_documents = (
        None if args.expected_document_count == 0 else args.expected_document_count
    )
    expected_mismatches = (
        None if args.expected_mismatch_count < 0 else args.expected_mismatch_count
    )
    try:
        summary = normalize_metadata_corpus(
            args.data_root,
            parsed_path=args.parsed,
            parse_warnings_path=args.parse_warnings,
            output_dir=args.output_dir,
            report_dir=args.report_dir,
            handoff_dir=args.handoff_dir,
            data_contract_path=args.data_contract,
            expected_document_count=expected_documents,
            expected_mismatch_count=expected_mismatches,
            sample_count=args.sample_count,
            progress_every=args.progress_every,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        logging.getLogger(__name__).exception("Phase 3 normalization failed: %s", exc)
        return 1

    logging.getLogger(__name__).info(
        "Phase 3 complete: %d metadata rows; joins=%d; mismatches=%d; ready=%s",
        summary["corpus"]["metadata_record_count"],
        summary["joins"]["matched_count"],
        summary["mismatches"]["phase2_occurrence_count"],
        summary["ready_for_downstream"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
