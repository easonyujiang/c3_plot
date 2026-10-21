import json
import math
import unittest

from c3_mass.mission_simulation import (
    BurnRequirement,
    DataQuality,
    FailureCode,
    IgnitionKind,
    StageState,
)
from c3_mass.mission_solver import (
    MASS_TOLERANCE_T,
    execute_burn,
    simulate_stage_sequence,
)


def _stage(propellant=20.0, isp=300.0, multi=True, count=0, attached=True,
           parallel=False, dry=5.0, wet=25.0, index=1):
    return StageState(
        stage_index=index,
        name='upper',
        wet_mass_t=wet,
        dry_mass_t=dry,
        propellant_remaining_t=propellant,
        isp_vac_s=isp,
        multi_ignition=multi,
        ignition_count=count,
        attached=attached,
        parallel=parallel,
        metadata={'role': 'upper'},
    )


def _req(target=400.0, burn_id='b1', source='mission_model', order=1,
         preferred=None, name=None):
    return BurnRequirement(
        burn_id=burn_id, order=order, target_dv_m_s=target,
        source=source, name=name or burn_id,
        preferred_stage_index=preferred,
    )


def _calib(burn_id='park', order=1):
    return _req(0.0, burn_id=burn_id, order=order,
                source='rocket_calibration', name='进入停泊轨道')


class SequenceValidationTests(unittest.TestCase):
    def test_stage_type_error_is_value_error_not_attribute_error(self):
        req = _req(300.0)
        with self.assertRaises(ValueError) as ctx:
            simulate_stage_sequence('R', {}, [req], 100.0, 1.0)
        self.assertIn('StageState', str(ctx.exception))

    def test_empty_requirements_rejected(self):
        stage = _stage()
        with self.assertRaises(ValueError):
            simulate_stage_sequence('R', stage, [], 100.0, 1.0)
        with self.assertRaises(ValueError):
            simulate_stage_sequence('R', stage, (), 100.0, 1.0)

    def test_duplicate_burn_id_rejected(self):
        stage = _stage()
        reqs = [_req(300.0, burn_id='b1'), _req(200.0, burn_id='b1')]
        with self.assertRaises(ValueError) as ctx:
            simulate_stage_sequence('R', stage, reqs, 100.0, 1.0)
        self.assertIn('duplicate burn_id', str(ctx.exception))

    def test_order_must_be_strictly_increasing(self):
        stage = _stage()
        reqs = [_req(300.0, burn_id='b1', order=1),
                _req(200.0, burn_id='b2', order=1)]
        with self.assertRaises(ValueError):
            simulate_stage_sequence('R', stage, reqs, 100.0, 1.0)
        reqs = [_req(300.0, burn_id='b1', order=2),
                _req(200.0, burn_id='b2', order=1)]
        with self.assertRaises(ValueError):
            simulate_stage_sequence('R', stage, reqs, 100.0, 1.0)

    def test_non_burn_requirement_element_rejected(self):
        stage = _stage()
        with self.assertRaises(ValueError):
            simulate_stage_sequence('R', stage, [_req(300.0), {'burn_id': 'x'}],
                                    100.0, 1.0)
        with self.assertRaises(ValueError):
            simulate_stage_sequence('R', stage, 'b1b2', 100.0, 1.0)

    def test_payload_exceeding_vehicle_rejected(self):
        stage = _stage()
        with self.assertRaises(ValueError):
            simulate_stage_sequence('R', stage, [_req(300.0)], 100.0, 101.0)
        with self.assertRaises(ValueError):
            simulate_stage_sequence('R', stage, [_req(300.0)], 100.0, -1.0)
        with self.assertRaises(ValueError):
            simulate_stage_sequence('R', stage, [_req(300.0)], 0.0, 1.0)

    def test_invalid_rocket_ref_rejected(self):
        stage = _stage()
        with self.assertRaises(ValueError):
            simulate_stage_sequence('', stage, [_req(300.0)], 100.0, 1.0)


