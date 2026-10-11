-- C3-Mass Dashboard: Initial Schema
-- SQLite DDL — run via database.py init_db()

CREATE TABLE IF NOT EXISTS folders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    parent_id   INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name, parent_id)
);

CREATE TABLE IF NOT EXISTS rockets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL UNIQUE,
    folder_id   INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    description TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS engines (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cfg_filename  TEXT NOT NULL UNIQUE,
    engine_name   TEXT,
    thrust_kN     REAL NOT NULL DEFAULT 0,
    isp_vac_s     REAL NOT NULL DEFAULT 0,
    isp_sl_s      REAL,
    isp_type      TEXT DEFAULT 'unknown',
    isp_curve     TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS stages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    rocket_id      INTEGER NOT NULL REFERENCES rockets(id) ON DELETE CASCADE,
    stage_index    INTEGER NOT NULL,
    wet_mass_t     REAL NOT NULL,
    dry_mass_t     REAL NOT NULL,
    multi_ignition INTEGER NOT NULL DEFAULT 0,
    parallel       INTEGER NOT NULL DEFAULT 0,
    UNIQUE(rocket_id, stage_index)
);

CREATE TABLE IF NOT EXISTS stage_engines (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_id   INTEGER NOT NULL REFERENCES stages(id) ON DELETE CASCADE,
    engine_id  INTEGER NOT NULL REFERENCES engines(id) ON DELETE RESTRICT,
    count      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS calibration_points (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    rocket_id      INTEGER NOT NULL REFERENCES rockets(id) ON DELETE CASCADE,
    payload_t      REAL NOT NULL,
    orbit_alt_km   REAL NOT NULL DEFAULT 220,
    dv_rem_m_s     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS reference_missions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rocket_id     INTEGER NOT NULL REFERENCES rockets(id) ON DELETE CASCADE,
    mission_key   TEXT NOT NULL,
    label         TEXT NOT NULL,
    c3            REAL,
    apoapsis_km   REAL,
    color         TEXT NOT NULL DEFAULT '#888888',
    UNIQUE(rocket_id, mission_key)
);

CREATE TABLE IF NOT EXISTS standard_missions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    label      TEXT NOT NULL,
    c3         REAL NOT NULL,
    dv         REAL NOT NULL,
    group_name TEXT NOT NULL DEFAULT 'Custom',
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS computation_results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    rocket_id    INTEGER NOT NULL UNIQUE REFERENCES rockets(id) ON DELETE CASCADE,
    m_pl_max     REAL NOT NULL,
    c3_max       REAL NOT NULL,
    c3_0_payload REAL NOT NULL,
    c3_min       REAL NOT NULL,
    loss_l0      REAL NOT NULL DEFAULT 0,
    loss_l1      REAL NOT NULL DEFAULT 0,
    loss_l2      REAL NOT NULL DEFAULT 0,
    data_json    TEXT NOT NULL,
    computed_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS c3_mass_curve_points (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    rocket_id INTEGER NOT NULL REFERENCES rockets(id) ON DELETE CASCADE,
    c3        REAL NOT NULL,
    payload   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_c3_curve_rocket ON c3_mass_curve_points(rocket_id, c3);

CREATE TABLE IF NOT EXISTS dv_curve_points (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    rocket_id INTEGER NOT NULL REFERENCES rockets(id) ON DELETE CASCADE,
    m         REAL NOT NULL,
    dv        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dv_curve_rocket ON dv_curve_points(rocket_id, m);

CREATE TABLE IF NOT EXISTS activity_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    action     TEXT NOT NULL,
    entity     TEXT NOT NULL,
    entity_id  INTEGER,
    details    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
