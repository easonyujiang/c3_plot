"""
Import endpoints: YAML upload, YAML paste.
"""
import sys
import os
import sqlite3
from flask import Blueprint, request, jsonify

bp = Blueprint('import', __name__)

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from compute_c3_mass import compute_rocket
from . import models
from . import import_service
from .validation import validate_rocket
from .yaml_codec import load_config
from .craft_library import configured_ksp_root, list_crafts, read_craft


@bp.route('/api/import/library', methods=['GET'])
def saved_craft_library():
    root = configured_ksp_root()
    if root is None:
        return jsonify(items=[], warnings=['未找到 KSP 安装，请设置 C3_MASS_KSP_ROOT'],
                       available=False, truncated=False)
    try:
        return jsonify(**list_crafts(root, include_validation=os.environ.get('C3_MASS_INCLUDE_VALIDATION_CRAFTS')=='1'), available=True)
    except OSError as exc:
        return jsonify(error=f'读取火箭列表失败: {exc}'), 400


@bp.route('/api/import/library/preview', methods=['POST'])
def preview_saved_craft():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get('sha256'), str):
        return jsonify(error='缺少文件标识及版本，请刷新列表'), 400
    root = configured_ksp_root()
    if root is None:
        return jsonify(error='未找到 KSP 安装'), 400
    try:
        filename, content, loadmeta, digest = read_craft(
            root, body.get('id'), body['sha256'])
    except (OSError, ValueError, UnicodeError) as exc:
        return jsonify(error=str(exc)), 409
    item = import_service.preview_craft(filename, content, loadmeta)
    if item.get('status') == 'error':
        return jsonify(item), 400
    from compute_c3_mass import compute_orbit_mission, MU, R_EARTH
    import math
    benchmarks = [
        {'payload_t':0, 'orbit_alt_km':220, 'dv_rem_m_s':0, 'benchmark':'220 km 圆轨道'},
        {'payload_t':0, 'orbit_alt_km':220, 'dv_rem_m_s':compute_orbit_mission(35786,19.81,orbit_type='transfer')['dv_orbit'], 'benchmark':'220 × 35786 km 转移轨道'},
        {'payload_t':0, 'orbit_alt_km':220, 'dv_rem_m_s':(math.sqrt(2)-1)*math.sqrt(MU/(R_EARTH+220000)), 'benchmark':'地球逃逸 C3 = 0'},
    ]
    return jsonify(draft=item['config'], status=item['status'], calibration_templates=benchmarks,
                   sha256=digest, warnings=item.get('warnings') or [],
                   structured_warnings=item.get('structured_warnings') or [])


@bp.route('/api/import/yaml', methods=['POST'])
def import_yaml_files():
    """Upload one or more .yaml files via multipart/form-data. Computes and saves each."""
    if 'files' not in request.files:
        return jsonify(error='No files uploaded. Use field name "files"'), 400

    files = request.files.getlist('files')
    if not files:
        return jsonify(error='No files provided'), 400

    results = []
    errors = []
    for f in files:
        if not f.filename or not f.filename.endswith(('.yaml', '.yml')):
            errors.append(dict(file=f.filename, error='Not a .yaml file'))
            continue
        try:
            content = f.read().decode('utf-8')
            cfg = load_config(content)
        except Exception as e:
            errors.append(dict(file=f.filename, error=f'YAML parse error: {e}'))
            continue

        valid, errs = validate_rocket(cfg)
        if not valid:
            errors.append(dict(file=f.filename, error='Validation failed', details=errs))
            continue

        name = cfg['rocket']['name']
        try:
            result = compute_rocket(cfg)
        except Exception as e:
            errors.append(dict(file=f.filename, name=name, error=f'Computation failed: {e}'))
            continue

        try:
            slug = models.create_rocket(name, cfg, result)
        except sqlite3.IntegrityError:
            # If slug/name exists, update instead
            slug = models.slugify(name)
            if models.rocket_exists(slug):
                models.update_rocket(slug, cfg, result)
                results.append(dict(file=f.filename, name=name, slug=slug, action='updated'))
                continue
            errors.append(dict(file=f.filename, name=name, error='Rocket name or slug already exists'))
            continue
        except Exception as e:
            errors.append(dict(file=f.filename, name=name, error=str(e)))
            continue

        results.append(dict(file=f.filename, name=name, slug=slug, action='created'))

    return jsonify(
        imported=len(results),
        errors=len(errors),
        results=results,
        error_details=errors,
    ), 201 if results else 200


