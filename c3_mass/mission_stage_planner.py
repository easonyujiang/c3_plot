"""
Post-parking explicit mission-stage planner — pure Python, no I/O.
==================================================================

Given the parking-orbit state (multiple StageStates, current vehicle mass,
payload) and the burn requirements produced by
``burn_sequence_to_requirements`` (real burns still unassigned), this module
builds a complete, auditable plan:

    * preferred_stage_index for every real burn,
    * separations_before for every burn,
    * predicted final stage states and vehicle mass.

The planner ONLY builds the plan; it never replaces the final verification
by ``simulate_multistage_sequence`` (the caller must execute the plan and
set plan_verified itself).

Planning strategy — ``lowest_reachable_capable_stage``:

    For every real burn (in order), the attached stages are examined
    bottom-up and the FIRST stage that can complete the whole burn is
    committed (with the separations it requires).  There is NO cross-burn
    backtracking: in this model (stages usable only bottom-up, all lower
    stages separated before using a higher one, one burn completed by one
    stage, no ignition-count ceilings beyond the multi_ignition boolean, no
    stage-life/coast constraints), the lowest reachable capable stage is
    dominant — using it never reduces future feasibility, because it keeps
    upper stages fuller, does not advance their ignition_count early, and
    can still be separated later.  Failure diagnosis is built ONLY from the
    snapshot state at the start of the failing burn, never from mission
    start or from discarded branch states.

Rules:
    * whole_burn_per_stage=True: one BurnRequirement is completed by exactly
      one stage — no cross-stage burn splitting, no parallel combustion.
    * restart needs multi_ignition=True when ignition_count > 0.
    * calibration requirements pass through unassigned and untouched.

Dependencies: standard library, mission_simulation.py and mission_solver.py
(execute_burn, available_delta_v_m_s, MASS_TOLERANCE_T).  No Flask, SQLite,
server.models, compute_orbit_mission, compute_rocket, YAML, frontend,
.craft/VAB or NumPy.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from numbers import Integral, Real
from typing import Dict, List, Optional

from c3_mass.mission_simulation import (
    BurnRequirement,
    DataQuality,
    FailureCode,
    StageState,
    to_dict as _contract_to_dict,
)
from c3_mass.mission_solver import (
    MASS_TOLERANCE_T,
    available_delta_v_m_s,
    execute_burn,
)

_PLANNING_POLICY = 'lowest_reachable_capable_stage'

# Tolerance used for the whole-burn aggregate comparison.
_DV_CHECK_TOL = 1e-6


# =============================================================================
# Result type
# =============================================================================


@dataclass
class PostParkingPlanResult:
    """A complete post-parking mission plan (or a stable failure).

    ``requirements`` are fresh objects with real burns assigned; calibration
    requirements stay unassigned.  ``separations_before`` maps burn_id to the
    stage indices (input-list order) separated before that burn.
    ``planned_final_stages`` predict the state after the plan; they are all
    fresh objects.  ``to_dict()`` is directly JSON-serializable.
    """
    rocket_ref: str
    success: bool
    failure_code: FailureCode
    failure_reason_zh: str
    data_quality: DataQuality
    requirements: List[BurnRequirement]
    separations_before: Dict[str, List[int]]
    planned_final_stages: List[StageState]
    initial_vehicle_mass_t: float
    planned_final_vehicle_mass_t: float
    payload_t: float
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


def _require_positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f'{name} must be a positive integer, got {value!r}')
    value = int(value)
    if value < 1:
        raise ValueError(f'{name} must be a positive integer, got {value}')
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


def _copy_requirement(req, preferred_stage_index):
    return BurnRequirement(
        burn_id=req.burn_id,
        order=req.order,
        target_dv_m_s=float(req.target_dv_m_s),
        source=req.source,
        name=req.name,
        preferred_stage_index=preferred_stage_index,
        metadata=copy.deepcopy(req.metadata),
    )


def _unassigned_requirements(reqs):
    return [_copy_requirement(r, None) for r in reqs]


def _verify_conservation(payload_t, untracked, working, current_mass, label):
    """current_vehicle_mass == payload + untracked + attached stage masses.
    Violation beyond MASS_TOLERANCE_T is an internal error: raise, never fix."""
    attached = sum(
        s.dry_mass_t + s.propellant_remaining_t
        for s in working if s.attached)
    accounted = payload_t + untracked + attached
    if abs(accounted - current_mass) > MASS_TOLERANCE_T:
        raise RuntimeError(
            f'mass conservation violated at {label}: '
            f'accounted {accounted} t vs current {current_mass} t')
    return True


def _classify_failure_code(candidate_codes):
    """Classify the failing burn's failure code from its candidate failure
    codes.

    Priority: DATA_INSUFFICIENT > PROPELLANT_INSUFFICIENT >
    RESTART_UNSUPPORTED.  Unknown or unexpected codes are treated as
    DATA_INSUFFICIENT, never defaulted to RESTART_UNSUPPORTED.  An empty
    candidate list means no reachable attached stage.
    """
    if not candidate_codes:
        return FailureCode.NO_STAGE_ASSIGNED
    if any(code is FailureCode.DATA_INSUFFICIENT for code in candidate_codes):
        return FailureCode.DATA_INSUFFICIENT
    known = (FailureCode.RESTART_UNSUPPORTED, FailureCode.PROPELLANT_INSUFFICIENT)
    if any(code is not None and code not in known for code in candidate_codes):
        return FailureCode.DATA_INSUFFICIENT
    if any(code is FailureCode.PROPELLANT_INSUFFICIENT
           for code in candidate_codes):
        return FailureCode.PROPELLANT_INSUFFICIENT
    return FailureCode.RESTART_UNSUPPORTED


def _sequential_reachable_dv(working, current_mass, target):
    """Aggregate delta-v of the stages that can actually contribute to THIS
    burn, computed on the failure snapshot with staging (each stage burns all
    its propellant, then its dry mass is discarded before the next stage).

    Restart-blocked stages (ignition_count>0, multi_ignition=False) cannot
    ignite and are only separated.  Parallel stages and stages without Isp
    are excluded (they are DATA_INSUFFICIENT, not contributors).
    Returns (total_dv, contributing_count).
    """
    total = 0.0
    count = 0
    mass_iter = current_mass
    for stage in working:
        if not stage.attached:
            continue
        if stage.parallel or stage.isp_vac_s is None or stage.isp_vac_s <= 0:
            continue
        if not stage.ignition_available:
            mass_iter -= stage.dry_mass_t + stage.propellant_remaining_t
            continue
        if mass_iter < 0:
            break
        total += available_delta_v_m_s(stage, mass_iter)
        count += 1
        mass_iter -= stage.dry_mass_t + stage.propellant_remaining_t
        if mass_iter < 0:
            break
    return total, count


def _plan_metadata(stages, real_burns, nodes, max_nodes, assigned, discarded,
                   diagnostics=None):
    meta = {
        'planning_policy': _PLANNING_POLICY,
        'search_nodes': nodes,
        'max_search_nodes': max_nodes,
        'stage_order': [s.stage_index for s in stages],
        'real_burn_count': len(real_burns),
        'assigned_stage_indices': [
            idx for idx in (assigned.get(b.burn_id) for b in real_burns)
            if idx is not None],
        'discarded_propellant_t': discarded,
        'whole_burn_per_stage': True,
        'plan_verified': False,
    }
    if diagnostics is None:
        meta.update({
            'failure_burn_id': None,
            'failure_burn_order': None,
            'failure_attached_stage_indices': [],
            'failure_snapshot_vehicle_mass_t': None,
            'candidate_failure_codes': [],
            'candidate_available_dv_m_s': [],
            'aggregate_reachable_dv_m_s': None,
            'whole_burn_limitation_triggered': False,
        })
    else:
        meta.update(diagnostics)
    return meta


# =============================================================================
# Planner
# =============================================================================


def plan_post_parking_sequence(
        rocket_ref: str,
        stages,
        requirements,
        vehicle_mass_t: float,
        payload_t: float,
        data_quality: DataQuality = DataQuality.EXACT,
        max_search_nodes: int = 10000,
) -> PostParkingPlanResult:
    """Build a complete post-parking plan; see the module docstring.

    All calling errors raise ValueError before any state advance.  Expected
    data insufficiency (parallel stage, missing Isp, candidate-evaluation
    limit) is a DATA_INSUFFICIENT result with zero assignments and zero
    separations.  ``max_search_nodes`` is the maximum number of candidate
    stage evaluations (NOT exponential backtracking nodes).  Never modifies
    any input object.
    """
    # --- basic calling validation -------------------------------------------
    if not isinstance(rocket_ref, str) or not rocket_ref.strip():
        raise ValueError('rocket_ref must be a non-empty string')

    if (isinstance(stages, (str, bytes))
            or not isinstance(stages, (list, tuple))
            or not stages):
        raise ValueError('stages must be a non-empty list or tuple')
    stages = list(stages)
    seen_indices = set()
    for i, stage in enumerate(stages):
        if not isinstance(stage, StageState):
            raise ValueError(f'stages[{i}] must be a StageState')
        if stage.stage_index in seen_indices:
            raise ValueError(f'duplicate stage_index: {stage.stage_index}')
        seen_indices.add(stage.stage_index)
    seen_attached = False
    for stage in stages:
        if stage.attached:
            seen_attached = True
        elif seen_attached:
            raise ValueError(
                'attached/detached topology must be a detached prefix '
                'followed by an attached suffix')

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
        if req.source != 'rocket_calibration':
            target = _require_finite_number(
                req.target_dv_m_s, f'requirements[{i}].target_dv_m_s',
                minimum=0.0)
            if target <= 0:
                raise ValueError(
                    f'requirements[{i}] target_dv_m_s must be > 0 for '
                    'non-calibration burns')
            if req.preferred_stage_index is not None:
                raise ValueError(
                    f'requirements[{i}] already assigns '
                    f'preferred_stage_index={req.preferred_stage_index}; '
                    'the planner must not silently override existing '
                    'assignments')

    vehicle_mass_t = _require_finite_number(
        vehicle_mass_t, 'vehicle_mass_t', minimum=0.0)
    if vehicle_mass_t <= 0:
        raise ValueError('vehicle_mass_t must be > 0')
    payload_t = _require_finite_number(payload_t, 'payload_t', minimum=0.0)
    max_nodes = _require_positive_int(max_search_nodes, 'max_search_nodes')

    initial_attached_mass = sum(
        s.dry_mass_t + s.propellant_remaining_t
        for s in stages if s.attached)
    if vehicle_mass_t + MASS_TOLERANCE_T < payload_t + initial_attached_mass:
        raise ValueError(
            f'vehicle_mass_t ({vehicle_mass_t} t) must be at least payload '
            f'({payload_t} t) plus all attached stage masses '
            f'({initial_attached_mass} t) = '
            f'{payload_t + initial_attached_mass} t')
    untracked = vehicle_mass_t - payload_t - initial_attached_mass

    # --- expected data insufficiency (result, not an exception) -------------
    real_burns = [r for r in reqs if r.source != 'rocket_calibration']
    reasons = []
    for stage in stages:
        if not stage.attached:
            continue
        if stage.parallel:
            reasons.append(
                f'attached stage（stage_index {stage.stage_index}）是并联级，'
                '规划器不支持并联燃烧')
        elif stage.isp_vac_s is None or stage.isp_vac_s <= 0:
            reasons.append(
                f'attached stage（stage_index {stage.stage_index}）缺少真空'
                '比冲数据（isp_vac_s 不可用）')
    if reasons:
        return _data_insufficient_result(
            rocket_ref, stages, reqs, vehicle_mass_t, payload_t,
            data_quality, max_nodes, 0, reasons)

    # --- sequential planning: lowest reachable capable stage ----------------
    working = [_copy_stage(s) for s in stages]
    current_mass = vehicle_mass_t
    _verify_conservation(payload_t, untracked, working, current_mass,
                         'planning start')

    assignments = []
    separations = []
    discarded = 0.0
    evaluations = 0
    failure = None

    for burn in real_burns:
        target = float(burn.target_dv_m_s)
        # immutable failure snapshot at the start of THIS burn
        snapshot = {
            'burn': burn,
            'target': target,
            'working': [_copy_stage(s) for s in working],
            'mass': current_mass,
        }
        candidates = []  # (position, stage_index, failure_code, avail_dv,
        #                  separated_before_indices, test_vehicle_mass)
        chosen = None
        for pos, stage in enumerate(working):
            if not stage.attached:
                continue
            evaluations += 1
            if evaluations > max_nodes:
                return _data_insufficient_result(
                    rocket_ref, stages, reqs, vehicle_mass_t, payload_t,
                    data_quality, max_nodes, evaluations,
                    [f'候选级评估次数超过上限（max_search_nodes={max_nodes}）'])

            # candidate test on a throwaway copy: separate everything below
            branch = [_copy_stage(s) for s in working]
            branch_mass = current_mass
            sep_before = []
            for below in range(pos):
                lower = branch[below]
                if lower.attached:
                    branch_mass -= (lower.dry_mass_t
                                    + lower.propellant_remaining_t)
                    if branch_mass < 0:
                        raise RuntimeError(
                            f'negative mass in candidate branch for burn '
                            f'{burn.burn_id} on stage {stage.stage_index}')
                    branch[below] = _copy_stage(lower, attached=False)
                    sep_before.append(lower.stage_index)

            # available delta-v after the necessary separation (diagnostics)
            avail = None
            if stage.isp_vac_s is not None and stage.isp_vac_s > 0 \
                    and not stage.parallel:
                try:
                    avail = available_delta_v_m_s(branch[pos], branch_mass)
                except ValueError:
                    avail = None

            if not stage.ignition_available:
                candidates.append((pos, stage.stage_index,
                                   FailureCode.RESTART_UNSUPPORTED, avail,
                                   sep_before, branch_mass))
                continue

            state, burn_result = execute_burn(
                branch[pos],
                BurnRequirement(
                    burn_id=burn.burn_id,
                    order=burn.order,
                    target_dv_m_s=target,
                    source=burn.source,
                    name=burn.name,
                ),
                branch_mass,
                data_quality=data_quality,
            )
            if not burn_result.success:
                candidates.append((pos, stage.stage_index,
                                   burn_result.failure_code, avail,
                                   sep_before, branch_mass))
                continue

            chosen = (pos, stage.stage_index, sep_before, state,
                      burn_result.mass_after_t)
            break

        if chosen is None:
            failure = _build_failure(snapshot, candidates, untracked)
            break

        # commit: separate below-stages, then the burn state
        pos, stage_index, sep_before, state, mass_after = chosen
        if sep_before:
            for below in range(pos):
                lower = working[below]
                if lower.attached:
                    discarded += lower.propellant_remaining_t
                    working[below] = _copy_stage(lower, attached=False)
            separations.append((burn.burn_id, sep_before))
        working[pos] = state
        current_mass = mass_after
        assignments.append((burn.burn_id, stage_index))
        _verify_conservation(payload_t, untracked, working, current_mass,
                             f'after burn {burn.burn_id}')

    if failure is not None:
        return _failure_result(
            rocket_ref, stages, reqs, vehicle_mass_t, payload_t,
            data_quality, max_nodes, evaluations, failure)

    assigned = dict(assignments)
    out_reqs = [_copy_requirement(
        r, assigned.get(r.burn_id) if r.source != 'rocket_calibration'
        else None) for r in reqs]

    warnings = []
    if discarded > MASS_TOLERANCE_T:
        warnings.append(f'分离丢弃了 {discarded:.4f} t 未使用推进剂')

    _verify_conservation(payload_t, untracked, working, current_mass, 'final')
    return PostParkingPlanResult(
        rocket_ref=rocket_ref,
        success=True,
        failure_code=FailureCode.NONE,
        failure_reason_zh='',
        data_quality=data_quality,
        requirements=out_reqs,
        separations_before={burn_id: list(indices)
                            for burn_id, indices in separations},
        planned_final_stages=[_copy_stage(s) for s in working],
        initial_vehicle_mass_t=vehicle_mass_t,
        planned_final_vehicle_mass_t=current_mass,
        payload_t=payload_t,
        warnings=warnings,
        metadata=_plan_metadata(stages, real_burns, evaluations, max_nodes,
                                assigned, discarded),
    )


def _build_failure(snapshot, candidates, untracked):
    """Build the failure diagnosis from the immutable snapshot of the failing
    burn.  Never uses mission-start state, other branches or rolled-back
    temporary states."""
    burn = snapshot['burn']
    target = snapshot['target']
    working = snapshot['working']
    mass = snapshot['mass']
    attached_indices = [s.stage_index for s in working if s.attached]
    tried = [(pos, sidx, code) for pos, sidx, code, _, _, _ in candidates]
    codes = [code for _, _, code in tried]
    avail_list = [avail for _, _, _, avail, _, _ in candidates]
    failure_code = _classify_failure_code(codes)

    aggregate_dv, contributing = _sequential_reachable_dv(working, mass,
                                                          target)
    limitation = False
    if (failure_code is FailureCode.PROPELLANT_INSUFFICIENT
            and contributing >= 2
            and aggregate_dv + _DV_CHECK_TOL >= target):
        limitation = True

    if failure_code is FailureCode.NO_STAGE_ASSIGNED:
        main_reason = '没有仍连接的可用执行级'
    elif failure_code is FailureCode.DATA_INSUFFICIENT:
        main_reason = '候选级存在数据不足或异常失败'
    elif failure_code is FailureCode.PROPELLANT_INSUFFICIENT:
        main_reason = '候选级推进剂/Δv 不足'
    else:
        main_reason = '所有候选级均因不支持多次点火被阻断'

    limitation_text = ''
    if limitation:
        limitation_text = (
            '当前模型限制 whole_burn_per_stage=True（一次燃烧必须由单级'
            '完整完成），多个级合计可能完成该次机动，但未建模跨级燃烧。')

    per_candidate = '；'.join(
        f'级 {sidx}: {code.value}' for _, sidx, code in tried) or '无'

    reason = (
        f'燃烧 {burn.burn_id}（{burn.name}）目标 {target:.1f} m/s 无法规划；'
        f'当前已连接级 {attached_indices}；实际检查候选级 '
        f'{[sidx for _, sidx, _ in tried]}；各候选级失败原因：'
        f'{per_candidate}；主要阻断原因：{main_reason}。{limitation_text}'
    ).strip()

    warnings = []
    if all(code is FailureCode.RESTART_UNSUPPORTED for code in codes) \
            and codes:
        warnings.append(
            f'候选级 {[sidx for _, sidx, _ in tried]} 均不满足剩余点火次数要求'
            '（不支持重启或次数已用尽）')
    if limitation:
        warnings.append(
            '多个级合计可能完成该次机动，但当前模型不支持一次 '
            'BurnRequirement 跨级。')

    diagnostics = {
        'failure_burn_id': burn.burn_id,
        'failure_burn_order': burn.order,
        'failure_attached_stage_indices': attached_indices,
        'failure_snapshot_vehicle_mass_t': mass,
        'candidate_failure_codes': [code.value if code else None
                                    for code in codes],
        'candidate_available_dv_m_s': avail_list,
        'aggregate_reachable_dv_m_s': aggregate_dv,
        'whole_burn_limitation_triggered': limitation,
    }
    return {
        'reason': reason,
        'warnings': warnings,
        'code': failure_code,
        'diagnostics': diagnostics,
        'working': working,
        'mass': mass,
    }


def _failure_result(rocket_ref, stages, reqs, vehicle_mass_t, payload_t,
                    data_quality, max_nodes, evaluations, failure):
    """Build the failure result from a snapshot diagnosis.  Never leaks
    partial assignments or separation events."""
    return PostParkingPlanResult(
        rocket_ref=rocket_ref,
        success=False,
        failure_code=failure['code'],
        failure_reason_zh=failure['reason'],
        data_quality=data_quality,
        requirements=_unassigned_requirements(reqs),
        separations_before={},
        planned_final_stages=[_copy_stage(s) for s in stages],
        initial_vehicle_mass_t=vehicle_mass_t,
        planned_final_vehicle_mass_t=vehicle_mass_t,
        payload_t=payload_t,
        warnings=failure['warnings'],
        metadata=_plan_metadata(
            stages, [r for r in reqs if r.source != 'rocket_calibration'],
            evaluations, max_nodes, {}, 0.0, failure['diagnostics']),
    )


def _data_insufficient_result(rocket_ref, stages, reqs, vehicle_mass_t,
                              payload_t, data_quality, max_nodes, nodes,
                              reasons):
    return PostParkingPlanResult(
        rocket_ref=rocket_ref,
        success=False,
        failure_code=FailureCode.DATA_INSUFFICIENT,
        failure_reason_zh='；'.join(reasons),
        data_quality=DataQuality.INSUFFICIENT,
        requirements=_unassigned_requirements(reqs),
        separations_before={},
        planned_final_stages=[_copy_stage(s) for s in stages],
        initial_vehicle_mass_t=vehicle_mass_t,
        planned_final_vehicle_mass_t=vehicle_mass_t,
        payload_t=payload_t,
        warnings=list(reasons),
        metadata=_plan_metadata(
            stages, [r for r in reqs if r.source != 'rocket_calibration'],
            nodes, max_nodes, {}, 0.0),
    )
