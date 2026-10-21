import copy
import json
import math
import unittest
from pathlib import Path

import numpy as np

from c3_mass.compute_c3_mass import compute_orbit_mission, compute_rocket
from c3_mass.mission_adapters import burn_sequence_to_requirements, normalize_stages
from c3_mass.mission_payload_evaluator import (
    PARKING_DV_CHECK_TOLERANCE_M_S,
    PayloadFeasibilityResult,
    compute_serial_stack_vacuum_dv,
    evaluate_payload_for_mission,
    interpolate_calibrated_remaining_dv,
)
from c3_mass.mission_simulation import (
    BurnRequirement,
    DataQuality,
    FailureCode,
    IgnitionKind,
    StageState,
)
from server.yaml_codec import load_config

G = 9.80665


def _stage(index, propellant=20.0, isp=300.0, multi=False, count=0,
           attached=True, parallel=False, dry=5.0, wet=None):
    if wet is None:
        wet = dry + propellant
    return StageState(
        stage_index=index,
        name=f'stage-{index}',
        wet_mass_t=wet,
        dry_mass_t=dry,
        propellant_remaining_t=propellant,
        isp_vac_s=isp,
        multi_ignition=multi,
        ignition_count=count,
        attached=attached,
        parallel=parallel,
        metadata={'idx': index},
    )


def _req(target, burn_id, order, source='mission_model'):
    return BurnRequirement(burn_id=burn_id, order=order,
                           target_dv_m_s=target, source=source, name=burn_id)


def _calib(burn_id='park', order=1):
    return BurnRequirement(burn_id=burn_id, order=order, target_dv_m_s=0.0,
                           source='rocket_calibration', name=burn_id)


def _curve(points):
    return [{'m': m, 'dv': dv} for m, dv in points]


def _stack():
    """Two-stage serial stack: s0 (prop 20, dry 5), s1 (prop 8, dry 2),
    payload 1 -> total vacuum dv ~6208 m/s."""
    return [_stage(0, propellant=20.0, dry=5.0, multi=False),
            _stage(1, propellant=8.0, dry=2.0, multi=True)]


class InterpolationTests(unittest.TestCase):
    CURVE = _curve([(1.0, 500.0), (2.0, 400.0), (3.0, 250.0)])

    def test_exact_point_hit(self):
        self.assertEqual(interpolate_calibrated_remaining_dv(1.0, self.CURVE),
                         500.0)
        self.assertEqual(interpolate_calibrated_remaining_dv(2.0, self.CURVE),
                         400.0)

    def test_linear_interpolation_between_points(self):
        self.assertAlmostEqual(
            interpolate_calibrated_remaining_dv(1.5, self.CURVE), 450.0,
            places=9)
        self.assertAlmostEqual(
            interpolate_calibrated_remaining_dv(2.5, self.CURVE), 325.0,
            places=9)

    def test_numpy_float64_converted_to_python_float(self):
        curve = [{'m': np.float64(1.0), 'dv': np.float64(500.0)},
                 {'m': np.float64(2.0), 'dv': np.float64(400.0)}]
        value = interpolate_calibrated_remaining_dv(np.float64(1.5), curve)
        self.assertIsInstance(value, float)
        self.assertNotIsInstance(value, np.float64)
        self.assertAlmostEqual(value, 450.0, places=9)

    def test_out_of_range_raises_no_clamping(self):
        with self.assertRaises(ValueError) as ctx:
            interpolate_calibrated_remaining_dv(0.5, self.CURVE)
        self.assertIn('calibration range', str(ctx.exception))
        with self.assertRaises(ValueError):
            interpolate_calibrated_remaining_dv(3.5, self.CURVE)

    def test_structural_errors_rejected(self):
        with self.assertRaises(ValueError):
            interpolate_calibrated_remaining_dv(1.0, [])
        with self.assertRaises(ValueError):
            interpolate_calibrated_remaining_dv(1.0, [{'m': 1.0, 'dv': 500.0}])
        with self.assertRaises(ValueError):
            interpolate_calibrated_remaining_dv(
                1.0, [{'m': 1.0}, {'m': 2.0, 'dv': 400.0}])
        with self.assertRaises(ValueError):
            interpolate_calibrated_remaining_dv(
                1.0, [{'m': 1.0, 'dv': 500.0}, {'m': 1.0, 'dv': 400.0}])
        with self.assertRaises(ValueError):
            interpolate_calibrated_remaining_dv(
                1.0, [{'m': 2.0, 'dv': 500.0}, {'m': 1.0, 'dv': 400.0}])
        with self.assertRaises(ValueError):
            interpolate_calibrated_remaining_dv(
                1.0, [{'m': float('nan'), 'dv': 500.0},
                      {'m': 2.0, 'dv': 400.0}])
        with self.assertRaises(ValueError):
            interpolate_calibrated_remaining_dv(
                1.0, [{'m': 1.0, 'dv': float('inf')},
                      {'m': 2.0, 'dv': 400.0}])
        with self.assertRaises(ValueError):
            interpolate_calibrated_remaining_dv(
                1.0, [{'m': -1.0, 'dv': 500.0}, {'m': 2.0, 'dv': 400.0}])

    def test_negative_dv_rejected(self):
        with self.assertRaises(ValueError):
            interpolate_calibrated_remaining_dv(
                1.0, [{'m': 1.0, 'dv': -1.0}, {'m': 2.0, 'dv': 400.0}])

    def test_non_monotonic_curve_rejected_by_pure_function(self):
        curve = _curve([(1.0, 400.0), (2.0, 500.0), (3.0, 250.0)])
        with self.assertRaises(ValueError):
            interpolate_calibrated_remaining_dv(1.5, curve)

    def test_inputs_not_modified(self):
        before = copy.deepcopy(self.CURVE)
        interpolate_calibrated_remaining_dv(1.5, self.CURVE)
        self.assertEqual(self.CURVE, before)


