#!/usr/bin/env python3
"""CLI for the Phase 2 evidence-preserving unified DART parser."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from disclosure_preprocess.pipeline import parse_corpus  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Parse a DART corpus into one evidence-preserving JSONL row per "
            "manifest disclosure. DATA_ROOT may be a directory or ZIP."
        )
    )
    parser.add_argument(
        "--data-root",
        required=True,
        type=Path,
        help="Dataset directory or ZIP archive",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "processed",
        help="Processed JSONL directory (default: data/processed)",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Parser quality report directory (default: reports)",
    )
    parser.add_argument(
        "--expected-document-count",
        type=int,
        default=4204,
        help=(
            "Required manifest row count (default: 4204 for this festival "
            "corpus; use 0 to disable the corpus-specific assertion)"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Development-only manifest row limit; omit for a complete run",
    )
    parser.add_argument(
        "--doc-id",
        action="append",
        default=None,
        help=(
            "Parse only this manifest doc_id; repeat for multiple IDs. "
            "Mutually exclusive with --limit."
        ),
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="Log progress every N logical disclosures; 0 disables progress logs",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    expected = (
        None if args.expected_document_count == 0 else args.expected_document_count
    )
    try:
        summary = parse_corpus(
            args.data_root,
            output_dir=args.output_dir,
            report_dir=args.report_dir,
            expected_document_count=expected,
            document_limit=args.limit,
            document_ids=args.doc_id,
            progress_every=args.progress_every,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        logging.getLogger(__name__).exception("Corpus parsing failed: %s", exc)
        return 1

    logical = summary["logical_documents"]
    logging.getLogger(__name__).info(
        "Parsing complete: %d JSONL rows (%s)",
        logical["output_line_count"],
        logical["status_counts"],
    )
    logging.getLogger(__name__).info(
        "Processed output: %s; quality reports: %s",
        args.output_dir.resolve(),
        args.report_dir.resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