class SequenceExecutionTests(unittest.TestCase):
    def test_two_burns_use_progressive_mass_and_propellant(self):
        stage = _stage(propellant=20.0, isp=300.0, multi=True)
        reqs = [_req(400.0, burn_id='b1', order=1), _req(200.0, burn_id='b2', order=2)]
        final, mission = simulate_stage_sequence('R', stage, reqs, 100.0, 1.0)
        self.assertTrue(mission.success)
        self.assertEqual(mission.failure_code, FailureCode.NONE)
        self.assertEqual(len(mission.burns), 2)
        b1, b2 = mission.burns
        self.assertEqual(b1.ignition_kind, IgnitionKind.FIRST)
        self.assertEqual(b2.ignition_kind, IgnitionKind.RESTART)
        self.assertEqual(final.ignition_count, 2)
        expected1 = 100.0 * (1.0 - math.exp(-400.0 / (300.0 * 9.80665)))
        m2 = 100.0 - expected1
        expected2 = m2 * (1.0 - math.exp(-200.0 / (300.0 * 9.80665)))
        self.assertAlmostEqual(b2.mass_before_t, m2, places=9)
        self.assertAlmostEqual(b2.propellant_consumed_t, expected2, places=9)
        self.assertAlmostEqual(
            final.propellant_remaining_t,
            20.0 - expected1 - expected2, places=9)
        self.assertAlmostEqual(
            b2.propellant_remaining_t,
            20.0 - expected1 - expected2, places=9)
        self.assertEqual(mission.metadata['initial_vehicle_mass_t'], 100.0)
        self.assertAlmostEqual(mission.metadata['final_vehicle_mass_t'],
                               100.0 - expected1 - expected2, places=9)
        self.assertEqual(mission.metadata['total_requirement_count'], 2)
        self.assertEqual(mission.metadata['completed_burn_count'], 2)
        self.assertIsNone(mission.metadata['stopped_at_burn_id'])
        self.assertEqual(mission.metadata['executing_stage_index'], 1)

    def test_calibration_item_does_not_consume_or_ignite(self):
        stage = _stage(propellant=20.0, isp=300.0, multi=True)
        reqs = [_calib(burn_id='park', order=1), _req(400.0, burn_id='b2', order=2)]
        final, mission = simulate_stage_sequence('R', stage, reqs, 100.0, 1.0)
        self.assertTrue(mission.success)
        park = mission.burns[0]
        self.assertTrue(park.success)
        self.assertEqual(park.ignition_kind, IgnitionKind.UNASSIGNED)
        self.assertIsNone(park.stage_index)
        self.assertIsNone(park.actual_dv_m_s)
        self.assertEqual(park.propellant_consumed_t, 0.0)
        self.assertIsNone(park.propellant_remaining_t)
        self.assertEqual(park.mass_before_t, 100.0)
        self.assertEqual(park.mass_after_t, 100.0)
        # real burn after calibration still sees the untouched stage
        self.assertEqual(mission.burns[1].ignition_kind, IgnitionKind.FIRST)
        self.assertEqual(mission.burns[1].mass_before_t, 100.0)
        self.assertEqual(final.ignition_count, 1)
        expected = 100.0 * (1.0 - math.exp(-400.0 / (300.0 * 9.80665)))
        self.assertAlmostEqual(final.propellant_remaining_t, 20.0 - expected,
                               places=9)

    def test_calibration_item_does_not_make_restart_of_fresh_stage(self):
        stage = _stage(propellant=20.0, isp=300.0, multi=True, count=1)
        reqs = [_calib(burn_id='park', order=1), _req(200.0, burn_id='b2', order=2)]
        final, mission = simulate_stage_sequence('R', stage, reqs, 100.0, 1.0)
        self.assertTrue(mission.success)
        self.assertEqual(mission.burns[1].ignition_kind, IgnitionKind.RESTART)
        self.assertEqual(final.ignition_count, 2)

    def test_calibration_only_sequence_succeeds_without_touching_stage(self):
        stage = _stage(propellant=20.0, isp=300.0)
        final, mission = simulate_stage_sequence(
            'R', stage, [_calib(burn_id='park')], 100.0, 1.0)
        self.assertTrue(mission.success)
        self.assertEqual(final.ignition_count, 0)
        self.assertEqual(final.propellant_remaining_t, 20.0)
        self.assertIsNot(final, stage)

    def test_restart_unsupported_fails_and_stops(self):
        stage = _stage(propellant=20.0, isp=300.0, multi=False, count=1)
        reqs = [_req(300.0, burn_id='b1', order=1), _req(300.0, burn_id='b2', order=2)]
        final, mission = simulate_stage_sequence('R', stage, reqs, 100.0, 1.0)
        self.assertFalse(mission.success)
        self.assertEqual(mission.failure_code, FailureCode.RESTART_UNSUPPORTED)
        self.assertTrue(mission.failure_reason_zh)
        self.assertEqual(len(mission.burns), 1)
        self.assertEqual(mission.metadata['completed_burn_count'], 1)
        self.assertEqual(mission.metadata['stopped_at_burn_id'], 'b1')
        self.assertFalse(mission.burns[0].success)
        self.assertEqual(final.ignition_count, 1)
        self.assertEqual(final.propellant_remaining_t, 20.0)

    def test_propellant_insufficient_keeps_exhausted_state_and_stops(self):
        stage = _stage(propellant=5.0, isp=300.0)
        reqs = [_req(800.0, burn_id='b1', order=1), _req(200.0, burn_id='b2', order=2)]
        final, mission = simulate_stage_sequence('R', stage, reqs, 100.0, 1.0)
        self.assertFalse(mission.success)
        self.assertEqual(mission.failure_code, FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertEqual(len(mission.burns), 1)
        self.assertEqual(mission.metadata['stopped_at_burn_id'], 'b1')
        self.assertEqual(final.propellant_remaining_t, 0.0)
        self.assertEqual(final.ignition_count, 1)
        expected_actual = 300.0 * 9.80665 * math.log(100.0 / 95.0)
        self.assertAlmostEqual(mission.burns[0].actual_dv_m_s,
                               expected_actual, places=9)
        self.assertAlmostEqual(mission.metadata['final_vehicle_mass_t'], 95.0,
                               places=9)

    def test_preferred_stage_index_match_succeeds(self):
        stage = _stage(index=1)
        reqs = [_req(400.0, burn_id='b1', preferred=1)]
        final, mission = simulate_stage_sequence('R', stage, reqs, 100.0, 1.0)
        self.assertTrue(mission.success)
        self.assertEqual(mission.burns[0].stage_index, 1)

    def test_preferred_stage_index_mismatch_returns_no_stage_assigned(self):
        stage = _stage(index=1)
        reqs = [_req(400.0, burn_id='b1', order=1, preferred=2),
                _req(100.0, burn_id='b2', order=2)]
        final, mission = simulate_stage_sequence('R', stage, reqs, 100.0, 1.0)
        self.assertFalse(mission.success)
        self.assertEqual(mission.failure_code, FailureCode.NO_STAGE_ASSIGNED)
        self.assertEqual(len(mission.burns), 1)
        self.assertEqual(mission.metadata['stopped_at_burn_id'], 'b1')
        burn = mission.burns[0]
        self.assertFalse(burn.success)
        self.assertEqual(burn.ignition_kind, IgnitionKind.UNASSIGNED)
        self.assertIsNone(burn.stage_index)
        self.assertEqual(burn.actual_dv_m_s, 0.0)
        self.assertEqual(burn.propellant_consumed_t, 0.0)
        self.assertEqual(burn.mass_before_t, 100.0)
        self.assertEqual(burn.mass_after_t, 100.0)
        self.assertEqual(final.ignition_count, 0)
        self.assertEqual(final.propellant_remaining_t, 20.0)

    def test_first_failure_returns_new_stage_instance(self):
        cases = [
            (_stage(multi=False, count=1), [_req(300.0, burn_id='b1')]),
            (_stage(isp=None), [_req(300.0, burn_id='b1')]),
            (_stage(attached=False), [_req(300.0, burn_id='b1')]),
            (_stage(parallel=True), [_req(300.0, burn_id='b1')]),
            (_stage(), [_req(300.0, burn_id='b1', preferred=9)]),
            (_stage(propellant=1.0), [_req(5000.0, burn_id='b1')]),
        ]
        for stage, reqs in cases:
            final, mission = simulate_stage_sequence('R', stage, reqs,
                                                     100.0, 1.0)
            self.assertIsNot(final, stage)
            self.assertFalse(mission.success)


class SequenceQualityTests(unittest.TestCase):
    def test_data_quality_uses_worst_rank(self):
        stage = _stage()
        _, mission = simulate_stage_sequence('R', stage, [_req(400.0)], 100.0,
                                             1.0, data_quality=DataQuality.EXACT)
        self.assertEqual(mission.data_quality, DataQuality.EXACT)
        _, mission = simulate_stage_sequence('R', stage, [_req(400.0)], 100.0,
                                             1.0, data_quality=DataQuality.ESTIMATED)
        self.assertEqual(mission.data_quality, DataQuality.ESTIMATED)
        # missing isp forces INSUFFICIENT on the burn regardless of the
        # passed quality, so the mission must aggregate to INSUFFICIENT.
        _, mission = simulate_stage_sequence('R', _stage(isp=None),
                                             [_req(400.0)], 100.0, 1.0,
                                             data_quality=DataQuality.EXACT)
        self.assertEqual(mission.data_quality, DataQuality.INSUFFICIENT)

    def test_burn_context_metadata_present(self):
        req = _req(400.0, burn_id='b1', name='GTO 转移点火')
        req.metadata = {'tag': 'mission-7', 'deep': {'a': 1}}
        final, mission = simulate_stage_sequence('R', _stage(), [req],
                                                 100.0, 1.0)
        ctx = mission.burns[0].metadata
        self.assertEqual(ctx['source'], 'mission_model')
        self.assertEqual(ctx['name'], 'GTO 转移点火')
        self.assertEqual(ctx['requirement_metadata'],
                         {'tag': 'mission-7', 'deep': {'a': 1}})

    def test_result_metadata_does_not_share_with_requirement(self):
        req = _req(400.0, burn_id='b1')
        req.metadata = {'list': [1, 2]}
        final, mission = simulate_stage_sequence('R', _stage(), [req],
                                                 100.0, 1.0)
        ctx = mission.burns[0].metadata['requirement_metadata']
        ctx['list'].append(99)
        ctx['extra'] = 1
        self.assertEqual(req.metadata, {'list': [1, 2]})

    def test_results_json_dumpable(self):
        stage = _stage(propellant=5.0, isp=300.0)
        reqs = [_calib(burn_id='park', order=1),
                _req(800.0, burn_id='b1', order=2),
                _req(200.0, burn_id='b2', order=3)]
        final, mission = simulate_stage_sequence('R', stage, reqs, 100.0, 1.0)
        json.dumps(final.to_dict(), ensure_ascii=False)
        json.dumps(mission.to_dict(), ensure_ascii=False)


class SequenceImmutabilityTests(unittest.TestCase):
    def test_inputs_completely_unmodified(self):
        stage = _stage(propellant=20.0, isp=300.0, multi=True)
        req1 = _req(400.0, burn_id='b1', order=1)
        req2 = _req(200.0, burn_id='b2', order=2)
        req1.metadata = {'m': [1, 2]}
        req2.metadata = {'m': [3]}
        stage_before = stage.to_dict()
        reqs_before = [req1.to_dict(), req2.to_dict()]
        simulate_stage_sequence('R', stage, [req1, req2], 100.0, 1.0)
        simulate_stage_sequence('R', _stage(propellant=1.0),
                                [_req(9000.0)], 100.0, 1.0)
        self.assertEqual(stage.to_dict(), stage_before)
        self.assertEqual(req1.to_dict(), reqs_before[0])
        self.assertEqual(req2.to_dict(), reqs_before[1])

    def test_success_mass_continuity(self):
        stage = _stage(propellant=20.0, isp=300.0, multi=True)
        reqs = [_req(200.0, burn_id='b1', order=1), _req(200.0, burn_id='b2', order=2),
                _req(200.0, burn_id='b3', order=3)]
        final, mission = simulate_stage_sequence('R', stage, reqs, 100.0, 1.0)
        self.assertTrue(mission.success)
        self.assertEqual(mission.metadata['initial_vehicle_mass_t'], 100.0)
        previous_after = 100.0
        for burn in mission.burns:
            self.assertAlmostEqual(burn.mass_before_t, previous_after, places=9)
            previous_after = burn.mass_after_t
        self.assertAlmostEqual(mission.metadata['final_vehicle_mass_t'],
                               previous_after, places=9)
        self.assertEqual(final.ignition_count, 3)


class SequenceInitialMassTests(unittest.TestCase):
    def test_impossible_mass_rejected_even_for_calibration_only(self):
        # stage current mass = dry 5 + propellant 7 = 12 t, payload 1 t,
        # vehicle only 5 t: must be rejected up front.
        stage = _stage(dry=5.0, propellant=7.0, wet=22.0)
        with self.assertRaises(ValueError) as ctx:
            simulate_stage_sequence('R', stage, [_calib(burn_id='park')],
                                    5.0, 1.0)
        msg = str(ctx.exception)
        self.assertIn('5.0', msg)
        self.assertIn('12.0', msg)
        self.assertIn('1.0', msg)
        self.assertIn('13.0', msg)

    def test_vehicle_below_stage_plus_payload_rejected(self):
        # stage mass 12 t, payload 5 t: minimum total is 17 t, vehicle 13 t.
        stage = _stage(dry=5.0, propellant=7.0, wet=22.0)
        with self.assertRaises(ValueError):
            simulate_stage_sequence('R', stage, [_req(200.0, burn_id='b1')],
                                    13.0, 5.0)

    def test_exact_minimum_accepted(self):
        stage = _stage(dry=5.0, propellant=7.0, wet=22.0)
        final, mission = simulate_stage_sequence(
            'R', stage, [_req(200.0, burn_id='b1')], 17.0, 5.0)
        self.assertTrue(mission.success)
        self.assertIsNot(final, stage)

    def test_extra_mass_accepted(self):
        # 20 t vehicle = 12 t stage + 5 t payload + 3 t other structure.
        stage = _stage(dry=5.0, propellant=7.0, wet=22.0)
        final, mission = simulate_stage_sequence(
            'R', stage, [_req(200.0, burn_id='b1')], 20.0, 5.0)
        self.assertTrue(mission.success)
        self.assertAlmostEqual(mission.metadata['initial_vehicle_mass_t'],
                               20.0, places=9)

    def test_detached_stage_excluded_from_minimum(self):
        # detached stage mass is not part of the vehicle lower bound, so a
        # calibration-only sequence still passes as a pure record.
        stage = _stage(dry=5.0, propellant=7.0, wet=22.0, attached=False)
        final, mission = simulate_stage_sequence('R', stage,
                                                 [_calib(burn_id='park')],
                                                 5.0, 1.0)
        self.assertTrue(mission.success)
        self.assertIsNot(final, stage)

    def test_rejected_call_does_not_modify_inputs(self):
        stage = _stage(dry=5.0, propellant=7.0, wet=22.0)
        reqs = [_calib(burn_id='park')]
        before_stage = stage.to_dict()
        before_reqs = [r.to_dict() for r in reqs]
        with self.assertRaises(ValueError):
            simulate_stage_sequence('R', stage, reqs, 5.0, 1.0)
        self.assertEqual(stage.to_dict(), before_stage)
        self.assertEqual([r.to_dict() for r in reqs], before_reqs)


if __name__ == '__main__':
    unittest.main()
