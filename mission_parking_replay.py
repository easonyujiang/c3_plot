"""
Serial-vehicle parking-orbit ascent replay — pure Python, no I/O.
=================================================================

Given a stack of fully-fueled, unflown serial StageStates (bottom-to-top,
list order = burn order) and an externally computed "effective delta-v
required to reach the parking orbit", replay the ascent: stages burn until
exhausted, separate (dry mass only), and the next stage ignites, until the
remaining delta-v budget is met or the stack runs out of propellant.

This phase ONLY replays serial stages.  It does NOT:

    * compute atmospheric losses or fit the required delta-v from
      calibration points (ascent_required_dv_m_s must be provided by the
      caller's calibration model);
    * handle parallel (boosted) stages — they are reported as
      DATA_INSUFFICIENT;
    * simulate coasting restarts (multi_ignition does not gate the first
      ascent ignition).

The single-burn math is delegated to mission_solver.execute_burn — no
second rocket equation lives here.

Dependencies: standard library, mission_simulation.py and mission_solver.py
(execute_burn, MASS_TOLERANCE_T).  No Flask, SQLite, server.models, frontend,
YAML, .craft/VAB or NumPy.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from numbers import Real
from typing import List, Optional

from mission_simulation import (
    BurnRequirement,
    DataQuality,
    FailureCode,
    StageState,
    to_dict as _contract_to_dict,
)
from mission_solver import MASS_TOLERANCE_T, execute_burn

# Remaining delta-v at or below this value counts as "arrived".
DV_TOLERANCE_M_S = 1e-6

_REPLAY_SOURCE = 'ascent_replay'


# =============================================================================
# Result type
# =============================================================================


@dataclass
class ParkingOrbitReplayResult:
    """Outcome of replaying the serial ascent to the parking orbit.

    ``stages`` are all fresh objects (input stages are never modified or
    shared).  ``burn_segments`` and ``separation_events`` record the replay
    in burn order.  ``to_dict()`` is directly JSON-serializable.
    """
    rocket_ref: str
    success: bool
    failure_code: FailureCode
    failure_reason_zh: str
    data_quality: DataQuality
    stages: List[StageState]
    initial_vehicle_mass_t: float
    final_vehicle_mass_t: float
    required_dv_m_s: float
    achieved_dv_m_s: float
    remaining_dv_m_s: float
    active_stage_index: Optional[int]
    burn_segments: List[dict]
    separation_events: List[dict]
    warnings: List[str]
    metadata: dict

    def to_dict(self):
        return _contract_to_dict(self)


# =============================================================================
# Validation helpers
# =============================================================================


def _require_finite_number(value, name, *, minimum=0.0):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError(f'{name} must be a finite number, got {value!r}')
    value = float(value)
    if value < minimum:
        raise ValueError(f'{name} must be >= {minimum}, got {value}')
    return value


def _copy_stage(stage, **overrides):
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


def _verify_mass_conservation(payload_t, working, current_mass, label):
    attached = sum(
        s.dry_mass_t + s.propellant_remaining_t
        for s in working if s.attached)
    accounted = payload_t + attached
    if abs(accounted - current_mass) > MASS_TOLERANCE_T:
        raise RuntimeError(
            f'mass conservation violated at {label}: '
            f'accounted {accounted} t vs current {current_mass} t')
    return True


# =============================================================================
# Replay
# =============================================================================


def replay_serial_ascent_to_parking(
        rocket_ref: str,
        stages,
        payload_t: float,
        ascent_required_dv_m_s: float,
        data_quality: DataQuality = DataQuality.EXACT,
) -> ParkingOrbitReplayResult:
    """Replay the serial ascent from full stack to the parking orbit.

    ``stages``: bottom-to-top serial StageStates; list order IS the burn
    order (never re-sorted by stage_index); stage_index must be unique but
    may be non-contiguous.  Every stage must be attached, unflown
    (ignition_count=0) and full of propellant
    (propellant_remaining_t ~= wet - dry within MASS_TOLERANCE_T) — this
    function only replays from a full-stack liftoff state.

    ``ascent_required_dv_m_s``: externally computed effective vacuum
    delta-v to reach the parking orbit (includes ascent needs and calibrated
    losses).  This function never calls compute_orbit_mission / compute_rocket
    and never fits the loss model itself.

    Truly invalid calling arguments raise ValueError without advancing any
    state.  Expected data insufficiency (parallel stage or missing Isp) is a
    DATA_INSUFFICIENT result with zero burns and zero separations.
    """
    if not isinstance(rocket_ref, str) or not rocket_ref.strip():
        raise ValueError('rocket_ref must be a non-empty string')

    if (isinstance(stages, (str, bytes))
            or not isinstance(stages, (list, tuple))
            or not stages):
        raise ValueError('stages must be a non-empty list or tuple')
    stages = list(stages)
    seen = set()
    for i, stage in enumerate(stages):
        if not isinstance(stage, StageState):
            raise ValueError(f'stages[{i}] must be a StageState')
        if stage.stage_index in seen:
            raise ValueError(f'duplicate stage_index: {stage.stage_index}')
        seen.add(stage.stage_index)
        if not stage.attached:
            raise ValueError(
                f'stage {i} (stage_index {stage.stage_index}) must be attached '
                'for a liftoff replay')
        if stage.ignition_count != 0:
            raise ValueError(
                f'stage {i} (stage_index {stage.stage_index}) must be unflown '
                f'(ignition_count={stage.ignition_count})')
        full = stage.wet_mass_t - stage.dry_mass_t
        if abs(stage.propellant_remaining_t - full) > MASS_TOLERANCE_T:
            raise ValueError(
                f'stage {i} (stage_index {stage.stage_index}) must be full of '
                f'propellant ({stage.propellant_remaining_t} != {full})')

    payload_t = _require_finite_number(payload_t, 'payload_t', minimum=0.0)
    required_dv = _require_finite_number(
        ascent_required_dv_m_s, 'ascent_required_dv_m_s', minimum=0.0)
    if required_dv <= 0:
        raise ValueError('ascent_required_dv_m_s must be > 0')

    # --- expected data insufficiency (result, not an exception) -------------
    working = [_copy_stage(s) for s in stages]
    reasons = []
    for i, stage in enumerate(working):
        if stage.parallel:
            reasons.append(
                f'stage {i}（stage_index {stage.stage_index}）是并联级，'
                '串联回放不支持并联燃烧')
        elif stage.isp_vac_s is None or stage.isp_vac_s <= 0:
            reasons.append(
                f'stage {i}（stage_index {stage.stage_index}）缺少真空比冲'
                '数据（isp_vac_s 不可用）')
    if reasons:
        return ParkingOrbitReplayResult(
            rocket_ref=rocket_ref,
            success=False,
            failure_code=FailureCode.DATA_INSUFFICIENT,
            failure_reason_zh='；'.join(reasons),
            data_quality=DataQuality.INSUFFICIENT,
            stages=working,
            initial_vehicle_mass_t=payload_t + sum(
                s.dry_mass_t + s.propellant_remaining_t for s in working),
            final_vehicle_mass_t=payload_t + sum(
                s.dry_mass_t + s.propellant_remaining_t for s in working),
            required_dv_m_s=required_dv,
            achieved_dv_m_s=0.0,
            remaining_dv_m_s=required_dv,
            active_stage_index=None,
            burn_segments=[],
            separation_events=[],
            warnings=list(reasons),
            metadata={
                'stage_order': [s.stage_index for s in stages],
                'total_stage_count': len(stages),
                'burned_stage_count': 0,
                'separated_stage_count': 0,
                'mass_conservation_verified': True,
                'replay_model': 'serial_impulsive_calibrated_dv',
            },
        )

    # --- replay -------------------------------------------------------------
    initial_mass = payload_t + sum(
        s.dry_mass_t + s.propellant_remaining_t for s in working)
    current_mass = initial_mass
    remaining = required_dv
    achieved = 0.0
    burn_segments = []
    separation_events = []
    active_index = None
    success = False
    failure_code = FailureCode.NONE
    failure_reason = ''
    _verify_mass_conservation(payload_t, working, current_mass, 'liftoff')

    for i, stage in enumerate(working):
        if remaining <= DV_TOLERANCE_M_S:
            break

        target = remaining
        state, burn = execute_burn(
            stage,
            BurnRequirement(
                burn_id=f'ascent_stage_{stage.stage_index}',
                order=i + 1,
                target_dv_m_s=target,
                source=_REPLAY_SOURCE,
                name=f'上升段第 {i + 1} 级点火',
            ),
            current_mass,
            data_quality=data_quality,
        )
        if burn.failure_code not in (FailureCode.NONE,
                                     FailureCode.PROPELLANT_INSUFFICIENT):
            raise RuntimeError(
                f'unexpected burn failure {burn.failure_code.value} on '
                f'stage {stage.stage_index} during ascent replay')

        achieved += burn.actual_dv_m_s
        remaining = max(0.0, remaining - burn.actual_dv_m_s)
        working[i] = state
        current_mass = burn.mass_after_t
        _verify_mass_conservation(payload_t, working, current_mass,
                                  f'burn of stage {stage.stage_index}')

        exhausted = state.propellant_remaining_t <= MASS_TOLERANCE_T
        burn_segments.append({
            'stage_index': stage.stage_index,
            'target_remaining_dv_m_s': target,
            'actual_dv_m_s': burn.actual_dv_m_s,
            'propellant_consumed_t': burn.propellant_consumed_t,
            'propellant_remaining_t': state.propellant_remaining_t,
            'mass_before_t': burn.mass_before_t,
            'mass_after_burn_t': burn.mass_after_t,
            'ignition_count_after': state.ignition_count,
            'exhausted': exhausted,
            'continued_to_next_stage': False,
        })

        if burn.success:
            # This stage completed the remaining budget; do NOT separate it
            # even if it happens to be empty.
            active_index = stage.stage_index
            break

        # PROPELLANT_INSUFFICIENT here means the stage contributed all it
        # could.  If the leftover delta-v is within DV_TOLERANCE_M_S, the
        # vehicle has arrived at the parking orbit: the current stage stays
        # attached and active, and NO separation happens — even though the
        # underlying BurnResult carries PROPELLANT_INSUFFICIENT, the ascent
        # semantics interpret this as a successful arrival within tolerance.
        # achieved_dv_m_s is kept at the real value (never faked); its gap to
        # the target is within tolerance.
        if remaining <= DV_TOLERANCE_M_S:
            burn_segments[-1]['continued_to_next_stage'] = False
            active_index = stage.stage_index
            break

        if i + 1 < len(working):
            burn_segments[-1]['continued_to_next_stage'] = True
            discarded = state.dry_mass_t  # propellant already spent
            mass_before = current_mass
            current_mass -= discarded
            working[i] = _copy_stage(state, attached=False)
            _verify_mass_conservation(payload_t, working, current_mass,
                                      f'separation of stage {stage.stage_index}')
            separation_events.append({
                'stage_index': stage.stage_index,
                'reason': 'ascent_stage_exhausted',
                'discarded_mass_t': discarded,
                'mass_before_t': mass_before,
                'mass_after_t': current_mass,
            })
            continue

        # Last stage exhausted: overall propellant budget shortfall.  The
        # final stage stays attached; no fabricated later stages or burns.
        failure_code = FailureCode.PROPELLANT_INSUFFICIENT
        failure_reason = (
            f'总推进剂不足：目标 {required_dv:.1f} m/s，'
            f'实际完成 {achieved:.1f} m/s，缺少 {remaining:.1f} m/s')
        break

    success = failure_code is FailureCode.NONE
    if success and active_index is None:
        active_index = next(
            (s.stage_index for s in working if s.attached), None)

    remaining_dv = 0.0 if remaining <= DV_TOLERANCE_M_S else remaining
    final_mass = current_mass
    _verify_mass_conservation(payload_t, working, final_mass, 'final')

    return ParkingOrbitReplayResult(
        rocket_ref=rocket_ref,
        success=success,
        failure_code=failure_code,
        failure_reason_zh=failure_reason,
        data_quality=data_quality,
        stages=working,
        initial_vehicle_mass_t=initial_mass,
        final_vehicle_mass_t=final_mass,
        required_dv_m_s=required_dv,
        achieved_dv_m_s=achieved,
        remaining_dv_m_s=remaining_dv,
        active_stage_index=active_index,
        burn_segments=burn_segments,
        separation_events=separation_events,
        warnings=[],
        metadata={
            'stage_order': [s.stage_index for s in stages],
            'total_stage_count': len(stages),
            'burned_stage_count': len(burn_segments),
            'separated_stage_count': len(separation_events),
            'mass_conservation_verified': True,
            'replay_model': 'serial_impulsive_calibrated_dv',
        },
    )
