"""
KSP installed-configuration index — read-only evidence base (phase 6B.1).
==========================================================================

Builds a deterministic, cacheable, auditable index of the user's ACTUAL
KSP installation from ``GameData/ModuleManager.ConfigCache`` (the file
ModuleManager writes after applying every patch), so downstream stages can
read final part / resource / engine-layout configuration without re-implementing
ModuleManager patch syntax and without merging raw ``.cfg`` files.

Guarantees:

* pure backend, no Flask / SQLite / disk writes during build;
* callers must pass ``ksp_root`` explicitly (no disk or Steam library scan);
* never modifies KSP, GameData, ConfigCache or VAB files (read-only);
* strict craft parsing (25 MB / 50,000 nodes / no anonymous nodes) is
  untouched: the index uses its own permissive
  :data:`CACHE_PARSE_OPTIONS` (anonymous nodes allowed, 64 MB /
  150,000 nodes);
* every value here is CONFIGURATION EVIDENCE.  It must NOT be asserted as
  the final mass of a craft instance (dynamic SSTU layouts, ProceduralParts
  sizes, fuel loads and Resource tanks change instance mass).

Index statuses: ``ok`` | ``cache_missing`` | ``parse_error`` |
``cache_invalid`` (corrupt/absent JSON cache file).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .ksp_confignode import ConfigNode, CraftParseError, ParseOptions, parse

SCHEMA_VERSION = 1
SOURCE_KIND = 'module_manager_config_cache'
CONFIG_CACHE_FILENAME = 'ModuleManager.ConfigCache'

# Permissive ConfigCache parse mode: anonymous ``{ ... }`` nodes are legal
# (e.g. ``{ name = FXModuleLookAtConstraint ... }``) and the real cache is
# ~15 MB / ~73 k nodes, well above craft limits.
CACHE_PARSE_OPTIONS = ParseOptions(
    allow_anonymous_nodes=True,
    max_input_chars=64 * 1024 * 1024,
    max_nodes=150000,
)

# Modules known to change part mass dynamically.  This is evidence only:
# the presence of one of these means "the configured static mass is not the
# instance mass".
_DYNAMIC_MASS_MODULES = frozenset({
    'ProceduralPart',
    'SSTUModularEngineCluster',
    'SSTUModularFuelTank',
    'ModuleFuelTanks',
    'ModulePartVariants',
    'TweakScale',
    'B9PartSwitch',
    'InterstellarFuelSwitch',
})

# Public alias for consumers (e.g. the mass-evidence module).
DYNAMIC_MASS_MODULES = _DYNAMIC_MASS_MODULES

_ENGINE_MODULE_NAMES = ('ModuleEngines', 'ModuleEnginesFX', 'ModuleEnginesRF')

_EMPTY_COUNTS = {
    'url_configs': 0,
    'parts': 0,
    'resources': 0,
    'sstu_layouts': 0,
    'materials': 0,
    'anonymous_nodes': 0,
    'duplicate_definitions': 0,
}


def _number(value):
    """Parse a ConfigNode value as float; None when absent or unparseable."""
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _finite_number(value):
    """Strict float parse: booleans, NaN, infinity and non-numeric values
    are rejected (never treated as valid numbers)."""
    if isinstance(value, bool):
        return None
    number = _number(value)
    if number is None or not math.isfinite(number):
        return None
    return number


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


def _int_value(value):
    if value is None or value == '':
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _node_values(node: ConfigNode) -> List[List[str]]:
    return [[entry.key, entry.value] for entry in node.values]


def _diag(record_diags, code, message):
    record_diags.append({'code': code, 'message': message})


# --------------------------------------------------------------------------
# Record builders (configuration evidence)
# --------------------------------------------------------------------------

def _build_engine_evidence(module: ConfigNode, diags) -> dict:
    engine_id = module.value('engineID')
    max_thrust_raw = module.value('maxThrust')
    min_thrust_raw = module.value('minThrust')
    max_thrust = _number(max_thrust_raw)
    min_thrust = _number(min_thrust_raw)
    if max_thrust_raw is not None and max_thrust is None:
        _diag(diags, 'numeric_parse_failed',
              f'engine {engine_id!r} maxThrust 无法解析为数值: '
              f'{max_thrust_raw!r}')
    if min_thrust_raw is not None and min_thrust is None:
        _diag(diags, 'numeric_parse_failed',
              f'engine {engine_id!r} minThrust 无法解析为数值: '
              f'{min_thrust_raw!r}')
    propellants = [
        {'name': prop.value('name'), 'ratio': prop.value('ratio')}
        for prop in module.children_named('PROPELLANT')
    ]
    curve_keys = []
    for curve in module.children_named('atmosphereCurve'):
        curve_keys.extend(curve.values_named('key'))
    return {
        'module': module.value('name'),
        'engine_id': engine_id,
        'max_thrust_raw': max_thrust_raw,
        'max_thrust': max_thrust,
        'min_thrust_raw': min_thrust_raw,
        'min_thrust': min_thrust,
        'propellants': propellants,
        'atmosphere_curve_keys': curve_keys,
    }


def _build_fuel_tank_evidence(module: ConfigNode) -> dict:
    tanks = [
        {'name': tank.value('name'), 'amount': tank.value('amount'),
         'max_amount': tank.value('maxAmount')}
        for tank in module.children_named('TANK')
    ]
    return {'volume': module.value('volume'), 'tanks': tanks}


# Numeric/boolean fields kept for the mass-forensics modules (6B.3C.1).
# Only these known modules are captured — arbitrary nodes are never copied.
_PDC_NUMERIC_FIELDS = ('diameter', 'minDiameter', 'maxDiameter', 'height',
                       'minHeight', 'maxHeight', 'thickness', 'minThickness',
                       'maxThickness', 'massPerCubicMeter')
_CHUTE_NUMERIC_FIELDS = ('mass', 'chuteCount')
_CHUTE_BOOL_FIELDS = ('getMass', 'useDry')


def _numeric_evidence(node: ConfigNode, fields) -> dict:
    entry = {}
    for field in fields:
        raw = node.value(field)
        entry[f'{field}_raw'] = raw
        entry[field] = _finite_number(raw)
    return entry


def _build_module_evidence(part_node: ConfigNode) -> dict:
    """Structured evidence for known mass-relevant modules (raw + parsed)."""
    evidence = {}
    for module in part_node.children_named('MODULE'):
        module_name = module.value('name') or ''
        if module_name == 'SSTUProceduralDecoupler':
            evidence.setdefault('SSTUProceduralDecoupler', []).append(
                _numeric_evidence(module, _PDC_NUMERIC_FIELDS))
        elif module_name == 'RealChuteModule':
            case_raw = module.value('caseMass')
            spares_raw = module.value('spareChutes')
            parachutes = []
            for chute in module.children_named('PARACHUTE'):
                entry = _numeric_evidence(chute, _CHUTE_NUMERIC_FIELDS)
                entry['material'] = chute.value('material')
                entry['deployed_diameter_raw'] = chute.value(
                    'deployedDiameter')
                entry['deployed_diameter'] = _finite_number(
                    chute.value('deployedDiameter'))
                entry['pre_deployed_diameter_raw'] = chute.value(
                    'preDeployedDiameter')
                entry['pre_deployed_diameter'] = _finite_number(
                    chute.value('preDeployedDiameter'))
                parachutes.append(entry)
            entry = {
                'case_mass_raw': case_raw,
                'case_mass': _finite_number(case_raw),
                'spare_chutes_raw': spares_raw,
                'spare_chutes': _finite_number(spares_raw),
                'parachutes': parachutes,
            }
            evidence.setdefault('RealChuteModule', []).append(entry)
        elif module_name == 'ProceduralChute':
            chutes = []
            for chute in module.children_named('CHUTE'):
                entry = _numeric_evidence(chute, _CHUTE_NUMERIC_FIELDS)
                for field in _CHUTE_BOOL_FIELDS:
                    entry[f'{field}_raw'] = chute.value(field)
                    entry[field] = _bool_value(chute.value(field))
                entry['dep_diam_raw'] = chute.value('depDiam')
                entry['dep_diam'] = _finite_number(chute.value('depDiam'))
                entry['pre_dep_diam_raw'] = chute.value('preDepDiam')
                entry['pre_dep_diam'] = _finite_number(
                    chute.value('preDepDiam'))
                chutes.append(entry)
            evidence.setdefault('ProceduralChute', []).append(
                {'chutes': chutes})
        elif module_name == 'ModuleFuelTanks':
            entry = _numeric_evidence(module, (
                'mass', 'basemass', 'volume', 'utilization',
                'totalVolume', '_numberOfAddedMLILayers'))
            entry['type'] = module.value('type')
            entry['mass_is_additive_raw'] = module.value('massIsAdditive')
            entry['mass_is_additive'] = _bool_value(
                module.value('massIsAdditive'))
            tanks = []
            for tank in module.children_named('TANK'):
                tanks.append({
                    'name': tank.value('name'),
                    'mass_raw': tank.value('mass'),
                    'mass': _finite_number(tank.value('mass')),
                    'utilization_raw': tank.value('utilization'),
                    'utilization': _finite_number(
                        tank.value('utilization')),
                    'amount_raw': tank.value('amount'),
                    'amount': _finite_number(tank.value('amount')),
                    'max_amount_raw': tank.value('maxAmount'),
                    'max_amount': _finite_number(tank.value('maxAmount')),
                })
            entry['tanks'] = tanks
            evidence.setdefault('ModuleFuelTanks', []).append(entry)
    return evidence


def _build_material_record(material_node: ConfigNode) -> dict:
    """Parachute MATERIAL evidence (name -> areaDensity)."""
    name = material_node.value('name')
    diags: List[dict] = []
    if not name:
        _diag(diags, 'missing_required_field', 'MATERIAL 缺少 name 字段')
    density_raw = material_node.value('areaDensity')
    density = _finite_number(density_raw)
    if density_raw is not None and density is None:
        _diag(diags, 'numeric_parse_failed',
              f'areaDensity 无法解析为数值: {density_raw!r}')
    return {
        'kind': 'material',
        'name': name or '',
        'area_density_raw': density_raw,
        'area_density': density,
        'parent_url': material_node.parent_url if hasattr(
            material_node, 'parent_url') else None,
        'diagnostics': diags,
    }


_ATTACH_RULES_FIELDS = ('stack', 'srfAttach', 'allowStack',
                        'allowSrfAttach', 'allowCollision')


def _parse_attach_rules(raw):
    """Parse a PART attachRules value into a named structure.

    KSP attachRules has exactly five boolean fields:
    ``stack, srfAttach, allowStack, allowSrfAttach, allowCollision``.
    Fail-closed: missing / wrong field count / illegal boolean values are
    reported with a stable reason; the raw string is always preserved.
    """
    if raw is None:
        return {'raw': None, 'valid': False, 'reason': 'missing'}
    parts = [part.strip() for part in str(raw).split(',')]
    if len(parts) != 5:
        return {'raw': str(raw), 'valid': False,
                'reason': f'expected_5_fields_got_{len(parts)}'}
    result = {'raw': str(raw), 'valid': True}
    for name, part in zip(_ATTACH_RULES_FIELDS, parts):
        if part == '1':
            value = True
        elif part == '0':
            value = False
        else:
            return {'raw': str(raw), 'valid': False,
                    'reason': f'illegal_bool_{name}'}
        result[name] = value
    return result


def _stack_nodes(part_node):
    """Names of non-null ``node_stack_*`` values (attachment nodes) IN
    CONFIG ORDER — the order is the KSP attach-node index order used by
    ``attN`` connection indices."""
    nodes = []
    for entry in part_node.values:
        if not entry.key.startswith('node_stack_'):
            continue
        value = (entry.value or '').strip()
        if not value or all(token.strip() == '0'
                            for token in value.split(',')):
            continue  # null node (all-zero position/orientation/size)
        nodes.append(entry.key)
    return nodes


def _build_part_record(part_node: ConfigNode) -> dict:
    name = part_node.value('name')
    diags: List[dict] = []
    if not name:
        _diag(diags, 'missing_required_field', 'PART 缺少 name 字段')

    mass_raw = part_node.value('mass')
    cost_raw = part_node.value('cost')
    mass_t = _number(mass_raw)
    cost = _number(cost_raw)
    if mass_raw is not None and mass_t is None:
        _diag(diags, 'numeric_parse_failed',
              f'mass 无法解析为数值: {mass_raw!r}')
    if cost_raw is not None and cost is None:
        _diag(diags, 'numeric_parse_failed',
              f'cost 无法解析为数值: {cost_raw!r}')

    attach_rules = _parse_attach_rules(part_node.value('attachRules'))
    if not attach_rules['valid']:
        _diag(diags, 'attach_rules_invalid',
              f'attachRules 无法解析: {attach_rules["reason"]}')
    bulkhead_raw = part_node.value('bulkheadProfiles')
    bulkhead_profiles = [
        profile.strip() for profile in (bulkhead_raw or '').split(',')
        if profile.strip()
    ] or None
    crew_capacity_raw = part_node.value('CrewCapacity')
    crew_capacity = _int_value(crew_capacity_raw)
    if crew_capacity_raw is not None and crew_capacity is None:
        _diag(diags, 'numeric_parse_failed',
              f'CrewCapacity 无法解析为整数: {crew_capacity_raw!r}')

    modules: List[str] = []
    dynamic_mass_modules: List[str] = []
    engines: List[dict] = []
    fuel_tanks: List[dict] = []
    for module in part_node.children_named('MODULE'):
        module_name = module.value('name') or ''
        if module_name:
            modules.append(module_name)
        if module_name in _DYNAMIC_MASS_MODULES:
            dynamic_mass_modules.append(module_name)
        if module_name in _ENGINE_MODULE_NAMES:
            engines.append(_build_engine_evidence(module, diags))
        if module_name == 'ModuleFuelTanks':
            fuel_tanks.append(_build_fuel_tank_evidence(module))

    sstu_cluster = None
    for module in part_node.children_named('MODULE'):
        if module.value('name') == 'SSTUModularEngineCluster':
            sstu_cluster = {
                'layout': module.value('currentEngineLayoutName'),
                'engine_model': module.value('engineModelName'),
            }
            break

    resources = [
        {'name': res.value('name'), 'amount': res.value('amount'),
         'max_amount': res.value('maxAmount')}
        for res in part_node.children_named('RESOURCE')
    ]

    physics_raw = part_node.value('PhysicsSignificance')

    return {
        'kind': 'part',
        'name': name or '',
        'title': part_node.value('title'),
        'manufacturer': part_node.value('manufacturer'),
        'description': part_node.value('description'),
        'category': part_node.value('category'),
        'mass_raw': mass_raw,
        'mass_t': mass_t,
        'cost_raw': cost_raw,
        'cost': cost,
        'physics_significance_raw': physics_raw,
        'physics_significance': _bool_value(physics_raw),
        'attach_rules': attach_rules,
        'attach_rules_raw': attach_rules.get('raw'),
        'bulkhead_profiles_raw': bulkhead_raw,
        'bulkhead_profiles': bulkhead_profiles,
        'crew_capacity_raw': crew_capacity_raw,
        'crew_capacity': crew_capacity,
        'stack_nodes': sorted(_stack_nodes(part_node)),
        'stack_nodes_ordered': _stack_nodes(part_node),
        'attach_node': any(
            entry.key == 'node_attach' and (entry.value or '').strip()
            for entry in part_node.values),
        'resources': resources,
        'modules': modules,
        'module_evidence': _build_module_evidence(part_node),
        'fuel_tanks': fuel_tanks,
        'engines': engines,
        'sstu_cluster': sstu_cluster,
        'dynamic_mass_modules': dynamic_mass_modules,
        'diagnostics': diags,
    }


def _build_resource_record(resource_node: ConfigNode) -> dict:
    name = resource_node.value('name')
    diags: List[dict] = []
    if not name:
        _diag(diags, 'missing_required_field',
              'RESOURCE_DEFINITION 缺少 name 字段')

    density_raw = resource_node.value('density')
    density_t = _number(density_raw)
    unit_cost_raw = resource_node.value('unitCost')
    unit_cost = _number(unit_cost_raw)
    if density_raw is not None and density_t is None:
        _diag(diags, 'numeric_parse_failed',
              f'density 无法解析为数值: {density_raw!r}')
    if unit_cost_raw is not None and unit_cost is None:
        _diag(diags, 'numeric_parse_failed',
              f'unitCost 无法解析为数值: {unit_cost_raw!r}')

    return {
        'kind': 'resource',
        'name': name or '',
        'density_raw': density_raw,
        'density_t_per_unit': density_t,
        'unit_cost_raw': unit_cost_raw,
        'unit_cost': unit_cost,
        'flow_mode': resource_node.value('flowMode'),
        'transfer': resource_node.value('transfer'),
        'diagnostics': diags,
    }


def _build_layout_record(layout_node: ConfigNode) -> dict:
    name = layout_node.value('name')
    diags: List[dict] = []
    if not name:
        _diag(diags, 'missing_required_field',
              'SSTU_ENGINELAYOUT 缺少 name 字段')

    positions = [
        {'index': index, 'values': _node_values(pos)}
        for index, pos in enumerate(layout_node.children_named('POSITION'))
    ]
    mount_size_raw = layout_node.value('mountSizeMult')
    mount_size = _number(mount_size_raw)
    if mount_size_raw is not None and mount_size is None:
        _diag(diags, 'numeric_parse_failed',
              f'mountSizeMult 无法解析为数值: {mount_size_raw!r}')

    return {
        'kind': 'sstu_layout',
        'name': name or '',
        'position_count': len(positions),
        'positions': positions,
        'mount_size_mult_raw': mount_size_raw,
        'mount_size_mult': mount_size,
        'mount_positions': layout_node.value('mountPositions'),
        'rotate_engines': layout_node.value('rotateEngines'),
        'diagnostics': diags,
    }


def _count_anonymous(node: ConfigNode, samples: List[str], limit=10):
    """Count anonymous child nodes (name == ''), sampling node paths."""
    count = 0
    for child in node.children:
        if child.name == '':
            count += 1
            if len(samples) < limit:
                samples.append(child.value('name') or '<no name>')
        count += _count_anonymous(child, samples, limit)
    return count


# --------------------------------------------------------------------------
# Index object
# --------------------------------------------------------------------------

class KspIndex:
    """Immutable-ish in-memory view of the installation index."""

    def __init__(self, *, status: str, metadata: dict, parts: List[dict],
                 resources: List[dict], sstu_layouts: List[dict],
                 diagnostics: List[dict], materials: Optional[List[dict]]
                 = None):
        self.status = status
        self.metadata = metadata
        self.parts = parts
        self.resources = resources
        self.sstu_layouts = sstu_layouts
        self.materials = materials or []
        self.diagnostics = diagnostics
        self._parts_by_name = _group_by_name(parts)
        self._resources_by_name = _group_by_name(resources)
        self._layouts_by_name = _group_by_name(sstu_layouts)
        self._materials_by_name = _group_by_name(self.materials)

    @classmethod
    def empty(cls, *, status, metadata, diagnostics):
        return cls(status=status, metadata=metadata, parts=[], resources=[],
                   sstu_layouts=[], diagnostics=list(diagnostics))

    # ---- queries ----------------------------------------------------------

    def find_material(self, name: str) -> Optional[dict]:
        return _first_primary(self._materials_by_name.get(name, []))

    def find_parts(self, name: str) -> List[dict]:
        """All PART candidates for ``name`` in cache order (never deduped)."""
        return list(self._parts_by_name.get(name, []))

    def primary_part(self, name: str) -> Optional[dict]:
        """Deterministic primary PART candidate (see docs) or None."""
        return _first_primary(self._parts_by_name.get(name, []))

    def find_resource(self, name: str) -> Optional[dict]:
        return _first_primary(self._resources_by_name.get(name, []))

    def find_resources(self, name: str) -> List[dict]:
        """All RESOURCE_DEFINITION candidates for ``name`` in cache order.

        Callers that need densities must treat several candidates as
        ambiguous; never silently use the primary here.
        """
        return list(self._resources_by_name.get(name, []))

    def find_sstu_layout(self, name: str) -> Optional[dict]:
        return _first_primary(self._layouts_by_name.get(name, []))

    def find_sstu_layouts(self, name: str) -> List[dict]:
        """All SSTU_ENGINELAYOUT candidates for ``name`` in cache order."""
        return list(self._layouts_by_name.get(name, []))

    def lookup_status(self, kind: str, name: str) -> str:
        """Classify a query: found | missing | ambiguous | invalid_record."""
        bucket = {
            'part': self._parts_by_name,
            'resource': self._resources_by_name,
            'sstu_layout': self._layouts_by_name,
        }.get(kind, {})
        records = bucket.get(name, [])
        if not records:
            return 'missing'
        primary = _first_primary(records)
        if primary and any(d['code'] == 'missing_required_field'
                           for d in primary.get('diagnostics', [])):
            return 'invalid_record'
        return 'ambiguous' if len(records) > 1 else 'found'

    def diagnostics_summary(self) -> dict:
        by_severity = Counter(d['severity'] for d in self.diagnostics)
        by_code = Counter(d['code'] for d in self.diagnostics)
        return {
            'total': len(self.diagnostics),
            'by_severity': dict(by_severity),
            'by_code': dict(by_code),
            'errors': [d for d in self.diagnostics
                       if d['severity'] == 'error'],
            'warnings': [d for d in self.diagnostics
                         if d['severity'] == 'warning'],
            'infos': [d for d in self.diagnostics
                      if d['severity'] == 'info'],
        }

    # ---- serialization ----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            'schema_version': SCHEMA_VERSION,
            'status': self.status,
            'metadata': self.metadata,
            'parts': self.parts,
            'resources': self.resources,
            'sstu_layouts': self.sstu_layouts,
            'materials': self.materials,
            'diagnostics': self.diagnostics,
        }

    def __repr__(self):
        return (f'KspIndex(status={self.status!r}, parts={len(self.parts)}, '
                f'resources={len(self.resources)}, '
                f'sstu_layouts={len(self.sstu_layouts)}, '
                f'diagnostics={len(self.diagnostics)})')


def _group_by_name(records: List[dict]) -> Dict[str, List[dict]]:
    grouped: Dict[str, List[dict]] = defaultdict(list)
    for record in records:
        grouped[record.get('name', '')].append(record)
    return grouped


def _first_primary(records: List[dict]) -> Optional[dict]:
    for record in records:
        if record.get('primary'):
            return record
    return records[0] if records else None


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def _source_path(ksp_root, config_cache_path=None) -> Path:
    if config_cache_path is not None:
        return Path(config_cache_path)
    return Path(ksp_root) / 'GameData' / CONFIG_CACHE_FILENAME


def _metadata(ksp_root: Path, source_path: Path, raw: bytes, size: int,
              mtime_ns: int, sha256: str, patched_node_count,
              record_counts: dict, status: str) -> dict:
    return {
        'schema_version': SCHEMA_VERSION,
        'source_kind': SOURCE_KIND,
        'ksp_root': str(ksp_root),
        'config_cache_path': str(source_path),
        'file_size': size,
        'mtime_ns': mtime_ns,
        'sha256': sha256,
        'patched_node_count': patched_node_count,
        'built_at': datetime.now(timezone.utc).isoformat(),
        'status': status,
        'record_counts': record_counts,
    }


def _build_from_tree(root: ConfigNode, ksp_root: Path, source_path: Path,
                     raw: bytes) -> KspIndex:
    parts_by_name: Dict[str, List[dict]] = defaultdict(list)
    resources_by_name: Dict[str, List[dict]] = defaultdict(list)
    layouts_by_name: Dict[str, List[dict]] = defaultdict(list)
    materials_by_name: Dict[str, List[dict]] = defaultdict(list)
    diagnostics: List[dict] = []
    anonymous_samples: List[str] = []
    anonymous_count = _count_anonymous(root, anonymous_samples)
    if anonymous_count:
        sample = '；'.join(repr(s) for s in anonymous_samples)
        diagnostics.append({
            'severity': 'info', 'code': 'anonymous_node',
            'message': (f'ConfigCache 含 {anonymous_count} 个匿名节点'
                        f'（索引模式允许）；示例: {sample}'),
        })
    if anonymous_samples and len(anonymous_samples) < anonymous_count:
        diagnostics[-1]['message'] += '…'

    patched_node_count = _int_value(root.value('patchedNodeCount'))

    def ingest(node: ConfigNode, parent_url: str, cache_order: int):
        record = None
        bucket = None
        kind = node.name
        if kind == 'PART':
            record = _build_part_record(node)
            bucket = parts_by_name
        elif kind == 'RESOURCE_DEFINITION':
            record = _build_resource_record(node)
            bucket = resources_by_name
        elif kind == 'SSTU_ENGINELAYOUT':
            record = _build_layout_record(node)
            bucket = layouts_by_name
        elif kind == 'MATERIAL':
            record = _build_material_record(node)
            bucket = materials_by_name
        else:
            return
        name = record['name']
        record['parent_url'] = parent_url
        record['cache_order'] = cache_order
        record['primary'] = False
        existing = bucket.get(name)
        if existing:
            diagnostics.append({
                'severity': 'warning', 'code': 'duplicate_definition',
                'message': f'{kind} {name!r} 重复定义',
                'parent_url': parent_url, 'cache_order': cache_order,
            })
        bucket[name].append(record)

    cache_order = 0
    url_config_count = 0
    for top in root.children:
        if top.name == 'UrlConfig':
            url_config_count += 1
            parent_url = top.value('parentUrl') or ''
            for inner in top.children:
                cache_order += 1
                ingest(inner, parent_url, cache_order)
        else:
            # A record node outside any UrlConfig wrapper (not produced by
            # stock ModuleManager, but tolerated and audited).
            cache_order += 1
            ingest(top, '', cache_order)
            if top.name in ('PART', 'RESOURCE_DEFINITION', 'SSTU_ENGINELAYOUT'):
                diagnostics.append({
                    'severity': 'info', 'code': 'record_outside_url_config',
                    'message': f'{top.name} 节点未包在 UrlConfig 内',
                    'cache_order': cache_order,
                })

    # Deterministic primary selection: sort candidates by (parent_url
    # casefold, cache_order); the first is the primary.  This is a stable
    # default for queries, NOT a correctness assertion.
    for bucket in (parts_by_name, resources_by_name, layouts_by_name,
                   materials_by_name):
        for records in bucket.values():
            records.sort(key=lambda r: (r['parent_url'].casefold(),
                                        r['cache_order']))
            records[0]['primary'] = True

    parts = _flatten(parts_by_name)
    resources = _flatten(resources_by_name)
    layouts = _flatten(layouts_by_name)
    materials = _flatten(materials_by_name)
    # Mirror record-level diagnostics into the global audit list (with
    # provenance) so diagnostics_summary() reports every parsing issue.
    for record in parts + resources + layouts + materials:
        for local in record.get('diagnostics') or []:
            diagnostics.append({
                'severity': 'warning',
                'code': local['code'],
                'message': local['message'],
                'parent_url': record.get('parent_url'),
                'cache_order': record.get('cache_order'),
            })
    counts = {
        'url_configs': url_config_count,
        'parts': len(parts),
        'resources': len(resources),
        'sstu_layouts': len(layouts),
        'materials': len(materials),
        'anonymous_nodes': anonymous_count,
        'duplicate_definitions': sum(
            1 for d in diagnostics if d['code'] == 'duplicate_definition'),
    }
    metadata = _metadata(ksp_root, source_path, raw, len(raw),
                         source_path.stat().st_mtime_ns,
                         hashlib.sha256(raw).hexdigest(),
                         patched_node_count, counts, 'ok')
    return KspIndex(status='ok', metadata=metadata, parts=parts,
                    resources=resources, sstu_layouts=layouts,
                    materials=materials,
                    diagnostics=diagnostics)


def _flatten(grouped: Dict[str, List[dict]]) -> List[dict]:
    return sorted((record for records in grouped.values()
                   for record in records),
                  key=lambda r: r['cache_order'])


def build_ksp_index(ksp_root, cache_path=None, *, config_cache_path=None):
    """Build the installation index from the module-manager ConfigCache.

    Read-only: never modifies KSP/GameData.  ``cache_path=None`` builds an
    in-memory index and writes nothing; a JSON cache is written atomically
    only when ``cache_path`` is given.  Missing ConfigCache yields
    ``status='cache_missing'``; format errors yield ``status='parse_error'``
    with no half-built records.
    """
    ksp_root = Path(ksp_root)
    source_path = _source_path(ksp_root, config_cache_path)
    diagnostics: List[dict] = []
    if not source_path.is_file():
        diagnostics.append({
            'severity': 'error', 'code': 'cache_missing',
            'message': (f'未找到 {CONFIG_CACHE_FILENAME}；请先启动一次 KSP '
                        f'生成 ModuleManager 缓存（{source_path}）'),
        })
        metadata = _metadata(ksp_root, source_path, b'', 0, 0, '', None,
                             dict(_EMPTY_COUNTS), 'cache_missing')
        return KspIndex.empty(status='cache_missing', metadata=metadata,
                              diagnostics=diagnostics)

    raw = source_path.read_bytes()
    size = len(raw)
    mtime_ns = source_path.stat().st_mtime_ns
    sha256 = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        diagnostics.append({
            'severity': 'error', 'code': 'cache_encoding_error',
            'message': f'{CONFIG_CACHE_FILENAME} 不是有效的 UTF-8 编码',
        })
        metadata = _metadata(ksp_root, source_path, raw, size, mtime_ns,
                             sha256, None, dict(_EMPTY_COUNTS),
                             'parse_error')
        return KspIndex.empty(status='parse_error', metadata=metadata,
                              diagnostics=diagnostics)
    try:
        root = parse(text, options=CACHE_PARSE_OPTIONS)
    except CraftParseError as exc:
        diagnostics.append({
            'severity': 'error', 'code': 'cache_parse_error',
            'message': f'ConfigCache 解析失败 [{exc.code}]: {exc.message}',
            'line': exc.line, 'column': exc.column,
            'node_path': exc.node_path,
        })
        metadata = _metadata(ksp_root, source_path, raw, size, mtime_ns,
                             sha256, None, dict(_EMPTY_COUNTS),
                             'parse_error')
        return KspIndex.empty(status='parse_error', metadata=metadata,
                              diagnostics=diagnostics)

    index = _build_from_tree(root, ksp_root, source_path, raw)
    if cache_path is not None:
        _atomic_write_json(cache_path, index.to_dict())
    return index


# --------------------------------------------------------------------------
# JSON cache
# --------------------------------------------------------------------------

def _atomic_write_json(path, payload):
    """Atomically write JSON: temp file in the same directory + rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + '.tmp')
    try:
        with open(temp_path, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def load_ksp_index(cache_path):
    """Load a JSON index cache; never touches the KSP install.

    Missing / corrupt / wrong-schema cache files return an index with
    ``status='cache_invalid'`` instead of raising.
    """
    path = Path(cache_path)
    if not path.is_file():
        return KspIndex.empty(
            status='cache_invalid',
            metadata={'schema_version': SCHEMA_VERSION,
                      'source_kind': SOURCE_KIND},
            diagnostics=[{
                'severity': 'error', 'code': 'cache_invalid',
                'message': f'索引缓存文件不存在: {path}',
            }])
    try:
        with open(path, encoding='utf-8') as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError('payload 不是对象')
        if payload.get('schema_version') != SCHEMA_VERSION:
            raise ValueError(
                f'schema_version 不匹配: {payload.get("schema_version")!r}')
        parts = payload.get('parts')
        resources = payload.get('resources')
        layouts = payload.get('sstu_layouts')
        materials = payload.get('materials')
        diagnostics = payload.get('diagnostics')
        if not (isinstance(parts, list) and isinstance(resources, list)
                and isinstance(layouts, list)
                and isinstance(diagnostics, list)):
            raise ValueError('payload 结构不完整')
        if materials is None:
            materials = []
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return KspIndex.empty(
            status='cache_invalid',
            metadata={'schema_version': SCHEMA_VERSION,
                      'source_kind': SOURCE_KIND},
            diagnostics=[{
                'severity': 'error', 'code': 'cache_invalid',
                'message': f'索引缓存无效: {exc}',
            }])
    return KspIndex(status=payload.get('status', 'ok'),
                    metadata=payload.get('metadata') or {},
                    parts=parts, resources=resources, sstu_layouts=layouts,
                    materials=materials, diagnostics=diagnostics)


def _fingerprint_matches(index: KspIndex, ksp_root, config_cache_path) -> bool:
    meta = index.metadata
    source_path = _source_path(ksp_root, config_cache_path)
    if str(source_path) != meta.get('config_cache_path'):
        return False
    if not source_path.is_file():
        return False
    try:
        stat = source_path.stat()
        if stat.st_size != meta.get('file_size'):
            return False
        if stat.st_mtime_ns != meta.get('mtime_ns'):
            return False
        digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        return digest == meta.get('sha256')
    except OSError:
        return False


def load_or_build_ksp_index(ksp_root, cache_path=None, *,
                            config_cache_path=None):
    """Return the cached index when valid, else build (and cache) it.

    Reuses the JSON cache only when its schema_version and source
    fingerprint (path, size, mtime_ns, SHA-256 of the ConfigCache) match.
    """
    if cache_path is not None:
        cached = load_ksp_index(cache_path)
        if cached.status == 'ok' and _fingerprint_matches(
                cached, ksp_root, config_cache_path):
            return cached
    return build_ksp_index(ksp_root, cache_path=cache_path,
                           config_cache_path=config_cache_path)


# --------------------------------------------------------------------------
# Lazy auto-index provider (phase 6B.2)
# --------------------------------------------------------------------------

_AUTO_INDEX_CACHE = {}


def get_auto_ksp_index():
    """Lazily return the install index for preview flows, or None.

    Candidate roots come from ``C3_MASS_KSP_ROOT`` first, then
    :data:`server.config.KSP_INSTALL_CANDIDATES`.  Never scans disks or
    Steam libraries.  The in-memory build is cached per source fingerprint
    (path + size + mtime_ns) for the lifetime of the process.
    """
    from .config import KSP_INSTALL_CANDIDATES
    roots = []
    env_root = os.environ.get('C3_MASS_KSP_ROOT')
    if env_root:
        from .paths import native_path
        roots.append(native_path(env_root))
    roots.extend(KSP_INSTALL_CANDIDATES)
    for root in roots:
        source_path = Path(root) / 'GameData' / CONFIG_CACHE_FILENAME
        if not source_path.is_file():
            continue
        try:
            stat = source_path.stat()
        except OSError:
            continue
        fingerprint = (str(source_path), stat.st_size, stat.st_mtime_ns)
        cached = _AUTO_INDEX_CACHE.get(fingerprint)
        if cached is not None:
            return cached
        index = build_ksp_index(root)
        if index.status == 'ok':
            _AUTO_INDEX_CACHE[fingerprint] = index
            return index
    return None


def clear_auto_index_cache():
    """Drop the process-wide auto-index cache (used by tests)."""
    _AUTO_INDEX_CACHE.clear()
