# Periodic Pilot SQL Retrieval Smoke Test

These are functional smoke checks, not a keyword-search accuracy or latency benchmark.

## Company and section title

Filtering by the DB-resolved company name `삼성전자` and exact source title `I. 회사의 개요`, while ordering main files first, returned:

| Receipt | Section order | File role |
|---|---:|---|
| 20260310002820 | 3 | main |
| 20250311001085 | 3 | main |
| 20240312000736 | 3 | main |

## Company and body lexeme

The `simple` FTS index returned receipt `20250317000894`, section 22, `3. 연결재무제표 주석` for a Korean lexeme present in that section. The returned row is from the main file and begins with table reference `20250317000894:t000244` followed by narrative text.

## Receipt section lookup

Receipt `20250319000952` returned ordered sections including:

- order 0: `사 업 보 고 서`, narrative;
- order 3: `I. 회사의 개요`, hierarchy-only;
- order 4: `1. 회사의 개요`, narrative;
- order 5: `2. 회사의 연혁`, narrative.

The missing source orders are the intentionally excluded empty leaf sections; source `section_order` values are not renumbered.

## Section provenance trace

Section ID 3148 traces to receipt `20250319000952`, its main XML file, locator `/DOCUMENT/BODY/COVER/COVER-TITLE`, source line 22, and SHA-256 `b0ad982693e4215545c1e8a3ba7e8b28f5de71daffb7f29d0300806fa73bd0c7`.

## Table trace and search

Table `20230627000387:t000000` traces through its section and file FKs to locator `/DOCUMENT/BODY/LIBRARY/CORRECTION/TABLE[1]` and lossless archive identifier `line:26770;table_id:20230627000387:t000000`.

Table FTS also returned projections across receipts `20240318000635` and `20240318000918`, with their source section titles and bounded searchable text.

## Observation before keyword baseline

The mechanics work: company/title filtering, body FTS, receipt ordering, source provenance, table FTS, and reverse tracing all return valid rows. The next benchmark should define Unicode normalization and Korean query-token construction explicitly. PostgreSQL's `simple` configuration performs lexeme matching but does not provide Korean morphological analysis; user-friendly partial-word behavior should be measured before changing indexes or adopting another search method.
