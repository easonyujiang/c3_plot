#!/usr/bin/env python
"""
KSP controlled mass experiment (phase 6B.3C.3A-R).
====================================================

Read-only tooling for a MINIMAL user-run mass experiment that breaks the
``parachuteSingle`` / ``mk1pod.v2`` collinearity of the existing 20
samples:

    M0_control                  control core only
    M1_one_chute_default        M0 + one default parachuteSingle
    M2_two_chutes_default       M0 + two independent default parachuteSingle
    M3_one_chute_changed_diameter  M1 with only the deployed diameter
                                   changed

The four crafts are built MANUALLY by the user inside one standalone KSP
save; this tool NEVER writes to KSP, never starts KSP, never fabricates
craft/loadmeta files and never modifies the existing VAB samples.

Commands:

    prepare [--control-core PART] --ksp-root PATH [--output DIR]
        read-only KSP/assembly/config checks, DETERMINISTIC safe
        control-core selection (a single unique selected core bound into
        the manifest), Chinese capture instructions and an EMPTY data
        intake directory.  Refuses to overwrite a manifest/instructions
        when captured files are already present.  Final status:
        awaiting_ksp_capture.

    analyze  --input DIR [--ksp-root PATH] [--json]
        strict validation of the four captured craft/loadmeta pairs
        (pairing, control core, symmetry, variable isolation, provenance
        fingerprints, input SHA-256 evidence list) and paired-difference
        analysis against three mass models.  Only runs when all four
        pairs are present; any validation failure yields
        experiment_status = invalid_design with explicit machine-readable
        codes and NO support/refute verdicts.

All tolerances come from the experiment manifest with an explicit
rounding-error derivation (loadmeta resolution 1e-7 t) and are validated
fail-closed (unknown/missing/illegal fields are rejected).  JSON output
is deterministic and written only to stdout with --json.  Exit codes:
analyzed / awaiting_ksp_capture -> 0; invalid_design / invalid_setup /
invalid_experiment_setup -> 2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

SCHEMA_VERSION = 3
EXPERIMENT_NAME = 'control_mass_parachute'

SERIALIZATION_MODEL = 'ieee754_binary32_round_trip'

CRAFT_NAMES = (
    'M0_control',
    'M1_one_chute_default',
    'M2_two_chutes_default',
    'M3_one_chute_changed_diameter',
)

CHUTE_COUNTS = {
    'M0_control': 0,
    'M1_one_chute_default': 1,
    'M2_two_chutes_default': 2,
    'M3_one_chute_changed_diameter': 1,
}

PARACHUTE_PART = 'parachuteSingle'

# Mass modifiers that must NEVER appear in the experiment crafts (the
# planned parachute modules RealChuteModule / ProceduralChute are allowed
# on parachuteSingle only).
FORBIDDEN_MODULES = frozenset({
    'ModuleFuelTanks',
    'SSTUProceduralDecoupler',
    'ProceduralPart',
    'SSTUModularEngineCluster',
    'SSTUModularFuelTank',
    'TweakScale',
    'B9PartSwitch',
    'InterstellarFuelSwitch',
    'ModulePartVariants',
})

# ---------------------------------------------------------------------------
# Numerical precision model (schema v3)
#
# The fixed "7-decimal loadmeta" assumption is ABANDONED: real KSP
# .loadmeta files print totalMass with 5..9 fractional digits.  The error
# model instead assumes the stored value is an IEEE-754 binary32 value
# serialized via a round-tripping decimal conversion and derives bounds
# DYNAMICALLY from the magnitude of each parsed value:
#
#   * for a parsed value v, the representable neighbours are the adjacent
#     binary32 values lower/upper around v (computed via float32
#     nextafter);
#   * the single-value rounding bound is the max distance from v to its
#     neighbours (half the ULP interval; for exactly representable v the
#     full spacing is taken — conservative);
#   * M1-M0  : bound = b(M1) + b(M0)
#   * M2-2M1+M0 : bound = b(M2) + 2*b(M1) + b(M0)   (|coefficients|)
#   * M3-M1  : bound = b(M3) + b(M1)
# The predicted side (float32 chute-mass chain) error is recorded
# separately: |predicted| * predicted_float32_error_factor.
# The final verdict tolerance = dynamic observed bound + predicted error
# + an explicit experiment margin.  The fixed constants
# loadmeta_resolution_t / single_value_max_rounding_error_t / ... were
# removed in schema v3; a manifest carrying them is rejected (unknown
# field) and they never participate in verdicts.
# ---------------------------------------------------------------------------

TOLERANCES = {
    'serialization_model': SERIALIZATION_MODEL,
    'predicted_float32_error_factor': 1.0 / (2 ** 21),
    'experiment_margin_t': 1e-7,
    'diameter_min_delta_m': 2.0,
    'rationale': (
        '已只读检查真实 KSP .loadmeta：totalMass 小数位数在 5–9 位之间'
        '分布，固定七位小数假设不成立，也不能用小数位数统计猜测精度。'
        '假设存储值是 IEEE-754 binary32 并通过可往返的十进制序列化'
        '（serialization_model = ieee754_binary32_round_trip）；对每个'
        '解析值 v 取相邻可表示 binary32 值（下/上邻值）与 ULP，单值'
        '舍入界限 = max(|v-lower|, |upper-v|)（恰好可表示时取全间距，'
        '保守）。差分/线性/直径实验的观测误差界限按系数绝对值传播'
        '（M1-M0：b1+b0；M2-2M1+M0：b2+2b1+b0；M3-M1：b3+b1）。'
        '预测端误差单独记录：|预测值| × predicted_float32_error_factor'
        '（2^-21，覆盖 float32 结算链 ≤8 次运算的 2^-24 相对误差与 '
        'float64 往返）。最终判定容差 = 动态观测误差界限 + 预测误差 + '
        '明确的实验裕量 experiment_margin_t。直径变化阈值单位是米'
        '（diameter_min_delta_m）。'),
}

REQUIRED_TOLERANCE_KEYS = frozenset(TOLERANCES.keys())

DIAMETER_GUIDANCE = {
    'min_m': 15.0,
    'max_m': 26.0,
    'avoid_near_m': 20.7,
    'notes': ('在 RealChute UI 的 parachuteSingle 参数中选择一个可保存的'
              '展开直径，明显不同于 M1 的默认值（默认约 20.7 m）；建议在 '
              '15–26 m 范围内选择，避开 20.7 ± 0.5 m。'),
}

STATUS_AWAITING = 'awaiting_ksp_capture'
STATUS_INVALID = 'invalid_design'
STATUS_ANALYZED = 'analyzed'

VERDICT_SUPPORTED = 'supported'
VERDICT_CONTRADICTED = 'contradicted'
VERDICT_INSUFFICIENT = 'insufficient_evidence'

EXIT_OK = 0
EXIT_INVALID = 2


def _finite(value):
    if isinstance(value, bool):
        return None
    if value is None or value == '':
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _normalize_missing(missing):
    """Deduplicate and sort a list of missing-evidence codes."""
    return sorted(set(str(item) for item in (missing or [])))


def _f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def _f32_from_bits(bits):
    return struct.unpack('<f', struct.pack('<I', bits & 0xFFFFFFFF))[0]


def _f32_bits(value):
    return struct.unpack('<I', struct.pack('<f', value))[0]


def _binary32_analysis(value):
    """Binary32 normalization and quantization analysis of a parsed
    decimal totalMass.

    Three numbers are kept separate:

    * ``parsed_decimal_t``         — the decimal string parsed as float64;
    * ``stored_f32_t``             — the normalized binary32 value
      (float32(parsed)); ALL differentials use THIS value;
    * ``decimal_roundtrip_delta_t`` — abs(parsed - stored): the display
      round-trip error, reported separately and NEVER added to the
      quantization bound.

    The quantization error uses the ROUND-TRIP INTERVAL MIDPOINTS: for a
    positive finite binary32 ``stored`` with neighbours ``lower`` /
    ``upper``,

        lower_midpoint = (lower + stored) / 2
        upper_midpoint = (stored + upper) / 2
        quantization_bound_t = max(stored - lower_midpoint,
                                   upper_midpoint - stored)

    i.e. half the adjacent spacing (asymmetric at powers of two and at
    the normal/subnormal boundary).  ``+0`` is handled with the smallest
    denormal neighbour pair; non-finite values, negative values and
    values outside the binary32 range fail closed (None).
    """
    if not math.isfinite(value):
        return None
    if value < 0:
        return None
    try:
        stored = _f32(value)
        bits = _f32_bits(stored)
    except (OverflowError, struct.error):
        return None
    if bits in (0x7F800000, 0xFF800000):  # infinities
        return None
    if bits in (0x00000000, 0x80000000):  # ±0
        lower = -_f32_from_bits(1)
        upper = _f32_from_bits(1)
    else:
        lower = _f32_from_bits(bits - 1)
        upper = _f32_from_bits(bits + 1)
    lower_midpoint = (lower + stored) / 2.0
    upper_midpoint = (stored + upper) / 2.0
    quantization = max(stored - lower_midpoint, upper_midpoint - stored)
    if quantization <= 0 or not math.isfinite(quantization):
        return None
    return {
        'parsed_decimal_t': value,
        'stored_f32_t': stored,
        'decimal_roundtrip_delta_t': abs(value - stored),
        'lower_neighbour_t': lower,
        'upper_neighbour_t': upper,
        'lower_midpoint_t': lower_midpoint,
        'upper_midpoint_t': upper_midpoint,
        'quantization_bound_t': quantization,
    }


def _observed_error_bound(rounding_numbers, coefficients):
    """Propagate single-value quantization bounds with |coefficients|."""
    total = 0.0
    for scenario, coefficient in coefficients:
        entry = rounding_numbers.get(scenario) or {}
        bound = entry.get('quantization_bound_t')
        if bound is None:
            return None
        total += abs(coefficient) * bound
    return total


def _atomic_write(path, text):
    """Write via temp file + os.replace (atomic on the same filesystem)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + '.tmp')
    try:
        temp_path.write_text(text, encoding='utf-8')
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Control core selection
# ---------------------------------------------------------------------------

def _safe_core_candidates(index):
    """SAFE control-core candidates from the PATCHED PART configs.

    Every candidate must:

    * come from the final ConfigCache index;
    * contain ``ModuleCommand``;
    * have a finite, valid, positive fixed base mass;
    * contain NO engine module (``ModuleEngines`` / ``ModuleEnginesFX`` /
      any ``ModuleEngines*`` variant);
    * contain NO module from the CURRENT ``DYNAMIC_MASS_MODULES`` set;
    * contain NO mass-variable module (RealChute / ModuleFuelTanks /
      ProceduralPart / SSTU / PDC / TweakScale / B9PartSwitch / ...);
    * have VALID attachRules (missing / malformed / illegal -> excluded);
    * be physically able to host the experiment parachute: it must have
      ``allowSrfAttach=true`` (radial mounting surface) AND ``allowStack
      =true`` AND at least one non-null ``node_stack_*`` node (the
      parachuteSingle attaches via its bottom stack node, and the
      experiment must work for either attachment path).  Radial-only
      attachment shells such as ``mumech_MJ2_AR202`` (attachRules
      ``0,1,0,0,0``, no stack nodes) are excluded.

    Ranking is EXPERIMENT-STABILITY-FIRST, then mass: normal ``Pods``
    category unmanned probe cores with a top stack node come first;
    the lightest part never overrides physical usability.  The order is
    deterministic: (category != 'Pods', crew_capacity != 0,
    not has_top_stack_node, not has_stack_node, mass, name, parent_url).
    """
    from server.ksp_install_index import DYNAMIC_MASS_MODULES
    candidates = []
    for record in index.parts:
        modules = record.get('modules') or []
        if 'ModuleCommand' not in modules:
            continue
        mass = _finite(record.get('mass_t'))
        if mass is None or mass <= 0:
            continue
        if any(name.startswith('ModuleEngines') for name in modules):
            continue
        if set(modules) & set(DYNAMIC_MASS_MODULES):
            continue
        if set(modules) & FORBIDDEN_MODULES:
            continue
        attach_rules = record.get('attach_rules') or {}
        if not attach_rules.get('valid', False):
            continue
        if not attach_rules.get('allowSrfAttach'):
            continue
        if not attach_rules.get('allowStack'):
            continue
        stack_nodes = sorted(record.get('stack_nodes') or [])
        if not stack_nodes:
            continue
        candidates.append(record)
    candidates.sort(key=lambda item: (
        (item.get('category') or '') != 'Pods',
        _finite(item.get('crew_capacity')) not in (0, None),
        'node_stack_top' not in sorted(item.get('stack_nodes') or []),
        not sorted(item.get('stack_nodes') or []),
        _finite(item.get('mass_t')) or math.inf,
        item.get('name') or '',
        item.get('parent_url') or ''))
    return candidates


