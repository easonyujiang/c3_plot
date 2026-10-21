"""
Single-stage burn execution primitive — pure Python, no I/O.
============================================================

Given an explicit executing stage, a positive-delta-v burn requirement and
the current whole-vehicle mass, compute propellant consumption and return a
new StageState and a BurnResult.

Scope:
    * Single stage only.  No automatic stage assignment, no staging events,
      no parallel (boosted) combustion, no calibration-curve coverage, no
      full mission aggregation, no API or frontend.

    In addition to the single-burn primitive, this module provides
    ``simulate_stage_sequence``: sequential execution of BurnRequirements on
    one explicitly given stage, aggregated into a MissionSimulationResult.
    It does not choose stages, does not stage, and never infers which stage
    performed a calibration-covered ascent.

    ``simulate_multistage_sequence`` extends this to MULTIPLE explicit
    StageStates with explicit separation events.  The caller must name the
    executing stage of every real burn and every separation event; the solver
    never selects stages, never stages on its own and never guesses the
    rocket configuration.

Constraints:
    * Depends only on the standard library and mission_simulation.py.
    * Must not import Flask, SQLite, server.models, compute_c3_mass or any
      frontend code.
    * Must not read or write files.
    * Must not modify input objects (stage, requirement, metadata).
    * No third-party dependencies.

Units: masses in tonnes, delta-v in m/s, specific impulse in seconds.
The mass ratio in the rocket equation is unit-free, so tonnes need no
conversion to kilograms.
"""

from __future__ import annotations

import copy
import math
from numbers import Real
from typing import List, Tuple

from c3_mass.mission_simulation import (
    BurnRequirement,
    BurnResult,
    DataQuality,
    FailureCode,
    IgnitionKind,
    MissionSimulationResult,
    StageState,
)

STANDARD_GRAVITY_M_S2 = 9.80665

# Tolerance used ONLY for comparisons, never for rounding results.
MASS_TOLERANCE_T = 1e-9

_CALIBRATION_SOURCE = 'rocket_calibration'


# =============================================================================
# Shared validation
# =============================================================================


def _require_stage(stage):
    if not isinstance(stage, StageState):
        raise ValueError('stage must be a StageState')
    return stage


def _require_vehicle_mass(vehicle_mass_t):
    if (isinstance(vehicle_mass_t, bool) or not isinstance(vehicle_mass_t, Real)
            or not math.isfinite(float(vehicle_mass_t))):
        raise ValueError('vehicle_mass_t must be a finite number')
    if float(vehicle_mass_t) <= 0:
        raise ValueError('vehicle_mass_t must be > 0')
    return float(vehicle_mass_t)


def _validate_math_inputs(stage, vehicle_mass_t):
    """Shared validation for available_delta_v_m_s / required_propellant_t."""
    _require_stage(stage)
    vehicle_mass_t = _require_vehicle_mass(vehicle_mass_t)
    if not stage.attached:
        raise ValueError('stage must be attached (attached=True)')
    if stage.parallel:
        raise ValueError('parallel stages are not supported by the '
                         'single-stage burn primitive')
    if stage.isp_vac_s is None or stage.isp_vac_s <= 0:
        raise ValueError('stage.isp_vac_s must be present and > 0')
    minimum = stage.dry_mass_t + stage.propellant_remaining_t
    if vehicle_mass_t + MASS_TOLERANCE_T < minimum:
        raise ValueError(
            f'vehicle_mass_t ({vehicle_mass_t} t) must be >= current stage mass '
            f'(dry + propellant = {minimum} t)')
    if vehicle_mass_t <= stage.propellant_remaining_t:
        raise ValueError('vehicle_mass_t must be greater than the stage '
                         'propellant mass (no dry mass left to burn against)')
    return vehicle_mass_t


def _require_target_dv(target_dv_m_s):
    if (isinstance(target_dv_m_s, bool) or not isinstance(target_dv_m_s, Real)
            or not math.isfinite(float(target_dv_m_s))):
        raise ValueError('target_dv_m_s must be a finite number')
    if float(target_dv_m_s) <= 0:
        raise ValueError('target_dv_m_s must be > 0')
    return float(target_dv_m_s)


# =============================================================================
# Maximum available delta-v
# =============================================================================


