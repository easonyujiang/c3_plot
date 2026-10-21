import unittest

from c3_mass.compute_c3_mass import compute_orbit_mission
from server.models import interpolate_payload_for_dv


class OrbitMissionTests(unittest.TestCase):
    def test_retrograde_geosynchronous_circular_budget(self):
        result = compute_orbit_mission(
            35786,
            180,
            parking_alt_km=220,
            launch_lat=19.81,
            orbit_type='circular',
        )
        self.assertAlmostEqual(result['parking_inc_deg'], 160.19, places=2)
        self.assertAlmostEqual(result['delta_i_deg'], 19.81, places=2)
        self.assertAlmostEqual(result['required_dv'], 4986.3, delta=2.0)
        self.assertAlmostEqual(result['period_min'] / 60, 23.93, delta=0.05)

    def test_circular_delivery_costs_more_than_transfer_injection(self):
        circular = compute_orbit_mission(35786, 28.5, orbit_type='circular')
        transfer = compute_orbit_mission(35786, 28.5, orbit_type='transfer')
        self.assertGreater(circular['required_dv'], transfer['required_dv'])

    def test_circular_mission_exposes_multiple_ignitions(self):
        result = compute_orbit_mission(35786, 180, orbit_type='circular')
        self.assertEqual(result['total_ignitions'], 3)
        self.assertEqual(result['post_parking_ignitions'], 2)
        self.assertTrue(result['restart_required'])
        self.assertEqual(len(result['burn_sequence']), 3)

    def test_transfer_without_plane_change_needs_one_post_parking_ignition(self):
        result = compute_orbit_mission(35786, 19.81, orbit_type='transfer')
        self.assertEqual(result['total_ignitions'], 2)
        self.assertEqual(result['post_parking_ignitions'], 1)
        self.assertFalse(result['restart_required'])

    def test_retrograde_costs_more_than_prograde(self):
        prograde = compute_orbit_mission(35786, 19.81, orbit_type='circular')
        retrograde = compute_orbit_mission(35786, 180, orbit_type='circular')
        self.assertGreater(retrograde['required_dv'], prograde['required_dv'])
        self.assertGreater(retrograde['dv_launch_plane'], 800)

    def test_payload_curve_is_inverted_by_delta_v(self):
        curve = [
            {'m': 0.1, 'dv': 5000},
            {'m': 1.0, 'dv': 3000},
            {'m': 2.0, 'dv': 0},
        ]
        self.assertAlmostEqual(interpolate_payload_for_dv(4000, curve), 0.55)
        self.assertEqual(interpolate_payload_for_dv(6000, curve), 0)
        self.assertEqual(interpolate_payload_for_dv(0, curve, leo_capacity=1.95), 1.95)


if __name__ == '__main__':
    unittest.main()
