-- Evidence-based indexes from full-corpus EXPLAIN ANALYZE.
CREATE INDEX IF NOT EXISTS document_sections_search_idx
    ON document_sections USING gin (search_tsv);
CREATE INDEX IF NOT EXISTS document_sections_loader_namespace_idx
    ON document_sections ((metadata->>'loader_namespace'));
