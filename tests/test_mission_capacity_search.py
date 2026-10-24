import copy
import json
import math
import unittest
from pathlib import Path
from unittest import mock

from c3_mass.compute_c3_mass import compute_orbit_mission, compute_rocket
from c3_mass.mission_adapters import burn_sequence_to_requirements, normalize_stages
from c3_mass.mission_capacity_search import (
    MissionCapacityResult,
    find_max_payload_for_mission,
)
from c3_mass.mission_payload_evaluator import PayloadFeasibilityResult
from c3_mass.mission_simulation import (
    BurnRequirement,
    DataQuality,
    FailureCode,
    StageState,
)
from server.yaml_codec import load_config

G = 9.80665

CAL_LO, CAL_HI = 0.1, 1.0


def _stage(index, propellant=20.0, isp=300.0, multi=False, dry=5.0):
    return StageState(
        stage_index=index, name=f's{index}', wet_mass_t=dry + propellant,
        dry_mass_t=dry, propellant_remaining_t=propellant, isp_vac_s=isp,
        multi_ignition=multi, ignition_count=0, attached=True,
        parallel=False, metadata={'idx': index})


def _req(target, burn_id, order):
    return BurnRequirement(burn_id=burn_id, order=order,
                           target_dv_m_s=target, source='mission_model',
                           name=burn_id)


def _curve(points):
    return [{'m': m, 'dv': dv} for m, dv in points]


def _monotonic_curve():
    return _curve([(0.1, 900.0), (0.3, 800.0), (0.5, 700.0),
                   (0.7, 600.0), (0.9, 500.0), (1.0, 450.0)])


def _mock_feasible_result(payload):
    return PayloadFeasibilityResult(
        rocket_ref='R', payload_t=float(payload), feasible=True,
        failure_code=FailureCode.NONE, failure_reason_zh='',
        data_quality=DataQuality.ESTIMATED,
        calibrated_remaining_dv_m_s=100.0, total_vacuum_dv_m_s=200.0,
        ascent_required_dv_m_s=100.0, parking_replay=None, stage_plan=None,
        simulation=None, warnings=[],
        metadata={'plan_verified': True, 'failure_phase': None})


def _mock_infeasible_result(payload, code=FailureCode.PROPELLANT_INSUFFICIENT):
    return PayloadFeasibilityResult(
        rocket_ref='R', payload_t=float(payload), feasible=False,
        failure_code=code, failure_reason_zh='infeasible',
        data_quality=DataQuality.ESTIMATED,
        calibrated_remaining_dv_m_s=100.0, total_vacuum_dv_m_s=200.0,
        ascent_required_dv_m_s=100.0, parking_replay=None, stage_plan=None,
        simulation=None, warnings=[],
        metadata={'plan_verified': False, 'failure_phase': 'stage_planning'})


def _mock_unknown_result(payload):
    return PayloadFeasibilityResult(
        rocket_ref='R', payload_t=float(payload), feasible=False,
        failure_code=FailureCode.DATA_INSUFFICIENT,
        failure_reason_zh='数据不足', data_quality=DataQuality.INSUFFICIENT,
        calibrated_remaining_dv_m_s=None, total_vacuum_dv_m_s=None,
        ascent_required_dv_m_s=None, parking_replay=None, stage_plan=None,
        simulation=None, warnings=[],
        metadata={'plan_verified': False, 'failure_phase': 'parking_consistency'})


def _make_mock(threshold, data_band=None, call_log=None,
               max_search_nodes_seen=None):
    def mock_eval(rocket_ref, stages, requirements, dv_curve, payload_t,
                  data_quality=DataQuality.EXACT, max_search_nodes=10000):
        p = float(payload_t)
        if call_log is not None:
            call_log.append((p, max_search_nodes))
        if max_search_nodes_seen is not None:
            max_search_nodes_seen.append(max_search_nodes)
        if data_band is not None and data_band[0] < p < data_band[1]:
            return _mock_unknown_result(p)
        if p <= threshold:
            return _mock_feasible_result(p)
        return _mock_infeasible_result(p)
    return mock_eval


def _search(threshold, **kwargs):
    call_log = []
    with mock.patch('c3_mass.mission_capacity_search.evaluate_payload_for_mission',
                    _make_mock(threshold, call_log=call_log)):
        result = find_max_payload_for_mission(
            'R', [_stage(0), _stage(1)], [_req(100.0, 'b1', 1)],
            _monotonic_curve(), **kwargs)
    return result, call_log


