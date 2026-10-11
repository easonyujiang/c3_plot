"""
Maximum payload capacity search for an orbital mission — pure Python, no I/O.
============================================================================

Repeatedly calls ``evaluate_payload_for_mission`` over the dv_curve's legal
payload range to bracket the largest verifiable payload for one rocket and
one mission.  The search NEVER re-implements parking-orbit replay, stage
assignment, ignition counting, rocket equations or mission simulation: the
single-payload evaluator is the only authority on feasibility.

Search strategy — ``grid_guard_then_bisection``:

    1. monotonicity guard: evaluate ``monotonicity_samples`` equally spaced
       points (including both calibration endpoints) and require a
       feasible* -> infeasible* sequence (a feasible point after an
       infeasible one is ``non_monotonic`` — the search stops, it never
       pretends a sampled point is the maximum);
    2. bisection between the highest verified-feasible sample and the lowest
       verified-infeasible sample until the bracket is within
       ``payload_tolerance_t``;
    3. ``max_payload_t`` is ALWAYS a verified feasible lower bound — never
       the interval midpoint, never the infeasible upper bound.

All payload evaluations go through an internal cache keyed by the float
payload; equal payloads are never evaluated twice.  The search range is
strictly [calibration_min_payload_t, calibration_max_payload_t] — no
extrapolation, no guessing from rocket wet mass.

Dependencies: standard library, mission_simulation.py and
mission_payload_evaluator.py.  No Flask, SQLite, server.models, frontend,
YAML, .craft/VAB or NumPy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real
from typing import List, Optional

from mission_simulation import (
    DataQuality,
    FailureCode,
    to_dict as _contract_to_dict,
)
from mission_payload_evaluator import (
    PayloadFeasibilityResult,
    calibration_payload_bounds,
    evaluate_payload_for_mission,
)

_SEARCH_METHOD = 'grid_guard_then_bisection'


# =============================================================================
# Result type
# =============================================================================


@dataclass
class MissionCapacityResult:
    """Result of a maximum-payload search for one rocket and one mission.

    ``capacity_status`` is one of: bounded / below_calibration_range /
    above_calibration_range / data_insufficient / non_monotonic /
    evaluation_limit.  ``max_payload_t`` is set ONLY for ``bounded`` and is
    always a verified-feasible lower bound.  ``best_feasible_evaluation`` and
    ``first_infeasible_evaluation`` keep the full single-payload results;
    ``evaluations`` holds only light summaries.  ``to_dict()`` is directly
    JSON-serializable.
    """
    rocket_ref: str
    success: bool
    capacity_status: str
    max_payload_t: Optional[float]
    feasible_lower_bound_t: Optional[float]
    infeasible_upper_bound_t: Optional[float]
    payload_tolerance_t: float
    failure_code: FailureCode
    failure_reason_zh: str
    data_quality: DataQuality
    best_feasible_evaluation: Optional[PayloadFeasibilityResult]
    first_infeasible_evaluation: Optional[PayloadFeasibilityResult]
    evaluations: List[dict]
    warnings: List[str]
    metadata: dict

    def to_dict(self):
        return _contract_to_dict(self)


# =============================================================================
# Validation helpers
# =============================================================================


def _require_finite_positive(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError(f'{name} must be a finite number, got {value!r}')
    value = float(value)
    if value <= 0:
        raise ValueError(f'{name} must be > 0, got {value}')
    return value


def _require_int(value, name, minimum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{name} must be an integer, got {value!r}')
    if value < minimum:
        raise ValueError(f'{name} must be >= {minimum}, got {value}')
    return value


def _base_warnings():
    return [
        '基于 dv_curve 校准插值，仅在校准载荷范围内搜索。',
        '单调性仅经过有限采样保护，不能证明连续单调，也不能排除狭窄'
        '可行岛。',
        'whole_burn_per_stage=True（一次燃烧必须由单级完整完成）。',
        '未模拟有限推力、级寿命和并联燃烧。',
    ]


# =============================================================================
# Search
# =============================================================================


def find_max_payload_for_mission(
        rocket_ref: str,
        stages,
        requirements,
        dv_curve,
        data_quality: DataQuality = DataQuality.EXACT,
        payload_tolerance_t: float = 0.001,
        monotonicity_samples: int = 17,
        max_evaluations: int = 128,
        max_search_nodes: int = 10000,
        ascent_adjustment_dv_m_s: float = 0.0,
) -> MissionCapacityResult:
    """Find the largest verifiable payload for one mission; see module doc.

    Invalid calling arguments raise ValueError.  Malformed or non-monotonic
    dv_curve raises ValueError (never silently fixed).  Never modifies any
    input.  ``max_search_nodes`` is passed through to the single-payload
    evaluator untouched.
    """
    if not isinstance(rocket_ref, str) or not rocket_ref.strip():
        raise ValueError('rocket_ref must be a non-empty string')
    if (isinstance(ascent_adjustment_dv_m_s, bool) or not isinstance(ascent_adjustment_dv_m_s, Real)
            or not math.isfinite(ascent_adjustment_dv_m_s)):
        raise ValueError('ascent_adjustment_dv_m_s must be a finite number')
    tolerance = _require_finite_positive(payload_tolerance_t,
                                         'payload_tolerance_t')
    samples = _require_int(monotonicity_samples, 'monotonicity_samples', 3)
    max_evals = _require_int(max_evaluations, 'max_evaluations', 1)
    if max_evals < samples:
        raise ValueError(
            f'max_evaluations ({max_evals}) must be >= monotonicity_samples '
            f'({samples})')

    lo, hi = calibration_payload_bounds(dv_curve)

    # --- evaluation cache ---------------------------------------------------
    cache = {}
    calls = 0
    hits = 0

    def evaluate(payload):
        nonlocal calls, hits
        key = float(payload)
        if key in cache:
            hits += 1
            return cache[key]
        correction = ({'ascent_adjustment_dv_m_s': ascent_adjustment_dv_m_s}
                      if ascent_adjustment_dv_m_s else {})
        result = evaluate_payload_for_mission(
            rocket_ref, stages, requirements, dv_curve, key,
            data_quality=data_quality, max_search_nodes=max_search_nodes, **correction)
        cache[key] = result
        calls += 1
        return result


    def classify(result):
        if result.feasible:
            return 'feasible'
        if result.failure_code is FailureCode.DATA_INSUFFICIENT:
            return 'unknown'
        return 'infeasible'

    def snap(payload):
        return {
            'payload_t': float(payload),
            'feasible': cache[float(payload)].feasible,
            'failure_code': cache[float(payload)].failure_code.value,
            'failure_phase': cache[float(payload)].metadata.get(
                'failure_phase'),
            'data_quality': cache[float(payload)].data_quality.value,
            'calibrated_remaining_dv_m_s':
                cache[float(payload)].calibrated_remaining_dv_m_s,
            'assigned_stage_indices': list(
                cache[float(payload)].stage_plan.metadata.get(
                    'assigned_stage_indices', [])
                if cache[float(payload)].stage_plan is not None else []),
        }

    def evaluations_sorted():
        return [snap(p) for p in sorted(cache)]

    def make(status, failure_code, reason, quality, *, max_payload=None,
             low=None, high=None, best=None, first=None, extra_warnings=None,
             monotonicity_checked=True, boundary_width=None):
        warnings = _base_warnings()
        if extra_warnings:
            warnings.extend(extra_warnings)
        metadata = {
            'evaluation_count': calls,
            'cache_hit_count': hits,
            'calibration_payload_min_t': lo,
            'calibration_payload_max_t': hi,
            'monotonicity_samples': samples,
            'monotonicity_checked': monotonicity_checked,
            'boundary_width_t': boundary_width,
            'search_method': _SEARCH_METHOD,
            'whole_burn_per_stage': True,
        }
        return MissionCapacityResult(
            rocket_ref=rocket_ref,
            success=(status == 'bounded'),
            capacity_status=status,
            max_payload_t=max_payload,
            feasible_lower_bound_t=low,
            infeasible_upper_bound_t=high,
            payload_tolerance_t=tolerance,
            failure_code=failure_code,
            failure_reason_zh=reason,
            data_quality=quality,
            best_feasible_evaluation=best,
            first_infeasible_evaluation=first,
            evaluations=evaluations_sorted(),
            warnings=warnings,
            metadata=metadata,
        )

    # --- stage 1: monotonicity guard sampling ------------------------------
    points = [lo + (hi - lo) * (i / (samples - 1)) for i in range(samples)]
    points[-1] = hi

    sequence = []
    for point in points:
        result = evaluate(point)
        status = classify(result)
        sequence.append((point, status, result))
        if status == 'unknown':
            return make(
                'data_insufficient', FailureCode.DATA_INSUFFICIENT,
                f'单调性采样中载荷 {point:.6f} t 评估为数据不足：'
                f'{result.failure_reason_zh}',
                DataQuality.INSUFFICIENT,
                extra_warnings=[f'采样点 {point:.6f} t 数据不足'],
                monotonicity_checked=False)

    # monotonic sequence check: feasible* then infeasible*
    seen_infeasible = False
    reversal = None
    for i, (_, status, _) in enumerate(sequence):
        if status == 'infeasible':
            seen_infeasible = True
        elif status == 'feasible' and seen_infeasible:
            reversal = i
            break
    if reversal is not None:
        infeasible_point = sequence[reversal - 1][0]
        feasible_point = sequence[reversal][0]
        return make(
            'non_monotonic', FailureCode.DATA_INSUFFICIENT,
            f'校准曲线单调性反转：载荷 {infeasible_point:.6f} t 不可行，'
            f'但更高载荷 {feasible_point:.6f} t 可行',
            DataQuality.INSUFFICIENT,
            extra_warnings=[
                f'单调性反转发生在 {infeasible_point:.6f} t（不可行）→ '
                f'{feasible_point:.6f} t（可行）之间；未进行二分搜索'])

    feasible_pts = [t for t in sequence if t[1] == 'feasible']
    infeasible_pts = [t for t in sequence if t[1] == 'infeasible']

    if not feasible_pts:
        result_min = sequence[0][2]
        return make(
            'below_calibration_range', FailureCode.DATA_INSUFFICIENT,
            f'校准范围最小载荷 {lo:.6f} t 仍不可行；真实最大载荷可能低于'
            '校准范围',
            DataQuality.INSUFFICIENT, low=None, high=lo, first=result_min,
            extra_warnings=[
                f'全部 {samples} 个采样点均不可行（最小 {lo:.6f} t）'])

    if not infeasible_pts:
        result_max = sequence[-1][2]
        return make(
            'above_calibration_range', FailureCode.DATA_INSUFFICIENT,
            f'校准范围最大载荷 {hi:.6f} t 仍可行；真实最大载荷可能高于'
            '校准范围',
            DataQuality.INSUFFICIENT, low=hi, high=None, best=result_max,
            extra_warnings=[
                f'全部 {samples} 个采样点均可行（最大 {hi:.6f} t）'])

    low = feasible_pts[-1][0]
    low_result = feasible_pts[-1][2]
    high = infeasible_pts[0][0]
    high_result = infeasible_pts[0][2]

    # --- stage 2: bisection refinement -------------------------------------
    limit_reached = False
    while high - low > tolerance:
        if calls >= max_evals:
            limit_reached = True
            break
        mid = (low + high) / 2.0
        result = evaluate(mid)
        status = classify(result)
        if status == 'unknown':
            return make(
                'data_insufficient', FailureCode.DATA_INSUFFICIENT,
                f'二分搜索中载荷 {mid:.6f} t 评估为数据不足：'
                f'{result.failure_reason_zh}',
                DataQuality.INSUFFICIENT, low=low, high=high, best=low_result,
                first=high_result,
                extra_warnings=[f'二分点 {mid:.6f} t 数据不足'],
                boundary_width=high - low)
        if status == 'feasible':
            low = mid
            low_result = result
        else:
            high = mid
            high_result = result

    if limit_reached:
        return make(
            'evaluation_limit', FailureCode.DATA_INSUFFICIENT,
            f'达到最大评估次数（{max_evals}）仍未满足误差要求；当前区间 '
            f'[{low:.6f}, {high:.6f}] t 宽度 {high - low:.6f} t 大于容差 '
            f'{tolerance} t',
            DataQuality.INSUFFICIENT, low=low, high=high, best=low_result,
            first=high_result, boundary_width=high - low)

    # --- bounded: boundary audit --------------------------------------------
    audit_failures = []
    if not low_result.feasible:
        audit_failures.append('可行下界评估并非可行')
    if high_result.feasible:
        audit_failures.append('不可行上界评估并非不可行')
    if high_result.failure_code is FailureCode.DATA_INSUFFICIENT:
        audit_failures.append('不可行上界失败代码为 DATA_INSUFFICIENT')
    if low_result.metadata.get('plan_verified') is not True:
        audit_failures.append('可行下界计划未通过验证')
    if high - low > tolerance:
        audit_failures.append('边界宽度超过容差')
    if audit_failures:
        return make(
            'data_insufficient', FailureCode.DATA_INSUFFICIENT,
            '边界审计失败：' + '；'.join(audit_failures),
            DataQuality.INSUFFICIENT, low=low, high=high, best=low_result,
            first=high_result, boundary_width=high - low)

    quality = low_result.data_quality
    return make(
        'bounded', FailureCode.NONE, '', quality,
        max_payload=low, low=low, high=high, best=low_result,
        first=high_result, boundary_width=high - low)
