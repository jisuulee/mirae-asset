# Periodic Parser Pilot

## Pilot composition

The bounded pilot contains 20 receipts selected from PDF fallback, table-count extremes, section-count extremes, narrative-heavy records, and receipts with one, two, or three source files. It includes 17 XML-main receipts and all three PDF/HTML fallback receipts. Source-file counts are: 5 receipts with one file, 5 with two, and 10 with three.

No part of the 1,054-document corpus was run beyond these 20 receipts.

## Quality metrics

| Metric | Result |
|---|---:|
| Parsed documents | 20 / 20 |
| Parse success/partial acceptance | 100% |
| Parser exceptions | 0 |
| Documents with sections | 100% |
| Sections | 3,586 |
| Tables | 41,660 |
| Empty section ratio | 5.44% |
| Parent/path success | 100% |
| Section provenance missing | 0% |
| Table provenance missing | 0% |
| Orphan table ratio | 0% |
| Main-source SHA present | 100% |

Sections per document: min 1, median 152.5, mean 179.3, max 1,085. Tables per document: min 0, median 1,903.5, mean 2,083, max 4,872. The pilot is deliberately stress-biased, so these means must not be extrapolated as corpus averages.

Every XML document reported markup recovery, uncertain hierarchy, and preserved table spans. These warnings are expected quality flags. The three PDFs reported fallback/page-section/no-table-extraction warnings.

## Size and memory

Streaming output sizes were:

- document summaries: 0.64 MiB;
- sections: 14.83 MiB;
- tables: 797.58 MiB.

The builder holds one receipt at a time and writes JSONL incrementally. It no longer duplicates full blocks/tables into `documents.jsonl`. The dominant cost is deliberate raw table/cell preservation. Using the known canonical corpus totals (about 84,256 sections and 1,398,153 tables), section-only export is likely sub-gigabyte, while loss-conscious full table export can again reach tens of gigabytes. Compression, partitioning, or storing only searchable table text in PostgreSQL should be decided before the 1,054-document run.

## Full-run readiness

The parser can execute all 1,054 receipts using explicit `--all`, and source coverage is already known to be complete. Do not run it yet. Resolve these gates first:

1. decide whether empty sections are dropped or merged before the `body_text <> ''` database check;
2. accept or revise untitled PDF page sections;
3. choose full loss-conscious tables versus a smaller Retrieval table projection;
4. decide whether audit-report XML attachments are a separate retrieval corpus;
5. add the database loader that resolves `file_id` and parent keys.

The 26.6 GB canonical file does not need to be requested to build section retrieval data. It would only avoid recreating complex canonical table/parser behavior that this minimal parser intentionally does not reproduce.
