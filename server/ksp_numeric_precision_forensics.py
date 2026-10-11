"""
KSP whole-craft numeric-precision forensics (phase 6B.3C.2A).
==============================================================

Tests whether the unexplained ``Neutrons II 354S`` residual is explained by
KSP/C# single-precision (IEEE-754 binary32) accumulation semantics.

Five candidate aggregation models are evaluated per vehicle, all starting
from the SAME per-part ``current_part_mass_estimate_t`` values:

1. ``double_order``            — Python double, craft PART order;
2. ``input_float32_then_double`` — each part cast to binary32, then summed
                                in double;
3. ``float32_craft_order``     — each part cast to binary32, accumulated
                                step-by-step in binary32, craft order;
4. ``float32_reverse_order``   — same as 3 but reversed craft order (order
                                sensitivity control only);
5. ``double_sum_then_float32`` — double sum, single final cast (proves
                                final-cast != stepwise single precision).

``to_float32`` uses ``struct.pack/unpack`` — a strict IEEE-754 binary32
round-trip, never platform-default floating behaviour.  Booleans, NaN,
infinity, non-numeric strings and negative masses are rejected; a rejected
item makes the model incomplete (never skipped to return a "complete"
total).

Part order is always the deterministic craft PART order (with
``craft_index`` recorded); missing / duplicate / unexpected / identity-
mismatched evidence makes the model incomplete — evidence array order is
never trusted.

Pure module: no disk, no mutation, no SQL/YAML, no Flask,
JSON-serializable and deterministic.
"""

from __future__ import annotations

import math
import struct
from typing import Dict, List, Optional

SCHEMA_VERSION = 1
SOURCE_KIND = 'ksp_numeric_precision_forensics'

MODELS = ('double_order', 'input_float32_then_double',
          'float32_craft_order', 'float32_reverse_order',
          'double_sum_then_float32')

INSUFFICIENT_EVIDENCE = 'insufficient_evidence'
SUPPORTED_BY_DATASET = 'supported_by_dataset'


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


def to_float32(value):
    """Strict IEEE-754 binary32 round-trip.

    Returns the exact double representation of the binary32 value, or None
    for bool / NaN / infinity / non-numeric / negative / out-of-range
    inputs (an OverflowError from the platform struct layer is never
    leaked).  ``-0.0`` is normalized to legal ``+0.0``.
    """
    number = _finite(value)
    if number is None or number < 0:
        return None
    if number == 0:
        number = 0.0
    try:
        packed = struct.pack('<f', number)
    except OverflowError:
        return None
    return struct.unpack('<f', packed)[0]


# Largest finite binary32 value (struct round-trip of ~3.4028235e38).
_FLOAT32_MAX = struct.unpack('<f', struct.pack('<f', 3.4028235e38))[0]


def classify_float32_input(value) -> str:
    """Stable diagnosis for an input that ``to_float32`` rejects.

    Returns ``'ok'`` for usable inputs and one of
    ``invalid_float32_input`` / ``float32_overflow`` otherwise.
    """
    number = _finite(value)
    if number is None or number < 0:
        return 'invalid_float32_input'
    if number > _FLOAT32_MAX:
        return 'float32_overflow'
    try:
        struct.pack('<f', number)
    except OverflowError:
        return 'float32_overflow'
    return 'ok'


def _sum_float32_with_status(values):
    """Stepwise in-order binary32 accumulation with a failure diagnosis.

    Returns ``(total, None)`` or ``(None, code)`` where code is
    ``invalid_float32_input`` / ``float32_overflow`` /
    ``float32_accumulation_overflow``.
    """
    accumulator = 0.0  # binary32 +0.0
    for value in values:
        item = to_float32(value)
        if item is None:
            return None, classify_float32_input(value)
        next_accumulator = to_float32(accumulator + item)
        if next_accumulator is None:
            return None, 'float32_accumulation_overflow'
        accumulator = next_accumulator
    return accumulator, None


def sum_float32_in_order(values) -> Optional[float]:
    """Stepwise in-order binary32 accumulation (None when any item or the
    accumulation itself is invalid / overflows; items are never skipped)."""
    total, _ = _sum_float32_with_status(values)
    return total


def _identity_conflict(part, instance) -> bool:
    for field in ('part', 'base_part', 'persistent_id'):
        left = part.get(field)
        right = instance.get(field)
        if left is not None and right is not None and left != right:
            return True
    return False