def available_delta_v_m_s(stage: StageState, vehicle_mass_t: float) -> float:
    """Maximum delta-v this stage can deliver by burning all its remaining
    propellant, using the vacuum rocket equation:

        available_dv = isp_vac_s * g0 * ln(vehicle_mass_t / (vehicle_mass_t - propellant_remaining_t))

    Numerically this is computed as
    ``isp_vac_s * g0 * (-log1p(-propellant_remaining_t / vehicle_mass_t))``:
    forming the near-unity mass ratio first (``vehicle / (vehicle - prop)``)
    would round to 1.0 for tiny propellant masses and wrongly yield 0 m/s.
    The propellant fraction must satisfy 0 <= fraction < 1; an internal state
    violating that raises ValueError (no complex/NaN/zero-division).

    ``vehicle_mass_t`` is the total mass of the whole current vehicle BEFORE
    the burn (all still-attached stages + the executing stage + payload).

    Raises ValueError on calling errors (bad vehicle mass, detached stage,
    parallel stage, missing isp data, vehicle lighter than the stage).
    Never modifies ``stage``.
    """
    vehicle_mass_t = _validate_math_inputs(stage, vehicle_mass_t)
    propellant_fraction = stage.propellant_remaining_t / vehicle_mass_t
    if not (0.0 <= propellant_fraction < 1.0):
        raise ValueError(
            f'propellant fraction must satisfy 0 <= fraction < 1, '
            f'got {propellant_fraction}')
    return (stage.isp_vac_s * STANDARD_GRAVITY_M_S2
            * (-math.log1p(-propellant_fraction)))


# =============================================================================
# Propellant required for a target delta-v
# =============================================================================


def required_propellant_t(stage: StageState, vehicle_mass_t: float,
                          target_dv_m_s: float) -> float:
    """Theoretical propellant mass required to achieve ``target_dv_m_s``:

        required = vehicle_mass_t * (1 - exp(-target_dv_m_s / (isp_vac_s * g0)))

    Numerically this is computed as
    ``vehicle_mass_t * (-expm1(-target_dv_m_s / (isp_vac_s * g0)))``:
    for tiny targets ``exp(-x)`` would round to 1.0 and wrongly yield 0 t.
    Any positive target therefore yields a strictly positive requirement; no
    artificial minimum and no rounding is applied.

    Uses the same input validation as ``available_delta_v_m_s``; additionally
    ``target_dv_m_s`` must be a finite positive number (0 is rejected in this
    phase).  Returns the theoretical requirement — it is NOT clamped to the
    stage's remaining propellant.  Never modifies any input.
    """
    vehicle_mass_t = _validate_math_inputs(stage, vehicle_mass_t)
    target_dv_m_s = _require_target_dv(target_dv_m_s)
    exhaust_velocity = stage.isp_vac_s * STANDARD_GRAVITY_M_S2
    return vehicle_mass_t * (-math.expm1(-target_dv_m_s / exhaust_velocity))


# =============================================================================
# Single-burn execution
# =============================================================================


def _copy_stage(stage, **overrides):
    """Fresh StageState equal to ``stage`` (deep-copied metadata) plus overrides."""
    data = {
        'stage_index': stage.stage_index,
        'name': stage.name,
        'wet_mass_t': stage.wet_mass_t,
        'dry_mass_t': stage.dry_mass_t,
        'propellant_remaining_t': stage.propellant_remaining_t,
        'isp_vac_s': stage.isp_vac_s,
        'multi_ignition': stage.multi_ignition,
        'ignition_count': stage.ignition_count,
        'attached': stage.attached,
        'parallel': stage.parallel,
        'metadata': copy.deepcopy(stage.metadata),
    }
    data.update(overrides)
    return StageState(**data)


def _ignition_kind_for(stage):
    return IgnitionKind.FIRST if stage.ignition_count == 0 else IgnitionKind.RESTART


def _failure_burn(stage, requirement, vehicle_mass_t, *, ignition_kind,
                  stage_index, failure_code, reason, data_quality):
    return BurnResult(
        burn_id=requirement.burn_id,
        order=requirement.order,
        target_dv_m_s=float(requirement.target_dv_m_s),
        actual_dv_m_s=0.0,
        stage_index=stage_index,
        mass_before_t=vehicle_mass_t,
        mass_after_t=vehicle_mass_t,
        propellant_consumed_t=0.0,
        propellant_remaining_t=stage.propellant_remaining_t,
        ignition_kind=ignition_kind,
        success=False,
        failure_code=failure_code,
        failure_reason_zh=reason,
        data_quality=data_quality,
    )


