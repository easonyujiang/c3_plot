import copy
import json
import math
import unittest

from mission_simulation import (
    BurnRequirement,
    DataQuality,
    FailureCode,
    IgnitionKind,
    StageState,
)
from mission_solver import simulate_multistage_sequence

G = 9.80665


def _stage(index, propellant=20.0, isp=300.0, multi=False, count=0,
           attached=True, parallel=False, dry=5.0, wet=None):
    if wet is None:
        wet = dry + propellant + 10.0
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


def _req(target, burn_id, order, preferred=None, source='mission_model', name=None):
    return BurnRequirement(
        burn_id=burn_id, order=order, target_dv_m_s=target,
        source=source, name=name or burn_id,
        preferred_stage_index=preferred,
    )


def _calib(burn_id, order):
    return _req(0.0, burn_id=burn_id, order=order, source='rocket_calibration',
                name='进入停泊轨道')


def _two_stage_vehicle():
    """Stage 0: dry 5 + prop 20 (mass 25). Stage 1: dry 2 + prop 8 (mass 10).
    payload 1, vehicle 36 -> untracked 0."""
    return (
        [_stage(0, propellant=20.0, dry=5.0),
         _stage(1, propellant=8.0, dry=2.0, multi=True)],
        36.0,
        1.0,
    )


def _req200(required_at):
    return required_at * (-math.expm1(-200.0 / (300.0 * G)))


