"""Build the standard six-flight CampaignSpec from a RocketProfile JSON.

Planning only: no KSP launch, no game directory access, no database writes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.paths import ROOT
from server.campaign import CampaignQueue
from server.campaign_plan import build_six_flight_plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True, help='RocketProfile JSON')
    parser.add_argument('--design-payload', type=float, required=True,
                        help='Declared design payload in tonnes (e.g. 5.0 for 220 km/20°)')
    parser.add_argument('--sso', action='store_true',
                        help='Use a 600 km SSO anchor instead of 220 km polar')
    parser.add_argument('--high-energy', choices=('tli', 'molniya'), default='tli',
                        help='Choose the sixth anchor')
    parser.add_argument('--output', type=Path,
                        help='Spec JSON; defaults to output/campaigns/<profile stem>.json')
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text(encoding='utf-8'))
    spec = build_six_flight_plan(profile, args.design_payload,
                                 sso=args.sso, high_energy=args.high_energy)
    queue = CampaignQueue(spec)
    output = args.output or (ROOT / 'output' / 'campaigns' / (args.profile.stem + '.json'))
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(queue.snapshot(), ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8')
    print(json.dumps({
        'campaign_id': spec.campaign_id,
        'profile': spec.rocket_profile_id,
        'trials': len(spec.trials),
        'max_trials': spec.max_trials,
        'labels': [trial['label'] for trial in spec.trials],
        'payloads_t': [trial['payload_t'] for trial in spec.trials],
        'output': str(output),
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
