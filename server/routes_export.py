"""
Export endpoints: CSV and JSON download.
"""
import csv
import io
import json
from flask import Blueprint, request, jsonify, Response
from . import models

bp = Blueprint('export', __name__)


@bp.route('/api/export/<slug>/csv', methods=['GET'])
def export_csv(slug):
    """Export C3-Mass curve + mission data as CSV."""
    data = models.get_computed_data(slug)
    if not data:
        return jsonify(error='Not found'), 404

    output = io.StringIO()
    writer = csv.writer(output)

    # Header info
    writer.writerow(['Rocket', data.get('name', slug)])
    writer.writerow(['Max LEO (t)', data.get('m_pl_max')])
    writer.writerow(['C3=0 Payload (t)', data.get('c3_0_payload')])
    writer.writerow(['Max C3', data.get('c3_max')])
    writer.writerow([])

    # C3-Mass curve
    writer.writerow(['C3 (km²/s²)', 'Payload (t)'])
    for pt in data.get('c3_mass_curve', []):
        writer.writerow([pt['c3'], pt['payload']])

    writer.writerow([])

    # Mission payloads
    writer.writerow(['Mission', 'C3', 'dv (m/s)', 'Max Payload (t)'])
    for key, m in data.get('missions', {}).items():
        writer.writerow([m.get('label', key), m.get('c3'), m.get('dv'),
                         m.get('payload', 'N/A')])

    writer.writerow([])

    # dv-Mass curve
    writer.writerow(['Payload Mass (t)', 'dv Remaining (m/s)'])
    for pt in data.get('dv_curve', []):
        writer.writerow([pt['m'], pt['dv']])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={slug}.csv'}
    )


@bp.route('/api/export/<slug>/json', methods=['GET'])
def export_json(slug):
    """Download the full computed data as a JSON file."""
    data = models.get_computed_data(slug)
    if not data:
        return jsonify(error='Not found'), 404

    return Response(
        json.dumps(data, ensure_ascii=False, indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename={slug}.json'}
    )
