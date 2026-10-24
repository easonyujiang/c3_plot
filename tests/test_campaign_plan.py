import json
import unittest

from server.campaign import CampaignQueue
from server.campaign_plan import build_six_flight_plan


def profile():
    return {'profile_id': 'profile-1', 'source': {'craft_sha256': 'craft-hash'}}


class CampaignPlanTests(unittest.TestCase):
    def test_default_plan_has_six_explicit_anchors(self):
        spec = build_six_flight_plan(profile(), 5.0)
        self.assertEqual(len(spec.trials), 6)
        self.assertEqual(spec.max_trials, 6)
        self.assertEqual([trial['payload_t'] for trial in spec.trials],
                         [5.0, 2.5, 3.5, 2.0, 2.5, 1.5])
        self.assertEqual(spec.trials[2]['orbit']['inclination_deg'], 97.8)
        self.assertEqual(spec.trials[2]['orbit']['apoapsis_km'], 220.0)
        self.assertEqual(spec.trials[3]['orbit']['inclination_deg'], 180.0)
        self.assertEqual(spec.trials[4]['orbit']['apoapsis_km'], 35786.0)
        self.assertEqual(spec.trials[5]['orbit']['orbit_type'], 'high_energy')
        queue = CampaignQueue(spec)
        self.assertEqual(queue.stats()['total'], 6)
        json.dumps(queue.snapshot())

    def test_sso_and_molniya_variants(self):
        sso = build_six_flight_plan(profile(), 4.0, sso=True)
        self.assertEqual(sso.trials[2]['label'], 'SSO')
        self.assertEqual(sso.trials[2]['orbit']['periapsis_km'], 600.0)
        molniya = build_six_flight_plan(profile(), 4.0, high_energy='molniya')
        self.assertEqual(molniya.trials[5]['orbit']['orbit_type'], 'elliptical')
        self.assertEqual(molniya.trials[5]['orbit']['inclination_deg'], 63.4)

    def test_invalid_plan_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            build_six_flight_plan(profile(), 0)
        with self.assertRaises(ValueError):
            build_six_flight_plan(profile(), 5.0, high_energy='geo')
        with self.assertRaises(ValueError):
            build_six_flight_plan({'profile_id': 'only'}, 5.0)


if __name__ == '__main__':
    unittest.main()
