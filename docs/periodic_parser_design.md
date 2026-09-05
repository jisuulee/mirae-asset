# Periodic Retrieval Parser Design

## Scope

This parser is a Retrieval/RAG export path, not a clone of the Role 2 canonical parser. It reads the original periodic XML/HTML/PDF files without changing them and produces compact document summaries plus separate section and table JSONL files. Embeddings, pgvector, retrieval, and database loading are intentionally out of scope.

Runtime dependencies are listed in `requirements-rag.txt`.

## Pipeline

1. `build_periodic_export.py` joins authoritative `metadata_normalized.jsonl` rows to raw files by the 14-digit receipt number.
2. An exact `<receipt_no>.xml` is preferred. An exact PDF is the fallback; viewer HTML is retained in source inventory but not preferred over PDF.
3. `xml_parser.py` uses lxml recovery mode and emits ordered `heading`, `paragraph`, `table`, and `page_break` blocks. Table descendants are excluded from narrative text.
4. `table_extractor.py` preserves rows, cells, raw text, attributes, rowspan/colspan, searchable text, and table provenance.
5. `section_builder.py` uses explicit levels and conservative title patterns. Unknown levels stay null and receive `SECTION_HIERARCHY_UNCERTAIN`.
6. Section bodies contain narrative blocks in source order. Tables are represented only by `[TABLE: table_id]` markers.
7. SHA-256 is calculated from every source file. The parsed main source and unparsed attachments remain distinguishable.
8. `pdf_parser.py` emits page text and page provenance. It creates untitled page fallback sections and does not claim table extraction.

## Export contract

`sections.jsonl` contains the requested metadata, hierarchy, body, table references, source locator, SHA, provenance, status, and warnings. `tables.jsonl` contains loss-conscious table records. `documents.jsonl` contains only validation summaries; it deliberately does not duplicate blocks, sections, or tables.

The builder writes section/table rows per document rather than accumulating the corpus in memory. Peak memory is therefore bounded by one parsed receipt, although very large table-heavy receipts can still be expensive.

Example:

```powershell
python -m pip install -r requirements-rag.txt
python -m src.rag.build_periodic_export --pilot 20 --output artifacts/periodic_parser_pilot
python -m src.rag.validate_periodic_parser --export-dir artifacts/periodic_parser_pilot --output artifacts/periodic_parser_pilot/validation.json
```

A full run requires the explicit `--all` flag. It was not executed in this work.

## PostgreSQL mapping

The section export maps to `document_sections` after two load-time transformations:

- resolve `source_file` to `disclosure_files.file_id`;
- insert parents after sections and translate `parent_section_key` to `parent_section_id`.

`level`, `table_ids`, parse status, warnings, block boundaries, SHA, and provenance belong in `metadata` JSONB. Empty sections cannot pass the existing `CHECK (body_text <> '')`; a loader must either exclude them, merge them under an adjacent heading, or record them outside `document_sections`. No policy is silently applied by the parser.

## Known differences from canonical

- Role 2 filtering rules are unavailable. All recovered `TITLE`/`TABLE` elements are retained unless structurally excluded, so counts can differ.
- The Retrieval export adds table markers to section body text; canonical body length is therefore not directly comparable.
- PDF canonical samples have no sections. This parser deliberately creates untitled page sections for retrievability and records a warning.
- Attachments are hashed and inventoried but only the selected main document is parsed. Audit-report attachment parsing should be a separate, explicit follow-up.
- Recovery warnings are expected for DART markup and make `parse_status=partial`; they are not parser exceptions.

## Full-run gate

Parser correctness is sufficient for a bounded full run, but three policies should be decided first: empty-section handling for the database constraint, PDF page-section acceptance, and table storage. The 20-document stress-biased pilot produced a 797.58 MiB loss-conscious table export; full table preservation can approach canonical-scale storage. A section-only retrieval export is much smaller and does not require receiving the 26.6 GB canonical file.