def execute_burn(stage: StageState, requirement: BurnRequirement,
                 vehicle_mass_t: float,
                 data_quality: DataQuality = DataQuality.EXACT
                 ) -> Tuple[StageState, BurnResult]:
    """Execute one burn on an explicit stage and return (new_state, result).

    Never modifies ``stage``, ``requirement`` or any metadata.  Task-infeasible
    conditions (restart unsupported, missing isp, detached, parallel) return a
    failed BurnResult with an unchanged stage; calling errors raise ValueError.
    """
    # --- basic inputs -------------------------------------------------------
    _require_stage(stage)
    if not isinstance(requirement, BurnRequirement):
        raise ValueError('requirement must be a BurnRequirement')
    target = _require_target_dv(requirement.target_dv_m_s)
    if requirement.source == _CALIBRATION_SOURCE:
        raise ValueError(
            "requirement.source='rocket_calibration' is covered by the "
            "calibration curve; the mission solver must emit an UNASSIGNED "
            "successful BurnResult for it instead of burning propellant here")
    vehicle_mass_t = _require_vehicle_mass(vehicle_mass_t)

    # --- task-infeasible stage conditions (results, not exceptions) ---------
    if not stage.attached:
        burn = _failure_burn(
            stage, requirement, vehicle_mass_t,
            ignition_kind=IgnitionKind.UNASSIGNED,
            stage_index=None,
            failure_code=FailureCode.NO_STAGE_ASSIGNED,
            reason='该级已分离，无法执行点火',
            data_quality=data_quality,
        )
        return _copy_stage(stage), burn

    if stage.parallel:
        burn = _failure_burn(
            stage, requirement, vehicle_mass_t,
            ignition_kind=IgnitionKind.UNASSIGNED,
            stage_index=None,
            failure_code=FailureCode.DATA_INSUFFICIENT,
            reason='并联燃烧需要组合求解，单级燃烧原语不支持',
            data_quality=DataQuality.INSUFFICIENT,
        )
        return _copy_stage(stage), burn

    if stage.isp_vac_s is None:
        burn = _failure_burn(
            stage, requirement, vehicle_mass_t,
            ignition_kind=_ignition_kind_for(stage),
            stage_index=stage.stage_index,
            failure_code=FailureCode.DATA_INSUFFICIENT,
            reason='该级缺少真空比冲数据（isp_vac_s 为空），无法计算燃烧',
            data_quality=DataQuality.INSUFFICIENT,
        )
        return _copy_stage(stage), burn

    ignition_kind = _ignition_kind_for(stage)

    if stage.metadata.get('ground_ignition_only') and not (stage.stage_index==0 and stage.ignition_count==0 and requirement.source=='ascent_replay'):
        burn=_failure_burn(stage,requirement,vehicle_mass_t,ignition_kind=ignition_kind,
                          stage_index=stage.stage_index,failure_code=FailureCode.RESTART_UNSUPPORTED,
                          reason='该发动机需要地面支援点火，不能在此处启动',data_quality=data_quality)
        return _copy_stage(stage),burn

    if not stage.ignition_available:
        burn = _failure_burn(
            stage, requirement, vehicle_mass_t,
            ignition_kind=IgnitionKind.RESTART,
            stage_index=stage.stage_index,
            failure_code=FailureCode.RESTART_UNSUPPORTED,
            reason=('该级不支持多次点火（multi_ignition=False）' if not stage.multi_ignition else
                    f'该级点火次数已用尽（已使用 {stage.ignition_count} 次，限制 {stage.metadata.get("max_ignitions")} 次）'),
            data_quality=data_quality,
        )
        return _copy_stage(stage), burn

    # --- remaining validation ----------------------------------------------
    minimum = stage.dry_mass_t + stage.propellant_remaining_t
    if vehicle_mass_t + MASS_TOLERANCE_T < minimum:
        raise ValueError(
            f'vehicle_mass_t ({vehicle_mass_t} t) must be >= current stage mass '
            f'(dry + propellant = {minimum} t)')
    if vehicle_mass_t <= stage.propellant_remaining_t:
        raise ValueError('vehicle_mass_t must be greater than the stage '
                         'propellant mass (no dry mass left to burn against)')

    # --- burn decision ------------------------------------------------------
    remaining = stage.propellant_remaining_t
    required = required_propellant_t(stage, vehicle_mass_t, target)

    if required <= remaining + MASS_TOLERANCE_T:
        # --- propellant sufficient -----------------------------------------
        # Never consume more propellant than the stage actually has: when
        # `required` exceeds `remaining` by less than MASS_TOLERANCE_T this is
        # a numerical-tolerance success, not permission to create propellant.
        consumed = min(required, remaining)
        new_remaining = remaining - consumed
        if new_remaining < 0:
            new_remaining = 0.0  # tolerance zero-out (consumed already capped)
        mass_after = vehicle_mass_t - consumed
        new_stage = _copy_stage(
            stage,
            propellant_remaining_t=new_remaining,
            ignition_count=stage.ignition_count + 1,
        )
        burn = BurnResult(
            burn_id=requirement.burn_id,
            order=requirement.order,
            target_dv_m_s=target,
            actual_dv_m_s=target,
            stage_index=stage.stage_index,
            mass_before_t=vehicle_mass_t,
            mass_after_t=mass_after,
            propellant_consumed_t=consumed,
            propellant_remaining_t=new_remaining,
            ignition_kind=ignition_kind,
            success=True,
            failure_code=FailureCode.NONE,
            failure_reason_zh='',
            data_quality=data_quality,
        )
        return new_stage, burn

    # --- propellant insufficient: burn until exhausted ----------------------
    actual = available_delta_v_m_s(stage, vehicle_mass_t)
    consumed = remaining
    new_stage = _copy_stage(
        stage,
        propellant_remaining_t=0.0,
        ignition_count=stage.ignition_count + 1,
    )
    missing_dv = max(0.0, target - actual)
    burn = BurnResult(
        burn_id=requirement.burn_id,
        order=requirement.order,
        target_dv_m_s=target,
        actual_dv_m_s=actual,
        stage_index=stage.stage_index,
        mass_before_t=vehicle_mass_t,
        mass_after_t=vehicle_mass_t - consumed,
        propellant_consumed_t=consumed,
        propellant_remaining_t=0.0,
        ignition_kind=ignition_kind,
        success=False,
        failure_code=FailureCode.PROPELLANT_INSUFFICIENT,
        failure_reason_zh=(
            f'推进剂不足：需要 {required:.4f} t，仅剩 {remaining:.4f} t，'
            f'实际可提供 {actual:.1f} m/s，缺少 {missing_dv:.1f} m/s'),
        data_quality=data_quality,
    )
    return new_stage, burn


