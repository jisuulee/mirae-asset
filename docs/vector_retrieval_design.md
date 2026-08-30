# Section-level Vector Retrieval design

## Scope and competition constraint

This phase is an independent section-level Vector baseline. It does not implement semantic Hybrid, reranking, an Agent, a generation LLM, or embeddings for the 1.4 million `retrieval_tables` rows.

The repository contains no authoritative competition rule that says whether the HyperCLOVA X requirement applies only to generation or to every ML component. Public 2026 AI Festival material describes the challenge as “with HyperCLOVA X”, but does not expose a precise embedding restriction. The official CLOVA Studio documentation treats Chat/HyperCLOVA X and Embedding tools as separate APIs. Embedding v2 uses `bge-m3`, returns 1,024-dimensional dense vectors, accepts up to 8,192 tokens, and recommends cosine distance. Therefore the code keeps a provider interface and records provider/model/version/dimension. The operational default is CLOVA Studio Embedding v2 only when an explicit `CLOVASTUDIO_API_KEY` is supplied; no third-party model is silently selected.

Sources:

- AI Festival official site: https://miraeassetfesta.com/
- CLOVA Studio Embedding v2 API: https://api.ncloud-docs.com/docs/clovastudio-embeddingv2
- CLOVA Studio embedding model/distance guide: https://guide.ncloud-docs.com/docs/ko/clovastudio-explorer03

## Embedding unit and chunking

The authoritative retrieval unit remains `document_sections`. The full corpus contains 32,446 narrative, 42,194 table-only, and 8,222 hierarchy-only sections. Narrative body length is P50 295 characters, P95 3,450, P99 19,022, and maximum 130,956.

- Narrative sections are embedded.
- PDF fallback pages classified as narrative are included and evaluated as page-level section gold.
- Hierarchy-only sections are excluded: their heading has no independent body and tends to duplicate child meaning.
- Table-only sections are excluded: a marker such as `[TABLE: ...]` is not sufficient semantic content. Table embedding remains a separate future track.
- A section up to 8,000 characters stays one embedding unit.
- Longer sections are split by blank-line paragraphs, targeting at most 6,000 characters. Only hard overlong paragraphs use a 200-character overlap.
- Title and section path are prepended to the body. The original `document_sections` row is unchanged.

The deterministic dry run produces 32,446 sections, 38,971 chunks, and 34,084 unique embedding texts (75,914,512 characters). Content-hash reuse avoids 4,887 initial duplicate calls, or 12.54% of chunk rows.

Every chunk stores section/receipt/file IDs, parent section, section order/path, source locator, chunk order, character offsets, source type, policy version, loader namespace, and SHA-256 content hash.

## Schema and idempotency

Migration `005_pgvector.sql` installs pgvector without altering `document_sections` and creates:

- `section_vector_chunks`: stable retrieval units and provenance.
- `section_embedding_vectors`: content-addressed vectors, unique by `(content_hash, embedding_model, embedding_version)`.
- `section_chunk_embeddings`: many-model mapping between chunks and cached vectors.

Dimension, input-token usage, and creation time are recorded. A rerun first resolves cached hashes and calls the provider only for misses. Different model versions coexist; a content change creates a new hash/vector and updates the chunk mapping.

## Exact search before ANN

The initial implementation uses exact cosine distance. The vector column is intentionally not declared as `vector(1024)` because the migration must remain provider-neutral. This also prevents creating a valid HNSW/IVFFlat operator-class index until a model and dimension are explicitly frozen.

After a real exact baseline is available, an HNSW expression/partial index may be evaluated for the fixed model/version/dimension. HNSW is preferred over IVFFlat for this corpus because it does not require a training phase and usually offers a better recall/latency operating curve at roughly 39k chunks. Any ANN report must compare Recall@10 against exact results and record `m`, `ef_construction`, and query `hnsw.ef_search`. It must not replace the exact baseline silently.

## Search and evaluation

`search_vector_sections()` mirrors Keyword filters: company, inclusive filing-date range, canonical report type, exact receipt number, and loader namespace. It embeds the query once, finds the best chunk per section, and returns a section-ranked list with score, selected chunk, receipt, title/path, source locator, file, and combined chunk/source provenance.

The 120-row benchmark is immutable. Seven `table` targets are explicitly excluded from Vector-only section metrics. Three `either` rows retain section evaluation and may be answered from their containing section. PDF rows remain separately grouped. Reports must retain both reviewed-only (36) and all-candidate views, and may flag suspected gold issues only as review candidates.

## Operational gate

A real baseline requires a CLOVA Studio API key or an explicit, rule-approved alternative provider/model. The current environment has neither. Deterministic test embeddings validate plumbing but are prohibited for benchmark reporting. Until credentials and the competition interpretation are confirmed, no fabricated Vector scores, costs, DB vector sizes, or FTS recovery claims are published.
