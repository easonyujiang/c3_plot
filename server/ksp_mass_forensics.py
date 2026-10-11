"""
KSP mass-residual forensics (phase 6B.3C.1 / 6B.3C.1-R).
==========================================================

Collects per-instance evidence for the known dynamic-mass modules and
tests ONE falsifiable candidate formula against the real reconciliation
residuals: the SSTUProceduralDecoupler annular-ring shell mass

    R = diameter / 2
    r = max(R - thickness, 0)
    volume = pi * (R^2 - r^2) * height
    candidate_mass_t = volume * massPerCubicMeter

Everything here is FORENSIC ONLY — candidate formulas are never used for
formal mass estimation, never written back, and are explicitly not claimed
to be the official KSP/SSTU settlement formula.

Rework contract (6B.3C.1-R):

* partial PDC evidence is NEVER presented as a full correction: complete
  totals are null unless every expected PDC instance has a unique, valid
  candidate mass and valid current estimate (known_*_subtotal_t keeps the
  partial sums);
* one-to-one correspondence between PDC parts and mass evidence (missing /
  duplicate / unexpected / identity mismatches detected, never silently
  choosing a record);
* strict numeric boundaries (booleans, NaN, infinity, numeric strings,
  negative masses); config massPerCubicMeter conflicts are ambiguous;
* an empty fleet or all-zero features never produces a supporting verdict;
  collinearity uses full count vectors (perfect / not_collinear /
  insufficient_variation / invalid_evidence);
* deterministic JSON: stable fingerprint fields only (no built_at).

Pure module: no disk access, no mutation of inputs, no database, no Flask,
JSON-serializable and deterministic.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

SCHEMA_VERSION = 1
SOURCE_KIND = 'ksp_mass_forensics'

# Forensic comparison tolerance (loadmeta serialization precision only).
FORENSIC_ABS_TOLERANCE_T = 5e-5

# Hypothesis verdicts
SUPPORTED = 'supported'
CONTRADICTED = 'contradicted'
INSUFFICIENT_EVIDENCE = 'insufficient_evidence'

# Collinearity statuses
COLINEAR_PERFECT = 'perfect'
COLINEAR_NOT = 'not_collinear'
COLINEAR_INSUFFICIENT_VARIATION = 'insufficient_variation'
COLINEAR_INVALID = 'invalid_evidence'

_PDC_MODULE = 'SSTUProceduralDecoupler'

# Stable fingerprint fields only (built_at excluded for determinism).
_FINGERPRINT_FIELDS = ('schema_version', 'source_kind', 'config_cache_path',
                       'file_size', 'mtime_ns', 'sha256',
                       'patched_node_count')


def _finite(value):
    """Strict finite-float parse: booleans, NaN, infinity and non-numeric
    values are rejected; numeric strings are accepted."""
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


def _stable_fingerprint(mass_evidence) -> Optional[dict]:
    source = (mass_evidence or {}).get('index_fingerprint')
    if not isinstance(source, dict):
        return None
    return {key: source.get(key) for key in _FINGERPRINT_FIELDS}


def annular_ring_candidate_mass(diameter, height, thickness,
                                mass_per_cubic_meter) -> dict:
    """Candidate PDC mass from strict, validated parameters.

    Returns ``{'mass_t': float, 'diagnostics': []}`` or
    ``{'mass_t': None, 'diagnostics': [...]}`` when the geometry is
    missing / non-finite / out of range (never silently falls back).
    """
    diagnostics: List[dict] = []
    d = _finite(diameter)
    h = _finite(height)
    t = _finite(thickness)
    m = _finite(mass_per_cubic_meter)
    if None in (d, h, t, m):
        diagnostics.append({
            'code': 'insufficient_pdc_evidence',
            'message': ('PDC 参数不完整或非法（diameter/height/thickness/'
                        'massPerCubicMeter 必须为有限数值）'),
        })
        return {'mass_t': None, 'diagnostics': diagnostics}
    if d <= 0 or h <= 0:
        diagnostics.append({
            'code': 'invalid_pdc_geometry',
            'message': f'diameter（{d}）与 height（{h}）必须大于 0',
        })
        return {'mass_t': None, 'diagnostics': diagnostics}
    if t < 0 or m < 0:
        diagnostics.append({
            'code': 'invalid_pdc_geometry',
            'message': f'thickness（{t}）与 massPerCubicMeter（{m}）不得为负',
        })
        return {'mass_t': None, 'diagnostics': diagnostics}
    if t > d / 2:
        diagnostics.append({
            'code': 'invalid_pdc_geometry',
            'message': f'thickness（{t}）超过外半径（{d / 2}）',
        })
        return {'mass_t': None, 'diagnostics': diagnostics}
    outer = d / 2
    inner = max(outer - t, 0)
    mass = math.pi * (outer ** 2 - inner ** 2) * h * m
    return {'mass_t': mass, 'diagnostics': diagnostics}


def _resolve_mass_per_cubic_meter(ksp_index, canonical_name) -> dict:
    """Resolve massPerCubicMeter from config evidence.

    Unique value -> used; several numerically-identical values -> used
    (source count kept); conflicting values -> ambiguous (never picks the
    first record).
    """
    if ksp_index is None or not canonical_name:
        return {'value': None, 'diagnostics': [{
            'code': 'insufficient_pdc_evidence',
            'message': '索引或匹配名称缺失，无法取得 massPerCubicMeter',
        }]}
    record = ksp_index.primary_part(canonical_name)
    if not record:
        return {'value': None, 'diagnostics': [{
            'code': 'insufficient_pdc_evidence',
            'message': f'索引中不存在部件 {canonical_name!r}',
        }]}
    values = []
    for entry in (record.get('module_evidence') or {}).get(_PDC_MODULE, []):
        value = _finite(entry.get('massPerCubicMeter'))
        if value is not None:
            values.append(value)
    if not values:
        return {'value': None, 'diagnostics': [{
            'code': 'insufficient_pdc_evidence',
            'message': '索引配置中缺少 massPerCubicMeter',
        }]}
    first = values[0]
    if all(value == first for value in values[1:]):
        return {'value': first, 'diagnostics': [{
            'code': 'mass_per_cubic_meter_resolved',
            'message': (f'massPerCubicMeter 采用 {first!r}'
                        f'（{len(values)} 处来源一致）'),
        }]}
    return {'value': None, 'diagnostics': [{
        'code': 'ambiguous_mass_per_cubic_meter',
        'message': f'massPerCubicMeter 冲突（{values!r}），不采用任何值',
    }]}


def _resolve_pdc_module_state(part) -> dict:
    """Resolve the PDC module state of a PART.

    No entry -> missing state.  Several entries: used only when their
    normalized finite geometry is identical (source count kept); any
    differing entry -> ambiguous (never picks the first).
    """
    entries = (part.get('module_evidence') or {}).get(_PDC_MODULE, [])
    if not entries:
        return {'entry': None, 'diagnostics': [{
            'code': 'missing_pdc_module_state',
            'message': '该 PART 没有 PDC 模块状态',
        }]}

    def normalized(entry):
        return (_finite(entry.get('diameter')),
                _finite(entry.get('height')),
                _finite(entry.get('thickness')))

    first = normalized(entries[0])
    if all(normalized(entry) == first for entry in entries[1:]):
        return {'entry': entries[0], 'diagnostics': [{
            'code': 'pdc_module_state_resolved',
            'message': f'采用 PDC 模块状态（{len(entries)} 处一致）',
        }]}
    return {'entry': None, 'diagnostics': [{
        'code': 'ambiguous_pdc_module_state',
        'message': '同一 PART 保存多个不同的 PDC 模块状态，不选择任何一条',
    }]}


def _evidence_index(evidence_instances: List[dict]):
    """group evidence by craft_index; detects duplicates without dropping."""
    by_index: Dict[object, List[dict]] = {}
    for instance in evidence_instances:
        by_index.setdefault(instance.get('craft_index'), []).append(instance)
    return by_index


def _pdc_instance_record(part, entry, instance, config_mpm) -> dict:
    candidate = annular_ring_candidate_mass(
        entry.get('diameter'), entry.get('height'),
        entry.get('thickness'), config_mpm)
    current = instance.get('current_part_mass_estimate_t')
    current_f = _finite(current)
    diff = None
    if candidate['mass_t'] is not None and current_f is not None \
            and current_f >= 0:
        diff = current_f - candidate['mass_t']
    diagnostics = list(candidate['diagnostics'])
    if current is None or current_f is None:
        diagnostics.append({
            'code': 'invalid_current_mass',
            'message': f'当前估计质量非法（{current!r}），差值不可计算',
        })
    elif current_f < 0:
        diagnostics.append({
            'code': 'negative_current_mass',
            'message': f'当前估计质量为负（{current_f!r}）',
        })
    return {
        'craft_index': instance.get('craft_index'),
        'part': part.get('part'),
        'base_part': part.get('base_part'),
        'diameter_raw': entry.get('diameter_raw'),
        'diameter': entry.get('diameter'),
        'height_raw': entry.get('height_raw'),
        'height': entry.get('height'),
        'thickness_raw': entry.get('thickness_raw'),
        'thickness': entry.get('thickness'),
        'candidate_mass_t': candidate['mass_t'],
        'current_part_mass_estimate_t': current,
        'diff_t': diff,
        'diagnostics': diagnostics,
    }


def _identity_conflict(part, instance) -> bool:
    """Evidence identity must match the craft part whenever both sides
    provide a value for part / base_part / persistent_id."""
    for field in ('part', 'base_part', 'persistent_id'):
        left = part.get(field)
        right = instance.get(field)
        if left is not None and right is not None and left != right:
            return True
    return False


def analyze_vehicle_forensics(craft_draft, mass_evidence, reconciliation,
                              ksp_index=None) -> dict:
    """Per-vehicle forensic analysis (pure, read-only)."""
    parts = list((craft_draft or {}).get('parts') or [])
    evidence_instances = list((mass_evidence or {}).get('instances') or [])
    by_index = _evidence_index(evidence_instances)

    # ---- global craft/evidence index conservation -------------------------
    craft_indexes = [part.get('index') for part in parts]
    craft_index_set = set(craft_indexes)
    global_duplicate_index = len(craft_index_set) != len(craft_indexes)
    unexpected_evidence = [
        instance.get('craft_index') for instance in evidence_instances
        if instance.get('craft_index') not in craft_index_set]

    pdc_parts = [part for part in parts
                 if (part.get('module_evidence') or {}).get(_PDC_MODULE)]
    expected_count = len(pdc_parts)

    pdc_instances = []
    missing_or_invalid: List[dict] = []
    evidence_count = 0
    for part in pdc_parts:
        index = part.get('index')
        records = by_index.get(index, [])
        if len(records) > 1:
            missing_or_invalid.append({
                'craft_index': index,
                'part': part.get('part'),
                'base_part': part.get('base_part'),
                'reasons': ['duplicate_evidence_index'],
            })
            continue
        if not records:
            missing_or_invalid.append({
                'craft_index': index,
                'part': part.get('part'),
                'base_part': part.get('base_part'),
                'reasons': ['missing_evidence'],
            })
            continue
        instance = records[0]
        if _identity_conflict(part, instance):
            missing_or_invalid.append({
                'craft_index': index,
                'part': part.get('part'),
                'base_part': part.get('base_part'),
                'reasons': ['evidence_identity_mismatch'],
            })
            continue
        evidence_count += 1
        config = _resolve_mass_per_cubic_meter(
            ksp_index, instance.get('canonical_name'))
        state = _resolve_pdc_module_state(part)
        if state['entry'] is None:
            missing_or_invalid.append({
                'craft_index': index,
                'part': part.get('part'),
                'base_part': part.get('base_part'),
                'reasons': [state['diagnostics'][0]['code']],
            })
            continue
        record = _pdc_instance_record(part, state['entry'], instance,
                                      config['value'])
        record['diagnostics'] = (record['diagnostics']
                                 + config['diagnostics']
                                 + state['diagnostics'])
        pdc_instances.append(record)
        if record['candidate_mass_t'] is None or record['diff_t'] is None:
            missing_or_invalid.append({
                'craft_index': index,
                'part': part.get('part'),
                'base_part': part.get('base_part'),
                'reasons': ['invalid_candidate_or_diff'],
            })
    if global_duplicate_index:
        missing_or_invalid.append({
            'craft_index': None,
            'part': None,
            'base_part': None,
            'reasons': ['duplicate_craft_part_index'],
        })
    for unexpected_index in unexpected_evidence:
        missing_or_invalid.append({
            'craft_index': unexpected_index,
            'part': None,
            'base_part': None,
            'reasons': ['unexpected_evidence_index'],
        })

    valid_candidates = sum(1 for inst in pdc_instances
                           if inst['candidate_mass_t'] is not None)
    complete = (expected_count > 0
                and evidence_count == expected_count
                and valid_candidates == expected_count
                and not global_duplicate_index
                and not unexpected_evidence
                and not missing_or_invalid)

    known_candidate = sum(inst['candidate_mass_t']
                          for inst in pdc_instances
                          if inst['candidate_mass_t'] is not None)
    known_diff = sum(inst['diff_t'] for inst in pdc_instances
                     if inst['diff_t'] is not None)

    residual = reconciliation.get('residual_include_all_t')
    residual_f = _finite(residual) if not isinstance(residual, bool) else None
    residual_after_pdc = None
    if complete and residual_f is not None:
        residual_after_pdc = residual_f - known_diff
    diagnostics = []
    if residual is None or residual_f is None:
        diagnostics.append({
            'code': 'invalid_residual',
            'message': f'整箭残差非法（{residual!r}），无法计算扣除后残差',
        })
    if missing_or_invalid:
        diagnostics.append({
            'code': 'pdc_evidence_incomplete',
            'message': (f'PDC 证据不完整：expected={expected_count} '
                        f'evidence={evidence_count} '
                        f'valid={valid_candidates}；'
                        + ', '.join(sorted({reason for item
                                            in missing_or_invalid
                                            for reason in item['reasons']}))),
        })

    chute_evidence = []
    parachute_count = 0
    mk1pod_count = 0
    for part in parts:
        base = part.get('base_part')
        if base == 'parachuteSingle':
            parachute_count += 1
            chute_evidence.append(_chute_evidence(part, ksp_index))
        if base == 'mk1pod.v2':
            mk1pod_count += 1
            chute_evidence.append(_chute_evidence(part, ksp_index))

    return {
        'schema_version': SCHEMA_VERSION,
        'source_kind': SOURCE_KIND,
        'craft_filename': (craft_draft or {}).get('source', {}).get(
            'filename'),
        'index_fingerprint': _stable_fingerprint(mass_evidence),
        'reconciliation_status': reconciliation.get(
            'reconciliation_status'),
        'residual_include_all_t': residual_f,
        'pdc_instances': pdc_instances,
        'pdc_instance_count': expected_count,
        'pdc_expected_instance_count': expected_count,
        'pdc_evidence_instance_count': evidence_count,
        'pdc_valid_candidate_count': valid_candidates,
        'pdc_candidate_complete': complete,
        'pdc_missing_or_invalid_instances': missing_or_invalid,
        'known_pdc_candidate_mass_subtotal_t':
            round(known_candidate, 12) if valid_candidates else None,
        'known_pdc_candidate_diff_subtotal_t':
            round(known_diff, 12) if any(inst['diff_t'] is not None
                                         for inst in pdc_instances) else None,
        'pdc_candidate_mass_total_t':
            round(known_candidate, 12) if complete else None,
        'pdc_candidate_diff_total_t':
            round(known_diff, 12) if complete else None,
        'residual_after_pdc_t': residual_after_pdc,
        'parachute_single_count': parachute_count,
        'mk1pod_v2_count': mk1pod_count,
        'chute_evidence': chute_evidence,
        'diagnostics': diagnostics,
    }


def _chute_evidence(part, ksp_index) -> dict:
    """parachute/mk1pod mass-relevant module evidence (craft + config)."""
    state = part.get('module_evidence') or {}
    config = {}
    if ksp_index is not None:
        record = ksp_index.primary_part(part.get('base_part'))
        if record:
            config = record.get('module_evidence') or {}
    return {
        'base_part': part.get('base_part'),
        'part': part.get('part'),
        'craft': state,
        'config': config,
    }


def _collinearity_status(vehicle_reports: List[dict]) -> dict:
    """Compare FULL count vectors; never reduces to booleans."""
    parachute = []
    mk1pod = []
    for report in vehicle_reports:
        p = report.get('parachute_single_count')
        m = report.get('mk1pod_v2_count')
        if (not isinstance(p, int) or isinstance(p, bool) or p < 0
                or not isinstance(m, int) or isinstance(m, bool) or m < 0):
            return {'status': COLINEAR_INVALID,
                    'message': '存在非法或缺失的计数'}
        parachute.append(p)
        mk1pod.append(m)
    if not parachute:
        return {'status': COLINEAR_INSUFFICIENT_VARIATION,
                'message': '空样本，无法判断共线性'}
    if parachute != mk1pod:
        return {'status': COLINEAR_NOT,
                'message': '两列计数不一致，不共线'}
    if any(value != 0 for value in parachute) \
            and any(value == 0 for value in parachute):
        return {'status': COLINEAR_PERFECT,
                'message': '两列计数逐项相同且存在 0 与非 0，完全共线'}
    return {'status': COLINEAR_INSUFFICIENT_VARIATION,
            'message': '特征缺乏变化（全 0 或全非 0），不是有效共线证据'}


def fleet_forensics(vehicle_reports: List[dict]) -> dict:
    """Aggregate forensic verdicts over a fleet of vehicle reports."""
    pdc_total = 0
    normal_after = []
    normal_names = []
    special_after = []
    special_names = []
    incomplete_pdc = 0
    for report in vehicle_reports:
        pdc_total += report.get('pdc_instance_count', 0)
        if not report.get('pdc_candidate_complete', True):
            incomplete_pdc += 1
        name = report.get('craft_filename')
        after = report.get('residual_after_pdc_t')
        if report.get('parachute_single_count', 0) > 0:
            special_after.append(after)
            special_names.append(name)
        else:
            normal_after.append(after)
            normal_names.append(name)

    # PDC hypothesis: never supported on empty / incomplete evidence.
    pdc_diagnostics = []
    if not vehicle_reports:
        pdc_hypothesis = INSUFFICIENT_EVIDENCE
        pdc_diagnostics.append({'code': 'empty_fleet',
                                'message': '没有载具样本，无法判定'})
    elif not normal_after or any(v is None for v in normal_after) \
            or incomplete_pdc:
        pdc_hypothesis = INSUFFICIENT_EVIDENCE
        pdc_diagnostics.append({
            'code': 'pdc_evidence_incomplete',
            'message': '普通载具的 PDC 证据不完整，无法得出支持性结论',
        })
    elif all(abs(v) <= FORENSIC_ABS_TOLERANCE_T for v in normal_after):
        pdc_hypothesis = SUPPORTED
    elif all(abs(v) <= 10 * FORENSIC_ABS_TOLERANCE_T
             for v in normal_after):
        pdc_hypothesis = INSUFFICIENT_EVIDENCE
    else:
        pdc_hypothesis = CONTRADICTED

    collinearity = _collinearity_status(vehicle_reports)
    if collinearity['status'] == COLINEAR_PERFECT:
        remaining = INSUFFICIENT_EVIDENCE
        remaining_message = (
            'parachuteSingle 与 mk1pod.v2 完全共线，剩余 ~0.075 t 无法'
            '归因到两者之一')
    else:
        remaining = INSUFFICIENT_EVIDENCE
        remaining_message = collinearity['message']

    return {
        'schema_version': SCHEMA_VERSION,
        'source_kind': SOURCE_KIND,
        'total_pdc_instances': pdc_total,
        'normal_vehicle_count': len(normal_after),
        'normal_vehicle_after_pdc': [
            {'craft': n, 'residual_after_pdc_t': v}
            for n, v in zip(normal_names, normal_after)
        ],
        'normal_vehicles_within_tolerance': sum(
            1 for v in normal_after
            if v is not None and abs(v) <= FORENSIC_ABS_TOLERANCE_T),
        'special_vehicle_count': len(special_after),
        'special_vehicle_after_pdc': [
            {'craft': n, 'residual_after_pdc_t': v}
            for n, v in zip(special_names, special_after)
        ],
        'special_residual_band_t': 0.075,
        'collinearity_status': collinearity['status'],
        'colinear_parachute_mk1pod': (
            collinearity['status'] == COLINEAR_PERFECT),
        'pdc_hypothesis_status': pdc_hypothesis,
        'pdc_hypothesis_diagnostics': pdc_diagnostics,
        'remaining_residual_hypothesis_status': remaining,
        'remaining_residual_message': remaining_message,
        'forensic_abs_tolerance_t': FORENSIC_ABS_TOLERANCE_T,
    }
