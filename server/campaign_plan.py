"""Build the standard six-flight capacity campaign from a profile (Phase C).

Pure planning: no KSP, database or file writes.  The caller may render this
spec to JSON and later hand it to the campaign executor.
"""
from __future__ import annotations

from .campaign import CampaignSpec


DEFAULT_PROGRAM = {'staging': 'user-provided', 'guidance': 'user-provided'}
DEFAULT_TOLERANCES = {'orbit_km': 5.0, 'inclination_deg': 0.2, 'hold_s': 30.0}


def _payload(design: float, fraction: float) -> float:
    return round(design * fraction, 6)


def build_six_flight_plan(profile: dict, design_payload_t: float, *,
                          sso: bool = False, high_energy: str = 'tli',
                          program: dict | None = None,
                          tolerances: dict | None = None) -> CampaignSpec:
    """Build the six anchors from the roadmap.

    Anchors: baseline design, baseline half payload, polar (or SSO), retrograde,
    GTO, and either TLI or Molniya.  Payloads are fractions of the declared
    design payload; they are starting values, not capacity claims.
    """
    if not isinstance(design_payload_t, (int, float)) or isinstance(design_payload_t, bool) or design_payload_t <= 0:
        raise ValueError('design_payload_t must be a positive number')
    if high_energy not in ('tli', 'molniya'):
        raise ValueError("high_energy must be 'tli' or 'molniya'")
    if not isinstance(profile, dict):
        raise ValueError('profile must be a mapping')
    profile_id = profile.get('profile_id')
    craft_sha = (profile.get('source') or {}).get('craft_sha256')
    if not profile_id or not craft_sha:
        raise ValueError('profile needs profile_id and source.craft_sha256')
    polar = ({'orbit_type': 'circular', 'periapsis_km': 600.0,
              'apoapsis_km': 600.0, 'inclination_deg': 97.8}
             if sso else
             {'orbit_type': 'circular', 'periapsis_km': 220.0,
              'apoapsis_km': 220.0, 'inclination_deg': 97.8})
    if high_energy == 'tli':
        high = {'orbit_type': 'high_energy', 'c3_km2_s2': -2.0,
                'declination_deg': 0.0}
        high_label = 'TLI C3=-2'
    else:
        high = {'orbit_type': 'elliptical', 'periapsis_km': 500.0,
                'apoapsis_km': 40000.0, 'inclination_deg': 63.4,
                'argument_of_periapsis_deg': 270.0}
        high_label = 'Molniya 500x40000'
    trials = [
        {'label': 'baseline design', 'payload_t': _payload(design_payload_t, 1.0),
         'orbit': {'orbit_type': 'circular', 'periapsis_km': 220.0,
                   'apoapsis_km': 220.0, 'inclination_deg': 20.0}},
        {'label': 'baseline half', 'payload_t': _payload(design_payload_t, 0.5),
         'orbit': {'orbit_type': 'circular', 'periapsis_km': 220.0,
                   'apoapsis_km': 220.0, 'inclination_deg': 20.0}},
        {'label': 'SSO' if sso else 'polar', 'payload_t': _payload(design_payload_t, 0.7),
         'orbit': polar},
        {'label': 'retrograde', 'payload_t': _payload(design_payload_t, 0.4),
         'orbit': {'orbit_type': 'circular', 'periapsis_km': 220.0,
                   'apoapsis_km': 220.0, 'inclination_deg': 180.0}},
        {'label': 'GTO', 'payload_t': _payload(design_payload_t, 0.5),
         'orbit': {'orbit_type': 'transfer', 'periapsis_km': 220.0,
                   'apoapsis_km': 35786.0, 'inclination_deg': 28.5}},
        {'label': high_label, 'payload_t': _payload(design_payload_t, 0.3),
         'orbit': high},
    ]
    return CampaignSpec(
        rocket_profile_id=str(profile_id),
        rocket_sha256=str(craft_sha),
        trials=trials,
        max_trials=6,
        program=dict(program or DEFAULT_PROGRAM),
        tolerances=dict(tolerances or DEFAULT_TOLERANCES),
        metadata={'design_payload_t': design_payload_t, 'sso': sso,
                  'high_energy': high_energy, 'plan': 'six_flight_standard'},
    )