class BasicTwoStageTests(unittest.TestCase):
    def test_two_stage_burn_separate_ignite(self):
        stages, vehicle, payload = _two_stage_vehicle()
        reqs = [_req(200.0, 'b1', 1, preferred=0),
                _req(200.0, 'b2', 2, preferred=1)]
        final, mission = simulate_multistage_sequence(
            'R', stages, reqs, vehicle, payload,
            separations_before={'b2': [0]})
        self.assertTrue(mission.success)
        self.assertEqual(mission.failure_code, FailureCode.NONE)
        self.assertEqual(len(mission.burns), 2)
        b1, b2 = mission.burns
        self.assertEqual(b1.ignition_kind, IgnitionKind.FIRST)
        # second stage first ignition must NOT be misjudged as a restart
        self.assertEqual(b2.ignition_kind, IgnitionKind.FIRST)
        consumed1 = _req200(36.0)
        self.assertAlmostEqual(b1.propellant_consumed_t, consumed1, places=9)
        self.assertAlmostEqual(b1.mass_after_t, 36.0 - consumed1, places=9)
        # separation discards dry + unused propellant of stage 0
        discarded = 5.0 + (20.0 - consumed1)
        consumed2 = _req200(11.0)
        self.assertAlmostEqual(b2.mass_before_t, 11.0, places=9)
        self.assertAlmostEqual(b2.propellant_consumed_t, consumed2, places=9)
        self.assertAlmostEqual(b2.mass_after_t, 11.0 - consumed2, places=9)
        # final states
        self.assertIsNot(final[0], stages[0])
        self.assertIsNot(final[1], stages[1])
        self.assertEqual(final[0].attached, False)
        self.assertEqual(final[0].ignition_count, 1)  # it burned before separating
        self.assertAlmostEqual(final[0].propellant_remaining_t,
                               20.0 - consumed1, places=9)
        self.assertTrue(final[1].attached)
        self.assertEqual(final[1].ignition_count, 1)
        self.assertAlmostEqual(final[1].propellant_remaining_t,
                               8.0 - consumed2, places=9)
        # separation audit event
        self.assertEqual(len(mission.metadata['separation_events']), 1)
        event = mission.metadata['separation_events'][0]
        self.assertEqual(event['before_burn_id'], 'b2')
        self.assertEqual(event['stage_indices'], [0])
        self.assertAlmostEqual(event['discarded_mass_t'], discarded, places=9)
        self.assertAlmostEqual(event['mass_before_t'], 36.0 - consumed1,
                               places=9)
        self.assertAlmostEqual(event['mass_after_t'], 11.0, places=9)
        # metadata
        self.assertEqual(mission.metadata['initial_vehicle_mass_t'], 36.0)
        self.assertAlmostEqual(mission.metadata['final_vehicle_mass_t'],
                               11.0 - consumed2, places=9)
        self.assertEqual(mission.metadata['initial_attached_stage_mass_t'], 35.0)
        self.assertAlmostEqual(mission.metadata['final_attached_stage_mass_t'],
                               2.0 + (8.0 - consumed2), places=9)
        self.assertEqual(mission.metadata['untracked_mass_t'], 0.0)
        self.assertEqual(mission.metadata['stage_indices'], [0, 1])
        self.assertEqual(mission.metadata['total_requirement_count'], 2)
        self.assertEqual(mission.metadata['completed_burn_count'], 2)
        self.assertIsNone(mission.metadata['stopped_at_burn_id'])

    def test_dry_mass_stays_until_explicit_separation(self):
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        reqs = [_req(2000.0, 'b1', 1, preferred=0),
                _req(100.0, 'b2', 2, preferred=1)]
        final, mission = simulate_multistage_sequence(
            'R', [s0, s1], reqs, 17.0, 1.0)
        self.assertFalse(mission.success)
        self.assertEqual(mission.failure_code,
                         FailureCode.PROPELLANT_INSUFFICIENT)
        # b1 exhausts all of stage 0's 1 t; dry mass 5 t is still attached
        expected_actual = 300.0 * G * (-math.log1p(-1.0 / 17.0))
        self.assertAlmostEqual(mission.burns[0].actual_dv_m_s, expected_actual,
                               places=9)
        self.assertAlmostEqual(mission.burns[0].mass_after_t, 16.0, places=9)
        self.assertEqual(len(mission.burns), 1)
        self.assertEqual(final[0].propellant_remaining_t, 0.0)
        self.assertTrue(final[0].attached)
        self.assertEqual(final[0].ignition_count, 1)
        # stage 1 untouched
        self.assertEqual(final[1].ignition_count, 0)
        self.assertEqual(final[1].propellant_remaining_t, 8.0)
        self.assertAlmostEqual(mission.metadata['final_vehicle_mass_t'], 16.0,
                               places=9)
        self.assertAlmostEqual(mission.metadata['final_attached_stage_mass_t'],
                               5.0 + 2.0 + 8.0, places=9)

    def test_dry_mass_included_in_vehicle_for_later_burn(self):
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        reqs = [_req(150.0, 'b1', 1, preferred=0),
                _req(100.0, 'b2', 2, preferred=1)]
        final, mission = simulate_multistage_sequence(
            'R', [s0, s1], reqs, 17.0, 1.0)
        self.assertTrue(mission.success)
        # b2 vehicle = 17 - consumed1; stage 0 dry 5 t is still on the vehicle
        consumed1 = 17.0 * (-math.expm1(-150.0 / (300.0 * G)))
        self.assertLess(consumed1, 1.0)
        self.assertAlmostEqual(mission.burns[1].mass_before_t,
                               17.0 - consumed1, places=9)
        consumed2 = (17.0 - consumed1) * (-math.expm1(-100.0 / (300.0 * G)))
        self.assertAlmostEqual(mission.burns[1].propellant_consumed_t,
                               consumed2, places=9)
        self.assertAlmostEqual(
            mission.metadata['final_vehicle_mass_t'],
            (17.0 - consumed1) - consumed2, places=9)
        self.assertTrue(final[0].attached)
        self.assertAlmostEqual(final[0].propellant_remaining_t,
                               1.0 - consumed1, places=9)
        self.assertEqual(final[0].ignition_count, 1)

    def test_same_stage_second_burn_is_restart(self):
        s0 = _stage(0, propellant=20.0, dry=5.0, multi=True)
        reqs = [_req(200.0, 'b1', 1, preferred=0),
                _req(200.0, 'b2', 2, preferred=0)]
        final, mission = simulate_multistage_sequence(
            'R', [s0], reqs, 26.0, 1.0)
        self.assertTrue(mission.success)
        self.assertEqual(mission.burns[0].ignition_kind, IgnitionKind.FIRST)
        self.assertEqual(mission.burns[1].ignition_kind, IgnitionKind.RESTART)
        consumed1 = 26.0 * (-math.expm1(-200.0 / (300.0 * G)))
        m2 = 26.0 - consumed1
        consumed2 = m2 * (-math.expm1(-200.0 / (300.0 * G)))
        self.assertAlmostEqual(mission.burns[1].mass_before_t, m2, places=9)
        self.assertAlmostEqual(final[0].ignition_count, 2)
        self.assertAlmostEqual(final[0].propellant_remaining_t,
                               20.0 - consumed1 - consumed2, places=9)

    def test_restart_unsupported_fails_and_stops(self):
        s0 = _stage(0, propellant=20.0, dry=5.0, multi=False, count=1)
        reqs = [_req(100.0, 'b1', 1, preferred=0),
                _req(100.0, 'b2', 2, preferred=0)]
        final, mission = simulate_multistage_sequence(
            'R', [s0], reqs, 26.0, 1.0)
        self.assertFalse(mission.success)
        self.assertEqual(mission.failure_code, FailureCode.RESTART_UNSUPPORTED)
        self.assertEqual(len(mission.burns), 1)
        self.assertEqual(mission.metadata['stopped_at_burn_id'], 'b1')
        self.assertEqual(final[0].ignition_count, 1)
        self.assertEqual(final[0].propellant_remaining_t, 20.0)

    def test_separated_stage_re_specified_returns_no_stage_assigned(self):
        stages, vehicle, payload = _two_stage_vehicle()
        reqs = [_req(200.0, 'b1', 1, preferred=0),
                _req(200.0, 'b2', 2, preferred=1),
                _req(100.0, 'b3', 3, preferred=0)]
        final, mission = simulate_multistage_sequence(
            'R', stages, reqs, vehicle, payload,
            separations_before={'b2': [0]})
        self.assertFalse(mission.success)
        self.assertEqual(mission.failure_code, FailureCode.NO_STAGE_ASSIGNED)
        self.assertEqual(len(mission.burns), 3)
        self.assertEqual(mission.metadata['stopped_at_burn_id'], 'b3')
        burn = mission.burns[2]
        self.assertFalse(burn.success)
        self.assertEqual(burn.ignition_kind, IgnitionKind.UNASSIGNED)
        self.assertIsNone(burn.stage_index)
        self.assertEqual(burn.actual_dv_m_s, 0.0)
        self.assertEqual(burn.propellant_consumed_t, 0.0)
        # stage 0 separated before b2; b3 specifies it again -> mass unchanged
        consumed1 = _req200(36.0)
        consumed2 = _req200(11.0)
        self.assertAlmostEqual(burn.mass_before_t, 11.0 - consumed2, places=9)
        self.assertAlmostEqual(burn.mass_after_t, 11.0 - consumed2, places=9)
        self.assertFalse(final[0].attached)
        self.assertEqual(final[0].ignition_count, 1)
        self.assertAlmostEqual(final[0].propellant_remaining_t,
                               20.0 - consumed1, places=9)
        self.assertEqual(final[1].ignition_count, 1)

    def test_non_contiguous_stage_indices_execute_normally(self):
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        s3 = _stage(3, propellant=20.0, dry=5.0)
        reqs = [_req(200.0, 'b1', 1, preferred=1),
                _req(200.0, 'b2', 2, preferred=3)]
        final, mission = simulate_multistage_sequence(
            'R', [s1, s3], reqs, 36.0, 1.0)
        self.assertTrue(mission.success)
        self.assertEqual(mission.metadata['stage_indices'], [1, 3])
        self.assertEqual(final[0].ignition_count, 1)
        self.assertEqual(final[1].ignition_count, 1)


