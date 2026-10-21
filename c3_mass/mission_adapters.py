"""
Mission-simulation input adapters — pure Python.
================================================

Two conversions that bridge the existing computation layer to the mission
simulation layer (mission_simulation.py / mission_solver.py):

1. ``normalize_stages``: raw rocket-config stages (the stored stages, ordered
   bottom-to-top, list position = canonical stage_index) -> one-to-one
   StageState list.  Computed-layer virtual stages (result['stages'] after
   parallel booster expansion) are NEVER used or paired here.

2. ``burn_sequence_to_requirements``: compute_orbit_mission's burn_sequence
   -> BurnRequirement list, skipping non-ignition entries below the
   ignition threshold.

I/O behaviour: this module itself performs no file writes, no database and no
network access.  Note that ``normalize_stages`` indirectly READS local
engine .cfg files through ``resolve_stage_engine_performance``, so it is not
a fully I/O-free function.

This phase does NOT perform parking-orbit propellant replay, does not assign
executing stages (all preferred_stage_index stay None) and does not generate
separation plans or call the multistage solver.

Dependencies: standard library, mission_simulation.py and
compute_c3_mass.resolve_stage_engine_performance only.  No Flask, SQLite,
server.models, frontend or .craft/VAB code, no third-party dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from numbers import Integral, Real
from typing import Any, List, Optional

from c3_mass.compute_c3_mass import resolve_stage_engine_performance
from c3_mass.mission_simulation import (
    BurnRequirement,
    DataQuality,
    StageState,
    to_dict as _contract_to_dict,
)

_ENGINE_DATA_MISSING = (ValueError, FileNotFoundError)


# =============================================================================
# Validation helpers — reject truly invalid calling arguments with ValueError.
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


# =============================================================================
# Stage normalization
# =============================================================================


@dataclass
class StageNormalizationResult:
    """Outcome of normalizing raw rocket-config stages into StageStates.

    ``stages`` are one-to-one with the input config stages (input order,
    list position as stage_index).  ``ready_for_serial_replay`` is False when
    any stage is parallel or lacks usable engine data.
    """
    stages: List[StageState]
    data_quality: DataQuality
    ready_for_serial_replay: bool
    warnings: List[str]
    metadata: dict

    def to_dict(self):
        return _contract_to_dict(self)


def _stage_engine_cfgs(stage_cfg):
    """JSON-safe list of engine cfg filenames used by a config stage."""
    groups = stage_cfg.get('engine_groups')
    if groups is None:
        groups = [stage_cfg.get('engines', {})]
    elif isinstance(groups, dict):
        groups = [groups]
    result = []
    total = 0
    if isinstance(groups, (list, tuple)):
        for group in groups:
            if not isinstance(group, dict):
                continue
            cfg = group.get('cfg')
            if isinstance(cfg, str) and cfg:
                result.append(cfg)
            try:
                total += int(group.get('count', 1))
            except (TypeError, ValueError):
                pass
    return result, total


def normalize_stages(cfg_stages) -> StageNormalizationResult:
    """Convert raw rocket-config stages to one-to-one StageStates.

    Rules:
    * cfg_stages must be a non-empty list/tuple of dicts; order is preserved.
    * stage_index = list position (0, 1, 2, ...) — computed-layer virtual
      stage numbers are never used.
    * wet/dry come from the config; propellant_remaining_t = wet - dry;
      isp_vac_s comes from ``resolve_stage_engine_performance`` (multi-engine
      groups are combined, never reduced to the first engine only).
    * multi_ignition / parallel are preserved; parallel stages are returned
      one-to-one (no virtual expansion) but mark the result as not ready for
      serial replay.
    * Missing engine data -> isp_vac_s=None (no fabricated Isp), quality
      INSUFFICIENT, warning with stage_index, cfg and reason.
    * Truly invalid arguments (negative mass, dry > wet, bool-as-mass, empty
      input, non-dict entry) raise ValueError.
    """
    if (isinstance(cfg_stages, (str, bytes))
            or not isinstance(cfg_stages, (list, tuple))
            or not cfg_stages):
        raise ValueError('cfg_stages must be a non-empty list or tuple')

    stages = []
    warnings = []
    ready = True
    quality = DataQuality.EXACT

    for i, stage_cfg in enumerate(cfg_stages):
        if not isinstance(stage_cfg, dict):
            raise ValueError(f'cfg_stages[{i}] must be a dict')

        if 'wet_mass_t' not in stage_cfg or 'dry_mass_t' not in stage_cfg:
            raise ValueError(
                f'cfg_stages[{i}] missing wet_mass_t/dry_mass_t')
        wet = _require_finite_number(
            stage_cfg['wet_mass_t'], f'cfg_stages[{i}].wet_mass_t', minimum=0.0)
        dry = _require_finite_number(
            stage_cfg['dry_mass_t'], f'cfg_stages[{i}].dry_mass_t', minimum=0.0)
        if dry > wet:
            raise ValueError(
                f'cfg_stages[{i}] dry_mass_t ({dry}) must be <= wet_mass_t ({wet})')

        for flag in ('multi_ignition', 'parallel'):
            if flag in stage_cfg and not isinstance(stage_cfg[flag], bool):
                raise ValueError(f'cfg_stages[{i}].{flag} must be a bool')
        multi = bool(stage_cfg.get('multi_ignition', False))
        parallel = bool(stage_cfg.get('parallel', False))

        engine_cfgs, engine_count_total = _stage_engine_cfgs(stage_cfg)
        isp_vac = None
        try:
            performance = resolve_stage_engine_performance(stage_cfg)
            isp_vac = float(performance['isp_vac'])
        except _ENGINE_DATA_MISSING as exc:
            ready = False
            quality = DataQuality.INSUFFICIENT
            warnings.append(
                f'stage {i} 发动机数据不足（cfg={engine_cfgs!r}）：{exc}')

        if parallel:
            ready = False
            quality = DataQuality.INSUFFICIENT
            warnings.append(
                f'stage {i} 是并联级（parallel=True），并联燃烧尚未建模，'
                '暂不支持串联回放')

        stages.append(StageState(
            stage_index=i,
            name=None,
            wet_mass_t=wet,
            dry_mass_t=dry,
            propellant_remaining_t=wet - dry,
            isp_vac_s=isp_vac,
            multi_ignition=multi,
            ignition_count=0,
            attached=True,
            parallel=parallel,
            metadata={
                'cfg_index': i,
                'engine_cfg': list(engine_cfgs),
                'engine_count_total': engine_count_total,
                'performance_source': 'resolve_stage_engine_performance',
                'max_ignitions': stage_cfg.get('max_ignitions'),
                'ground_ignition_only': stage_cfg.get('ground_ignition_only',False),
                'ignition_limit_source': stage_cfg.get('ignition_limit_source', 'legacy_flag'),
            },
        ))

    return StageNormalizationResult(
        stages=stages,
        data_quality=quality,
        ready_for_serial_replay=ready,
        warnings=warnings,
        metadata={
            'source': 'raw_rocket_config_stages',
            'stage_count': len(stages),
        },
    )


# =============================================================================
# Burn sequence conversion
# =============================================================================


@dataclass
class BurnSequenceConversionResult:
    """Outcome of converting a burn_sequence to BurnRequirements.

    ``requirements`` only contain real ignitions (source='rocket_calibration'
    is always kept as a record) and no preferred_stage_index (stage
    assignment is a later phase).  Skipped entries are recorded in
    ``skipped_entries`` with reason='below_ignition_threshold'.
    """
    requirements: List[BurnRequirement]
    skipped_entries: List[dict]
    warnings: List[str]
    metadata: dict

    def to_dict(self):
        return _contract_to_dict(self)


def burn_sequence_to_requirements(burn_sequence,
                                  ignition_threshold_m_s=0.1
                                  ) -> BurnSequenceConversionResult:
    """Convert a ``compute_orbit_mission`` burn_sequence into requirements.

    * ``order`` must be a non-bool positive integer, strictly increasing.
    * ``source`` non-empty string; ``name`` a string; ``dv_m_s`` a finite
      non-negative number (numpy scalars are converted to Python float).
    * burn_id: a valid explicit ``burn_id`` is kept; otherwise
      ``orbit_burn_{order}`` is generated (never derived from the name);
      final ids must be unique.
    * source='rocket_calibration': always converted (even dv_m_s=0),
      preferred_stage_index=None.
    * other sources: converted only when ``dv_m_s > ignition_threshold_m_s``;
      entries at or below the threshold are recorded in skipped_entries with
      reason='below_ignition_threshold' and do not renumber the others.
    * No preferred_stage_index is filled in this phase.
    """
    threshold = _require_finite_number(
        ignition_threshold_m_s, 'ignition_threshold_m_s', minimum=0.0)

    if (isinstance(burn_sequence, (str, bytes))
            or not isinstance(burn_sequence, (list, tuple))
            or not burn_sequence):
        raise ValueError('burn_sequence must be a non-empty list or tuple')

    seen_orders = []
    explicit_ids = set()
    for i, entry in enumerate(burn_sequence):
        if not isinstance(entry, dict):
            raise ValueError(f'burn_sequence[{i}] must be a dict')
        if 'order' not in entry:
            raise ValueError(f'burn_sequence[{i}] missing order')
        order = _require_positive_int(entry['order'], f'burn_sequence[{i}].order')
        if seen_orders and order <= seen_orders[-1]:
            raise ValueError(
                f'burn_sequence order must be strictly increasing '
                f'(index {i}, order={order})')
        seen_orders.append(order)
        source = entry.get('source')
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f'burn_sequence[{i}].source must be a non-empty string')
        if not isinstance(entry.get('name'), str):
            raise ValueError(f'burn_sequence[{i}].name must be a string')
        _require_finite_number(entry.get('dv_m_s'), f'burn_sequence[{i}].dv_m_s',
                               minimum=0.0)
        burn_id = entry.get('burn_id')
        if burn_id is not None:
            if not isinstance(burn_id, str) or not burn_id.strip():
                raise ValueError(f'burn_sequence[{i}].burn_id must be a non-empty string')
            if burn_id in explicit_ids:
                raise ValueError(f'duplicate burn_id: {burn_id}')
            explicit_ids.add(burn_id)

    requirements = []
    skipped_entries = []
    used_ids = set()
    for entry in burn_sequence:
        order = int(entry['order'])
        source = entry['source']
        name = entry['name']
        dv = float(entry['dv_m_s'])
        burn_id = entry['burn_id'] if entry.get('burn_id') else f'orbit_burn_{order}'

        is_calibration = source == 'rocket_calibration'
        is_ignition = dv > threshold
        if is_calibration or is_ignition:
            if burn_id in used_ids:
                raise ValueError(f'duplicate burn_id after conversion: {burn_id}')
            used_ids.add(burn_id)
            requirements.append(BurnRequirement(
                burn_id=burn_id,
                order=order,
                target_dv_m_s=dv,
                source=source,
                name=name,
                preferred_stage_index=None,
                metadata={},
            ))
        else:
            skipped_entries.append({
                'order': order,
                'name': name,
                'source': source,
                'dv_m_s': dv,
                'reason': 'below_ignition_threshold',
            })

    warnings = []
    if skipped_entries:
        warnings.append(
            f'{len(skipped_entries)} 个低于点火阈值（{threshold} m/s）的'
            '任务条目被跳过')

    return BurnSequenceConversionResult(
        requirements=requirements,
        skipped_entries=skipped_entries,
        warnings=warnings,
        metadata={
            'entry_count': len(burn_sequence),
            'ignition_threshold_m_s': threshold,
            'requirement_count': len(requirements),
            'skipped_count': len(skipped_entries),
        },
    )
