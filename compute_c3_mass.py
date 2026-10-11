"""
C3-Mass Performance Computation Engine
=======================================
Pure computation — no plotting. Outputs data structures consumed by the web frontend.

Fixed: RSS Earth, 19.81°N 92.7m, 220km parking orbit.
Engine parameters read from engines/*.cfg via engine_parser.
"""

import sys, os, json, yaml
import numpy as np
from engine_parser import extract_engine_params

# =============================================================================
# Fixed parameters (RSS Earth, Cape Canaveral equivalent)
# =============================================================================
MU       = 3.986004418e+14
R_EARTH  = 6371000.0
G0       = 9.80665
SID_DAY  = 86164.1
LAT      = 19.81
ALT      = 92.7
H_PARK   = 220.0

earth_rot = 2 * np.pi * (R_EARTH + ALT) / SID_DAY * np.cos(np.radians(LAT))
r_park    = R_EARTH + H_PARK * 1000
v_orbit   = np.sqrt(MU / r_park)
v_esc     = np.sqrt(2 * MU / r_park)

# =============================================================================
# Core functions
# =============================================================================

def ideal_dv_to_orbit(h_km):
    """Ideal delta-v to reach a circular orbit at altitude h_km."""
    return np.sqrt(MU / (R_EARTH + h_km * 1000)) - earth_rot


def total_vac_dv(m_pl, stages):
    """Rocket equation: total vacuum delta-v for given payload mass (tons)."""
    dv = 0.0
    mass_above = m_pl
    for s in reversed(stages):
        wet = s['wet'] + mass_above
        dry = s['dry'] + mass_above
        dv += s['isp_vac'] * G0 * np.log(wet / dry)
        mass_above += s['wet']
    return dv


def _stage_engine_groups(stage):
    """Return normalized engine groups while keeping legacy configs supported."""
    groups = stage.get('engine_groups')
    if groups is None:
        groups = [stage.get('engines', {})]
    elif isinstance(groups, dict):
        groups = [groups]
    return [g for g in groups if isinstance(g, dict) and g.get('cfg')]


def resolve_stage_engine_performance(stage):
    """Combine multiple engine models into one effective stage performance.

    Public entry point reused by the mission-simulation input adapters
    (mission_adapters.py).  Returns the same structure as before:
    isp_vac / isp_sl / thrust / isp_type.
    """
    if 'runtime_performance' in stage:
        from server.editor_snapshot import checked_runtime_performance
        return checked_runtime_performance(stage)
    groups = _stage_engine_groups(stage)
    if not groups:
        raise ValueError('stage has no engine groups')
    resolved = []
    for group in groups:
        ep = extract_engine_params(group['cfg'])
        count = int(group.get('count', 1) or 1)
        thrust = float(ep['thrust_kN']) * count
        if thrust <= 0 or float(ep['isp_vac_s']) <= 0:
            continue
        resolved.append((ep, thrust))
    if not resolved:
        raise ValueError('stage engine groups have no usable thrust/Isp')
    thrust_total = sum(thrust for _, thrust in resolved)
    mass_flow_vac = sum(thrust / float(ep['isp_vac_s']) for ep, thrust in resolved)
    mass_flow_sl = sum(
        thrust / float(ep['isp_sl_s'] or ep['isp_vac_s'])
        for ep, thrust in resolved
    )
    isp_vac = thrust_total / mass_flow_vac
    isp_sl = thrust_total / mass_flow_sl
    primary = resolved[0][0]
    return dict(
        isp_vac=isp_vac,
        isp_sl=isp_sl,
        thrust=thrust_total,
        isp_type=primary.get('isp_type', 'unknown'),
    )


def _resolve_stage_engine_performance(stage):
    """Backward-compatible alias for :func:`resolve_stage_engine_performance`."""
    return resolve_stage_engine_performance(stage)


