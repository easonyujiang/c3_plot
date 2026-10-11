import math
import unittest

from compute_c3_mass import MU, R_EARTH, compute_orbit_mission
from server.routes_data import _parse_mission_query


class CustomEllipticalOrbitTests(unittest.TestCase):
    def test_user_acceptance_orbit_and_energy(self):
        mission = compute_orbit_mission(40000, 89, orbit_type='elliptical', periapsis_km=400)
        rp, ra = R_EARTH+400000, R_EARTH+40000000
        a = (rp+ra)/2
        self.assertEqual(mission['periapsis_km'], 400)
        self.assertEqual(mission['apoapsis_km'], 40000)
        self.assertEqual(mission['inclination_deg'], 89)
        self.assertAlmostEqual(mission['c3'], -MU/a/1e6, places=3)
        self.assertAlmostEqual(mission['v_peri'], math.sqrt(MU*(2/rp-1/a)), delta=.051)
        self.assertAlmostEqual(mission['v_apo'], math.sqrt(MU*(2/ra-1/a)), delta=.051)
        self.assertEqual(mission['post_parking_ignitions'], 2)
        self.assertEqual(mission['total_ignitions'], 3)
        self.assertGreater(mission['dv_arrival'], 0)
        self.assertEqual(mission['delta_i_deg'], 0)

    def test_matching_parking_perigee_needs_only_departure(self):
        mission = compute_orbit_mission(40000, 89, orbit_type='elliptical', periapsis_km=220)
        self.assertEqual(mission['post_parking_ignitions'], 1)
        self.assertEqual(mission['dv_arrival'], 0)

    def test_equal_apses_matches_circular_mission(self):
        circular = compute_orbit_mission(40000, 0)
        ellipse = compute_orbit_mission(40000, 0, orbit_type='elliptical', periapsis_km=40000)
        for key in ('required_dv', 'c3', 'dv_departure', 'dv_arrival', 'period_min'):
            self.assertEqual(ellipse[key], circular[key], key)

    def test_api_parses_exact_user_task(self):
        parsed = _parse_mission_query({'orbit_type':'elliptical', 'periapsis_km':400,
                                     'apoapsis_km':40000, 'inclination_deg':89, 'payload_t':.7})
        self.assertEqual(parsed['target_altitude_km'], 40000)
        self.assertEqual(parsed['periapsis_km'], 400)
        self.assertEqual(parsed['payload_t'], .7)

    def test_rejects_missing_or_conflicting_apses(self):
        bad = [
            {'orbit_type':'elliptical', 'apoapsis_km':40000},
            {'orbit_type':'elliptical', 'periapsis_km':400},
            {'orbit_type':'elliptical', 'periapsis_km':40000, 'apoapsis_km':400},
            {'orbit_type':'elliptical', 'periapsis_km':True, 'apoapsis_km':40000},
            {'orbit_type':'elliptical', 'periapsis_km':float('nan'), 'apoapsis_km':40000},
            {'orbit_type':'elliptical', 'periapsis_km':400, 'apoapsis_km':40000, 'target_altitude_km':220},
            {'orbit_type':'circular', 'periapsis_km':400},
        ]
        for body in bad:
            with self.subTest(body=body), self.assertRaises(ValueError):
                _parse_mission_query(body)
