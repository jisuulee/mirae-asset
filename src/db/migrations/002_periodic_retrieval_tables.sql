-- Minimal storage additions for periodic Retrieval sections and table projections.
-- No vector/embedding objects are introduced here.

ALTER TABLE document_sections
    DROP CONSTRAINT IF EXISTS document_sections_body_text_check;
ALTER TABLE document_sections
    ADD CONSTRAINT document_sections_body_text_check
    CHECK (
        body_text <> '' OR
        metadata->>'section_kind' = 'hierarchy_only'
    );

CREATE TABLE IF NOT EXISTS retrieval_tables (
    retrieval_table_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_id         TEXT NOT NULL UNIQUE,
    receipt_no       VARCHAR(14) NOT NULL REFERENCES disclosures(receipt_no) ON DELETE CASCADE,
    file_id          BIGINT NOT NULL REFERENCES disclosure_files(file_id) ON DELETE CASCADE,
    section_id       BIGINT NOT NULL REFERENCES document_sections(section_id) ON DELETE CASCADE,
    table_order      INTEGER NOT NULL,
    caption          TEXT,
    searchable_text  TEXT NOT NULL,
    row_count        INTEGER NOT NULL,
    column_count     INTEGER NOT NULL,
    has_rowspan      BOOLEAN NOT NULL DEFAULT false,
    has_colspan      BOOLEAN NOT NULL DEFAULT false,
    source_locator   TEXT NOT NULL,
    provenance       JSONB NOT NULL DEFAULT '{}'::jsonb,
    archive_path     TEXT NOT NULL,
    archive_identifier TEXT NOT NULL,
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_tsv       TSVECTOR GENERATED ALWAYS AS
        (to_tsvector('simple', coalesce(caption, '') || ' ' || searchable_text)) STORED,
    UNIQUE (file_id, table_order),
    CHECK (table_order >= 0),
    CHECK (row_count >= 0),
    CHECK (column_count >= 0),
    CHECK (length(searchable_text) <= 12000),
    CHECK (jsonb_typeof(provenance) = 'object'),
    CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX IF NOT EXISTS retrieval_tables_receipt_order_idx
    ON retrieval_tables (receipt_no, table_order);
CREATE INDEX IF NOT EXISTS retrieval_tables_section_idx
    ON retrieval_tables (section_id);
CREATE INDEX IF NOT EXISTS retrieval_tables_search_idx
    ON retrieval_tables USING gin (search_tsv);