class StackVacuumDvTests(unittest.TestCase):
    def test_serial_stack_total_with_staging(self):
        stages = _stack()
        total = compute_serial_stack_vacuum_dv(stages, 1.0)
        expected_s0 = 300.0 * G * (-math.log1p(-20.0 / 36.0))
        expected_s1 = 300.0 * G * (-math.log1p(-8.0 / 11.0))
        self.assertAlmostEqual(total, expected_s0 + expected_s1, places=9)

    def test_detached_stages_excluded(self):
        stages = _stack()
        stages.append(_stage(2, propellant=50.0, dry=10.0, attached=False))
        total = compute_serial_stack_vacuum_dv(stages, 1.0)
        expected_s0 = 300.0 * G * (-math.log1p(-20.0 / 36.0))
        expected_s1 = 300.0 * G * (-math.log1p(-8.0 / 11.0))
        self.assertAlmostEqual(total, expected_s0 + expected_s1, places=9)

    def test_parallel_and_missing_isp_raise(self):
        with self.assertRaises(ValueError):
            compute_serial_stack_vacuum_dv([_stage(0, parallel=True)], 1.0)
        with self.assertRaises(ValueError):
            compute_serial_stack_vacuum_dv([_stage(0, isp=None)], 1.0)

    def test_inputs_not_modified(self):
        stages = _stack()
        before = [s.to_dict() for s in stages]
        compute_serial_stack_vacuum_dv(stages, 1.0)
        self.assertEqual([s.to_dict() for s in stages], before)