def build_ordered_masses(craft_draft, mass_evidence) -> dict:
    """Deterministic craft-order one-to-one (part, mass) mapping.

    Returns the per-part masses strictly in craft PART order with the
    used craft_index sequence, or incomplete when the mapping cannot be
    established uniquely.
    """
    parts = list((craft_draft or {}).get('parts') or [])
    instances = list((mass_evidence or {}).get('instances') or [])
    diagnostics: List[dict] = []
    if not parts:
        diagnostics.append({
            'code': 'empty_craft',
            'message': 'craft 没有 PART；空 craft 不等于零质量 craft',
        })
    by_index: Dict[object, List[dict]] = {}
    for instance in instances:
        by_index.setdefault(instance.get('craft_index'), []).append(instance)

    craft_indexes = [part.get('index') for part in parts]
    craft_index_set = set(craft_indexes)
    global_duplicate = len(craft_index_set) != len(craft_indexes)
    if global_duplicate:
        diagnostics.append({
            'code': 'duplicate_craft_part_index',
            'message': 'craft 中存在重复的 PART index',
        })
    unexpected = [index for index in by_index
                  if index not in craft_index_set]
    for index in unexpected:
        diagnostics.append({
            'code': 'unexpected_evidence_index',
            'message': f'evidence 中出现 craft 不存在的 index {index!r}',
        })

    ordered_masses: List[Optional[float]] = []
    used_indexes: List = []
    complete = bool(parts) and not global_duplicate and not unexpected
    if not parts:
        complete = False
    for part in parts:
        index = part.get('index')
        records = by_index.get(index, [])
        if len(records) > 1:
            complete = False
            diagnostics.append({
                'code': 'duplicate_evidence_index',
                'message': f'index {index!r} 有多条 evidence',
            })
            ordered_masses.append(None)
            used_indexes.append(index)
            continue
        if not records:
            complete = False
            diagnostics.append({
                'code': 'missing_evidence',
                'message': f'index {index!r} 没有 evidence',
            })
            ordered_masses.append(None)
            used_indexes.append(index)
            continue
        instance = records[0]
        if _identity_conflict(part, instance):
            complete = False
            diagnostics.append({
                'code': 'evidence_identity_mismatch',
                'message': f'index {index!r} 的身份字段与 craft 不一致',
            })
            ordered_masses.append(None)
            used_indexes.append(index)
            continue
        value = instance.get('current_part_mass_estimate_t')
        finite_value = _finite(value)
        if finite_value is None or finite_value < 0:
            complete = False
            diagnostics.append({
                'code': 'invalid_mass_value',
                'message': f'index {index!r} 的质量非法（{value!r}）',
            })
            ordered_masses.append(None)
        else:
            ordered_masses.append(finite_value)
        used_indexes.append(index)

    return {
        'input_part_count': len(parts),
        'valid_mass_count': sum(1 for mass in ordered_masses
                                if mass is not None),
        'used_craft_indexes': used_indexes,
        'ordered_masses': ordered_masses,
        'complete': complete,
        'diagnostics': diagnostics,
    }


def _model_totals(masses: List[Optional[float]], complete: bool) -> dict:
    """All five candidate totals; None when incomplete or any item invalid."""
    totals = {}
    if not complete or not masses or any(mass is None for mass in masses):
        for model in MODELS:
            totals[model] = {'estimated_total_t': None,
                             'complete': False,
                             'diagnostics': [{
                                 'code': 'model_incomplete',
                                 'message': '质量映射不完整，模型不可用',
                             }]}
        return totals

    valid = [mass for mass in masses if mass is not None]

    def entry(total, diagnostics=None):
        return {'estimated_total_t': total,
                'complete': True,
                'diagnostics': diagnostics or []}

    totals['double_order'] = entry(sum(valid))
    cast_values = [to_float32(mass) for mass in valid]
    if any(value is None for value in cast_values):
        overflowed = [mass for mass in valid
                      if to_float32(mass) is None]
        totals['input_float32_then_double'] = {
            'estimated_total_t': None,
            'complete': False,
            'diagnostics': [{
                'code': classify_float32_input(overflowed[0]),
                'message': f'输入超出 binary32 范围（{overflowed[0]!r}）',
            }],
        }
    else:
        totals['input_float32_then_double'] = entry(sum(cast_values))
    float32_total, float32_status = _sum_float32_with_status(valid)
    if float32_total is None:
        totals['float32_craft_order'] = {
            'estimated_total_t': None,
            'complete': False,
            'diagnostics': [{
                'code': float32_status or 'float32_accumulation_overflow',
                'message': '逐项 float32 累加失败',
            }],
        }
    else:
        totals['float32_craft_order'] = entry(float32_total)
    reverse_total, reverse_status = _sum_float32_with_status(
        list(reversed(valid)))
    if reverse_total is None:
        totals['float32_reverse_order'] = {
            'estimated_total_t': None,
            'complete': False,
            'diagnostics': [{
                'code': reverse_status or 'float32_accumulation_overflow',
                'message': '反向 float32 累加失败',
            }],
        }
    else:
        totals['float32_reverse_order'] = entry(reverse_total)
    final_cast = to_float32(sum(valid))
    if final_cast is None:
        totals['double_sum_then_float32'] = {
            'estimated_total_t': None,
            'complete': False,
            'diagnostics': [{
                'code': 'float32_overflow',
                'message': '最终 cast 超出 binary32 范围',
            }],
        }
    else:
        totals['double_sum_then_float32'] = entry(final_cast)
    return totals


