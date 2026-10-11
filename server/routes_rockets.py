"""
Rocket CRUD endpoints.
"""
import sys
import os
from flask import Blueprint, request, jsonify

bp = Blueprint('rockets', __name__)

# Ensure compute_c3_mass is importable from project root
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from compute_c3_mass import compute_rocket
from . import models
from .validation import validate_rocket

def _editor_source(cfg):
    identification=cfg.get('automatic_identification') or {}
    if identification.get('status')!='verified_editor_snapshot':
        return None
    source=identification['source']
    return {'filename':(cfg.get('source') or {}).get('filename','editor.craft'),
            'sha256':source['craft_sha256'], 'format':'ksp-craft',
            'metadata':{'automatic_identification':identification},'warnings':[]}


@bp.route('/api/rockets', methods=['GET'])
def list_rockets():
    return jsonify(models.list_rockets())


@bp.route('/api/rockets/<slug>', methods=['GET'])
def get_rocket(slug):
    cfg = models.get_rocket_config(slug)
    if cfg is None:
        return jsonify(error='Not found'), 404
    return jsonify(cfg)


@bp.route('/api/rockets', methods=['POST'])
def create_rocket():
    try:
        cfg = request.get_json(force=True)
    except Exception:
        return jsonify(error='Invalid JSON'), 400

    valid, errors = validate_rocket(cfg)
    if not valid:
        return jsonify(error='Validation failed', details=errors), 422

    name = cfg['rocket']['name']
    try:
        result = compute_rocket(cfg)
    except Exception as e:
        return jsonify(error=f'Computation failed: {e}'), 500

    try:
        slug = models.create_rocket(name, cfg, result, source=_editor_source(cfg))
    except Exception as e:
        return jsonify(error=f'Database error: {e}'), 500

    return jsonify(slug=slug, name=name, result=result), 201


@bp.route('/api/rockets/<slug>', methods=['PUT'])
def update_rocket(slug):
    try:
        cfg = request.get_json(force=True)
    except Exception:
        return jsonify(error='Invalid JSON'), 400

    valid, errors = validate_rocket(cfg)
    if not valid:
        return jsonify(error='Validation failed', details=errors), 422

    try:
        result = compute_rocket(cfg)
    except Exception as e:
        return jsonify(error=f'Computation failed: {e}'), 500

    new_slug = models.update_rocket(slug, cfg, result, source=_editor_source(cfg))
    if new_slug is None:
        return jsonify(error='Not found'), 404

    return jsonify(slug=new_slug, name=cfg['rocket']['name'], result=result)


@bp.route('/api/rockets/<slug>', methods=['DELETE'])
def delete_rocket(slug):
    if models.delete_rocket(slug):
        return jsonify(deleted=slug)
    return jsonify(error='Not found'), 404