class CalibrationGateTests(unittest.TestCase):
    def test_out_of_range_payload_returns_data_insufficient(self):
        stages = _stack()
        reqs = [_req(100.0, 'b1', 1)]
        curve = _curve([(0.5, 700.0), (3.0, 100.0)])
        result = evaluate_payload_for_mission('R', stages, reqs, curve, 5.0)
        self.assertFalse(result.feasible)
        self.assertEqual(result.failure_code, FailureCode.DATA_INSUFFICIENT)
        self.assertEqual(result.metadata['failure_phase'],
                         'calibration_lookup')
        self.assertIn('5.0', result.failure_reason_zh)
        self.assertIn('0.5', result.failure_reason_zh)
        self.assertIn('3.0', result.failure_reason_zh)

    def test_non_monotonic_curve_returns_data_insufficient(self):
        stages = _stack()
        reqs = [_req(100.0, 'b1', 1)]
        curve = _curve([(0.5, 400.0), (1.0, 500.0), (3.0, 100.0)])
        result = evaluate_payload_for_mission('R', stages, reqs, curve, 1.0)
        self.assertFalse(result.feasible)
        self.assertEqual(result.failure_code, FailureCode.DATA_INSUFFICIENT)
        self.assertEqual(result.metadata['failure_phase'],
                         'calibration_lookup')
        self.assertIn('不可信', result.failure_reason_zh)
        self.assertIsNone(result.parking_replay)

    def test_calibrated_above_total_returns_data_insufficient(self):
        s0 = _stage(0, propellant=10.0, dry=5.0)
        reqs = [_req(100.0, 'b1', 1)]
        curve = _curve([(0.5, 4000.0), (1.0, 3000.0)])
        result = evaluate_payload_for_mission('R', [s0], reqs, curve, 1.0)
        self.assertFalse(result.feasible)
        self.assertEqual(result.failure_code, FailureCode.DATA_INSUFFICIENT)
        self.assertEqual(result.metadata['failure_phase'],
                         'calibration_lookup')
        self.assertIn('不一致', result.failure_reason_zh)


