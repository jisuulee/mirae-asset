-- Filter-aware ANN projection for the fixed CLOVA Embedding v2 / bge-m3 profile.
-- Existing embeddings/chunks remain immutable; vectors are copied, never regenerated.
DROP INDEX IF EXISTS section_embedding_vectors_hnsw_cosine_idx;

CREATE TABLE IF NOT EXISTS section_vector_hnsw_projection (
    vector_chunk_id bigint PRIMARY KEY REFERENCES section_vector_chunks(vector_chunk_id) ON DELETE CASCADE,
    section_id bigint NOT NULL,
    embedding_id bigint NOT NULL,
    receipt_no text NOT NULL,
    company_name_normalized text NOT NULL,
    receipt_date date NOT NULL,
    report_type text NOT NULL,
    loader_namespace text NOT NULL,
    embedding_model text NOT NULL,
    embedding_version text NOT NULL,
    embedding vector(1024) NOT NULL,
    refreshed_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO section_vector_hnsw_projection(
    vector_chunk_id,section_id,embedding_id,receipt_no,company_name_normalized,
    receipt_date,report_type,loader_namespace,embedding_model,embedding_version,embedding)
SELECT c.vector_chunk_id,c.section_id,e.embedding_id,c.receipt_no,co.company_name_normalized,
       d.receipt_date,d.report_type,c.loader_namespace,e.embedding_model,e.embedding_version,
       e.embedding::vector(1024)
FROM section_vector_chunks c
JOIN section_chunk_embeddings ce USING(vector_chunk_id)
JOIN section_embedding_vectors e USING(embedding_id)
JOIN disclosures d ON d.receipt_no=c.receipt_no
JOIN companies co ON co.company_id=d.company_id
WHERE c.loader_namespace='periodic-full-v1'
  AND e.embedding_model='clova-studio/bge-m3'
  AND e.embedding_version='embedding-v2'
  AND e.embedding_dimension=1024
ON CONFLICT (vector_chunk_id) DO UPDATE SET
    section_id=excluded.section_id,embedding_id=excluded.embedding_id,
    receipt_no=excluded.receipt_no,company_name_normalized=excluded.company_name_normalized,
    receipt_date=excluded.receipt_date,report_type=excluded.report_type,
    loader_namespace=excluded.loader_namespace,embedding_model=excluded.embedding_model,
    embedding_version=excluded.embedding_version,embedding=excluded.embedding,refreshed_at=now()
WHERE (section_vector_hnsw_projection.section_id,section_vector_hnsw_projection.embedding_id,
       section_vector_hnsw_projection.receipt_no,section_vector_hnsw_projection.company_name_normalized,
       section_vector_hnsw_projection.receipt_date,section_vector_hnsw_projection.report_type,
       section_vector_hnsw_projection.loader_namespace,section_vector_hnsw_projection.embedding_model,
       section_vector_hnsw_projection.embedding_version,section_vector_hnsw_projection.embedding)
  IS DISTINCT FROM
      (excluded.section_id,excluded.embedding_id,excluded.receipt_no,excluded.company_name_normalized,
       excluded.receipt_date,excluded.report_type,excluded.loader_namespace,excluded.embedding_model,
       excluded.embedding_version,excluded.embedding);

CREATE INDEX IF NOT EXISTS section_vector_hnsw_cosine_idx
    ON section_vector_hnsw_projection
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS section_vector_hnsw_namespace_idx
    ON section_vector_hnsw_projection(loader_namespace);
CREATE INDEX IF NOT EXISTS section_vector_hnsw_company_idx
    ON section_vector_hnsw_projection(company_name_normalized);
CREATE INDEX IF NOT EXISTS section_vector_hnsw_receipt_idx
    ON section_vector_hnsw_projection(receipt_no);
CREATE INDEX IF NOT EXISTS section_vector_hnsw_date_report_idx
    ON section_vector_hnsw_projection(receipt_date,report_type);
