"""
Multi-rocket comparison endpoint.
"""
from flask import Blueprint, request, jsonify
from . import models

bp = Blueprint('compare', __name__)


@bp.route('/api/compare', methods=['GET'])
def compare_rockets():
    """Return computed data for multiple rockets. Query: ?slugs=a,b,c"""
    slugs_param = request.args.get('slugs', '')
    if not slugs_param:
        return jsonify(error='Provide slugs parameter: ?slugs=a,b,c'), 400

    slugs = [s.strip() for s in slugs_param.split(',') if s.strip()]
    if not slugs:
        return jsonify(error='No valid slugs'), 400

    data = models.get_compare_data(slugs)
    return jsonify(rockets=data, count=len(data))