def compute_rocket(config, *, engine_resolver=None):
    """
    Compute C3-Mass performance curve for a rocket config.

    Args:
        config: dict with keys 'stages', 'calibration', 'reference_missions'

    Returns:
        dict with C3-Mass curve, dv-Mass curve, mission payloads, loss model,
        parking orbit params, and stage summaries.
    """
    STAGES_RAW = config['stages']
    CALIB      = config['calibration']
    MISSIONS   = config.get('reference_missions', {})

    # Resolve engine parameters from .cfg files
    # Handle boosters: parallel stages fire with the stage below, separate earlier
    raw_stages = []
    engine_resolver = resolve_stage_engine_performance if engine_resolver is None else engine_resolver
    for s in STAGES_RAW:
        performance = engine_resolver(s)
        raw_stages.append(dict(
            wet=s['wet_mass_t'],
            dry=s['dry_mass_t'],
            isp_vac=performance['isp_vac'],
            isp_sl=performance['isp_sl'],
            thrust=performance['thrust'],
            isp_type=performance['isp_type'],
            multi=s.get('multi_ignition', False),
            parallel=s.get('parallel', False),
        ))

    # Build final stage list, handling parallel boosters
    stages = []
    i = 0
    while i < len(raw_stages):
        s = raw_stages[i]
        if s['parallel'] and i + 1 < len(raw_stages):
            core = raw_stages[i + 1]

            # --- Phase 1: booster + core fire together ---
            # Booster burn time: how long until booster runs out of fuel
            bfr  = s['thrust'] / (s['isp_vac'] * G0) if s['isp_vac'] > 0 else 0  # booster mass flow rate (t/s)
            b_prop = s['wet'] - s['dry']  # booster propellant mass (t)
            b_time = b_prop / bfr if bfr > 0 else 0  # booster burn time (s)

            # Core also burns during booster phase
            cfr  = core['thrust'] / (core['isp_vac'] * G0) if core['isp_vac'] > 0 else 0  # core mass flow rate
            c_used = cfr * b_time  # core propellant consumed during booster phase
            if core['parallel'] or c_used >= core['wet'] - core['dry']:
                raise ValueError('Parallel model requires one booster group whose burnout precedes core burnout')

            # Thrust-weighted Isp during parallel burn
            tw = s['thrust'] + core['thrust']
            if tw > 0:
                comb_isp = tw / (s['thrust'] / s['isp_vac'] + core['thrust'] / core['isp_vac'])
            else:
                comb_isp = s['isp_vac']

            # Phase 1: combined burn
            # The following virtual stage already carries the remaining core.
            # This stage owns only the booster and core fuel consumed here;
            # total_vac_dv adds the remaining core as mass_above exactly once.
            phase1_wet = s['wet'] + c_used
            phase1_dry = s['dry']

            # Phase 2: core alone after booster separation
            phase2_wet = core['wet'] - c_used

            stages.append(dict(
                wet=phase1_wet,
                dry=phase1_dry,
                isp_vac=comb_isp,
                isp_sl=round(s['isp_sl'], 1),
                thrust=round(tw, 1),
                isp_type='booster',
                multi=False,
                parallel=False,
            ))
            stages.append(dict(
                wet=phase2_wet,
                dry=core['dry'],
                isp_vac=core['isp_vac'],
                isp_sl=core['isp_sl'],
                thrust=core['thrust'],
                isp_type='core',
                multi=core.get('multi', False),
                parallel=False,
            ))
            i += 2
        else:
            stages.append(dict(
                wet=s['wet'], dry=s['dry'],
                isp_vac=s['isp_vac'], isp_sl=s['isp_sl'],
                thrust=s['thrust'], isp_type=s.get('isp_type', ''),
                multi=s.get('multi', False), parallel=False,
            ))
            i += 1

    # ---- Calibration: Quadratic loss model ----
    m_pts, loss_pts = [], []
    ideal_park = ideal_dv_to_orbit(H_PARK)
    for pt in CALIB:
        m = pt['payload_t']
        h = pt.get('orbit_alt_km', H_PARK)
        dv_rem_val = pt['dv_rem_m_s']
        loss = total_vac_dv(m, stages) - dv_rem_val - ideal_dv_to_orbit(h)
        m_pts.append(m)
        loss_pts.append(loss)

    coeffs = np.polyfit(m_pts, loss_pts, 2)
    L0, L1, L2 = float(coeffs[2]), float(coeffs[1]), float(coeffs[0])

    def loss(m):
        # Clamp loss to non-negative: losses are physically positive and a
        # negative quadratic extrapolation would produce absurd payloads.
        return max(0.0, L0 + L1 * m + L2 * m * m)

    def dv_rem(m, h_km=H_PARK):
        return total_vac_dv(m, stages) - ideal_dv_to_orbit(h_km) - loss(m)

    # ---- C3 / dv conversion ----
    def c3_to_dv(c3, h_km=H_PARK):
        r = R_EARTH + h_km * 1000
        v_esc_sq = 2 * MU / r
        c3_mks = c3 * 1e6
        if c3_mks <= -v_esc_sq:
            return 0.0
        return np.sqrt(v_esc_sq + c3_mks) - np.sqrt(MU / r)

    def dv_to_c3(dv_val, h_km=H_PARK):
        r = R_EARTH + h_km * 1000
        v_orb_l = np.sqrt(MU / r)
        v_esc_sq = 2 * MU / r
        return ((dv_val + v_orb_l) ** 2 - v_esc_sq) / 1e6

    def payload_for_dv(target_dv):
        if target_dv < 0:
            # Negative target means an easier orbit than parking; use small epsilon.
            target_dv = 1e-6
        # Upper bound: payload where the rocket can just provide the required orbital dv.
        required_total = max(0.0, ideal_dv_to_orbit(H_PARK) + target_dv)
        total_wet = sum(s['wet'] for s in stages)
        lo, hi = 0.0, max(30.0, total_wet * 2.0)
        while total_vac_dv(hi, stages) > required_total and hi < 1e6:
            hi *= 2.0
        if dv_rem(lo) - target_dv < 0:
            return 0.0
        for _ in range(60):
            mid = (lo + hi) / 2
            if dv_rem(mid) - target_dv > 0:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    # ---- Mission C3 values ----
    def elliptical_c3(apo_km):
        a = (r_park + R_EARTH + apo_km * 1000) / 2
        return -MU / a / 1e6

    missions = {}
    for key, m in MISSIONS.items():
        if 'apoapsis_km' in m:
            c3_val = elliptical_c3(m['apoapsis_km'])
        else:
            c3_val = m.get('c3', 0)
        missions[key] = dict(c3=c3_val, label=m['label'], color=m.get('color', '#888888'))

    # ---- C3-Mass curve ----
    m_pl_max = payload_for_dv(0)
    if missions:
        min_c3 = min(v['c3'] for v in missions.values()) - 4
    else:
        min_c3 = -20

    # Cover all observed calibration loads and the true zero-payload limit.
    # A mission-list-dependent window must not hide calibration points.
    min_c3 = min(min_c3, dv_to_c3(max(dv_rem(max(m_pts)), 0.0)))
    c3_max = dv_to_c3(dv_rem(0.0))
    c3_vals = np.linspace(min_c3, max(min_c3, c3_max), 400)
    m_vals = np.array([
        m_pl_max if c3_to_dv(c) <= 0 else max(payload_for_dv(c3_to_dv(c)), 0)
        for c in c3_vals
    ])
    m_vals = np.minimum(m_vals, m_pl_max)

    valid = (m_vals > 0.0) & (c3_vals < c3_max)
    c3_plot = c3_vals[valid].tolist()
    m_plot  = m_vals[valid].tolist()
    if m_pl_max > 0:
        c3_plot.append(c3_max)
        m_plot.append(0.0)

    # ---- Per-mission payloads ----
    mission_results = {}
    for key, m in missions.items():
        mp = payload_for_dv(c3_to_dv(m['c3']))
        mission_results[key] = dict(
            label=m['label'],
            c3=round(m['c3'], 2),
            dv=round(c3_to_dv(m['c3']), 1),
            payload=round(mp, 3) if mp > 0.002 else None,
            color=m['color'],
        )

    # ---- dv-Mass curve ----
    m_curve = np.linspace(0.05, m_pl_max * 1.02, 200).tolist()
    dv_curve_raw = [max(dv_rem(m), 0) for m in m_curve]
    dv_curve = [
        dict(m=round(m_curve[i], 4), dv=round(dv_curve_raw[i], 1))
        for i in range(len(m_curve))
    ]

    # ---- C3-Mass curve data (pair lists for Plotly) ----
    c3_mass_curve = [
        dict(c3=round(c3_plot[i], 2), payload=round(m_plot[i], 4))
        for i in range(len(c3_plot))
    ]

    return dict(
        name=config['rocket']['name'],
        m_pl_max=round(m_pl_max, 3),
        c3_max=round(c3_max, 1),
        c3_0_payload=round(payload_for_dv(c3_to_dv(0)), 3),
        c3_min=round(min_c3, 1),
        c3_mass_curve=c3_mass_curve,
        missions=mission_results,
        loss_model=dict(L0=round(L0, 2), L1=round(L1, 2), L2=round(L2, 2)),
        calibration_support=dict(
            measured_payload_min_t=min(m_pts), measured_payload_max_t=max(m_pts),
            measured_point_count=len(m_pts), model='quadratic_ascent_loss',
            reference_parking_alt_km=H_PARK, reference_launch_lat_deg=LAT,
            generated_payload_min_t=m_curve[0], generated_payload_max_t=m_curve[-1],
            extrapolation_is_measurement=False,
        ),
        dv_curve=dv_curve,
        parking_orbit=dict(
            h_km=H_PARK, v_orbit=round(v_orbit, 1), v_esc=round(v_esc, 1),
            dv_to_escape=round(v_esc - v_orbit, 1), lat=LAT,
        ),
        stages=[dict(
            wet=s['wet'], dry=s['dry'],
            isp_vac=s['isp_vac'], isp_sl=s['isp_sl'],
            thrust=round(s['thrust'], 1),
            isp_type=s.get('isp_type', ''),
        ) for s in stages],
    )


