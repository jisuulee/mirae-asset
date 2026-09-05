-- Dedicated title/path retrieval channel. Source title/path values remain unchanged.
ALTER TABLE document_sections
    ADD COLUMN IF NOT EXISTS title_path_search_normalized TEXT
    GENERATED ALWAYS AS (
      lower(regexp_replace(translate(coalesce(title_normalized,'') || ' ' || coalesce(section_path,''),
        U&'\200B\200C\200D\FEFF\00A0', '     '), '\s+', ' ', 'g'))
    ) STORED;

ALTER TABLE document_sections
    ADD COLUMN IF NOT EXISTS title_path_search_nospace TEXT
    GENERATED ALWAYS AS (regexp_replace(lower(regexp_replace(
      translate(coalesce(title_normalized,'') || ' ' || coalesce(section_path,''),
        U&'\200B\200C\200D\FEFF\00A0', '     '), '\s+', ' ', 'g')), E'\\s+', '', 'g')) STORED;

ALTER TABLE document_sections
    ADD COLUMN IF NOT EXISTS title_path_search_tsv TSVECTOR
    GENERATED ALWAYS AS (
      to_tsvector('simple', coalesce(title_normalized,'') || ' ' || coalesce(section_path,''))
    ) STORED;

CREATE INDEX IF NOT EXISTS document_sections_title_path_fts_idx
    ON document_sections USING gin (title_path_search_tsv);
CREATE INDEX IF NOT EXISTS document_sections_title_path_trgm_idx
    ON document_sections USING gin (title_path_search_normalized gin_trgm_ops);
CREATE INDEX IF NOT EXISTS document_sections_title_path_nospace_trgm_idx
    ON document_sections USING gin (title_path_search_nospace gin_trgm_ops);
