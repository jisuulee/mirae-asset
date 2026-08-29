-- Keyword baseline indexes. Original body_text/title/path remain unchanged.
ALTER TABLE document_sections
    ADD COLUMN IF NOT EXISTS search_text_normalized TEXT
    GENERATED ALWAYS AS (
      lower(regexp_replace(
        translate(coalesce(title_normalized,'') || ' ' || coalesce(section_path,'') || ' ' || coalesce(body_text,''),
          U&'\200B\200C\200D\FEFF\00A0', '     '),
        '\s+', ' ', 'g'))
    ) STORED;

ALTER TABLE document_sections
    ADD COLUMN IF NOT EXISTS search_text_nospace TEXT
    GENERATED ALWAYS AS (regexp_replace(lower(regexp_replace(translate(coalesce(title_normalized,'') || ' ' || coalesce(section_path,'') || ' ' || coalesce(body_text,''), U&'\200B\200C\200D\FEFF\00A0', '     '), '\s+', ' ', 'g')), E'\\s+', '', 'g')) STORED;

ALTER TABLE document_sections
    DROP COLUMN IF EXISTS search_tsv;
ALTER TABLE document_sections
    ADD COLUMN search_tsv TSVECTOR GENERATED ALWAYS AS (
      to_tsvector('simple', coalesce(title_normalized,'') || ' ' || coalesce(section_path,'') || ' ' || coalesce(body_text,''))
    ) STORED;

CREATE INDEX IF NOT EXISTS document_sections_search_trgm_idx
    ON document_sections USING gin (search_text_normalized gin_trgm_ops);
CREATE INDEX IF NOT EXISTS document_sections_title_trgm_idx
    ON document_sections USING gin (title_normalized gin_trgm_ops);

CREATE INDEX IF NOT EXISTS document_sections_nospace_trgm_idx
    ON document_sections USING gin (search_text_nospace gin_trgm_ops);
