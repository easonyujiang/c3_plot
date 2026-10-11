"""Audit a passive game telemetry session without changing calibration or saves."""
import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.ksp_confignode import parse


def number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Non-finite telemetry value')
    return value


def analyze(session):
    session = Path(session)
    with session.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    if not rows or 'orbit_speed_m_s' not in rows[0]:
        raise ValueError('Require a nonempty schema 2 flight session')
    # Read historical workspace archives, but new game snapshots use an
    # extension which cannot be loaded as a KSP part configuration.
    snapshots = sorted(path for extension in ('.c3snapshot', '.cfg')
                       for path in session.parent.glob(session.stem + '_*' + extension))
    if not snapshots:
        raise ValueError('Runtime snapshots missing; cannot independently check mass or C3')
    bodies, mass_checks, fingerprints = {}, [], []
    for path in snapshots:
        raw = path.read_bytes()
        tree = parse(raw.decode('utf-8-sig'))
        # KSP ConfigNode.Save writes the node's contents without its own name.
        # Also accept the explicit wrapper used by archived/exported snapshots.
        node = tree.child('C3_RUNTIME_SNAPSHOT') or tree
        if node is None or node.value('schema_version') != '2':
            raise ValueError('Unexpected snapshot schema: ' + str(path))
        body = node.value('body')
        parameters = (number(node.value('body_radius_m')), number(node.value('body_mu_m3_s2')))
        if min(parameters) <= 0 or (body in bodies and bodies[body] != parameters):
            raise ValueError('Invalid or changing body parameters')
        bodies[body] = parameters
        mass = number(node.value('vessel_mass_t'))
        parts = node.children_named('PART')
        if mass <= 0 or not parts:
            raise ValueError('Snapshot has no positive vessel mass or parts')
        part_sum = sum(number(p.value('runtime_part_mass_t')) for p in parts)
        delta = part_sum - mass
        mass_checks.append({'ut_s': number(node.value('ut_s')), 'mass_t': mass,
                            'part_sum_t': part_sum, 'error_t': delta,
                            'ok': abs(delta) <= max(1e-5, mass * 2e-7)})
        fingerprints.append({'path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest()})
    energy_errors, stages, parking = [], [], []
    previous_ut, previous_stage = None, None
    for row in rows:
        ut = number(row['ut_s'])
        if previous_ut is not None and ut <= previous_ut:
            raise ValueError('UT must increase; split sessions at time reversal')
        previous_ut = ut
        radius, mu = bodies[row['body']]
        distance = radius + number(row['altitude_m'])
        if distance <= 0 or number(row['mass_t']) <= 0:
            raise ValueError('Invalid radius or mass')
        expected = (number(row['orbit_speed_m_s']) ** 2 - 2 * mu / distance) / 1e6
        energy_errors.append(abs(expected - number(row['c3_km2_s2'])))
        if row['stage'] != previous_stage:
            stages.append({'ut_s': ut, 'stage': int(row['stage']), 'mass_t': number(row['mass_t'])})
            previous_stage = row['stage']
        # Candidate only: a coast near the reference orbit is not evidence of
        # payload identity, remaining delta-v, optimal ascent or full capacity.
        if (row['body'] == 'Earth' and row['situation'] == 'ORBITING'
                and abs(number(row['periapsis_m']) - 220000) <= 2000
                and abs(number(row['apoapsis_m']) - 220000) <= 2000
                and number(row['thrust_kN']) <= 1e-6):
            parking.append({'ut_s': ut, 'mass_t': number(row['mass_t']),
                            'inclination_deg': number(row['inclination_deg'])})
    return {'status': 'runtime_invariants_passed' if all(p['ok'] for p in mass_checks)
            and max(energy_errors) <= 1e-5 else 'runtime_invariants_failed',
            'session': str(session.resolve()), 'session_sha256': hashlib.sha256(session.read_bytes()).hexdigest(),
            'samples': len(rows), 'mass_checks': mass_checks, 'max_c3_error_km2_s2': max(energy_errors),
            'stage_events': stages, 'reference_orbit_coast_candidates': parking,
            'snapshots': fingerprints, 'calibration_updated': False,
            'limitation': 'Checks logged game quantities only; does not validate the payload curve or infer usable remaining propellant.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.resolve() == args.session.resolve():
        parser.error('Output must not overwrite the flight session')
    result = analyze(args.session)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(result['status'])
    sys.exit(0 if result['status'] == 'runtime_invariants_passed' else 1)