class BoundedSearchTests(unittest.TestCase):
    def test_monotonic_threshold_gives_bounded(self):
        result, _ = _search(0.5)
        self.assertEqual(result.capacity_status, 'bounded')
        self.assertTrue(result.success)
        self.assertEqual(result.failure_code, FailureCode.NONE)
        self.assertLessEqual(result.max_payload_t, 0.5)
        self.assertGreaterEqual(result.max_payload_t, 0.5 - 0.001 - 1e-9)
        self.assertAlmostEqual(result.feasible_lower_bound_t,
                               result.max_payload_t, places=12)

    def test_max_payload_is_verified_feasible_lower_bound(self):
        result, call_log = _search(0.5)
        # max is a point that was actually evaluated and feasible
        evaluated = [p for p, _ in call_log]
        self.assertIn(result.max_payload_t, evaluated)
        self.assertTrue(result.best_feasible_evaluation.feasible)
        # max is never the final interval midpoint or the infeasible upper
        mid = (result.feasible_lower_bound_t
               + result.infeasible_upper_bound_t) / 2
        self.assertNotEqual(result.max_payload_t, mid)
        self.assertNotEqual(result.max_payload_t,
                            result.infeasible_upper_bound_t)

    def test_final_boundary_width_within_tolerance(self):
        result, _ = _search(0.5)
        width = result.infeasible_upper_bound_t - result.feasible_lower_bound_t
        self.assertLessEqual(width, 0.001)
        self.assertAlmostEqual(result.metadata['boundary_width_t'], width,
                               places=12)

    def test_boundary_results_are_the_verified_pair(self):
        result, _ = _search(0.5)
        self.assertTrue(result.best_feasible_evaluation.feasible)
        self.assertTrue(
            result.best_feasible_evaluation.metadata['plan_verified'])
        self.assertFalse(result.first_infeasible_evaluation.feasible)
        self.assertNotEqual(result.first_infeasible_evaluation.failure_code,
                            FailureCode.DATA_INSUFFICIENT)
        # both boundary payloads appear in the evaluations summary
        payloads = [e['payload_t'] for e in result.evaluations]
        self.assertIn(result.feasible_lower_bound_t, payloads)
        self.assertIn(result.infeasible_upper_bound_t, payloads)


class StatusTests(unittest.TestCase):
    def test_all_infeasible_below_calibration_range(self):
        result, _ = _search(0.0)
        self.assertEqual(result.capacity_status, 'below_calibration_range')
        self.assertFalse(result.success)
        self.assertIsNone(result.max_payload_t)
        self.assertIsNone(result.feasible_lower_bound_t)
        self.assertAlmostEqual(result.infeasible_upper_bound_t, CAL_LO,
                               places=9)
        self.assertEqual(result.first_infeasible_evaluation.feasible, False)

    def test_all_feasible_above_calibration_range(self):
        result, _ = _search(2.0)
        self.assertEqual(result.capacity_status, 'above_calibration_range')
        self.assertFalse(result.success)
        self.assertIsNone(result.max_payload_t)
        self.assertAlmostEqual(result.feasible_lower_bound_t, CAL_HI,
                               places=9)
        self.assertIsNone(result.infeasible_upper_bound_t)
        self.assertTrue(result.best_feasible_evaluation.feasible)

    def test_data_insufficient_during_sampling(self):
        # band spanning sampling points -> stop during the guard grid
        call_log = []
        with mock.patch('c3_mass.mission_capacity_search.evaluate_payload_for_mission',
                        _make_mock(0.5, data_band=(0.4, 0.6),
                                   call_log=call_log)):
            result = find_max_payload_for_mission(
                'R', [_stage(0), _stage(1)], [_req(100.0, 'b1', 1)],
                _monotonic_curve())
        self.assertEqual(result.capacity_status, 'data_insufficient')
        self.assertFalse(result.success)
        self.assertEqual(result.failure_code, FailureCode.DATA_INSUFFICIENT)
        self.assertIsNone(result.max_payload_t)
        self.assertFalse(result.metadata['monotonicity_checked'])

    def test_data_insufficient_during_bisection(self):
        # narrow band between two samples: only bisection mids hit it
        call_log = []
        with mock.patch('c3_mass.mission_capacity_search.evaluate_payload_for_mission',
                        _make_mock(0.5, data_band=(0.499, 0.5),
                                   call_log=call_log)):
            result = find_max_payload_for_mission(
                'R', [_stage(0), _stage(1)], [_req(100.0, 'b1', 1)],
                _monotonic_curve())
        self.assertEqual(result.capacity_status, 'data_insufficient')
        self.assertIsNone(result.max_payload_t)
        self.assertTrue(result.metadata['monotonicity_checked'])
        # bisection started: more evaluations than the sampling grid
        self.assertGreater(len(call_log), 17)

    def test_non_monotonic_detected_and_no_bisection(self):
        def mock_eval(rocket_ref, stages, requirements, dv_curve, payload_t,
                      data_quality=DataQuality.EXACT, max_search_nodes=10000):
            p = float(payload_t)
            if p <= 0.3 or p >= 0.7:
                return _mock_feasible_result(p)
            return _mock_infeasible_result(p)
        call_log = []
        with mock.patch('c3_mass.mission_capacity_search.evaluate_payload_for_mission',
                        mock_eval):
            result = find_max_payload_for_mission(
                'R', [_stage(0), _stage(1)], [_req(100.0, 'b1', 1)],
                _monotonic_curve())
            call_log = []
        self.assertEqual(result.capacity_status, 'non_monotonic')
        self.assertFalse(result.success)
        self.assertEqual(result.failure_code, FailureCode.DATA_INSUFFICIENT)
        self.assertIsNone(result.max_payload_t)
        # only the guard grid was evaluated: no bisection
        self.assertEqual(result.metadata['evaluation_count'], 17)
        self.assertTrue(any('反转' in w for w in result.warnings))

    def test_evaluation_limit(self):
        result, call_log = _search(0.5, max_evaluations=22)
        self.assertEqual(result.capacity_status, 'evaluation_limit')
        self.assertFalse(result.success)
        self.assertEqual(result.failure_code, FailureCode.DATA_INSUFFICIENT)
        self.assertIsNone(result.max_payload_t)
        self.assertIsNotNone(result.feasible_lower_bound_t)
        self.assertIsNotNone(result.infeasible_upper_bound_t)
        self.assertEqual(result.metadata['evaluation_count'], 22)
        width = (result.infeasible_upper_bound_t
                 - result.feasible_lower_bound_t)
        self.assertGreater(width, 0.001)


