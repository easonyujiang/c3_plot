"""
SQLite database initialization, connection management, and auto-migration.
"""
import os
import json
import sqlite3
from flask import g

from .config import DB_PATH, MIGRATIONS_DIR, ROCKETS_DIR, ENGINES_DIR, OUTPUT_DIR


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


def _repair_missing_stage_engines(db):
    """Repair rockets migrated before engines were imported."""
    import os as _os

    se_count = db.execute("SELECT COUNT(*) as n FROM stage_engines").fetchone()['n']
    if se_count > 0:
        return

    rockets = db.execute("SELECT id, slug, name FROM rockets").fetchall()
    if not rockets:
        return

    print("  Repairing missing stage engine links from YAML files...")
    for rocket in rockets:
        yaml_path = _os.path.join(ROCKETS_DIR, rocket['slug'] + '.yaml')
        if not _os.path.exists(yaml_path):
            yaml_path = _os.path.join(ROCKETS_DIR, rocket['slug'].replace('_', ' ') + '.yaml')
        if not _os.path.exists(yaml_path):
            continue
        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                from .yaml_codec import load_config
                cfg = load_config(f.read())
        except Exception as e:
            print(f"    SKIP repair {rocket['name']}: {e}")
            continue

        stages = cfg.get('stages', [])
        for i, s in enumerate(stages):
            stage_row = db.execute(
                "SELECT id FROM stages WHERE rocket_id=? AND stage_index=?",
                (rocket['id'], i)
            ).fetchone()
            if not stage_row:
                continue
            stage_id = stage_row['id']
            eng = s.get('engines', {})
            eng_cfg = eng.get('cfg', '')
            eng_count = eng.get('count', 1)
            multi = 1 if s.get('multi_ignition', False) else 0
            parallel = 1 if s.get('parallel', False) else 0
            db.execute(
                "UPDATE stages SET multi_ignition=?, parallel=? WHERE id=?",
                (multi, parallel, stage_id)
            )
            eng_row = db.execute(
                "SELECT id FROM engines WHERE cfg_filename=?", (eng_cfg,)
            ).fetchone()
            if eng_row:
                db.execute(
                    "INSERT INTO stage_engines (stage_id, engine_id, count) VALUES (?, ?, ?)",
                    (stage_id, eng_row['id'], eng_count)
                )
    db.commit()


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
    Initialize the database: run schema migrations, seed data,
    and auto-migrate from legacy YAML/JSON files if needed.
    """
    # Connect directly (not via g) for initialization
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")

    # Run schema migration
    _run_sql_file(db, '001_initial_schema.sql')
    _run_sql_file(db, '002_seed_standard_missions.sql')
    _ensure_stage_parallel_column(db)
    _run_sql_file(db, '003_add_stage_parallel_and_indexes.sql')
    _run_sql_file(db, '004_dedupe_standard_missions.sql')
    _run_sql_file(db, '005_import_sources.sql')
    _run_sql_file(db, '006_folder_sibling_uniqueness.sql')
    _run_sql_file(db, '007_rocket_config_evidence.sql')
    _ensure_indexes(db)
    db.commit()

    # Parse engines from .cfg files into DB FIRST so rockets can link to them
    eng_count = db.execute("SELECT COUNT(*) as n FROM engines").fetchone()['n']
    if eng_count == 0 and os.path.isdir(ENGINES_DIR):
        print("  Importing engine definitions from .cfg files...")
        _import_engines(db)

    # Auto-migrate from legacy YAML/JSON files ONLY on first-ever run
    count = db.execute("SELECT COUNT(*) as n FROM rockets").fetchone()['n']
    if count == 0 and os.path.isdir(ROCKETS_DIR):
        yaml_files = [f for f in os.listdir(ROCKETS_DIR)
                      if f.endswith(('.yaml', '.yml'))]
        if yaml_files:
            print("  First run: auto-migrating legacy YAML files to SQLite...")
            _auto_migrate(db, yaml_files)
        else:
            print("  Database is empty. Create rockets via the web UI or add YAML files to rockets/.")

    # Repair legacy rockets that were migrated before engines existed
    _repair_missing_stage_engines(db)

    db.close()

    # Register teardown
    app.teardown_appcontext(close_db)


def _auto_migrate(db, yaml_files):
    """Migrate existing YAML rocket configs and JSON results into SQLite."""
    import json
    import re
    import sys
    import os as _os

    # Ensure compute_c3_mass is importable
    _proj_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    if _proj_root not in sys.path:
        sys.path.insert(0, _proj_root)

    from compute_c3_mass import compute_rocket
    from engine_parser import extract_engine_params

    for fname in sorted(yaml_files):
        filepath = os.path.join(ROCKETS_DIR, fname)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                from .yaml_codec import load_config
                cfg = load_config(f.read())
        except Exception as e:
            print(f"    SKIP {fname}: YAML parse error - {e}")
            continue

        name = cfg.get('rocket', {}).get('name', fname)
        slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')

        # Try to load pre-computed result
        result = None
        json_path = os.path.join(OUTPUT_DIR, slug, 'data.json')
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    result = json.load(f)
            except Exception:
                pass

        # Compute if no cached result
        if result is None:
            try:
                result = compute_rocket(cfg)
            except Exception as e:
                print(f"    SKIP {name}: computation error - {e}")
                continue

        # Insert into SQLite
        try:
            _insert_rocket_from_config(db, name, slug, cfg, result)
            print(f"    {name}: LEO={result['m_pl_max']}t  C3=0={result['c3_0_payload']}t  maxC3={result['c3_max']}")
        except Exception as e:
            print(f"    ERROR {name}: {e}")
            db.rollback()


def _insert_rocket_from_config(db, name, slug, cfg, result):
    """Insert a rocket config and computed result into the database."""
    import json as _json

    # Rocket
    db.execute(
        "INSERT INTO rockets (name, slug) VALUES (?, ?)",
        (name, slug)
    )
    rocket_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Stages
    for i, s in enumerate(cfg.get('stages', [])):
        engine_groups = s.get('engine_groups') or [s.get('engines', {})]
        if isinstance(engine_groups, dict):
            engine_groups = [engine_groups]
        multi = 1 if s.get('multi_ignition', False) else 0
        parallel = 1 if s.get('parallel', False) else 0

        db.execute(
            "INSERT INTO stages (rocket_id, stage_index, wet_mass_t, dry_mass_t, multi_ignition, parallel) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rocket_id, i, s['wet_mass_t'], s['dry_mass_t'], multi, parallel)
        )
        stage_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Link all engine groups (a single SSTU part may represent many engines).
        for group in engine_groups:
            eng_cfg = group.get('cfg', '')
            eng_count = group.get('count', 1)
            eng_row = db.execute(
                "SELECT id FROM engines WHERE cfg_filename = ?", (eng_cfg,)
            ).fetchone()
            if eng_row:
                db.execute(
                    "INSERT INTO stage_engines (stage_id, engine_id, count) VALUES (?, ?, ?)",
                    (stage_id, eng_row['id'], eng_count)
                )

    # Calibration points
    for c in cfg.get('calibration', []):
        db.execute(
            "INSERT INTO calibration_points (rocket_id, payload_t, orbit_alt_km, dv_rem_m_s) "
            "VALUES (?, ?, ?, ?)",
            (rocket_id, c['payload_t'], c.get('orbit_alt_km', 220), c['dv_rem_m_s'])
        )

    # Reference missions
    for key, m in cfg.get('reference_missions', {}).items():
        c3_val = m.get('c3')
        apo = m.get('apoapsis_km')
        db.execute(
            "INSERT INTO reference_missions (rocket_id, mission_key, label, c3, apoapsis_km, color) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rocket_id, key, m['label'], c3_val, apo, m.get('color', '#888888'))
        )

    # Computation result
    data_json = _json.dumps(result, ensure_ascii=False)
    loss = result.get('loss_model', {})
    db.execute(
        "INSERT INTO computation_results (rocket_id, m_pl_max, c3_max, c3_0_payload, c3_min, "
        "loss_l0, loss_l1, loss_l2, data_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rocket_id, result['m_pl_max'], result['c3_max'], result['c3_0_payload'],
         result.get('c3_min', 0), loss.get('L0', 0), loss.get('L1', 0), loss.get('L2', 0),
         data_json)
    )

    # C3-Mass curve points
    for pt in result.get('c3_mass_curve', []):
        db.execute(
            "INSERT INTO c3_mass_curve_points (rocket_id, c3, payload) VALUES (?, ?, ?)",
            (rocket_id, pt['c3'], pt['payload'])
        )

    # dv-Mass curve points
    for pt in result.get('dv_curve', []):
        db.execute(
            "INSERT INTO dv_curve_points (rocket_id, m, dv) VALUES (?, ?, ?)",
            (rocket_id, pt['m'], pt['dv'])
        )

    # Activity log
    db.execute(
        "INSERT INTO activity_log (action, entity, entity_id, details) VALUES (?, ?, ?, ?)",
        ('migrate', 'rocket', rocket_id, f"Migrated from YAML: {name}")
    )
    db.commit()


def _import_engines(db):
    """Parse all .cfg files in engines/ and insert into the engines table."""
    import sys
    import os as _os

    _proj_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    if _proj_root not in sys.path:
        sys.path.insert(0, _proj_root)
    from engine_parser import extract_engine_params

    for fname in sorted(os.listdir(ENGINES_DIR)):
        if not fname.endswith('.cfg'):
            continue
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
