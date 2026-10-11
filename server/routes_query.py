"""
Advanced query endpoint.
"""
from flask import Blueprint, request, jsonify
from . import models

bp = Blueprint('query', __name__)


@bp.route('/api/query', methods=['POST'])
def query_rockets():
    """
    Filter/search rockets. Accepts JSON body with optional filters:
        search, min_leo_payload, min_c3_0_payload,
        min_payload_at_c3, c3_target, engine_cfg,
        stage_count_min, stage_count_max, sort_by, sort_dir
    """
    try:
        filters = request.get_json(force=True) or {}
    except Exception:
        return jsonify(error='Invalid JSON'), 400

    results = models.query_rockets(filters)
    return jsonify(count=len(results), results=results)


@bp.route('/api/stats', methods=['GET'])
def get_stats():
    """Dashboard-wide statistics."""
    stats = models.get_stats()
    activity = models.get_activity(limit=5)
    return jsonify(stats=stats, recent_activity=activity)


@bp.route('/api/activity', methods=['GET'])
def get_activity():
    """Recent activity feed."""
    limit = request.args.get('limit', 20, type=int)
    return jsonify(models.get_activity(limit=limit))
