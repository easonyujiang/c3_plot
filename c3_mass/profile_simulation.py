"""Profile -> T0/T1 ascent adapter (Phase B), pure Python.

Turns a RocketProfile dict (as produced by tools.profile) into StageSpec inputs,
subtracting declared payload mass from the editor-grouped stages, then runs the
T0 rocket equation and T1 ascent skeleton.  It never reads KSP or the database.
"""
from __future__ import annotations

from dataclasses import replace

from .ascent_simulation import (
    AscentSettings,
    PitchProgram,
    Planet,
    StageSpec,
    simulate_ascent,
    total_ideal_dv,
)


def _number(value, name):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{name} must be numeric')
    return result


def stage_specs_from_profile(profile: dict) -> dict:
    """Return StageSpec list, payload mass and warnings from a profile dict."""
    payload = profile.get('payload') or {}
    payload_status = payload.get('status')
    payload_t = 0.0
    payload_dry = 0.0
    warnings = []
    if payload_status == 'declared':
        payload_t = _number(payload.get('wet_mass_t') or 0.0, 'payload.wet_mass_t')
        payload_dry = _number(payload.get('dry_mass_t') or 0.0, 'payload.dry_mass_t')
    else:
        warnings.append('payload is not declared; using 0 t (declare payload parts first)')
    fairing_wet = (profile.get('fairings') or {}).get('wet_mass_t') or 0.0
    if float(fairing_wet) > 0:
        warnings.append('fairing mass remains in the stage; T1 does not model fairing jettison')
    stages = []
    for index, raw in enumerate(profile.get('stages') or []):
        wet = _number(raw.get('wet_mass_t'), f'stage[{index}].wet_mass_t')
        dry = _number(raw.get('dry_mass_t'), f'stage[{index}].dry_mass_t')
        if raw.get('payload_included') is True:
            wet -= payload_t
            dry -= payload_dry
        elif payload_status == 'declared' and raw.get('payload_included') is None:
            warnings.append(f'stage[{index}] payload inclusion is unknown; not subtracting payload')
        if wet <= 0 or dry <= 0 or dry >= wet:
            raise ValueError(f'stage[{index}] has invalid net masses after removing payload')
        propellant = wet - dry
        thrust = _number(raw.get('thrust_kN'), f'stage[{index}].thrust_kN')
        isp = raw.get('isp_vac_s')
        if isp in (None, ''):
            isp = raw.get('isp_sl_s')
            warnings.append(f'stage[{index}] has no vacuum Isp; using sea-level Isp')
        isp = _number(isp, f'stage[{index}].isp_vac_s')
        if thrust <= 0 or isp <= 0:
            raise ValueError(f'stage[{index}] has non-positive thrust or Isp')
        stages.append(StageSpec(dry_mass_t=dry, propellant_t=propellant,
                                thrust_kN=thrust, isp_s=isp))
    if not stages:
        raise ValueError('profile has no stages')
    return {'stages': stages, 'payload_t': payload_t, 'warnings': warnings}


def simulate_profile(profile: dict, *, planet: Planet | None = None,
                     pitch: PitchProgram | None = None,
                     settings: AscentSettings | None = None,
                     area_m2: float = 0.0, drag_coefficient: float = 0.0) -> dict:
    """Run T0/T1 from a profile dict; returns a JSON-serializable result."""
    parsed = stage_specs_from_profile(profile)
    planet = planet or Planet()
    pitch = pitch or PitchProgram()
    settings = settings or AscentSettings()
    stages = [replace(stage, area_m2=area_m2, drag_coefficient=drag_coefficient)
              for stage in parsed['stages']]
    ideal = total_ideal_dv(stages, parsed['payload_t'])
    result = simulate_ascent(stages, parsed['payload_t'], planet=planet,
                             pitch=pitch, settings=settings)
    warnings = list(parsed['warnings'])
    if area_m2 <= 0 or drag_coefficient <= 0:
        warnings.append('drag is zero; supply --area and --drag-coefficient for an aero estimate')
    return {
        'profile_id': profile.get('profile_id'),
        'name': profile.get('name'),
        'payload_t': parsed['payload_t'],
        'ideal_dv_m_s': ideal,
        'stages': [{'dry_mass_t': stage.dry_mass_t,
                    'propellant_t': stage.propellant_t,
                    'thrust_kN': stage.thrust_kN,
                    'isp_s': stage.isp_s} for stage in stages],
        'simulation': result.to_dict(),
        'warnings': warnings,
    }