class CalibrationTests(unittest.TestCase):
    def test_calibration_changes_no_stage_state(self):
        stages, vehicle, payload = _two_stage_vehicle()
        reqs = [_calib('park', 1), _req(200.0, 'b2', 2, preferred=1)]
        final, mission = simulate_multistage_sequence(
            'R', stages, reqs, vehicle, payload)
        self.assertTrue(mission.success)
        park = mission.burns[0]
        self.assertTrue(park.success)
        self.assertEqual(park.ignition_kind, IgnitionKind.UNASSIGNED)
        self.assertIsNone(park.stage_index)
        self.assertEqual(park.propellant_consumed_t, 0.0)
        self.assertIsNone(park.actual_dv_m_s)
        # stage 0 completely untouched; stage 1's burn is still FIRST
        self.assertEqual(final[0].ignition_count, 0)
        self.assertEqual(final[0].propellant_remaining_t, 20.0)
        self.assertEqual(final[1].ignition_count, 1)
        self.assertEqual(mission.burns[1].ignition_kind, IgnitionKind.FIRST)

    def test_separation_before_calibration_rejected(self):
        stages, vehicle, payload = _two_stage_vehicle()
        reqs = [_calib('park', 1)]
        with self.assertRaises(ValueError):
            simulate_multistage_sequence(
                'R', stages, reqs, vehicle, payload,
                separations_before={'park': [0]})


