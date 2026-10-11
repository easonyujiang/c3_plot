"""
Single-payload orbital-mission feasibility evaluator — pure Python, no I/O.
==========================================================================

Orchestrates the whole calibrated serial multi-burn evaluation chain for ONE
explicit payload:

    1. interpolate the calibrated remaining delta-v at this payload from the
       rocket's dv_curve (compute_rocket output);
    2. compute the serial stack's total vacuum delta-v for this payload;
    3. derive the effective ascent requirement
       (ascent = total_vacuum_dv - calibrated_remaining_dv);
    4. replay the ascent to the parking orbit;
    5. plan the post-parking multi-burn mission (executing stages and
       separations);
    6. execute the plan through simulate_multistage_sequence;
    7. report feasibility with full audit trail.

This module evaluates a SINGLE payload only — no bisection, no grid search,
no maximum-capacity computation.  It never calls compute_rocket or
compute_orbit_mission (tests may build inputs with them; the module itself
must not).

Dependencies: standard library, mission_simulation.py, mission_solver.py,
mission_parking_replay.py and mission_stage_planner.py.  No Flask, SQLite,
server.models, frontend, YAML, .craft/VAB or NumPy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real
from typing import List, Optional

from mission_simulation import (
    BurnRequirement,
    DataQuality,
    FailureCode,
    MissionSimulationResult,
    StageState,
    to_dict as _contract_to_dict,
)
from mission_solver import (
    MASS_TOLERANCE_T,
    available_delta_v_m_s,
    simulate_multistage_sequence,
)
from mission_parking_replay import (
    ParkingOrbitReplayResult,
    replay_serial_ascent_to_parking,
)
from mission_stage_planner import (
    PostParkingPlanResult,
    plan_post_parking_sequence,
)

# Tolerance for the parking-state remaining-delta-v self check.
PARKING_DV_CHECK_TOLERANCE_M_S = 1e-4

# Tolerance for the dv_curve monotonicity check (dv must not increase with m).
_CURVE_MONOTONIC_TOLERANCE_M_S = 1e-6

# Tolerance for "calibrated remaining dv > total vacuum dv".
_CALIBRATION_TOTAL_TOLERANCE_M_S = 1e-6


# =============================================================================
# Result type
# =============================================================================


@dataclass
class PayloadFeasibilityResult:
    """Feasibility of ONE payload for ONE mission, with the full audit trail.

    ``parking_replay`` / ``stage_plan`` / ``simulation`` are kept when their
    pipeline stage ran (None otherwise) so the caller can audit the failure
    stage.  ``to_dict()`` is directly JSON-serializable; every value is a
    native Python number (no numpy scalars).
    """
    rocket_ref: str
    payload_t: float
    feasible: bool
    failure_code: FailureCode
    failure_reason_zh: str
    data_quality: DataQuality
    calibrated_remaining_dv_m_s: Optional[float]
    total_vacuum_dv_m_s: Optional[float]
    ascent_required_dv_m_s: Optional[float]
    parking_replay: Optional[ParkingOrbitReplayResult]
    stage_plan: Optional[PostParkingPlanResult]
    simulation: Optional[MissionSimulationResult]
    warnings: List[str]
    metadata: dict

    def to_dict(self):
        return _contract_to_dict(self)


# =============================================================================
# Validation helpers
# =============================================================================


def _require_number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError(f'{name} must be a finite number, got {value!r}')
    return float(value)


def _validate_dv_curve(dv_curve):
    """Structural validation of the dv_curve.  Calling errors (wrong type,
    NaN, duplicate/descending m, negative dv) raise ValueError; the curve is
    never sorted or silently pruned."""
    if (isinstance(dv_curve, (str, bytes))
            or not isinstance(dv_curve, (list, tuple))
            or len(dv_curve) < 2):
        raise ValueError('dv_curve must be a list/tuple with at least 2 points')
    points = []
    prev_m = None
    for i, point in enumerate(dv_curve):
        if not isinstance(point, dict):
            raise ValueError(f'dv_curve[{i}] must be a dict with m and dv')
        if 'm' not in point or 'dv' not in point:
            raise ValueError(f'dv_curve[{i}] missing m or dv')
        m = _require_number(point['m'], f'dv_curve[{i}].m')
        dv = _require_number(point['dv'], f'dv_curve[{i}].dv')
        if m <= 0:
            raise ValueError(f'dv_curve[{i}].m must be > 0')
        if dv < 0:
            raise ValueError(f'dv_curve[{i}].dv must be >= 0')
        if prev_m is not None and m <= prev_m:
            raise ValueError(
                f'dv_curve m must be strictly increasing (index {i})')
        prev_m = m
        points.append((m, dv))
    return points


def _curve_is_monotonic(points):
    for left, right in zip(points, points[1:]):
        if right[1] > left[1] + _CURVE_MONOTONIC_TOLERANCE_M_S:
            return False
    return True


def _validate_requirements(requirements):
    if (isinstance(requirements, (str, bytes))
            or not isinstance(requirements, (list, tuple))
            or not requirements):
        raise ValueError('requirements must be a non-empty list or tuple')
    reqs = list(requirements)
    seen = set()
    prev_order = None
    for i, req in enumerate(reqs):
        if not isinstance(req, BurnRequirement):
            raise ValueError(f'requirements[{i}] must be a BurnRequirement')
        if req.burn_id in seen:
            raise ValueError(f'duplicate burn_id: {req.burn_id}')
        seen.add(req.burn_id)
        if prev_order is not None and req.order <= prev_order:
            raise ValueError(
                f'requirements order must be strictly increasing '
                f'(burn_id={req.burn_id}, order={req.order})')
        prev_order = req.order
        if req.source != 'rocket_calibration':
            target = _require_number(req.target_dv_m_s,
                                     f'requirements[{i}].target_dv_m_s')
            if target <= 0:
                raise ValueError(
                    f'requirements[{i}] target_dv_m_s must be > 0 for '
                    'non-calibration burns')
            if req.preferred_stage_index is not None:
                raise ValueError(
                    f'requirements[{i}] already assigns '
                    f'preferred_stage_index={req.preferred_stage_index}')
    return reqs


# =============================================================================
# Pure functions
# =============================================================================


def calibration_payload_bounds(dv_curve):
    """(min_payload_t, max_payload_t) of the calibration dv_curve.

    Reuses the same validation as ``interpolate_calibrated_remaining_dv``
    (no second, inconsistent copy of the rules): structural errors and
    non-monotonic dv raise ValueError.  The curve is never sorted or fixed.
    Returns Python floats.
    """
    points = _validate_dv_curve(dv_curve)
    if not _curve_is_monotonic(points):
        raise ValueError('dv_curve is not monotonic (dv must not increase '
                         'with payload)')
    return points[0][0], points[-1][0]


def interpolate_calibrated_remaining_dv(payload_t, dv_curve) -> float:
    """Calibrated remaining delta-v at ``payload_t`` from the dv_curve.

    Linear interpolation between points; exact hits return the point value;
    out-of-range payloads raise ValueError (never clamped or extrapolated).
    Structural errors and non-monotonic dv raise ValueError.  Returns a
    Python float.  Never modifies the input.
    """
    payload = _require_number(payload_t, 'payload_t')
    if payload < 0:
        raise ValueError('payload_t must be >= 0')
    points = _validate_dv_curve(dv_curve)
    if not _curve_is_monotonic(points):
        raise ValueError('dv_curve is not monotonic (dv must not increase '
                         'with payload)')

    for m, dv in points:
        if payload == m:
            return dv
    if payload < points[0][0] or payload > points[-1][0]:
        raise ValueError(
            f'payload_t ({payload} t) is outside the calibration range '
            f'[{points[0][0]}, {points[-1][0]}] t')
    for (m0, dv0), (m1, dv1) in zip(points, points[1:]):
        if m0 <= payload <= m1:
            fraction = (payload - m0) / (m1 - m0)
            return float(dv0 + fraction * (dv1 - dv0))
    raise ValueError('payload_t outside calibration range')  # unreachable


def compute_serial_stack_vacuum_dv(stages, payload_t) -> float:
    """Total vacuum delta-v of the serial stack for ``payload_t``.

    Uses ``available_delta_v_m_s`` for every attached stage in input order
    with staging (after a stage exhausts its propellant, its dry mass is
    discarded before the next stage burns).  Detached stages are ignored.
    Parallel stages or stages without Isp raise ValueError (data
    insufficient — they cannot be treated as serial stages).  Returns a
    finite non-negative Python float.  Never modifies any stage.
    """
    if (isinstance(stages, (str, bytes))
            or not isinstance(stages, (list, tuple))
            or not stages):
        raise ValueError('stages must be a non-empty list or tuple')
    stages = list(stages)
    for i, stage in enumerate(stages):
        if not isinstance(stage, StageState):
            raise ValueError(f'stages[{i}] must be a StageState')
    payload = _require_number(payload_t, 'payload_t')
    if payload < 0:
        raise ValueError('payload_t must be >= 0')

    attached = [s for s in stages if s.attached]
    for stage in attached:
        if stage.parallel or stage.isp_vac_s is None or stage.isp_vac_s <= 0:
            raise ValueError(
                f'stage {stage.stage_index} 数据不足（parallel 或缺失比冲），'
                '不能按串联级计算')

    mass = payload + sum(s.dry_mass_t + s.propellant_remaining_t
                         for s in attached)
    total = 0.0
    for stage in attached:
        total += available_delta_v_m_s(stage, mass)
        mass -= stage.dry_mass_t + stage.propellant_remaining_t
    return float(total)


# =============================================================================
# Evaluator
# =============================================================================


def _base_warnings():
    return [
        '结果基于校准 dv_curve，未模拟有限推力、大气轨迹、上面级寿命和'
        '跨级单次燃烧。',
        'whole_burn_per_stage=True（一次燃烧必须由单级完整完成）。',
    ]


def _parking_remaining_dv(replay):
    """Sequential remaining delta-v of the replayed parking state."""
    total = 0.0
    mass = replay.final_vehicle_mass_t
    for stage in replay.stages:
        if not stage.attached:
            continue
        if stage.parallel or stage.isp_vac_s is None or stage.isp_vac_s <= 0:
            raise RuntimeError(
                f'parking state has data-insufficient attached stage '
                f'{stage.stage_index}')
        total += available_delta_v_m_s(stage, mass)
        mass -= stage.dry_mass_t + stage.propellant_remaining_t
    return float(total)


def _verify_simulation(plan, simulation, final_states):
    """Compare the executed simulation against the plan prediction, strictly.

    Stage lists are compared by LENGTH first (zip alone would silently
    ignore extra or missing stages), then element-wise in order (stage_index
    order is never re-sorted).  Burns are compared one-to-one in order:
    count, burn_id, order, and the executed stage_index (None for
    calibration) against the planned preferred_stage_index.  Returns a list
    of mismatch descriptions (empty == fully consistent).
    """
    mismatches = []
    if not simulation.success:
        mismatches.append(
            f'simulation failed: {simulation.failure_reason_zh}')
    planned_mass = plan.planned_final_vehicle_mass_t
    sim_mass = simulation.metadata['final_vehicle_mass_t']
    if abs(sim_mass - planned_mass) > MASS_TOLERANCE_T:
        mismatches.append(
            f'final vehicle mass mismatch: simulation {sim_mass} vs '
            f'plan {planned_mass}')

    planned_stages = plan.planned_final_stages
    if len(planned_stages) != len(final_states):
        mismatches.append(
            f'stage list length mismatch: plan {len(planned_stages)} vs '
            f'simulation {len(final_states)}')
    for i in range(min(len(planned_stages), len(final_states))):
        planned = planned_stages[i]
        executed = final_states[i]
        if planned.stage_index != executed.stage_index:
            mismatches.append(
                f'stage {i} stage_index mismatch: plan {planned.stage_index} '
                f'vs simulation {executed.stage_index}')
        elif planned.attached != executed.attached:
            mismatches.append(
                f'stage {i} (index {planned.stage_index}) attached mismatch')
        elif planned.ignition_count != executed.ignition_count:
            mismatches.append(
                f'stage {i} (index {planned.stage_index}) ignition_count '
                'mismatch')
        elif abs(planned.propellant_remaining_t
                 - executed.propellant_remaining_t) > MASS_TOLERANCE_T:
            mismatches.append(
                f'stage {i} (index {planned.stage_index}) propellant mismatch')

    planned_burns = plan.requirements
    simulated_burns = simulation.burns
    if len(planned_burns) != len(simulated_burns):
        mismatches.append(
            f'burn count mismatch: plan {len(planned_burns)} vs simulation '
            f'{len(simulated_burns)}')
    by_id = {r.burn_id: r for r in planned_burns}
    for i in range(min(len(planned_burns), len(simulated_burns))):
        req = planned_burns[i]
        burn = simulated_burns[i]
        if burn.burn_id != req.burn_id:
            mismatches.append(
                f'burn {i} id mismatch: plan {req.burn_id} vs simulation '
                f'{burn.burn_id}')
            continue
        if burn.order != req.order:
            mismatches.append(
                f'burn {burn.burn_id} order mismatch: plan {req.order} vs '
                f'simulation {burn.order}')
        expected_stage = by_id[burn.burn_id].preferred_stage_index
        if burn.stage_index != expected_stage:
            mismatches.append(
                f'burn {burn.burn_id} executed on stage {burn.stage_index}, '
                f'planned {expected_stage}')
    return mismatches


def evaluate_payload_for_mission(
        rocket_ref: str,
        stages,
        requirements,
        dv_curve,
        payload_t: float,
        data_quality: DataQuality = DataQuality.EXACT,
        max_search_nodes: int = 10000,
        ascent_adjustment_dv_m_s: float = 0.0,
) -> PayloadFeasibilityResult:
    """Evaluate ONE payload for ONE mission; see the module docstring.

    Invalid calling arguments raise ValueError.  Calibration out-of-range,
    non-monotonic curve, parking-state inconsistency and plan-verification
    failures return DATA_INSUFFICIENT.  Sub-stage failures propagate their
    failure_code.  Never modifies any input.
    """
    # --- input validation ---------------------------------------------------
    if not isinstance(rocket_ref, str) or not rocket_ref.strip():
        raise ValueError('rocket_ref must be a non-empty string')
    if (isinstance(stages, (str, bytes))
            or not isinstance(stages, (list, tuple))
            or not stages):
        raise ValueError('stages must be a non-empty list or tuple')
    stages = list(stages)
    for i, stage in enumerate(stages):
        if not isinstance(stage, StageState):
            raise ValueError(f'stages[{i}] must be a StageState')
    reqs = _validate_requirements(requirements)
    if not isinstance(data_quality, DataQuality):
        raise ValueError('data_quality must be a DataQuality')
    if isinstance(max_search_nodes, bool) or not isinstance(max_search_nodes, int) \
            or max_search_nodes < 1:
        raise ValueError('max_search_nodes must be a positive integer')
    payload = _require_number(payload_t, 'payload_t')
    ascent_adjustment = _require_number(ascent_adjustment_dv_m_s, 'ascent_adjustment_dv_m_s')
    if payload < 0:
        raise ValueError('payload_t must be >= 0')

    real_burn_count = sum(1 for r in reqs if r.source != 'rocket_calibration')
    warnings = _base_warnings()

    # --- data quality gate --------------------------------------------------
    if data_quality is DataQuality.INSUFFICIENT:
        return _result(
            rocket_ref, payload, False, FailureCode.DATA_INSUFFICIENT,
            '输入数据质量不足（INSUFFICIENT），未执行评估',
            DataQuality.INSUFFICIENT, None, calibrated=None, total=None,
            ascent=None, warnings=warnings,
            stage_count=len(stages), real_burn_count=real_burn_count,
        )

    effective_quality = (DataQuality.ESTIMATED
                         if data_quality is DataQuality.EXACT
                         else data_quality)

    # --- calibration lookup -------------------------------------------------
    points = _validate_dv_curve(dv_curve)
    if not _curve_is_monotonic(points):
        return _result(
            rocket_ref, payload, False, FailureCode.DATA_INSUFFICIENT,
            f'校准曲线不可信：dv 随载荷非单调（载荷 {payload} t）',
            DataQuality.INSUFFICIENT, 'calibration_lookup',
            warnings=warnings, stage_count=len(stages),
            real_burn_count=real_burn_count,
            curve_min=points[0][0], curve_max=points[-1][0])
    try:
        calibrated = interpolate_calibrated_remaining_dv(payload, dv_curve)
    except ValueError as exc:
        if str(exc).startswith('payload_t'):
            return _result(
                rocket_ref, payload, False, FailureCode.DATA_INSUFFICIENT,
                f'载荷 {payload} t 超出校准范围 '
                f'[{points[0][0]}, {points[-1][0]}] t，无法评估',
                DataQuality.INSUFFICIENT, 'calibration_lookup',
                warnings=warnings, stage_count=len(stages),
                real_burn_count=real_burn_count,
                curve_min=points[0][0], curve_max=points[-1][0])
        raise

    exact_hit = any(payload == m for m, _ in points)
    calibrated = float(calibrated)
    # Apply changes in launch demand before parking replay, so they consume
    # ascent propellant rather than becoming a fictitious upper-stage burn.
    calibrated -= ascent_adjustment
    if calibrated < 0:
        return _result(
            rocket_ref, payload, False, FailureCode.PROPELLANT_INSUFFICIENT,
            '发射条件修正超过可用剩余 Δv', effective_quality, 'parking_replay',
            calibrated=calibrated, warnings=warnings,
            stage_count=len(stages), real_burn_count=real_burn_count,
            curve_min=points[0][0], curve_max=points[-1][0])

    # --- total vacuum delta-v and ascent requirement ------------------------
    try:
        total_vacuum = compute_serial_stack_vacuum_dv(stages, payload)
    except ValueError as exc:
        if '数据不足' in str(exc):
            return _result(
                rocket_ref, payload, False, FailureCode.DATA_INSUFFICIENT,
                str(exc), DataQuality.INSUFFICIENT, 'parking_replay',
                calibrated=calibrated, total=None, ascent=None,
                warnings=warnings, stage_count=len(stages),
                real_burn_count=real_burn_count,
                curve_min=points[0][0], curve_max=points[-1][0],
                curve_interpolated=not exact_hit)
        raise

    if calibrated - total_vacuum > _CALIBRATION_TOTAL_TOLERANCE_M_S:
        return _result(
            rocket_ref, payload, False, FailureCode.DATA_INSUFFICIENT,
            f'校准剩余 Δv（{calibrated:.1f} m/s）大于总真空 Δv'
            f'（{total_vacuum:.1f} m/s），校准曲线与级性能不一致',
            DataQuality.INSUFFICIENT, 'calibration_lookup',
            calibrated=calibrated, total=total_vacuum, ascent=None,
            warnings=warnings, stage_count=len(stages),
            real_burn_count=real_burn_count,
            curve_min=points[0][0], curve_max=points[-1][0],
            curve_interpolated=not exact_hit)
    ascent = float(total_vacuum - calibrated)
    if ascent <= 0:
        return _result(
            rocket_ref, payload, False, FailureCode.DATA_INSUFFICIENT,
            f'上升段需求必须为正（得到 {ascent:.6f} m/s），校准曲线与级性能'
            '不一致', DataQuality.INSUFFICIENT, 'calibration_lookup',
            calibrated=calibrated, total=total_vacuum, ascent=ascent,
            warnings=warnings, stage_count=len(stages),
            real_burn_count=real_burn_count,
            curve_min=points[0][0], curve_max=points[-1][0],
            curve_interpolated=not exact_hit)

    # --- parking-orbit ascent replay ----------------------------------------
    replay = replay_serial_ascent_to_parking(
        rocket_ref, stages, payload, ascent, data_quality=effective_quality)
    if not replay.success:
        return _result(
            rocket_ref, payload, False, replay.failure_code,
            replay.failure_reason_zh, replay.data_quality, 'parking_replay',
            calibrated=calibrated, total=total_vacuum, ascent=ascent,
            replay=replay, warnings=warnings + list(replay.warnings),
            stage_count=len(stages), real_burn_count=real_burn_count,
            curve_min=points[0][0], curve_max=points[-1][0],
            curve_interpolated=not exact_hit)

    # --- parking-state remaining-dv self check ------------------------------
    parking_check = _parking_remaining_dv(replay)
    parking_error = parking_check - calibrated
    if abs(parking_error) > PARKING_DV_CHECK_TOLERANCE_M_S:
        return _result(
            rocket_ref, payload, False, FailureCode.DATA_INSUFFICIENT,
            f'停泊轨道状态与校准剩余 Δv 不一致（回放剩余 {parking_check:.4f} '
            f'm/s vs 校准 {calibrated:.4f} m/s）',
            DataQuality.INSUFFICIENT, 'parking_consistency',
            calibrated=calibrated, total=total_vacuum, ascent=ascent,
            replay=replay, warnings=warnings,
            stage_count=len(stages), real_burn_count=real_burn_count,
            curve_min=points[0][0], curve_max=points[-1][0],
            curve_interpolated=not exact_hit,
            parking_check=parking_check, parking_error=parking_error)

    # --- post-parking planning ----------------------------------------------
    plan = plan_post_parking_sequence(
        rocket_ref, replay.stages, reqs, replay.final_vehicle_mass_t, payload,
        data_quality=effective_quality, max_search_nodes=max_search_nodes)
    if not plan.success:
        return _result(
            rocket_ref, payload, False, plan.failure_code,
            plan.failure_reason_zh, plan.data_quality, 'stage_planning',
            calibrated=calibrated, total=total_vacuum, ascent=ascent,
            replay=replay, plan=plan, warnings=warnings + list(plan.warnings),
            stage_count=len(stages), real_burn_count=real_burn_count,
            curve_min=points[0][0], curve_max=points[-1][0],
            curve_interpolated=not exact_hit,
            parking_check=parking_check, parking_error=parking_error)

    # --- execute and verify the plan ----------------------------------------
    final_states, simulation = simulate_multistage_sequence(
        rocket_ref, replay.stages, plan.requirements,
        replay.final_vehicle_mass_t, payload,
        separations_before=plan.separations_before,
        data_quality=effective_quality)
    mismatches = _verify_simulation(plan, simulation, final_states)
    if mismatches:
        return _result(
            rocket_ref, payload, False, FailureCode.DATA_INSUFFICIENT,
            '内部计划验证不一致：' + '；'.join(mismatches),
            DataQuality.INSUFFICIENT, 'plan_verification',
            calibrated=calibrated, total=total_vacuum, ascent=ascent,
            replay=replay, plan=plan, simulation=simulation,
            warnings=warnings + list(plan.warnings) + mismatches,
            stage_count=len(stages), real_burn_count=real_burn_count,
            curve_min=points[0][0], curve_max=points[-1][0],
            curve_interpolated=not exact_hit,
            parking_check=parking_check, parking_error=parking_error,
            plan_verified=False)

    return _result(
        rocket_ref, payload, True, FailureCode.NONE, '', effective_quality,
        None, calibrated=calibrated, total=total_vacuum, ascent=ascent,
        replay=replay, plan=plan, simulation=simulation,
        warnings=warnings + list(plan.warnings),
        stage_count=len(stages), real_burn_count=real_burn_count,
        curve_min=points[0][0], curve_max=points[-1][0],
        curve_interpolated=not exact_hit,
        parking_check=parking_check, parking_error=parking_error,
        plan_verified=True)


def _result(rocket_ref, payload_t, feasible, failure_code, failure_reason_zh,
            data_quality, failure_phase, *, calibrated=None, total=None,
            ascent=None, replay=None, plan=None, simulation=None,
            warnings=None, stage_count=0, real_burn_count=0,
            curve_min=None, curve_max=None, curve_interpolated=False,
            parking_check=None, parking_error=None, plan_verified=False):
    metadata = {
        'calibration_payload_min_t': curve_min,
        'calibration_payload_max_t': curve_max,
        'calibration_interpolated': curve_interpolated,
        'parking_remaining_dv_check_m_s': parking_check,
        'parking_remaining_dv_error_m_s': parking_error,
        'plan_verified': plan_verified,
        'stage_count': stage_count,
        'real_burn_count': real_burn_count,
        'evaluation_model': 'calibrated_serial_multiburn',
        'whole_burn_per_stage': True,
        'failure_phase': failure_phase,
    }
    return PayloadFeasibilityResult(
        rocket_ref=rocket_ref,
        payload_t=payload_t,
        feasible=feasible,
        failure_code=failure_code,
        failure_reason_zh=failure_reason_zh,
        data_quality=data_quality,
        calibrated_remaining_dv_m_s=calibrated,
        total_vacuum_dv_m_s=total,
        ascent_required_dv_m_s=ascent,
        parking_replay=replay,
        stage_plan=plan,
        simulation=simulation,
        warnings=warnings or [],
        metadata=metadata,
    )
