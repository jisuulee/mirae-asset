# Question Router V1

Question Router V1 is a deterministic layer above the frozen Retrieval V1. It does not change FTS, Vector, Title/Path, RRF, reranker, chunking, embedding, SQL, or correction-chain semantics.

## Routes

- `sql`: authoritative structured values such as contract amount, counterparty, sales ratio, contract period, filing date, and exact receipt lookup.
- `rag`: narrative evidence such as business descriptions, R&D, risk factors, strategy, technology, and company explanations. It calls the frozen `search_default_sections()` pipeline.
- `correction`: filing history, original filing, latest correction, and changed-filing questions. It follows explicit/high-confidence correction edges only.
- `composite`: a small contract mechanism for genuine multi-intent questions. `routes` contains the required atomic paths.

## Priority

1. Detect correction intent.
2. Detect structured field intent and exact receipt lookup.
3. Detect narrative evidence intent.
4. Use RAG as the general-document fallback.

A receipt number supplies an identifier but does not force SQL when the question explicitly asks for correction history. Correction plus a structured field becomes `composite`, ordered as SQL then correction. Narrative content combined with an atomic intent is also preserved as composite rather than discarded.

## Metadata

The router extracts only explicit values:

- 14-digit receipt number
- four-digit year and its inclusive calendar date range
- explicit ISO-like dates
- annual/half/quarter report label
- company names supplied from the current database vocabulary
- structured field intent

It does not infer aliases or unstated companies. Receipt digits are masked before year extraction.

## Clarification

Empty questions and unresolved deictic structured questions such as `그 회사 계약 알려줘` request clarification. Correction-chain execution requires a receipt number; a company-only correction request retains the correction route but asks for the receipt. The router never changes an uncertain request into a different high-confidence route merely to produce results.

## Output and multi-intent contract

`RouteDecision` contains `route`, ordered `routes`, intent, company/year/report/receipt metadata, structured fields, filters, confidence, reason, and clarification state. `RoutedSearchResult` contains that decision plus route-tagged evidence and non-fatal adapter errors.

Common evidence fields are route/type, receipt, company/report/date, evidence text or value, provenance, source file/locator, rank/score, and retrieval sources. SQL retains `field_provenance`; correction evidence records explicit-chain observation order; RAG retains frozen retriever and Vector chunk provenance.

## Integration

```python
from src.retrieval import retrieve

result = retrieve(question, connection, provider=embedding_provider)
payload = result.to_dict()
```

- SQL delegates to `get_contract()` or `search_contracts()`.
- Correction delegates to `get_correction_history()`.
- RAG delegates to `search_default_sections()` without altering Retrieval V1 configuration.
- HCX answer generation is not part of Router V1.

## Evaluation policy

`eval/question_router_v1.jsonl` is independent of the 120-question retrieval benchmark. No question ID, category, difficulty, gold section, or retrieval result is a router feature. Future additions should represent general user intents and be reviewed separately from retrieval relevance gold.