def _core_safety_checks(record) -> List[str]:
    """Machine-readable list of safety checks satisfied by a record."""
    attach = record.get('attach_rules') or {}
    checks = [
        'module_command_present',
        'fixed_finite_positive_mass',
        'no_engine_module',
        'no_dynamic_mass_module',
        'no_forbidden_mass_module',
        'valid_attach_rules',
        'allow_surface_attach',
        'allow_stack_attach',
        'has_stack_node',
    ]
    if (record.get('category') or '') == 'Pods':
        checks.append('normal_pods_category')
    if _finite(record.get('crew_capacity')) in (0, None):
        checks.append('unmanned_crew_capacity')
    if 'node_stack_top' in sorted(record.get('stack_nodes') or []):
        checks.append('has_top_stack_node')
    return checks


def _selection_reason(record) -> str:
    """Selection reason describing only the core's OWN verified facts.

    It deliberately does NOT claim any specific parachute installation
    path: with the current parachuteSingle (srfAttach=false) only the
    stack path exists.  The actual per-scenario node assignments are
    emitted by ``_experiment_attachment_plan`` (see attachment_plan in
    the manifest/preflight), not guessed here.
    """
    attach = record.get('attach_rules') or {}
    category = record.get('category') or ''
    nodes = sorted(record.get('stack_nodes') or [])
    text = (
        f'{record.get("name")}（{category} 类，质量 '
        f'{_finite(record.get("mass_t"))} t）：'
        f'attachRules={attach.get("raw")}（allowSrfAttach='
        f'{attach.get("allowSrfAttach")}，allowStack='
        f'{attach.get("allowStack")}）；控制核心自身具有 '
        f'{len(nodes)} 个独立 stack 节点 {nodes}')
    if len(nodes) >= 2:
        text += '；当前 parachuteSingle 组合仅使用 stack 路径，' \
                'M1/M3 用 node_stack_top，M2 分别用 node_stack_top ' \
                '与 node_stack_bottom'
    return text


def _core_summary(record) -> dict:
    """Canonical mass-relevant + installation-relevant summary of a
    patched PART record (used for the manifest hash)."""
    attach = record.get('attach_rules') or {}
    return {
        'name': record.get('name'),
        'title': record.get('title'),
        'mass_raw': record.get('mass_raw'),
        'mass_t': record.get('mass_t'),
        'parent_url': record.get('parent_url'),
        'category': record.get('category'),
        'attach_rules': {
            key: attach.get(key)
            for key in ('stack', 'srfAttach', 'allowStack',
                        'allowSrfAttach', 'allowCollision')},
        'bulkhead_profiles': sorted(record.get('bulkhead_profiles') or []),
        'stack_nodes': sorted(record.get('stack_nodes') or []),
        'attach_node': bool(record.get('attach_node')),
        'crew_capacity': record.get('crew_capacity'),
        'modules': sorted(record.get('modules') or []),
        'resources': sorted(
            [{'name': r.get('name'), 'amount': r.get('amount'),
              'max_amount': r.get('max_amount')}
             for r in record.get('resources') or []],
            key=lambda item: item['name']),
    }