# =============================================================================
# Sequential task solver for one explicit executing stage
# =============================================================================

_QUALITY_RANK = {
    DataQuality.EXACT: 0,
    DataQuality.ESTIMATED: 1,
    DataQuality.INSUFFICIENT: 2,
}


def _worst_quality(qualities):
    """Worst (least trustworthy) quality of the given values. Explicit rank
    mapping, not string comparison."""
    worst = DataQuality.EXACT
    for quality in qualities:
        if _QUALITY_RANK[quality] > _QUALITY_RANK[worst]:
            worst = quality
    return worst


def _burn_context(requirement):
    """BurnResult.metadata context for a requirement (deep-copied metadata)."""
    return {
        'source': requirement.source,
        'name': requirement.name,
        'requirement_metadata': copy.deepcopy(requirement.metadata),
    }


def _require_payload(payload_t):
    if (isinstance(payload_t, bool) or not isinstance(payload_t, Real)
            or not math.isfinite(float(payload_t))):
        raise ValueError('payload_t must be a finite number')
    if float(payload_t) < 0:
        raise ValueError('payload_t must be >= 0')
    return float(payload_t)


def _validate_sequence_initial_mass(stage, vehicle_mass_t, payload_t):
    """Enforce total-mass conservation at sequence entry.

    With ``stage.attached=True`` the whole vehicle must weigh at least the
    executing stage's current mass plus the payload:

        vehicle_mass_t >= stage.dry_mass_t + stage.propellant_remaining_t + payload_t

    compared within MASS_TOLERANCE_T.  Extra mass is allowed (other still-
    attached structures or stages).  With ``stage.attached=False`` the stage
    contributes nothing to the lower bound, so calibration-only sequences on
    a detached stage may still pass through as pure records (real burns on it
    are handled later by execute_burn's NO_STAGE_ASSIGNED result).

    Raises ValueError showing vehicle_mass_t, the stage's current mass,
    payload_t and the required minimum.  vehicle_mass_t is never silently
    corrected.
    """
    if stage.attached:
        stage_mass = stage.dry_mass_t + stage.propellant_remaining_t
        required_minimum = stage_mass + payload_t
        if vehicle_mass_t + MASS_TOLERANCE_T < required_minimum:
            raise ValueError(
                f'vehicle_mass_t ({vehicle_mass_t} t) must be at least the '
                f'current stage mass ({stage_mass} t) plus payload '
                f'({payload_t} t) = {required_minimum} t')
    return vehicle_mass_t