def evaluate_five_models(craft_draft, mass_evidence, reconciliation=None,
                         pdc_diff_total=None) -> dict:
    """Per-vehicle evaluation of the five aggregation models.

    ``pdc_diff_total`` comes from the mass-forensics report (unchanged
    PDC difference); residuals are only computed when both the target mass
    and the model are complete.
    """
    ordered = build_ordered_masses(craft_draft, mass_evidence)
    totals = _model_totals(ordered['ordered_masses'], ordered['complete'])

    source_total = None
    if isinstance(reconciliation, dict):
        source_total = _finite(reconciliation.get('source_total_mass_t'))
    target_ok = source_total is not None and source_total >= 0
    pdc_diff = _finite(pdc_diff_total)

    models = {}
    double_order_total = totals['double_order']['estimated_total_t']
    for model in MODELS:
        total = totals[model]['estimated_total_t']
        entry = dict(totals[model])
        entry['residual_to_loadmeta_t'] = None
        entry['residual_after_pdc_t'] = None
        entry['delta_vs_double_order_t'] = None
        if total is not None:
            entry['delta_vs_double_order_t'] = total - double_order_total
            if target_ok:
                entry['residual_to_loadmeta_t'] = total - source_total
                if pdc_diff is not None:
                    entry['residual_after_pdc_t'] = (
                        total - source_total - pdc_diff)
        models[model] = entry

    return {
        'schema_version': SCHEMA_VERSION,
        'source_kind': SOURCE_KIND,
        'craft_filename': (craft_draft or {}).get('source', {}).get(
            'filename'),
        'input_part_count': ordered['input_part_count'],
        'valid_mass_count': ordered['valid_mass_count'],
        'used_craft_indexes': ordered['used_craft_indexes'],
        'complete': ordered['complete'],
        'diagnostics': ordered['diagnostics'],
        'models': models,
    }


def _validate_tolerance(tolerance_t):
    """Strict positive finite tolerance; None when invalid (bool, NaN,
    infinity, garbage, negative or zero)."""
    if isinstance(tolerance_t, bool):
        return None
    value = _finite(tolerance_t)
    if value is None or value <= 0:
        return None
    return value


def _model_sample_set(vehicle_numeric_reports, model):
    """Split normal vehicles into valid / incomplete samples for a model."""
    expected = []
    valid = []
    incomplete = []
    for report in vehicle_numeric_reports:
        if report.get('parachute_single_count', 0) > 0:
            continue
        name = report.get('craft_filename')
        expected.append(name)
        entry = (report.get('models') or {}).get(model) or {}
        after = entry.get('residual_after_pdc_t')
        if not report.get('complete') or not entry.get('complete') \
                or after is None:
            reason = 'model_incomplete'
            for diagnostic in (report.get('diagnostics') or []) \
                    + (entry.get('diagnostics') or []):
                reason = diagnostic.get('code', reason)
                break
            incomplete.append({'craft': name, 'reason': reason})
        else:
            valid.append((name, after))
    return expected, valid, incomplete


