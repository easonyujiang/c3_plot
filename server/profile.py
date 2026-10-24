"""Versioned rocket-profile data contracts and builders (Phase A).

Pure Python: no Flask, SQLite, file writes or game access.  The import/preview
pipeline supplies a draft dict; this module turns it into a stable profile that
later simulation, calibration and encyclopedia layers can consume.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any


SCHEMA_VERSION = 1
CONFIG_CACHE = 'ModuleManager.ConfigCache'
MECHJEB = 'MechJeb2.dll'
VALIDATION = 'C3MassValidation.dll'


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _mod_versions(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    if not isinstance(raw, str):
        return {}
    versions = {}
    for item in raw.split('|'):
        if '=' in item:
            name, version = item.split('=', 1)
            if name.strip():
                versions[name.strip()] = version.strip()
    return versions


def _file_hash(files: list[dict], suffix: str) -> str | None:
    for record in files:
        path = str(record.get('path') or '').replace('\\', '/')
        if path.endswith(suffix):
            return record.get('sha256')
    return None


@dataclass
class ParameterEntry:
    key: str
    value: Any
    unit: str = ''
    source: str = 'unknown'
    source_sha256: str | None = None
    uncertainty: float | None = None
    identified_by: str | None = None
    confidence: str = 'unknown'
    note: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EnvironmentProfile:
    ksp_root: str | None = None
    game_version: str | None = None
    craft_version: str | None = None
    config_cache_sha256: str | None = None
    mechjeb_sha256: str | None = None
    validation_dll_sha256: str | None = None
    mod_versions: dict[str, str] = field(default_factory=dict)
    files: list[dict] = field(default_factory=list)
    craft_sha256: str | None = None
    snapshot_sha256: str | None = None
    fingerprint: str = ''

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EngineProfile:
    cfg: str
    count: int = 1
    part_id: str | None = None
    engine_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TankProfile:
    part_id: str | None = None
    module: str | None = None
    fields: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PartProfile:
    part_id: str
    name: str | None = None
    stage_index: int | None = None
    role: str = 'unknown'  # payload | fairing | stage | unknown
    dry_mass_t: float | None = None
    resource_mass_t: float | None = None
    wet_mass_t: float | None = None
    shielded: bool | None = None
    source: str = 'runtime_editor_snapshot'
    source_sha256: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StageProfile:
    index: int
    wet_mass_t: float | None = None
    dry_mass_t: float | None = None
    propellant_t: float | None = None
    thrust_kN: float | None = None
    isp_vac_s: float | None = None
    isp_sl_s: float | None = None
    multi_ignition: bool = False
    max_ignitions: int | None = None
    engines: list[EngineProfile] = field(default_factory=list)
    tanks: list[TankProfile] = field(default_factory=list)
    part_ids: list[str] = field(default_factory=list)
    source: str = 'unknown'
    source_sha256: str | None = None
    confidence: str = 'unknown'
    mass_scope: str = 'editor_grouping'
    payload_included: bool | None = None
    fairing_included: bool | None = None

    def to_dict(self) -> dict:
        return {
            'index': self.index,
            'wet_mass_t': self.wet_mass_t,
            'dry_mass_t': self.dry_mass_t,
            'propellant_t': self.propellant_t,
            'thrust_kN': self.thrust_kN,
            'isp_vac_s': self.isp_vac_s,
            'isp_sl_s': self.isp_sl_s,
            'multi_ignition': self.multi_ignition,
            'max_ignitions': self.max_ignitions,
            'engines': [engine.to_dict() for engine in self.engines],
            'tanks': [tank.to_dict() for tank in self.tanks],
            'part_ids': list(self.part_ids),
            'source': self.source,
            'source_sha256': self.source_sha256,
            'confidence': self.confidence,
            'mass_scope': self.mass_scope,
            'payload_included': self.payload_included,
            'fairing_included': self.fairing_included,
        }


@dataclass
class CapacityPoint:
    orbit: dict
    payload_t: float | None = None
    status: str = 'unknown'  # verified | estimated | low_confidence | failed | infeasible
    estimate_t: float | None = None
    lower_t: float | None = None
    upper_t: float | None = None
    confidence: str = 'unknown'
    uncertainty_t: float | None = None
    evidence: list[str] = field(default_factory=list)
    note: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrialSpec:
    rocket_sha256: str
    payload_t: float
    orbit: dict
    launch: dict = field(default_factory=dict)
    program: dict = field(default_factory=dict)
    tolerances: dict = field(default_factory=dict)
    budget: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrialResult:
    spec_id: str
    status: str
    final_orbit: dict = field(default_factory=dict)
    remaining_dv_m_s: float | None = None
    failure_code: str | None = None
    telemetry_path: str | None = None
    evidence: list[dict] = field(default_factory=list)
    note: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Encyclopedia:
    profile: dict
    capacity: list[CapacityPoint] = field(default_factory=list)
    trials: list[TrialResult] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    generated_by: str = 'c3-mass'

    def to_dict(self) -> dict:
        return {
            'generated_by': self.generated_by,
            'profile': self.profile,
            'capacity': [point.to_dict() for point in self.capacity],
            'trials': [trial.to_dict() for trial in self.trials],
            'gaps': list(self.gaps),
        }


@dataclass
class RocketProfile:
    schema_version: int
    profile_id: str
    name: str
    source: dict
    environment: EnvironmentProfile
    stages: list[StageProfile]
    parameters: list[ParameterEntry]
    parts: list[PartProfile] = field(default_factory=list)
    payload: dict = field(default_factory=dict)
    fairings: dict = field(default_factory=dict)
    automatic_identification: dict = field(default_factory=dict)
    mass_reconciliation: dict | None = None
    warnings: list[dict] = field(default_factory=list)
    calibration: list[dict] = field(default_factory=list)
    capacity: list[CapacityPoint] = field(default_factory=list)
    trials: list[TrialResult] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'schema_version': self.schema_version,
            'profile_id': self.profile_id,
            'name': self.name,
            'source': self.source,
            'environment': self.environment.to_dict(),
            'stages': [stage.to_dict() for stage in self.stages],
            'parameters': [entry.to_dict() for entry in self.parameters],
            'parts': [part.to_dict() for part in self.parts],
            'payload': self.payload,
            'fairings': self.fairings,
            'automatic_identification': self.automatic_identification,
            'mass_reconciliation': self.mass_reconciliation,
            'warnings': self.warnings,
            'calibration': self.calibration,
            'capacity': [point.to_dict() for point in self.capacity],
            'trials': [trial.to_dict() for trial in self.trials],
            'provenance': self.provenance,
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


def build_environment_profile(draft: dict) -> EnvironmentProfile:
    identification = draft.get('automatic_identification') or {}
    source = identification.get('source') or {}
    files = list(source.get('files') or [])
    if not files:
        provenance = draft.get('ksp_runtime_provenance') or {}
        if provenance.get('config_cache_path'):
            files.append({'path': 'GameData/ModuleManager.ConfigCache',
                          'sha256': provenance.get('config_cache_sha256')})
        if provenance.get('realchute_dll_path'):
            files.append({'path': 'GameData/RealChute/Plugins/RealChute.dll',
                          'sha256': provenance.get('realchute_dll_sha256')})
    root_info = (draft.get('source') or {}).get('root_info') or {}
    environment = EnvironmentProfile(
        ksp_root=source.get('ksp_root'),
        game_version=root_info.get('version'),
        craft_version=root_info.get('version'),
        config_cache_sha256=_file_hash(files, CONFIG_CACHE),
        mechjeb_sha256=_file_hash(files, MECHJEB),
        validation_dll_sha256=_file_hash(files, VALIDATION),
        mod_versions=_mod_versions(root_info.get('mod_versions')),
        files=files,
        craft_sha256=source.get('craft_sha256'),
        snapshot_sha256=source.get('snapshot_sha256'),
    )
    environment.fingerprint = _stable_hash({
        'ksp_root': environment.ksp_root,
        'game_version': environment.game_version,
        'mod_versions': environment.mod_versions,
        'files': sorted(files, key=lambda record: str(record.get('path'))),
    })
    return environment


def _stage_performance(stage: dict) -> dict:
    performance = stage.get('runtime_performance') or {}
    return {
        'thrust_kN': _number(performance.get('thrust')),
        'isp_vac_s': _number(performance.get('isp_vac')),
        'isp_sl_s': _number(performance.get('isp_sl')),
    }


def _stage_engines(stage: dict) -> list[EngineProfile]:
    groups = stage.get('engine_groups')
    if groups is None:
        groups = [stage.get('engines')]
    elif isinstance(groups, dict):
        groups = [groups]
    engines = []
    for group in groups or []:
        if not isinstance(group, dict) or not group.get('cfg'):
            continue
        engines.append(EngineProfile(
            cfg=str(group['cfg']),
            count=int(group.get('count', 1) or 1),
            part_id=group.get('part_id'),
            engine_id=group.get('engine_id'),
        ))
    return engines


def build_stage_profiles(draft: dict, environment: EnvironmentProfile) -> list[StageProfile]:
    stages = []
    for index, raw in enumerate(draft.get('stages') or []):
        if not isinstance(raw, dict):
            continue
        runtime = raw.get('runtime_source') or {}
        source = 'runtime_editor_snapshot' if runtime else 'draft'
        source_sha = (_file_hash(list(runtime.get('files') or []), CONFIG_CACHE)
                      if runtime else environment.craft_sha256)
        wet = _number(raw.get('wet_mass_t'))
        dry = _number(raw.get('dry_mass_t'))
        propellant = wet - dry if wet is not None and dry is not None else None
        performance = _stage_performance(raw)
        tanks = []
        for tank in raw.get('tank_states') or []:
            if not isinstance(tank, dict):
                continue
            tanks.append(TankProfile(
                part_id=tank.get('part_id'),
                module=tank.get('module'),
                fields={str(k): str(v) for k, v in (tank.get('fields') or {}).items()},
            ))
        stages.append(StageProfile(
            index=index,
            wet_mass_t=wet,
            dry_mass_t=dry,
            propellant_t=propellant,
            thrust_kN=performance['thrust_kN'],
            isp_vac_s=performance['isp_vac_s'],
            isp_sl_s=performance['isp_sl_s'],
            multi_ignition=bool(raw.get('multi_ignition')),
            max_ignitions=raw.get('max_ignitions'),
            engines=_stage_engines(raw),
            tanks=tanks,
            part_ids=list(raw.get('part_ids') or []),
            source=source,
            source_sha256=source_sha,
            confidence='exact' if runtime else ('estimated' if wet is not None else 'unknown'),
        ))
    return stages


def build_part_profiles(draft: dict, stages: list[StageProfile],
                        environment: EnvironmentProfile) -> list[PartProfile]:
    stage_by_part = {part_id: stage.index for stage in stages for part_id in stage.part_ids}
    payload_ids = {str(part_id) for part_id in (draft.get('payload_part_ids') or [])}
    parts = []
    for raw in draft.get('editor_parts') or []:
        if not isinstance(raw, dict) or not raw.get('part_id'):
            continue
        part_id = str(raw['part_id'])
        role = str(raw.get('role') or 'unknown')
        if part_id in payload_ids:
            role = 'payload'
        parts.append(PartProfile(
            part_id=part_id,
            name=raw.get('name'),
            stage_index=raw.get('stage_index', stage_by_part.get(part_id)),
            role=role,
            dry_mass_t=_number(raw.get('dry_mass_t')),
            resource_mass_t=_number(raw.get('resource_mass_t')),
            wet_mass_t=_number(raw.get('wet_mass_t')),
            shielded=raw.get('shielded'),
            source='runtime_editor_snapshot',
            source_sha256=environment.config_cache_sha256,
        ))
    return parts


def _sum_parts(parts: list[PartProfile], field: str) -> float | None:
    values = [getattr(part, field) for part in parts if getattr(part, field) is not None]
    return sum(values) if values else None


def build_parameter_catalog(stages: list[StageProfile],
                            environment: EnvironmentProfile,
                            parts: list[PartProfile] | None = None) -> list[ParameterEntry]:
    entries = []
    for stage in stages:
        prefix = f'stage[{stage.index}]'
        for key, value, unit in (
            ('wet_mass_t', stage.wet_mass_t, 't'),
            ('dry_mass_t', stage.dry_mass_t, 't'),
            ('propellant_t', stage.propellant_t, 't'),
            ('thrust_kN', stage.thrust_kN, 'kN'),
            ('isp_vac_s', stage.isp_vac_s, 's'),
            ('isp_sl_s', stage.isp_sl_s, 's'),
        ):
            entries.append(ParameterEntry(
                key=f'{prefix}.{key}', value=value, unit=unit,
                source=stage.source, source_sha256=stage.source_sha256,
                uncertainty=0.0 if stage.confidence == 'exact' else None,
                identified_by='editor_snapshot' if stage.source == 'runtime_editor_snapshot' else None,
                confidence=stage.confidence,
            ))
        for engine_index, engine in enumerate(stage.engines):
            entries.append(ParameterEntry(
                key=f'{prefix}.engine[{engine_index}].cfg', value=engine.cfg,
                source=stage.source, source_sha256=stage.source_sha256,
                confidence=stage.confidence,
            ))
            entries.append(ParameterEntry(
                key=f'{prefix}.engine[{engine_index}].count', value=engine.count,
                unit='count', source=stage.source, source_sha256=stage.source_sha256,
                confidence=stage.confidence,
            ))
        for tank_index, tank in enumerate(stage.tanks):
            for field, value in tank.fields.items():
                numeric = _number(value)
                entries.append(ParameterEntry(
                    key=f'{prefix}.tank[{tank_index}].{field}',
                    value=numeric if numeric is not None else value,
                    source=stage.source, source_sha256=stage.source_sha256,
                    uncertainty=0.0 if stage.confidence == 'exact' else None,
                    identified_by='editor_snapshot' if stage.source == 'runtime_editor_snapshot' else None,
                    confidence=stage.confidence,
                ))
    for part in parts or []:
        prefix = f'part[{part.part_id}]'
        for key, value, unit in (
            ('dry_mass_t', part.dry_mass_t, 't'),
            ('resource_mass_t', part.resource_mass_t, 't'),
            ('wet_mass_t', part.wet_mass_t, 't'),
        ):
            entries.append(ParameterEntry(
                key=f'{prefix}.{key}', value=value, unit=unit,
                source=part.source, source_sha256=part.source_sha256,
                uncertainty=0.0 if value is not None else None,
                identified_by='editor_snapshot', confidence='exact' if value is not None else 'unknown',
            ))
    for key, value, source in (
        ('environment.config_cache_sha256', environment.config_cache_sha256, 'game_data'),
        ('environment.mechjeb_sha256', environment.mechjeb_sha256, 'game_data'),
        ('environment.validation_dll_sha256', environment.validation_dll_sha256, 'game_data'),
        ('environment.game_version', environment.game_version, 'craft'),
        ('environment.craft_sha256', environment.craft_sha256, 'craft'),
        ('environment.snapshot_sha256', environment.snapshot_sha256, 'runtime_editor_snapshot'),
    ):
        entries.append(ParameterEntry(
            key=key, value=value, source=source,
            source_sha256=environment.config_cache_sha256 if source == 'game_data' else environment.craft_sha256,
            confidence='exact' if value not in (None, '') else 'unknown',
        ))
    if environment.mod_versions:
        entries.append(ParameterEntry(
            key='environment.mod_versions',
            value=environment.mod_versions, source='craft',
            source_sha256=environment.craft_sha256, confidence='exact',
        ))
    return entries


def build_rocket_profile(draft: dict) -> RocketProfile:
    environment = build_environment_profile(draft)
    stages = build_stage_profiles(draft, environment)
    parts = build_part_profiles(draft, stages, environment)
    parameters = build_parameter_catalog(stages, environment, parts)
    payload_ids = {str(part_id) for part_id in (draft.get('payload_part_ids') or [])}
    payload_parts = [part for part in parts if part.role == 'payload']
    fairing_parts = [part for part in parts if part.role == 'fairing']
    for stage in stages:
        if payload_ids:
            stage.payload_included = any(part.part_id in stage.part_ids and part.role == 'payload'
                                         for part in parts)
        else:
            stage.payload_included = None
        stage.fairing_included = any(part.part_id in stage.part_ids and part.role == 'fairing'
                                     for part in parts)
    payload = {
        'status': 'declared' if payload_ids else 'unknown',
        'parts': [part.to_dict() for part in payload_parts],
        'wet_mass_t': _sum_parts(payload_parts, 'wet_mass_t'),
        'dry_mass_t': _sum_parts(payload_parts, 'dry_mass_t'),
        'resource_mass_t': _sum_parts(payload_parts, 'resource_mass_t'),
        'source': 'payload_part_ids' if payload_ids else 'not_declared',
    }
    fairings = {
        'parts': [part.to_dict() for part in fairing_parts],
        'wet_mass_t': _sum_parts(fairing_parts, 'wet_mass_t'),
        'state': 'present' if fairing_parts else 'none',
    }
    source = draft.get('source') or {}
    identification = draft.get('automatic_identification') or {}
    source_info = {
        'filename': source.get('filename'),
        'format': source.get('format'),
        'part_count': source.get('part_count') or len(stages),
        'craft_sha256': environment.craft_sha256,
        'snapshot_sha256': environment.snapshot_sha256,
    }
    warnings = list(draft.get('structured_warnings') or [])
    if not warnings:
        warnings = [{'code': 'legacy_warning', 'severity': 'warning', 'message': str(item)}
                    for item in (draft.get('warnings') or [])]
    identification_summary = {
        'status': identification.get('status'),
        'message': identification.get('message'),
        'topology': identification.get('topology'),
        'editor_dry_mass_t': identification.get('editor_dry_mass_t'),
        'editor_resource_mass_t': identification.get('editor_resource_mass_t'),
    }
    profile_id = _stable_hash({
        'schema': SCHEMA_VERSION,
        'craft_sha256': environment.craft_sha256,
        'environment': environment.fingerprint,
    })
    return RocketProfile(
        schema_version=SCHEMA_VERSION,
        profile_id=profile_id,
        name=(draft.get('rocket') or {}).get('name') or source.get('filename') or 'unnamed',
        source=source_info,
        environment=environment,
        stages=stages,
        parameters=parameters,
        parts=parts,
        payload=payload,
        fairings=fairings,
        automatic_identification=identification_summary,
        mass_reconciliation=draft.get('mass_reconciliation'),
        warnings=warnings,
        calibration=list(draft.get('calibration') or []),
        capacity=[],
        trials=[],
        provenance=list(environment.files),
    )
