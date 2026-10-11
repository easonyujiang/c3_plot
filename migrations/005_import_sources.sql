-- Source provenance and idempotent batch-import tracking.
CREATE TABLE IF NOT EXISTS import_sources (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    rocket_id          INTEGER NOT NULL REFERENCES rockets(id) ON DELETE CASCADE,
    source_filename    TEXT NOT NULL,
    source_sha256      TEXT NOT NULL UNIQUE,
    source_format      TEXT NOT NULL,
    metadata_json      TEXT NOT NULL DEFAULT '{}',
    parser_warnings    TEXT NOT NULL DEFAULT '[]',
    imported_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_import_sources_rocket ON import_sources(rocket_id);