class CacheAndSummaryTests(unittest.TestCase):
    def test_same_payload_evaluated_only_once(self):
        result, call_log = _search(0.5)
        evaluated = [p for p, _ in call_log]
        self.assertEqual(len(evaluated), len(set(evaluated)))
        self.assertEqual(len(evaluated), result.metadata['evaluation_count'])

    def test_evaluations_sorted_ascending(self):
        result, _ = _search(0.5)
        payloads = [e['payload_t'] for e in result.evaluations]
        self.assertEqual(payloads, sorted(payloads))

    def test_evaluations_contain_only_summaries(self):
        result, _ = _search(0.5)
        for entry in result.evaluations:
            self.assertNotIn('parking_replay', entry)
            self.assertNotIn('stage_plan', entry)
            self.assertNotIn('simulation', entry)
            for key in ('payload_t', 'feasible', 'failure_code',
                        'failure_phase', 'data_quality',
                        'calibrated_remaining_dv_m_s',
                        'assigned_stage_indices'):
                self.assertIn(key, entry)

    def test_failure_phase_preserved_in_summaries(self):
        result, _ = _search(0.5)
        phases = {e['failure_phase'] for e in result.evaluations}
        self.assertIn('stage_planning', phases)

    def test_cache_hit_count_recorded(self):
        result, _ = _search(0.5)
        self.assertEqual(result.metadata['cache_hit_count'], 0)
        self.assertEqual(result.metadata['evaluation_count'],
                         len(result.evaluations))

    def test_max_search_nodes_passed_through(self):
        seen = []
        with mock.patch('c3_mass.mission_capacity_search.evaluate_payload_for_mission',
                        _make_mock(0.5, max_search_nodes_seen=seen)):
            find_max_payload_for_mission(
                'R', [_stage(0), _stage(1)], [_req(100.0, 'b1', 1)],
                _monotonic_curve(), max_search_nodes=1234)
        self.assertTrue(seen)
        self.assertTrue(all(v == 1234 for v in seen))