def fleet_model_comparison(vehicle_numeric_reports: List[dict],
                           tolerance_t=5e-5) -> dict:
    """Compare the five models over the NORMAL vehicles (no
    parachuteSingle).  Missing samples are NEVER dropped from the
    denominator: ``all_within_tolerance`` additionally requires
    ``all_samples_complete``.  Invalid tolerance input yields
    ``status='invalid_input'`` and never a supporting conclusion."""
    tol = _validate_tolerance(tolerance_t)
    if tol is None:
        models = {model: {
            'expected_sample_count': 0,
            'valid_sample_count': 0,
            'incomplete_sample_count': 0,
            'incomplete_vehicles': [],
            'within_tolerance_count': 0,
            'max_abs_error_t': None,
            'mae_t': None,
            'rmse_t': None,
            'worst_vehicle': None,
            'all_samples_complete': False,
            'all_within_tolerance': False,
            'diagnostics': [{
                'code': 'invalid_tolerance',
                'message': f'非法取证容差（{tolerance_t!r}）',
            }],
        } for model in MODELS}
        return {
            'schema_version': SCHEMA_VERSION,
            'source_kind': SOURCE_KIND,
            'status': 'invalid_input',
            'tolerance_t': None,
            'models': models,
            'conclusion': {
                'preferred_candidate': None,
                'conclusion_strength': INSUFFICIENT_EVIDENCE,
                'authoritative_ksp_semantics_confirmed': False,
                'production_use_authorized': False,
                'all_required_samples_complete': False,
                'selection_reasons': [],
                'diagnostics': [{
                    'code': 'invalid_tolerance',
                    'message': f'非法取证容差（{tolerance_t!r}）',
                }],
            },
        }

    summary = {}
    for model in MODELS:
        expected, valid, incomplete = _model_sample_set(
            vehicle_numeric_reports, model)
        expected_count = len(expected)
        valid_count = len(valid)
        incomplete_count = len(incomplete)
        within = sum(1 for _, after in valid if after is not None
                     and abs(after) <= tol)
        errors = [abs(after) for _, after in valid]
        all_complete = (expected_count > 0
                        and valid_count == expected_count
                        and incomplete_count == 0)
        all_within = all_complete and within == expected_count
        if not errors:
            summary[model] = {
                'expected_sample_count': expected_count,
                'valid_sample_count': valid_count,
                'incomplete_sample_count': incomplete_count,
                'incomplete_vehicles': incomplete,
                'within_tolerance_count': within,
                'max_abs_error_t': None,
                'mae_t': None,
                'rmse_t': None,
                'worst_vehicle': None,
                'all_samples_complete': all_complete,
                'all_within_tolerance': all_within,
                'diagnostics': [{
                    'code': 'insufficient_evidence',
                    'message': '没有可用的普通载具样本',
                }] if not expected_count else [],
            }
            continue
        mean = sum(errors) / len(errors)
        rmse = math.sqrt(sum(error * error for error in errors)
                         / len(errors))
        worst_index = max(range(len(errors)), key=errors.__getitem__)
        summary[model] = {
            'expected_sample_count': expected_count,
            'valid_sample_count': valid_count,
            'incomplete_sample_count': incomplete_count,
            'incomplete_vehicles': incomplete,
            'within_tolerance_count': within,
            'max_abs_error_t': max(errors),
            'mae_t': mean,
            'rmse_t': rmse,
            'worst_vehicle': valid[worst_index][0],
            'all_samples_complete': all_complete,
            'all_within_tolerance': all_within,
            'diagnostics': [],
        }

    conclusion = _compute_conclusion(summary)
    return {
        'schema_version': SCHEMA_VERSION,
        'source_kind': SOURCE_KIND,
        'status': 'ok',
        'tolerance_t': tol,
        'models': summary,
        'conclusion': conclusion,
    }


def _compute_conclusion(summary: dict) -> dict:
    """Dynamic machine-readable verdict (never hard-coded)."""
    diagnostics: List[dict] = []
    candidates = [model for model in MODELS
                  if summary[model]['all_within_tolerance']]
    if not candidates:
        diagnostics.append({
            'code': 'no_model_within_tolerance',
            'message': '没有模型让全部普通样本进入容差',
        })
    else:
        diagnostics.append({
            'code': 'within_tolerance_candidates',
            'message': '容差内模型: ' + ', '.join(candidates),
        })

    def min_unique(key):
        values = {model: summary[model][key] for model in MODELS}
        usable = {model: value for model, value in values.items()
                  if value is not None}
        if not usable:
            return None, False
        best = min(usable.values())
        tied = [model for model, value in usable.items()
                if value == best]
        return best, len(tied) == 1

    min_mae, mae_unique = min_unique('mae_t')
    min_rmse, rmse_unique = min_unique('rmse_t')
    min_mae_models = [model for model in MODELS
                      if summary[model]['mae_t'] == min_mae]
    min_rmse_models = [model for model in MODELS
                       if summary[model]['rmse_t'] == min_rmse]

    preferred = None
    strength = INSUFFICIENT_EVIDENCE
    reasons: List[str] = []
    if (len(candidates) == 1 and mae_unique and rmse_unique
            and min_mae_models == [candidates[0]]
            and min_rmse_models == [candidates[0]]):
        preferred = candidates[0]
        strength = 'supported_by_dataset'
        reasons = ['all_normal_samples_within_tolerance',
                   'lowest_mae', 'lowest_rmse']
    else:
        diagnostics.append({
            'code': 'no_unique_best_model',
            'message': '不存在同时满足容差、严格最低 MAE 与严格最低 RMSE '
                       '的唯一模型',
        })

    return {
        'preferred_candidate': preferred,
        'conclusion_strength': strength,
        'authoritative_ksp_semantics_confirmed': False,
        'production_use_authorized': False,
        'all_required_samples_complete': bool(
            candidates and summary[candidates[0]]['all_samples_complete']),
        'selection_reasons': reasons,
        'diagnostics': diagnostics,
    }
