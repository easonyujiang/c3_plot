"""Read-only phase verification against the installed-engine curve."""
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flask import g
from server import create_app
from data_tools import audit, open_readonly


def main():
    out = ROOT / 'output/curve_accuracy_20261010'
    paths = [ROOT / 'c3_plot.db'] + [p for name in ['rockets', 'engines', 'VAB', 'experiments']
                                  for p in (ROOT / name).rglob('*') if p.is_file()]
    hashes = lambda: {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    before = hashes()
    data = json.loads((out / 'neutron_i_2630/curve.json').read_text(encoding='utf-8'))
    report = {'data_audit': audit(ROOT), 'missions': []}
    with patch('server.init_db'):
        app = create_app()
    db = open_readonly(ROOT)
    try:
        with app.app_context(), patch('server.models.get_computed_data', return_value=data):
            g.db = db
            client = app.test_client()
            for inclination, parking in [(0, 220), (180, 220), (0, 400)]:
                response = client.post('/api/mission-query', json={
                    'target_altitude_km': 35786, 'inclination_deg': inclination,
                    'parking_alt_km': parking, 'payload_t': 0.4, 'include_audit': True})
                result = response.get_json()
                (out / f'mission-{inclination}-{parking}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                detail = result['results'][0]['detailed_validation']
                evaluation = detail['audit']['requested_payload_evaluation']
                report['missions'].append({'inclination': inclination, 'parking_km': parking,
                                           'http': response.status_code, 'status': detail['verification_status'],
                                           'capacity': (detail['capacity'] or {}).get('max_payload_t'),
                                           'support': detail['calibration_support'],
                                           'recommendation': result['recommended'],
                                           'ascent_dv': evaluation['ascent_required_dv_m_s'] if evaluation else None})
            report['c3_zero_filter'] = client.post('/api/query', json={'min_payload_at_c3': 999999, 'c3_target': 0}).get_json()
            report['out_of_range'] = client.post('/api/find', json={'payload_t': 0.00345, 'c3': 1000}).get_json()
    finally:
        db.close()
    report['protected_files_unchanged'] = before == hashes()
    report['protected_file_count'] = len(paths)
    (out / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
