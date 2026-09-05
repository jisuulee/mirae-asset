# Periodic Parser Sample Validation

## Result

All four periodic gold samples parsed without exceptions. Main-source SHA-256 matched canonical source hashes in 4/4 documents, and block, section, and table provenance was present in every emitted record.

| Receipt | Blocks ours/gold | Block order | Sections ours/gold | Title order | Exact boundary | Body token overlap | Tables ours/gold |
|---|---:|---:|---:|---:|---:|---:|---:|
| 20250515001302 | 1,900 / 1,848 | 0.9840 | 70 / 59 | 0.9147 | 0.0339 | 0.7922 | 814 / 786 |
| 20240320002050 | 2,848 / 2,852 | 0.9993 | 8 / 8 | 1.0000 | 0.1250 | 0.1995 | 2,768 / 2,768 |
| 20250319000952 | 401 / 414 | 0.9840 | 6 / 6 | 1.0000 | 0.3333 | 0.3317 | 381 / 381 |
| 20240514001522 | 504 / 503 | 0.9990 | 252 / 0 | not comparable | not comparable | 1.0000 | 0 / 0 |

Strictly matched fields are source SHA and provenance presence. Block count/order, heading filtering, section boundaries, table filtering, and body text are heuristic comparison fields because Role 2 parser rules are not available.

## Difference analysis

- NAVER retains 11 additional headings and 28 additional tables. This reflects conservative preservation of recovered source elements rather than receipt-specific filtering.
- KT and Shiftup exactly match canonical section title sequences and table counts. Their block type order is also nearly identical.
- PDF block order/count is nearly identical, while this Retrieval parser creates 252 untitled page sections. Canonical intentionally creates no PDF sections. The new behavior is explicit and warning-tagged.
- Body character counts are not equality metrics. This parser inserts one table marker per table, whereas canonical body text largely omits table payloads. PDF text length is close: 293,005 versus 294,247 characters.

- Exact numeric boundaries are low despite high block-type order similarity because retaining one extra heading/table shifts every later block index. They are recorded for transparency, but title order and provenance are more stable without the Role 2 filtering rules.
Sample aggregate: 4 documents, 336 sections, 3,963 tables, 100% parse acceptance, 5.06% empty sections, 0% missing provenance, 0% orphan tables, and 0 exceptions.