# =============================================================================
# General Earth-orbit mission calculator
# =============================================================================

def compute_orbit_mission(target_altitude_km, inclination_deg, parking_alt_km=220.0,
                          launch_lat=19.81, orbit_type='circular',
                          arrival_strategy='combined', periapsis_km=None):
    """
    Calculate a practical post-parking-orbit delta-v budget.

    ``orbit_type='circular'`` models a Hohmann transfer followed by a combined
    circularisation/plane-change burn at the destination.  ``'transfer'``
    stops in the transfer ellipse and only performs the plane change there.
    The returned burn sequence always includes parking-orbit insertion and
    exposes the number of post-parking ignitions required for vehicle ranking.

    Rocket calibration is referenced to a due-east 220 km orbit from LAT.
    The launch-plane term therefore accounts for the different projection of
    Earth's rotation.  A launch site can directly reach inclinations between
    |latitude| and 180-|latitude|; targets outside that range use the nearest
    directly reachable plane and finish the remaining change at destination.
    """
    orbit_type = str(orbit_type or 'circular').lower()
    if orbit_type not in ('circular', 'transfer', 'elliptical'):
        raise ValueError("orbit_type must be 'circular', 'transfer' or 'elliptical'")
    arrival_strategy = str(arrival_strategy or 'combined').lower()
    if arrival_strategy not in ('combined', 'separate'):
        raise ValueError("arrival_strategy must be 'combined' or 'separate'")

    r_park = R_EARTH + parking_alt_km * 1000.0
    r_target = R_EARTH + target_altitude_km * 1000.0
    if r_park <= R_EARTH or r_target <= R_EARTH:
        raise ValueError('orbit altitudes must be above Earth surface')

    if orbit_type == 'elliptical':
        if periapsis_km is None or not np.isfinite(periapsis_km):
            raise ValueError('elliptical orbit requires finite periapsis_km')
        if periapsis_km <= 0 or periapsis_km > target_altitude_km:
            raise ValueError('periapsis must be above surface and no higher than apoapsis')
        if target_altitude_km < parking_alt_km:
            raise ValueError('elliptical apoapsis must be no lower than parking orbit')

    v_park = np.sqrt(MU / r_park)
    v_target_circular = np.sqrt(MU / r_target)
    a_transfer = (r_park + r_target) / 2.0
    v_transfer_departure = np.sqrt(2.0 * MU / r_park - MU / a_transfer)
    v_transfer_arrival = np.sqrt(2.0 * MU / r_target - MU / a_transfer)
    dv_departure = abs(v_transfer_departure - v_park)

    # Select the closest plane that can be reached directly from the site.
    site_lat_abs = abs(float(launch_lat))
    min_direct_inc = min(site_lat_abs, 90.0)
    max_direct_inc = 180.0 - min_direct_inc
    parking_inc = min(max(float(inclination_deg), min_direct_inc), max_direct_inc)
    delta_i_deg = abs(float(inclination_deg) - parking_inc)
    delta_i_rad = np.radians(delta_i_deg)

    # Arrival burn can be combined (minimum ideal impulse) or split into a
    # circularisation burn followed by a plane change on the final orbit.
    dv_circularize = 0.0
    dv_plane = 0.0
    if orbit_type in ('circular', 'elliptical'):
        final_sma = (R_EARTH + periapsis_km * 1000.0 + r_target) / 2.0 if orbit_type == 'elliptical' else r_target
        v_final = np.sqrt(2.0 * MU / r_target - MU / final_sma)
        dv_circularize = abs(v_final - v_transfer_arrival)
        dv_plane = 2.0 * v_final * np.sin(delta_i_rad / 2.0)
        dv_arrival_combined = np.sqrt(
            v_transfer_arrival ** 2 + v_final ** 2
            - 2.0 * v_transfer_arrival * v_final * np.cos(delta_i_rad)
        )
        dv_arrival = dv_arrival_combined if arrival_strategy == 'combined' else dv_circularize + dv_plane
    else:
        dv_plane = 2.0 * v_transfer_arrival * np.sin(delta_i_rad / 2.0)
        dv_arrival = dv_plane
        final_sma = a_transfer
        v_final = v_transfer_arrival

    dv_orbit = dv_departure + dv_arrival

    # Earth-rotation contribution along the launch azimuth.  cos(i) gives the
    # eastward component: negative for retrograde launches.
    site_rotation_equator = 2.0 * np.pi * (R_EARTH + ALT) / SID_DAY
    launch_rotation_component = site_rotation_equator * np.cos(np.radians(parking_inc))
    reference_rotation_component = earth_rot
    dv_launch_plane = reference_rotation_component - launch_rotation_component
    dv_total = max(0.0, dv_orbit + dv_launch_plane)

    c3_orbit = -MU / final_sma / 1e6
    v_esc_park = np.sqrt(2.0 * MU / r_park)
    c3_effective = ((v_park + dv_total) ** 2 - v_esc_park ** 2) / 1e6
    period_min = 2.0 * np.pi * np.sqrt(final_sma ** 3 / MU) / 60.0

    # The calibrated curve covers insertion into the initial parking orbit.
    # Every non-zero burn after that point is a separate upper-stage ignition.
    # Keeping this explicit prevents a single-impulse assumption from leaking
    # into vehicle ranking (especially for GEO/GTO and plane-change missions).
    burn_sequence = (
        [
            {'order': 1, 'name': '进入初始停泊轨道', 'dv_m_s': round(ideal_dv_to_orbit(parking_alt_km), 1), 'source': 'rocket_calibration'},
            {'order': 2, 'name': '霍曼转移出发点火', 'dv_m_s': round(dv_departure, 1), 'source': 'mission_model'},
            {'order': 3, 'name': '目标点圆化与平面变化合并点火', 'dv_m_s': round(dv_arrival, 1), 'source': 'mission_model'},
        ] if orbit_type in ('circular', 'elliptical') and arrival_strategy == 'combined' else
        [
            {'order': 1, 'name': '进入初始停泊轨道', 'dv_m_s': round(ideal_dv_to_orbit(parking_alt_km), 1), 'source': 'rocket_calibration'},
            {'order': 2, 'name': '霍曼转移出发点火', 'dv_m_s': round(dv_departure, 1), 'source': 'mission_model'},
            {'order': 3, 'name': '目标点圆化点火', 'dv_m_s': round(dv_circularize, 1), 'source': 'mission_model'},
            {'order': 4, 'name': '目标轨道平面变化点火', 'dv_m_s': round(dv_plane, 1), 'source': 'mission_model'},
        ] if orbit_type in ('circular', 'elliptical') else
        [
            {'order': 1, 'name': '进入初始停泊轨道', 'dv_m_s': round(ideal_dv_to_orbit(parking_alt_km), 1), 'source': 'rocket_calibration'},
            {'order': 2, 'name': '霍曼转移出发点火', 'dv_m_s': round(dv_departure, 1), 'source': 'mission_model'},
            {'order': 3, 'name': '转移轨道高点平面变化点火', 'dv_m_s': round(dv_plane, 1), 'source': 'mission_model'},
        ]
    )
    if orbit_type == 'elliptical':
        for burn in burn_sequence:
            if burn['order'] == 3:
                burn['name'] = '远地点调整近地点与平面合并点火' if arrival_strategy == 'combined' else '远地点调整近地点点火'
    # A zero-delta-v plane-change entry is a bookkeeping step, not an ignition.
    post_parking_ignitions = sum(1 for burn in burn_sequence[1:] if burn['dv_m_s'] > 0.1)
    total_ignitions = 1 + post_parking_ignitions
    restart_required = post_parking_ignitions > 1

    warnings = []
    if delta_i_deg > 0.01:
        warnings.append(
            f'{inclination_deg:.2f}° cannot be reached directly from latitude '
            f'{launch_lat:.2f}°; launch into {parking_inc:.2f}° and change the '
            f'remaining {delta_i_deg:.2f}° at destination.'
        )
    if orbit_type == 'transfer':
        warnings.append('Capacity is for transfer-orbit injection; destination circularisation is not included.')
    elif arrival_strategy == 'separate' and delta_i_deg > 0.01:
        warnings.append('Separate arrival burns are conservative; combining circularisation and plane change requires less ideal delta-v.')
    warnings.append(
        'This is an impulsive two-body estimate calibrated against the stored 220 km reference curve; '
        'finite burns and upper-stage lifetime are not modelled; restart feasibility is filtered separately from the vehicle stage flag.'
    )

    return dict(
        orbit_type=orbit_type,
        arrival_strategy=arrival_strategy,
        target_altitude_km=round(float(target_altitude_km), 3),
        apoapsis_km=round(float(target_altitude_km), 3),
        periapsis_km=round(float(periapsis_km if orbit_type == 'elliptical' else target_altitude_km if orbit_type == 'circular' else parking_alt_km), 3),
        inclination_deg=round(float(inclination_deg), 3),
        parking_alt_km=round(float(parking_alt_km), 3),
        launch_lat=round(float(launch_lat), 3),

        c3=round(c3_orbit, 3),
        c3_effective=round(c3_effective, 3),
        required_dv=round(dv_total, 1),
        dv_total=round(dv_total, 1),
        dv_orbit=round(dv_orbit, 1),
        dv_raise=round(dv_departure, 1),
        dv_departure=round(dv_departure, 1),
        dv_arrival=round(dv_arrival, 1),
        dv_circularize=round(dv_circularize, 1),
        dv_plane=round(dv_plane, 1),
        dv_launch_plane=round(dv_launch_plane, 1),
        ascent_adjustment_dv_m_s=float(dv_launch_plane),

        parking_inc_deg=round(parking_inc, 2),
        delta_i_deg=round(delta_i_deg, 2),
        direct_inclination_min_deg=round(min_direct_inc, 2),
        direct_inclination_max_deg=round(max_direct_inc, 2),

        v_park=round(v_park, 1),
        v_peri=round(np.sqrt(2.0 * MU / (R_EARTH + periapsis_km * 1000.0) - MU / final_sma) if orbit_type == 'elliptical' else v_transfer_departure, 1),
        v_apo=round(v_final if orbit_type == 'elliptical' else v_transfer_arrival, 1),
        eccentricity=round((r_target - (R_EARTH + periapsis_km * 1000.0)) / (2 * final_sma) if orbit_type == 'elliptical' else 0.0 if orbit_type == 'circular' else (r_target-r_park)/(2*a_transfer), 8),
        v_target=round(v_final, 1),
        period_min=round(period_min, 1),
        sma_km=round(final_sma / 1000.0, 1),
        post_parking_ignitions=post_parking_ignitions,
        total_ignitions=total_ignitions,
        restart_required=restart_required,
        parking_orbit=dict(
            altitude_km=round(float(parking_alt_km), 3),
            insertion_dv_reference=round(ideal_dv_to_orbit(parking_alt_km), 1),
            covered_by_calibration=abs(float(parking_alt_km) - H_PARK) < 1e-6,
        ),
        burn_sequence=burn_sequence,
        warnings=warnings,
    )


