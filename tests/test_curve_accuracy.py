"""Independent physical invariants, rather than snapshots of current output."""
import copy
import math
import unittest
from pathlib import Path
from unittest.mock import patch

from c3_mass.compute_c3_mass import compute_rocket, compute_orbit_mission, total_vac_dv, G0
from server.mission_analysis import analyze_rocket_for_mission
from server.models import interpolate_payload
from server.yaml_codec import load_config

ROOT = Path(__file__).resolve().parents[1]


class CurveAccuracyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config((ROOT / 'rockets/neutron_i_2630.yaml').read_text(encoding='utf-8'))
        cls.data = compute_rocket(cls.cfg)

    def parallel_config(self):
        cfg = copy.deepcopy(self.cfg)
        cfg['stages'] = [
            {'wet_mass_t': 10, 'dry_mass_t': 2, 'parallel': True},
            {'wet_mass_t': 20, 'dry_mass_t': 4, 'parallel': False},
        ]
        perf = [{'isp_vac': 300, 'isp_sl': 300, 'thrust': t, 'isp_type': 'test'} for t in (80, 100)]
        return cfg, perf

    def test_parallel_delta_v_obeys_independent_mass_balance(self):
        cfg, perf = self.parallel_config()
        with patch('c3_mass.compute_c3_mass.resolve_stage_engine_performance', side_effect=perf):
            data = compute_rocket(cfg)
        stages = [dict(wet=s['wet'], dry=s['dry'], isp_vac=s['isp_vac']) for s in data['stages']]
        for payload in (0.1, 1.0, 4.0):
            # Combined initial mass 30+p; combined burnout mass 12+p;
            # discard 2 t booster dry mass, leaving core 10+p, then 4+p.
            expected = 300 * G0 * (math.log((30 + payload) / (12 + payload))
                                  + math.log((10 + payload) / (4 + payload)))
            self.assertAlmostEqual(total_vac_dv(payload, stages), expected, places=8)

    def test_parallel_core_first_burnout_is_not_silently_extrapolated(self):
        cfg, perf = self.parallel_config()
        perf[1]['thrust'] = 300
        with patch('c3_mass.compute_c3_mass.resolve_stage_engine_performance', side_effect=perf):
            with self.assertRaisesRegex(ValueError, 'burnout'):
                compute_rocket(cfg)

    def analyze(self, inclination=0, parking=220, payload=0.4):
        return analyze_rocket_for_mission(
            rocket='Neutron I 2630', slug='neutron_i_2630', config=self.cfg,
            computed_data=self.data, mission=compute_orbit_mission(35786, inclination, parking_alt_km=parking),
            payload_t=payload)

    def test_retrograde_correction_consumes_ascent_propellant(self):
        prograde = self.analyze(0)
        retrograde = self.analyze(180)
        self.assertTrue(prograde.requested_payload_evaluation.feasible)
        self.assertFalse(prograde.recommendation_eligible)
        self.assertEqual(prograde.to_api_dict()['calibration_support']['status'], 'extrapolated')
        self.assertFalse(retrograde.requested_payload_evaluation.feasible)
        self.assertLess(retrograde.capacity_search.max_payload_t, prograde.capacity_search.max_payload_t)
        difference = (retrograde.requested_payload_evaluation.ascent_required_dv_m_s
                      - prograde.requested_payload_evaluation.ascent_required_dv_m_s)
        self.assertAlmostEqual(difference, compute_orbit_mission(35786, 180)['ascent_adjustment_dv_m_s'])

    def test_parking_altitude_requires_matching_calibration(self):
        result = self.analyze(parking=400)
        self.assertEqual(result.verification_status, 'data_insufficient')
        self.assertFalse(result.recommendation_eligible)
        self.assertFalse(compute_orbit_mission(35786, 0, parking_alt_km=400)['parking_orbit']['covered_by_calibration'])

    def test_c3_outside_upper_boundary_has_no_positive_capacity(self):
        curve = [{'c3': 0, 'payload': 1}, {'c3': 50, 'payload': 0.01}]
        self.assertEqual(interpolate_payload(55, curve), 0)
        self.assertEqual(interpolate_payload(50, curve), 0.01)
        self.assertAlmostEqual(interpolate_payload(25, curve), 0.505)

    def test_measured_support_is_distinct_from_generated_curve(self):
        support = self.data['calibration_support']
        self.assertEqual(support['measured_payload_min_t'], 1.055)
        self.assertEqual(support['measured_payload_max_t'], 4)
        self.assertLess(support['generated_payload_min_t'], support['measured_payload_min_t'])
        self.assertFalse(support['extrapolation_is_measurement'])

    def test_curve_covers_highest_calibration_load_and_zero_payload_limit(self):
        curve = self.data['c3_mass_curve']
        self.assertAlmostEqual(curve[0]['payload'], 4, delta=0.001)
        self.assertEqual(curve[-1]['payload'], 0)
        self.assertAlmostEqual(curve[-1]['c3'], self.data['c3_max'], delta=0.051)