class PipelineFailureTests(unittest.TestCase):
    def test_parallel_stage_returns_data_insufficient(self):
        stages = [_stage(0, parallel=True)]
        reqs = [_req(100.0, 'b1', 1)]
        curve = _curve([(0.5, 100.0), (1.0, 50.0)])
        result = evaluate_payload_for_mission('R', stages, reqs, curve, 1.0)
        self.assertFalse(result.feasible)
        self.assertEqual(result.failure_code, FailureCode.DATA_INSUFFICIENT)
        self.assertEqual(result.metadata['failure_phase'], 'parking_replay')
        self.assertIn('数据不足', result.failure_reason_zh)
        self.assertIsNone(result.parking_replay)
        self.assertIsNone(result.stage_plan)
        self.assertIsNone(result.simulation)

    def test_restart_unsupported_propagated(self):
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=False)
        reqs = [_req(500.0, 'b1', 1), _req(300.0, 'b2', 2)]
        curve = _curve([(0.5, 3100.0), (1.0, 3000.0)])
        result = evaluate_payload_for_mission('R', [s0, s1], reqs, curve, 1.0)
        self.assertFalse(result.feasible)
        self.assertEqual(result.failure_code, FailureCode.RESTART_UNSUPPORTED)
        self.assertEqual(result.metadata['failure_phase'], 'stage_planning')
        self.assertIsNotNone(result.parking_replay)
        self.assertIsNotNone(result.stage_plan)
        self.assertIsNone(result.simulation)

    def test_propellant_insufficient_propagated(self):
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        reqs = [_req(2450.7, 'b1', 1), _req(1661.4, 'b2', 2)]
        curve = _curve([(0.5, 1600.0), (1.0, 1500.0)])
        result = evaluate_payload_for_mission('R', [s0, s1], reqs, curve, 1.0)
        self.assertFalse(result.feasible)
        self.assertEqual(result.failure_code,
                         FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertEqual(result.metadata['failure_phase'], 'stage_planning')
        self.assertIsNotNone(result.stage_plan)
        self.assertIsNone(result.simulation)

    def test_whole_burn_limitation_diagnosis_preserved(self):
        s0 = _stage(0, isp=150.0, propellant=0.5, dry=5.0, multi=True)
        s1 = _stage(1, isp=150.0, propellant=0.5, dry=2.0, multi=True)
        reqs = [_req(300.0, 'b1', 1)]
        curve = _curve([(0.5, 320.0), (1.0, 300.0)])
        result = evaluate_payload_for_mission('R', [s0, s1], reqs, curve, 1.0)
        self.assertFalse(result.feasible)
        self.assertEqual(result.failure_code,
                         FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertEqual(result.metadata['failure_phase'], 'stage_planning')
        self.assertIn('whole_burn_per_stage', result.failure_reason_zh)
        self.assertTrue(
            result.stage_plan.metadata['whole_burn_limitation_triggered'])


class SuccessPipelineTests(unittest.TestCase):
    def _evaluate(self, stages=None, reqs=None, curve=None, payload=1.0):
        if stages is None:
            stages = _stack()
        if reqs is None:
            reqs = [_calib('park', 1), _req(500.0, 'b1', 2),
                    _req(300.0, 'b2', 3)]
        if curve is None:
            curve = _curve([(0.5, 2200.0), (1.0, 2000.0)])
        return evaluate_payload_for_mission('R', stages, reqs, curve, payload)

    def test_success_runs_simulation_and_verifies(self):
        result = self._evaluate()
        self.assertTrue(result.feasible)
        self.assertEqual(result.failure_code, FailureCode.NONE)
        self.assertTrue(result.metadata['plan_verified'])
        self.assertIsNotNone(result.parking_replay)
        self.assertIsNotNone(result.stage_plan)
        self.assertIsNotNone(result.simulation)
        self.assertTrue(result.simulation.success)
        # executing stages match the plan
        self.assertEqual(result.stage_plan.metadata['assigned_stage_indices'],
                         [1, 1])
        for burn in result.simulation.burns:
            expected = {r.burn_id: r.preferred_stage_index
                        for r in result.stage_plan.requirements}[burn.burn_id]
            self.assertEqual(burn.stage_index, expected)
        # both post-parking burns are restarts of stage 1
        kinds = [b.ignition_kind for b in result.simulation.burns]
        self.assertEqual(kinds, [IgnitionKind.UNASSIGNED, IgnitionKind.RESTART,
                                 IgnitionKind.RESTART])
        self.assertIsNone(result.simulation.burns[0].stage_index)
        # final mass matches plan prediction
        self.assertAlmostEqual(
            result.simulation.metadata['final_vehicle_mass_t'],
            result.stage_plan.planned_final_vehicle_mass_t, places=9)
        # calibration entry stays unassigned

    def test_parking_self_check_passes_and_is_recorded(self):
        result = self._evaluate()
        check = result.metadata['parking_remaining_dv_check_m_s']
        error = result.metadata['parking_remaining_dv_error_m_s']
        self.assertIsNotNone(check)
        self.assertLessEqual(abs(error), PARKING_DV_CHECK_TOLERANCE_M_S)
        self.assertAlmostEqual(check, 2000.0, delta=0.01)

    def test_manufactured_parking_inconsistency_rejected(self):
        from c3_mass import mission_payload_evaluator as mpe
        # The parking self-check error is float noise (~1e-13); a patched
        # tolerance below that floor makes the consistency gate trip.
        original = mpe.PARKING_DV_CHECK_TOLERANCE_M_S
        try:
            mpe.PARKING_DV_CHECK_TOLERANCE_M_S = 1e-14
            result = self._evaluate()
            self.assertFalse(result.feasible)
            self.assertEqual(result.failure_code, FailureCode.DATA_INSUFFICIENT)
            self.assertEqual(result.metadata['failure_phase'],
                             'parking_consistency')
            self.assertIn('不一致', result.failure_reason_zh)
            self.assertIsNotNone(result.parking_replay)
            self.assertIsNone(result.stage_plan)
            self.assertIsNone(result.simulation)
        finally:
            mpe.PARKING_DV_CHECK_TOLERANCE_M_S = original

    def test_manufactured_plan_simulation_mismatch_rejected(self):
        from c3_mass import mission_payload_evaluator as mpe
        real_simulate = mpe.simulate_multistage_sequence

        def tampered(*args, **kwargs):
            states, mission = real_simulate(*args, **kwargs)
            mission.metadata['final_vehicle_mass_t'] += 0.5
            return states, mission

        mpe.simulate_multistage_sequence = tampered
        try:
            result = self._evaluate()
            self.assertFalse(result.feasible)
            self.assertEqual(result.failure_code, FailureCode.DATA_INSUFFICIENT)
            self.assertEqual(result.metadata['failure_phase'],
                             'plan_verification')
            self.assertFalse(result.metadata['plan_verified'])
            self.assertIn('不一致', result.failure_reason_zh)
            self.assertTrue(any('mismatch' in w or '不一致' in w
                                for w in result.warnings))
        finally:
            mpe.simulate_multistage_sequence = real_simulate


class QualityAndSafetyTests(unittest.TestCase):
    def test_quality_capped_at_estimated(self):
        result = evaluate_payload_for_mission(
            'R', _stack(), [_req(500.0, 'b1', 1)],
            _curve([(0.5, 2200.0), (1.0, 2000.0)]), 1.0,
            data_quality=DataQuality.EXACT)
        self.assertTrue(result.feasible)
        self.assertEqual(result.data_quality, DataQuality.ESTIMATED)
        result2 = evaluate_payload_for_mission(
            'R', _stack(), [_req(500.0, 'b1', 1)],
            _curve([(0.5, 2200.0), (1.0, 2000.0)]), 1.0,
            data_quality=DataQuality.ESTIMATED)
        self.assertEqual(result2.data_quality, DataQuality.ESTIMATED)
        self.assertTrue(any('校准 dv_curve' in w for w in result.warnings))

    def test_insufficient_input_skips_all_stages(self):
        result = evaluate_payload_for_mission(
            'R', _stack(), [_req(500.0, 'b1', 1)],
            _curve([(0.5, 2200.0), (1.0, 2000.0)]), 1.0,
            data_quality=DataQuality.INSUFFICIENT)
        self.assertFalse(result.feasible)
        self.assertEqual(result.failure_code, FailureCode.DATA_INSUFFICIENT)
        self.assertEqual(result.data_quality, DataQuality.INSUFFICIENT)
        self.assertIsNone(result.calibrated_remaining_dv_m_s)
        self.assertIsNone(result.parking_replay)
        self.assertIsNone(result.stage_plan)
        self.assertIsNone(result.simulation)
        self.assertIsNone(result.metadata['failure_phase'])

    def test_inputs_completely_unmodified(self):
        stages = _stack()
        reqs = [_req(500.0, 'b1', 1), _req(300.0, 'b2', 2)]
        reqs[0].metadata = {'deep': {'a': [1, 2]}}
        curve = _curve([(0.5, 2200.0), (1.0, 2000.0)])
        stages_before = [s.to_dict() for s in stages]
        reqs_before = [r.to_dict() for r in reqs]
        curve_before = copy.deepcopy(curve)
        evaluate_payload_for_mission('R', stages, reqs, curve, 1.0)
        evaluate_payload_for_mission('R', stages, reqs,
                                     _curve([(0.5, 4000.0), (1.0, 3000.0)]),
                                     1.0)
        self.assertEqual([s.to_dict() for s in stages], stages_before)
        self.assertEqual([r.to_dict() for r in reqs], reqs_before)
        self.assertEqual(curve, curve_before)

    def test_outputs_do_not_share_references(self):
        stages = _stack()
        result = evaluate_payload_for_mission(
            'R', stages, [_req(500.0, 'b1', 1)],
            _curve([(0.5, 2200.0), (1.0, 2000.0)]), 1.0)
        result.stage_plan.planned_final_stages[0].metadata['mutated'] = True
        result.metadata['stage_count'] = 99
        self.assertNotIn('mutated', stages[0].metadata)
        self.assertEqual(result.metadata['stage_count'], 99)
        self.assertEqual(stages[0].metadata, {'idx': 0})

    def test_all_outcomes_json_dumpable(self):
        scenarios = [
            evaluate_payload_for_mission(
                'R', _stack(), [_req(500.0, 'b1', 1)],
                _curve([(0.5, 2200.0), (1.0, 2000.0)]), 1.0),
            evaluate_payload_for_mission(
                'R', [_stage(0, propellant=1.0, dry=5.0)],
                [_req(2450.7, 'b1', 1)],
                _curve([(0.5, 3000.0), (1.0, 2500.0)]), 1.0),
            evaluate_payload_for_mission(
                'R', [_stage(0, parallel=True)], [_req(100.0, 'b1', 1)],
                _curve([(0.5, 100.0), (1.0, 50.0)]), 1.0),
            evaluate_payload_for_mission(
                'R', _stack(), [_req(100.0, 'b1', 1)],
                _curve([(0.5, 700.0), (3.0, 100.0)]), 5.0),
        ]
        for result in scenarios:
            self.assertIsInstance(result, PayloadFeasibilityResult)
            json.dumps(result.to_dict(), ensure_ascii=False)

    def test_deterministic_results(self):
        stages = _stack()
        reqs = [_req(500.0, 'b1', 1), _req(300.0, 'b2', 2)]
        curve = _curve([(0.5, 2200.0), (1.0, 2000.0)])
        r1 = evaluate_payload_for_mission('R', stages, reqs, curve, 1.0)
        r2 = evaluate_payload_for_mission('R', stages, reqs, curve, 1.0)
        self.assertEqual(r1.to_dict(), r2.to_dict())

    def test_invalid_arguments_rejected(self):
        stages = _stack()
        reqs = [_req(100.0, 'b1', 1)]
        curve = _curve([(0.5, 700.0), (1.0, 500.0)])
        with self.assertRaises(ValueError):
            evaluate_payload_for_mission('', stages, reqs, curve, 1.0)
        with self.assertRaises(ValueError):
            evaluate_payload_for_mission('R', [], reqs, curve, 1.0)
        with self.assertRaises(ValueError):
            evaluate_payload_for_mission('R', 'x', reqs, curve, 1.0)
        with self.assertRaises(ValueError):
            evaluate_payload_for_mission('R', stages, [], curve, 1.0)
        with self.assertRaises(ValueError):
            evaluate_payload_for_mission('R', stages,
                                         [_req(100.0, 'b1', 1),
                                          _req(100.0, 'b1', 2)], curve, 1.0)
        with self.assertRaises(ValueError):
            evaluate_payload_for_mission('R', stages,
                                         [_req(100.0, 'b1', 2),
                                          _req(100.0, 'b2', 1)], curve, 1.0)
        with self.assertRaises(ValueError):
            evaluate_payload_for_mission('R', stages,
                                         [_req(0.0, 'b1', 1)], curve, 1.0)
        with self.assertRaises(ValueError):
            evaluate_payload_for_mission('R', stages,
                                         [BurnRequirement(
                                             burn_id='b1', order=1,
                                             target_dv_m_s=100.0,
                                             source='mission_model',
                                             preferred_stage_index=0)],
                                         curve, 1.0)
        with self.assertRaises(ValueError):
            evaluate_payload_for_mission('R', stages, reqs, curve, -1.0)
        with self.assertRaises(ValueError):
            evaluate_payload_for_mission('R', stages, reqs, curve, 1.0,
                                         max_search_nodes=0)
        with self.assertRaises(ValueError):
            evaluate_payload_for_mission('R', stages, reqs, 'nope', 1.0)
        with self.assertRaises(ValueError):
            evaluate_payload_for_mission(
                'R', stages, reqs, [{'m': 1.0, 'dv': 500.0},
                                    {'m': 1.0, 'dv': 400.0}], 1.0)


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

    def test_feasible_0_2t(self):
        result = evaluate_payload_for_mission(
            'Neutron I 2630', self.stages, self.requirements, self.dv_curve,
            0.2)
        self.assertTrue(result.feasible)
        self.assertEqual(result.failure_code, FailureCode.NONE)
        self.assertEqual(result.data_quality, DataQuality.ESTIMATED)
        self.assertTrue(result.metadata['plan_verified'])
        # parking: second stage active
        self.assertEqual(result.parking_replay.active_stage_index, 1)
        self.assertEqual(result.stage_plan.metadata['assigned_stage_indices'],
                         [1, 1])
        self.assertEqual(result.stage_plan.separations_before, {})
        # both real burns are restarts of the second stage
        burns = result.simulation.burns
        self.assertEqual([b.ignition_kind for b in burns],
                         [IgnitionKind.UNASSIGNED, IgnitionKind.RESTART,
                          IgnitionKind.RESTART])
        self.assertEqual([b.stage_index for b in burns],
                         [None, 1, 1])
        self.assertIsNone(burns[0].stage_index)
        # parking self-check within tolerance
        self.assertLessEqual(
            abs(result.metadata['parking_remaining_dv_error_m_s']),
            PARKING_DV_CHECK_TOLERANCE_M_S)
        json.dumps(result.to_dict(), ensure_ascii=False)

    def test_infeasible_2_5t(self):
        result = evaluate_payload_for_mission(
            'Neutron I 2630', self.stages, self.requirements, self.dv_curve,
            2.5)
        self.assertFalse(result.feasible)
        self.assertEqual(result.failure_code,
                         FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertEqual(result.metadata['failure_phase'], 'stage_planning')
        # calibrated remaining ~1581 m/s
        self.assertAlmostEqual(result.calibrated_remaining_dv_m_s, 1581.0,
                               delta=5.0)
        self.assertIsNotNone(result.parking_replay)
        self.assertIsNotNone(result.stage_plan)
        self.assertFalse(result.stage_plan.success)
        self.assertEqual(result.stage_plan.metadata['assigned_stage_indices'],
                         [])
        self.assertIsNone(result.simulation)
        self.assertIn('2450.7', result.failure_reason_zh)
        self.assertIn('orbit_burn_2', result.failure_reason_zh)
        json.dumps(result.to_dict(), ensure_ascii=False)


class VerificationCompletenessTests(unittest.TestCase):
    """Regression tests: _verify_simulation must catch extra/missing stages,
    extra/missing burns, replaced burn_ids and wrong burn order."""

    def _run_with_tampered_simulation(self, tamper):
        from c3_mass import mission_payload_evaluator as mpe
        real_simulate = mpe.simulate_multistage_sequence

        def wrapper(*args, **kwargs):
            states, mission = real_simulate(*args, **kwargs)
            return tamper(states, mission)

        mpe.simulate_multistage_sequence = wrapper
        try:
            return evaluate_payload_for_mission(
                'R', _stack(), [_calib('park', 1), _req(500.0, 'b1', 2),
                                _req(300.0, 'b2', 3)],
                _curve([(0.5, 2200.0), (1.0, 2000.0)]), 1.0)
        finally:
            mpe.simulate_multistage_sequence = real_simulate

    def assert_verification_failure(self, result):
        self.assertFalse(result.feasible)
        self.assertEqual(result.failure_code, FailureCode.DATA_INSUFFICIENT)
        self.assertEqual(result.metadata['failure_phase'],
                         'plan_verification')
        self.assertFalse(result.metadata['plan_verified'])
        self.assertIn('不一致', result.failure_reason_zh)

    def test_missing_stage_detected(self):
        result = self._run_with_tampered_simulation(
            lambda states, mission: (states[:-1], mission))
        self.assert_verification_failure(result)
        self.assertTrue(any('length mismatch' in w or '长度' in w
                            for w in result.warnings))

    def test_extra_stage_detected(self):
        result = self._run_with_tampered_simulation(
            lambda states, mission: (states + [states[-1]], mission))
        self.assert_verification_failure(result)

    def test_missing_burn_detected(self):
        def tamper(states, mission):
            mission.burns = mission.burns[:-1]
            return states, mission
        result = self._run_with_tampered_simulation(tamper)
        self.assert_verification_failure(result)
        self.assertTrue(any('burn count' in w for w in result.warnings))

    def test_extra_burn_detected(self):
        def tamper(states, mission):
            mission.burns = mission.burns + [mission.burns[0]]
            return states, mission
        result = self._run_with_tampered_simulation(tamper)
        self.assert_verification_failure(result)

    def test_replaced_burn_id_detected(self):
        def tamper(states, mission):
            mission.burns[1].burn_id = 'tampered'
            return states, mission
        result = self._run_with_tampered_simulation(tamper)
        self.assert_verification_failure(result)
        self.assertTrue(any('id mismatch' in w for w in result.warnings))

    def test_reordered_burns_detected(self):
        def tamper(states, mission):
            mission.burns = [mission.burns[1], mission.burns[0],
                             mission.burns[2]]
            return states, mission
        result = self._run_with_tampered_simulation(tamper)
        self.assert_verification_failure(result)
        self.assertTrue(any('id mismatch' in w for w in result.warnings))

    def test_wrong_burn_order_detected(self):
        def tamper(states, mission):
            mission.burns[1].order = 99
            return states, mission
        result = self._run_with_tampered_simulation(tamper)
        self.assert_verification_failure(result)
        self.assertTrue(any('order mismatch' in w for w in result.warnings))

    def test_swapped_stage_indices_detected(self):
        def tamper(states, mission):
            states[0], states[1] = states[1], states[0]
            return states, mission
        result = self._run_with_tampered_simulation(tamper)
        self.assert_verification_failure(result)


if __name__ == '__main__':
    unittest.main()
