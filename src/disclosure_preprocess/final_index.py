"""Pure Phase 5 document-index and sample-selection rules.

The functions in this module do not read or write the corpus.  Keeping the
projection rules separate makes the safety-critical correction flags easy to
unit test without touching the 26.6 GB content artifact.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any


PHASE5_SCHEMA_VERSION = "5.0.0"
COMPLETE_RESOLUTION_STATUSES = frozenset({"linked_exact", "linked_inferred"})
GUARDED_RESOLUTION_STATUSES = frozenset({"ambiguous", "unresolved"})


def warning_codes(record: Mapping[str, Any]) -> list[str]:
    """Return unique parser warning codes from a logical disclosure."""

    codes: set[str] = set()
    collections: list[object] = [record.get("warnings")]
    main = record.get("main_document")
    if isinstance(main, Mapping):
        collections.append(main.get("warnings"))
    for key in ("attachments", "alternative_representations"):
        values = record.get(key)
        if isinstance(values, Sequence) and not isinstance(
            values, (str, bytes, bytearray)
        ):
            for value in values:
                if isinstance(value, Mapping):
                    collections.append(value.get("warnings"))
    for values in collections:
        if not isinstance(values, Sequence) or isinstance(
            values, (str, bytes, bytearray)
        ):
            continue
        for warning in values:
            if isinstance(warning, Mapping) and warning.get("code"):
                codes.add(str(warning["code"]))
    return sorted(codes)


def _content(record: Mapping[str, Any]) -> Mapping[str, Any]:
    main = record.get("main_document")
    if not isinstance(main, Mapping):
        return {}
    content = main.get("content")
    return content if isinstance(content, Mapping) else {}


def _table_has_source_text(table: object) -> bool:
    if not isinstance(table, Mapping):
        return False
    raw_rows = table.get("raw_rows")
    if not isinstance(raw_rows, list):
        return False
    return any(
        isinstance(cell, Mapping)
        and isinstance(cell.get("raw_text"), str)
        and bool(str(cell["raw_text"]).strip())
        for row in raw_rows
        if isinstance(row, list)
        for cell in row
    )


def content_summary(
    record: Mapping[str, Any], *, record_size_bytes: int | None = None
) -> dict[str, Any]:
    """Build a structural, non-semantic summary of canonical main content."""

    content = _content(record)
    sections = content.get("sections")
    tables = content.get("tables")
    attachments = record.get("attachments")
    sources = record.get("source_files")
    section_count = len(sections) if isinstance(sections, list) else 0
    table_count = len(tables) if isinstance(tables, list) else 0
    attachment_count = len(attachments) if isinstance(attachments, list) else 0
    source_files = sources if isinstance(sources, list) else []
    sha_count = sum(
        1
        for source in source_files
        if isinstance(source, Mapping)
        and isinstance(source.get("sha256"), str)
        and len(str(source["sha256"])) == 64
    )
    codes = warning_codes(record)
    body = content.get("body_text")
    title = content.get("title")
    has_body = isinstance(body, str) and bool(body.strip())
    if not has_body and isinstance(tables, list):
        has_body = any(_table_has_source_text(table) for table in tables)
    summary = {
        "parse_status": record.get("parse_status"),
        "has_title": isinstance(title, str) and bool(title.strip()),
        "has_body": has_body,
        "has_sections": section_count > 0,
        "section_count": section_count,
        "has_tables": table_count > 0,
        "table_count": table_count,
        "attachment_count": attachment_count,
        "source_file_count": len(source_files),
        "source_sha256_count": sha_count,
        "has_complete_source_sha256": sha_count == len(source_files)
        and len(source_files) > 0,
        "has_pdf_fallback": "PDF_FALLBACK_USED" in codes,
        "has_table_span_warning": "TABLE_SPAN_NORMALIZATION_FAILED" in codes,
        "warning_codes": codes,
    }
    if record_size_bytes is not None:
        summary["record_size_bytes"] = int(record_size_bytes)
    return summary


def count_provenance(record: Mapping[str, Any]) -> tuple[Counter[str], Counter[str]]:
    """Count structured provenance items and missing locators.

    The traversal mirrors the Phase 2 evidence-bearing structures without
    interpreting text or table meaning.  A provenance object is considered
    present only when it names a source file and source role.
    """

    counts: Counter[str] = Counter()
    missing: Counter[str] = Counter()

    def observe(kind: str, value: object) -> None:
        counts[kind] += 1
        if not isinstance(value, Mapping) or not value.get("source_file") or not value.get(
            "file_role"
        ):
            missing[kind] += 1

    components: list[Mapping[str, Any]] = []
    main = record.get("main_document")
    if isinstance(main, Mapping):
        components.append(main)
    for key in ("attachments", "alternative_representations"):
        values = record.get(key)
        if isinstance(values, Sequence) and not isinstance(
            values, (str, bytes, bytearray)
        ):
            components.extend(value for value in values if isinstance(value, Mapping))

    for component in components:
        content = component.get("content")
        if not isinstance(content, Mapping):
            # Non-canonical PDF/HTML representations may deliberately carry
            # only source metadata with content_included=false.  No structured
            # content object exists in the serialized dataset to validate.
            continue
        observe("document", content.get("provenance"))
        title = content.get("title")
        if isinstance(title, str) and title.strip():
            observe("title", content.get("title_provenance"))
        sections = content.get("sections")
        if isinstance(sections, list):
            for section in sections:
                observe(
                    "section",
                    section.get("provenance") if isinstance(section, Mapping) else None,
                )
        blocks = content.get("block_sequence")
        if isinstance(blocks, list):
            for block in blocks:
                observe(
                    "block",
                    block.get("provenance") if isinstance(block, Mapping) else None,
                )
                if not isinstance(block, Mapping):
                    continue
                items = block.get("items")
                if isinstance(items, list):
                    for item in items:
                        observe(
                            "list_item",
                            item.get("provenance")
                            if isinstance(item, Mapping)
                            else None,
                        )
        tables = content.get("tables")
        if isinstance(tables, list):
            for table in tables:
                observe(
                    "table",
                    table.get("provenance") if isinstance(table, Mapping) else None,
                )
                if not isinstance(table, Mapping):
                    continue
                raw_rows = table.get("raw_rows")
                if not isinstance(raw_rows, list):
                    continue
                for row in raw_rows:
                    if not isinstance(row, list):
                        continue
                    for cell in row:
                        observe(
                            "table_cell",
                            cell.get("provenance")
                            if isinstance(cell, Mapping)
                            else None,
                        )
    return counts, missing


def build_chain_membership(
    chains: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Project Phase 4 chains to per-document safety fields."""

    result: dict[str, dict[str, Any]] = {}
    for chain in chains:
        chain_id = chain.get("chain_id")
        is_complete = chain.get("is_complete") is True
        latest = chain.get("latest_rcept_no")
        members = chain.get("members")
        if not isinstance(chain_id, str) or not isinstance(members, list):
            raise ValueError("Invalid Phase 4 chain structure")
        for member in members:
            if not isinstance(member, Mapping):
                raise ValueError(f"Invalid member in chain {chain_id}")
            doc_id = str(member.get("doc_id") or "")
            receipt = str(member.get("rcept_no") or "")
            if not doc_id or not receipt:
                raise ValueError(f"Missing member identifier in chain {chain_id}")
            if doc_id in result:
                raise ValueError(f"Document appears in multiple chains: {doc_id}")
            result[doc_id] = {
                "chain_id": chain_id,
                "root_rcept_no": chain.get("root_rcept_no"),
                "latest_rcept_no": latest,
                "sequence": member.get("sequence"),
                "is_complete_chain": is_complete,
                "is_latest_in_complete_chain": receipt == latest if is_complete else None,
                "is_superseded_in_complete_chain": receipt != latest if is_complete else None,
            }
    return result


