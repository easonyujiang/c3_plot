"""
Mission Simulation Data Contract — pure Python, no I/O.
=======================================================

Pure data structures for staged, multi-ignition mission simulation.

Responsibilities:
    BurnRequirement           — what a mission asks for (task input).
    StageState                — a stage at a moment in time (solver state).
    BurnResult                — what a single burn actually did (solver output).
    MissionSimulationResult   — full mission outcome (solver output).

Constraints:
    * Must not import Flask, SQLite, server.models or frontend code.
    * Must not read or write files.
    * No rocket equation, no propellant deduction, no staging logic,
      no stage assignment. The solver (later phase) decides those.
    * Enum values are stable lowercase strings and part of the API contract.
    * to_dict() output is directly JSON-serializable.  metadata and any
      dict/list/tuple data must be JSON-compatible (recursive validation,
      with field paths in error messages); ``to_dict()`` re-validates data
      that was assigned to public fields after construction.
    * Caller-supplied dicts/lists/metadata are deep-copied; nothing is mutated.
"""

from __future__ import annotations

import copy
import dataclasses
import math
from dataclasses import dataclass, field
from enum import Enum
from numbers import Real
from typing import Any, List, Optional


# =============================================================================
# Stable enumerations — values are lowercase strings used by the API.
# Do not rename or reword values; add new members only.
# =============================================================================


class FailureCode(str, Enum):
    """Stable failure codes. ``NONE`` means no failure."""
    NONE = 'none'
    INVALID_INPUT = 'invalid_input'
    DATA_INSUFFICIENT = 'data_insufficient'
    NO_STAGE_ASSIGNED = 'no_stage_assigned'
    RESTART_UNSUPPORTED = 'restart_unsupported'
    PROPELLANT_INSUFFICIENT = 'propellant_insufficient'


class DataQuality(str, Enum):
    """Confidence in the data backing a result."""
    EXACT = 'exact'
    ESTIMATED = 'estimated'
    INSUFFICIENT = 'insufficient'


class IgnitionKind(str, Enum):
    """Per-burn ignition classification. Decided by the solver, never by the
    task requirement: the same requirement may be a first ignition or a
    restart depending on the stage assignment."""
    FIRST = 'first'
    RESTART = 'restart'
    UNASSIGNED = 'unassigned'


# =============================================================================
# Validation helpers — reject invalid input loudly, never fix it silently.
# =============================================================================


def _require_number(value, name, *, minimum=0.0, allow_none=False):
    if value is None:
        if allow_none:
            return None
        raise ValueError(f'{name} must be a number, got None')
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError(f'{name} must be a finite number, got {value!r}')
    value = float(value)
    if value < minimum:
        raise ValueError(f'{name} must be >= {minimum}, got {value}')
    return value


def _require_int(value, name, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{name} must be an integer, got {value!r}')
    if value < minimum:
        raise ValueError(f'{name} must be >= {minimum}, got {value}')
    return value


def _require_non_empty_str(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a non-empty string')
    return value


def _require_optional_str(value, name):
    if value is not None and not isinstance(value, str):
        raise ValueError(f'{name} must be a string or None')
    return value


def _require_metadata(value):
    """Validate JSON-compatible metadata recursively and return a deep copy.

    Allowed: None, bool, str, int, finite float, list, tuple, dict.
    Dict keys must be strings; tuples are normalized to lists; NaN/Infinity
    and any non-JSON object (set, bytes, class instances, ...) raise
    ValueError with the offending field path.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('metadata must be a dict')
    return _validate_json(copy.deepcopy(value), 'metadata')


def _validate_json(value, path):
    """Validate JSON-compatible data, returning a normalized copy.

    ``path`` is a human-readable field path used in error messages, e.g.
    ``metadata.engine.value`` or ``metadata.parts[2]``.
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f'{path} must be a finite number')
        return value
    if isinstance(value, (list, tuple)):
        return [
            _validate_json(item, f'{path}[{i}]')
            for i, item in enumerate(value)
        ]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f'{path} keys must be strings, got {key!r}')
            result[key] = _validate_json(item, f'{path}.{key}')
        return result
    raise ValueError(f'{path} is not JSON-compatible')


