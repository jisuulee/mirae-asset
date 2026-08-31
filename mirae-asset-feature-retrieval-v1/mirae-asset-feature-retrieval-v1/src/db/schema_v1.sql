-- PostgreSQL 15+ Retrieval V1 schema.
-- V1 deliberately does not install pgvector or store embeddings.  Add the
-- vector column in a later migration after the embedding model is fixed.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS companies (
    company_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    corp_code       VARCHAR(8) UNIQUE,
    stock_code      VARCHAR(6),
    company_name    TEXT NOT NULL,
    company_name_normalized TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (corp_code IS NULL OR corp_code ~ '^[0-9]{8}$'),
    CHECK (stock_code IS NULL OR stock_code ~ '^[0-9]{6}$')
);
CREATE INDEX IF NOT EXISTS companies_name_trgm_idx ON companies USING gin (company_name_normalized gin_trgm_ops);
CREATE INDEX IF NOT EXISTS companies_stock_code_idx ON companies (stock_code) WHERE stock_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS disclosures (
    receipt_no      VARCHAR(14) PRIMARY KEY,
    company_id      BIGINT NOT NULL REFERENCES companies(company_id),
    receipt_date    DATE NOT NULL,
    receipt_at      TIMESTAMPTZ,
    category        TEXT NOT NULL,
    report_name     TEXT NOT NULL,
    report_type     TEXT,
    is_correction_name BOOLEAN NOT NULL DEFAULT false,
    is_correction_rm   BOOLEAN NOT NULL DEFAULT false,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (receipt_no ~ '^[0-9]{14}$')
);
CREATE INDEX IF NOT EXISTS disclosures_company_date_idx ON disclosures (company_id, receipt_date DESC, receipt_no DESC);
CREATE INDEX IF NOT EXISTS disclosures_category_type_date_idx ON disclosures (category, report_type, receipt_date DESC);
CREATE INDEX IF NOT EXISTS disclosures_report_name_trgm_idx ON disclosures USING gin (report_name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS disclosure_files (
    file_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    receipt_no      VARCHAR(14) NOT NULL REFERENCES disclosures(receipt_no) ON DELETE CASCADE,
    relative_path   TEXT NOT NULL,
    file_role       TEXT NOT NULL DEFAULT 'main',
    root_tag        TEXT,
    encoding        TEXT,
    byte_size       BIGINT,
    sha256          CHAR(64) NOT NULL,
    source_order    SMALLINT NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (receipt_no, relative_path),
    UNIQUE (receipt_no, source_order),
    CHECK (byte_size IS NULL OR byte_size >= 0),
    CHECK (source_order > 0),
    CHECK (sha256 ~ '^[0-9a-f]{64}$')
);
CREATE INDEX IF NOT EXISTS disclosure_files_receipt_idx ON disclosure_files (receipt_no);

CREATE TABLE IF NOT EXISTS contracts (
    contract_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    receipt_no      VARCHAR(14) NOT NULL UNIQUE REFERENCES disclosures(receipt_no) ON DELETE CASCADE,
    contract_name_raw TEXT,
    contract_name   TEXT,
    contract_amount_raw TEXT,
    contract_amount NUMERIC(24,0),
    contract_currency CHAR(3) NOT NULL DEFAULT 'KRW',
    counterparty_raw TEXT,
    counterparty    TEXT,
    contract_start_date_raw TEXT,
    contract_start_date DATE,
    contract_end_date_raw TEXT,
    contract_end_date DATE,
    sales_ratio_raw TEXT,
    sales_ratio     NUMERIC(12,6),
    source_file_id  BIGINT NOT NULL REFERENCES disclosure_files(file_id),
    field_provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    parser_version  TEXT NOT NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (sales_ratio IS NULL OR sales_ratio >= 0),
    CHECK (jsonb_typeof(field_provenance) = 'object')
);
CREATE INDEX IF NOT EXISTS contracts_amount_idx ON contracts (contract_amount DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS contracts_counterparty_trgm_idx ON contracts USING gin (counterparty gin_trgm_ops);
CREATE INDEX IF NOT EXISTS contracts_period_idx ON contracts (contract_start_date, contract_end_date);

CREATE TABLE IF NOT EXISTS correction_links (
    predecessor_receipt_no VARCHAR(14) NOT NULL REFERENCES disclosures(receipt_no),
    successor_receipt_no   VARCHAR(14) NOT NULL REFERENCES disclosures(receipt_no),
    link_method     TEXT NOT NULL,
    confidence      TEXT NOT NULL,
    explicit_original_date DATE NOT NULL,
    evidence        JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_analysis_path TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (predecessor_receipt_no, successor_receipt_no),
    CHECK (predecessor_receipt_no <> successor_receipt_no),
    CHECK (link_method = 'explicit_original_filing_date'),
    CHECK (confidence = 'high')
);
CREATE UNIQUE INDEX IF NOT EXISTS correction_links_one_predecessor_idx ON correction_links (successor_receipt_no);
CREATE INDEX IF NOT EXISTS correction_links_successors_idx ON correction_links (predecessor_receipt_no);

CREATE TABLE IF NOT EXISTS document_sections (
    section_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    receipt_no      VARCHAR(14) NOT NULL REFERENCES disclosures(receipt_no) ON DELETE CASCADE,
    file_id         BIGINT NOT NULL REFERENCES disclosure_files(file_id) ON DELETE CASCADE,
    parent_section_id BIGINT REFERENCES document_sections(section_id) ON DELETE SET NULL,
    section_order   INTEGER NOT NULL,
    section_path    TEXT NOT NULL,
    title_raw       TEXT,
    title_normalized TEXT,
    concept         TEXT,
    body_text       TEXT NOT NULL,
    source_locator  TEXT NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_tsv      TSVECTOR GENERATED ALWAYS AS
        (to_tsvector('simple', coalesce(title_normalized, '') || ' ' || body_text)) STORED,
    UNIQUE (file_id, section_order),
    CHECK (section_order >= 0),
    CHECK (body_text <> '')
);
CREATE INDEX IF NOT EXISTS document_sections_receipt_order_idx ON document_sections (receipt_no, section_order);
CREATE INDEX IF NOT EXISTS document_sections_concept_idx ON document_sections (concept) WHERE concept IS NOT NULL;
CREATE INDEX IF NOT EXISTS document_sections_search_idx ON document_sections USING gin (search_tsv);

-- Future migration, intentionally not executed in V1:
-- CREATE EXTENSION vector;
-- ALTER TABLE document_sections ADD COLUMN embedding vector(<model_dimension>);
-- CREATE INDEX ... USING hnsw (embedding vector_cosine_ops);
