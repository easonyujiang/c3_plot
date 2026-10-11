"""
Data endpoints: get computed results, recompute, mission planner.
"""
import math
import sys
import os
from flask import Blueprint, request, jsonify

bp = Blueprint('data', __name__)

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from compute_c3_mass import compute_rocket, compute_orbit_c3, compute_orbit_mission
from mission_simulation import DataQuality
from . import models
from .mission_analysis import analyze_rocket_for_mission, unavailable_rocket_analysis

# Internal, fixed resource limits for the detailed validation pipeline.
# Clients must never be able to control these.
_PAYLOAD_TOLERANCE_T = 0.001
_INITIAL_SAMPLES = 17
_MAX_EVALUATIONS = 128
_MAX_SEARCH_NODES = 10000

_MISSION_QUERY_KEYS = {
    'target_altitude_km', 'inclination_deg', 'parking_alt_km', 'launch_lat',
    'payload_t', 'orbit_type', 'arrival_strategy', 'include_audit',
    'periapsis_km', 'apoapsis_km',
}


def _strict_number(value, name, *, minimum=None, maximum=None):
    """Strict finite-number parsing.  Booleans, NaN, +/-Infinity and
    unparseable values are rejected with ValueError; returns a Python float."""
    if isinstance(value, bool):
        raise ValueError(f'{name} 必须是数值，不能是布尔值')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{name} 必须是有限数值')
    if not math.isfinite(number):
        raise ValueError(f'{name} 必须是有限数值')
    if minimum is not None and number < minimum:
        raise ValueError(f'{name} 必须 >= {minimum}')
    if maximum is not None and number > maximum:
        raise ValueError(f'{name} 必须 <= {maximum}')
    return number


def _parse_mission_query(body):
    """Strictly parse and validate the /api/mission-query request body.
    Unknown keys, booleans-as-numbers, NaN/Infinity and unparseable values
    are rejected.  Returns a validated parameter dict."""
    if not isinstance(body, dict):
        raise ValueError('请求体必须是 JSON 对象')
    unknown = sorted(set(body) - _MISSION_QUERY_KEYS)
    if unknown:
        raise ValueError(f'未知参数：{", ".join(unknown)}')

    target = _strict_number(body.get('apoapsis_km', body.get('target_altitude_km', 35786)),
                            'target_altitude_km', minimum=80)
    parking = _strict_number(body.get('parking_alt_km', 220),
                             'parking_alt_km', minimum=80)
    inclination = _strict_number(body.get('inclination_deg', 180),
                                 'inclination_deg', minimum=0, maximum=180)
    launch_lat = _strict_number(body.get('launch_lat', 19.81),
                                'launch_lat', minimum=-90, maximum=90)
    payload = _strict_number(body.get('payload_t', 0), 'payload_t', minimum=0)

    orbit_type = body.get('orbit_type', 'circular')
    if orbit_type not in ('circular', 'transfer', 'elliptical'):
        raise ValueError('orbit_type 必须是 circular、transfer 或 elliptical')
    periapsis = None
    if orbit_type == 'elliptical':
        if 'periapsis_km' not in body or ('apoapsis_km' not in body and 'target_altitude_km' not in body):
            raise ValueError('椭圆轨道必须填写近地点和远地点')
        periapsis = _strict_number(body['periapsis_km'], 'periapsis_km', minimum=80)
        if periapsis > target:
            raise ValueError('近地点不得高于远地点')
    elif 'periapsis_km' in body:
        raise ValueError('指定近地点请使用 elliptical 轨道类型')
    if 'apoapsis_km' in body and 'target_altitude_km' in body and target != _strict_number(body['target_altitude_km'], 'target_altitude_km'):
        raise ValueError('远地点与目标高度冲突')
    arrival_strategy = body.get('arrival_strategy', 'combined')
    if arrival_strategy not in ('combined', 'separate'):
        raise ValueError('arrival_strategy 必须是 combined 或 separate')
    include_audit = body.get('include_audit', False)
    if not isinstance(include_audit, bool):
        raise ValueError('include_audit 必须是布尔值')

    return {
        'target_altitude_km': target,
        'inclination_deg': inclination,
        'parking_alt_km': parking,
        'launch_lat': launch_lat,
        'payload_t': payload,
        'orbit_type': orbit_type,
        'arrival_strategy': arrival_strategy,
        'include_audit': include_audit,
        'periapsis_km': periapsis,
    }


