"""Resolve fixed engine groups from final installed configuration evidence.

Instance-selected RealFuels/multimode configurations are rejected until a
runtime snapshot supplies the actual selection. No silent local-CFG fallback.
"""
import math
from pathlib import Path

from compute_c3_mass import _stage_engine_groups
from server.ksp_confignode import parse
from server.ksp_install_index import build_ksp_index

ROOT = Path(__file__).resolve().parent


def installed_engine_resolver(config, ksp_root):
    index = build_ksp_index(ksp_root)
    if index.status != 'ok':
        raise ValueError(f'Installed configuration unavailable: {index.status}')
    resolved = {}
    evidence = {'source': index.metadata, 'engines': {},
                'scope': 'fixed cfg engine groups; supplied masses and counts require independent confirmation'}
    for stage in config['stages']:
        for group in _stage_engine_groups(stage):
            filename = group['cfg']
            if filename in resolved:
                continue
            if Path(filename).name != filename:
                raise ValueError('Engine file must be a basename')
            tree = parse((ROOT / 'engines' / filename).read_text(encoding='utf-8-sig'))
            definitions = tree.children_named('PART')
            if len(definitions) != 1:
                raise ValueError(f'{filename}: expected one local part definition')
            name = definitions[0].value('name')
            matches = index.find_parts(name)
            if len(matches) != 1:
                raise ValueError(f'{filename}: installed part missing or ambiguous: {name}')
            part = matches[0]
            if 'ModuleEngineConfigs' in part['modules'] or 'MultiModeEngine' in part['modules']:
                raise ValueError(f'{name}: runtime engine configuration selection required')
            engines = part['engines']
            if len(engines) != 1:
                raise ValueError(f'{name}: multiple engine modules require runtime selection')
            engine = engines[0]
            keys = [tuple(float(v) for v in row.split()[:2]) for row in engine['atmosphere_curve_keys']]
            vacuum = [isp for pressure, isp in keys if abs(pressure) < 1e-9]
            sea = [isp for pressure, isp in keys if abs(pressure - 1) < 1e-9]
            thrust = float(engine['max_thrust'])
            if len(vacuum) != 1 or not math.isfinite(thrust) or thrust <= 0 or not math.isfinite(vacuum[0]) or vacuum[0] <= 0:
                raise ValueError(f'{name}: invalid vacuum engine performance')
            resolved[filename] = {'thrust': thrust, 'isp_vac': vacuum[0],
                                  'isp_sl': sea[0] if len(sea) == 1 else None}
            evidence['engines'][filename] = {'part': name, 'parent_url': part['parent_url'],
                                            'performance': resolved[filename], 'engine': engine,
                                            'cluster_count_source': 'rocket configuration; thrust scaling verified against SSTU source'}

    def resolve(stage):
        groups = [(resolved[g['cfg']], g.get('count', 1)) for g in _stage_engine_groups(stage)]
        if any(isinstance(count, bool) or not isinstance(count, int) or count < 1 for _, count in groups):
            raise ValueError('Engine counts must be positive integers')
        thrust = sum(ep['thrust'] * count for ep, count in groups)
        return {'thrust': thrust,
                'isp_vac': thrust / sum(ep['thrust'] * count / ep['isp_vac'] for ep, count in groups),
                'isp_sl': thrust / sum(ep['thrust'] * count / (ep['isp_sl'] or ep['isp_vac']) for ep, count in groups),
                'isp_type': 'installed_config'}

    return resolve, evidence