def _require_string_list(value, name):
    if not isinstance(value, (list, tuple)):
        raise ValueError(f'{name} must be a list')
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f'{name} entries must be strings, got {item!r}')
    return list(value)


def _coerce_enum(value, enum_cls, name):
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError:
            raise ValueError(
                f'unknown {name} {value!r}; allowed: {[e.value for e in enum_cls]}'
            )
    raise ValueError(f'{name} must be a {enum_cls.__name__} or lowercase string')


# =============================================================================
# Serialization — enums become plain lowercase strings; nested structures recurse.
# =============================================================================


def _convert(value, path=''):
    """Recursively convert contract objects / enums to JSON-safe plain data.

    Containers are walked here so nested contract objects (e.g. BurnResult
    inside MissionSimulationResult.burns) resolve correctly; scalar leaves are
    delegated to ``_validate_json``.  This is also the last line of defense:
    objects assigned to public fields after construction are re-validated
    here, so problems surface at ``to_dict()`` instead of a later
    ``json.dumps()``.
    """
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return {
            f.name: _convert(getattr(value, f.name), f'{path}.{f.name}' if path else f.name)
            for f in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f'{path} keys must be strings, got {key!r}')
            result[key] = _convert(item, f'{path}.{key}')
        return result
    if isinstance(value, (list, tuple)):
        return [_convert(item, f'{path}[{i}]') for i, item in enumerate(value)]
    return _validate_json(value, path)


def to_dict(value):
    """Convert any contract object (or nested structure) to a JSON-safe dict."""
    return _convert(value)


# =============================================================================
# BurnRequirement — task input only.
# =============================================================================


@dataclass
class BurnRequirement:
    """One burn a mission asks for.

    Contains only the *requirement*.  Whether the burn is a first ignition or
    a restart, which stage executes it, how much propellant it consumes and
    whether it succeeds are solver results and are deliberately NOT here.
    """
    burn_id: str
    order: int
    target_dv_m_s: float
    source: str
    name: str = ''
    preferred_stage_index: Optional[int] = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        self.burn_id = _require_non_empty_str(self.burn_id, 'burn_id')
        self.order = _require_int(self.order, 'order', minimum=1)
        self.target_dv_m_s = _require_number(self.target_dv_m_s, 'target_dv_m_s', minimum=0.0)
        self.source = _require_non_empty_str(self.source, 'source')
        if self.name is None:
            self.name = ''
        _require_optional_str(self.name, 'name')
        if self.preferred_stage_index is not None:
            self.preferred_stage_index = _require_int(
                self.preferred_stage_index, 'preferred_stage_index', minimum=0)
        self.metadata = _require_metadata(self.metadata)

    @classmethod
    def from_dict(cls, data):
        return _from_dict(cls, data)

    def to_dict(self):
        return _convert(self)


# =============================================================================
# StageState — a stage at a moment in time (solver state snapshot).
# =============================================================================