class ValidationAndSafetyTests(unittest.TestCase):
    def test_invalid_parameters_rejected(self):
        stages = [_stage(0), _stage(1)]
        reqs = [_req(100.0, 'b1', 1)]
        curve = _monotonic_curve()
        with self.assertRaises(ValueError):
            find_max_payload_for_mission('', stages, reqs, curve)
        with self.assertRaises(ValueError):
            find_max_payload_for_mission('R', stages, reqs, curve,
                                         payload_tolerance_t=0.0)
        with self.assertRaises(ValueError):
            find_max_payload_for_mission('R', stages, reqs, curve,
                                         payload_tolerance_t=-1.0)
        with self.assertRaises(ValueError):
            find_max_payload_for_mission('R', stages, reqs, curve,
                                         monotonicity_samples=2)
        with self.assertRaises(ValueError):
            find_max_payload_for_mission('R', stages, reqs, curve,
                                         monotonicity_samples=True)
        with self.assertRaises(ValueError):
            find_max_payload_for_mission('R', stages, reqs, curve,
                                         max_evaluations=0)
        with self.assertRaises(ValueError):
            find_max_payload_for_mission('R', stages, reqs, curve,
                                         max_evaluations=10,
                                         monotonicity_samples=17)

    def test_malformed_or_non_monotonic_curve_not_fixed(self):
        stages = [_stage(0), _stage(1)]
        reqs = [_req(100.0, 'b1', 1)]
        with self.assertRaises(ValueError):
            find_max_payload_for_mission('R', stages, reqs,
                                         _curve([(1.0, 500.0), (0.5, 600.0)]))
        with self.assertRaises(ValueError):
            find_max_payload_for_mission('R', stages, reqs,
                                         _curve([(0.5, 400.0), (1.0, 500.0)]))
        with self.assertRaises(ValueError):
            find_max_payload_for_mission('R', stages, reqs,
                                         _curve([(0.5, 500.0)]))

    def test_inputs_completely_unmodified(self):
        stages = [_stage(0), _stage(1)]
        reqs = [_req(100.0, 'b1', 1)]
        reqs[0].metadata = {'deep': {'a': [1, 2]}}
        curve = _monotonic_curve()
        stages_before = [s.to_dict() for s in stages]
        reqs_before = [r.to_dict() for r in reqs]
        curve_before = copy.deepcopy(curve)
        with mock.patch('c3_mass.mission_capacity_search.evaluate_payload_for_mission',
                        _make_mock(0.5)):
            find_max_payload_for_mission('R', stages, reqs, curve)
        self.assertEqual([s.to_dict() for s in stages], stages_before)
        self.assertEqual([r.to_dict() for r in reqs], reqs_before)
        self.assertEqual(curve, curve_before)

    def test_outputs_do_not_share_references(self):
        stages = [_stage(0), _stage(1)]
        reqs = [_req(100.0, 'b1', 1)]
        curve = _monotonic_curve()
        with mock.patch('c3_mass.mission_capacity_search.evaluate_payload_for_mission',
                        _make_mock(0.5)):
            result = find_max_payload_for_mission('R', stages, reqs, curve)
        result.metadata['stage_count'] = 99
        result.evaluations[0]['payload_t'] = 0.0
        self.assertEqual(stages[0].metadata, {'idx': 0})
        self.assertNotEqual(result.metadata['stage_count'], 0)

    def test_all_statuses_json_dumpable(self):
        scenarios = []
        for threshold, kwargs in [(0.5, {}), (0.0, {}), (2.0, {}),
                                  (0.5, {'max_evaluations': 20})]:
            with mock.patch(
                    'c3_mass.mission_capacity_search.evaluate_payload_for_mission',
                    _make_mock(threshold)):
                scenarios.append(find_max_payload_for_mission(
                    'R', [_stage(0), _stage(1)], [_req(100.0, 'b1', 1)],
                    _monotonic_curve(), **kwargs))
        with mock.patch('c3_mass.mission_capacity_search.evaluate_payload_for_mission',
                        _make_mock(0.5, data_band=(0.4, 0.6))):
            scenarios.append(find_max_payload_for_mission(
                'R', [_stage(0), _stage(1)], [_req(100.0, 'b1', 1)],
                _monotonic_curve()))
        for result in scenarios:
            self.assertIsInstance(result, MissionCapacityResult)
            json.dumps(result.to_dict(), ensure_ascii=False)

    def test_deterministic_results(self):
        def run():
            with mock.patch(
                    'c3_mass.mission_capacity_search.evaluate_payload_for_mission',
                    _make_mock(0.5)):
                return find_max_payload_for_mission(
                    'R', [_stage(0), _stage(1)], [_req(100.0, 'b1', 1)],
                    _monotonic_curve())
        self.assertEqual(run().to_dict(), run().to_dict())

    def test_bounded_quality_is_estimated(self):
        result, _ = _search(0.5)
        self.assertEqual(result.data_quality, DataQuality.ESTIMATED)
        self.assertLessEqual(
            _QUALITY_RANK[result.data_quality],
            _QUALITY_RANK[result.best_feasible_evaluation.data_quality])

    def test_non_bounded_quality_is_insufficient(self):
        for threshold in (0.0, 2.0):
            result, _ = _search(threshold)
            self.assertEqual(result.data_quality, DataQuality.INSUFFICIENT)


