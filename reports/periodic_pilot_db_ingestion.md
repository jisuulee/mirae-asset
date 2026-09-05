# Periodic Pilot PostgreSQL Ingestion

## Outcome

The 20-receipt periodic pilot was loaded successfully into PostgreSQL 15.19. The full 1,054-receipt corpus was not executed. Existing contracts and correction links were not mutated and remain at 1,106 and 461 rows.

## Section policy and mapping

`receipt_no` remains the disclosure primary identifier because Retrieval V1 has no numeric `disclosure_id`. The same value is also recorded as `metadata.disclosure_id`. Source SHA maps each parsed source to `disclosure_files.file_id`; `parent_section_key` is resolved in a second SQL phase after section upsert.

The 3,586 input sections were classified as follows:

| Class | Input | DB treatment |
|---|---:|---|
| Narrative | 2,288 | retained |
| Table-only | 1,103 | retained with table markers |
| Empty, hierarchy parent | 134 | retained as `hierarchy_only` |
| Empty leaf without tables | 61 | excluded from Retrieval projection |
| Empty without tables, total | 195 | 134 retained + 61 excluded |

The resulting `document_sections` count is 3,525. No source row is deleted; exclusion applies only to the derived Retrieval projection.

Migration `002_periodic_retrieval_tables.sql` replaces the broad non-empty check with a narrow exception: an empty body is legal only when `metadata.section_kind='hierarchy_only'`. This is safer than inventing searchable sentinel text and preserves structural parents without allowing arbitrary empty retrieval rows.

## PDF and attachment policy

The three PDF receipts produce 1,784 untitled page sections. Each row has `source_type=pdf`, `title_kind=untitled`, a `PDF page N` display title, page-number provenance, and PDF warning codes. They are therefore distinguishable from XML source sections.

Pilot source files now map 45/45:

- main: 20;
- audit report (`_00760`): 12;
- consolidated audit report (`_00761`): 10;
- alternative viewer HTML: 3.

Six previously absent PDF/HTML rows were inserted. Twenty-two generic `attachment` roles were refined to audit/consolidated-audit roles. Only main documents were parsed in this baseline: attachments contain zero sections and zero table projections but remain linked and available for a later attachment corpus.

## Table policy

PostgreSQL stores 41,660 bounded Retrieval projections, not the 797.58 MiB lossless cell payload. Each projection contains identifiers/FKs, caption, at most 12,000 characters of caption/header/source-ordered cell text, shape/span flags, locator/provenance, and an archive identifier such as `line:26770;table_id:...`.

Lossless rows/cells/span attributes remain in `artifacts/periodic_parser_pilot/tables.jsonl`. `archive_path` and `archive_identifier` connect the projection to that file. The PostgreSQL check enforces the 12,000-character limit.

## Transaction and idempotency

Section/file/table work runs in a single transaction. Parent links are resolved before table insertion; any error rolls back the transaction. An injected failure after section upsert was tested and left the prior section count unchanged.

The identical pilot command was run twice:

- first run: 6 files inserted, 22 roles refined, 3,525 sections and 41,660 table projections;
- second run: 0 files inserted, 0 role changes, 0 stale rows deleted, same final counts;
- duplicates: 0 sections and 0 tables.

## Commands

```powershell
python -m src.db.load_document_sections
python -m src.db.validate_document_sections --output reports/periodic_pilot_db_validation.json
```

The loader rejects exports over 50 receipts as a pilot safety boundary unless the code is deliberately changed.