@dataclass
class StageState:
    """A single stage at a single moment of a mission.

    This phase defines the data structure only; no state advance is performed.
    ``multi_ignition`` is the hardware capability, ``ignition_count`` is how
    many times this stage has ignited so far — both are consulted by the
    solver to classify a burn as first vs. restart.
    """
    stage_index: int
    wet_mass_t: float
    dry_mass_t: float
    propellant_remaining_t: float
    isp_vac_s: Optional[float] = None
    multi_ignition: bool = False
    ignition_count: int = 0
    attached: bool = True
    parallel: bool = False
    name: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        self.stage_index = _require_int(self.stage_index, 'stage_index', minimum=0)
        self.wet_mass_t = _require_number(self.wet_mass_t, 'wet_mass_t', minimum=0.0)
        self.dry_mass_t = _require_number(self.dry_mass_t, 'dry_mass_t', minimum=0.0)
        if self.dry_mass_t > self.wet_mass_t:
            raise ValueError(
                f'dry_mass_t must be <= wet_mass_t, got dry={self.dry_mass_t} wet={self.wet_mass_t}')
        self.propellant_remaining_t = _require_number(
            self.propellant_remaining_t, 'propellant_remaining_t', minimum=0.0)
        capacity = self.wet_mass_t - self.dry_mass_t
        # Epsilon tolerance absorbs float noise (e.g. 19.467 - 0.899).
        if self.propellant_remaining_t > capacity + 1e-9:
            raise ValueError(
                'propellant_remaining_t exceeds stage propellant capacity '
                f'({self.propellant_remaining_t} > {capacity})')
        if self.isp_vac_s is None:
            pass  # None means data insufficient; accepted.
        else:
            self.isp_vac_s = _require_number(self.isp_vac_s, 'isp_vac_s', minimum=0.0)
            if self.isp_vac_s <= 0:
                raise ValueError(
                    f'isp_vac_s must be > 0 when provided, got {self.isp_vac_s}')
        if not isinstance(self.multi_ignition, bool):
            raise ValueError('multi_ignition must be a bool')
        self.ignition_count = _require_int(self.ignition_count, 'ignition_count', minimum=0)
        if not isinstance(self.attached, bool):
            raise ValueError('attached must be a bool')
        if not isinstance(self.parallel, bool):
            raise ValueError('parallel must be a bool')
        _require_optional_str(self.name, 'name')
        self.metadata = _require_metadata(self.metadata)
        limit = self.metadata.get('max_ignitions')
        if limit is not None:
            _require_int(limit, 'max_ignitions', minimum=1)

    @property
    def ignition_available(self):
        limit = self.metadata.get('max_ignitions')
        return (not (self.ignition_count > 0 and not self.multi_ignition)
                and (limit is None or self.ignition_count < limit))

    @classmethod
    def from_dict(cls, data):
        return _from_dict(cls, data)

    def to_dict(self):
        return _convert(self)


# =============================================================================
# BurnResult — what a single burn actually did (solver output).
# =============================================================================


@dataclass
class BurnResult:
    """Outcome of one burn.

    Fields left as None mean "not computed" (e.g. the burn was never
    assigned to a stage).  All numbers are optional but must be >= 0.
    ``ignition_kind`` distinguishes first ignition / restart / unassigned.
    """
    burn_id: str
    order: int
    target_dv_m_s: float
    ignition_kind: IgnitionKind
    success: bool
    failure_code: FailureCode
    failure_reason_zh: str
    data_quality: DataQuality
    actual_dv_m_s: Optional[float] = None
    stage_index: Optional[int] = None
    mass_before_t: Optional[float] = None
    mass_after_t: Optional[float] = None
    propellant_consumed_t: Optional[float] = None
    propellant_remaining_t: Optional[float] = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        self.burn_id = _require_non_empty_str(self.burn_id, 'burn_id')
        self.order = _require_int(self.order, 'order', minimum=1)
        self.target_dv_m_s = _require_number(self.target_dv_m_s, 'target_dv_m_s', minimum=0.0)
        self.ignition_kind = _coerce_enum(self.ignition_kind, IgnitionKind, 'ignition_kind')
        if not isinstance(self.success, bool):
            raise ValueError('success must be a bool')
        self.failure_code = _coerce_enum(self.failure_code, FailureCode, 'failure_code')
        self.data_quality = _coerce_enum(self.data_quality, DataQuality, 'data_quality')
        if self.failure_reason_zh is None:
            self.failure_reason_zh = ''
        if not isinstance(self.failure_reason_zh, str):
            raise ValueError('failure_reason_zh must be a string')
        if self.stage_index is not None:
            self.stage_index = _require_int(self.stage_index, 'stage_index', minimum=0)
        self.actual_dv_m_s = _require_number(
            self.actual_dv_m_s, 'actual_dv_m_s', minimum=0.0, allow_none=True)
        self.mass_before_t = _require_number(
            self.mass_before_t, 'mass_before_t', minimum=0.0, allow_none=True)
        self.mass_after_t = _require_number(
            self.mass_after_t, 'mass_after_t', minimum=0.0, allow_none=True)
        self.propellant_consumed_t = _require_number(
            self.propellant_consumed_t, 'propellant_consumed_t', minimum=0.0, allow_none=True)
        self.propellant_remaining_t = _require_number(
            self.propellant_remaining_t, 'propellant_remaining_t', minimum=0.0, allow_none=True)
        self.metadata = _require_metadata(self.metadata)
        self._check_consistency()

    def _check_consistency(self):
        if self.success and self.failure_code is not FailureCode.NONE:
            raise ValueError('success=True requires failure_code=FailureCode.NONE')
        if not self.success:
            if self.failure_code is FailureCode.NONE:
                raise ValueError('success=False requires failure_code != FailureCode.NONE')
            if not self.failure_reason_zh.strip():
                raise ValueError('success=False requires a non-empty failure_reason_zh')
        if self.ignition_kind is IgnitionKind.UNASSIGNED:
            if self.stage_index is not None:
                raise ValueError('ignition_kind=unassigned requires stage_index=None')
        elif self.stage_index is None:
            raise ValueError(
                f'ignition_kind={self.ignition_kind.value} requires stage_index')
        if (self.mass_before_t is not None and self.mass_after_t is not None
                and self.mass_after_t > self.mass_before_t):
            raise ValueError(
                'mass_after_t must not exceed mass_before_t '
                f'({self.mass_after_t} > {self.mass_before_t})')
        # Note: actual_dv_m_s may exceed target_dv_m_s — over-burn is legal at
        # the data-structure level and is not rejected.

    @classmethod
    def from_dict(cls, data):
        return _from_dict(cls, data)

    def to_dict(self):
        return _convert(self)