class PreflightValidationTests(unittest.TestCase):
    def test_unknown_preferred_stage_index_rejected(self):
        stages, vehicle, payload = _two_stage_vehicle()
        reqs = [_req(200.0, 'b1', 1, preferred=5)]
        with self.assertRaises(ValueError) as ctx:
            simulate_multistage_sequence('R', stages, reqs, vehicle, payload)
        self.assertIn('5', str(ctx.exception))

    def test_missing_preferred_stage_index_rejected(self):
        stages, vehicle, payload = _two_stage_vehicle()
        reqs = [_req(200.0, 'b1', 1)]
        with self.assertRaises(ValueError) as ctx:
            simulate_multistage_sequence('R', stages, reqs, vehicle, payload)
        self.assertIn('preferred_stage_index', str(ctx.exception))

    def test_duplicate_stage_index_rejected(self):
        stages = [_stage(0), _stage(0)]
        with self.assertRaises(ValueError):
            simulate_multistage_sequence('R', stages, [_req(200.0, 'b1', 1,
                                                            preferred=0)],
                                         36.0, 1.0)

    def test_empty_stages_rejected(self):
        with self.assertRaises(ValueError):
            simulate_multistage_sequence('R', [], [_req(200.0, 'b1', 1,
                                                        preferred=0)],
                                         36.0, 1.0)

    def test_duplicate_separation_rejected(self):
        stages, vehicle, payload = _two_stage_vehicle()
        reqs = [_req(200.0, 'b1', 1, preferred=1),
                _req(200.0, 'b2', 2, preferred=1)]
        with self.assertRaises(ValueError) as ctx:
            simulate_multistage_sequence(
                'R', stages, reqs, vehicle, payload,
                separations_before={'b1': [0], 'b2': [0]})
        self.assertIn('more than once', str(ctx.exception))

    def test_separation_of_unknown_stage_rejected(self):
        stages, vehicle, payload = _two_stage_vehicle()
        reqs = [_req(200.0, 'b1', 1, preferred=1)]
        with self.assertRaises(ValueError):
            simulate_multistage_sequence(
                'R', stages, reqs, vehicle, payload,
                separations_before={'b1': [9]})

    def test_separation_of_already_detached_stage_rejected(self):
        s0 = _stage(0, attached=False)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        reqs = [_req(200.0, 'b1', 1, preferred=1)]
        with self.assertRaises(ValueError):
            simulate_multistage_sequence(
                'R', [s0, s1], reqs, 36.0, 1.0,
                separations_before={'b1': [0]})

    def test_separation_of_executing_stage_rejected(self):
        stages, vehicle, payload = _two_stage_vehicle()
        reqs = [_req(200.0, 'b1', 1, preferred=0)]
        with self.assertRaises(ValueError) as ctx:
            simulate_multistage_sequence(
                'R', stages, reqs, vehicle, payload,
                separations_before={'b1': [0]})
        self.assertIn('executing stage', str(ctx.exception))

    def test_malformed_separation_plans_rejected(self):
        stages, vehicle, payload = _two_stage_vehicle()
        reqs = [_req(200.0, 'b1', 1, preferred=1)]
        with self.assertRaises(ValueError):
            simulate_multistage_sequence('R', stages, reqs, vehicle, payload,
                                         separations_before={'b1': []})
        with self.assertRaises(ValueError):
            simulate_multistage_sequence('R', stages, reqs, vehicle, payload,
                                         separations_before={'b1': [0, 0]})
        with self.assertRaises(ValueError):
            simulate_multistage_sequence('R', stages, reqs, vehicle, payload,
                                         separations_before={'b1': [True]})
        with self.assertRaises(ValueError):
            simulate_multistage_sequence('R', stages, reqs, vehicle, payload,
                                         separations_before={'unknown': [0]})
        with self.assertRaises(ValueError):
            simulate_multistage_sequence('R', stages, reqs, vehicle, payload,
                                         separations_before='not-a-dict')

    def test_impossible_initial_mass_rejected(self):
        stages, _, _ = _two_stage_vehicle()
        with self.assertRaises(ValueError):
            simulate_multistage_sequence('R', stages, [_req(200.0, 'b1', 1,
                                                            preferred=0)],
                                         30.0, 1.0)