def simulate_stage_sequence(rocket_ref: str, stage: StageState, requirements,
                            vehicle_mass_t: float, payload_t: float,
                            data_quality: DataQuality = DataQuality.EXACT
                            ) -> Tuple[StageState, MissionSimulationResult]:
    """Sequentially execute BurnRequirements on ONE explicitly given stage.

    ``stage`` is the only stage allowed to perform real burns.  No automatic
    stage assignment, no staging events, no parking-orbit inference, no
    parallel combustion.  Calibration-covered requirements
    (``source='rocket_calibration'``) are recorded as UNASSIGNED successes
    without consuming propellant or touching ``ignition_count``; the stage's
    ``ignition_count`` as passed in is the authoritative state for deciding
    FIRST vs. RESTART.

    Input order is execution order (never sorted).  After the first failed
    burn the sequence stops; remaining requirements produce no results.

    Never modifies any input object.  Invalid calling arguments raise
    ValueError.  Returns (final_stage_state, MissionSimulationResult) where
    ``final_stage_state`` is always a fresh object.
    """
    _require_stage(stage)
    if not isinstance(rocket_ref, str) or not rocket_ref.strip():
        raise ValueError('rocket_ref must be a non-empty string')
    if (isinstance(requirements, (str, bytes))
            or not isinstance(requirements, (list, tuple))
            or not requirements):
        raise ValueError('requirements must be a non-empty list or tuple')
    reqs = list(requirements)
    seen_ids = set()
    prev_order = None
    for i, req in enumerate(reqs):
        if not isinstance(req, BurnRequirement):
            raise ValueError(f'requirements[{i}] must be a BurnRequirement')
        if req.burn_id in seen_ids:
            raise ValueError(f'duplicate burn_id: {req.burn_id}')
        seen_ids.add(req.burn_id)
        if prev_order is not None and req.order <= prev_order:
            raise ValueError(
                f'requirements order must be strictly increasing '
                f'(burn_id={req.burn_id}, order={req.order})')
        prev_order = req.order
        if req.source != _CALIBRATION_SOURCE:
            _require_target_dv(req.target_dv_m_s)
    vehicle_mass_t = _require_vehicle_mass(vehicle_mass_t)
    payload_t = _require_payload(payload_t)
    if payload_t - vehicle_mass_t > MASS_TOLERANCE_T:
        raise ValueError(
            f'payload_t ({payload_t} t) must not exceed vehicle_mass_t '
            f'({vehicle_mass_t} t)')
    _validate_sequence_initial_mass(stage, vehicle_mass_t, payload_t)

    state = stage
    current_mass = vehicle_mass_t
    burns = []
    stopped_at = None

    for req in reqs:
        if req.source == _CALIBRATION_SOURCE:
            # Calibration-covered burn: recorded, never burned.  The stage's
            # ignition_count stays authoritative for FIRST/RESTART.
            burns.append(BurnResult(
                burn_id=req.burn_id,
                order=req.order,
                target_dv_m_s=float(req.target_dv_m_s),
                actual_dv_m_s=None,
                stage_index=None,
                mass_before_t=current_mass,
                mass_after_t=current_mass,
                propellant_consumed_t=0.0,
                propellant_remaining_t=None,
                ignition_kind=IgnitionKind.UNASSIGNED,
                success=True,
                failure_code=FailureCode.NONE,
                failure_reason_zh='',
                data_quality=data_quality,
                metadata=_burn_context(req),
            ))
            continue

        if (req.preferred_stage_index is not None
                and req.preferred_stage_index != stage.stage_index):
            # Explicit-stage mismatch: task-infeasible for this solver, which
            # must never look for another stage on its own.
            burns.append(BurnResult(
                burn_id=req.burn_id,
                order=req.order,
                target_dv_m_s=float(req.target_dv_m_s),
                actual_dv_m_s=0.0,
                stage_index=None,
                mass_before_t=current_mass,
                mass_after_t=current_mass,
                propellant_consumed_t=0.0,
                propellant_remaining_t=state.propellant_remaining_t,
                ignition_kind=IgnitionKind.UNASSIGNED,
                success=False,
                failure_code=FailureCode.NO_STAGE_ASSIGNED,
                failure_reason_zh=(
                    f'燃烧 {req.burn_id} 指定执行级 {req.preferred_stage_index} '
                    f'与本求解器的执行级 {stage.stage_index} 不一致'),
                data_quality=data_quality,
                metadata=_burn_context(req),
            ))
            stopped_at = req.burn_id
            break

        state, burn = execute_burn(state, req, current_mass,
                                   data_quality=data_quality)
        burn.metadata = {**burn.metadata, **_burn_context(req)}
        burns.append(burn)
        current_mass = burn.mass_after_t
        if not burn.success:
            stopped_at = req.burn_id
            break

    all_done = len(burns) == len(reqs)
    success = all_done and all(burn.success for burn in burns)
    if success:
        failure_code = FailureCode.NONE
        failure_reason_zh = ''
    else:
        first_failed = next(burn for burn in burns if not burn.success)
        failure_code = first_failed.failure_code
        failure_reason_zh = first_failed.failure_reason_zh

    result = MissionSimulationResult(
        rocket_ref=rocket_ref,
        payload_t=payload_t,
        success=success,
        failure_code=failure_code,
        failure_reason_zh=failure_reason_zh,
        data_quality=_worst_quality(burn.data_quality for burn in burns),
        burns=burns,
        warnings=[],
        metadata={
            'initial_vehicle_mass_t': vehicle_mass_t,
            'final_vehicle_mass_t': current_mass,
            'total_requirement_count': len(reqs),
            'completed_burn_count': len(burns),
            'stopped_at_burn_id': stopped_at,
            'executing_stage_index': stage.stage_index,
        },
    )
    return _copy_stage(state), result


