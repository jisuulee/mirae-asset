# Deterministic reranker baseline

The repository has no authoritative competition rule that explicitly authorizes an external cross-encoder, nor a fixed HyperCLOVA X chat model/endpoint and structured-output contract. Only CLOVA Studio Embedding v2 is operationally configured. This baseline therefore adds no pretrained model and uses no external corpus or labels.

The input is the immutable Hybrid Top-10 produced by FTS Top-20 + Exact Vector Top-20 and unweighted RRF k=60. No retrieval is performed by the reranker. It scores only query, section title/path, the retrieved vector chunk (up to 2,000 characters) or section fallback excerpt, FTS rank, Vector rank, and whether both retrievers found the section. Gold, question ID, category, difficulty, split, and review status are prohibited inputs.

The fixed score is `0.30/fts_rank + 0.30/vector_rank + 0.20*text_token_coverage + 0.15*title_path_token_coverage + 0.05*consensus`. Korean/ASCII tokens are normalized with the existing NFKC/casefold/whitespace policy. Ties use original Hybrid rank and then section ID. The formula is fixed before evaluation and is not trained or tuned on benchmark labels.

Candidate text is capped at 2,000 characters. A Vector candidate uses its retrieved `vector_chunk_id`; an FTS-only candidate uses the beginning of the parent section. Parent sections are not otherwise expanded.

Scores are cached by normalized-query hash, candidate content hash, model name, scoring version, and the FTS/Vector ranks consumed by the scoring formula in a local ignored SQLite artifact. The cache is an execution optimization only and cannot alter ordering across candidate-depth experiments.