def compute_orbit_c3(apoapsis_km, inclination_deg, parking_alt_km=220.0, launch_lat=19.81):
    """Backward-compatible transfer-orbit calculator."""
    return compute_orbit_mission(
        apoapsis_km,
        inclination_deg,
        parking_alt_km=parking_alt_km,
        launch_lat=launch_lat,
        orbit_type='transfer',
    )


# =============================================================================
# CLI — JSON output only, no plotting
# =============================================================================
if __name__ == '__main__':
    json_only = '--json' in sys.argv
    all_rockets = '--all' in sys.argv

    rocket_files = []
    if all_rockets:
        rockets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rockets')
        if os.path.isdir(rockets_dir):
            rocket_files = [
                os.path.join(rockets_dir, f) for f in os.listdir(rockets_dir)
                if f.endswith(('.yaml', '.yml'))
            ]
    else:
        config_path = [a for a in sys.argv[1:] if a.endswith('.yaml') or a.endswith('.yml')]
        rocket_files = config_path

    if not rocket_files:
        rocket_files = ['rockets/neutron_i_2630.yaml']

    for rfile in rocket_files:
        if not os.path.exists(rfile):
            print(f"File not found: {rfile}")
            continue
        with open(rfile, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)

        name = cfg['rocket']['name']
        slug = name.lower().replace(' ', '_')
        out_dir = os.path.join('output', slug)

        print(f"Computing {name} ...")
        result = compute_rocket(cfg)

        json_path = os.path.join(out_dir, 'data.json')
        os.makedirs(out_dir, exist_ok=True)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"  -> {json_path}")

        print(f"  Max LEO: {result['m_pl_max']} t  |  C3=0: {result['c3_0_payload']} t  |  "
              f"Max C3: {result['c3_max']} km^2/s^2")
        for k, m in result['missions'].items():
            pl = f"{m['payload']:.2f} t" if m['payload'] else "N/A"
            print(f"    {m['label']:15s} (C3={m['c3']:6.1f}):  {pl}")
