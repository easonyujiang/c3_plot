"""
Data access layer — all CRUD operations against SQLite.
Also syncs rocket configs to YAML files for backup.
"""
import json
import os
import re
import sqlite3
import tempfile
import yaml
from .config import ROCKETS_DIR
from flask import g
from .database import get_db


# =============================================================================
# Rockets
# =============================================================================

def slugify(name):
    """Create a URL/file-safe slug without discarding Unicode rocket names."""
    slug = re.sub(r'[^\w-]+', '_', name.casefold(), flags=re.UNICODE).strip('_-')
    if slug:
        return slug
    # Emoji-only or punctuation-only names need a stable, non-empty identifier.
    import hashlib
    return 'rocket_' + hashlib.sha256(name.encode('utf-8')).hexdigest()[:12]


def _plain_data(value):
    """Convert numpy scalar values to YAML-safe Python primitives."""
    if isinstance(value, dict):
        return {_plain_data(key): _plain_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_data(item) for item in value]
    item = getattr(value, 'item', None)
    if callable(item):
        try:
            return _plain_data(item())
        except (TypeError, ValueError):
            pass
    return value


def _sync_yaml(slug, cfg):
    """Atomically write the YAML backup and return its previous bytes, if any."""
    os.makedirs(ROCKETS_DIR, exist_ok=True)
    filepath = os.path.join(ROCKETS_DIR, slug + '.yaml')
    previous = _read_yaml_bytes(filepath)
    # Remove internal fields
    cfg_clean = _plain_data({k: v for k, v in cfg.items() if not k.startswith('_')})
    content = yaml.dump(cfg_clean, allow_unicode=True, default_flow_style=False,
                        sort_keys=False).encode('utf-8')
    _write_yaml_bytes(filepath, content)
    return previous


def _delete_yaml(slug):
    """Remove YAML backup and return its previous bytes, if any."""
    filepath = os.path.join(ROCKETS_DIR, slug + '.yaml')
    previous = _read_yaml_bytes(filepath)
    if previous is not None:
        os.remove(filepath)
    return previous


def _read_yaml_bytes(filepath):
    try:
        with open(filepath, 'rb') as f:
            return f.read()
    except FileNotFoundError:
        return None


