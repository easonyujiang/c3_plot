"""
Engine endpoints.
"""
from flask import Blueprint, request, jsonify
from . import models

bp = Blueprint('engines', __name__)


@bp.route('/api/engines', methods=['GET'])
def list_engines():
    """Return list of engine cfg filenames (compatible with legacy API)."""
    return jsonify(models.get_engine_cfg_filenames())


@bp.route('/api/engines/detail', methods=['GET'])
def list_engines_detail():
    """Return full engine details."""
    return jsonify(models.list_engines())


@bp.route('/api/engines/reload', methods=['POST'])
def reload_engines():
    """Re-parse all .cfg files from engines/ directory."""
    try:
        engines = models.reload_engines()
        return jsonify(engines=engines, count=len(engines))
    except Exception as e:
        return jsonify(error=str(e)), 500
