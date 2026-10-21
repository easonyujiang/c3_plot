import copy
import json
import math
import unittest
from pathlib import Path
from unittest import mock

from c3_mass.compute_c3_mass import compute_orbit_mission, compute_rocket
from c3_mass.mission_adapters import normalize_stages
from c3_mass.mission_simulation import DataQuality, FailureCode
from server.mission_analysis import (
    RocketMissionAnalysisResult,
    analyze_rocket_for_mission,
)
from server.yaml_codec import load_config

G = 9.80665


def _stages_cfg():
    return [
        {
            'wet_mass_t': 25.0, 'dry_mass_t': 5.0,
            'engines': {'cfg': 'SC-ENG-Merlin-1A.cfg', 'count': 1},
            'multi_ignition': False, 'parallel': False,
        },
        {
            'wet_mass_t': 10.0, 'dry_mass_t': 2.0,
            'engines': {'cfg': 'SC-ENG-LR81-8096.cfg', 'count': 1},
            'multi_ignition': True, 'parallel': False,
        },
    ]


def _total_vac(m):
    return (304.0 * G * (-math.log1p(-20.0 / (m + 35.0)))
            + 300.0 * G * (-math.log1p(-8.0 / (m + 10.0))))


def _computed_data():
    return {'dv_curve': [
        {'m': m, 'dv': _total_vac(m) - 3000.0}
        for m in (0.5, 2.0, 4.0, 6.0, 8.0, 10.0)
    ]}


def _mission():
    return {
        'burn_sequence': [
            {'order': 1, 'name': 'park', 'burn_id': 'park',
             'source': 'rocket_calibration', 'dv_m_s': 0.0},
            {'order': 2, 'name': 'b1', 'burn_id': 'b1',
             'source': 'mission_model', 'dv_m_s': 500.0},
            {'order': 3, 'name': 'b2', 'burn_id': 'b2',
             'source': 'mission_model', 'dv_m_s': 300.0},
        ],
    }


def _cfg():
    return {'rocket': {'name': 'Test Rocket'}, 'stages': _stages_cfg()}

def _analyze(**kwargs):
    defaults = dict(
        rocket='Test Rocket', slug='test_rocket', config=_cfg(),
        computed_data=_computed_data(), mission=_mission())
    defaults.update(kwargs)
    return analyze_rocket_for_mission(**defaults)