def lineage_projection(
    metadata: Mapping[str, Any],
    lineage_record: Mapping[str, Any] | None,
    chain_membership: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Create safe lineage fields for one document.

    Boolean latest/superseded flags are deliberately derived from complete
    chain membership only.  Resolution confidence alone can never set them.
    """

    is_correction = metadata.get("is_correction") is True
    resolution = (
        lineage_record.get("resolution")
        if isinstance(lineage_record, Mapping)
        and isinstance(lineage_record.get("resolution"), Mapping)
        else {}
    )
    lineage = (
        lineage_record.get("lineage")
        if isinstance(lineage_record, Mapping)
        and isinstance(lineage_record.get("lineage"), Mapping)
        else {}
    )
    membership = chain_membership if isinstance(chain_membership, Mapping) else {}
    complete = membership.get("is_complete_chain")
    if complete is not True:
        latest_flag = None
        superseded_flag = None
    else:
        latest_flag = membership.get("is_latest_in_complete_chain")
        superseded_flag = membership.get("is_superseded_in_complete_chain")
        if not isinstance(latest_flag, bool) or not isinstance(superseded_flag, bool):
            raise ValueError("Complete chain membership lacks boolean safety flags")
    return {
        "is_correction": is_correction,
        "status": resolution.get("status") if is_correction else None,
        "method": resolution.get("method") if is_correction else None,
        "confidence": resolution.get("confidence") if is_correction else None,
        "parent_rcept_no": lineage.get("parent_rcept_no") if is_correction else None,
        "root_rcept_no": membership.get("root_rcept_no", lineage.get("root_rcept_no")),
        "latest_rcept_no": membership.get(
            "latest_rcept_no", lineage.get("latest_rcept_no")
        ),
        "chain_id": membership.get("chain_id", lineage.get("chain_id")),
        "sequence": membership.get("sequence", lineage.get("sequence")),
        "is_complete_chain": complete if isinstance(complete, bool) else None,
        "is_latest_in_complete_chain": latest_flag,
        "is_superseded_in_complete_chain": superseded_flag,
    }


def quality_projection(
    *, parse_status: object, metadata_status: object, lineage: Mapping[str, Any]
) -> dict[str, Any]:
    """Return deterministic caution flags with no semantic inference."""

    parse_value = str(parse_status) if parse_status is not None else "failed"
    metadata_value = str(metadata_status) if metadata_status is not None else "invalid"
    status = lineage.get("status")
    complete = lineage.get("is_complete_chain")
    correction = lineage.get("is_correction") is True
    lineage_guard = correction and (
        status in GUARDED_RESOLUTION_STATUSES or complete is not True
    )
    return {
        "parse_status": parse_value,
        "metadata_status": metadata_value,
        "requires_parse_caution": parse_value != "success",
        "requires_metadata_caution": metadata_value != "valid",
        "requires_lineage_guard": lineage_guard,
        "safe_for_automatic_lineage_dedup": (not correction) or complete is True,
    }


def build_document_index_row(
    metadata_row: Mapping[str, Any],
    parsed_record: Mapping[str, Any],
    lineage_record: Mapping[str, Any] | None,
    chain_membership: Mapping[str, Any] | None,
    *,
    record_size_bytes: int | None = None,
) -> dict[str, Any]:
    """Join Phase 2/3/4 facts into one lightweight Phase 5 row."""

    metadata = metadata_row.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("Phase 3 row has no metadata object")
    doc_id = str(metadata_row.get("doc_id") or "")
    receipt = str(metadata_row.get("rcept_no") or "")
    if doc_id != str(parsed_record.get("doc_id") or ""):
        raise ValueError(f"doc_id join mismatch: {doc_id}")
    if receipt != str(parsed_record.get("rcept_no") or ""):
        raise ValueError(f"rcept_no join mismatch: {doc_id}")
    lineage = lineage_projection(metadata, lineage_record, chain_membership)
    validation = metadata_row.get("metadata_validation")
    metadata_status = (
        validation.get("status") if isinstance(validation, Mapping) else "invalid"
    )
    parse_status = parsed_record.get("parse_status")
    return {
        "schema_version": PHASE5_SCHEMA_VERSION,
        "doc_id": doc_id,
        "rcept_no": receipt,
        "metadata": dict(metadata),
        "content_summary": content_summary(
            parsed_record, record_size_bytes=record_size_bytes
        ),
        "lineage": lineage,
        "quality": quality_projection(
            parse_status=parse_status,
            metadata_status=metadata_status,
            lineage=lineage,
        ),
        "references": {
            "content_dataset": "data/processed/parsed_disclosures.jsonl",
            "metadata_dataset": "data/processed/metadata_normalized.jsonl",
            "lineage_dataset": "data/processed/correction_lineage.jsonl"
            if metadata.get("is_correction") is True
            else None,
            "join_key": "doc_id",
        },
    }


def sample_traits(row: Mapping[str, Any]) -> set[str]:
    """Describe selection-relevant, source-grounded properties of an index row."""

    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    content = (
        row.get("content_summary")
        if isinstance(row.get("content_summary"), Mapping)
        else {}
    )
    lineage = row.get("lineage") if isinstance(row.get("lineage"), Mapping) else {}
    quality = row.get("quality") if isinstance(row.get("quality"), Mapping) else {}
    traits = {
        f"doc_group:{metadata.get('doc_group')}",
        f"parse_status:{content.get('parse_status')}",
        "correction" if metadata.get("is_correction") is True else "non_correction",
        "has_table" if content.get("has_tables") is True else "no_table",
        "has_sections" if content.get("has_sections") is True else "no_sections",
        "metadata_warning"
        if quality.get("requires_metadata_caution") is True
        else "metadata_valid",
    }
    if metadata.get("doc_group") == "periodic":
        traits.add(
            "periodic_multi_file"
            if int(content.get("attachment_count") or 0) > 0
            else "periodic_single_file"
        )
    if content.get("has_pdf_fallback") is True:
        traits.add("pdf_fallback")
    if content.get("has_table_span_warning") is True:
        traits.add("table_span_warning")
    if lineage.get("is_complete_chain") is True:
        if lineage.get("is_correction") is not True:
            traits.add("complete_chain_root")
        elif lineage.get("is_latest_in_complete_chain") is True:
            traits.add("complete_chain_latest")
        else:
            traits.add("complete_chain_intermediate")
    elif lineage.get("is_complete_chain") is False:
        traits.add("partial_chain")
    status = lineage.get("status")
    if status in {"linked_exact", "linked_inferred", "ambiguous", "unresolved"}:
        traits.add(f"lineage:{status}")
    return traits


PRODUCTION_SAMPLE_REQUIREMENTS = (
    "doc_group:periodic",
    "doc_group:major",
    "doc_group:exchange",
    "doc_group:holding",
    "parse_status:success",
    "parse_status:partial",
    "periodic_single_file",
    "periodic_multi_file",
    "correction",
    "non_correction",
    "has_table",
    "has_sections",
    "no_sections",
    "metadata_warning",
    "pdf_fallback",
    "complete_chain_root",
    "complete_chain_intermediate",
    "complete_chain_latest",
    "partial_chain",
    "lineage:ambiguous",
    "lineage:unresolved",
)


def select_representative_sample(
    descriptors: Sequence[Mapping[str, Any]],
    *,
    requested_count: int = 90,
    enforce_requirements: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Select a deterministic, compact, coverage-oriented sample."""

    if requested_count < 1 or requested_count > 100:
        raise ValueError("sample_count must be between 1 and 100")
    prepared: list[dict[str, Any]] = []
    for raw in descriptors:
        row = raw.get("index_row")
        if not isinstance(row, Mapping):
            raise ValueError("Sample descriptor lacks index_row")
        item = dict(raw)
        item["traits"] = set(raw.get("traits") or sample_traits(row))
        item["reasons"] = []
        prepared.append(item)
    if not prepared:
        raise ValueError("Cannot sample an empty corpus")

    # The high-table example is selected from the top 25 table counts, then
    # minimized by record size so the developer package remains manageable.
    by_tables = sorted(
        prepared,
        key=lambda item: (
            -int(item["index_row"]["content_summary"].get("table_count") or 0),
            int(item.get("line_size") or 0),
            str(item.get("doc_id")),
        ),
    )
    high_pool = by_tables[: min(25, len(by_tables))]
    high_table = min(
        high_pool,
        key=lambda item: (int(item.get("line_size") or 0), str(item.get("doc_id"))),
    )
    high_table["traits"].add("many_tables")

    selected: dict[str, dict[str, Any]] = {}

    def add(item: dict[str, Any], reason: str) -> None:
        doc_id = str(item["doc_id"])
        existing = selected.get(doc_id)
        if existing is None:
            selected[doc_id] = item
            existing = item
        if reason not in existing["reasons"]:
            existing["reasons"].append(reason)

    requirements = (*PRODUCTION_SAMPLE_REQUIREMENTS, "many_tables")
    missing: list[str] = []
    for requirement in requirements:
        candidates = [item for item in prepared if requirement in item["traits"]]
        if not candidates:
            missing.append(requirement)
            continue
        candidate = min(
            candidates,
            key=lambda item: (
                0 if str(item["doc_id"]) in selected else 1,
                int(item.get("line_size") or 0),
                str(item["doc_id"]),
            ),
        )
        add(candidate, f"coverage:{requirement}")
    if enforce_requirements and missing:
        raise ValueError(f"Sample coverage unavailable: {missing}")

    # Add one compact example per company where possible.
    by_company: dict[str, list[dict[str, Any]]] = {}
    for item in prepared:
        metadata = item["index_row"]["metadata"]
        by_company.setdefault(str(metadata.get("corp_code") or ""), []).append(item)
    for corp_code in sorted(by_company):
        if len(selected) >= requested_count:
            break
        candidate = min(
            by_company[corp_code],
            key=lambda item: (int(item.get("line_size") or 0), str(item["doc_id"])),
        )
        add(candidate, f"company_coverage:{corp_code}")

    # Fill remaining slots by maximizing new traits per MiB, with stable ties.
    covered = set().union(*(item["traits"] for item in selected.values())) if selected else set()
    remaining = [item for item in prepared if str(item["doc_id"]) not in selected]
    while len(selected) < min(requested_count, len(prepared)) and remaining:
        candidate = max(
            remaining,
            key=lambda item: (
                len(item["traits"] - covered),
                -math.log2(max(2, int(item.get("line_size") or 0))),
                str(item["doc_id"]),
            ),
        )
        add(candidate, "deterministic_diversity_fill")
        covered.update(candidate["traits"])
        remaining.remove(candidate)

    result = sorted(selected.values(), key=lambda item: str(item["doc_id"]))
    return result, missing


__all__ = [
    "PHASE5_SCHEMA_VERSION",
    "PRODUCTION_SAMPLE_REQUIREMENTS",
    "build_chain_membership",
    "build_document_index_row",
    "content_summary",
    "count_provenance",
    "lineage_projection",
    "quality_projection",
    "sample_traits",
    "select_representative_sample",
    "warning_codes",
]
