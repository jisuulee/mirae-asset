#!/usr/bin/env python3
"""Assemble the Phase 5 document index and Role 2 handoff package."""

from __future__ import annotations

import argparse
import json
import logging
import platform
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from disclosure_preprocess.final_pipeline import (  # noqa: E402
    LOCKED_INPUT_HASHES,
    assemble_final_dataset,
)


def _discover_data_root(project_root: Path) -> Path:
    summary_path = project_root / "reports" / "metadata_summary.json"
    if summary_path.is_file():
        try:
            source_path = json.loads(summary_path.read_text(encoding="utf-8"))["source"][
                "path"
            ]
            candidate = Path(source_path)
            if candidate.exists():
                return candidate
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    archives = sorted(project_root.glob("*.zip"))
    if len(archives) == 1:
        return archives[0]
    raise ValueError("--data-root is required when exactly one corpus ZIP cannot be discovered")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build additive Phase 5 final artifacts without rewriting Phase 2~4 inputs."
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--parsed", type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--lineage", type=Path)
    parser.add_argument("--chains", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--handoff-dir", type=Path)
    parser.add_argument("--data-contract", type=Path)
    parser.add_argument("--sample-count", type=int, default=90)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument(
        "--materialize-merged",
        action="store_true",
        help="Opt in to one large atomically published merged_disclosures.jsonl.",
    )
    for name, digest in LOCKED_INPUT_HASHES.items():
        parser.add_argument(
            f"--expected-{name.replace('_', '-')}-sha256",
            default=digest,
            metavar="HEX",
        )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if platform.python_version() != "3.13.14":
        raise RuntimeError(
            f"Phase 5 is locked to Python 3.13.14; observed {platform.python_version()}"
        )
    project_root = args.project_root.resolve()
    data_root = (args.data_root or _discover_data_root(project_root)).resolve()
    expected_hashes = {
        name: getattr(args, f"expected_{name}_sha256")
        for name in LOCKED_INPUT_HASHES
    }
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    summary = assemble_final_dataset(
        project_root=project_root,
        data_root=data_root,
        parsed_path=args.parsed or project_root / "data" / "processed" / "parsed_disclosures.jsonl",
        metadata_path=args.metadata or project_root / "data" / "processed" / "metadata_normalized.jsonl",
        lineage_path=args.lineage or project_root / "data" / "processed" / "correction_lineage.jsonl",
        chains_path=args.chains or project_root / "data" / "processed" / "correction_chains.jsonl",
        output_dir=args.output_dir or project_root / "data" / "final",
        report_dir=args.report_dir or project_root / "reports",
        handoff_dir=args.handoff_dir or project_root / "handoff" / "role2",
        data_contract_path=args.data_contract or project_root / "docs" / "data_contract.md",
        expected_hashes=expected_hashes,
        sample_count=args.sample_count,
        progress_every=args.progress_every,
        materialize_merged=args.materialize_merged,
    )
    sys.stdout.buffer.write(
        (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
