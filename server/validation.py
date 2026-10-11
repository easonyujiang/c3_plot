"""
Rocket config validation.
"""
import math
import os
from numbers import Real
from .config import ENGINES_DIR


def _is_finite_number(value):
    """Return true for finite real numbers, excluding bool."""
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _validate_number(errors, value, field, *, minimum=None, strictly_positive=False):
    if not _is_finite_number(value):
        errors.append(f"{field} must be a finite number")
        return False
    if strictly_positive and value <= 0:
        errors.append(f"{field} must be > 0")
        return False
    if minimum is not None and value < minimum:
        errors.append(f"{field} must be >= {minimum}")
        return False
    return True


def validate_rocket(cfg, *, engines_dir=None):
    """
    Validate a rocket config dict. Returns (is_valid, errors_list).

    The config dict has the same shape as the YAML format:
        { rocket: {name}, stages: [...], calibration: [...], reference_missions: {...} }
    """
    engines_dir = ENGINES_DIR if engines_dir is None else engines_dir
    errors = []
    if not isinstance(cfg, dict):
        return False, ["Rocket configuration must be a mapping"]

    rocket = cfg.get('rocket')
    if not isinstance(rocket, dict) or 'name' not in rocket:
        errors.append("Missing rocket.name")
    elif not isinstance(rocket['name'], str):
        errors.append("Rocket name must be a string")
    elif not rocket['name'].strip():
        errors.append("Rocket name cannot be empty")
    elif len(rocket['name'].strip()) > 200:
        errors.append("Rocket name must be 200 characters or fewer")

    stages = cfg.get('stages')
    if not isinstance(stages, list) or not stages:
        errors.append("Need at least 1 stage")
    else:
        for i, s in enumerate(stages):
            if not isinstance(s, dict):
                errors.append(f"Stage {i}: must be a mapping")
                continue
            for k in ['wet_mass_t', 'dry_mass_t']:
                if k not in s:
                    errors.append(f"Stage {i}: missing {k}")
                else:
                    _validate_number(errors, s[k], f"Stage {i}: {k}", strictly_positive=True)
            if (_is_finite_number(s.get('wet_mass_t')) and _is_finite_number(s.get('dry_mass_t'))
                    and s['dry_mass_t'] >= s['wet_mass_t']):
                errors.append(f"Stage {i}: dry_mass_t must be < wet_mass_t")
            engine_groups = s.get('engine_groups')
            if engine_groups is None:
                engine_groups = [s.get('engines')]
            elif isinstance(engine_groups, dict):
                engine_groups = [engine_groups]
            if not isinstance(engine_groups, list) or not engine_groups:
                errors.append(f"Stage {i}: missing engine groups")
            else:
                for group_index, eng in enumerate(engine_groups):
                    prefix = f"Stage {i} engine {group_index}"
                    if not isinstance(eng, dict):
                        errors.append(f"{prefix}: must be a mapping")
                        continue
                    cfg_name = eng.get('cfg')
                    if not isinstance(cfg_name, str) or not cfg_name:
                        errors.append(f"{prefix}: missing cfg")
                    elif (os.path.basename(cfg_name) != cfg_name or not cfg_name.endswith('.cfg')
                          or ('runtime_performance' not in s and not os.path.isfile(os.path.join(engines_dir, cfg_name)))):
                        errors.append(f"{prefix}: engine cfg '{cfg_name}' not found in engines/")
                    if 'count' in eng and (not isinstance(eng['count'], int)
                                           or isinstance(eng['count'], bool) or eng['count'] < 1):
                        errors.append(f"{prefix}: count must be a positive integer")
            for flag in ('multi_ignition', 'parallel'):
                if flag in s and not isinstance(s[flag], bool):
                    errors.append(f"Stage {i}: {flag} must be boolean")
            limit = s.get('max_ignitions')
            if limit is not None and (isinstance(limit,bool) or not isinstance(limit,int) or limit<1):
                errors.append(f'Stage {i}: max_ignitions must be a positive integer or null')
            if 'runtime_performance' in s:
                try:
                    from .editor_snapshot import checked_runtime_performance
                    checked_runtime_performance(s)
                except (KeyError, ValueError, OSError) as exc:
                    errors.append(f'Stage {i}: runtime engine evidence invalid: {exc}')
            if s.get('parallel') and i == len(stages) - 1:
                errors.append(f"Stage {i}: parallel stage cannot be the last stage")

    calibration = cfg.get('calibration')
    if not isinstance(calibration, list) or len(calibration) < 3:
        errors.append("Need at least 3 calibration points")
    else:
        payloads = set()
        for i, c in enumerate(calibration):
            if not isinstance(c, dict):
                errors.append(f"Calibration {i}: must be a mapping")
                continue
            for k in ['payload_t', 'orbit_alt_km', 'dv_rem_m_s']:
                if k not in c:
                    errors.append(f"Calibration {i}: missing {k}")
            if 'payload_t' in c and _validate_number(errors, c['payload_t'], f"Calibration {i}: payload_t", strictly_positive=True):
                payloads.add(c['payload_t'])
            if 'orbit_alt_km' in c:
                _validate_number(errors, c['orbit_alt_km'], f"Calibration {i}: orbit_alt_km", strictly_positive=True)
            if 'dv_rem_m_s' in c:
                _validate_number(errors, c['dv_rem_m_s'], f"Calibration {i}: dv_rem_m_s", minimum=0)
        if len(payloads) < 3:
            errors.append("Calibration requires at least 3 distinct payload_t values")

    missions = cfg.get('reference_missions', {})
    if not isinstance(missions, dict):
        errors.append("reference_missions must be a dict")
    else:
        for key, m in missions.items():
            if not isinstance(m, dict):
                errors.append(f"Mission {key}: must be a dict")
                continue
            if not isinstance(key, str) or not key:
                errors.append("Mission key must be a non-empty string")
            if not isinstance(m.get('label'), str) or not m['label'].strip():
                errors.append(f"Mission {key}: missing label")
            has_c3 = m.get('c3') is not None
            has_apo = m.get('apoapsis_km') is not None
            if not has_c3 and not has_apo:
                errors.append(f"Mission {key}: must specify c3 or apoapsis_km")
            if has_c3:
                _validate_number(errors, m['c3'], f"Mission {key}: c3")
            if has_apo:
                _validate_number(errors, m['apoapsis_km'], f"Mission {key}: apoapsis_km", strictly_positive=True)
            if m.get('color') and not isinstance(m['color'], str):
                errors.append(f"Mission {key}: color must be a string")

    return len(errors) == 0, errors
