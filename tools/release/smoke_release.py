"""Verify and extract a release, then launch it with a fresh database in a subprocess."""
import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from tools.paths import ROOT
PROBE = r'''
import json, re
from pathlib import Path
from server import create_app
client = create_app().test_client()
page = client.get('/')
assert page.status_code == 200
for asset in re.findall(r'(?:src|href)="([^"]+)"', page.get_data(as_text=True)):
    assert client.get(asset).status_code == 200, asset
assert client.get('/static/plotly.min.js').status_code == 200
rockets = client.get('/api/rockets').get_json()
assert len(rockets) == 1 and rockets[0]['slug'] == 'neutron_i_2630'
result = client.post('/api/mission-query', json={'orbit_type':'elliptical', 'periapsis_km':400,
    'apoapsis_km':40000, 'inclination_deg':89, 'payload_t':.7,
    'parking_alt_km':220, 'launch_lat':19.81, 'arrival_strategy':'combined'})
assert result.status_code == 200, result.get_json()
data = result.get_json()
candidate = next(r for r in data['results'] if r['slug'] == 'neutron_i_2630')
assert candidate['verified_feasible'] and 1.19 < candidate['verified_capacity'] < 1.21
assert candidate['detailed_validation']['calibration_support']['status'] == 'extrapolated'
assert Path('c3_plot.db').exists()
Path('smoke-result.json').write_text(json.dumps({'fresh_start':True, 'static_assets':True,
    'rocket_count':len(rockets), 'capacity_t':candidate['verified_capacity'],
    'mission':data['mission']}, indent=2), encoding='utf-8')
print('Fresh release startup and 700 kg mission passed.')
'''

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path,
                        help='Fresh extraction directory; defaults beside the archive')
    args = parser.parse_args()
    destination = args.output or args.archive.resolve().parent / 'smoke-unpacked'
    if destination.exists():
        raise SystemExit('Smoke destination already exists; preserve previous evidence.')
    with zipfile.ZipFile(args.archive) as archive:
        for item in archive.infolist():
            path = (destination / item.filename).resolve()
            if not path.is_relative_to(destination.resolve()):
                raise SystemExit('Unsafe archive path.')
        archive.extractall(destination)
    package = next(destination.iterdir())
    manifest = json.loads((package / 'RELEASE_MANIFEST.json').read_text())
    for name, expected in manifest['files'].items():
        assert hashlib.sha256((package / name).read_bytes()).hexdigest() == expected, name
    assert not (package / 'c3_plot.db').exists()
    dlls = list(package.rglob('*.dll'))
    assert len(dlls) == 1 and dlls[0].name == 'C3MassTelemetry.dll'
    import os
    environment = os.environ.copy()
    for key in ('C3_MASS_DB_PATH', 'C3_MASS_ROCKETS_DIR', 'C3_MASS_OUTPUT_DIR'):
        environment.pop(key, None)
    subprocess.run([sys.executable, '-c', PROBE], cwd=package, env=environment, check=True)
    print(json.dumps({'verified_files':len(manifest['files']), 'git_commit':manifest['git_commit']}))

if __name__ == '__main__':
    main()
