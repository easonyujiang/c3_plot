"""Compare actual pad mass/engine performance with a supplied rocket model.

Does not assign stage masses or change calibration: clamp exclusion and payload
identity are reported explicitly, not hidden in a best-fit correction.
"""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from compute_c3_mass import _stage_engine_groups
from server.ksp_confignode import parse
from server.yaml_codec import load_config


def compare(snapshot, config_path, engine_evidence, payload):
    raw = snapshot.read_bytes()
    tree = parse(raw.decode('utf-8-sig'))
    node = tree.child('C3_RUNTIME_SNAPSHOT') or tree
    if node.value('schema_version') != '2':
        raise ValueError('Require runtime snapshot schema 2')
    config = load_config(config_path.read_text(encoding='utf-8-sig'))
    installed = json.loads(engine_evidence.read_text(encoding='utf-8'))['installed_engines']['engines']
    parts = node.children_named('PART')
    masses = [float(p.value('runtime_part_mass_t')) for p in parts]
    if not parts or not all(math.isfinite(m) and m >= 0 for m in masses):
        raise ValueError('Invalid runtime parts/masses')
    clamps = [p for p in parts if 'LaunchClamp' in [n.value('name') for n in p.children_named('MODULE_PRESENT')]]
    clamp_mass = sum(float(p.value('runtime_part_mass_t')) for p in clamps)
    runtime_mass = float(node.value('vessel_mass_t'))
    model_mass = sum(s['wet_mass_t'] for s in config['stages']) + payload
    checks = []
    for stage_number, stage in enumerate(config['stages'], 1):
        expected = []
        for group in _stage_engine_groups(stage):
            info = installed[group['cfg']]
            expected.append((info, group['count']))
        for info, count in expected:
            matching = [p for p in parts if p.value('name') == info['part']]
            engines = [en for p in matching for en in p.children_named('ENGINE')]
            actual_thrust = sum(float(en.value('max_thrust_kN')) for en in engines)
            desired = info['performance']['thrust'] * count
            checks.append({'stage': stage_number, 'part': info['part'], 'expected_count': count,
                'runtime_engine_modules': len(engines), 'expected_total_thrust_kN': desired,
                'actual_total_thrust_kN': actual_thrust, 'thrust_matches': abs(actual_thrust-desired) <= 0.01,
                'expected_vacuum_isp_s': info['performance']['isp_vac'],
                'actual_vacuum_isp_s': [float(en.value('vacuum_isp_curve_s')) for en in engines],
                'isp_matches': bool(engines) and all(abs(float(en.value('vacuum_isp_curve_s'))-info['performance']['isp_vac']) <= 0.01 for en in engines),
                'thrust_limiter_percent': [float(en.value('thrust_limiter_percent')) for en in engines],
                'inverse_stages': [int(p.value('inverse_stage')) for p in matching]})
    return {'snapshot': str(snapshot.resolve()), 'sha256': hashlib.sha256(raw).hexdigest(),
        'vessel': node.value('vessel_name'), 'runtime_mass_t': runtime_mass,
        'part_sum_t': sum(masses), 'launch_clamp_mass_t': clamp_mass,
        'runtime_mass_excluding_clamps_t': runtime_mass-clamp_mass,
        'declared_payload_t': payload, 'payload_identity_verified': False,
        'model_total_wet_mass_t': model_mass, 'mass_difference_excluding_clamps_t': runtime_mass-clamp_mass-model_mass,
        'engine_checks': checks, 'parameters_changed': False,
        'limitation': 'Does not establish payload identity, stage dry masses, propellant accessibility, flight strategy or curve accuracy.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--engine-evidence', required=True, type=Path)
    parser.add_argument('--payload', required=True, type=float)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not math.isfinite(args.payload) or args.payload < 0:
        parser.error('Payload must be finite and nonnegative')
    if args.output.resolve() == args.snapshot.resolve():
        parser.error('Preserve actual snapshot')
    result = compare(args.snapshot, args.config, args.engine_evidence, args.payload)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
