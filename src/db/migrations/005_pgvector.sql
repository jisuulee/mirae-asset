-- Section-level vector retrieval. Original document_sections remain immutable.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS section_embedding_vectors (
    embedding_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    content_hash char(64) NOT NULL,
    embedding_model text NOT NULL,
    embedding_version text NOT NULL,
    embedding_dimension integer NOT NULL CHECK (embedding_dimension > 0),
    embedding vector NOT NULL,
    input_tokens integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (content_hash, embedding_model, embedding_version)
);

CREATE TABLE IF NOT EXISTS section_vector_chunks (
    vector_chunk_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id bigint NOT NULL REFERENCES document_sections(section_id) ON DELETE CASCADE,
    receipt_no text NOT NULL REFERENCES disclosures(receipt_no) ON DELETE CASCADE,
    file_id bigint NOT NULL REFERENCES disclosure_files(file_id) ON DELETE CASCADE,
    parent_section_id bigint REFERENCES document_sections(section_id) ON DELETE SET NULL,
    section_order integer NOT NULL,
    section_path text,
    source_locator text NOT NULL,
    chunk_order integer NOT NULL CHECK (chunk_order >= 0),
    chunk_text text NOT NULL,
    content_hash char(64) NOT NULL,
    chunk_policy text NOT NULL,
    loader_namespace text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (section_id, chunk_order, chunk_policy, loader_namespace)
);

CREATE INDEX IF NOT EXISTS section_vector_chunks_namespace_idx
    ON section_vector_chunks (loader_namespace);
CREATE INDEX IF NOT EXISTS section_vector_chunks_section_idx
    ON section_vector_chunks (section_id);
CREATE TABLE IF NOT EXISTS section_chunk_embeddings (
    vector_chunk_id bigint NOT NULL REFERENCES section_vector_chunks(vector_chunk_id) ON DELETE CASCADE,
    embedding_id bigint NOT NULL REFERENCES section_embedding_vectors(embedding_id) ON DELETE RESTRICT,
    embedding_model text NOT NULL,
    embedding_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (vector_chunk_id, embedding_model, embedding_version),
    UNIQUE (vector_chunk_id, embedding_id)
);

CREATE INDEX IF NOT EXISTS section_chunk_embeddings_embedding_idx
    ON section_chunk_embeddings (embedding_id);

-- The provider/model dimension is deliberately not baked into this migration.
-- Exact cosine search works with the untyped vector column. A model-specific
-- HNSW expression index is added only after one model/version/dimension is fixed.