_QUICK_ESTIMATE_FIELDS = (
    'rocket', 'slug', 'capacity', 'requested_payload', 'margin',
    'utilization', 'dv_feasible', 'supports_restart', 'restart_required',
    'feasible', 'reason',
)


def _detailed_entry(item, validation):
    """Combine the legacy quick-estimate item with the detailed validation
    summary.  The old fields keep their exact meaning; new aliases mirror
    the detailed validation for gradual frontend migration."""
    entry = {key: item[key] for key in _QUICK_ESTIMATE_FIELDS}
    entry['quick_estimate'] = {key: item[key] for key in _QUICK_ESTIMATE_FIELDS}
    entry['detailed_validation'] = validation
    entry['verification_status'] = validation['verification_status']
    entry['verified_feasible'] = validation['verified_feasible']
    entry['verified_capacity'] = (validation.get('capacity') or {}).get(
        'max_payload_t')
    entry['recommendation_eligible'] = validation['recommendation_eligible']
    entry['detailed_reason'] = validation['failure_reason_zh']
    return entry


@bp.route('/api/data/<slug>', methods=['GET'])
def get_data(slug):
    data = models.get_computed_data(slug)
    if data is None:
        return jsonify(error=f'Not computed yet. POST /api/recompute/{slug}'), 404
    # Always expose mission payloads from the computed C3 curve. This keeps
    # old rockets (which predate mission payload persistence) useful too.
    standard = models.list_standard_missions()
    curve = data.get('c3_mass_curve', [])
    payload_at = lambda c3: models.interpolate_payload(c3, curve) if curve else None
    if not data.get('missions'):
        data['missions'] = {}
        for mission in standard:
            data['missions'][str(mission['id'])] = {
                'label': mission['label'], 'c3': mission['c3'], 'dv': mission['dv'],
                'payload': payload_at(mission['c3']),
                'color': '#8e8cff',
            }
    else:
        for mission in data['missions'].values():
            if mission.get('payload') is None:
                mission['payload'] = payload_at(mission.get('c3', 0))
    return jsonify(data)


@bp.route('/api/recompute/<slug>', methods=['POST'])
def recompute(slug):
    cfg = models.get_rocket_config(slug)
    if cfg is None:
        return jsonify(error='Not found'), 404

    try:
        result = compute_rocket(cfg)
    except Exception as e:
        return jsonify(error=str(e)), 500

    models.save_computed_data(slug, result)
    return jsonify(result)


@bp.route('/api/orbit-calc', methods=['POST'])
def orbit_calc():
    """Calculate C3 for a high Earth orbit given apoapsis and inclination."""
    try:
        body = request.get_json(force=True)
    except Exception:
        return jsonify(error='Invalid JSON'), 400

    if not isinstance(body, dict):
        return jsonify(error='JSON body must be an object'), 400
    try:
        apoapsis_km = float(body.get('apoapsis_km', 0))
        inclination_deg = float(body.get('inclination_deg', 19.81))
        parking_alt_km = float(body.get('parking_alt_km', 220))
        launch_lat = float(body.get('launch_lat', 19.81))
    except (TypeError, ValueError):
        return jsonify(error='Orbit parameters must be numbers'), 400

    if apoapsis_km <= parking_alt_km:
        return jsonify(error='Apoapsis must be above parking orbit altitude'), 400
    if inclination_deg < 0 or inclination_deg > 180:
        return jsonify(error='Inclination must be 0-180 degrees'), 400

    result = compute_orbit_c3(apoapsis_km, inclination_deg, parking_alt_km, launch_lat)
    return jsonify(result)