class ParallelAndFailureTests(unittest.TestCase):
    def test_parallel_executing_stage_returns_data_insufficient(self):
        s0 = _stage(0, parallel=True)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        reqs = [_req(100.0, 'b1', 1, preferred=0),
                _req(100.0, 'b2', 2, preferred=1)]
        final, mission = simulate_multistage_sequence(
            'R', [s0, s1], reqs, 36.0, 1.0)
        self.assertFalse(mission.success)
        self.assertEqual(mission.failure_code, FailureCode.DATA_INSUFFICIENT)
        self.assertEqual(len(mission.burns), 1)
        self.assertEqual(mission.metadata['stopped_at_burn_id'], 'b1')
        self.assertEqual(final[0].propellant_remaining_t, 20.0)
        self.assertEqual(final[0].ignition_count, 0)
        # stage 1 was never auto-assigned
        self.assertEqual(final[1].ignition_count, 0)
        self.assertEqual(final[1].propellant_remaining_t, 8.0)

    def test_untracked_mass_stays_constant_through_burns_and_separation(self):
        stages, _, _ = _two_stage_vehicle()
        reqs = [_req(100.0, 'b1', 1, preferred=0),
                _req(100.0, 'b2', 2, preferred=1)]
        final, mission = simulate_multistage_sequence(
            'R', stages, reqs, 40.0, 1.0, separations_before={'b2': [0]})
        self.assertTrue(mission.success)
        self.assertEqual(mission.metadata['untracked_mass_t'], 4.0)
        consumed1 = 40.0 * (-math.expm1(-100.0 / (300.0 * G)))
        m_after_b1 = 40.0 - consumed1
        self.assertAlmostEqual(mission.burns[0].mass_after_t, m_after_b1,
                               places=9)
        discarded = 5.0 + (20.0 - consumed1)
        m_after_sep = m_after_b1 - discarded
        self.assertAlmostEqual(mission.burns[1].mass_before_t, m_after_sep,
                               places=9)
        consumed2 = m_after_sep * (-math.expm1(-100.0 / (300.0 * G)))
        m_final = m_after_sep - consumed2
        self.assertAlmostEqual(mission.metadata['final_vehicle_mass_t'],
                               m_final, places=9)
        self.assertAlmostEqual(
            mission.metadata['final_attached_stage_mass_t'],
            2.0 + (8.0 - consumed2), places=9)
        # invariant: final = payload + untracked + attached stages
        self.assertAlmostEqual(m_final, 1.0 + 4.0 + 2.0 + (8.0 - consumed2),
                               places=9)

    def test_insufficient_propellant_preserves_prior_state_and_stops(self):
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        reqs = [_req(2000.0, 'b1', 1, preferred=0),
                _req(100.0, 'b2', 2, preferred=1)]
        final, mission = simulate_multistage_sequence(
            'R', [s0, s1], reqs, 17.0, 1.0)
        self.assertFalse(mission.success)
        self.assertEqual(mission.failure_code,
                         FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertEqual(len(mission.burns), 1)
        self.assertEqual(mission.metadata['stopped_at_burn_id'], 'b1')
        self.assertEqual(final[0].propellant_remaining_t, 0.0)
        self.assertEqual(final[0].ignition_count, 1)
        self.assertEqual(final[1].ignition_count, 0)
        self.assertEqual(final[1].propellant_remaining_t, 8.0)


class ImmutabilityAndSerializationTests(unittest.TestCase):
    def test_inputs_completely_unmodified(self):
        stages, vehicle, payload = _two_stage_vehicle()
        reqs = [_req(200.0, 'b1', 1, preferred=0, name='一级点火'),
                _req(200.0, 'b2', 2, preferred=1)]
        reqs[0].metadata = {'deep': {'a': [1, 2]}}
        plan = {'b2': [0]}
        stages_before = [s.to_dict() for s in stages]
        reqs_before = [r.to_dict() for r in reqs]
        plan_before = copy.deepcopy(plan)
        simulate_multistage_sequence('R', stages, reqs, vehicle, payload,
                                     separations_before=plan)
        self.assertEqual([s.to_dict() for s in stages], stages_before)
        self.assertEqual([r.to_dict() for r in reqs], reqs_before)
        self.assertEqual(plan, plan_before)

    def test_outputs_do_not_share_mutable_references(self):
        stages, vehicle, payload = _two_stage_vehicle()
        reqs = [_req(200.0, 'b1', 1, preferred=0),
                _req(200.0, 'b2', 2, preferred=1)]
        reqs[0].metadata = {'list': [1, 2]}
        final, mission = simulate_multistage_sequence(
            'R', stages, reqs, vehicle, payload,
            separations_before={'b2': [0]})
        # mutate output metadata aggressively
        final[0].metadata['mutated'] = True
        mission.metadata['separation_events'][0]['stage_indices'].append(99)
        mission.burns[0].metadata['requirement_metadata']['list'].append(99)
        self.assertNotIn('mutated', stages[0].metadata)
        self.assertEqual(mission.metadata['separation_events'][0]
                         ['stage_indices'], [0, 99])
        self.assertEqual(reqs[0].metadata, {'list': [1, 2]})
        # input stage metadata dict is not the same object as output's
        self.assertIsNot(final[0].metadata, stages[0].metadata)

    def test_results_json_dumpable(self):
        stages, vehicle, payload = _two_stage_vehicle()
        reqs = [_calib('park', 1),
                _req(200.0, 'b1', 2, preferred=0),
                _req(200.0, 'b2', 3, preferred=1)]
        final, mission = simulate_multistage_sequence(
            'R', stages, reqs, vehicle, payload,
            separations_before={'b2': [0]})
        json.dumps(mission.to_dict(), ensure_ascii=False)
        for state in final:
            json.dumps(state.to_dict(), ensure_ascii=False)


class MassConservationTests(unittest.TestCase):
    def test_stepwise_mass_conservation_with_independent_formula(self):
        stages, vehicle, payload = _two_stage_vehicle()
        reqs = [_req(200.0, 'b1', 1, preferred=0),
                _req(200.0, 'b2', 2, preferred=1)]
        final, mission = simulate_multistage_sequence(
            'R', stages, reqs, vehicle, payload,
            separations_before={'b2': [0]})
        # independent bookkeeping
        m = vehicle
        consumed1 = m * (-math.expm1(-200.0 / (300.0 * G)))
        m -= consumed1
        self.assertAlmostEqual(mission.burns[0].mass_before_t, 36.0, places=9)
        self.assertAlmostEqual(mission.burns[0].mass_after_t, m, places=9)
        m -= 5.0 + (20.0 - consumed1)  # separation of stage 0
        self.assertAlmostEqual(mission.metadata['separation_events'][0]
                               ['mass_after_t'], m, places=9)
        self.assertAlmostEqual(mission.burns[1].mass_before_t, m, places=9)
        consumed2 = m * (-math.expm1(-200.0 / (300.0 * G)))
        m -= consumed2
        self.assertAlmostEqual(mission.burns[1].mass_after_t, m, places=9)
        self.assertAlmostEqual(mission.metadata['final_vehicle_mass_t'], m,
                               places=9)
        # every stage accounted: payload + attached masses
        attached = sum(s.dry_mass_t + s.propellant_remaining_t
                       for s in final if s.attached)
        self.assertAlmostEqual(m, payload + attached, places=9)


if __name__ == '__main__':
    unittest.main()