@bp.route('/api/import/paste', methods=['POST'])
def import_yaml_paste():
    """Accept YAML text in JSON body and create a rocket from it."""
    try:
        body = request.get_json(force=True)
    except Exception:
        return jsonify(error='Invalid JSON'), 400

    if not isinstance(body, dict):
        return jsonify(error='JSON body must be an object'), 400
    yaml_text = body.get('yaml_text', '')
    if not isinstance(yaml_text, str):
        return jsonify(error='yaml_text must be a string'), 400
    if not yaml_text.strip():
        return jsonify(error='No YAML text provided'), 400

    try:
        cfg = load_config(yaml_text)
    except Exception as e:
        return jsonify(error=f'YAML parse error: {e}'), 400

    valid, errors = validate_rocket(cfg)
    if not valid:
        return jsonify(error='Validation failed', details=errors), 422

    name = cfg['rocket']['name']
    try:
        result = compute_rocket(cfg)
    except Exception as e:
        return jsonify(error=f'Computation failed: {e}'), 500

    slug = models.slugify(name)
    try:
        if models.rocket_exists(slug):
            models.update_rocket(slug, cfg, result)
            action = 'updated'
        else:
            slug = models.create_rocket(name, cfg, result)
            action = 'created'
    except sqlite3.IntegrityError:
        return jsonify(error='Rocket name or slug already exists'), 409
    except Exception as e:
        return jsonify(error=f'Database error: {e}'), 500

    return jsonify(slug=slug, name=name, action=action, result=result), 201


def _error_body(code, message):
    """Unified single-file preview error payload."""
    return {
        'error': message,
        'error_code': code,
        'line': None,
        'column': None,
        'node_path': None,
    }


@bp.route('/api/import/vab/preview', methods=['POST'])
def preview_vab():
    """Parse a KSP VAB craft into an editable draft; never writes to SQL.

    Invalid craft (size/encoding/ConfigNode errors, no top-level PART)
    returns HTTP 400 with the unified error payload; valid craft returns
    ``status=needs_review`` / ``action=review`` until mass is confirmed.
    """
    if 'file' not in request.files:
        return jsonify(_error_body('missing_file', '请上传 .craft 文件')), 400
    uploaded = request.files['file']
    if not uploaded.filename or not uploaded.filename.lower().endswith('.craft'):
        return jsonify(_error_body('unsupported_format', '只支持 .craft 文件')), 400
    raw = uploaded.read(import_service.MAX_FILE_BYTES + 1)
    if len(raw) > import_service.MAX_FILE_BYTES:
        return jsonify(_error_body(
            'file_too_large',
            f'文件超过 {import_service.MAX_FILE_BYTES // 1024 // 1024} MB 限制')), 400
    item = import_service.preview_craft(uploaded.filename, raw)
    if item.get('status') == 'error':
        body = {key: item.get(key) for key in
                ('error', 'error_code', 'line', 'column', 'node_path')}
        return jsonify(body), 400
    return jsonify(
        draft=item['config'],
        requires_confirmation=True,
        status=item['status'],
        action=item['action'],
        warnings=item.get('warnings') or [],
        structured_warnings=item.get('structured_warnings') or [],
    )


@bp.route('/api/import/vab/confirm', methods=['POST'])
def confirm_vab():
    """Save a user-reviewed VAB draft through the normal YAML pipeline."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error='JSON body must be an object'), 400
    cfg = body.get('config')
    if not isinstance(cfg, dict):
        return jsonify(error='缺少 config 草稿'), 400
    valid, errors = validate_rocket(cfg)
    if not valid:
        return jsonify(error='草稿仍需补全', details=errors), 422
    try:
        name = cfg['rocket']['name']
        result = compute_rocket(cfg)
        slug = models.slugify(name)
        if models.rocket_exists(slug):
            models.update_rocket(slug, cfg, result)
            action = 'updated'
        else:
            slug = models.create_rocket(name, cfg, result)
            action = 'created'
    except Exception as exc:
        return jsonify(error=f'保存 VAB 草稿失败: {exc}'), 500
    return jsonify(slug=slug, name=name, action=action, result=result), 201


@bp.route('/api/import/batch/preview', methods=['POST'])
def preview_batch_import():
    """Preview a mixed YAML/.craft selection without mutating the database."""
    files = request.files.getlist('files')
    if not files:
        return jsonify(error='请至少选择一个 YAML 或 .craft 文件'), 400
    try:
        return jsonify(import_service.preview_batch(files))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception as exc:
        return jsonify(error=f'批量预览失败: {exc}'), 500


@bp.route('/api/import/batch/confirm', methods=['POST'])
def confirm_batch_import():
    """Commit approved items from a previous batch preview."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error='JSON body must be an object'), 400
    items = body.get('items')
    if not isinstance(items, list):
        return jsonify(error='缺少 items 列表'), 400
    allow_updates = body.get('allow_updates', False)
    if not isinstance(allow_updates, bool):
        return jsonify(error='allow_updates must be boolean'), 400
    result = import_service.confirm_batch(items, allow_updates=allow_updates)
    return jsonify(result), 201 if result['imported'] else 200