def _core_summary_sha256(record) -> str:
    canonical = json.dumps(_core_summary(record), ensure_ascii=False,
                           sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _experiment_attachment_plan(core_record, chute_record):
    """Composite attachment plan for the FOUR-scenario experiment.

    The plan is decided by BOTH parts together — never by the core's own
    flags alone:

    * surface_path = chute.attach_rules.srfAttach
                    AND core.attach_rules.allowSrfAttach
    * stack_path   = chute.attach_rules.stack
                    AND core.attach_rules.allowStack
                    AND the core has non-null node_stack_* nodes

    M2 needs TWO INDEPENDENT parachutes: with the surface path two
    independent surface mounts are possible; with the stack path only the
    core can supply them, so at least two distinct stack nodes are
    required (never chute-to-chute daisy chaining).  M1 and M3 use the
    SAME primary node (node_stack_top when present, else the first ordered
    node); M2 uses the primary plus the next distinct node.

    Returns ``{'mode', 'surface_path_available', 'stack_path_available',
    'supports_experiment', 'M1_one_chute_default', 'M2_two_chutes_default',
    'M3_one_chute_changed_diameter', 'reasons'}``.
    """
    core_attach = (core_record or {}).get('attach_rules') or {}
    chute_attach = (chute_record or {}).get('attach_rules') or {}
    ordered_nodes = list((core_record or {}).get('stack_nodes_ordered')
                         or [])
    reasons = []
    surface = bool(chute_attach.get('srfAttach')
                   and core_attach.get('allowSrfAttach'))
    stack = bool(chute_attach.get('stack')
                 and core_attach.get('allowStack')
                 and ordered_nodes)
    if surface:
        mode = 'surface'
        m1 = ['surface']
        m3 = ['surface']
        m2 = ['surface', 'surface']
        supports = True
        reasons.append('surface 路径可用（chute.srfAttach 且 '
                       'core.allowSrfAttach）：可关闭对称后独立放置两个')
    elif stack:
        mode = 'stack'
        primary = 'node_stack_top' if 'node_stack_top' in ordered_nodes \
            else ordered_nodes[0]
        others = [node for node in ordered_nodes if node != primary]
        m1 = [primary]
        m3 = [primary]
        if others:
            m2 = [primary, others[0]]
            supports = True
            reasons.append(
                f'stack 路径可用：M1/M3 使用 {primary}，M2 使用 '
                f'{primary} 与 {others[0]}（两个独立节点，不串联）')
        else:
            m2 = None
            supports = False
            reasons.append(
                f'stack 路径可用但控制核心只有 {ordered_nodes} 一个'
                f'独立节点，无法独立安装两个降落伞（M2 不可行）')
    else:
        mode = 'none'
        m1 = None
        m3 = None
        m2 = None
        supports = False
        reasons.append('径向路径与 stack 路径均不可用')
    return {
        'mode': mode,
        'surface_path_available': surface,
        'stack_path_available': stack,
        'supports_experiment': supports,
        'M1_one_chute_default': m1,
        'M2_two_chutes_default': m2,
        'M3_one_chute_changed_diameter': m3,
        'reasons': reasons,
    }


def _canonical_part_map(craft_draft, index):
    """Canonical part-name resolution reusing ``ksp_part_matcher``.

    Strategy (unchanged from the existing matcher): exact name first,
    then ``.`` -> ``_`` normalization; a unique candidate is required
    (``normalized_unique``), several candidates are ``ambiguous`` and no
    candidate is ``missing`` — both fail closed.  Raw base names are
    preserved alongside the canonical names.

    Returns ``{craft_index: {'raw_base', 'canonical', 'status',
    'strategy', 'diagnostics'}}``.
    """
    from server.ksp_part_matcher import match_craft_parts
    report = match_craft_parts(craft_draft, index)
    result = {}
    for instance in report.get('instances', []):
        result[instance.get('craft_index')] = {
            'raw_base': instance.get('base_part'),
            'canonical': instance.get('canonical_name'),
            'status': instance.get('status'),
            'strategy': instance.get('strategy'),
            'diagnostics': instance.get('diagnostics') or [],
        }
    return result


def _canonical_name(canonical_map, part):
    info = canonical_map.get(part.get('index')) or {}
    return info.get('canonical') or None


def _normalize_stack_topology(craft_draft, canonical_map, core_canonical):
    """Stack connection topology from the CORE's OWN attN rows.

    The core's attN rows carry the core node name in the FIRST field
    (``top`` / ``bottom``); the trailing ``_<n>`` of the target is the
    target part's node index and is NEVER used to infer the core node.
    ``Null_0_0`` targets are ignored.  The chute's reciprocal attN is
    used only to CONFIRM it connects back to the core (and to detect
    chute-to-chute daisy chaining).

    Returns ``{'core_present', 'assignments', 'chute_to_chute',
    'unconnected'}`` where each assignment is ``{'chute_instance',
    'core_node'}``.
    """
    parts = list((craft_draft or {}).get('parts') or [])
    instance_to_canonical = {}
    for part in parts:
        canonical = _canonical_name(canonical_map, part)
        if canonical:
            instance_to_canonical[part.get('part')] = canonical
    core_part = next(
        (part for part in parts
         if _canonical_name(canonical_map, part) == core_canonical),
        None)
    if core_part is None:
        return {'core_present': False, 'assignments': [],
                'chute_to_chute': [], 'unconnected': []}
    node_names = {'top': 'node_stack_top', 'bottom': 'node_stack_bottom'}
    assignments = []
    for attn in (core_part.get('connections') or {}).get('attN') or []:
        head = str(attn).split('|')[0]
        fields = head.split(',')
        if len(fields) < 2:
            continue
        core_node_raw = fields[0]
        target = fields[1]
        target_instance = target.rpartition('_')[0] if '_' in target \
            else target
        target_canonical = instance_to_canonical.get(target_instance)
        if not target_canonical or target_canonical in ('Null', 'null'):
            continue  # ignore Null_0_0
        if target_canonical != PARACHUTE_PART:
            continue
        core_node = node_names.get(core_node_raw,
                                   'node_stack_' + core_node_raw)
        assignments.append({'chute_instance': target_instance,
                            'core_node': core_node})
    chute_to_chute = []
    unconnected = []
    for part in parts:
        if _canonical_name(canonical_map, part) != PARACHUTE_PART:
            continue
        targets = []
        for attn in (part.get('connections') or {}).get('attN') or []:
            head = str(attn).split('|')[0]
            fields = head.split(',')
            if len(fields) < 2:
                continue
            target = fields[1]
            target_instance = target.rpartition('_')[0] if '_' in target \
                else target
            target_canonical = instance_to_canonical.get(target_instance)
            if not target_canonical or target_canonical in ('Null',
                                                            'null'):
                continue
            targets.append(target_canonical)
        if any(canonical == PARACHUTE_PART for canonical in targets):
            chute_to_chute.append(part.get('part'))
        if not any(canonical == core_canonical for canonical in targets) \
                and not any(assignment['chute_instance'] == part.get('part')
                            for assignment in assignments):
            unconnected.append(part.get('part'))
    return {'core_present': True, 'assignments': assignments,
            'chute_to_chute': chute_to_chute, 'unconnected': unconnected}


def _select_control_core(index, part_name=None):
    """Deterministic safe control-core selection.

    Returns ``(selected_record, candidates, diagnostics)``.  ``part_name``
    given but missing / failing the safety filters -> the selection FAILS
    (no silent fallback).  Without ``part_name`` the top ranked safe
    candidate is used.
    """
    candidates = _safe_core_candidates(index)
    diagnostics = []
    if not candidates:
        return None, [], [{
            'code': 'no_safe_control_core',
            'message': '补丁后配置中没有满足全部安全条件的控制核心'
                       '（ModuleCommand + 固定基础质量 + 无发动机 + '
                       '无质量可变模块 + allowSrfAttach + allowStack + '
                       'stack 节点）',
        }]
    if part_name:
        for record in candidates:
            if record.get('name') == part_name:
                return record, candidates, diagnostics
        return None, candidates, [{
            'code': 'control_core_not_safe_or_missing',
            'message': f'指定的控制核心 {part_name!r} 不在安全候选列表'
                       f'（部件缺失、无 ModuleCommand、质量非法、含'
                       f'发动机、含质量可变模块或不满足物理可安装条件'
                       f'都会导致被排除）',
        }]
    return candidates[0], candidates, diagnostics


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

def _fingerprints(ksp_root):
    result = {'ksp_root': str(Path(ksp_root))}
    cache = Path(ksp_root) / 'GameData' / 'ModuleManager.ConfigCache'
    result['config_cache_path'] = str(cache)
    result['config_cache_sha256'] = _file_sha256(cache) \
        if cache.is_file() else None
    result['dlls'] = {}
    for name, rel in (('RealChute',
                       'GameData/RealChute/Plugins/RealChute.dll'),
                      ('RealFuels',
                       'GameData/RealFuels/Plugins/RealFuels.dll')):
        path = Path(ksp_root) / rel
        result['dlls'][name] = {
            'path': str(path),
            'sha256': _file_sha256(path) if path.is_file() else None,
        }
    return result


def _validate_tolerances(tolerances):
    """Fail-closed tolerance validation.  Returns a list of error codes."""
    errors = []
    if not isinstance(tolerances, dict):
        return ['tolerances_not_object']
    unknown = sorted(set(tolerances) - REQUIRED_TOLERANCE_KEYS)
    if unknown:
        errors.append(f'tolerances_unknown_field:{",".join(unknown)}')
    missing = sorted(REQUIRED_TOLERANCE_KEYS - set(tolerances))
    if missing:
        errors.append(f'tolerances_missing_field:{",".join(missing)}')
    for key in REQUIRED_TOLERANCE_KEYS:
        if key not in tolerances:
            continue
        value = tolerances[key]
        if key == 'rationale':
            if not isinstance(value, str) or not value:
                errors.append('tolerances_rationale_invalid')
            continue
        if key == 'serialization_model':
            if value != SERIALIZATION_MODEL:
                errors.append('tolerances_serialization_model_unsupported')
            continue
        number = _finite(value)
        if number is None or number <= 0:
            errors.append(f'tolerances_illegal_value:{key}')
    return errors


def prepare_experiment(ksp_root, experiment_dir, index=None,
                       control_core=None, created_at=None,
                       backfill_migration_history=None) -> dict:
    """Prepare the experiment workspace (read-only w.r.t. KSP).

    Returns ``{'status': 'awaiting_ksp_capture', ...}``.  Creates the
    manifest (schema v3), the Chinese capture instructions and the EMPTY
    input directory.  PROVENANCE GUARDS:

    * any captured file already inside ``input/`` -> REFUSE to overwrite
      the manifest/instructions (migration and history backfill
      included);
    * an existing manifest that FAILS to parse (corrupt JSON) -> REFUSE,
      never silently overwritten;
    * an existing CURRENT-schema manifest with a DIFFERENT KSP fingerprint
      or selected control core -> REFUSE (no silent overwrite);
    * an OLD-schema manifest with EMPTY input -> MIGRATED to the current
      schema and an append-only ``migration_history`` entry records
      from_schema / to_schema / old and new control cores;
    * ``backfill_migration_history`` (list of records) may be appended
      ONLY when input is empty and the manifest has no history yet — used
      for the ONE-TIME repair of the audited schema-2->3 migration that
      lost its markers; it never fabricates other history;
    * a fresh run and an idempotent re-run (same fingerprint + same core
      + empty input) produce the same manifest bytes (created_at and
      migration_history kept).
    """
    ksp_root = Path(ksp_root)
    experiment_dir = Path(experiment_dir)
    from server.ksp_install_index import build_ksp_index
    if index is None:
        index = build_ksp_index(ksp_root)
        if index.status != 'ok':
            return {'status': 'invalid_setup',
                    'error': f'KSP 索引构建失败（status={index.status}）'}
    fingerprints = _fingerprints(ksp_root)
    if not fingerprints['config_cache_sha256']:
        return {'status': 'invalid_setup',
                'error': '未找到 ModuleManager.ConfigCache'}

    selected, candidates, diagnostics = _select_control_core(
        index, part_name=control_core)
    if selected is None:
        return {'status': 'invalid_setup',
                'error': diagnostics[0]['message'],
                'diagnostics': diagnostics}

    input_dir = experiment_dir / 'input'
    if input_dir.is_dir():
        captured = sorted(
            p.name for p in input_dir.iterdir()
            if p.is_file() and p.name not in ('README.txt',))
        if captured:
            return {'status': 'input_not_empty',
                    'error': f'input/ 已包含采集文件，拒绝覆盖或迁移 '
                             f'manifest/instructions：{captured}'}

    manifest_path = experiment_dir / 'manifest.json'
    existing = None
    if manifest_path.is_file():
        try:
            existing = json.loads(
                manifest_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return {'status': 'manifest_corrupt',
                    'error': f'现有 manifest.json 存在但无法解析（损坏），'
                             f'拒绝覆盖；请手动处理或删除后重新 prepare：'
                             f'{manifest_path}'}
    migrated_from_schema = None
    migrated_control_core = None
    migration_history = []
    if existing:
        migration_history = list(existing.get('migration_history') or [])
        existing_schema = existing.get('schema_version')
        if not isinstance(existing_schema, int):
            return {'status': 'invalid_setup',
                    'error': '现有 manifest 缺少合法的 schema_version，'
                             '拒绝覆盖'}
        if existing_schema > SCHEMA_VERSION:
            return {'status': 'invalid_setup',
                    'error': f'现有 manifest schema_version='
                             f'{existing_schema} 高于当前'
                             f'（{SCHEMA_VERSION}），拒绝覆盖'}
        if existing_schema == SCHEMA_VERSION:
            if (existing.get('ksp', {}).get('config_cache_sha256')
                    != fingerprints['config_cache_sha256']):
                return {'status': 'manifest_conflict',
                        'error': '现有 manifest 的 KSP 指纹与当前安装'
                                 '不一致，拒绝覆盖；请先清空 input/ 并'
                                 '手动处理旧 manifest'}
            old_core = existing.get('selected_control_core') or {}
            if old_core.get('name') != selected.get('name'):
                return {'status': 'manifest_conflict',
                        'error': '现有 manifest 绑定的控制核心与本次选择'
                                 '不同，拒绝覆盖；请先清空 input/ 并手动'
                                 '处理旧 manifest'}
            # idempotent re-run: keep the migration provenance markers
            migrated_from_schema = existing.get('migrated_from_schema')
            migrated_control_core = existing.get('migrated_control_core')
            # one-time backfill of an audited migration record: only when
            # input is EMPTY (guaranteed above) and no history exists yet
            if backfill_migration_history and not migration_history:
                migration_history = [dict(record) for record in
                                     backfill_migration_history]
        else:
            # older schema + EMPTY input (guaranteed above): safe migration
            migrated_from_schema = existing_schema
            migrated_control_core = (
                (existing.get('selected_control_core') or {}).get('name'))
            migration_history.append({
                'from_schema': existing_schema,
                'to_schema': SCHEMA_VERSION,
                'old_control_core': migrated_control_core,
                'new_control_core': selected.get('name'),
                'reason': ('old core cannot host experiment chute'
                           if migrated_control_core != selected.get('name')
                           else 'schema upgrade'),
            })

    selected_entry = {
        'name': selected.get('name'),
        'title': selected.get('title'),
        'mass_t': selected.get('mass_t'),
        'parent_url': selected.get('parent_url'),
        'category': selected.get('category'),
        'attach_rules': selected.get('attach_rules') or {},
        'bulkhead_profiles': sorted(
            selected.get('bulkhead_profiles') or []),
        'stack_nodes': sorted(selected.get('stack_nodes') or []),
        'crew_capacity': selected.get('crew_capacity'),
        'modules': sorted(selected.get('modules') or []),
        'resources': [
            {'name': r.get('name'), 'amount': r.get('amount'),
             'max_amount': r.get('max_amount')}
            for r in selected.get('resources') or []],
        'safety_checks': _core_safety_checks(selected),
        'selection_reason': _selection_reason(selected),
        'sha256': _core_summary_sha256(selected),
    }
    short_candidates = [
        {'name': record.get('name'), 'title': record.get('title'),
         'mass_t': record.get('mass_t'),
         'category': record.get('category')}
        for record in candidates[:5]
    ]
    chute_record = index.primary_part(PARACHUTE_PART)
    attachment_plan = _experiment_attachment_plan(selected, chute_record)

    manifest = {
        'schema_version': SCHEMA_VERSION,
        'experiment_name': EXPERIMENT_NAME,
        'status': STATUS_AWAITING,
        'created_at': (created_at or
                       (existing or {}).get('created_at') or
                       datetime.now(timezone.utc).isoformat()),
        'migrated_from_schema': migrated_from_schema,
        'migrated_control_core': migrated_control_core,
        'migration_history': migration_history,
        'ksp': fingerprints,
        'selected_control_core': selected_entry,
        'control_core_candidates': short_candidates,
        'attachment_plan': attachment_plan,
        'craft_specs': {
            name: {'chute_count': CHUTE_COUNTS[name],
                   'notes': _CRAFT_NOTES[name]}
            for name in CRAFT_NAMES
        },
        'diameter_guidance': DIAMETER_GUIDANCE,
        'tolerances': TOLERANCES,
        'input_layout': {
            'directory': 'input',
            'expected_files': [
                f'{name}.craft' for name in CRAFT_NAMES
            ] + [f'{name}.loadmeta' for name in CRAFT_NAMES],
            'notes': ('把 KSP 生成的 .craft 与同名 .loadmeta 复制到 '
                      'input/ 目录；两两成对，共 8 个文件。'),
        },
    }
    experiment_dir.mkdir(parents=True, exist_ok=True)
    input_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(manifest_path,
                  json.dumps(manifest, ensure_ascii=False, indent=1))
    _atomic_write(experiment_dir / 'instructions.md',
                  _instructions_text(manifest, experiment_dir))

    return {
        'status': STATUS_AWAITING,
        'experiment_dir': str(experiment_dir),
        'manifest_path': str(manifest_path),
        'input_dir': str(input_dir),
        'selected_control_core': selected_entry,
        'control_core_candidates': short_candidates,
        'attachment_plan': attachment_plan,
        'ksp_fingerprints': fingerprints,
        'migrated_from_schema': migrated_from_schema,
        'migrated_control_core': migrated_control_core,
        'migration_history': migration_history,
    }


_CRAFT_NOTES = {
    'M0_control': '只有一个最小控制核心（必须与 manifest 绑定的'
                  'selected_control_core 完全一致）；不得包含 '
                  'ModuleFuelTanks、RealChute、ProceduralParts、PDC 或'
                  '其他质量 modifier；推进剂和可变资源必须为空或在四份 '
                  'craft 中完全一致。',
    'M1_one_chute_default': '在 M0 基础上只增加一个 parachuteSingle：'
                            '关闭对称、使用默认 RealChute 参数；保存后'
                            '退出并重新加载一次，再保存；不修改其他部件'
                            '和资源。',
    'M2_two_chutes_default': '在 M0 基础上增加两个彼此独立的默认 '
                             'parachuteSingle：不使用对称复制，两个实例'
                             '参数一致；除此之外与 M0 完全相同。',
    'M3_one_chute_changed_diameter': '从 M1 复制，只修改 RealChute 的展开'
                                     '直径：直径必须与 M1 明显不同（建议 '
                                     '15–26 m，避开 20.7 ± 0.5 m）；不得'
                                     '修改 caseMass、材料、根部件、资源或'
                                     '其他参数；实际直径必须能从保存后的 '
                                     'craft 中重新读取。',
}


def _instructions_text(manifest, experiment_dir=None):
    core = manifest.get('selected_control_core') or {}
    lines = [
        '# KSP 受控质量实验——人工采集步骤',
        '',
        f'实验目录：{experiment_dir or "（见 prepare 输出）"}',
        '',
        '## 为什么需要这个实验',
        '',
        '现有 20 个 VAB 样本中 parachuteSingle 与 mk1pod.v2 的计数完全共线，',
        '无法把 +0.075 t 残差归因到两者之一。本实验用最小载具逐一改变降落伞',
        '数量与直径，用配对差分打破共线性。',
        '',
        '## 绑定的控制核心（唯一，不得更换）',
        '',
        f'- 部件：{core.get("name")}',
        f'- 标题：{core.get("title") or ""}',
        f'- 补丁后质量：{core.get("mass_t")} t',
        f'- 类别：{core.get("category") or ""}',
        f'- attachRules：{core.get("attach_rules", {}).get("raw")}',
        f'- stack 节点：{core.get("stack_nodes") or []}',
        f'- 选择理由：{core.get("selection_reason") or ""}',
        f'- 配置摘要 SHA-256：{core.get("sha256")}',
        '',
        '四份 craft 的根部件都必须等于这个控制核心；更换核心会被 analyze '
        '判为 invalid_design。',
        '',
        '## 第 0 步：准备独立存档',
        '',
        '1. 在 KSP 主菜单新建一个独立存档（或在独立复制的 KSP 安装中进行），',
        '   四份 craft 必须在同一个存档中手动搭建；',
        '2. 确保该存档使用与 prepare 相同的模组与 ModuleManager 配置。',
        '',
        '## 第 1 步：M0_control',
        '',
        '1. 在 VAB 中把绑定的控制核心作为根部件放下；',
        '2. 不添加任何其他部件；推进剂与可变资源为空（或在四份 craft 中'
        '完全一致）；',
        '3. 保存为 M0_control。',
        '',
        '## 第 2 步：M1_one_chute_default',
        '',
        '1. 加载 M0_control，把 parachuteSingle 安装到控制核心的'
        ' node_stack_top 节点（当前组合为 stack 路径，径向路径不可用）；',
        '2. 关闭对称（单一实例）；不修改任何 RealChute 参数（保持默认）；',
        '3. 保存 → 退出 VAB → 重新加载该 craft → 再保存一次，文件名为 '
        'M1_one_chute_default；',
        '4. 不得修改根部件、资源或其他部件。',
        '',
        '## 第 3 步：M2_two_chutes_default',
        '',
        '1. 加载 M0_control，添加两个 parachuteSingle；',
        '2. 不使用对称复制：逐个放置两个独立实例，参数一致（均默认）；',
        '3. 一个安装到 node_stack_top，另一个安装到 node_stack_bottom'
        '（两个独立节点，各挂一个；禁止把第二个挂到第一个降落伞上）；',
        '4. 保存为 M2_two_chutes_default。',
        '',
        '## 第 4 步：M3_one_chute_changed_diameter',
        '',
        '1. 复制 M1（加载 M1 后立即另存为 M3_one_chute_changed_diameter）；',
        f"2. 在 RealChute UI 中把展开直径改为与 M1 明显不同的值"
        f"（建议 {DIAMETER_GUIDANCE['min_m']}–{DIAMETER_GUIDANCE['max_m']} m，"
        f"避开 {DIAMETER_GUIDANCE['avoid_near_m']} ± 0.5 m）；",
        '3. 不得修改 caseMass、材料、根部件、资源或其他参数；',
        '4. 保存后打开 craft 文件，确认 ProceduralChute 的 depDiam 已更新。',
        '',
        '## 第 5 步：找到 KSP 生成的 craft 与 loadmeta',
        '',
        'KSP 把 craft 与 loadmeta 保存在存档目录：',
        '',
        '    <KSP>/saves/<存档名>/Ships/VAB/',
        '',
        '每个 craft 对应一个 .craft 与同名 .loadmeta（loadmeta 在保存/'
        '飞行时由 KSP 生成）。',
        '',
        '## 第 6 步：复制到实验输入目录',
        '',
        '把下列 8 个文件复制到 input/ 目录（本仓库内）：',
        '',
    ]
    for name in CRAFT_NAMES:
        lines.append(f'- {name}.craft 与 {name}.loadmeta')
    lines += [
        '',
        '## 第 7 步：运行分析',
        '',
        '    C:\\ProgramData\\anaconda3\\python.exe '
        'python -m tools.research.control_mass_experiment analyze --input '
        '<实验目录> --json',
        '',
        '只有四组 craft/loadmeta 全部到位后才会分析；任何校验失败都会以 '
        'invalid_design 列出具体差异（机器可读 code），不会给出判定。',
        '',
        '## 数值精度模型（动态 binary32，prepare 时记录）',
        '',
        '真实 KSP .loadmeta 的 totalMass 小数位数在 5–9 位之间分布，固定'
        '七位小数假设不成立。误差界限按 IEEE-754 binary32 往返序列化'
        '动态计算：',
        '',
        f'- 序列化模型：{manifest["tolerances"]["serialization_model"]}',
        f'- 预测端相对误差因子：{manifest["tolerances"]["predicted_float32_error_factor"]}',
        f'- 实验裕量：{manifest["tolerances"]["experiment_margin_t"]} t',
        f'- M3 直径最小变化：{manifest["tolerances"]["diameter_min_delta_m"]} m',
        '',
        '每条差分/线性/直径判定使用动态观测误差界限 + 预测误差 + 实验'
        '裕量；观测到的质量邻值与 ULP 写入分析报告。',
        '',
        '## 重新采集',
        '',
        '若 analyze 报 invalid_design（例如某份 craft 意外多了一个部件、'
        'M3 改了其他参数、或指纹与 prepare 不一致），请删除 input/ 中的'
        '对应文件，回到对应步骤重新搭建并重新复制，然后重新运行 analyze。'
        '只要 input/ 中还有任何文件，prepare 就拒绝覆盖 manifest。',
        '',
        '## 当前状态',
        '',
        '**awaiting_ksp_capture**——本阶段不运行 KSP、不伪造任何 craft/'
        'loadmeta；所有结论必须来自用户在 KSP 中生成的真实数据。',
        '',
    ]
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# analyze — input loading
# ---------------------------------------------------------------------------

def _expected_input_files():
    return [f'{name}.craft' for name in CRAFT_NAMES] + \
           [f'{name}.loadmeta' for name in CRAFT_NAMES]


def _load_craft(path):
    from server.vab_parser import parse_craft
    return parse_craft(path.read_text(encoding='utf-8-sig'), path.name)


def _load_loadmeta(path):
    from server.import_service import _loadmeta_metadata
    return _loadmeta_metadata(
        path.read_text(encoding='utf-8-sig', errors='replace'))


def _loadmeta_total_mass(loadmeta):
    raw = (loadmeta or {}).get('totalMass')
    value = _finite(raw)
    return value, raw


def _load_inputs(experiment_dir):
    input_dir = Path(experiment_dir) / 'input'
    files = {}
    sha256 = {}
    for name in _expected_input_files():
        path = input_dir / name
        present = path.is_file()
        files[name] = {'path': str(path), 'present': present}
        sha256[name] = _file_sha256(path) if present else None
    missing = [name for name, info in files.items()
               if not info['present']]
    crafts = {}
    loadmetas = {}
    if not missing:
        try:
            for name in CRAFT_NAMES:
                crafts[name] = _load_craft(input_dir / f'{name}.craft')
                loadmetas[name] = _load_loadmeta(
                    input_dir / f'{name}.loadmeta')
        except Exception as exc:
            return {'files': files, 'missing_files': missing,
                    'sha256': sha256, 'crafts': {}, 'loadmetas': {},
                    'parse_error': str(exc)}
    return {'files': files, 'missing_files': missing, 'sha256': sha256,
            'crafts': crafts, 'loadmetas': loadmetas, 'parse_error': None}


# ---------------------------------------------------------------------------
# analyze — pairing validation
# ---------------------------------------------------------------------------

def _craft_ship_name(draft):
    return (draft.get('source') or {}).get('root_info', {}).get('ship')


def _craft_version(draft):
    return (draft.get('source') or {}).get('root_info', {}).get('version')


def _part_names_set(draft):
    """Distinct base part names of a craft (KSP loadmeta dedupes)."""
    return sorted({part.get('base_part') for part in draft['parts']})


def _validate_pairing(crafts, loadmetas):
    """Strict .craft / .loadmeta pairing validation.

    For every scenario M0..M3:
    * craft internal ship name == expected scenario name;
    * loadmeta.shipName == craft ship name (a renamed old loadmeta from
      another ship is rejected here);
    * loadmeta.partCount == actual parsed part count;
    * loadmeta.partNames (distinct names) == craft distinct part names;
    * loadmeta.type == 'VAB';
    * totalMass is a finite positive number;
    * the KSP version of the four pairs is identical.
    Returns a list of diff records with stable machine-readable codes.
    """
    diffs = []

    def diff(code, message, actual=None, expected=None, scenario=None):
        record = {'code': code, 'message': message}
        if scenario is not None:
            record['scenario'] = scenario
        if actual is not None:
            record['actual'] = actual
        if expected is not None:
            record['expected'] = expected
        diffs.append(record)

    versions = set()
    for name in CRAFT_NAMES:
        draft = crafts[name]
        loadmeta = loadmetas[name]
        ship = _craft_ship_name(draft)
        if ship != name:
            diff('craft_ship_name_mismatch',
                 f'{name}.craft 内部飞船名与场景名不符',
                 actual=ship, expected=name, scenario=name)
        lm_ship = (loadmeta or {}).get('shipName')
        if ship is not None and lm_ship is not None and lm_ship != ship:
            diff('loadmeta_shipname_mismatch',
                 f'{name}.loadmeta 的 shipName 与对应 craft 不符'
                 '（可能是改名自其他飞船的旧 loadmeta）',
                 actual=lm_ship, expected=ship, scenario=name)
        if lm_ship is None:
            diff('loadmeta_shipname_missing',
                 f'{name}.loadmeta 缺少 shipName', scenario=name)
        part_count = len(draft['parts'])
        lm_count_raw = (loadmeta or {}).get('partCount')
        try:
            lm_count = int(float(lm_count_raw))
        except (TypeError, ValueError):
            lm_count = None
        if lm_count is None or lm_count != part_count:
            diff('loadmeta_partcount_mismatch',
                 f'{name}.loadmeta 的 partCount 与 craft 实际部件数不符',
                 actual=lm_count_raw, expected=part_count, scenario=name)
        lm_type = (loadmeta or {}).get('type')
        if lm_type != 'VAB':
            diff('loadmeta_type_mismatch',
                 f'{name}.loadmeta 的 type 不是 VAB',
                 actual=lm_type, expected='VAB', scenario=name)
        lm_names = sorted((loadmeta or {}).get('part_names') or [])
        craft_names = _part_names_set(draft)
        if lm_names != craft_names:
            diff('loadmeta_partnames_mismatch',
                 f'{name}.loadmeta 的 partNames 与 craft 部件组成不符',
                 actual=lm_names, expected=craft_names, scenario=name)
        total, raw = _loadmeta_total_mass(loadmeta)
        if total is None or total <= 0:
            diff('loadmeta_totalmass_invalid',
                 f'{name}.loadmeta 的 totalMass 不是有限正数',
                 actual=raw, scenario=name)
        craft_version = _craft_version(draft)
        lm_version = (loadmeta or {}).get('version')
        if not craft_version:
            diff('craft_version_missing',
                 f'{name}.craft 缺少 version 字段',
                 actual=None, expected='<version>', scenario=name)
        if not lm_version:
            diff('loadmeta_version_missing',
                 f'{name}.loadmeta 缺少 version 字段',
                 actual=None, expected='<version>', scenario=name)
        elif craft_version and lm_version != craft_version:
            diff('craft_loadmeta_version_mismatch',
                 f'{name} 的 craft 与 loadmeta version 不一致',
                 actual=lm_version, expected=craft_version,
                 scenario=name)
        if craft_version:
            versions.add(craft_version)
        if lm_version:
            versions.add(lm_version)
    if len(versions) > 1:
        diff('ksp_version_inconsistent',
             f'四组文件的 KSP version 不一致：{sorted(versions)}',
             actual=sorted(versions))
    return diffs


# ---------------------------------------------------------------------------
# analyze — experiment variable validation
# ---------------------------------------------------------------------------

def _part_mass_state(part, include_diameter=True):
    """Mass-relevant saved state of a part.

    Compares base part, modules, resources, mod_mass fields and the parsed
    mass-modifier module state.  Part instance IDs, position, rotation and
    ordinary connections are intentionally ignored.
    """
    evidence = part.get('module_evidence') or {}
    normalized = {}
    for module_name in sorted(evidence):
        entries = evidence[module_name]
        if module_name == 'RealChuteModule':
            normalized[module_name] = tuple(
                (_finite(entry.get('case_mass')),
                 _finite(entry.get('spare_chutes')))
                for entry in entries)
        elif module_name == 'ProceduralChute':
            rows = []
            for entry in entries:
                chutes = []
                for chute in entry.get('chutes') or []:
                    row = (chute.get('get_mass'), chute.get('use_dry'),
                           _finite(chute.get('mass')),
                           _finite(chute.get('chute_count')),
                           _finite(chute.get('pre_dep_diam')))
                    if include_diameter:
                        row = row + (_finite(chute.get('dep_diam')),)
                    chutes.append(row)
                rows.append(tuple(chutes))
            normalized[module_name] = tuple(rows)
        else:
            normalized[module_name] = tuple(
                tuple(sorted((key, str(value))
                             for key, value in entry.items()
                             if key != 'diagnostics'))
                for entry in entries)
    resources = tuple(sorted(
        (resource.get('name'), resource.get('amount'),
         resource.get('max_amount'))
        for resource in part.get('resources') or []))
    return (
        part.get('base_part'),
        tuple(sorted(part.get('modules') or [])),
        resources,
        part.get('mod_mass_present'),
        part.get('mod_mass_raw'),
        _finite(part.get('mod_mass_t')),
        tuple(sorted(normalized.items())),
    )


_CHUTE_DESIGN_KEYS = (
    'calc_select', 'get_mass', 'use_dry', 'mass', 'chute_count',
    'pre_dep_diam', 'dep_diam', 'is_pressure', 'predep_clause',
    'ref_dep_alt', 'deployment_alt', 'cut_alt', 'pre_dep_speed',
    'dep_speed', 'landing_speed', 'deceleration',
)
_PARACHUTE_DESIGN_KEYS = (
    'material', 'pre_deployed_diameter', 'deployed_diameter',
    'min_is_pressure', 'min_deployment', 'min_pressure',
    'deployment_alt', 'cut_alt', 'pre_deployment_speed',
    'deployment_speed', 'canopy_count',
)
_DIAMETER_KEYS = frozenset(('dep_diam', 'deployed_diameter'))


def _chute_design_state(part, include_diameter=True):
    """RealChute DESIGN state of a chute part.

    Includes the ProceduralChute design fields (module-level values or
    CHUTE rows: calcSelect, getMass, useDry, mass, chuteCount, preDepDiam,
    depDiam, isPressure, predepClause, refDepAlt, deploymentAlt, cutAlt,
    preDepSpeed, depSpeed, landingSpeed, deceleration) and the
    RealChuteModule PARACHUTE rows (material, preDeployedDiameter,
    deployedDiameter, minIsPressure, minDeployment, minPressure,
    deploymentAlt, cutAlt, preDeploymentSpeed, deploymentSpeed,
    canopyCount).

    part-level ``modMass`` is a KSP-persisted RUNTIME derived mass delta
    and is deliberately NOT part of the design state.  Instance IDs,
    positions, rotations and ordinary connection data are excluded.
    """
    evidence = part.get('module_evidence') or {}
    rc_entries = evidence.get('RealChuteModule') or [{}]
    rc = rc_entries[0]
    chute_rows = []
    for row in (evidence.get('ProceduralChute') or [{}])[0] \
            .get('chutes') or []:
        entry = tuple(
            (key, row.get(key)) for key in _CHUTE_DESIGN_KEYS
            if include_diameter or key not in _DIAMETER_KEYS)
        chute_rows.append(entry)
    parachute_rows = []
    for parachute in rc.get('parachutes') or []:
        entry = tuple(
            (key, parachute.get(key)) for key in _PARACHUTE_DESIGN_KEYS
            if include_diameter or key not in _DIAMETER_KEYS)
        parachute_rows.append(entry)
    return (
        part.get('base_part'),
        tuple(sorted(part.get('modules') or [])),
        (_finite(rc.get('case_mass')), _finite(rc.get('spare_chutes'))),
        tuple(chute_rows),
        tuple(parachute_rows),
    )


def _chute_design_field_diffs(left, right):
    """Field-level differences between two ``_chute_design_state`` tuples.

    Returns a sorted list of ``(key, left_value, right_value)``.
    """
    diffs = []
    if left[0] != right[0]:
        diffs.append(('base_part', left[0], right[0]))
    if left[1] != right[1]:
        diffs.append(('modules', left[1], right[1]))
    if left[2] != right[2]:
        diffs.append(('rc_case_spares', left[2], right[2]))
    for index, (left_row, right_row) in enumerate(
            zip(left[3], right[3])):
        if left_row != right_row:
            for (key, left_value), (_, right_value) in zip(left_row,
                                                           right_row):
                if left_value != right_value:
                    diffs.append((f'chute[{index}].{key}',
                                  left_value, right_value))
    for index, (left_row, right_row) in enumerate(
            zip(left[4], right[4])):
        if left_row != right_row:
            for (key, left_value), (_, right_value) in zip(left_row,
                                                           right_row):
                if left_value != right_value:
                    diffs.append((f'parachute[{index}].{key}',
                                  left_value, right_value))
    if len(left[3]) != len(right[3]):
        diffs.append(('chute_row_count', len(left[3]), len(right[3])))
    if len(left[4]) != len(right[4]):
        diffs.append(('parachute_row_count', len(left[4]), len(right[4])))
    return sorted(diffs)


def _chute_diameter(part):
    state = part.get('module_evidence') or {}
    proc = (state.get('ProceduralChute') or [{}])[0]
    for chute in proc.get('chutes') or []:
        diameter = _finite(chute.get('dep_diam'))
        if diameter is not None:
            return diameter
    return None


def _chute_parts(draft):
    return [part for part in draft['parts']
            if part.get('base_part') == PARACHUTE_PART]


def _core_part(draft, core_name, canonical_map=None):
    matches = [
        part for part in draft['parts']
        if (_canonical_name(canonical_map, part) if canonical_map
            else part.get('base_part')) == core_name]
    return matches[0] if matches else None


def _calc_select_of(design_state):
    rows = design_state[3]
    if not rows:
        return None
    for key, value in rows[0]:
        if key == 'calc_select':
            return value
    return None


def _validate_variables(crafts, tolerances, selected_core_name,
                        index, attachment_plan=None, core_record=None):
    """Tightened experiment-variable validation.  Returns diff records.

    Part identities use CANONICAL names (exact-first, dot-to-underscore,
    unique required); stack topology is read from the core's OWN attN
    rows; RealChute DESIGN state excludes the runtime-derived part-level
    modMass (kept for the core and reported separately for chutes).
    """
    diffs = []

    def diff(code, message, **details):
        diffs.append({'code': code, 'message': message,
                      **{k: v for k, v in details.items()}})

    from server.ksp_install_index import DYNAMIC_MASS_MODULES
    from server.ksp_part_matcher import EXACT, NORMALIZED_UNIQUE

    canonical_maps = {}
    for name, draft in crafts.items():
        canonical_maps[name] = _canonical_part_map(draft, index)
        for part in draft['parts']:
            info = canonical_maps[name].get(part.get('index')) or {}
            if info.get('status') not in (EXACT, NORMALIZED_UNIQUE):
                diff('part_name_unresolved',
                     f'{name} 的部件 {part.get("part")} 无法唯一匹配'
                     f' ConfigCache（status={info.get("status")}，'
                     f'strategy={info.get("strategy")}）',
                     raw_base=info.get('raw_base'),
                     status=info.get('status'),
                     strategy=info.get('strategy'),
                     scenario=name)

    def canonical(part, name):
        info = canonical_maps[name].get(part.get('index')) or {}
        return info.get('canonical')

    # M0 strictly one part == the selected control core (canonical)
    m0_parts = crafts['M0_control']['parts']
    if len(m0_parts) != 1:
        diff('m0_part_count_mismatch',
             f'M0 必须严格只有一个部件，实际 {len(m0_parts)} 个',
             actual=len(m0_parts), expected=1)
    m0_root_canonical = canonical(m0_parts[0], 'M0_control') \
        if m0_parts else None
    if m0_root_canonical != selected_core_name:
        diff('m0_root_not_selected_core',
             'M0 的根部件不是 manifest 绑定的控制核心（canonical 名称）',
             actual=m0_root_canonical, expected=selected_core_name)

    # exact part composition for M1/M2/M3 (canonical)
    core_count = Counter({selected_core_name: 1})
    expected = {
        'M1_one_chute_default': core_count + Counter(
            {PARACHUTE_PART: 1}),
        'M2_two_chutes_default': core_count + Counter(
            {PARACHUTE_PART: 2}),
        'M3_one_chute_changed_diameter': core_count + Counter(
            {PARACHUTE_PART: 1}),
    }
    for name, expect in expected.items():
        actual = Counter(
            canonical(part, name) for part in crafts[name]['parts'])
        if actual != expect:
            diff('part_set_mismatch',
                 f'{name} 的部件集合不符合 控制核心 + 预期降落伞数'
                 '（canonical 名称）',
                 actual=dict(actual), expected=dict(expect))

    # symmetry: mirrored parts are forbidden in the experiment crafts
    for name, draft in crafts.items():
        for part in draft['parts']:
            sym = (part.get('connections') or {}).get('sym') or []
            if sym:
                diff('symmetry_forbidden',
                     f'{name} 的 {part.get("part")} 存在对称连接 '
                     f'（sym={sym}），对称复制的降落伞会破坏实例计数',
                     actual=sym, expected=[])

    # control-core mass-relevant instance state identical in all four
    # (including part-level modMass — the core must not drift)
    core_states = {}
    for name, draft in crafts.items():
        core = _core_part(draft, selected_core_name, canonical_maps[name])
        if core is None:
            diff('core_part_missing',
                 f'{name} 缺少控制核心 {selected_core_name}'
                 '（canonical 名称）')
            continue
        core_states[name] = _part_mass_state(core)
    if core_states:
        reference = core_states.get('M0_control')
        if reference is not None:
            for name in CRAFT_NAMES[1:]:
                if name in core_states and \
                        core_states[name] != reference:
                    diff('core_state_inconsistent',
                         f'{name} 的控制核心质量相关实例状态与 M0 不一致'
                         '（base part/modules/resources/mod_mass/质量 '
                         'modifier 状态必须完全一致）')

    # resources equal across crafts (canonical base attribution)
    def resource_counter(draft, name):
        counter = Counter()
        for part in draft['parts']:
            base = canonical(part, name)
            for resource in (part.get('resources') or []):
                counter[(base, resource.get('name'),
                         resource.get('amount'),
                         resource.get('max_amount'))] += 1
            for tank in (part.get('tanks') or []):
                counter[(base, tank.get('name'),
                         tank.get('amount'), tank.get('max_amount'))] += 1
        return counter

    reference_resources = resource_counter(crafts['M0_control'],
                                           'M0_control')
    for name in CRAFT_NAMES[1:]:
        if resource_counter(crafts[name], name) != reference_resources:
            diff('resource_mismatch',
                 f'{name} 的资源集合与 M0 不一致')

    # chute counts (canonical)
    for name, draft in crafts.items():
        actual_count = sum(
            1 for part in draft['parts']
            if canonical(part, name) == PARACHUTE_PART)
        if actual_count != CHUTE_COUNTS[name]:
            diff('chute_count_mismatch',
                 f'{name} 的 parachuteSingle 数量为 {actual_count}，'
                 f'预期 {CHUTE_COUNTS[name]}',
                 actual=actual_count, expected=CHUTE_COUNTS[name])

    def chute_parts(name):
        return [part for part in crafts[name]['parts']
                if canonical(part, name) == PARACHUTE_PART]

    # M1/M2 chute DESIGN states must be identical (all instances);
    # a difference ONLY in calc_select is reported as the calculation
    # mode mismatch
    m1_chutes = chute_parts('M1_one_chute_default')
    m2_chutes = chute_parts('M2_two_chutes_default')
    design_m1 = [_chute_design_state(part) for part in m1_chutes]
    design_m2 = [_chute_design_state(part) for part in m2_chutes]
    m1_reference = design_m1[0] if design_m1 else None
    if m1_reference is not None:
        all_equal = (
            all(state == m1_reference for state in design_m1)
            and design_m2 == [m1_reference] * len(design_m2))
        if not all_equal:
            field_diffs = _chute_design_field_diffs(
                m1_reference, design_m2[0]) if design_m2 else \
                [('chute_row_count', len(design_m1), 0)]
            only_calc = field_diffs and all(
                key == 'chute[0].calc_select'
                for key, _, _ in field_diffs)
            if only_calc:
                diff('chute_calculation_mode_mismatch',
                     'M2 的降落伞计算模式与 M1 不一致'
                     '（M1=Manual/calcSelect=False，'
                     'M2=Automatic/calcSelect=True）；请把 M2 两个'
                     '降落伞都切换到 Calculations mode = Manual',
                     m1_calc_select=_calc_select_of(m1_reference),
                     m2_calc_select=_calc_select_of(design_m2[0])
                     if design_m2 else None)
            else:
                diff('m1_m2_params_mismatch',
                     'M1/M2 的降落伞设计状态不一致：'
                     + ', '.join(f'{key}={left!r}≠{right!r}'
                                 for key, left, right in field_diffs),
                     differences=field_diffs)

    # M3 vs M1: ONLY depDiam + deployedDiameter may differ; part-level
    # modMass is a runtime derived value and is NOT part of the design
    # comparison
    m1_chute = chute_parts('M1_one_chute_default')
    m3_chute = chute_parts('M3_one_chute_changed_diameter')
    if len(m1_chute) == 1 and len(m3_chute) == 1:
        state_m1 = _chute_design_state(m1_chute[0],
                                       include_diameter=False)
        state_m3 = _chute_design_state(m3_chute[0],
                                       include_diameter=False)
        if state_m1 != state_m3:
            field_diffs = _chute_design_field_diffs(state_m1, state_m3)
            only_calc = field_diffs and all(
                key == 'chute[0].calc_select'
                for key, _, _ in field_diffs)
            if only_calc:
                diff('chute_calculation_mode_mismatch',
                     'M3 与 M1 的降落伞计算模式不一致'
                     '（M1=Manual，M3=Automatic）；M3 应从 M1 复制且'
                     '只改直径',
                     m1_calc_select=_calc_select_of(state_m1),
                     m3_calc_select=_calc_select_of(state_m3))
            else:
                diff('m3_extra_changes',
                     'M3 与 M1 的降落伞在展开直径之外还有其他设计差异'
                     '：'
                     + ', '.join(f'{key}={left!r}≠{right!r}'
                                 for key, left, right in field_diffs),
                     differences=field_diffs)
        d1 = _chute_diameter(m1_chute[0])
        d3 = _chute_diameter(m3_chute[0])
        if d1 is None or d3 is None:
            diff('m3_diameter_unreadable',
                 '无法从保存的 craft 中读取 M1/M3 的展开直径',
                 m1_diameter=d1, m3_diameter=d3)
        elif abs(d3 - d1) < tolerances['diameter_min_delta_m']:
            diff('m3_diameter_not_changed',
                 f'M3 直径（{d3} m）与 M1 直径（{d1} m）相差不足 '
                 f'{tolerances["diameter_min_delta_m"]} m')

    # mass modifiers: static denylist + CURRENT index DYNAMIC_MASS_MODULES
    forbidden = FORBIDDEN_MODULES | set(DYNAMIC_MASS_MODULES)
    for name, draft in crafts.items():
        for part in draft['parts']:
            present = set(part.get('modules') or []) & forbidden
            if present:
                diff('forbidden_mass_modifier',
                     f'{name} 的 {part.get("part")} 含有被禁止的质量 '
                     f'modifier：{sorted(present)}',
                     actual=sorted(present))

    # attachment-plan validation: the CORE's own attN rows define which
    # chute sits on which node; the child's trailing _0/_1 is never used
    # to infer the core node
    plan = attachment_plan or {}
    if not plan.get('M1_one_chute_default'):
        diff('attachment_plan_missing',
             '无法从 manifest 或当前索引取得安装计划')
    else:
        expected = {
            'M1_one_chute_default':
                plan.get('M1_one_chute_default') or [],
            'M2_two_chutes_default':
                plan.get('M2_two_chutes_default') or [],
            'M3_one_chute_changed_diameter':
                plan.get('M3_one_chute_changed_diameter') or [],
        }
        for name, draft in crafts.items():
            if name == 'M0_control':
                continue
            topology = _normalize_stack_topology(
                draft, canonical_maps[name], selected_core_name)
            if not topology['core_present']:
                diff('attachment_connection_missing',
                     f'{name} 找不到控制核心的 attN 连接信息',
                     scenario=name)
                continue
            for chute in topology['chute_to_chute']:
                diff('chute_daisy_chain_forbidden',
                     f'{name} 的降落伞 {chute} 连接到另一个降落伞'
                     f'（禁止把一个降落伞挂到另一个降落伞上）',
                     chute=chute, scenario=name)
            for chute in topology['unconnected']:
                diff('attachment_connection_missing',
                     f'{name} 的降落伞 {chute} 未直接连接到控制核心',
                     chute=chute, scenario=name)
            node_counts = Counter(
                assignment['core_node']
                for assignment in topology['assignments'])
            chute_counts = Counter(
                assignment['chute_instance']
                for assignment in topology['assignments'])
            for node, count in sorted(node_counts.items()):
                if count > 1:
                    diff('duplicate_core_node',
                         f'{name} 的 {node} 节点连接了 {count} 个'
                         f'实验降落伞（每个节点只允许一个）',
                         core_node=node, count=count, scenario=name)
            for chute, count in sorted(chute_counts.items()):
                if count > 1:
                    diff('chute_multiple_core_nodes',
                         f'{name} 的降落伞 {chute} 出现在 {count} 个'
                         f'核心节点上（每个降落伞只允许一个节点）',
                         chute=chute, count=count, scenario=name)
            used_nodes = sorted(
                assignment['core_node']
                for assignment in topology['assignments'])
            expected_nodes = expected[name]
            if used_nodes != sorted(expected_nodes):
                diff('attachment_plan_mismatch',
                     f'{name} 的降落伞实际连接节点'
                     f' {used_nodes} 与安装计划'
                     f' {sorted(expected_nodes)} 不一致',
                     actual=used_nodes,
                     expected=sorted(expected_nodes), scenario=name)
    return diffs, canonical_maps


# ---------------------------------------------------------------------------
# analyze — runtime mass diagnostics
# ---------------------------------------------------------------------------

def _zero_noise_bound(reference_magnitude):
    """binary32 ULP at the reference magnitude — a principled zero-noise
    bound for float32 residuals (no arbitrary fixed epsilon)."""
    if reference_magnitude is None:
        return None
    analysis = _binary32_analysis(reference_magnitude)
    if analysis is None:
        return None
    return analysis['upper_neighbour_t'] - analysis['stored_f32_t']


def _runtime_mass_diagnostics(crafts, canonical_maps, evidence):
    """Part-level modMass (KSP/PartModule persisted RUNTIME derived mass
    delta) as result evidence and diagnostics only.

    The control core's modMass is still a design consistency check; the
    experiment chutes' modMass is reported (raw, parsed, zero-noise flag,
    diameter-prediction relation) and does NOT participate in design
    equality.
    """
    diagnostics = {}
    reference_scale = None
    if evidence.get('case_mass_t') is not None and \
            evidence.get('chute_mass_m1_t') is not None:
        reference_scale = evidence['case_mass_t'] + \
            evidence['chute_mass_m1_t']
    bound = _zero_noise_bound(reference_scale)
    for name in CRAFT_NAMES:
        draft = crafts[name]
        cmap = canonical_maps.get(name) or {}
        core = next(
            (part for part in draft['parts']
             if _canonical_name(cmap, part)), None)
        core_mod = _finite(core.get('mod_mass_t')) if core else None
        chutes = [
            part for part in draft['parts']
            if _canonical_name(cmap, part) == PARACHUTE_PART]
        chute_diags = []
        for chute in chutes:
            mod = _finite(chute.get('mod_mass_t'))
            within_zero_noise = None
            if mod is not None:
                within_zero_noise = abs(mod) <= bound if bound is not None \
                    else None
            chute_diags.append({
                'raw_value': chute.get('mod_mass_raw'),
                'parsed_value_t': mod,
                'within_binary32_zero_noise': within_zero_noise,
                'zero_noise_bound_t': bound,
                'participates_in_design_validation': False,
            })
        diagnostics[name] = {
            'core_mod_mass_t': core_mod,
            'core_participates_in_design_validation': True,
            'chutes': chute_diags,
            'zero_noise_reference_scale_t': reference_scale,
        }
    if evidence.get('chute_mass_m1_t') is not None and \
            evidence.get('chute_mass_m3_t') is not None:
        diagnostics['predicted_diameter_mass_delta_t'] = \
            evidence['chute_mass_m3_t'] - evidence['chute_mass_m1_t']
    return diagnostics


# ---------------------------------------------------------------------------
# analyze — differential analysis
# ---------------------------------------------------------------------------

def _chute_mass_evidence(index, crafts):
    """Per-chute evidence from the patched config + captured craft state."""
    evidence = {
        'prefab_part_mass_t': None,
        'case_mass_t': None,
        'material': None,
        'area_density_t_per_unit': None,
        'diameter_m1_m': None,
        'diameter_m3_m': None,
        'chute_mass_m1_t': None,
        'chute_mass_m3_t': None,
        'realchute_mass_delta_t': None,
        'predicted_settled_part_mass_t': None,
        'missing': [],
        'diagnostics': [],
    }
    from server.ksp_module_mass_semantics import (
        chute_deployed_area, chute_mass, realchute_mass_delta)
    record = index.primary_part(PARACHUTE_PART) if index else None
    if record is None:
        evidence['missing'].extend(
            ['patched_part_config', 'prefab_part_mass', 'material',
             'material_area_density'])
    else:
        evidence['prefab_part_mass_t'] = _finite(record.get('mass_t'))
        if evidence['prefab_part_mass_t'] is None:
            evidence['missing'].append('prefab_part_mass')
        config = (record.get('module_evidence') or {}).get(
            'RealChuteModule', [{}])[0]
        for chute in config.get('parachutes') or []:
            if chute.get('material'):
                evidence['material'] = chute.get('material')
                break
        if evidence['material'] is None:
            evidence['missing'].append('material')
        if evidence['material']:
            material = index.find_material(evidence['material'])
            evidence['area_density_t_per_unit'] = _finite(
                material.get('area_density')) if material else None
            if evidence['area_density_t_per_unit'] is None:
                evidence['missing'].append('material_area_density')

    m1_chutes = _chute_parts(crafts['M1_one_chute_default'])
    m3_chutes = _chute_parts(crafts['M3_one_chute_changed_diameter'])
    if len(m1_chutes) == 1 and len(m3_chutes) == 1:
        m1 = m1_chutes[0]
        m3 = m3_chutes[0]
        rc = (m1.get('module_evidence') or {}).get(
            'RealChuteModule', [{}])[0]
        evidence['case_mass_t'] = _finite(rc.get('case_mass'))
        if evidence['case_mass_t'] is None:
            evidence['missing'].append('case_mass')
        d1 = _chute_diameter(m1)
        d3 = _chute_diameter(m3)
        evidence['diameter_m1_m'] = d1
        evidence['diameter_m3_m'] = d3
        density = evidence['area_density_t_per_unit']
        if d1 is not None and density is not None:
            evidence['chute_mass_m1_t'] = chute_mass(
                chute_deployed_area(d1), density)
        if d3 is not None and density is not None:
            evidence['chute_mass_m3_t'] = chute_mass(
                chute_deployed_area(d3), density)
        if evidence['chute_mass_m1_t'] is None:
            evidence['missing'].append('chute_mass_m1')
        if evidence['chute_mass_m3_t'] is None:
            evidence['missing'].append('chute_mass_m3')
        if evidence['case_mass_t'] is not None and \
                evidence['chute_mass_m1_t'] is not None and \
                evidence['prefab_part_mass_t'] is not None:
            delta, diags = realchute_mass_delta(
                evidence['case_mass_t'], [evidence['chute_mass_m1_t']],
                evidence['prefab_part_mass_t'])
            evidence['realchute_mass_delta_t'] = delta
            evidence['diagnostics'].extend(diags)
            if delta is not None:
                evidence['predicted_settled_part_mass_t'] = \
                    evidence['prefab_part_mass_t'] + delta
            else:
                evidence['missing'].append('realchute_mass_delta')
    else:
        evidence['missing'].append('single_chute_state')
    evidence['missing'] = _normalize_missing(evidence['missing'])
    return evidence


def _model_verdict(predicted, observed, tolerance, complete):
    if not complete or predicted is None or observed is None \
            or tolerance is None:
        return VERDICT_INSUFFICIENT
    error = abs(predicted - observed)
    if error <= tolerance:
        return VERDICT_SUPPORTED
    return VERDICT_CONTRADICTED


def _predicted_error(predicted, factor):
    """Predicted-side float32/float64 computation error, recorded SEPARATELY
    from the loadmeta observation error."""
    if predicted is None:
        return None
    return abs(predicted) * factor


def _loadmeta_numbers(loadmetas):
    """Per-scenario binary32 serialization analysis of totalMass.

    Output per value: raw decimal, parsed decimal, NORMALIZED stored
    binary32 value, decimal round-trip display delta (reported separately,
    never part of the quantization bound), lower/upper neighbours,
    round-trip interval midpoints and the quantization bound.  The
    fractional-digit count is NOT used for precision (diagnostic only).
    """
    numbers = {}
    for name in CRAFT_NAMES:
        raw = (loadmetas[name] or {}).get('totalMass')
        parsed = _finite(raw)
        entry = {
            'raw_decimal': raw,
            'parsed_decimal_t': parsed,
            'stored_f32_t': None,
            'decimal_roundtrip_delta_t': None,
            'serialization_model': SERIALIZATION_MODEL,
            'serialization_valid': False,
            'lower_neighbour_t': None,
            'upper_neighbour_t': None,
            'lower_midpoint_t': None,
            'upper_midpoint_t': None,
            'quantization_bound_t': None,
            'fractional_digit_count': None,
        }
        if raw is not None and '.' in str(raw):
            entry['fractional_digit_count'] = len(str(raw).split('.')[1])
        if parsed is not None:
            analysis = _binary32_analysis(parsed)
            if analysis is not None:
                entry['serialization_valid'] = True
                entry.update(analysis)
        numbers[name] = entry
    return numbers


def _differential_analysis(validation, evidence, tolerances, loadmetas):
    numbers = _loadmeta_numbers(loadmetas)

    def stored(scenario):
        entry = numbers.get(scenario) or {}
        if not entry.get('serialization_valid'):
            return None
        return entry.get('stored_f32_t')

    m0 = stored('M0_control')
    m1 = stored('M1_one_chute_default')
    m2 = stored('M2_two_chutes_default')
    m3 = stored('M3_one_chute_changed_diameter')
    # ALL differentials use the NORMALIZED binary32 values, never the
    # parsed decimal approximations
    observed_one = m1 - m0 if None not in (m0, m1) else None
    observed_two = m2 - m0 if None not in (m0, m2) else None
    observed_diameter = m3 - m1 if None not in (m3, m1) else None

    margin = tolerances['experiment_margin_t']
    factor = tolerances['predicted_float32_error_factor']
    one_bound = _observed_error_bound(numbers, [
        ('M1_one_chute_default', 1), ('M0_control', -1)])
    linear_bound = _observed_error_bound(numbers, [
        ('M2_two_chutes_default', 1), ('M1_one_chute_default', -2),
        ('M0_control', 1)])
    diameter_bound = _observed_error_bound(numbers, [
        ('M3_one_chute_changed_diameter', 1),
        ('M1_one_chute_default', -1)])

    models = {}
    model_specs = {
        'prefab_base_only': {
            'name': 'prefab_base_only',
            'description': '单个降落伞的贡献 = 补丁后 PART mass',
            'predicted': evidence['prefab_part_mass_t'],
            'complete': evidence['prefab_part_mass_t'] is not None
            and 'prefab_part_mass' not in evidence['missing'],
        },
        'realchute_settled_mass': {
            'name': 'realchute_settled_mass',
            'description': '单个降落伞的贡献 = caseMass + ChuteMass',
            'predicted': evidence['predicted_settled_part_mass_t'],
            'complete': (
                evidence['predicted_settled_part_mass_t'] is not None
                and 'case_mass' not in evidence['missing']
                and 'chute_mass_m1' not in evidence['missing']
                and 'realchute_mass_delta' not in evidence['missing']),
        },
        'part_effectively_excluded': {
            'name': 'part_effectively_excluded',
            'description': '单个降落伞的贡献 ≈ 0',
            'predicted': 0.0,
            'complete': True,
        },
    }
    for key, spec in model_specs.items():
        predicted = spec['predicted']
        predicted_err = _predicted_error(predicted, factor)
        tolerance = None
        if one_bound is not None and predicted_err is not None:
            tolerance = one_bound + predicted_err + margin
        complete = spec['complete'] and observed_one is not None \
            and tolerance is not None
        models[key] = {
            'name': spec['name'],
            'description': spec['description'],
            'applicable': True,
            'evidence_complete': complete,
            'predicted_increment_t': predicted,
            'observed_increment_t': observed_one,
            'absolute_error_t': abs(predicted - observed_one)
            if complete and predicted is not None else None,
            'observed_error_bound_t': one_bound,
            'predicted_error_t': predicted_err,
            'verdict_tolerance_t': tolerance,
            'verdict': _model_verdict(
                predicted, observed_one, tolerance, complete),
        }

    linearity = None
    if observed_one is not None and observed_two is not None \
            and linear_bound is not None:
        expected_two = 2.0 * observed_one
        linearity = {
            'predicted_two_chutes_t': expected_two,
            'observed_two_chutes_t': observed_two,
            'absolute_error_t': abs(observed_two - expected_two),
            'observed_error_bound_t': linear_bound,
            'predicted_error_t': 0.0,
            'verdict_tolerance_t': linear_bound + margin,
            'within_tolerance': abs(observed_two - expected_two) <=
            linear_bound + margin,
            'verdict': VERDICT_SUPPORTED
            if abs(observed_two - expected_two) <= linear_bound + margin
            else VERDICT_CONTRADICTED,
        }

    diameter = None
    predicted_diameter = None
    if evidence['chute_mass_m3_t'] is not None and \
            evidence['chute_mass_m1_t'] is not None:
        predicted_diameter = evidence['chute_mass_m3_t'] - \
            evidence['chute_mass_m1_t']
    predicted_diameter_err = _predicted_error(predicted_diameter, factor)
    diameter_tolerance = None
    if diameter_bound is not None and predicted_diameter_err is not None:
        diameter_tolerance = diameter_bound + predicted_diameter_err \
            + margin
    if predicted_diameter is not None and observed_diameter is not None \
            and diameter_tolerance is not None:
        diameter = {
            'predicted_chute_mass_delta_t': predicted_diameter,
            'observed_diameter_effect_t': observed_diameter,
            'absolute_error_t': abs(observed_diameter - predicted_diameter),
            'observed_error_bound_t': diameter_bound,
            'predicted_error_t': predicted_diameter_err,
            'verdict_tolerance_t': diameter_tolerance,
            'verdict': _model_verdict(
                predicted_diameter, observed_diameter,
                diameter_tolerance, True),
        }
    else:
        diameter = {
            'predicted_chute_mass_delta_t': predicted_diameter,
            'observed_diameter_effect_t': observed_diameter,
            'absolute_error_t': None,
            'observed_error_bound_t': diameter_bound,
            'predicted_error_t': predicted_diameter_err,
            'verdict_tolerance_t': diameter_tolerance,
            'verdict': VERDICT_INSUFFICIENT,
        }

    return {
        'observed_one_chute_t': observed_one,
        'observed_two_chutes_t': observed_two,
        'observed_diameter_effect_t': observed_diameter,
        'loadmeta_numbers': numbers,
        'serialization_model': SERIALIZATION_MODEL,
        'evidence': evidence,
        'models': models,
        'linearity_check': linearity,
        'diameter_check': diameter,
        'design_note': (
            '配对差分：M1−M0 隔离单个降落伞的实际增量；M2 检查线性与'
            '实例计数；M3 检查 RealChute 的 chute mass 是否真正进入 '
            'loadmeta。本实验用于打破现有样本中 parachuteSingle 与 '
            'mk1pod.v2 的完全共线。绝对质量仅用于诊断，不作模型判定。'
            '观测误差界限按 IEEE-754 binary32 往返序列化动态计算；'
            '小数位数只用于诊断，不用于推导精度。'),
    }


# ---------------------------------------------------------------------------
# analyze — control core re-validation
# ---------------------------------------------------------------------------

def _validate_control_core(index, manifest_core):
    """Re-validate the manifest-bound control core against the CURRENT
    index: it must still exist, still pass every safety filter, and its
    normalized config summary must still hash to the manifest value."""
    diffs = []
    name = (manifest_core or {}).get('name')
    if not name:
        return [{'code': 'control_core_missing_in_manifest',
                 'message': 'manifest 未绑定 selected_control_core'}]
    record = index.primary_part(name)
    if record is None:
        return [{'code': 'control_core_missing_in_config',
                 'message': f'控制核心 {name} 已不在当前 ConfigCache 中'}]
    safe = [c for c in _safe_core_candidates(index)
            if c.get('name') == name]
    if not safe:
        diffs.append({
            'code': 'control_core_no_longer_safe',
            'message': f'控制核心 {name} 不再满足安全条件（缺 '
                       f'ModuleCommand / 质量非法 / 含发动机 / 含质量'
                       f'可变模块）',
        })
        return diffs
    current_hash = _core_summary_sha256(record)
    if current_hash != (manifest_core or {}).get('sha256'):
        diffs.append({
            'code': 'control_core_config_changed',
            'message': f'控制核心 {name} 的配置摘要与 manifest 不一致'
                       '（ConfigCache 中该部件已变化）',
            'actual_sha256': current_hash,
            'expected_sha256': (manifest_core or {}).get('sha256'),
        })
    return diffs


# ---------------------------------------------------------------------------
# analyze — entry point
# ---------------------------------------------------------------------------

def analyze_experiment(experiment_dir, ksp_root=None, index=None,
                       manifest=None) -> dict:
    """Validate and analyze one captured experiment.

    Returns ``awaiting_ksp_capture`` when any of the 8 files is missing,
    ``invalid_design`` (with explicit machine-readable diffs and NO
    verdicts) when pairing / control core / variables / fingerprints
    fail, else ``analyzed`` with the paired-difference models.  Input
    files' SHA-256 are recorded as a reviewable evidence list.
    """
    experiment_dir = Path(experiment_dir)
    if manifest is None:
        manifest_path = experiment_dir / 'manifest.json'
        if not manifest_path.is_file():
            return {'experiment_status': 'invalid_experiment_setup',
                    'error': f'manifest 缺失：{manifest_path}'}
        try:
            manifest = json.loads(
                manifest_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            return {'experiment_status': 'invalid_experiment_setup',
                    'error': f'manifest 无法解析：{exc}'}
    schema = manifest.get('schema_version')
    if schema != SCHEMA_VERSION:
        return {'experiment_status': 'invalid_experiment_setup',
                'error': (f'manifest schema_version={schema} 不兼容'
                          f'（当前 {SCHEMA_VERSION}）。请清空 input/ 后'
                          f'重新运行 prepare。')}
    tolerance_errors = _validate_tolerances(
        manifest.get('tolerances'))
    if tolerance_errors:
        return {'experiment_status': 'invalid_experiment_setup',
                'error': 'manifest 容差字段失败封闭：'
                         + ', '.join(tolerance_errors),
                'tolerance_errors': tolerance_errors}
    tolerances = dict(TOLERANCES)
    tolerances.update(manifest.get('tolerances') or {})

    loaded = _load_inputs(experiment_dir)
    report = {
        'schema_version': SCHEMA_VERSION,
        'experiment_name': manifest.get('experiment_name',
                                        EXPERIMENT_NAME),
        'experiment_status': STATUS_AWAITING,
        'input': {
            'directory': str(experiment_dir / 'input'),
            'files': loaded['files'],
            'missing_files': loaded['missing_files'],
            'sha256': loaded['sha256'],
        },
        'tolerances': tolerances,
        'design_note': None,
    }
    if loaded['missing_files']:
        return report  # awaiting_ksp_capture — user capture incomplete
    if loaded['parse_error']:
        report['experiment_status'] = STATUS_INVALID
        report['validation'] = {
            'valid': False,
            'diffs': [{'code': 'craft_parse_error',
                       'message': loaded['parse_error']}],
        }
        return report

    if ksp_root is None:
        ksp_root = manifest.get('ksp', {}).get('ksp_root')
    if not ksp_root:
        report['experiment_status'] = STATUS_INVALID
        report['validation'] = {
            'valid': False,
            'diffs': [{'code': 'ksp_root_missing',
                       'message': 'manifest 未记录 ksp_root 且未提供 '
                                  '--ksp-root'}],
        }
        return report
    from server.ksp_install_index import build_ksp_index
    if index is None:
        index = build_ksp_index(ksp_root)
        if index.status != 'ok':
            report['experiment_status'] = STATUS_INVALID
            report['validation'] = {
                'valid': False,
                'diffs': [{'code': 'index_unavailable',
                           'message': f'KSP 索引构建失败'
                                      f'（status={index.status}）'}],
            }
            return report

    fingerprints = _fingerprints(ksp_root)
    report['fingerprints'] = fingerprints
    fingerprint_differences = _fingerprint_diffs(
        fingerprints, manifest.get('ksp') or {})
    report['fingerprints_matches_manifest'] = \
        not fingerprint_differences

    diffs: List[dict] = []
    diffs.extend(_validate_pairing(loaded['crafts'], loaded['loadmetas']))
    diffs.extend(_validate_control_core(
        index, manifest.get('selected_control_core') or {}))
    # re-derive the attachment plan from the CURRENT index: it must match
    # the manifest plan (config drift is a design failure)
    selected_core_name = (manifest.get('selected_control_core') or {}).get(
        'name')
    current_core = index.primary_part(selected_core_name) \
        if selected_core_name else None
    current_chute = index.primary_part(PARACHUTE_PART)
    current_plan = _experiment_attachment_plan(current_core,
                                               current_chute)
    manifest_plan = manifest.get('attachment_plan') or {}
    if manifest_plan and current_plan != manifest_plan:
        diffs.append({
            'code': 'attachment_plan_changed',
            'message': '当前索引推导的安装计划与 manifest 不一致',
            'actual': current_plan,
            'expected': manifest_plan,
        })
    variable_diffs, canonical_maps = _validate_variables(
        loaded['crafts'], tolerances, selected_core_name, index,
        attachment_plan=current_plan, core_record=current_core)
    diffs.extend(variable_diffs)
    if fingerprint_differences:
        diffs.append({
            'code': 'fingerprint_mismatch',
            'message': '当前 KSP 安装指纹与 prepare 时的 manifest 不一致',
            'differences': sorted(fingerprint_differences),
        })
    report['attachment_plan'] = current_plan
    # runtime mass diagnostics (part-level modMass of chutes) — evidence
    # and diagnostics only, available for every outcome
    evidence = _chute_mass_evidence(index, loaded['crafts'])
    report['runtime_mass_diagnostics'] = _runtime_mass_diagnostics(
        loaded['crafts'], canonical_maps, evidence)

    totals = {}
    for name in CRAFT_NAMES:
        total, raw = _loadmeta_total_mass(loaded['loadmetas'][name])
        totals[name] = total
    validation = {
        'valid': not diffs,
        'diffs': diffs,
        'root_part': (loaded['crafts']['M0_control']['parts'][0]
                      ['base_part']
                      if loaded['crafts']['M0_control']['parts'] else None),
        'part_counts': {name: len(loaded['crafts'][name]['parts'])
                        for name in CRAFT_NAMES},
        'chute_counts': {name: len(_chute_parts(loaded['crafts'][name]))
                         for name in CRAFT_NAMES},
        'total_mass_t': totals,
    }
    report['validation'] = validation
    report['crafts'] = {
        name: {
            'part_count': len(loaded['crafts'][name]['parts']),
            'chute_count': len(_chute_parts(loaded['crafts'][name])),
            'root_part': loaded['crafts'][name]['parts'][0]['part']
            if loaded['crafts'][name]['parts'] else None,
            'loadmeta_total_mass_t': totals[name],
            'ship_name': _craft_ship_name(loaded['crafts'][name]),
        }
        for name in CRAFT_NAMES
    }
    if not validation['valid']:
        report['experiment_status'] = STATUS_INVALID
        report['design_note'] = (
            '设计校验失败：不给出任何支持/反驳判定。')
        return report

    differential = _differential_analysis(validation, evidence, tolerances,
                                          loaded['loadmetas'])
    report['experiment_status'] = STATUS_ANALYZED
    report['differential'] = differential
    report['design_note'] = differential['design_note']
    report['caveat'] = (
        '本分析的有效性完全取决于用户采集的 craft/loadmeta 是否真实且未被'
        '污染；synthetic 夹具的结果不构成真实 KSP 证据。')
    return report


def _fingerprint_diffs(fingerprints, manifest_fingerprints):
    differences = []
    if not manifest_fingerprints:
        return ['manifest 缺少 ksp 指纹']
    if fingerprints['config_cache_sha256'] != \
            manifest_fingerprints.get('config_cache_sha256'):
        differences.append('config_cache_sha256')
    for name, dll in (fingerprints.get('dlls') or {}).items():
        expected = (manifest_fingerprints.get('dlls') or {}).get(name, {})
        if dll.get('sha256') != expected.get('sha256'):
            differences.append(f'dll_{name}')
    return differences


# ---------------------------------------------------------------------------
# preflight — read-only real-environment check
# ---------------------------------------------------------------------------

def _preflight_pair(craft_path, loadmeta_path):
    """Pairing checks for ONE arbitrary .craft + .loadmeta pair."""
    result = {'passed': True, 'codes': [], 'total_mass_t': None,
              'ship_name': None, 'part_count': None}
    try:
        draft = _load_craft(craft_path)
    except Exception as exc:
        result['passed'] = False
        result['codes'].append('craft_parse_error')
        result['details'] = str(exc)
        return result
    ship = _craft_ship_name(draft)
    result['ship_name'] = ship
    result['part_count'] = len(draft['parts'])
    if not loadmeta_path.is_file():
        result['passed'] = False
        result['codes'].append('loadmeta_missing')
        return result
    try:
        loadmeta = _load_loadmeta(loadmeta_path)
    except Exception as exc:
        result['passed'] = False
        result['codes'].append('loadmeta_parse_error')
        result['details'] = str(exc)
        return result
    lm_ship = loadmeta.get('shipName')
    if lm_ship != ship:
        result['passed'] = False
        result['codes'].append('ship_name_mismatch')
    try:
        lm_count = int(float(loadmeta.get('partCount')))
    except (TypeError, ValueError):
        lm_count = None
    if lm_count != len(draft['parts']):
        result['passed'] = False
        result['codes'].append('part_count_mismatch')
    craft_names = _part_names_set(draft)
    lm_names = sorted(loadmeta.get('part_names') or [])
    if lm_names != craft_names:
        result['passed'] = False
        result['codes'].append('part_names_mismatch')
    if loadmeta.get('type') != 'VAB':
        result['passed'] = False
        result['codes'].append('type_mismatch')
    total, raw = _loadmeta_total_mass(loadmeta)
    result['total_mass_t'] = total
    if total is None or total <= 0:
        result['passed'] = False
        result['codes'].append('total_mass_invalid')
    craft_version = _craft_version(draft)
    lm_version = loadmeta.get('version')
    if not craft_version:
        result['passed'] = False
        result['codes'].append('craft_version_missing')
    if not lm_version:
        result['passed'] = False
        result['codes'].append('loadmeta_version_missing')
    elif craft_version and lm_version != craft_version:
        result['passed'] = False
        result['codes'].append('version_mismatch')
    result['details'] = {
        'ship_name': lm_ship, 'part_count': lm_count,
        'part_names': lm_names, 'type': loadmeta.get('type'),
        'total_mass_raw': raw, 'craft_version': craft_version,
        'loadmeta_version': lm_version,
    }
    return result


def preflight(ksp_root, vab_dir, index=None) -> dict:
    """Read-only environment precheck (never writes to KSP).

    Reports ConfigCache/DLL fingerprints, the deterministic control-core
    selection, whether the core can host parachuteSingle (attachRules
    compatibility), the scanned craft/loadmeta pair statistics and the
    machine-readable rejection reasons.  Fractional-digit counts of
    totalMass are reported as DIAGNOSTIC ONLY — never used to derive
    precision.
    """
    ksp_root = Path(ksp_root)
    vab_dir = Path(vab_dir)
    report = {
        'command': 'preflight',
        'schema_version': SCHEMA_VERSION,
        'ksp_fingerprints': _fingerprints(ksp_root),
        'status': 'preflight_complete',
    }
    from server.ksp_install_index import build_ksp_index
    if index is None:
        index = build_ksp_index(ksp_root)
        if index.status != 'ok':
            report['status'] = 'invalid_setup'
            report['error'] = f'KSP 索引构建失败（status={index.status}）'
            return report

    selected, candidates, diagnostics = _select_control_core(index)
    if selected is None:
        report['status'] = 'invalid_setup'
        report['error'] = diagnostics[0]['message']
        return report
    report['selected_control_core'] = {
        'name': selected.get('name'),
        'title': selected.get('title'),
        'mass_t': selected.get('mass_t'),
        'category': selected.get('category'),
        'attach_rules': selected.get('attach_rules') or {},
        'bulkhead_profiles': sorted(selected.get('bulkhead_profiles') or []),
        'stack_nodes': sorted(selected.get('stack_nodes') or []),
        'safety_checks': _core_safety_checks(selected),
        'selection_reason': _selection_reason(selected),
        'sha256': _core_summary_sha256(selected),
    }
    chute = index.primary_part(PARACHUTE_PART)
    plan = _experiment_attachment_plan(selected, chute)
    report['attachment_plan'] = plan
    report['attach_compatibility'] = {
        'core_allow_srf_attach': bool(
            (selected.get('attach_rules') or {}).get('allowSrfAttach')),
        'core_allow_stack': bool(
            (selected.get('attach_rules') or {}).get('allowStack')),
        'core_stack_nodes_ordered':
            list((selected or {}).get('stack_nodes_ordered') or []),
        'chute_attach_rules': (chute or {}).get('attach_rules') or {},
        'surface_path_available': plan['surface_path_available'],
        'stack_path_available': plan['stack_path_available'],
        # feasibility of the FULL four-scenario experiment, not just one
        # parachute
        'supports_chute_installation': plan['supports_experiment'],
        'attachment_mode': plan['mode'],
        'M1_one_chute_default_nodes': plan['M1_one_chute_default'],
        'M2_two_chutes_default_nodes': plan['M2_two_chutes_default'],
        'M3_one_chute_changed_diameter_nodes':
            plan['M3_one_chute_changed_diameter'],
        'reasons': plan['reasons'],
    }

    pairs = sorted(
        p for p in vab_dir.glob('*.craft')
        if not p.name.endswith('.original'))
    results = []
    check_counts = {}
    fractional_digits = []
    total_masses = []
    for craft_path in pairs:
        loadmeta_path = craft_path.with_suffix('.loadmeta')
        result = _preflight_pair(craft_path, loadmeta_path)
        result['craft'] = craft_path.name
        results.append(result)
        for code in result['codes']:
            check_counts[code] = check_counts.get(code, 0) + 1
        if result['total_mass_t'] is not None:
            total_masses.append(result['total_mass_t'])
        raw_total = (result.get('details') or {}).get('total_mass_raw')
        if raw_total is not None and '.' in str(raw_total):
            fractional_digits.append(len(str(raw_total).split('.')[1]))
    report['scan'] = {
        'vab_dir': str(vab_dir),
        'pairs_total': len(pairs),
        'pairs_passed': sum(1 for r in results if r['passed']),
        'pairs_rejected': sum(1 for r in results if not r['passed']),
        'check_counts': dict(sorted(check_counts.items())),
        'rejected_samples': [
            {'craft': r['craft'], 'codes': r['codes'],
             'details': r.get('details')}
            for r in results if not r['passed']],
        'total_mass': {
            'valid_count': len(total_masses),
            'min_t': min(total_masses) if total_masses else None,
            'max_t': max(total_masses) if total_masses else None,
            'fractional_digit_range':
                [min(fractional_digits), max(fractional_digits)]
                if fractional_digits else None,
            'note': '小数位数仅用于诊断，不用于推导精度（精度按 '
                    'IEEE-754 binary32 往返动态计算）',
        },
    }
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _stderr(text):
    print(text, file=sys.stderr)


def _fail(message):
    _stderr(f'错误：{message}')
    sys.exit(EXIT_INVALID)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='KSP 受控质量实验（只读，6B.3C.3A-R）')
    sub = parser.add_subparsers(dest='command', required=True)

    prepare = sub.add_parser('prepare', help='准备实验工作区')
    prepare.add_argument('--ksp-root', default=None,
                         help='KSP 根目录（默认 '
                              r'D:\SteamLibrary\steamapps\common\Kerbal '
                              'Space Program）')
    prepare.add_argument('--output', default='experiments/control_mass',
                         help='实验目录（仓库内）')
    prepare.add_argument('--control-core', default=None,
                         help='指定控制核心部件名（可选；缺省时确定性'
                              '选择排名最高的安全推荐项）')
    prepare.add_argument(
        '--backfill-migration', default=None, metavar='FROM:TO:OLD:NEW',
        help='一次性补齐审计过的迁移记录（仅 input/ 为空且 manifest 尚无'
             'migration_history 时生效），格式 FROM:TO:OLD_CORE:NEW_CORE')

    analyze = sub.add_parser('analyze', help='校验并分析采集结果')
    analyze.add_argument('--input', required=True, help='实验目录')
    analyze.add_argument('--ksp-root', default=None,
                         help='KSP 根目录（默认取 manifest 记录值）')
    analyze.add_argument('--json', action='store_true',
                         help='stdout 仅输出 JSON')

    preflight_parser = sub.add_parser(
        'preflight', help='只读真实环境预检（不写入 KSP）')
    preflight_parser.add_argument('--ksp-root', default=None,
                                  help='KSP 根目录（默认 '
                                       r'D:\SteamLibrary\steamapps\common'
                                       r'\Kerbal Space Program）')
    preflight_parser.add_argument('--vab-dir', required=True,
                                  help='待扫描的 Ships/VAB 目录')
    preflight_parser.add_argument('--json', action='store_true',
                                  help='stdout 仅输出 JSON')

    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass

    if args.command == 'prepare':
        ksp_root = args.ksp_root or \
            r'D:\SteamLibrary\steamapps\common\Kerbal Space Program'
        backfill = None
        if args.backfill_migration:
            try:
                from_schema, to_schema, old_core, new_core = \
                    args.backfill_migration.split(':')
                backfill = [{
                    'from_schema': int(from_schema),
                    'to_schema': int(to_schema),
                    'old_control_core': old_core,
                    'new_control_core': new_core,
                    'reason': 'old core cannot host experiment chute',
                }]
            except ValueError:
                _fail('--backfill-migration 格式应为 '
                      'FROM:TO:OLD_CORE:NEW_CORE')
        result = prepare_experiment(ksp_root, args.output,
                                    control_core=args.control_core,
                                    backfill_migration_history=backfill)
        if result.get('status') != STATUS_AWAITING:
            _fail(result.get('error', 'prepare 失败'))
        print(json.dumps(result, ensure_ascii=False, indent=1))
        _stderr('')
        core = result['selected_control_core']
        _stderr('选中控制核心：'
                f"{core['name']}（mass={core['mass_t']} t）")
        _stderr('操作清单：'
                f'{Path(args.output) / "instructions.md"}')
        _stderr('输入目录（把 8 个文件复制到这里）：'
                f'{Path(args.output) / "input"}')
        _stderr('完成后运行：'
                'python -m tools.research.control_mass_experiment analyze '
                f'--input {args.output} --json')
        return EXIT_OK

    if args.command == 'preflight':
        ksp_root = args.ksp_root or \
            r'D:\SteamLibrary\steamapps\common\Kerbal Space Program'
        result = preflight(ksp_root, args.vab_dir)
        if args.json:
            sys.stdout.write(json.dumps(result, ensure_ascii=False,
                                        indent=1) + '\n')
        else:
            print(f"status: {result.get('status')}")
            core = result.get('selected_control_core') or {}
            print('选中控制核心：'
                  f"{core.get('name')}（mass={core.get('mass_t')} t，"
                  f"attachRules="
                  f"{core.get('attach_rules', {}).get('raw')}）")
            plan = result.get('attachment_plan') or {}
            print('安装方式：' + str(plan.get('mode')))
            print('径向路径：' + str(plan.get('surface_path_available')))
            print('stack 路径：' + str(plan.get('stack_path_available')))
            print('M1/M3 节点：' + str(plan.get(
                'M1_one_chute_default')))
            print('M2 节点：' + str(plan.get('M2_two_chutes_default')))
            print('可完成四组实验：'
                  + str(plan.get('supports_experiment')))
            scan = result.get('scan') or {}
            print(f"扫描 {scan.get('pairs_total')} 对："
                  f"通过 {scan.get('pairs_passed')}，"
                  f"拒绝 {scan.get('pairs_rejected')}")
            for sample in scan.get('rejected_samples') or []:
                print(f"  - {sample['craft']}: {sample['codes']}")
        if result.get('status') == 'preflight_complete':
            return EXIT_OK
        return EXIT_INVALID

    result = analyze_experiment(args.input, ksp_root=args.ksp_root)
    status = result.get('experiment_status')
    if args.json:
        sys.stdout.write(json.dumps(result, ensure_ascii=False,
                                    indent=1) + '\n')
    else:
        print(f'experiment_status: {status}')
        if status == STATUS_AWAITING:
            print('等待 KSP 采集：缺少文件 '
                  f'{result["input"]["missing_files"]}')
        elif status == STATUS_INVALID:
            for diff in result.get('validation', {}).get('diffs', []):
                code = diff.get('code')
                actual = diff.get('actual')
                expected = diff.get('expected')
                detail = ''
                if actual is not None or expected is not None:
                    detail = f'（实际={actual}，期望={expected}）'
                print(f'  - [{code}] {diff.get("message")}{detail}')
        elif status == 'invalid_experiment_setup':
            print(f'  - {result.get("error")}')
        else:
            differential = result['differential']
            print('observed_one_chute_t: '
                  f"{differential['observed_one_chute_t']}")
            print('observed_two_chutes_t: '
                  f"{differential['observed_two_chutes_t']}")
            print('observed_diameter_effect_t: '
                  f"{differential['observed_diameter_effect_t']}")
            for name, model in differential['models'].items():
                print(f"model {name}: "
                      f"predicted={model['predicted_increment_t']} "
                      f"observed={model['observed_increment_t']} "
                      f"error={model['absolute_error_t']} "
                      f"verdict={model['verdict']}")
            linearity = differential['linearity_check']
            if linearity:
                print(f"linearity: expected="
                      f"{linearity['predicted_two_chutes_t']} "
                      f"observed={linearity['observed_two_chutes_t']} "
                      f"verdict={linearity['verdict']}")
            diameter = differential['diameter_check']
            print(f"diameter: predicted="
                  f"{diameter['predicted_chute_mass_delta_t']} "
                  f"observed={diameter['observed_diameter_effect_t']} "
                  f"verdict={diameter['verdict']}")
    if status in (STATUS_ANALYZED, STATUS_AWAITING):
        return EXIT_OK
    return EXIT_INVALID


if __name__ == '__main__':
    sys.exit(main())
