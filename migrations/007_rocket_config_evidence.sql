CREATE TABLE IF NOT EXISTS rocket_config_evidence (
    rocket_id INTEGER PRIMARY KEY REFERENCES rockets(id) ON DELETE CASCADE,
    config_json TEXT NOT NULL
);
