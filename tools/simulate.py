"""Run the offline T0/T1 ascent skeleton from a RocketProfile JSON.

Pure offline tool: it does not read KSP, launch the game or write the database.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.paths import ROOT
from c3_mass.ascent_simulation import AscentSettings, PitchProgram
from c3_mass.profile_simulation import simulate_profile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True, help='RocketProfile JSON')
    parser.add_argument('--output', type=Path, help='Result JSON; defaults to output/simulations/<stem>.json')
    parser.add_argument('--dt', type=float, default=0.05, help='RK4 step in seconds')
    parser.add_argument('--max-time', type=float, default=900.0, help='Maximum simulated time in seconds')
    parser.add_argument('--vertical-altitude', type=float, default=1000.0,
                        help='Altitude where the pitch program starts turning (m)')
    parser.add_argument('--turn-rate', type=float, default=2.0,
                        help='Pitch turn rate in degrees per km after the vertical phase')
    parser.add_argument('--area', type=float, default=0.0, help='Reference drag area in m^2')
    parser.add_argument('--drag-coefficient', type=float, default=0.0, help='Drag coefficient')
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text(encoding='utf-8'))
    result = simulate_profile(
        profile,
        pitch=PitchProgram(vertical_altitude_m=args.vertical_altitude,
                           turn_rate_deg_per_km=args.turn_rate),
        settings=AscentSettings(dt_s=args.dt, max_time_s=args.max_time),
        area_m2=args.area, drag_coefficient=args.drag_coefficient,
    )
    output = args.output or (ROOT / 'output' / 'simulations' / (args.profile.stem + '.json'))
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    final = result['simulation'].get('final') or {}
    print(json.dumps({
        'profile': result['name'],
        'payload_t': result['payload_t'],
        'ideal_dv_m_s': round(result['ideal_dv_m_s'], 3),
        'final_speed_m_s': round(final.get('speed_m_s', 0.0), 3),
        'final_altitude_m': round(final.get('altitude_m', 0.0), 3),
        'final_mass_t': result['simulation'].get('final_mass_t'),
        'max_q_Pa': result['simulation'].get('max_dynamic_pressure_Pa'),
        'warnings': result['warnings'],
        'output': str(output),
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
