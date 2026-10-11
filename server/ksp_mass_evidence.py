"""
Per-PART mass evidence extraction (phase 6B.3A, rework 6B.3A-R).
=================================================================

Builds deterministic per-instance evidence on top of the phase 6B.2 match:
base mass, modMass delta, resource amounts, resource densities and dynamic
mass risk.  Everything here is EVIDENCE / ESTIMATE — it never produces
final stage masses, engine counts, delta-V or payload, and never touches
``stage.wet_mass_t`` / ``stage.dry_mass_t``.

Rework fixes (6B.3A-R):

* "no resource configuration" and "resource state missing" are distinct:
  a part whose index record declares RESOURCE but whose craft saved no
  state gets ``resource_state_missing`` and NEVER a zero resource mass;
* config default RESOURCE is real evidence: attached to craft rows as
  ``config_default_*``, and config-only resources produce
  ``config_default_only`` rows with ``state_missing``; the config default
  amount is never adopted as the craft amount;
* explicit invalid modMass (present but unparseable/NaN/inf) is never
  ignored: static parts -> ``invalid_mass``, dynamic parts ->
  ``dynamic_unresolved``;
* duplicate / unnamed craft RESOURCE and TANK entries are never silently
  collapsed; they become unresolved rows with diagnostics;
* mirroring compares NUMERIC semantics ("10" == "10.0"); invalid numbers
  can never be mirrored;
* one shared ``_row_is_resolved`` implementation drives every stat;
* report adds resolved/unresolved instance counts and density / config /
  duplicate line counters.

The module is pure: no disk access, no SQL/YAML/cache writes, no Flask,
no mutation of inputs, JSON-serializable and deterministic.
"""

from __future__ import annotations

import math
from typing import List, Optional

from .ksp_install_index import DYNAMIC_MASS_MODULES
from .ksp_module_mass_semantics import (
    REALCHUTE_CANDIDATE_PART,
    RC_DYNAMIC_UNRESOLVED,
    RC_SETTLED,
    realchute_config_material,
    realchute_part_settlement_evidence,
)

SCHEMA_VERSION = 3
SOURCE_KIND = 'ksp_mass_evidence'

# resolution statuses ------------------------------------------------------
RESOLVED_STATIC = 'resolved_static'
ESTIMATED_WITH_MODIFIER = 'estimated_with_modifier'
DYNAMIC_UNRESOLVED = 'dynamic_unresolved'
RESOURCE_UNRESOLVED = 'resource_unresolved'
MATCH_UNAVAILABLE = 'match_unavailable'
INVALID_MASS = 'invalid_mass'
_ALL_STATUSES = (RESOLVED_STATIC, ESTIMATED_WITH_MODIFIER,
                 DYNAMIC_UNRESOLVED, RESOURCE_UNRESOLVED,
                 MATCH_UNAVAILABLE, INVALID_MASS)

# density statuses ---------------------------------------------------------
DENSITY_RESOLVED = 'resolved'
DENSITY_MISSING = 'missing_density'
DENSITY_AMBIGUOUS = 'ambiguous_density'
DENSITY_INVALID = 'invalid_density'

# resource line sources ----------------------------------------------------
SOURCE_CRAFT_RESOURCE = 'craft_resource'
SOURCE_TANK_FALLBACK = 'tank_fallback'
SOURCE_CONFIG_DEFAULT_ONLY = 'config_default_only'

# match statuses we accept from the 6B.2 report
_ACCEPTED_MATCH_STATUSES = ('exact', 'normalized_unique')

_CONFIDENCE = {
    RESOLVED_STATIC: 'high',
    ESTIMATED_WITH_MODIFIER: 'medium',
    DYNAMIC_UNRESOLVED: 'low',
    RESOURCE_UNRESOLVED: 'low',
    MATCH_UNAVAILABLE: 'none',
    INVALID_MASS: 'none',
}


