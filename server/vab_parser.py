"""
KSP .craft structural reader — dependency-free, deterministic, read-only.
==========================================================================

Reads a .craft file into STRUCTURAL EVIDENCE using the ConfigNode parser
(server/ksp_confignode.py).  It deliberately does NOT solve the final
physics:

    * ``modMass`` is only a save-time mass delta, NOT the part's wet mass;
    * no fixed 18% dry-mass ratio is fabricated;
    * ``istg`` grouping is kept only as legacy placeholder stages and does
      NOT represent a confirmed physical staging stack;
    * SSTU clusters, MultiModeEngine modes and symmetry references are
      recorded as evidence with explicit sources and confidence — they are
      never double-counted and never multiplied by ``sym``.

The returned draft keeps the legacy ``stages`` / ``warnings`` keys so the
existing preview UI keeps working, but every craft draft is flagged with
``requires_mass_confirmation = True`` and must enter ``needs_review``.
Structured warnings follow the stable shape:

    {"code", "severity", "message", "part_name", "part_id", "module",
     "field", "impact", "details"}
"""

from __future__ import annotations

import math
from typing import List, Optional

from .ksp_confignode import (
    ConfigNode,
    CraftParseError,
    parse as parse_config,
)

# Built-in SSTU layout count fallback.  These are LOW-CONFIDENCE evidence:
# the authoritative counts live in the user's GameData install (phase 6B).
_LAYOUT_COUNTS = {
    'Single': 1,
    'Twin': 2,
    'Triple': 3,
    'Quad': 4,
    'SixRadial': 6,
    'SixCenter': 6,
}


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _finite_number(value):
    """Parse a value as a finite float; None when absent or unparseable.

    NaN and infinity are treated as unparseable (never silently kept).
    """
    if value is None or value == '':
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _int_or_none(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _split_part_name(part_full):
    """Split 'Name_12345' into ('Name', '12345').  Instance ids are numeric
    suffixes; part names may themselves contain digits."""
    if part_full.endswith('_'):
        return part_full[:-1], ''
    head, sep, tail = part_full.rpartition('_')
    if sep and tail.isdigit():
        return head, tail
    return part_full, ''


def _engine_cfg(part_name):
    """Convert a base part name into a local cfg filename hint."""
    base = part_name
    if base.startswith('SSTU-'):
        base = base[5:]
    return base + '.cfg' if base else ''


def _module_name(module_node):
    return module_node.value('name', '')


def _module_engines(part_node):
    engines = []
    for module in part_node.children_named('MODULE'):
        name = _module_name(module)
        # ModuleEngines / ModuleEnginesFX / ModuleEnginesRF (RealFuels)
        if name.startswith('ModuleEngines'):
            engines.append(module)
    return engines


def _propellants(engine_modules):
    propellants = []
    for module in engine_modules:
        for prop in module.children_named('PROPELLANT'):
            propellants.append({
                'name': prop.value('name'),
                'ratio': prop.value('ratio'),
            })
    return propellants


def _structured_warning(code, message, *, severity='warning', part_name=None,
                        part_id=None, module=None, field=None, impact='',
                        details=None):
    return {
        'code': code,
        'severity': severity,
        'message': message,
        'part_name': part_name,
        'part_id': part_id,
        'module': module,
        'field': field,
        'impact': impact,
        'details': details or {},
    }


def _extract_engine_evidence(part_node, base_part, structured, legacy):
    """Engine evidence for one PART.

    Deduplication rules:
    * a part with SSTUModularEngineCluster counts its layout once; the
      ModuleEnginesFX inside the same part is the cluster implementation
      and is NOT counted again;
    * a part with MultiModeEngine counts ONE engine whose primary/secondary
      modes switch — never two simultaneous engines;
    * ``sym`` references are never multiplied: KSP saves every actual PART.
    """
    modules = {}
    for module in part_node.children_named('MODULE'):
        name = _module_name(module)
        modules.setdefault(name, []).append(module)

    engine_modules = _module_engines(part_node)
    engine_ids = [m.value('engineID') or 'default' for m in engine_modules]
    sstu_modules = modules.get('SSTUModularEngineCluster', [])
    multimode_modules = modules.get('MultiModeEngine', [])

    part_id = part_node.value('persistentId') or part_node.value('_id')

    if sstu_modules:
        cluster = sstu_modules[0]
        layout = (cluster.value('currentEngineLayoutName')
                  or part_node.value('currentEngineLayoutName'))
        engine_model = cluster.value('engineModelName')
        layout_count = _LAYOUT_COUNTS.get(layout, 1)
        if layout and layout not in _LAYOUT_COUNTS:
            structured.append(_structured_warning(
                'sstu_layout_unknown',
                f'未识别 SSTU 布局 {layout}，暂按 1 台计数',
                part_name=base_part, part_id=part_id,
                module='SSTUModularEngineCluster',
                field='currentEngineLayoutName',
                impact='发动机台数可能被低估',
                details={'layout': layout}))
            legacy.append(
                f'{base_part}: 未识别 SSTU 布局 {layout}，暂按 1 台计数')
        structured.append(_structured_warning(
            'sstu_layout_low_confidence',
            '布局台数来自内置回退表，未与用户 GameData 安装核对',
            part_name=base_part, part_id=part_id,
            module='SSTUModularEngineCluster',
            field='currentEngineLayoutName',
            impact='发动机台数置信度低，需在 6B 阶段以安装配置为准',
            details={'layout': layout, 'count': layout_count}))
        return {
            'kind': 'sstu_cluster',
            'count': layout_count,
            'count_source': 'sstu_layout',
            'layout': layout,
            'engine_model': engine_model,
            'layout_confidence': 'low',
            'engine_ids': engine_ids,
            'propellants': _propellants(engine_modules),
        }

    if multimode_modules:
        multimode = multimode_modules[0]
        primary = multimode.value('primaryEngineID')
        secondary = multimode.value('secondaryEngineID')
        structured.append(_structured_warning(
            'multimode_single_engine',
            'MultiModeEngine 主/副模式是同一台发动机的模式切换，不按两台'
            '同时工作的发动机计数',
            part_name=base_part, part_id=part_id,
            module='MultiModeEngine',
            field='primaryEngineID',
            impact='单台发动机，模式之间互斥',
            details={'primary': primary, 'secondary': secondary}))
        return {
            'kind': 'multimode',
            'count': 1,
            'count_source': 'multimode_engine',
            'modes': {'primary': primary, 'secondary': secondary},
            'engine_ids': engine_ids,
            'propellants': _propellants(engine_modules),
        }

    if engine_modules:
        real_fuels = any(_module_name(m) == 'ModuleEnginesRF'
                         for m in engine_modules)
        if real_fuels:
            structured.append(_structured_warning(
                'engine_rf_config_unconfirmed',
                'ModuleEnginesRF（RealFuels）的推力/比冲配置位于 GameData 部件'
                '配置中，本阶段无法确认发动机映射',
                part_name=base_part, part_id=part_id,
                module='ModuleEnginesRF',
                field='engineID',
                impact='发动机 cfg 映射需在 6B 阶段结合安装配置确认'))
        return {
            'kind': 'single',
            'count': 1,
            'count_source': 'module_engine',
            'real_fuels': real_fuels,
            'engine_ids': engine_ids,
            'propellants': _propellants(engine_modules),
        }

    return None


def _bool_value(value):
    """Parse a ConfigNode boolean (True/False/0/1); None when ambiguous."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    lowered = str(value).strip().casefold()
    if lowered in ('true', '1'):
        return True
    if lowered in ('false', '0'):
        return False
    return None


_CHUTE_DESIGN_FIELDS = (
    ('calcSelect', 'calc_select', 'bool'),
    ('getMass', 'get_mass', 'bool'),
    ('useDry', 'use_dry', 'bool'),
    ('mass', 'mass', 'number'),
    ('chuteCount', 'chute_count', 'number'),
    ('preDepDiam', 'pre_dep_diam', 'number'),
    ('depDiam', 'dep_diam', 'number'),
    ('isPressure', 'is_pressure', 'bool'),
    ('predepClause', 'predep_clause', 'number'),
    ('refDepAlt', 'ref_dep_alt', 'number'),
    ('deploymentAlt', 'deployment_alt', 'number'),
    ('cutAlt', 'cut_alt', 'number'),
    ('preDepSpeed', 'pre_dep_speed', 'number'),
    ('depSpeed', 'dep_speed', 'number'),
    ('landingSpeed', 'landing_speed', 'number'),
    ('deceleration', 'deceleration', 'number'),
)

_PARACHUTE_FIELDS = (
    ('material', 'material', 'raw'),
    ('preDeployedDiameter', 'pre_deployed_diameter', 'number'),
    ('deployedDiameter', 'deployed_diameter', 'number'),
    ('minIsPressure', 'min_is_pressure', 'bool'),
    ('minDeployment', 'min_deployment', 'number'),
    ('minPressure', 'min_pressure', 'number'),
    ('deploymentAlt', 'deployment_alt', 'number'),
    ('cutAlt', 'cut_alt', 'number'),
    ('preDeploymentSpeed', 'pre_deployment_speed', 'number'),
    ('deploymentSpeed', 'deployment_speed', 'number'),
    ('canopyCount', 'canopy_count', 'number'),
)


def _chute_entry(chute):
    entry = {}
    for raw_key, key, kind in _CHUTE_DESIGN_FIELDS:
        raw = chute.value(raw_key)
        entry[f'{key}_raw'] = raw
        if kind == 'bool':
            entry[key] = _bool_value(raw)
        else:
            entry[key] = _finite_number(raw)
    return entry


def _module_level_chute(module):
    """ProceduralChute state persisted as module-level values."""
    entry = {}
    for raw_key, key, kind in _CHUTE_DESIGN_FIELDS:
        raw = module.value(raw_key)
        entry[f'{key}_raw'] = raw
        if kind == 'bool':
            entry[key] = _bool_value(raw)
        else:
            entry[key] = _finite_number(raw)
    if any(entry.get(f'{key}_raw') is not None
           for _, key, _ in _CHUTE_DESIGN_FIELDS):
        return {'chutes': [entry]}
    return {'chutes': []}


def _parachute_entry(parachute):
    entry = {}
    for raw_key, key, kind in _PARACHUTE_FIELDS:
        raw = parachute.value(raw_key)
        entry[f'{key}_raw'] = raw
        if kind == 'bool':
            entry[key] = _bool_value(raw)
        elif kind == 'number':
            entry[key] = _finite_number(raw)
        else:
            entry[key] = raw
    return entry


def _extract_module_state(part_node):
    """Instance state of known mass-relevant modules (raw + parsed).

    Only the modules relevant to mass forensics are captured (6B.3C.1):
    SSTUProceduralDecoupler, RealChuteModule and ProceduralChute.  Missing
    fields stay missing; invalid values stay None.
    """
    evidence = {}
    for module in part_node.children_named('MODULE'):
        module_name = _module_name(module)
        if module_name == 'SSTUProceduralDecoupler':
            entry = {}
            for field in ('diameter', 'height', 'thickness'):
                raw = module.value(field)
                entry[f'{field}_raw'] = raw
                entry[field] = _finite_number(raw)
            evidence.setdefault('SSTUProceduralDecoupler', []).append(entry)
        elif module_name == 'RealChuteModule':
            case_raw = module.value('caseMass')
            entry = {
                'case_mass_raw': case_raw,
                'case_mass': _finite_number(case_raw),
                'spare_chutes_raw': module.value('spareChutes'),
                'spare_chutes': _finite_number(module.value('spareChutes')),
                'parachutes': [
                    _parachute_entry(parachute)
                    for parachute in module.children_named('PARACHUTE')
                ],
            }
            evidence.setdefault('RealChuteModule', []).append(entry)
        elif module_name == 'ProceduralChute':
            chutes = [
                _chute_entry(chute)
                for chute in module.children_named('CHUTE')
            ]
            if chutes:
                evidence.setdefault('ProceduralChute', []).append(
                    {'chutes': chutes})
            else:
                # KSP can persist ProceduralChute state as module-level
                # values (newer RealChute save format)
                evidence.setdefault('ProceduralChute', []).append(
                    _module_level_chute(module))
        elif module_name == 'ModuleFuelTanks':
            entry = {
                'mass_raw': module.value('mass'),
                'mass': _finite_number(module.value('mass')),
                'volume_raw': module.value('volume'),
                'volume': _finite_number(module.value('volume')),
                'basemass_raw': module.value('basemass'),
                'basemass': _finite_number(module.value('basemass')),
                'mass_is_additive_raw': module.value('massIsAdditive'),
                'mass_is_additive': _bool_value(
                    module.value('massIsAdditive')),
                'type_raw': module.value('type'),
                'type': module.value('type'),
                'number_of_added_mlilayers_raw': module.value(
                    '_numberOfAddedMLILayers'),
                'number_of_added_mlilayers': _finite_number(
                    module.value('_numberOfAddedMLILayers')),
            }
            evidence.setdefault('ModuleFuelTanks', []).append(entry)
    return evidence


def _extract_part_evidence(part_node, index, structured, legacy):
    """Structural evidence for one top-level PART node."""
    part_full = part_node.value('part', '')
    base_part, instance_id = _split_part_name(part_full)
    part_id = part_node.value('persistentId') or part_node.value('_id')

    mod_mass_raw = part_node.value('modMass')
    mod_mass_present = mod_mass_raw is not None
    mod_mass_t = _finite_number(mod_mass_raw)
    if mod_mass_present and mod_mass_t is None:
        structured.append(_structured_warning(
            'mod_mass_invalid',
            f'modMass 无法解析为有限数值（{mod_mass_raw!r}），修正量不可用',
            part_name=base_part, part_id=part_id,
            module=None, field='modMass',
            impact='该实例的保存状态质量修正无法使用',
            details={'mod_mass_raw': mod_mass_raw}))
    elif mod_mass_present and mod_mass_t != 0.0:
        structured.append(_structured_warning(
            'mod_mass_is_delta_only',
            'modMass 是存档时刻的模块修正质量，不是部件总质量；本阶段不做'
            '质量求解',
            part_name=base_part, part_id=part_id,
            module=None, field='modMass',
            impact='不能用于运力计算，需要在 6B 阶段结合部件配置求解',
            details={'mod_mass_t': mod_mass_t}))

    modules = []
    for module in part_node.children_named('MODULE'):
        name = _module_name(module)
        if name:
            modules.append(name)

    resources = []
    for resource in part_node.children_named('RESOURCE'):
        resources.append({
            'name': resource.value('name'),
            'amount': resource.value('amount'),
            'max_amount': resource.value('maxAmount'),
        })

    tanks = []
    for module in part_node.children_named('MODULE'):
        if _module_name(module) != 'ModuleFuelTanks':
            continue
        for tank in module.children_named('TANK'):
            tanks.append({
                'name': tank.value('name'),
                'amount': tank.value('amount'),
                'max_amount': tank.value('maxAmount'),
            })

    engine = _extract_engine_evidence(part_node, base_part, structured,
                                      legacy)

    return {
        'index': index,
        'part': part_full,
        'base_part': base_part,
        'instance_id': instance_id,
        'persistent_id': part_id,
        'istg': _int_or_none(part_node.value('istg')),
        'dstg': _int_or_none(part_node.value('dstg')),
        'sidx': _int_or_none(part_node.value('sidx')),
        'sqor': _int_or_none(part_node.value('sqor')),
        'sepI': _int_or_none(part_node.value('sepI')),
        'mod_mass_present': mod_mass_present,
        'mod_mass_raw': mod_mass_raw,
        'mod_mass_t': mod_mass_t,
        'mod_cost': _number(part_node.value('modCost'), 0.0),
        'connections': {
            'attN': list(part_node.values_named('attN')),
            'srfN': list(part_node.values_named('srfN')),
            'link': list(part_node.values_named('link')),
            'sym': list(part_node.values_named('sym')),
        },
        'modules': modules,
        'module_evidence': _extract_module_state(part_node),
        'resources': resources,
        'tanks': tanks,
        'engine': engine,
        'requires_mass_confirmation': True,
    }


def _legacy_stages(parts):
    """Legacy KSP-stage placeholder drafts.

    These are NOT confirmed physical stages: masses are zero, staging is
    unconfirmed, and engine counts come from low-confidence evidence.
    Kept only so the existing preview/editor flow keeps working.
    """
    grouped = {}
    for part in parts:
        if part['istg'] is None or part['istg'] < 0:
            continue
        item = grouped.setdefault(part['istg'],
                                  {'parts': 0, 'engines': {}})
        item['parts'] += 1
        if part['engine']:
            cfg = _engine_cfg(part['base_part'])
            item['engines'][cfg] = (item['engines'].get(cfg, 0)
                                    + part['engine']['count'])

    stages = []
    for stage_no in sorted(grouped, reverse=True):
        item = grouped[stage_no]
        engine_groups = [
            {'cfg': cfg, 'count': count}
            for cfg, count in item['engines'].items()
        ]
        engines = engine_groups[0] if engine_groups else {'cfg': '', 'count': 0}
        stages.append({
            'ksp_stage': stage_no,
            'wet_mass_t': 0.0,
            'dry_mass_t': 0.0,
            'engines': engines,
            'engine_groups': engine_groups,
            'multi_ignition': False,
            'parallel': False,
            'legacy_placeholder': True,
            'requires_mass_confirmation': True,
        })
    return stages


def parse_craft(text: str, filename='vehicle.craft') -> dict:
    """Parse a KSP .craft file into a structural-evidence draft.

    The draft keeps the legacy keys (rocket/stages/calibration/
    reference_missions/source/parts/warnings) for preview compatibility and
    adds ``structured_warnings`` plus ``requires_mass_confirmation``.
    Never writes anything and never modifies the input.
    """
    root = parse_config(text)

    parts = []
    legacy_warnings = []
    structured = []

    structured.append(_structured_warning(
        'mass_not_confirmed',
        'craft 草稿没有可用的质量求解：湿/干质量留空（0），必须人工确认',
        severity='warning',
        impact='在人工确认质量前不能用于运力计算',
        details={'wet_mass_t': 0.0, 'dry_mass_t': 0.0}))
    structured.append(_structured_warning(
        'stage_structure_unconfirmed',
        'istg 分组只是存档分级序号，不代表已确认的物理级序',
        severity='warning',
        impact='物理级与分离关系需在 6B 阶段结合连接拓扑与 staging 字段求解'))

    for index, part_node in enumerate(root.children_named('PART')):
        parts.append(_extract_part_evidence(part_node, index, structured,
                                            legacy_warnings))

    if not parts:
        structured.append(_structured_warning(
            'no_parts_found',
            '未找到 PART 节点，这不是有效的 KSP .craft 文件',
            severity='error'))
        legacy_warnings.append('未找到 PART 节点，这不是有效的 KSP .craft 文件')

    sym_parts = sum(1 for p in parts if p['connections']['sym'])
    if sym_parts:
        structured.append(_structured_warning(
            'symmetry_not_multiplied',
            'KSP 存档已保存每个实际 PART 实例；sym 引用只作为对称证据，'
            '不用于二次放大发动机台数或质量',
            severity='info',
            impact='无',
            details={'parts_with_sym': sym_parts}))

    name = root.value('ship') or root.value('shipName') or \
        filename.rsplit('.', 1)[0]
    root_info = {
        'ship': root.value('ship'),
        'version': root.value('version'),
        'description': root.value('description'),
        'type': root.value('type'),
        'mod_versions': root.value('_modVersions'),
    }

    return {
        'rocket': {'name': name},
        'stages': _legacy_stages(parts),
        'calibration': [],
        'reference_missions': {},
        'source': {
            'filename': filename,
            'format': 'ksp-craft',
            'part_count': len(parts),
            'root_info': root_info,
        },
        'parts': parts,
        'warnings': legacy_warnings,
        'structured_warnings': structured,
        'requires_mass_confirmation': True,
    }