# =============================================================================
# Explicit multistage solver: multiple StageStates + explicit separation events
# =============================================================================


def _validate_requirements_common(requirements):
    """Shared preflight for a requirement sequence: non-empty, BurnRequirement
    instances, unique burn_ids, strictly increasing order, positive targets
    for real burns.  Returns a list copy; never modifies inputs."""
    if (isinstance(requirements, (str, bytes))
            or not isinstance(requirements, (list, tuple))
            or not requirements):
        raise ValueError('requirements must be a non-empty list or tuple')
    reqs = list(requirements)
    seen_ids = set()
    prev_order = None
    for i, req in enumerate(reqs):
        if not isinstance(req, BurnRequirement):
            raise ValueError(f'requirements[{i}] must be a BurnRequirement')
        if req.burn_id in seen_ids:
            raise ValueError(f'duplicate burn_id: {req.burn_id}')
        seen_ids.add(req.burn_id)
        if prev_order is not None and req.order <= prev_order:
            raise ValueError(
                f'requirements order must be strictly increasing '
                f'(burn_id={req.burn_id}, order={req.order})')
        prev_order = req.order
        if req.source != _CALIBRATION_SOURCE:
            _require_target_dv(req.target_dv_m_s)
    return reqs


def simulate_multistage_sequence(rocket_ref: str, stages, requirements,
                                 vehicle_mass_t: float, payload_t: float,
                                 separations_before=None,
                                 data_quality: DataQuality = DataQuality.EXACT
                                 ) -> Tuple[List[StageState], MissionSimulationResult]:
    """Sequentially execute BurnRequirements over MULTIPLE explicit stages
    with explicit separation events.

    The caller must state, for every real burn, which stage executes it
    (``preferred_stage_index``, mandatory) and which stages separate before
    which burn (``separations_before``).  The solver never chooses stages,
    never stages on its own and never infers the rocket configuration.

    ``stages``: non-empty list/tuple of StageState; stage_index must be unique
    (may be non-contiguous); the returned list keeps input order.

    ``separations_before``: dict[burn_id, list[stage_index]]; the listed
    stages separate (attached=False, mass discarded) immediately before that
    burn is processed.  Event order follows requirements, never the dict's
    iteration order.  The whole plan is validated before any state advance.

    Calibration-covered requirements keep their semantics: no
    preferred_stage_index required, no propellant, no ignition_count change,
    UNASSIGNED success; separations may not be scheduled before them.

    A later requirement may name a stage separated by an earlier event; that
    is valid input and yields a NO_STAGE_ASSIGNED failure burn at execution.
    Never modifies any input object.
    """
    if not isinstance(rocket_ref, str) or not rocket_ref.strip():
        raise ValueError('rocket_ref must be a non-empty string')

    if (isinstance(stages, (str, bytes))
            or not isinstance(stages, (list, tuple))
            or not stages):
        raise ValueError('stages must be a non-empty list or tuple')
    stages = list(stages)
    by_position = {}
    for i, stage in enumerate(stages):
        _require_stage(stage)
        if stage.stage_index in by_position:
            raise ValueError(f'duplicate stage_index: {stage.stage_index}')
        by_position[stage.stage_index] = i

    reqs = _validate_requirements_common(requirements)
    id_to_req = {req.burn_id: req for req in reqs}

    # --- mandatory explicit executing stage for every real burn -------------
    for req in reqs:
        if req.source == _CALIBRATION_SOURCE:
            continue
        if req.preferred_stage_index is None:
            raise ValueError(
                f'burn {req.burn_id} must specify preferred_stage_index '
                '(explicit executing stage is mandatory in multistage mode)')
        if req.preferred_stage_index not in by_position:
            raise ValueError(
                f'burn {req.burn_id} references unknown stage_index '
                f'{req.preferred_stage_index}')

    vehicle_mass_t = _require_vehicle_mass(vehicle_mass_t)
    payload_t = _require_payload(payload_t)
    if payload_t - vehicle_mass_t > MASS_TOLERANCE_T:
        raise ValueError(
            f'payload_t ({payload_t} t) must not exceed vehicle_mass_t '
            f'({vehicle_mass_t} t)')

    # --- initial mass conservation -----------------------------------------
    initial_attached_mass = sum(
        s.dry_mass_t + s.propellant_remaining_t
        for s in stages if s.attached)
    untracked_mass_t = vehicle_mass_t - payload_t - initial_attached_mass
    if untracked_mass_t + MASS_TOLERANCE_T < 0:
        raise ValueError(
            f'vehicle_mass_t ({vehicle_mass_t} t) must be at least payload '
            f'({payload_t} t) plus all attached stage masses '
            f'({initial_attached_mass} t) = '
            f'{payload_t + initial_attached_mass} t')

    # --- separation plan validation (complete, before any state advance) ----
    if separations_before is None:
        separation_plan = {}
    else:
        if not isinstance(separations_before, dict):
            raise ValueError('separations_before must be a dict or None')
        separation_plan = {}
        for burn_id, indices in separations_before.items():
            if not isinstance(burn_id, str) or not burn_id:
                raise ValueError('separations_before keys must be non-empty strings')
            if burn_id not in id_to_req:
                raise ValueError(f'separation before unknown burn_id: {burn_id}')
            if id_to_req[burn_id].source == _CALIBRATION_SOURCE:
                raise ValueError(
                    f'separations must not be scheduled before calibration burn {burn_id}')
            if (isinstance(indices, (str, bytes))
                    or not isinstance(indices, (list, tuple))
                    or not indices):
                raise ValueError(
                    f'separations_before[{burn_id}] must be a non-empty '
                    'list of stage indices')
            seen_in_event = set()
            parsed = []
            for idx in indices:
                if isinstance(idx, bool) or not isinstance(idx, int):
                    raise ValueError(
                        f'separations_before[{burn_id}] entries must be '
                        f'integers, got {idx!r}')
                if idx in seen_in_event:
                    raise ValueError(
                        f'duplicate stage index {idx} in separations before '
                        f'{burn_id}')
                seen_in_event.add(idx)
                parsed.append(idx)
            separation_plan[burn_id] = parsed

    scheduled_separation = {}
    for burn_id, indices in separation_plan.items():
        executing_index = id_to_req[burn_id].preferred_stage_index
        for idx in indices:
            if idx not in by_position:
                raise ValueError(
                    f'separation of unknown stage_index {idx} before {burn_id}')
            stage = stages[by_position[idx]]
            if not stage.attached:
                raise ValueError(
                    f'stage {idx} is already detached; cannot schedule '
                    'a separation for it')
            if idx in scheduled_separation:
                raise ValueError(
                    f'stage {idx} scheduled for separation more than once '
                    f'(before {scheduled_separation[idx]} and {burn_id})')
            scheduled_separation[idx] = burn_id
            if executing_index is not None and idx == executing_index:
                raise ValueError(
                    f'stage {idx} is the executing stage of burn {burn_id}; '
                    'it cannot be separated before its own burn')

    # --- execute ------------------------------------------------------------
    working = list(stages)
    current_mass = vehicle_mass_t
    burns = []
    separation_events = []
    stopped_at = None

    for req in reqs:
        burn_id = req.burn_id

        if burn_id in separation_plan:
            event_indices = separation_plan[burn_id]
            event_mass_before = current_mass
            event_discarded = 0.0
            for idx in event_indices:
                pos = by_position[idx]
                stage = working[pos]
                if not stage.attached:
                    continue  # defensive; the validated plan makes this unreachable
                discarded = stage.dry_mass_t + stage.propellant_remaining_t
                event_discarded += discarded
                current_mass -= discarded
                working[pos] = _copy_stage(stage, attached=False)
            separation_events.append({
                'before_burn_id': burn_id,
                'stage_indices': list(event_indices),
                'discarded_mass_t': event_discarded,
                'mass_before_t': event_mass_before,
                'mass_after_t': current_mass,
            })

        if req.source == _CALIBRATION_SOURCE:
            burns.append(BurnResult(
                burn_id=burn_id,
                order=req.order,
                target_dv_m_s=float(req.target_dv_m_s),
                actual_dv_m_s=None,
                stage_index=None,
                mass_before_t=current_mass,
                mass_after_t=current_mass,
                propellant_consumed_t=0.0,
                propellant_remaining_t=None,
                ignition_kind=IgnitionKind.UNASSIGNED,
                success=True,
                failure_code=FailureCode.NONE,
                failure_reason_zh='',
                data_quality=data_quality,
                metadata=_burn_context(req),
            ))
            continue

        target_index = req.preferred_stage_index
        pos = by_position[target_index]
        executing_stage = working[pos]
        if not executing_stage.attached:
            burns.append(BurnResult(
                burn_id=burn_id,
                order=req.order,
                target_dv_m_s=float(req.target_dv_m_s),
                actual_dv_m_s=0.0,
                stage_index=None,
                mass_before_t=current_mass,
                mass_after_t=current_mass,
                propellant_consumed_t=0.0,
                propellant_remaining_t=executing_stage.propellant_remaining_t,
                ignition_kind=IgnitionKind.UNASSIGNED,
                success=False,
                failure_code=FailureCode.NO_STAGE_ASSIGNED,
                failure_reason_zh=(
                    f'执行级 {target_index} 已分离，无法执行燃烧 {burn_id}'),
                data_quality=data_quality,
                metadata=_burn_context(req),
            ))
            stopped_at = burn_id
            break

        state, burn = execute_burn(executing_stage, req, current_mass,
                                   data_quality=data_quality)
        burn.metadata = {**burn.metadata, **_burn_context(req)}
        working[pos] = state
        burns.append(burn)
        current_mass = burn.mass_after_t
        if not burn.success:
            stopped_at = burn_id
            break

    # --- aggregation --------------------------------------------------------
    all_done = len(burns) == len(reqs)
    success = all_done and all(burn.success for burn in burns)
    if success:
        failure_code = FailureCode.NONE
        failure_reason_zh = ''
    else:
        first_failed = next(burn for burn in burns if not burn.success)
        failure_code = first_failed.failure_code
        failure_reason_zh = first_failed.failure_reason_zh

    final_states = [_copy_stage(s) for s in working]
    final_attached_mass = sum(
        s.dry_mass_t + s.propellant_remaining_t
        for s in final_states if s.attached)

    # Defensive mass-conservation check: every step subtracted exactly, so any
    # drift beyond tolerance would be a bug that must not be papered over.
    accounted = payload_t + untracked_mass_t + final_attached_mass
    if abs(accounted - current_mass) > MASS_TOLERANCE_T:
        raise ValueError(
            f'internal mass bookkeeping violated conservation '
            f'(accounted {accounted} vs current {current_mass})')

    result = MissionSimulationResult(
        rocket_ref=rocket_ref,
        payload_t=payload_t,
        success=success,
        failure_code=failure_code,
        failure_reason_zh=failure_reason_zh,
        data_quality=_worst_quality(burn.data_quality for burn in burns),
        burns=burns,
        warnings=[],
        metadata={
            'initial_vehicle_mass_t': vehicle_mass_t,
            'final_vehicle_mass_t': current_mass,
            'initial_attached_stage_mass_t': initial_attached_mass,
            'final_attached_stage_mass_t': final_attached_mass,
            'untracked_mass_t': untracked_mass_t,
            'stage_indices': [s.stage_index for s in stages],
            'total_requirement_count': len(reqs),
            'completed_burn_count': len(burns),
            'stopped_at_burn_id': stopped_at,
            'separation_events': separation_events,
        },
    )
    return final_states, result
