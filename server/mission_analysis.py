"""
Single-rocket orbital-mission backend analysis service — no Flask, no SQLite.
=============================================================================

Composes the pure-Python mission pipeline for ONE rocket and ONE mission:

    1. normalize_stages          (raw config -> StageStates)
    2. burn_sequence_to_requirements
    3. find_max_payload_for_mission
    4. evaluate_payload_for_mission   (only when a user payload is given)

and produces a compact, stable API summary plus a full audit trail.

This module is deliberately decoupled from Flask / request / jsonify /
SQLite / server.models / frontend.  ``normalize_stages`` may indirectly READ
local engine .cfg files (allowed); this module itself never writes files or
databases.  It is designed to be called per rocket in a whole-library loop:
a single rocket's data problems must not break other rockets.

The existing ``/api/mission-query`` route is NOT touched by this module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real
from typing import List, Optional

from c3_mass.mission_adapters import (
    BurnSequenceConversionResult,
    StageNormalizationResult,
    burn_sequence_to_requirements,
    normalize_stages,
)
from c3_mass.mission_capacity_search import (
    MissionCapacityResult,
    find_max_payload_for_mission,
)
from c3_mass.mission_payload_evaluator import (
    PayloadFeasibilityResult,
    evaluate_payload_for_mission,
)
from c3_mass.mission_simulation import (
    DataQuality,
    FailureCode,
    to_dict as _contract_to_dict,
)

_VERIFICATION_STATUSES = (
    'verified_feasible',
    'verified_infeasible',
    'data_insufficient',
)

_FAILURE_PHASES = (
    'normalization',
    'burn_conversion',
    'capacity_search',
    'calibration_lookup',
    'parking_replay',
    'parking_consistency',
    'stage_planning',
    'plan_verification',
    None,
)


# =============================================================================
# Result type
# =============================================================================


@dataclass
class RocketMissionAnalysisResult:
    """Full analysis of one rocket against one mission.

    ``to_dict()`` returns the complete structure (tests / backend
    diagnostics).  ``to_api_dict(include_audit=False)`` returns a compact,
    stable summary suitable for the future API layer; the default summary
    never embeds the capacity search's full evaluation samples or stage
    states.  Both are directly JSON-serializable and never share nested
    mutable references with each other.
    """
    rocket: str
    slug: str
    verification_status: str
    verified_feasible: Optional[bool]
    recommendation_eligible: bool
    data_quality: DataQuality
    failure_code: FailureCode
    failure_reason_zh: str
    failure_phase: Optional[str]
    normalization: Optional[StageNormalizationResult]
    burn_conversion: Optional[BurnSequenceConversionResult]
    capacity_search: Optional[MissionCapacityResult]
    requested_payload_evaluation: Optional[PayloadFeasibilityResult]
    warnings: List[str]
    metadata: dict

    def to_dict(self):
        return _contract_to_dict(self)

    # ------------------------------------------------------------------ API --
    def _capacity_summary(self):
        capacity = self.capacity_search
        if capacity is None:
            return None
        return {
            'status': capacity.capacity_status,
            'max_payload_t': capacity.max_payload_t,
            'feasible_lower_bound_t': capacity.feasible_lower_bound_t,
            'infeasible_upper_bound_t': capacity.infeasible_upper_bound_t,
            'payload_tolerance_t': capacity.payload_tolerance_t,
            'data_quality': capacity.data_quality.value,
            'evaluation_count': capacity.metadata['evaluation_count'],
            'boundary_width_t': capacity.metadata['boundary_width_t'],
        }

    def _requested_payload_summary(self):
        evaluation = self.requested_payload_evaluation
        if evaluation is None:
            return None
        replay = evaluation.parking_replay
        return {
            'payload_t': evaluation.payload_t,
            'feasible': evaluation.feasible,
            'failure_code': evaluation.failure_code.value,
            'failure_reason_zh': evaluation.failure_reason_zh,
            'failure_phase': evaluation.metadata.get('failure_phase'),
            'data_quality': evaluation.data_quality.value,
            'calibrated_remaining_dv_m_s':
                evaluation.calibrated_remaining_dv_m_s,
            'parking_active_stage_index':
                replay.active_stage_index if replay is not None else None,
            'plan_verified': evaluation.metadata.get('plan_verified'),
        }

    def _timeline_source(self):
        """Pick the evaluation whose simulation (if any) owns the timeline."""
        if self.requested_payload_evaluation is not None:
            return self.requested_payload_evaluation
        capacity = self.capacity_search
        if (capacity is not None and capacity.capacity_status == 'bounded'
                and capacity.best_feasible_evaluation is not None):
            return capacity.best_feasible_evaluation
        return None

    def _burn_timeline(self):
        source = self._timeline_source()
        simulation = source.simulation if source is not None else None
        requirements = self.burn_conversion.requirements \
            if self.burn_conversion is not None else []
        by_id = {req.burn_id: req for req in requirements}

        if simulation is not None:
            timeline = []
            for burn in simulation.burns:
                req = by_id.get(burn.burn_id)
                timeline.append({
                    'burn_id': burn.burn_id,
                    'order': burn.order,
                    'name': req.name if req is not None else burn.burn_id,
                    'source': req.source if req is not None else 'unknown',
                    'target_dv_m_s': burn.target_dv_m_s,
                    'stage_index': burn.stage_index,
                    'ignition_kind': burn.ignition_kind.value,
                    'actual_dv_m_s': burn.actual_dv_m_s,
                    'mass_before_t': burn.mass_before_t,
                    'mass_after_t': burn.mass_after_t,
                    'propellant_consumed_t': burn.propellant_consumed_t,
                    'success': burn.success,
                    'failure_code': burn.failure_code.value,
                })
            return timeline

        # No simulation: the mission burns were never executed.  Show the
        # original requirements as unexecuted entries — never forge stages.
        return [{
            'burn_id': req.burn_id,
            'order': req.order,
            'name': req.name,
            'source': req.source,
            'target_dv_m_s': float(req.target_dv_m_s),
            'stage_index': None,
            'ignition_kind': None,
            'actual_dv_m_s': None,
            'mass_before_t': None,
            'mass_after_t': None,
            'propellant_consumed_t': None,
            'success': None,
            'failure_code': None,
        } for req in requirements]

    def to_api_dict(self, include_audit=False):
        summary = {
            'rocket': self.rocket,
            'slug': self.slug,
            'verification_status': self.verification_status,
            'verified_feasible': self.verified_feasible,
            'recommendation_eligible': self.recommendation_eligible,
            'data_quality': self.data_quality.value,
            'calibration_support': dict(self.metadata.get('calibration_support') or {}),
            'failure_code': self.failure_code.value,
            'failure_reason_zh': self.failure_reason_zh,
            'failure_phase': self.failure_phase,
            'capacity': self._capacity_summary(),
            'requested_payload': self._requested_payload_summary(),
            'burn_timeline': self._burn_timeline(),
            'warnings': list(self.warnings),
            'model': {
                'evaluation_model': 'calibrated_serial_multiburn',
                'planning_policy': 'lowest_reachable_capable_stage',
                'whole_burn_per_stage': True,
                'parallel_supported': False,
                'finite_burn_supported': False,
                'calibration_source': 'dv_curve',
            },
        }
        if include_audit:
            summary['audit'] = {
                'normalization': (self.normalization.to_dict()
                                  if self.normalization is not None else None),
                'burn_conversion': (self.burn_conversion.to_dict()
                                    if self.burn_conversion is not None
                                    else None),
                'capacity_search': (self.capacity_search.to_dict()
                                    if self.capacity_search is not None
                                    else None),
                'requested_payload_evaluation': (
                    self.requested_payload_evaluation.to_dict()
                    if self.requested_payload_evaluation is not None
                    else None),
                'metadata': dict(self.metadata),
            }
        return summary


def unavailable_rocket_analysis(rocket, slug, *, reason, phase=None,
                                extra_warnings=None,
                                payload_requested=False) -> RocketMissionAnalysisResult:
    """Stable ``data_insufficient`` result for a rocket whose config or
    computed data are missing or unusable.

    Keeps a single result protocol across the whole fleet: the route layer
    must never hand-build a second, ad-hoc analysis structure.  The result
    is a full RocketMissionAnalysisResult, so ``to_dict()`` / ``to_api_dict()``
    behave exactly like results produced by ``analyze_rocket_for_mission``.
    ``payload_requested`` records whether the caller asked for a specific
    payload (default False).
    """
    return RocketMissionAnalysisResult(
        rocket=rocket,
        slug=slug,
        verification_status='data_insufficient',
        verified_feasible=None,
        recommendation_eligible=False,
        data_quality=DataQuality.INSUFFICIENT,
        failure_code=FailureCode.DATA_INSUFFICIENT,
        failure_reason_zh=reason,
        failure_phase=phase,
        normalization=None,
        burn_conversion=None,
        capacity_search=None,
        requested_payload_evaluation=None,
        warnings=[w for w in (extra_warnings or [])
                  if isinstance(w, str) and w],
        metadata={'payload_requested': bool(payload_requested),
                  'capacity_status': None},
    )


# =============================================================================
# Validation helpers
# =============================================================================
def _require_non_empty_string(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a non-empty string')
    return value


def _require_dict(value, name):
    if not isinstance(value, dict):
        raise ValueError(f'{name} must be a dict')
    return value


def _require_positive_finite(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError(f'{name} must be a finite number, got {value!r}')
    value = float(value)
    if value <= 0:
        raise ValueError(f'{name} must be > 0, got {value}')
    return value


def _require_positive_int(value, name, minimum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{name} must be an integer, got {value!r}')
    if value < minimum:
        raise ValueError(f'{name} must be >= {minimum}, got {value}')
    return value


def _merge_warnings(*groups):
    """Deduplicate warnings preserving first-occurrence order; all entries
    are non-empty strings.  Deterministic for identical inputs."""
    merged = []
    seen = set()
    for group in groups:
        if not group:
            continue
        for warning in group:
            if not isinstance(warning, str) or not warning:
                continue
            if warning not in seen:
                seen.add(warning)
                merged.append(warning)
    return merged


# =============================================================================
# Service entry point
# =============================================================================


def analyze_rocket_for_mission(
        rocket: str,
        slug: str,
        config: dict,
        computed_data: dict,
        mission: dict,
        payload_t=None,
        data_quality: DataQuality = DataQuality.EXACT,
        payload_tolerance_t: float = 0.001,
        monotonicity_samples: int = 17,
        max_evaluations: int = 128,
        max_search_nodes: int = 10000,
) -> RocketMissionAnalysisResult:
    """Analyze one rocket against one mission; see the module docstring.

    Calling errors (wrong top-level types, missing stages / burn_sequence /
    dv_curve structure, invalid payload_t or search parameters) raise
    ValueError.  Single-rocket DATA problems (bad curve content, engine
    files missing, parallel stages, calibration inconsistency, search
    data-insufficiency) are converted into a ``data_insufficient`` result
    and never interrupt a whole-library loop.  Internal errors (RuntimeError
    mass conservation, plan/simulation inconsistency) are NOT swallowed.
    """
    # --- calling validation (ValueError) ------------------------------------
    _require_non_empty_string(rocket, 'rocket')
    _require_non_empty_string(slug, 'slug')
    _require_dict(config, 'config')
    stages_cfg = config.get('stages')
    if (isinstance(stages_cfg, (str, bytes))
            or not isinstance(stages_cfg, (list, tuple))
            or not stages_cfg):
        raise ValueError('config must contain a non-empty stages list')
    _require_dict(computed_data, 'computed_data')
    _require_dict(mission, 'mission')
    burn_sequence = mission.get('burn_sequence')
    if (isinstance(burn_sequence, (str, bytes))
            or not isinstance(burn_sequence, (list, tuple))
            or not burn_sequence):
        raise ValueError('mission must contain a non-empty burn_sequence')
    if payload_t is not None:
        payload_t = _require_positive_finite(payload_t, 'payload_t')
    if not isinstance(data_quality, DataQuality):
        raise ValueError('data_quality must be a DataQuality')
    tolerance = _require_positive_finite(payload_tolerance_t,
                                         'payload_tolerance_t')
    samples = _require_positive_int(monotonicity_samples,
                                    'monotonicity_samples', 3)
    max_evals = _require_positive_int(max_evaluations, 'max_evaluations', 1)
    if max_evals < samples:
        raise ValueError(
            f'max_evaluations ({max_evals}) must be >= monotonicity_samples '
            f'({samples})')
    _require_positive_int(max_search_nodes, 'max_search_nodes', 1)

    def build(status, verified, quality, failure_code, reason, phase,
              normalization=None, burn_conversion=None, capacity=None,
              requested_eval=None, extra_warnings=None,
              capacity_status=None):
        points = config.get('calibration') or []
        measured = [point['payload_t'] for point in points
                    if isinstance(point, dict) and isinstance(point.get('payload_t'), Real)
                    and not isinstance(point['payload_t'], bool) and math.isfinite(point['payload_t'])]
        candidate = payload_t if payload_t is not None else (capacity.max_payload_t if capacity else None)
        support = {'status': 'unspecified', 'evaluated_payload_t': candidate}
        support_warnings = []
        if measured:
            inside = candidate is not None and min(measured) <= candidate <= max(measured)
            support.update(measured_payload_min_t=min(measured), measured_payload_max_t=max(measured),
                           status=('within_measured_payload_range' if inside else
                                   'extrapolated' if candidate is not None else 'not_evaluated'))
            if support['status'] == 'extrapolated':
                support_warnings.append(f'载荷 {candidate:.4g} t 位于实测范围 [{min(measured):g}, {max(measured):g}] t 外；结果为模型外推，不能作为实测支持的推荐')
        return RocketMissionAnalysisResult(
            rocket=rocket,
            slug=slug,
            verification_status=status,
            verified_feasible=verified,
            recommendation_eligible=(status == 'verified_feasible' and support['status'] != 'extrapolated'),
            data_quality=quality,
            failure_code=failure_code,
            failure_reason_zh=reason,
            failure_phase=phase,
            normalization=normalization,
            burn_conversion=burn_conversion,
            capacity_search=capacity,
            requested_payload_evaluation=requested_eval,
            warnings=_merge_warnings((extra_warnings or []) + support_warnings),
            metadata={
                'payload_requested': payload_t is not None,
                'capacity_status': capacity_status,
                'calibration_support': support,
            },
        )

    # --- normalization ------------------------------------------------------
    try:
        normalization = normalize_stages(config['stages'])
    except ValueError as exc:
        return build('data_insufficient', None, DataQuality.INSUFFICIENT,
                     FailureCode.DATA_INSUFFICIENT, str(exc),
                     'normalization',
                     extra_warnings=[f'级标准化失败：{exc}'])

    if not normalization.ready_for_serial_replay:
        return build('data_insufficient', None, DataQuality.INSUFFICIENT,
                     FailureCode.DATA_INSUFFICIENT,
                     '；'.join(normalization.warnings) or '级标准化后不可回放',
                     'normalization', normalization=normalization,
                     extra_warnings=normalization.warnings)

    # --- burn conversion ----------------------------------------------------
    try:
        burn_conversion = burn_sequence_to_requirements(burn_sequence)
    except ValueError as exc:
        return build('data_insufficient', None, DataQuality.INSUFFICIENT,
                     FailureCode.DATA_INSUFFICIENT, str(exc),
                     'burn_conversion', normalization=normalization,
                     extra_warnings=[f'燃烧序列转换失败：{exc}'])

    dv_curve = computed_data.get('dv_curve')
    if dv_curve is None:
        return build('data_insufficient', None, DataQuality.INSUFFICIENT,
                     FailureCode.DATA_INSUFFICIENT,
                     'computed_data 缺少 dv_curve，无法进行校准评估',
                     'capacity_search', normalization=normalization,
                     burn_conversion=burn_conversion,
                     extra_warnings=['computed_data 缺少 dv_curve'])

    # --- capacity search ----------------------------------------------------
    reference_parking = (computed_data.get('parking_orbit') or {}).get('h_km', 220.0)
    mission_parking = mission.get('parking_alt_km', reference_parking)
    if abs(float(mission_parking) - float(reference_parking)) > 1e-6:
        return build('data_insufficient', None, DataQuality.INSUFFICIENT,
                     FailureCode.DATA_INSUFFICIENT,
                     f'停泊轨道 {mission_parking} km 与校准基准 {reference_parking} km 不符',
                     'calibration_lookup', normalization=normalization,
                     burn_conversion=burn_conversion,
                     extra_warnings=['改变停泊高度需要对应校准或经过验证的上升段模型'])
    correction = mission.get('ascent_adjustment_dv_m_s', mission.get('dv_launch_plane', 0.0))
    if isinstance(correction, bool) or not isinstance(correction, Real) or not math.isfinite(correction):
        raise ValueError('ascent_adjustment_dv_m_s must be a finite number')
    correction_kwargs = {'ascent_adjustment_dv_m_s': float(correction)} if correction else {}
    capacity = None
    try:
        capacity = find_max_payload_for_mission(
            slug, normalization.stages, burn_conversion.requirements,
            dv_curve, data_quality=data_quality,
            payload_tolerance_t=tolerance,
            monotonicity_samples=samples,
            max_evaluations=max_evals,
            max_search_nodes=max_search_nodes, **correction_kwargs)
    except ValueError as exc:
        return build('data_insufficient', None, DataQuality.INSUFFICIENT,
                     FailureCode.DATA_INSUFFICIENT, str(exc),
                     'capacity_search', normalization=normalization,
                     burn_conversion=burn_conversion,
                     extra_warnings=[f'容量搜索失败：{exc}'])

    # --- requested payload evaluation ---------------------------------------
    requested_eval = None
    if payload_t is not None:
        try:
            requested_eval = evaluate_payload_for_mission(
                slug, normalization.stages, burn_conversion.requirements,
                dv_curve, payload_t, data_quality=data_quality,
                max_search_nodes=max_search_nodes, **correction_kwargs)
        except ValueError as exc:
            return build('data_insufficient', None,
                         DataQuality.INSUFFICIENT,
                         FailureCode.DATA_INSUFFICIENT, str(exc),
                         'capacity_search', normalization=normalization,
                         burn_conversion=burn_conversion, capacity=capacity,
                         extra_warnings=[f'单载荷评估失败：{exc}'],
                         capacity_status=capacity.capacity_status
                         if capacity else None)

    # --- verification status resolution -------------------------------------
    extra_warnings = []
    extra_warnings.extend(normalization.warnings)
    extra_warnings.extend(burn_conversion.warnings)
    if capacity is not None:
        extra_warnings.extend(capacity.warnings)
    if requested_eval is not None:
        extra_warnings.extend(requested_eval.warnings)

    if payload_t is not None:
        # the single-payload evaluation is the authority
        evaluation = requested_eval
        if evaluation.feasible:
            return build('verified_feasible', True, evaluation.data_quality,
                         FailureCode.NONE, '', None,
                         normalization=normalization,
                         burn_conversion=burn_conversion, capacity=capacity,
                         requested_eval=evaluation,
                         extra_warnings=extra_warnings,
                         capacity_status=capacity.capacity_status
                         if capacity else None)
        if evaluation.failure_code is FailureCode.DATA_INSUFFICIENT:
            return build('data_insufficient', None,
                         evaluation.data_quality, evaluation.failure_code,
                         evaluation.failure_reason_zh,
                         evaluation.metadata.get('failure_phase'),
                         normalization=normalization,
                         burn_conversion=burn_conversion, capacity=capacity,
                         requested_eval=evaluation,
                         extra_warnings=extra_warnings,
                         capacity_status=capacity.capacity_status
                         if capacity else None)
        return build('verified_infeasible', False, evaluation.data_quality,
                     evaluation.failure_code, evaluation.failure_reason_zh,
                     evaluation.metadata.get('failure_phase'),
                     normalization=normalization,
                     burn_conversion=burn_conversion, capacity=capacity,
                     requested_eval=evaluation,
                     extra_warnings=extra_warnings,
                     capacity_status=capacity.capacity_status
                     if capacity else None)

    # no user payload: the capacity search is the authority
    if capacity.success and capacity.capacity_status == 'bounded':
        return build('verified_feasible', True, capacity.data_quality,
                     FailureCode.NONE, '', None,
                     normalization=normalization,
                     burn_conversion=burn_conversion, capacity=capacity,
                     extra_warnings=extra_warnings,
                     capacity_status=capacity.capacity_status)
    return build('data_insufficient', None, capacity.data_quality,
                 capacity.failure_code, capacity.failure_reason_zh,
                 'capacity_search', normalization=normalization,
                 burn_conversion=burn_conversion, capacity=capacity,
                 extra_warnings=extra_warnings,
                 capacity_status=capacity.capacity_status)