_QUALITY_RANK = {DataQuality.EXACT: 0, DataQuality.ESTIMATED: 1,
                 DataQuality.INSUFFICIENT: 2}


class RealEndToEndTests(unittest.TestCase):
    def test_synthetic_rocket_real_evaluator_bounded(self):
        # consistent monotonic curve: dv(m) = total_vac(m) - 3000
        def total_vac(m):
            return (300.0 * G * (-math.log1p(-20.0 / (m + 35.0)))
                    + 300.0 * G * (-math.log1p(-8.0 / (m + 10.0))))

        points = [(m, total_vac(m) - 3000.0)
                  for m in (0.5, 2.0, 4.0, 6.0, 8.0, 10.0)]
        curve = _curve(points)
        stages = [_stage(0, propellant=20.0, dry=5.0, multi=False),
                  _stage(1, propellant=8.0, dry=2.0, multi=True)]
        reqs = [BurnRequirement(burn_id='park', order=1, target_dv_m_s=0.0,
                                source='rocket_calibration', name='park'),
                _req(500.0, 'b1', 2), _req(300.0, 'b2', 3)]
        result = find_max_payload_for_mission('R', stages, reqs, curve)
        self.assertEqual(result.capacity_status, 'bounded')
        self.assertTrue(result.success)
        self.assertEqual(result.data_quality, DataQuality.ESTIMATED)
        self.assertGreaterEqual(result.max_payload_t, 6.0)
        self.assertLessEqual(result.max_payload_t, 7.5)
        width = (result.infeasible_upper_bound_t
                 - result.feasible_lower_bound_t)
        self.assertLessEqual(width, 0.001)
        self.assertTrue(result.best_feasible_evaluation.feasible)
        self.assertTrue(
            result.best_feasible_evaluation.metadata['plan_verified'])
        self.assertFalse(result.first_infeasible_evaluation.feasible)
        # both real burns executed by the upper stage
        self.assertEqual(
            result.best_feasible_evaluation.stage_plan.metadata[
                'assigned_stage_indices'], [1, 1])
        json.dumps(result.to_dict(), ensure_ascii=False)


@unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
class NeutronIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = load_config(
            Path('rockets/neutron_i_2630.yaml').read_text(encoding='utf-8'))
        cls.stages = normalize_stages(config['stages']).stages
        cls.dv_curve = compute_rocket(config)['dv_curve']
        mission = compute_orbit_mission(35786, 180, orbit_type='circular',
                                        arrival_strategy='combined')
        cls.requirements = burn_sequence_to_requirements(
            mission['burn_sequence']).requirements

    def test_neutron_max_payload_search(self):
        result = find_max_payload_for_mission(
            'Neutron I 2630', self.stages, self.requirements, self.dv_curve,
            payload_tolerance_t=0.001, monotonicity_samples=17,
            max_evaluations=128)
        self.assertEqual(result.capacity_status, 'bounded')
        self.assertTrue(result.success)
        self.assertEqual(result.failure_code, FailureCode.NONE)
        self.assertEqual(result.data_quality, DataQuality.ESTIMATED)
        # regression window for the current model (not a hardcoded answer in
        # business code; the search produced the value)
        self.assertGreaterEqual(result.max_payload_t, 0.501)
        self.assertLessEqual(result.max_payload_t, 0.503)
        width = (result.infeasible_upper_bound_t
                 - result.feasible_lower_bound_t)
        self.assertLessEqual(width, 0.001)
        self.assertAlmostEqual(result.max_payload_t,
                               result.feasible_lower_bound_t, places=12)
        # boundary pair audits
        best = result.best_feasible_evaluation
        first_bad = result.first_infeasible_evaluation
        self.assertTrue(best.feasible)
        self.assertTrue(best.metadata['plan_verified'])
        self.assertFalse(first_bad.feasible)
        self.assertNotEqual(first_bad.failure_code,
                            FailureCode.DATA_INSUFFICIENT)
        self.assertIn(first_bad.metadata['failure_phase'],
                      ('stage_planning', 'plan_verification'))
        # both real burns executed by the second stage at the best point
        self.assertEqual(best.stage_plan.metadata['assigned_stage_indices'],
                         [1, 1])
        # both boundary payloads are present in the evaluation summaries
        payloads = [e['payload_t'] for e in result.evaluations]
        self.assertIn(result.feasible_lower_bound_t, payloads)
        self.assertIn(result.infeasible_upper_bound_t, payloads)
        json.dumps(result.to_dict(), ensure_ascii=False)


if __name__ == '__main__':
    unittest.main()
