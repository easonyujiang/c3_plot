"""
Standard mission CRUD endpoints.
"""
import math
from flask import Blueprint, request, jsonify
from . import models

bp = Blueprint('missions', __name__)


def _validate_mission(data):
    if not isinstance(data, dict):
        return 'JSON body must be an object'
    label = data.get('label')
    if not isinstance(label, str) or not label.strip():
        return 'label is required'
    for field in ('c3', 'dv'):
        value = data.get(field)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)):
            return f'{field} must be a number'
    if data['dv'] < 0:
        return 'dv must be >= 0'
    group_name = data.get('group_name', 'Custom')
    if not isinstance(group_name, str) or not group_name.strip():
        return 'group_name must be a non-empty string'
    sort_order = data.get('sort_order', 0)
    if isinstance(sort_order, bool) or not isinstance(sort_order, int):
        return 'sort_order must be an integer'
    return None


@bp.route('/api/missions', methods=['GET'])
def list_missions():
    return jsonify(models.list_standard_missions())


@bp.route('/api/missions', methods=['POST'])
def create_mission():
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify(error='Invalid JSON'), 400
    error = _validate_mission(data)
    if error:
        return jsonify(error=error), 400
    try:
        mid = models.create_standard_mission(data)
    except Exception as exc:
        return jsonify(error=str(exc)), 409
    return jsonify(id=mid), 201


@bp.route('/api/missions/<int:mid>', methods=['PUT'])
def update_mission(mid):
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify(error='Invalid JSON'), 400
    error = _validate_mission(data)
    if error:
        return jsonify(error=error), 400
    if not models.update_standard_mission(mid, data):
        return jsonify(error='Not found'), 404
    return jsonify(ok=True)


@bp.route('/api/missions/<int:mid>', methods=['DELETE'])
def delete_mission(mid):
    if not models.delete_standard_mission(mid):
        return jsonify(error='Not found'), 404
    return jsonify(deleted=mid)
