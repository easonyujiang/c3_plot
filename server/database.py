"""
SQLite schema initialization and connection management; no sample data seeding.
"""
import os
import json
import sqlite3
from flask import g

from .config import DB_PATH, MIGRATIONS_DIR


def get_db():
    """Get a database connection for the current request."""
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def close_db(e=None):
    """Close the database connection at the end of a request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def _ensure_stage_parallel_column(db):
    """Idempotently add parallel column to stages for older SQLite compat."""
    cols = [row['name'] for row in db.execute("PRAGMA table_info(stages)").fetchall()]
    if 'parallel' not in cols:
        db.execute("ALTER TABLE stages ADD COLUMN parallel INTEGER NOT NULL DEFAULT 0")


def _ensure_indexes(db):
    """Idempotently create performance indexes if they do not exist."""
    indexes = [
        ('idx_rockets_slug', 'CREATE INDEX IF NOT EXISTS idx_rockets_slug ON rockets(slug)'),
        ('idx_rockets_folder', 'CREATE INDEX IF NOT EXISTS idx_rockets_folder ON rockets(folder_id)'),
        ('idx_stages_rocket', 'CREATE INDEX IF NOT EXISTS idx_stages_rocket ON stages(rocket_id, stage_index)'),
        ('idx_stages_multi_ignition', 'CREATE INDEX IF NOT EXISTS idx_stages_multi_ignition ON stages(multi_ignition)'),
        ('idx_stages_parallel', 'CREATE INDEX IF NOT EXISTS idx_stages_parallel ON stages(parallel)'),
    ]
    existing = {row['name'] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    for name, sql in indexes:
        if name not in existing:
            try:
                db.execute(sql)
            except sqlite3.Error as e:
                print(f"  [DB] Failed to create index {name}: {e}")


def _run_sql_file(db, filename):
    """Execute all SQL statements in a file."""
    filepath = os.path.join(MIGRATIONS_DIR, filename)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f'Migration file not found: {filepath}')
    with open(filepath, 'r', encoding='utf-8') as f:
        sql = f.read()
    try:
        db.executescript(sql)
    except sqlite3.Error as e:
        raise RuntimeError(f'Migration failed: {filename}: {e}') from e


def init_db(app):
    """
    Initialize schema only. Business data is populated by explicit user actions.
    """
    # Connect directly (not via g) for initialization
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")

    # Run schema migration
    _run_sql_file(db, '001_initial_schema.sql')
    _ensure_stage_parallel_column(db)
    _run_sql_file(db, '003_add_stage_parallel_and_indexes.sql')
    _run_sql_file(db, '004_dedupe_standard_missions.sql')
    _run_sql_file(db, '005_import_sources.sql')
    _run_sql_file(db, '006_folder_sibling_uniqueness.sql')
    _run_sql_file(db, '007_rocket_config_evidence.sql')
    _ensure_indexes(db)
    db.commit()

    # Empty means empty: no bundled designs, engines, missions or archive replay.
    # YAML restore and game imports are explicit user actions.
    db.close()

    # Register teardown
    app.teardown_appcontext(close_db)


def _import_engines(db):
    """Parse all .cfg files in engines/ and insert into the engines table."""
    import sys
    import os as _os

    _proj_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    if _proj_root not in sys.path:
        sys.path.insert(0, _proj_root)
    from c3_mass.engine_parser import extract_engine_params

    from c3_mass.game_data import engine_cfg_files
    for path in engine_cfg_files():
        fname = path.name
        try:
            ep = extract_engine_params(fname)
            db.execute(
                "INSERT INTO engines (cfg_filename, engine_name, thrust_kN, isp_vac_s, isp_sl_s, isp_type, isp_curve) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(cfg_filename) DO UPDATE SET "
                "engine_name=excluded.engine_name, thrust_kN=excluded.thrust_kN, "
                "isp_vac_s=excluded.isp_vac_s, isp_sl_s=excluded.isp_sl_s, "
                "isp_type=excluded.isp_type, isp_curve=excluded.isp_curve",
                (fname, ep['engine_name'], ep['thrust_kN'], ep['isp_vac_s'],
                 ep['isp_sl_s'], ep['isp_type'], json.dumps(ep['isp_curve'], ensure_ascii=False))
            )
        except Exception as e:
            print(f"    WARN engine {fname}: {e}")
    db.commit()
    count = db.execute("SELECT COUNT(*) as n FROM engines").fetchone()['n']
    print(f"    Imported {count} engines.")