def _finite(value):
    """Finite float or None (rejects NaN / infinity / non-numeric).

    Accepts numbers and numeric strings (ConfigNode values are strings).
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


def _diag(diags, code, message):
    diags.append({'code': code, 'message': message})


def _selected_record(ksp_index, match_instance):
    """Locate the full index record behind a unique match by
    (name, cache_order) — deterministic, never ambiguous."""
    selected = (match_instance or {}).get('selected')
    if not selected:
        return None
    for record in ksp_index.find_parts(selected.get('name')):
        if record.get('cache_order') == selected.get('cache_order'):
            return record
    return None


# --------------------------------------------------------------------------
# Resource rows: craft RESOURCE + ModuleFuelTanks/TANK + config defaults
# --------------------------------------------------------------------------
#
# Two kinds of installation-config resource evidence are kept strictly
# apart (6B.3A-R2):
#
#   A. PART top-level RESOURCE (record["resources"]) — preconfigured
#      DEFAULT actual resources.  Missing craft state MAY produce a
#      ``config_default_only`` row (state unknown, never zero).
#   B. ModuleFuelTanks/TANK (record["fuel_tanks"][...]["tanks"]) — dynamic
#      fuel-tank TEMPLATES / candidate fuels / conversion parameters.
#      They never prove a resource is installed: missing craft state
#      produces NO row, NO resource_state_missing and NO unresolved
#      status; template amounts ("full") and capacities never enter any
#      mass total.

def _part_resource_defaults(name: str, record: dict) -> List[dict]:
    """PART top-level RESOURCE defaults for a name (kind A)."""
    return [{'amount_raw': res.get('amount'),
             'max_amount_raw': res.get('max_amount')}
            for res in record.get('resources') or []
            if res.get('name') == name]


def _module_fuel_tank_options(name: str, record: dict) -> List[dict]:
    """ModuleFuelTanks/TANK template entries for a name (kind B)."""
    options = []
    for tank_module in record.get('fuel_tanks') or []:
        for tank in tank_module.get('tanks') or []:
            if tank.get('name') == name:
                options.append({
                    'amount_raw': tank.get('amount'),
                    'max_amount_raw': tank.get('max_amount'),
                    'volume': tank_module.get('volume'),
                })
    return options


def _all_module_fuel_tank_options(record: dict) -> List[dict]:
    """Every ModuleFuelTanks/TANK template of the part (audit evidence)."""
    options = []
    for tank_module in record.get('fuel_tanks') or []:
        for tank in tank_module.get('tanks') or []:
            if not tank.get('name'):
                continue
            options.append({
                'name': tank['name'],
                'amount_raw': tank.get('amount'),
                'max_amount_raw': tank.get('max_amount'),
                'volume': tank_module.get('volume'),
            })
    return options


def _top_level_resource_names(record: dict) -> List[str]:
    """Names declared by PART top-level RESOURCE only (kind A)."""
    return sorted({res.get('name') for res in record.get('resources') or []
                   if res.get('name')})


def _attach_config_defaults(row: dict, name: str, record: dict,
                            diags: List[dict]) -> None:
    """Attach kind-A defaults and kind-B templates to a craft row.

    ``config_default_*`` and ``configured_maximum_mass_t`` come ONLY from
    top-level RESOURCE defaults; ModuleFuelTanks/TANK entries are kept as
    separate option/template evidence and never influence mass totals.
    """
    defaults = _part_resource_defaults(name, record)
    options = _module_fuel_tank_options(name, record)
    row['part_resource_defaults'] = defaults
    row['module_fuel_tank_options'] = options
    row['config_default_present'] = bool(defaults)
    row['config_default_amount_raw'] = (defaults[0]['amount_raw']
                                        if defaults else None)
    row['config_default_max_amount_raw'] = (defaults[0]['max_amount_raw']
                                            if defaults else None)
    if len(defaults) > 1:
        _diag(diags, 'config_default_ambiguous',
              f'资源 {name!r} 在 PART 顶层有 {len(defaults)} 个默认容量候选')


def _same_numeric(a, b) -> bool:
    """Numeric-equality for mirroring ("10" == "10.0"); invalid -> False."""
    fa, fb = _finite(a), _finite(b)
    return fa is not None and fb is not None and fa == fb


def _new_row(name, source, amount_raw, max_amount_raw, diags) -> dict:
    return {
        'name': name or '',
        'source': source,
        'amount_raw': amount_raw,
        'max_amount_raw': max_amount_raw,
        'mirrored': False,
        'fallback': source == SOURCE_TANK_FALLBACK,
        'tank_mismatch': False,
        'unresolved': False,
        'unnamed': not name,
        'state_missing': False,
        'density_raw': None,
        'density_t_per_unit': None,
        'density_status': None,
        'current_mass_t': None,
        'maximum_mass_t': None,
        'configured_maximum_mass_t': None,
        'part_resource_defaults': [],
        'module_fuel_tank_options': [],
        'config_default_present': False,
        'config_default_amount_raw': None,
        'config_default_max_amount_raw': None,
        'diagnostics': diags,
    }


def _collect_craft_rows(part: dict) -> List[dict]:
    """Craft RESOURCE / TANK rows, preserving duplicates and unnamed rows.

    Duplicate same-name entries from the same source are kept (never
    overwritten) and flagged; whenever a name repeats, EVERY row of that
    name is uncertain -> unresolved.
    """
    rows: List[dict] = []
    resource_entries = list(part.get('resources') or [])
    tank_entries = list(part.get('tanks') or [])
    resource_counts = {}
    for res in resource_entries:
        resource_counts[res.get('name')] = resource_counts.get(
            res.get('name'), 0) + 1
    tank_counts = {}
    for tank in tank_entries:
        tank_counts[tank.get('name')] = tank_counts.get(tank.get('name'), 0) + 1

    for res in resource_entries:
        name = res.get('name')
        diags: List[dict] = []
        row = _new_row(name, SOURCE_CRAFT_RESOURCE, res.get('amount'),
                       res.get('max_amount'), diags)
        if not name:
            _diag(diags, 'unnamed_resource',
                  'craft RESOURCE 缺少名称，状态无法确定')
            row['unresolved'] = True
        elif resource_counts[name] > 1:
            _diag(diags, 'duplicate_craft_resource',
                  f'craft 中同名 RESOURCE {name!r} 出现 '
                  f'{resource_counts[name]} 次，实际状态无法确定')
            row['unresolved'] = True
        rows.append(row)

    tank_rows: List[dict] = []
    for tank in tank_entries:
        name = tank.get('name')
        diags: List[dict] = []
        row = _new_row(name, SOURCE_TANK_FALLBACK, tank.get('amount'),
                       tank.get('max_amount'), diags)
        if not name:
            _diag(diags, 'unnamed_tank',
                  'ModuleFuelTanks/TANK 缺少名称，状态无法确定')
            row['unresolved'] = True
        elif tank_counts[name] > 1:
            _diag(diags, 'duplicate_tank_resource',
                  f'TANK 中同名资源 {name!r} 出现 {tank_counts[name]} 次，'
                  '实际状态无法确定')
            row['unresolved'] = True
        tank_rows.append(row)

    # pair TANK rows with craft RESOURCE rows (numeric mirroring)
    used = set()
    for tank_row in tank_rows:
        target = None
        for index, resource_row in enumerate(rows):
            if index in used:
                continue
            if resource_row['source'] != SOURCE_CRAFT_RESOURCE:
                continue
            if not tank_row['name'] or resource_row['name'] != tank_row['name']:
                continue
            target = index
            break
        if target is None:
            rows.append(tank_row)  # standalone fallback
            continue
        used.add(target)
        resource_row = rows[target]
        both_valid = (_same_numeric(resource_row['amount_raw'],
                                    tank_row['amount_raw'])
                      and _same_numeric(resource_row['max_amount_raw'],
                                        tank_row['max_amount_raw']))
        if both_valid:
            resource_row['mirrored'] = True
            _diag(resource_row['diagnostics'], 'mirrored',
                  'RESOURCE 与 TANK 记录同一状态，只计算一次')
        else:
            resource_row['tank_mismatch'] = True
            _diag(resource_row['diagnostics'], 'resource_tank_mismatch',
                  '同名 TANK 的 amount/maxAmount 与 craft RESOURCE 不一致'
                  '（按数值比较），以 craft RESOURCE 为准')
            if tank_row['unresolved']:
                _diag(resource_row['diagnostics'], 'duplicate_tank_resource',
                      '配对失败：TANK 存在重复或未命名条目')
            resource_row['fallback'] = False
    return rows


def _append_config_default_only_rows(rows: List[dict], record: dict,
                                     diags: List[dict]) -> List[dict]:
    """Top-level RESOURCE names with no craft state become evidence rows.

    Only PART top-level RESOURCE (kind A) is traversed — ModuleFuelTanks
    TANK templates never generate state-missing rows.
    """
    craft_names = {row['name'] for row in rows if row['name']}
    for name in _top_level_resource_names(record):
        if name in craft_names:
            continue
        row_diags: List[dict] = []
        row = _new_row(name, SOURCE_CONFIG_DEFAULT_ONLY, None, None,
                       row_diags)
        row['state_missing'] = True
        _diag(row_diags, 'resource_state_missing',
              f'PART 顶层 RESOURCE 声明了 {name!r}，但 craft 没有保存该'
              '资源状态；不得假设为空')
        _attach_config_defaults(row, name, record, row_diags)
        rows.append(row)
    return rows


# --------------------------------------------------------------------------
# Density and row computation
# --------------------------------------------------------------------------

def _density_evidence(name: str, ksp_index, diags: List[dict]):
    """Density from a UNIQUE RESOURCE_DEFINITION only."""
    if not name:
        _diag(diags, 'missing_density', '未命名资源无法查密度')
        return None, DENSITY_MISSING, None
    if ksp_index is None:
        _diag(diags, 'missing_density', f'资源 {name!r} 无索引可用')
        return None, DENSITY_MISSING, None
    candidates = ksp_index.find_resources(name)
    if not candidates:
        _diag(diags, 'missing_density',
              f'资源 {name!r} 在索引中没有 RESOURCE_DEFINITION')
        return None, DENSITY_MISSING, None
    if len(candidates) > 1:
        _diag(diags, 'ambiguous_density',
              f'资源 {name!r} 有 {len(candidates)} 个定义，未自动选取 '
              f'primary；密度不可用')
        return None, DENSITY_AMBIGUOUS, None
    record = candidates[0]
    density = _finite(record.get('density_t_per_unit'))
    if density is None or density < 0:
        _diag(diags, 'invalid_density',
              f'资源 {name!r} 密度非法（{record.get("density_raw")!r}）')
        return None, DENSITY_INVALID, record.get('density_raw')
    return density, DENSITY_RESOLVED, record.get('density_raw')


def _compute_row(row: dict, ksp_index, record: dict) -> dict:
    """Validate amounts/density/config defaults and compute row masses."""
    diags = row['diagnostics']
    if row['fallback']:
        _diag(diags, 'tank_fallback',
              'craft 顶层 RESOURCE 缺失，以 ModuleFuelTanks/TANK 回退')
    if row['unnamed']:
        row['density_status'] = DENSITY_MISSING
        return row

    _attach_config_defaults(row, row['name'], record, diags)

    if row['state_missing']:
        # config-declared resource with no craft state: amount is unknown
        row['density_status'] = DENSITY_MISSING
        density, density_status, density_raw = _density_evidence(
            row['name'], ksp_index, diags)
        row['density_t_per_unit'] = density
        row['density_raw'] = density_raw
        row['density_status'] = density_status
        config_max = _finite(row['config_default_max_amount_raw'])
        if density_status == DENSITY_RESOLVED and config_max is not None \
                and config_max >= 0:
            row['configured_maximum_mass_t'] = round(
                config_max * density, 12)
        return row

    amount = _finite(row['amount_raw'])
    max_amount = _finite(row['max_amount_raw'])
    if row['amount_raw'] is not None and amount is None:
        _diag(diags, 'invalid_amount',
              f'amount 非法（{row["amount_raw"]!r}）')
    elif amount is not None and amount < 0:
        _diag(diags, 'negative_amount', f'amount 为负（{amount!r}）')
        amount = None
    if row['max_amount_raw'] is not None and max_amount is None:
        _diag(diags, 'invalid_max_amount',
              f'maxAmount 非法（{row["max_amount_raw"]!r}）')
    elif max_amount is not None and max_amount < 0:
        _diag(diags, 'negative_max_amount',
              f'maxAmount 为负（{max_amount!r}）')
        max_amount = None
    if row['max_amount_raw'] is None:
        _diag(diags, 'max_amount_missing',
              '缺少 maxAmount，保存的最大资源质量无法计算')
    if amount is not None and max_amount is not None \
            and amount > max_amount:
        _diag(diags, 'amount_exceeds_max',
              f'amount（{amount}）大于 maxAmount（{max_amount}）')

    density, density_status, density_raw = _density_evidence(
        row['name'], ksp_index, diags)
    row['density_t_per_unit'] = density
    row['density_raw'] = density_raw
    row['density_status'] = density_status
    if density_status == DENSITY_RESOLVED and amount is not None:
        row['current_mass_t'] = round(amount * density, 12)
    if density_status == DENSITY_RESOLVED and max_amount is not None:
        row['maximum_mass_t'] = round(max_amount * density, 12)
    config_max = _finite(row['config_default_max_amount_raw'])
    if density_status == DENSITY_RESOLVED and config_max is not None \
            and config_max >= 0:
        row['configured_maximum_mass_t'] = round(config_max * density, 12)
    return row


def _row_is_resolved(row: dict) -> bool:
    """Single shared implementation: fully usable craft resource state."""
    if row.get('state_missing') or row.get('unresolved') or row.get('unnamed'):
        return False
    if row.get('density_status') != DENSITY_RESOLVED:
        return False
    amount = _finite(row['amount_raw'])
    if amount is None or amount < 0:
        return False
    max_amount = _finite(row['max_amount_raw'])
    if max_amount is not None and (max_amount < 0
                                   or amount > max_amount):
        return False
    return True


# --------------------------------------------------------------------------
# Per-part evidence
# --------------------------------------------------------------------------

def _evidence_for_part(part: dict, match_instance, ksp_index,
                       provenance=None, config_cache_sha256=None) -> dict:
    diags: List[dict] = []
    base = {
        'craft_index': part.get('index'),
        'part': part.get('part'),
        'base_part': part.get('base_part'),
        'persistent_id': part.get('persistent_id'),
        'match_status': None,
        'canonical_name': '',
        'part_source': None,
        'base_mass_raw': None,
        'base_mass_t': None,
        'mod_mass_present': part.get('mod_mass_present'),
        'mod_mass_raw': part.get('mod_mass_raw'),
        'mod_mass_t': part.get('mod_mass_t'),
        'dynamic_mass_modules': [],
        'module_fuel_tank_options': [],
        'resource_evidence': [],
        'mass_semantics_evidence': None,
        'static_dry_mass_estimate_t': None,
        'current_resource_mass_t': None,
        'saved_maximum_resource_mass_t': None,
        'configured_maximum_resource_mass_t': None,
        'maximum_resource_mass_t': None,
        'current_part_mass_estimate_t': None,
        'contains_launch_clamp': False,
        'possible_non_flight_part': False,
        'resolution_status': MATCH_UNAVAILABLE,
        'confidence': _CONFIDENCE[MATCH_UNAVAILABLE],
        'diagnostics': diags,
    }

    # ---- match gate -------------------------------------------------------
    if match_instance is None:
        _diag(diags, 'match_unavailable', '没有该实例的匹配记录')
        return base
    base['match_status'] = match_instance.get('status')
    base['canonical_name'] = match_instance.get('canonical_name') or ''
    if base['match_status'] not in _ACCEPTED_MATCH_STATUSES:
        _diag(diags, 'match_unavailable',
              f'实例未唯一匹配（{base["match_status"]}），不计算质量')
        return base

    record = _selected_record(ksp_index, match_instance)
    if record is None:
        _diag(diags, 'match_unavailable',
              '匹配记录无法在索引中定位完整定义')
        return base
    base['part_source'] = {
        'parent_url': record.get('parent_url'),
        'cache_order': record.get('cache_order'),
    }
    base['base_mass_raw'] = record.get('mass_raw')
    base['base_mass_t'] = record.get('mass_t')
    modules = record.get('modules') or []
    base['dynamic_mass_modules'] = list(record.get('dynamic_mass_modules')
                                        or [])
    has_dynamic = bool(base['dynamic_mass_modules'])

    base['contains_launch_clamp'] = 'LaunchClamp' in modules
    base['possible_non_flight_part'] = base['contains_launch_clamp']

    # ---- RealChute settlement gate (6B.3C.4A) ------------------------------
    # Authorized parachuteSingle instances use the verified settled mass
    # (caseMass + Sum(ChuteMass)) with the IL float32 order; the
    # ConfigCache base mass and the saved runtime modMass are NOT used.
    # Every other status fails closed (no base mass, no base + modMass).
    settlement = None
    static: Optional[float] = None
    resolution_status: Optional[str] = None
    if base['canonical_name'] == REALCHUTE_CANDIDATE_PART:
        material = realchute_config_material(record)
        material_record = ksp_index.find_material(material) \
            if material and ksp_index is not None else None
        settlement = realchute_part_settlement_evidence(
            craft_part=part, config_record=record,
            material_record=material_record, provenance=provenance,
            config_cache_sha256=config_cache_sha256)
        base['mass_semantics_evidence'] = settlement
        for entry in settlement.get('diagnostics') or []:
            _diag(diags, entry['code'], entry['message'])
        if settlement['status'] == RC_SETTLED:
            static = settlement['settled_part_mass_t']
            resolution_status = ESTIMATED_WITH_MODIFIER
        else:
            static = None
            resolution_status = DYNAMIC_UNRESOLVED

    # ---- base mass (non-RealChute parts, and RealChute gate fallback) -----
    if settlement is None:
        base_mass = _finite(base['base_mass_t'])
        if base_mass is None or base_mass < 0:
            _diag(diags, 'invalid_base_mass',
                  f'索引基础 mass 缺失或非法（{base["base_mass_raw"]!r}），'
                  '不用 0 回退')
            base['resolution_status'] = INVALID_MASS
            base['confidence'] = _CONFIDENCE[INVALID_MASS]
            return base

        # present but unparseable/NaN/inf is an EXPLICIT invalid modifier:
        # never ignored (cannot fall back to base mass as if resolved).
        mod_present = bool(base['mod_mass_present'])
        mod_mass = _finite(base['mod_mass_t'])
        mod_invalid = mod_present and mod_mass is None
        if mod_invalid:
            _diag(diags, 'invalid_mod_mass',
                  f'modMass 显式存在但非法（{base["mod_mass_raw"]!r}），'
                  '修正量不可用，不得退回基础质量')

        if mod_invalid:
            static = None
        elif mod_mass is not None:
            static = base_mass + mod_mass
        elif not has_dynamic:
            static = base_mass
            _diag(diags, 'mod_mass_absent',
                  'craft 没有 modMass 修正证据，仅保留基础质量')
        else:
            _diag(diags, 'dynamic_mass_unresolved',
                  '存在动态质量模块但 modMass 缺失，保存状态质量无法估计')
            static = None

        if mod_invalid:
            if has_dynamic:
                base['resolution_status'] = DYNAMIC_UNRESOLVED
                base['confidence'] = _CONFIDENCE[DYNAMIC_UNRESOLVED]
            else:
                base['resolution_status'] = INVALID_MASS
                base['confidence'] = _CONFIDENCE[INVALID_MASS]
            return base
        if static is not None and static < 0:
            _diag(diags, 'invalid_static_mass',
                  f'静态质量估计为负（{static!r}），判定无效')
            base['resolution_status'] = INVALID_MASS
            base['confidence'] = _CONFIDENCE[INVALID_MASS]
            return base

    # ---- resources --------------------------------------------------------
    rows = _collect_craft_rows(part)
    rows = _append_config_default_only_rows(rows, record, diags)
    for row in rows:
        _compute_row(row, ksp_index, record)
    base['resource_evidence'] = rows
    # ModuleFuelTanks TANK templates are audit evidence only: they never
    # prove installed resources, never create rows and never affect totals.
    base['module_fuel_tank_options'] = _all_module_fuel_tank_options(record)

    if rows and all(_row_is_resolved(r) for r in rows):
        base['current_resource_mass_t'] = round(
            sum(r['current_mass_t'] for r in rows), 12)
    if rows and all(r.get('maximum_mass_t') is not None for r in rows):
        base['saved_maximum_resource_mass_t'] = round(
            sum(r['maximum_mass_t'] for r in rows), 12)
    if rows and all(r.get('configured_maximum_mass_t') is not None
                    for r in rows):
        base['configured_maximum_resource_mass_t'] = round(
            sum(r['configured_maximum_mass_t'] for r in rows), 12)
    if not rows:
        # Neither the config nor the craft declare any resource state:
        # zero is exact.
        base['current_resource_mass_t'] = 0.0
        base['saved_maximum_resource_mass_t'] = 0.0
        base['maximum_resource_mass_t'] = 0.0
    else:
        # compatibility alias: craft-saved capacity (never config capacity)
        base['maximum_resource_mass_t'] = \
            base['saved_maximum_resource_mass_t']
        if any(r.get('state_missing') for r in rows):
            missing_names = sorted({r['name'] for r in rows
                                    if r.get('state_missing')})
            _diag(diags, 'resource_state_missing',
                  f'配置声明但 craft 状态缺失的资源: {", ".join(missing_names)}')
        unresolved = sorted({r['name'] for r in rows
                             if not _row_is_resolved(r)})
        if unresolved:
            _diag(diags, 'resource_unresolved',
                  f'资源行未全部解析: {", ".join(unresolved)}')

    base['static_dry_mass_estimate_t'] = static
    if static is not None and base['current_resource_mass_t'] is not None:
        base['current_part_mass_estimate_t'] = round(
            static + base['current_resource_mass_t'], 12)

    # ---- resolution status -------------------------------------------------
    if settlement is not None:
        # parachuteSingle gate outcome decides (settled -> estimated with
        # modifier; everything else -> dynamic_unresolved)
        base['resolution_status'] = resolution_status
    elif static is None and has_dynamic:
        base['resolution_status'] = DYNAMIC_UNRESOLVED
    elif rows and not all(_row_is_resolved(r) for r in rows):
        base['resolution_status'] = RESOURCE_UNRESOLVED
    elif has_dynamic:
        base['resolution_status'] = ESTIMATED_WITH_MODIFIER
    else:
        base['resolution_status'] = RESOLVED_STATIC
    base['confidence'] = _CONFIDENCE[base['resolution_status']]
    if settlement is None and base['resolution_status'] == RESOLVED_STATIC \
            and not mod_present:
        base['confidence'] = 'medium'
    if any(r['fallback'] for r in rows) and base['confidence'] == 'high':
        base['confidence'] = 'medium'
    return base


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def extract_part_mass_evidence(craft_draft, ksp_index,
                               installation_match=None, provenance=None,
                               config_cache_sha256=None) -> dict:
    """Per-instance mass evidence for every PART of a craft draft.

    ``installation_match`` may be the phase 6B.2 report (reused as-is);
    when omitted it is recomputed deterministically via
    :func:`server.ksp_part_matcher.match_craft_parts`.

    ``provenance`` is the runtime fingerprint provider output
    (:func:`server.ksp_runtime_fingerprints.ksp_runtime_provenance`),
    read ONCE by the caller; ``config_cache_sha256`` is the ConfigCache
    SHA-256 (``provenance['config_cache_sha256']`` when omitted).  Both
    gate the RealChute settlement path; missing/mismatched fingerprints
    fail closed (``dynamic_unresolved`` for parachuteSingle).  Inputs are
    never mutated; output is JSON-serializable and deterministic.
    """
    if installation_match is None:
        from .ksp_part_matcher import match_craft_parts
        installation_match = match_craft_parts(craft_draft, ksp_index)
    if config_cache_sha256 is None:
        config_cache_sha256 = (provenance or {}).get(
            'config_cache_sha256')

    by_index = {record['craft_index']: record
                for record in installation_match.get('instances') or []}
    instances = []
    for part in (craft_draft or {}).get('parts') or []:
        instances.append(_evidence_for_part(
            part, by_index.get(part.get('index')), ksp_index,
            provenance=provenance,
            config_cache_sha256=config_cache_sha256))

    counts = {status: 0 for status in _ALL_STATUSES}
    for instance in instances:
        counts[instance['resolution_status']] += 1
    total = len(instances)
    resolved = counts[RESOLVED_STATIC] + counts[ESTIMATED_WITH_MODIFIER]
    unresolved = total - resolved
    # "evidence_instances" = instances that produced evidence records
    # (a unique match existed); it does NOT mean everything is resolved.
    evidence = (counts[RESOLVED_STATIC] + counts[ESTIMATED_WITH_MODIFIER]
                + counts[DYNAMIC_UNRESOLVED] + counts[RESOURCE_UNRESOLVED])

    def count_rows(predicate):
        return sum(1 for instance in instances
                   for row in instance['resource_evidence']
                   if predicate(row))

    total_resource_lines = count_rows(lambda row: True)
    resolved_lines = count_rows(_row_is_resolved)
    missing_density = count_rows(
        lambda r: r['density_status'] == DENSITY_MISSING)
    ambiguous_density = count_rows(
        lambda r: r['density_status'] == DENSITY_AMBIGUOUS)
    invalid_density = count_rows(
        lambda r: r['density_status'] == DENSITY_INVALID)
    mirrored_lines = count_rows(lambda r: r.get('mirrored'))
    config_default_only = count_rows(
        lambda r: r['source'] == SOURCE_CONFIG_DEFAULT_ONLY)
    state_missing_lines = count_rows(lambda r: r.get('state_missing'))
    duplicate_lines = count_rows(
        lambda r: any(d['code'] in ('duplicate_craft_resource',
                                    'duplicate_tank_resource')
                      for d in r['diagnostics']))
    tank_mismatch_lines = count_rows(lambda r: r.get('tank_mismatch'))
    non_flight = sum(1 for instance in instances
                     if instance.get('possible_non_flight_part'))

    return {
        'schema_version': SCHEMA_VERSION,
        'source_kind': SOURCE_KIND,
        'index_fingerprint': installation_match.get('index_fingerprint'),
        'provenance': {
            'config_cache_sha256': config_cache_sha256,
            'realchute_dll_sha256': (provenance or {}).get(
                'realchute_dll_sha256'),
        },
        'total_instances': total,
        'resolved_instances': resolved,
        'unresolved_instances': unresolved,
        'evidence_instances': evidence,
        'resolved_static_instances': counts[RESOLVED_STATIC],
        'estimated_with_modifier_instances': counts[ESTIMATED_WITH_MODIFIER],
        'dynamic_unresolved_instances': counts[DYNAMIC_UNRESOLVED],
        'resource_unresolved_instances': counts[RESOURCE_UNRESOLVED],
        'match_unavailable_instances': counts[MATCH_UNAVAILABLE],
        'invalid_mass_instances': counts[INVALID_MASS],
        'total_resource_lines': total_resource_lines,
        'resolved_resource_lines': resolved_lines,
        'missing_density_lines': missing_density,
        'ambiguous_density_lines': ambiguous_density,
        'invalid_density_lines': invalid_density,
        'mirrored_resource_lines': mirrored_lines,
        'config_default_only_lines': config_default_only,
        'resource_state_missing_lines': state_missing_lines,
        'duplicate_resource_lines': duplicate_lines,
        'tank_mismatch_lines': tank_mismatch_lines,
        'possible_non_flight_instances': non_flight,
        'instances': instances,
    }