@bp.route('/api/mission-query', methods=['POST'])
def mission_query():
    """Calculate an orbit budget, rank all rockets by the legacy quick
    estimate, and run the detailed calibration-based validation pipeline
    (analyze_rocket_for_mission) on every rocket.

    The final ``recommended`` rocket is chosen ONLY from detailed-validated
    candidates (recommendation_eligible == true); the legacy quick estimate
    remains in every entry unchanged and is used only as a sorting aid.
    """
    try:
        body = request.get_json(force=True)
    except Exception:
        return jsonify(error='Invalid JSON'), 400
    try:
        params = _parse_mission_query(body)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    try:
        mission = compute_orbit_mission(
            params['target_altitude_km'],
            params['inclination_deg'],
            parking_alt_km=params['parking_alt_km'],
            launch_lat=params['launch_lat'],
            orbit_type=params['orbit_type'],
            arrival_strategy=params['arrival_strategy'],
            **({'periapsis_km': params['periapsis_km']} if params['orbit_type'] == 'elliptical' else {}),
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    payload_t = params['payload_t']
    requested = payload_t if payload_t > 0 else None

    results = models.find_rockets_for_orbit(
        mission['required_dv'],
        payload_t=requested,
        required_post_parking_ignitions=mission.get('post_parking_ignitions', 1),
    )

    detailed = []
    for item in results:
        cfg = models.get_rocket_config(item['slug'])
        data = models.get_computed_data(item['slug'])
        if cfg is None or data is None:
            if cfg is None and data is None:
                missing = '缺少火箭配置与计算结果'
            elif cfg is None:
                missing = '缺少火箭配置'
            else:
                missing = '缺少计算结果'
            analysis = unavailable_rocket_analysis(
                rocket=item['rocket'], slug=item['slug'],
                reason=f'{missing}，无法进行详细验证',
                phase='normalization',
                extra_warnings=[f'{missing}，无法进行详细验证'],
                payload_requested=requested is not None)
        else:
            try:
                analysis = analyze_rocket_for_mission(
                    rocket=item['rocket'], slug=item['slug'], config=cfg,
                    computed_data=data, mission=mission,
                    payload_t=requested,
                    data_quality=DataQuality.EXACT,
                    payload_tolerance_t=_PAYLOAD_TOLERANCE_T,
                    monotonicity_samples=_INITIAL_SAMPLES,
                    max_evaluations=_MAX_EVALUATIONS,
                    max_search_nodes=_MAX_SEARCH_NODES)
            except ValueError as exc:
                # expected per-rocket data problems must not break the fleet
                analysis = unavailable_rocket_analysis(
                    rocket=item['rocket'], slug=item['slug'],
                    reason=f'详细验证失败：{exc}',
                    phase='normalization',
                    extra_warnings=['该火箭的详细验证输入不可用'],
                    payload_requested=requested is not None)
        validation = analysis.to_api_dict(include_audit=params['include_audit'])
        detailed.append(_detailed_entry(item, validation))

    # --- recommendation from detailed validation only -----------------------
    eligible = [entry for entry in detailed if entry['recommendation_eligible']]
    if requested is not None:
        # prefer the smallest NON-NEGATIVE margin between the verified
        # capacity and the requested payload; rockets without a comparable
        # capacity fall back to the legacy margin as a secondary basis.
        def with_payload_key(entry):
            capacity = entry['verified_capacity']
            if capacity is not None:
                margin = capacity - requested
                if margin >= 0:
                    return (0, margin, -(entry['capacity'] or 0.0))
                return (1, -margin, -(entry['capacity'] or 0.0))
            legacy_margin = entry['margin'] if entry['margin'] is not None \
                else float('-inf')
            return (2, -legacy_margin, -(entry['capacity'] or 0.0))
        eligible.sort(key=with_payload_key)
    else:
        eligible.sort(key=lambda entry: -(entry['verified_capacity'] or 0.0))
    recommended = eligible[0] if eligible else None

    statuses = [entry['verification_status'] for entry in detailed]
    return jsonify(
        mission=mission,
        payload_t=requested,
        count=len(results),
        feasible_count=sum(1 for item in results if item['feasible']),
        recommended=recommended,
        results=detailed,
        detailed_analysis={
            'enabled': True,
            'model': 'calibrated_serial_multiburn',
            'verified_feasible_count': statuses.count('verified_feasible'),
            'verified_infeasible_count': statuses.count('verified_infeasible'),
            'data_insufficient_count': statuses.count('data_insufficient'),
            'recommendation_basis': 'detailed_validation',
            'include_audit': params['include_audit'],
        },
    )


@bp.route('/api/find', methods=['POST'])
def find_rockets():
    try:
        body = request.get_json(force=True)
    except Exception:
        return jsonify(error='Invalid JSON'), 400

    if not isinstance(body, dict):
        return jsonify(error='JSON body must be an object'), 400
    try:
        payload_t = float(body.get('payload_t', 0))
        c3 = float(body.get('c3', 0))
    except (TypeError, ValueError):
        return jsonify(error='payload_t and c3 must be numbers'), 400
    if payload_t <= 0:
        return jsonify(error='payload_t must be > 0'), 400

    results = models.find_rockets_for_mission(payload_t, c3)

    # Match against standard missions for display info
    std_missions = models.list_standard_missions()
    mission_info = None
    for m in std_missions:
        if abs(m['c3'] - c3) < 0.01:
            mission_info = m
            break

    return jsonify(payload_t=payload_t, c3=c3, mission=mission_info, results=results)
