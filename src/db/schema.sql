PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS companies (
    company_id INTEGER PRIMARY KEY,
    corp_code TEXT UNIQUE,
    company_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS disclosures (
    receipt_no TEXT PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(company_id),
    filing_date TEXT NOT NULL CHECK (length(filing_date) = 10),
    report_name TEXT NOT NULL,
    is_correction INTEGER NOT NULL CHECK (is_correction IN (0, 1)),
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contracts (
    receipt_no TEXT PRIMARY KEY REFERENCES disclosures(receipt_no) ON DELETE CASCADE,
    contract_amount NUMERIC,
    contract_amount_raw TEXT,
    contract_amount_source TEXT,
    counterparty TEXT,
    counterparty_raw TEXT,
    counterparty_source TEXT,
    contract_start_date TEXT,
    contract_start_date_raw TEXT,
    contract_start_date_source TEXT,
    contract_end_date TEXT,
    contract_end_date_raw TEXT,
    contract_end_date_source TEXT,
    sales_ratio NUMERIC,
    sales_ratio_raw TEXT,
    sales_ratio_source TEXT
);

CREATE TABLE IF NOT EXISTS correction_links (
    original_receipt_no TEXT NOT NULL REFERENCES disclosures(receipt_no),
    correction_receipt_no TEXT NOT NULL REFERENCES disclosures(receipt_no),
    link_method TEXT NOT NULL CHECK (link_method = 'explicit_original_filing_date'),
    confidence TEXT NOT NULL CHECK (confidence = 'high'),
    evidence_original_date TEXT NOT NULL,
    source_analysis_path TEXT NOT NULL,
    PRIMARY KEY (original_receipt_no, correction_receipt_no),
    CHECK (original_receipt_no <> correction_receipt_no)
);

CREATE INDEX IF NOT EXISTS idx_disclosures_company_date
    ON disclosures(company_id, filing_date);
CREATE INDEX IF NOT EXISTS idx_correction_links_correction
    ON correction_links(correction_receipt_no);

