#!/usr/bin/env python3
"""Resolve Phase 4 DART correction lineage without rewriting Phase 2/3 data."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from disclosure_preprocess.correction_pipeline import resolve_correction_corpus  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, default=PROJECT_ROOT / "data" / "processed" / "metadata_normalized.jsonl")
    parser.add_argument("--parsed", type=Path, default=PROJECT_ROOT / "data" / "processed" / "parsed_disclosures.jsonl")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data" / "processed")
    parser.add_argument("--report-dir", type=Path, default=PROJECT_ROOT / "reports")
    parser.add_argument("--expected-document-count", type=int, default=4204)
    parser.add_argument("--expected-correction-count", type=int, default=1004)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--enable-opendart", action="store_true")
    parser.add_argument("--opendart-cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "opendart")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    api_key = os.environ.get("OPEN_DART_API_KEY") if args.enable_opendart else None
    try:
        summary = resolve_correction_corpus(
            metadata_path=args.metadata,
            parsed_path=args.parsed,
            output_dir=args.output_dir,
            report_dir=args.report_dir,
            expected_document_count=None if args.expected_document_count == 0 else args.expected_document_count,
            expected_correction_count=None if args.expected_correction_count == 0 else args.expected_correction_count,
            progress_every=args.progress_every,
            enable_opendart=args.enable_opendart,
            opendart_api_key=api_key,
            opendart_cache_dir=args.opendart_cache_dir,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        logging.getLogger(__name__).exception("Phase 4 failed: %s", exc)
        return 1
    print(json.dumps({"ready": True, "mode": summary["execution"]["mode"], **summary["resolution"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
