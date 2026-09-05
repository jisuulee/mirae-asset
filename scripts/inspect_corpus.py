#!/usr/bin/env python3
"""Command-line entry point for Phase 1 corpus inventory."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from disclosure_preprocess.config import (  # noqa: E402
    DEFAULT_DECODE_CHUNK_BYTES,
    DEFAULT_PREFIX_BYTES,
    DEFAULT_REPORT_DIR,
)
from disclosure_preprocess.inventory import analyze_corpus  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Profile a DART disclosure corpus without modifying raw files. "
            "DATA_ROOT may be an extracted directory or a ZIP archive."
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
        default=DEFAULT_REPORT_DIR,
        help=f"Report directory (default: {DEFAULT_REPORT_DIR})",
    )
    parser.add_argument(
        "--prefix-bytes",
        type=int,
        default=DEFAULT_PREFIX_BYTES,
        help="Maximum prefix bytes used for content detection",
    )
    parser.add_argument(
        "--decode-chunk-bytes",
        type=int,
        default=DEFAULT_DECODE_CHUNK_BYTES,
        help="Chunk size for strict streaming UTF-8 validation",
    )
    parser.add_argument(
        "--encoding-mode",
        choices=("all", "sample", "none"),
        default="all",
        help="UTF-8 validation scope; declarations are still read from prefixes",
    )
    parser.add_argument(
        "--encoding-sample-size",
        type=int,
        default=200,
        help="Maximum text files checked when --encoding-mode=sample",
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
    try:
        summary = analyze_corpus(
            args.data_root,
            args.output_dir,
            prefix_bytes=args.prefix_bytes,
            decode_chunk_bytes=args.decode_chunk_bytes,
            encoding_mode=args.encoding_mode,
            encoding_sample_size=args.encoding_sample_size,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        logging.getLogger(__name__).exception("Corpus inventory failed: %s", exc)
        return 1

    manifest = summary["manifest"]
    physical = summary["physical_files"]
    logging.getLogger(__name__).info(
        "Inventory complete: %s documents, %s raw files, %s format mismatches",
        manifest.get("document_count", 0),
        physical.get("raw_file_count", 0),
        physical.get("extension_format_mismatch_count", 0),
    )
    logging.getLogger(__name__).info("Reports written to %s", args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