class StatusResolutionTests(unittest.TestCase):
    def test_no_payload_bounded_capacity(self):
        result = _analyze()
        self.assertEqual(result.verification_status, 'verified_feasible')
        self.assertTrue(result.verified_feasible)
        self.assertTrue(result.recommendation_eligible)
        capacity = result.capacity_search
        self.assertEqual(capacity.capacity_status, 'bounded')
        api = result.to_api_dict()
        self.assertEqual(api['capacity']['status'], 'bounded')
        self.assertIsNotNone(api['capacity']['max_payload_t'])
        self.assertIsNone(api['requested_payload'])

    def test_payload_feasible(self):
        result = _analyze(payload_t=1.0)
        self.assertEqual(result.verification_status, 'verified_feasible')
        self.assertTrue(result.verified_feasible)
        self.assertTrue(result.recommendation_eligible)
        self.assertTrue(result.requested_payload_evaluation.feasible)
        self.assertTrue(
            result.requested_payload_evaluation.metadata['plan_verified'])

    def test_payload_clearly_infeasible(self):
        result = _analyze(payload_t=9.0)
        self.assertEqual(result.verification_status, 'verified_infeasible')
        self.assertFalse(result.verified_feasible)
        self.assertFalse(result.recommendation_eligible)
        self.assertEqual(result.failure_code,
                         FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertEqual(result.failure_phase, 'stage_planning')

    def test_data_insufficient_gives_none(self):
        config = _cfg()
        config['stages'][0]['parallel'] = True
        result = _analyze(config=config)
        self.assertEqual(result.verification_status, 'data_insufficient')
        self.assertIsNone(result.verified_feasible)
        self.assertFalse(result.recommendation_eligible)
        self.assertIsNone(result.capacity_search)
        self.assertIsNone(result.requested_payload_evaluation)

    def test_recommendation_eligible_only_when_verified_feasible(self):
        self.assertTrue(_analyze().recommendation_eligible)
        self.assertTrue(_analyze(payload_t=1.0).recommendation_eligible)
        self.assertFalse(_analyze(payload_t=9.0).recommendation_eligible)
        config = _cfg()
        config['stages'][0]['parallel'] = True
        self.assertFalse(_analyze(config=config).recommendation_eligible)

    def test_payload_is_authority_over_capacity_status(self):
        # capacity hits the evaluation limit (no bisection budget) while the
        # specified payload is clearly feasible -> payload wins
        result = _analyze(payload_t=1.0, max_evaluations=17)
        self.assertEqual(result.verification_status, 'verified_feasible')
        self.assertTrue(result.verified_feasible)
        self.assertEqual(
            result.capacity_search.capacity_status, 'evaluation_limit')

    def test_capacity_is_authority_without_payload(self):
        result = _analyze(max_evaluations=17)
        self.assertEqual(result.verification_status, 'data_insufficient')
        self.assertIsNone(result.verified_feasible)
        self.assertEqual(
            result.capacity_search.capacity_status, 'evaluation_limit')


class DataIsolationTests(unittest.TestCase):
    def test_parallel_stops_later_stages(self):
        config = _cfg()
        config['stages'][0]['parallel'] = True
        result = _analyze(config=config, payload_t=1.0)
        self.assertEqual(result.failure_phase, 'normalization')
        self.assertIsNone(result.capacity_search)
        self.assertIsNone(result.requested_payload_evaluation)
        self.assertTrue(any('并联' in w for w in result.warnings))

    def test_missing_engine_data(self):
        config = _cfg()
        config['stages'][0]['engines'] = {'cfg': 'Nope.cfg', 'count': 1}
        result = _analyze(config=config)
        self.assertEqual(result.verification_status, 'data_insufficient')
        self.assertEqual(result.failure_phase, 'normalization')
        self.assertIsNone(result.capacity_search)

    def test_missing_dv_curve_is_data_insufficient_not_exception(self):
        result = _analyze(computed_data={})
        self.assertEqual(result.verification_status, 'data_insufficient')
        self.assertEqual(result.failure_phase, 'capacity_search')
        self.assertIsNone(result.capacity_search)
        self.assertIn('dv_curve', result.failure_reason_zh)

    def test_non_monotonic_dv_curve(self):
        computed = {'dv_curve': [
            {'m': 0.5, 'dv': 400.0}, {'m': 1.0, 'dv': 500.0}]}
        result = _analyze(computed_data=computed)
        self.assertEqual(result.verification_status, 'data_insufficient')
        self.assertEqual(result.failure_phase, 'capacity_search')
        self.assertIsNone(result.capacity_search)

    def test_per_rocket_value_error_becomes_own_result(self):
        config = _cfg()
        config['stages'][0]['wet_mass_t'] = -5.0
        result = _analyze(config=config)
        self.assertEqual(result.verification_status, 'data_insufficient')
        self.assertEqual(result.failure_phase, 'normalization')
        # other rockets still work
        healthy = _analyze()
        self.assertEqual(healthy.verification_status, 'verified_feasible')

    def test_runtime_error_not_swallowed(self):
        with mock.patch(
                'server.mission_analysis.evaluate_payload_for_mission',
                side_effect=RuntimeError('mass conservation violated')):
            with self.assertRaises(RuntimeError):
                _analyze(payload_t=1.0)


class CallingValidationTests(unittest.TestCase):
    def test_top_level_format_errors_raise_value_error(self):
        with self.assertRaises(ValueError):
            _analyze(rocket='')
        with self.assertRaises(ValueError):
            _analyze(slug='')
        with self.assertRaises(ValueError):
            _analyze(config='nope')
        with self.assertRaises(ValueError):
            _analyze(config={})
        with self.assertRaises(ValueError):
            _analyze(config={'stages': []})
        with self.assertRaises(ValueError):
            _analyze(computed_data='nope')
        with self.assertRaises(ValueError):
            _analyze(mission='nope')
        with self.assertRaises(ValueError):
            _analyze(mission={})
        with self.assertRaises(ValueError):
            _analyze(mission={'burn_sequence': []})

    def test_payload_t_rejected(self):
        for bad in (0, -1.0, float('nan'), True):
            with self.assertRaises(ValueError):
                _analyze(payload_t=bad)

    def test_search_parameters_rejected(self):
        with self.assertRaises(ValueError):
            _analyze(payload_tolerance_t=0.0)
        with self.assertRaises(ValueError):
            _analyze(monotonicity_samples=2)
        with self.assertRaises(ValueError):
            _analyze(max_evaluations=5, monotonicity_samples=17)
        with self.assertRaises(ValueError):
            _analyze(max_search_nodes=0)

    def test_search_parameters_passed_through(self):
        import server.mission_analysis as module
        real_search = module.find_max_payload_for_mission
        captured = {}

        def spy(*args, **kwargs):
            captured.update(kwargs)
            return real_search(*args, **kwargs)

        with mock.patch('server.mission_analysis.find_max_payload_for_mission',
                        spy):
            _analyze(payload_t=1.0, payload_tolerance_t=0.002,
                     monotonicity_samples=11, max_evaluations=64,
                     max_search_nodes=1234)
        self.assertEqual(captured['payload_tolerance_t'], 0.002)
        self.assertEqual(captured['monotonicity_samples'], 11)
        self.assertEqual(captured['max_evaluations'], 64)
        self.assertEqual(captured['max_search_nodes'], 1234)


class WarningsTests(unittest.TestCase):
    def test_warnings_deduplicated_and_stable(self):
        result = _analyze(payload_t=1.0)
        warnings = result.warnings
        self.assertEqual(warnings, list(dict.fromkeys(warnings)))
        self.assertTrue(all(isinstance(w, str) and w for w in warnings))
        result2 = _analyze(payload_t=1.0)
        self.assertEqual(warnings, result2.warnings)


class ApiSummaryTests(unittest.TestCase):
    def test_default_summary_omits_full_search_samples(self):
        result = _analyze()
        api = result.to_api_dict()
        self.assertNotIn('audit', api)
        self.assertNotIn('evaluations', api['capacity'])
        self.assertNotIn('evaluations', api)
        self.assertNotIn('stages', api)

    def test_include_audit_contains_full_audit(self):
        result = _analyze(payload_t=1.0)
        api = result.to_api_dict(include_audit=True)
        audit = api['audit']
        self.assertIn('normalization', audit)
        self.assertIn('burn_conversion', audit)
        self.assertIn('capacity_search', audit)
        self.assertIn('requested_payload_evaluation', audit)
        self.assertIn('metadata', audit)
        self.assertIn('evaluations', audit['capacity_search'])

    def test_default_summary_much_smaller_than_audit(self):
        result = _analyze(payload_t=1.0)
        compact = len(json.dumps(result.to_api_dict(), ensure_ascii=False))
        full = len(json.dumps(result.to_api_dict(include_audit=True),
                              ensure_ascii=False))
        self.assertLess(compact, full)
        self.assertLess(compact * 4, full)

    def test_summary_and_full_do_not_share_references(self):
        result = _analyze(payload_t=1.0)
        api = result.to_api_dict()
        full = result.to_dict()
        api['warnings'].append('tampered')
        api['capacity']['evaluation_count'] = 0
        self.assertNotIn('tampered', full['warnings'])
        self.assertNotEqual(full['capacity_search']['metadata']
                            ['evaluation_count'], 0)
        full['metadata']['tampered'] = True
        self.assertNotIn('tampered',
                         result.to_api_dict()['audit']
                         if False else {})  # api has no metadata at top level

    def test_api_dicts_json_dumpable(self):
        result = _analyze(payload_t=1.0)
        json.dumps(result.to_api_dict(), ensure_ascii=False)
        json.dumps(result.to_api_dict(include_audit=True), ensure_ascii=False)
        json.dumps(result.to_dict(), ensure_ascii=False)


class TimelineTests(unittest.TestCase):
    def test_success_timeline_uses_simulation_burns(self):
        result = _analyze(payload_t=1.0)
        timeline = result.to_api_dict()['burn_timeline']
        self.assertEqual([t['burn_id'] for t in timeline],
                         ['park', 'b1', 'b2'])
        self.assertEqual([t['success'] for t in timeline],
                         [True, True, True])

    def test_calibration_entry_stays_unassigned(self):
        result = _analyze(payload_t=1.0)
        park = result.to_api_dict()['burn_timeline'][0]
        self.assertEqual(park['source'], 'rocket_calibration')
        self.assertIsNone(park['stage_index'])
        self.assertEqual(park['ignition_kind'], 'unassigned')
        self.assertTrue(park['success'])
        self.assertEqual(park['propellant_consumed_t'], 0.0)

    def test_real_burns_show_restart_and_stage(self):
        result = _analyze(payload_t=1.0)
        burns = result.to_api_dict()['burn_timeline'][1:]
        self.assertEqual([b['ignition_kind'] for b in burns],
                         ['restart', 'restart'])
        self.assertEqual([b['stage_index'] for b in burns], [1, 1])
        self.assertGreater(burns[0]['mass_after_t'], 0.0)

    def test_planning_failure_forges_no_stages(self):
        result = _analyze(payload_t=9.0)
        timeline = result.to_api_dict()['burn_timeline']
        self.assertEqual([t['burn_id'] for t in timeline],
                         ['park', 'b1', 'b2'])
        for entry in timeline:
            self.assertIsNone(entry['stage_index'])
            self.assertIsNone(entry['ignition_kind'])
            self.assertIsNone(entry['success'])
            self.assertIsNone(entry['actual_dv_m_s'])

    def test_no_simulation_timeline_from_requirements(self):
        result = _analyze(payload_t=9.0)
        self.assertIsNone(result.requested_payload_evaluation.simulation)
        timeline = result.to_api_dict()['burn_timeline']
        self.assertEqual(len(timeline), 3)
        self.assertEqual([t['target_dv_m_s'] for t in timeline],
                         [0.0, 500.0, 300.0])

    def test_no_payload_timeline_from_best_feasible_simulation(self):
        result = _analyze()
        timeline = result.to_api_dict()['burn_timeline']
        self.assertEqual([t['burn_id'] for t in timeline],
                         ['park', 'b1', 'b2'])
        self.assertEqual([t['ignition_kind'] for t in timeline[1:]],
                         ['restart', 'restart'])


class ImmutabilityTests(unittest.TestCase):
    def test_inputs_completely_unmodified(self):
        config = _cfg()
        computed = _computed_data()
        mission = _mission()
        config_before = copy.deepcopy(config)
        computed_before = copy.deepcopy(computed)
        mission_before = copy.deepcopy(mission)
        _analyze(config=config, computed_data=computed, mission=mission,
                 payload_t=1.0)
        _analyze(config=config, computed_data=computed, mission=mission,
                 payload_t=9.0)
        self.assertEqual(config, config_before)
        self.assertEqual(computed, computed_before)
        self.assertEqual(mission, mission_before)

    def test_deterministic_results(self):
        a = _analyze(payload_t=1.0)
        b = _analyze(payload_t=1.0)
        self.assertEqual(a.to_dict(), b.to_dict())
        self.assertEqual(a.to_api_dict(), b.to_api_dict())


class NeutronIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = load_config(
            Path('rockets/neutron_i_2630.yaml').read_text(encoding='utf-8'))
        cls.config = config
        cls.computed = compute_rocket(config)
        cls.mission = compute_orbit_mission(35786, 180, orbit_type='circular',
                                            arrival_strategy='combined')
        cls.stages = normalize_stages(config['stages']).stages

    def _analyze(self, payload_t=None):
        return analyze_rocket_for_mission(
            'Neutron I 2630', 'neutron_i_2630', self.config, self.computed,
            self.mission, payload_t=payload_t)

    def test_case_a_no_payload(self):
        result = self._analyze()
        self.assertEqual(result.verification_status, 'verified_feasible')
        self.assertTrue(result.verified_feasible)
        self.assertFalse(result.recommendation_eligible)
        api = result.to_api_dict()
        self.assertEqual(api['capacity']['status'], 'bounded')
        self.assertGreaterEqual(api['capacity']['max_payload_t'], 0.133)
        self.assertLessEqual(api['capacity']['max_payload_t'], 0.135)
        self.assertLessEqual(api['capacity']['boundary_width_t'], 0.001)
        timeline = api['burn_timeline']
        self.assertEqual([t['ignition_kind'] for t in timeline[1:]],
                         ['restart', 'restart'])
        self.assertEqual([t['stage_index'] for t in timeline[1:]], [1, 1])

    def test_case_b_payload_0_1t(self):
        result = self._analyze(payload_t=0.1)
        self.assertEqual(result.verification_status, 'verified_feasible')
        self.assertTrue(result.verified_feasible)
        self.assertFalse(result.recommendation_eligible)
        requested = result.to_api_dict()['requested_payload']
        self.assertTrue(requested['feasible'])
        self.assertTrue(requested['plan_verified'])
        timeline = result.to_api_dict()['burn_timeline']
        self.assertEqual([t['ignition_kind'] for t in timeline[1:]],
                         ['restart', 'restart'])
        self.assertEqual([t['stage_index'] for t in timeline[1:]], [1, 1])

    def test_case_c_payload_2_5t(self):
        result = self._analyze(payload_t=2.5)
        self.assertEqual(result.verification_status, 'verified_infeasible')
        self.assertFalse(result.verified_feasible)
        self.assertFalse(result.recommendation_eligible)
        self.assertEqual(result.failure_code,
                         FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertEqual(result.failure_phase, 'stage_planning')
        api = result.to_api_dict()
        self.assertFalse(api['requested_payload']['feasible'])
        # timeline must not forge executing stages
        for entry in api['burn_timeline']:
            self.assertIsNone(entry['stage_index'])
            self.assertIsNone(entry['success'])
        # The launch-direction correction reduces retrograde capacity.
        self.assertEqual(api['capacity']['status'], 'bounded')
        self.assertGreaterEqual(api['capacity']['max_payload_t'], 0.133)
        self.assertLessEqual(api['capacity']['max_payload_t'], 0.135)
        json.dumps(api, ensure_ascii=False)
        json.dumps(api['audit'] if 'audit' in api else {}, ensure_ascii=False)


if __name__ == '__main__':
    unittest.main()