# =============================================================================
# MissionSimulationResult — full mission outcome (solver output).
# =============================================================================


@dataclass
class MissionSimulationResult:
    """Complete result of a staged, multi-ignition mission simulation."""
    rocket_ref: str
    payload_t: float
    success: bool
    failure_code: FailureCode
    failure_reason_zh: str
    data_quality: DataQuality
    burns: List[BurnResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        self.rocket_ref = _require_non_empty_str(self.rocket_ref, 'rocket_ref')
        self.payload_t = _require_number(self.payload_t, 'payload_t', minimum=0.0)
        if not isinstance(self.success, bool):
            raise ValueError('success must be a bool')
        self.failure_code = _coerce_enum(self.failure_code, FailureCode, 'failure_code')
        self.data_quality = _coerce_enum(self.data_quality, DataQuality, 'data_quality')
        if self.failure_reason_zh is None:
            self.failure_reason_zh = ''
        if not isinstance(self.failure_reason_zh, str):
            raise ValueError('failure_reason_zh must be a string')
        if self.success and self.failure_code is not FailureCode.NONE:
            raise ValueError('success=True requires failure_code=FailureCode.NONE')
        if not self.success:
            if self.failure_code is FailureCode.NONE:
                raise ValueError('success=False requires failure_code != FailureCode.NONE')
            if not self.failure_reason_zh.strip():
                raise ValueError('success=False requires a non-empty failure_reason_zh')
        # Note: "any failed burn implies mission failure" is solver aggregation
        # logic and is NOT enforced here. Caller-provided success/failure_code
        # are validated but never silently modified.
        burns = []
        for entry in self.burns:
            if isinstance(entry, BurnResult):
                burns.append(entry)
            elif isinstance(entry, dict):
                burns.append(BurnResult.from_dict(entry))
            else:
                raise ValueError('burns entries must be BurnResult or dict, '
                                 f'got {type(entry).__name__}')
        self.burns = burns
        self.warnings = _require_string_list(self.warnings, 'warnings')
        self.metadata = _require_metadata(self.metadata)

    @classmethod
    def from_dict(cls, data):
        return _from_dict(cls, data)

    def to_dict(self):
        return _convert(self)


# =============================================================================
# Shared dict input support — rejects unknown keys explicitly.
# =============================================================================


def _from_dict(cls, data):
    if not isinstance(data, dict):
        raise ValueError(f'{cls.__name__} input must be a dict')
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f'unknown fields for {cls.__name__}: {", ".join(unknown)}')
    return cls(**data)