def _write_yaml_bytes(filepath, content):
    directory = os.path.dirname(filepath)
    fd, temporary = tempfile.mkstemp(prefix='.rocket-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, filepath)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _restore_yaml(slug, previous):
    filepath = os.path.join(ROCKETS_DIR, slug + '.yaml')
    if previous is None:
        try:
            os.remove(filepath)
        except FileNotFoundError:
            pass
    else:
        _write_yaml_bytes(filepath, previous)


def list_rockets():
    db = get_db()
    rows = db.execute(
        "SELECT r.id, r.name, r.slug, r.folder_id, r.created_at, r.updated_at,"
        "  cr.m_pl_max, cr.c3_0_payload, cr.c3_max"
        " FROM rockets r"
        " LEFT JOIN computation_results cr ON cr.rocket_id = r.id"
        " ORDER BY r.name"
    ).fetchall()
    return [dict(r) for r in rows]


def move_rocket(slug, folder_id):
    """Move a rocket to a folder (folder_id=None for root)."""
    db = get_db()
    if not db.execute("SELECT 1 FROM rockets WHERE slug = ?", (slug,)).fetchone():
        return False
    if folder_id is not None and not db.execute("SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone():
        return False
    db.execute("UPDATE rockets SET folder_id = ?, updated_at = datetime('now') WHERE slug = ?", (folder_id, slug))
    db.commit()
    return True


# =============================================================================
# Folders
# =============================================================================

def list_folders():
    """Return flat list of all folders."""
    db = get_db()
    rows = db.execute(
        "SELECT f.*, (SELECT COUNT(*) FROM rockets WHERE folder_id = f.id) as rocket_count"
        " FROM folders f ORDER BY f.sort_order, f.name"
    ).fetchall()
    return [dict(r) for r in rows]


def get_folder_tree():
    """Return full folder tree with rocket counts."""
    db = get_db()
    folders = db.execute(
        "SELECT f.*, (SELECT COUNT(*) FROM rockets WHERE folder_id = f.id) as rocket_count"
        " FROM folders f ORDER BY f.sort_order, f.name"
    ).fetchall()
    rockets = db.execute(
        "SELECT r.id, r.name, r.slug, r.folder_id,"
        "  cr.m_pl_max, cr.c3_0_payload, cr.c3_max"
        " FROM rockets r"
        " LEFT JOIN computation_results cr ON cr.rocket_id = r.id"
        " ORDER BY r.name"
    ).fetchall()

    folder_list = [dict(f) for f in folders]
    rocket_list = [dict(r) for r in rockets]

    def build_tree(parent_id):
        children = []
        for f in folder_list:
            if f['parent_id'] == parent_id:
                node = dict(f)
                node['children'] = build_tree(f['id'])
                node['rockets'] = [r for r in rocket_list if r['folder_id'] == f['id']]
                children.append(node)
        return children

    root = {
        'folders': build_tree(None),
        'rockets': [r for r in rocket_list if r['folder_id'] is None],
    }
    return root


def create_folder(name, parent_id=None):
    db = get_db()
    try:
        db.execute(
            "INSERT INTO folders (name, parent_id) VALUES (?, ?)",
            (name, parent_id)
        )
        db.commit()
        return db.execute("SELECT last_insert_rowid()").fetchone()[0]
    except sqlite3.IntegrityError:
        db.rollback()
        return None


def rename_folder(folder_id, name):
    db = get_db()
    cursor = db.execute("UPDATE folders SET name = ? WHERE id = ?", (name, folder_id))
    db.commit()
    return cursor.rowcount == 1


def delete_folder(folder_id):
    """Delete a folder. Child folders cascade, rockets inside go to root."""
    db = get_db()
    # Move rockets to root first
    if not db.execute("SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone():
        return False
    db.execute("UPDATE rockets SET folder_id = NULL WHERE folder_id = ?", (folder_id,))
    db.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    db.commit()
    return True


def get_rocket_config(slug):
    """Return the full rocket config in the legacy dict format (for the editor)."""
    db = get_db()
    rocket = db.execute("SELECT * FROM rockets WHERE slug = ?", (slug,)).fetchone()
    if not rocket:
        return None

    stage_rows = db.execute(
        "SELECT s.*, e.cfg_filename, e.engine_name,"
        "  e.thrust_kN, e.isp_vac_s, e.isp_sl_s, e.isp_type, se.count as engine_count"
        " FROM stages s"
        " LEFT JOIN stage_engines se ON se.stage_id = s.id"
        " LEFT JOIN engines e ON e.id = se.engine_id"
        " WHERE s.rocket_id = ? ORDER BY s.stage_index",
        (rocket['id'],)
    ).fetchall()

    cal_pts = db.execute(
        "SELECT * FROM calibration_points WHERE rocket_id = ? ORDER BY id",
        (rocket['id'],)
    ).fetchall()

    ref_missions = db.execute(
        "SELECT * FROM reference_missions WHERE rocket_id = ? ORDER BY id",
        (rocket['id'],)
    ).fetchall()

    cfg = {
        'rocket': {'name': rocket['name']},
        'stages': [],
        'calibration': [],
        'reference_missions': {},
    }

    grouped_stages = {}
    for row in stage_rows:
        grouped_stages.setdefault(row['id'], []).append(row)
    for rows in grouped_stages.values():
        first = rows[0]
        groups = [
            {'cfg': row['cfg_filename'] or '', 'count': row['engine_count'] or 1}
            for row in rows
            if row['cfg_filename']
        ]
        stage = {
            'wet_mass_t': first['wet_mass_t'],
            'dry_mass_t': first['dry_mass_t'],
            'engines': groups[0] if groups else {'cfg': '', 'count': 1},
            'engine_groups': groups,
            'multi_ignition': bool(first['multi_ignition']),
            'parallel': bool(first['parallel']),
        }
        cfg['stages'].append(stage)

    for c in cal_pts:
        cfg['calibration'].append({
            'payload_t': c['payload_t'],
            'orbit_alt_km': c['orbit_alt_km'],
            'dv_rem_m_s': c['dv_rem_m_s'],
        })

    for m in ref_missions:
        entry = {'label': m['label'], 'color': m['color']}
        if m['c3'] is not None:
            entry['c3'] = m['c3']
        if m['apoapsis_km'] is not None:
            entry['apoapsis_km'] = m['apoapsis_km']
        cfg['reference_missions'][m['mission_key']] = entry

    # Read-only audit/recovery also opens backups predating migration 007.
    has_evidence = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rocket_config_evidence'").fetchone()
    evidence = db.execute('SELECT config_json FROM rocket_config_evidence WHERE rocket_id=?', (rocket['id'],)).fetchone() if has_evidence else None
    if evidence:
        import json
        original = json.loads(evidence['config_json'])
        for key, value in original.items():
            if key not in ('rocket', 'stages', 'calibration', 'reference_missions'):
                cfg[key] = value
        for stage, original_stage in zip(cfg['stages'], original.get('stages', [])):
            for key, value in original_stage.items():
                if key not in ('wet_mass_t', 'dry_mass_t', 'multi_ignition', 'parallel'):
                    stage[key] = value
        for point, original_point in zip(cfg['calibration'], original.get('calibration', [])):
            point.update({key:value for key,value in original_point.items() if key not in ('payload_t','orbit_alt_km','dv_rem_m_s')})
    return cfg


def create_rocket(name, cfg, result, *, source=None):
    """Insert a new rocket with its full config and computed result. Returns slug."""
    db = get_db()
    slug = slugify(name)

    # Read before attempting SQL: a duplicate slug must never delete an
    # existing rocket's YAML while rolling back the failed create.
    previous = _read_yaml_bytes(os.path.join(ROCKETS_DIR, slug + '.yaml'))
    try:
        db.execute("INSERT INTO rockets (name, slug) VALUES (?, ?)", (name, slug))
        rocket_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        _insert_related(db, rocket_id, cfg, result)
        _log(db, 'create', 'rocket', rocket_id, name)
        if source:
            record_import_source(slug, source, commit=False)
        previous = _sync_yaml(slug, cfg)
        db.commit()
    except Exception:
        db.rollback()
        _restore_yaml(slug, previous)
        raise
    return slug


def update_rocket(slug, cfg, result, *, source=None):
    """Update an existing rocket. Returns (new_slug, old_slug_if_renamed)."""
    db = get_db()
    rocket = db.execute("SELECT id, slug FROM rockets WHERE slug = ?", (slug,)).fetchone()
    if not rocket:
        return None

    rocket_id = rocket['id']
    new_name = cfg['rocket']['name']
    new_slug = slugify(new_name)

    old_slug = slug
    # Capture both files before SQL.  In particular, new_slug may already
    # belong to another rocket and its backup must survive a UNIQUE failure.
    new_backup = _read_yaml_bytes(os.path.join(ROCKETS_DIR, new_slug + '.yaml'))
    old_backup = _read_yaml_bytes(os.path.join(ROCKETS_DIR, old_slug + '.yaml'))
    try:
        db.execute("UPDATE rockets SET name = ?, slug = ?, updated_at = datetime('now') WHERE id = ?",
                   (new_name, new_slug, rocket_id))
        _delete_related(db, rocket_id)
        _insert_related(db, rocket_id, cfg, result)
        _log(db, 'update', 'rocket', rocket_id, new_name)
        if source:
            record_import_source(new_slug, source, commit=False)
        new_backup = _sync_yaml(new_slug, cfg)
        if old_slug != new_slug:
            _delete_yaml(old_slug)
        db.commit()
    except Exception:
        db.rollback()
        _restore_yaml(new_slug, new_backup)
        if old_slug != new_slug:
            _restore_yaml(old_slug, old_backup)
        raise
    return new_slug


def delete_rocket(slug):
    """Delete a rocket and all related data (cascading)."""
    db = get_db()
    rocket = db.execute("SELECT id FROM rockets WHERE slug = ?", (slug,)).fetchone()
    if not rocket:
        return False
    previous = _read_yaml_bytes(os.path.join(ROCKETS_DIR, slug + '.yaml'))
    try:
        _log(db, 'delete', 'rocket', rocket['id'], slug)
        db.execute("DELETE FROM rockets WHERE id = ?", (rocket['id'],))
        _delete_yaml(slug)
        db.commit()
    except Exception:
        db.rollback()
        _restore_yaml(slug, previous)
        raise
    return True


def rocket_exists(slug):
    db = get_db()
    return db.execute("SELECT 1 FROM rockets WHERE slug = ?", (slug,)).fetchone() is not None


def get_import_source_by_hash(source_sha256):
    """Return provenance for an already imported source, if any."""
    db = get_db()
    try:
        row = db.execute(
            "SELECT s.*, r.name AS rocket_name, r.slug AS rocket_slug"
            " FROM import_sources s JOIN rockets r ON r.id = s.rocket_id"
            " WHERE s.source_sha256 = ?",
            (source_sha256,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if 'no such table' not in str(exc).lower():
            raise
        row = None
    return dict(row) if row else None


def record_import_source(slug, source, *, commit=True):
    """Persist source provenance after a successful create/update."""
    if not source or not source.get('sha256'):
        return
    db = get_db()
    rocket = db.execute("SELECT id FROM rockets WHERE slug = ?", (slug,)).fetchone()
    if not rocket:
        return
    metadata = json.dumps(source.get('metadata') or {}, ensure_ascii=False)
    warnings = json.dumps(source.get('warnings') or [], ensure_ascii=False)
    try:
        db.execute(
            "INSERT INTO import_sources (rocket_id, source_filename, source_sha256, source_format, metadata_json, parser_warnings)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(source_sha256) DO UPDATE SET rocket_id=excluded.rocket_id,"
            " source_filename=excluded.source_filename, source_format=excluded.source_format,"
            " metadata_json=excluded.metadata_json, parser_warnings=excluded.parser_warnings,"
            " imported_at=datetime('now')",
            (rocket['id'], source.get('filename', 'unknown'), source['sha256'],
             source.get('format', 'unknown'), metadata, warnings),
        )
    except sqlite3.OperationalError as exc:
        raise
    if commit:
        db.commit()


# =============================================================================
# Computed Data
# =============================================================================

def get_computed_data(slug):
    """Return the full data.json-like dict for a rocket."""
    db = get_db()
    row = db.execute(
        "SELECT cr.data_json FROM computation_results cr"
        " JOIN rockets r ON r.id = cr.rocket_id"
        " WHERE r.slug = ?", (slug,)
    ).fetchone()
    if not row:
        return None
    data = json.loads(row['data_json'])
    sources = db.execute(
        "SELECT s.source_filename, s.source_sha256, s.source_format, s.imported_at"
        " FROM import_sources s JOIN rockets r ON r.id=s.rocket_id WHERE r.slug=?"
        " ORDER BY s.imported_at, s.id", (slug,)
    ).fetchall()
    data['source_summary'] = {
        'status': 'recorded' if sources else 'legacy_unknown',
        'sources': [dict(source) for source in sources],
        'model_quality': 'estimated',
        'manual_confirmation': 'not_recorded',
    }
    evidence=db.execute('SELECT e.config_json FROM rocket_config_evidence e JOIN rockets r ON r.id=e.rocket_id WHERE r.slug=?',(slug,)).fetchone()
    if evidence:
        cfg=json.loads(evidence['config_json'])
        identification=cfg.get('automatic_identification')
        if identification:
            data['source_summary']['automatic_identification']=identification
    return data


def save_computed_data(slug, result):
    """Save or update computation results for a rocket."""
    db = get_db()
    rocket = db.execute("SELECT id FROM rockets WHERE slug = ?", (slug,)).fetchone()
    if not rocket:
        return
    rocket_id = rocket['id']
    yaml_backup = _read_yaml_bytes(os.path.join(ROCKETS_DIR, slug + '.yaml'))
    cfg = get_rocket_config(slug)
    if cfg is not None:
        # Persist the computed mission capacities alongside the editable input
        # so the YAML remains a complete, portable backup of the vehicle.
        refs = cfg.setdefault('reference_missions', {})
        for key, mission in result.get('missions', {}).items():
            entry = dict(refs.get(key, {}))
            entry.update({k: mission[k] for k in ('label', 'c3', 'color') if k in mission})
            entry['payload_t'] = mission.get('payload')
            entry['dv'] = mission.get('dv')
            refs[key] = entry
    data_json = json.dumps(result, ensure_ascii=False)
    loss = result.get('loss_model', {})

    try:
        db.execute("DELETE FROM computation_results WHERE rocket_id = ?", (rocket_id,))
        db.execute("DELETE FROM c3_mass_curve_points WHERE rocket_id = ?", (rocket_id,))
        db.execute("DELETE FROM dv_curve_points WHERE rocket_id = ?", (rocket_id,))

        db.execute(
        "INSERT INTO computation_results (rocket_id, m_pl_max, c3_max, c3_0_payload, c3_min,"
        " loss_l0, loss_l1, loss_l2, data_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rocket_id, result['m_pl_max'], result['c3_max'], result['c3_0_payload'],
         result.get('c3_min', 0), loss.get('L0', 0), loss.get('L1', 0), loss.get('L2', 0),
         data_json)
    )

        for pt in result.get('c3_mass_curve', []):
            db.execute(
            "INSERT INTO c3_mass_curve_points (rocket_id, c3, payload) VALUES (?, ?, ?)",
            (rocket_id, pt['c3'], pt['payload'])
        )
        for pt in result.get('dv_curve', []):
            db.execute(
            "INSERT INTO dv_curve_points (rocket_id, m, dv) VALUES (?, ?, ?)",
            (rocket_id, pt['m'], pt['dv'])
        )
        if cfg is not None:
            _sync_yaml(slug, cfg)
        db.commit()
    except Exception:
        db.rollback()
        _restore_yaml(slug, yaml_backup)
        raise


# =============================================================================
# Engines
# =============================================================================

def list_engines():
    db = get_db()
    rows = db.execute(
        "SELECT id, cfg_filename, engine_name, thrust_kN, isp_vac_s, isp_sl_s, isp_type"
        " FROM engines ORDER BY cfg_filename"
    ).fetchall()
    return [dict(r) for r in rows]


def get_engine_cfg_filenames():
    db = get_db()
    rows = db.execute("SELECT cfg_filename FROM engines ORDER BY cfg_filename").fetchall()
    return [r['cfg_filename'] for r in rows]


def reload_engines():
    """Re-parse all .cfg files into engines table."""
    from .database import _import_engines
    db = get_db()
    _import_engines(db)
    return list_engines()


# =============================================================================
# Standard Missions
# =============================================================================

def list_standard_missions():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM standard_missions ORDER BY sort_order, id"
    ).fetchall()
    missions = []
    for r in rows:
        m = dict(r)
        m['group'] = m.pop('group_name', 'Custom')
        missions.append(m)
    return missions


def create_standard_mission(data):
    db = get_db()
    db.execute(
        "INSERT INTO standard_missions (label, c3, dv, group_name, sort_order) VALUES (?, ?, ?, ?, ?)",
        (data['label'], data['c3'], data['dv'], data.get('group_name', 'Custom'),
         data.get('sort_order', 0))
    )
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def update_standard_mission(mid, data):
    db = get_db()
    cursor = db.execute(
        "UPDATE standard_missions SET label=?, c3=?, dv=?, group_name=?, sort_order=? WHERE id=?",
        (data['label'], data['c3'], data['dv'], data.get('group_name', 'Custom'),
         data.get('sort_order', 0), mid)
    )
    db.commit()
    return cursor.rowcount == 1


def delete_standard_mission(mid):
    db = get_db()
    cursor = db.execute("DELETE FROM standard_missions WHERE id = ?", (mid,))
    db.commit()
    return cursor.rowcount == 1


# =============================================================================
# Mission Planner (find rockets for payload + C3)
# =============================================================================

def interpolate_payload(c3_target, curve_points):
    """Linear interpolation of payload at given C3 from discrete curve points."""
    if not curve_points:
        return 0
    pts = sorted(curve_points, key=lambda p: p['c3'])
    if c3_target <= pts[0]['c3']:
        return pts[0]['payload']
    if c3_target > pts[-1]['c3']:
        return 0.0
    if c3_target == pts[-1]['c3']:
        return pts[-1]['payload']
    for i in range(len(pts) - 1):
        if pts[i]['c3'] <= c3_target <= pts[i + 1]['c3']:
            frac = (c3_target - pts[i]['c3']) / (pts[i + 1]['c3'] - pts[i]['c3'])
            return pts[i]['payload'] + frac * (pts[i + 1]['payload'] - pts[i]['payload'])
    return 0


def find_rockets_for_mission(payload_t, c3):
    """Return all rockets with their payload capacity at a given C3."""
    db = get_db()
    rockets = db.execute("SELECT id, name, slug FROM rockets ORDER BY name").fetchall()
    results = []
    for r in rockets:
        pts = db.execute(
            "SELECT c3, payload FROM c3_mass_curve_points WHERE rocket_id = ? ORDER BY c3",
            (r['id'],)
        ).fetchall()
        if not pts:
            continue
        curve = [dict(p) for p in pts]
        capacity = interpolate_payload(c3, curve)
        margin = capacity - payload_t
        results.append(dict(
            rocket=r['name'], slug=r['slug'],
            capacity=round(capacity, 3),
            margin=round(margin, 3),
            feasible=margin >= -0.001,
        ))
    results.sort(key=lambda x: -x['margin'])
    return results


def interpolate_payload_for_dv(target_dv, curve_points, leo_capacity=0):
    """Invert a remaining-dv curve and return the maximum payload."""
    if target_dv <= 0:
        return max(float(leo_capacity or 0), 0.0)
    pts = sorted(
        (
            {'m': float(p['m']), 'dv': float(p['dv'])}
            for p in curve_points
            if p['m'] is not None and p['dv'] is not None
        ),
        key=lambda p: p['m'],
    )
    if not pts or target_dv > pts[0]['dv']:
        return 0.0

    # The calibrated curve should decrease with payload.  Walk in payload
    # order so small local fit noise cannot select a second, unphysical root.
    for left, right in zip(pts, pts[1:]):
        if left['dv'] >= target_dv >= right['dv']:
            span = left['dv'] - right['dv']
            if abs(span) < 1e-9:
                return left['m']
            fraction = (left['dv'] - target_dv) / span
            return left['m'] + fraction * (right['m'] - left['m'])
    return 0.0


def find_rockets_for_orbit(required_dv, payload_t=None, required_post_parking_ignitions=1):
    """Rank vehicles for an orbit budget, including upper-stage restarts.

    The calibrated curve pays for getting to the initial parking orbit.  If a
    mission needs more than one burn afterwards, the highest stage must be
    restartable; otherwise the vehicle is reported but never marked feasible.
    """
    db = get_db()
    rockets = db.execute(
        "SELECT r.id, r.name, r.slug, cr.m_pl_max, cr.c3_max"
        " FROM rockets r"
        " JOIN computation_results cr ON cr.rocket_id = r.id"
        " ORDER BY r.name"
    ).fetchall()
    requested_payload = float(payload_t or 0)
    results = []
    for rocket in rockets:
        points = db.execute(
            "SELECT m, dv FROM dv_curve_points WHERE rocket_id = ? ORDER BY m",
            (rocket['id'],),
        ).fetchall()
        capacity = interpolate_payload_for_dv(
            float(required_dv),
            [dict(point) for point in points],
            rocket['m_pl_max'],
        )
        top_stage = db.execute(
            "SELECT multi_ignition FROM stages "
            "WHERE rocket_id = ? ORDER BY stage_index DESC LIMIT 1",
            (rocket['id'],),
        ).fetchone()
        supports_restart = bool(top_stage['multi_ignition']) if top_stage else False
        restart_required = int(required_post_parking_ignitions or 1) > 1
        margin = capacity - requested_payload if requested_payload > 0 else None
        dv_feasible = capacity > 0.002 and (
            requested_payload <= 0 or margin >= -0.001
        )
        hardware_feasible = not restart_required or supports_restart
        feasible = dv_feasible and hardware_feasible
        if not hardware_feasible:
            reason = '上面级不支持多次点火'
        elif capacity <= 0.002:
            reason = '目标 Δv 超出运力'
        elif requested_payload > 0 and margin < -0.001:
            reason = '指定载荷超过运力'
        else:
            reason = ''
        utilization = (
            requested_payload / capacity
            if requested_payload > 0 and capacity > 0
            else None
        )
        results.append(dict(
            rocket=rocket['name'],
            slug=rocket['slug'],
            capacity=round(capacity, 3),
            requested_payload=round(requested_payload, 3) if requested_payload > 0 else None,
            margin=round(margin, 3) if margin is not None else None,
            utilization=round(utilization, 4) if utilization is not None else None,
            dv_feasible=dv_feasible,
            supports_restart=supports_restart,
            restart_required=restart_required,
            feasible=feasible,
            reason=reason,
        ))

    if requested_payload > 0:
        # Best fit first: sufficient capacity with the least unused margin,
        # then the closest shortfall for infeasible vehicles.
        results.sort(key=lambda item: (
            0 if item['feasible'] else 1,
            item['margin'] if item['feasible'] else -(item['margin'] or 0),
            -item['capacity'],
        ))
    else:
        # Without a payload, keep restart-capable vehicles at the top for
        # multi-burn missions and rank each group by available capacity.
        results.sort(key=lambda item: (0 if item['feasible'] else 1, -item['capacity']))
    return results


# =============================================================================
# Query Engine (advanced filters)
# =============================================================================

def query_rockets(filters):
    """
    Build a dynamic parameterized SQL query from filters dict.

    Supported filters:
        search: str             — name substring match
        min_leo_payload: float  — minimum LEO payload (t)
        min_c3_0_payload: float — minimum C3=0 payload (t)
        min_payload_at_c3: float — requires c3_target
        c3_target: float         — target C3 for min_payload_at_c3
        engine_cfg: str          — engine filename substring
        stage_count_min: int     — minimum stage count
        stage_count_max: int     — maximum stage count
        sort_by: str             — column to sort by
        sort_dir: str            — 'asc' or 'desc'
    """
    db = get_db()

    sql = """
        SELECT r.id, r.name, r.slug,
               cr.m_pl_max, cr.c3_0_payload, cr.c3_max,
               (SELECT COUNT(*) FROM stages WHERE rocket_id = r.id) as stage_count,
               (SELECT GROUP_CONCAT(DISTINCT e.cfg_filename)
                FROM stages s
                JOIN stage_engines se ON se.stage_id = s.id
                JOIN engines e ON e.id = se.engine_id
                WHERE s.rocket_id = r.id) as engine_names
        FROM rockets r
        JOIN computation_results cr ON cr.rocket_id = r.id
        WHERE 1=1
    """
    params = []

    if filters.get('search'):
        sql += " AND r.name LIKE ?"
        params.append(f"%{filters['search']}%")

    if filters.get('min_leo_payload'):
        sql += " AND cr.m_pl_max >= ?"
        params.append(float(filters['min_leo_payload']))

    if filters.get('min_c3_0_payload'):
        sql += " AND cr.c3_0_payload >= ?"
        params.append(float(filters['min_c3_0_payload']))

    if filters.get('min_payload_at_c3') is not None and filters.get('c3_target') is not None:
        sql += """ AND (
            SELECT cp.payload FROM c3_mass_curve_points cp
            WHERE cp.rocket_id = r.id
            ORDER BY ABS(cp.c3 - ?) LIMIT 1
        ) >= ?"""
        params.extend([float(filters['c3_target']), float(filters['min_payload_at_c3'])])

    if filters.get('engine_cfg'):
        sql += """ AND EXISTS (
            SELECT 1 FROM stages s
            JOIN stage_engines se ON se.stage_id = s.id
            JOIN engines e ON e.id = se.engine_id
            WHERE s.rocket_id = r.id AND e.cfg_filename LIKE ?
        )"""
        params.append(f"%{filters['engine_cfg']}%")

    if filters.get('stage_count_min'):
        sql += " AND (SELECT COUNT(*) FROM stages WHERE rocket_id = r.id) >= ?"
        params.append(int(filters['stage_count_min']))

    if filters.get('stage_count_max'):
        sql += " AND (SELECT COUNT(*) FROM stages WHERE rocket_id = r.id) <= ?"
        params.append(int(filters['stage_count_max']))

    # Sort
    sort_col = filters.get('sort_by', 'm_pl_max')
    sort_dir = 'DESC' if filters.get('sort_dir', 'desc') == 'desc' else 'ASC'

    allowed = {
        'm_pl_max': 'cr.m_pl_max',
        'c3_0_payload': 'cr.c3_0_payload',
        'c3_max': 'cr.c3_max',
        'name': 'r.name',
        'stage_count': 'stage_count',
    }
    col_ref = allowed.get(sort_col, 'cr.m_pl_max')
    sql += f" ORDER BY {col_ref} {sort_dir}"

    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# =============================================================================
# Comparison
# =============================================================================

def get_compare_data(slugs):
    """Return full computed data for multiple rockets."""
    results = []
    for slug in slugs:
        data = get_computed_data(slug)
        if data:
            results.append(data)
    return results


# =============================================================================
# Stats
# =============================================================================

def get_stats():
    db = get_db()
    rocket_count = db.execute("SELECT COUNT(*) as n FROM rockets").fetchone()['n']
    engine_count = db.execute("SELECT COUNT(*) as n FROM engines").fetchone()['n']
    rocket_rows = db.execute(
        "SELECT r.name, cr.m_pl_max FROM rockets r"
        " JOIN computation_results cr ON cr.rocket_id = r.id"
        " ORDER BY cr.m_pl_max DESC"
    ).fetchall()
    best_leo = dict(rocket_rows[0]) if rocket_rows else None
    return {
        'rocket_count': rocket_count,
        'engine_count': engine_count,
        'best_leo': best_leo,
    }


def get_activity(limit=20):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM activity_log ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# =============================================================================
# Internal helpers
# =============================================================================

def _insert_related(db, rocket_id, cfg, result):
    """Insert stages, engines, calibration, missions, and computation results."""
    import json as _json
    db.execute('INSERT INTO rocket_config_evidence (rocket_id,config_json) VALUES (?,?) ON CONFLICT(rocket_id) DO UPDATE SET config_json=excluded.config_json',
               (rocket_id, _json.dumps(cfg, ensure_ascii=False, allow_nan=False)))

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
        db.execute(
            "INSERT INTO reference_missions (rocket_id, mission_key, label, c3, apoapsis_km, color) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rocket_id, key, m['label'], m.get('c3'), m.get('apoapsis_km'),
             m.get('color', '#888888'))
        )

    # Save computed data
    data_json = _json.dumps(result, ensure_ascii=False)
    loss = result.get('loss_model', {})
    db.execute(
        "INSERT INTO computation_results (rocket_id, m_pl_max, c3_max, c3_0_payload, c3_min,"
        " loss_l0, loss_l1, loss_l2, data_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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


def _delete_related(db, rocket_id):
    """Delete all related data for a rocket (except the rocket itself)."""
    db.execute("DELETE FROM c3_mass_curve_points WHERE rocket_id = ?", (rocket_id,))
    db.execute("DELETE FROM dv_curve_points WHERE rocket_id = ?", (rocket_id,))
    db.execute("DELETE FROM computation_results WHERE rocket_id = ?", (rocket_id,))
    db.execute("DELETE FROM reference_missions WHERE rocket_id = ?", (rocket_id,))
    db.execute("DELETE FROM calibration_points WHERE rocket_id = ?", (rocket_id,))
    # Stage engines cascade from stages, which cascade from rocket
    for stage in db.execute("SELECT id FROM stages WHERE rocket_id = ?", (rocket_id,)).fetchall():
        db.execute("DELETE FROM stage_engines WHERE stage_id = ?", (stage['id'],))
    db.execute("DELETE FROM stages WHERE rocket_id = ?", (rocket_id,))


def _log(db, action, entity, entity_id, details):
    db.execute(
        "INSERT INTO activity_log (action, entity, entity_id, details) VALUES (?, ?, ?, ?)",
        (action, entity, entity_id, str(details)[:500])
    )
