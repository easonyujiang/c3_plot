import copy
import json
import math
import unittest
from pathlib import Path

from mission_adapters import normalize_stages
from mission_parking_replay import replay_serial_ascent_to_parking
from mission_simulation import (
    BurnRequirement,
    DataQuality,
    FailureCode,
    IgnitionKind,
    StageState,
)
from mission_solver import simulate_multistage_sequence
from mission_stage_planner import PostParkingPlanResult, plan_post_parking_sequence
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
    return BurnRequirement(burn_id=burn_id, order=order, target_dv_m_s=target,
                           source=source, name=burn_id)


def _calib(burn_id='park', order=1):
    return BurnRequirement(burn_id=burn_id, order=order, target_dv_m_s=0.0,
                           source='rocket_calibration', name=burn_id)


def _req2(b1_target, b2_target):
    return [_req(b1_target, 'b1', 1), _req(b2_target, 'b2', 2)]


class PlanningTests(unittest.TestCase):
    def test_current_stage_restart_completes_all(self):
        stages = [_stage(0, count=1, multi=True, propellant=20.0)]
        plan = plan_post_parking_sequence('R', stages, _req2(100.0, 100.0),
                                          26.0, 1.0)
        self.assertTrue(plan.success)
        self.assertEqual(plan.metadata['assigned_stage_indices'], [0, 0])
        self.assertEqual(plan.separations_before, {})
        self.assertEqual(plan.metadata['discarded_propellant_t'], 0.0)

    def test_restart_unsupported_moves_to_upper_stage_first_ignition(self):
        s0 = _stage(0, count=1, multi=False, propellant=20.0)
        s1 = _stage(1, propellant=8.0, dry=2.0)
        plan = plan_post_parking_sequence('R', [s0, s1], [_req(100.0, 'b1', 1)],
                                          36.0, 1.0)
        self.assertTrue(plan.success)
        self.assertEqual(plan.metadata['assigned_stage_indices'], [1])
        self.assertEqual(plan.separations_before, {'b1': [0]})
        self.assertAlmostEqual(plan.metadata['discarded_propellant_t'], 20.0,
                               places=9)
        self.assertTrue(any('丢弃' in w for w in plan.warnings))

    def test_second_burn_on_same_stage_is_restart(self):
        stages = [_stage(0, multi=True, propellant=20.0)]
        plan = plan_post_parking_sequence('R', stages, _req2(100.0, 100.0),
                                          26.0, 1.0)
        self.assertTrue(plan.success)
        final, mission = simulate_multistage_sequence(
            'R', stages, plan.requirements, 26.0, 1.0,
            separations_before=plan.separations_before)
        self.assertTrue(mission.success)
        self.assertEqual([b.ignition_kind for b in mission.burns],
                         [IgnitionKind.FIRST, IgnitionKind.RESTART])

    def test_second_ignition_unsupported_backtracks_upward(self):
        s0 = _stage(0, multi=False, propellant=20.0)
        s1 = _stage(1, multi=True, propellant=8.0, dry=2.0)
        plan = plan_post_parking_sequence('R', [s0, s1], _req2(100.0, 100.0),
                                          36.0, 1.0)
        self.assertTrue(plan.success)
        # b1 on stage 0 (FIRST), b2 cannot restart on stage 0 -> stage 1
        self.assertEqual(plan.metadata['assigned_stage_indices'], [0, 1])
        self.assertEqual(plan.separations_before, {'b2': [0]})
        # assignment positions never decrease
        self.assertEqual(plan.metadata['assigned_stage_indices'],
                         sorted(plan.metadata['assigned_stage_indices']))
        # stage 0's unused propellant is discarded when separated
        consumed1 = 36.0 * (-math.expm1(-100.0 / (300.0 * G)))
        self.assertAlmostEqual(plan.metadata['discarded_propellant_t'],
                               20.0 - consumed1, places=9)
        final, mission = simulate_multistage_sequence(
            'R', [s0, s1], plan.requirements, 36.0, 1.0,
            separations_before=plan.separations_before)
        self.assertTrue(mission.success)
        self.assertEqual([b.stage_index for b in mission.burns], [0, 1])
        self.assertAlmostEqual(mission.metadata['final_vehicle_mass_t'],
                               plan.planned_final_vehicle_mass_t, places=9)

    def test_all_attached_below_are_separated_in_list_order(self):
        s0 = _stage(0, count=1, multi=False, propellant=20.0)
        s1 = _stage(1, count=1, multi=False, propellant=8.0, dry=2.0)
        s2 = _stage(2, multi=True, propellant=20.0)
        plan = plan_post_parking_sequence('R', [s0, s1, s2],
                                          [_req(100.0, 'b1', 1)], 61.0, 1.0)
        self.assertTrue(plan.success)
        self.assertEqual(plan.separations_before, {'b1': [0, 1]})
        self.assertEqual(plan.metadata['assigned_stage_indices'], [2])

    def test_assignment_positions_never_move_down(self):
        s0 = _stage(0, count=1, multi=False, propellant=20.0)
        s1 = _stage(1, multi=False, propellant=8.0, dry=2.0)
        s2 = _stage(2, multi=True, propellant=20.0)
        plan = plan_post_parking_sequence('R', [s0, s1, s2],
                                          _req2(100.0, 100.0), 61.0, 1.0)
        self.assertTrue(plan.success)
        self.assertEqual(plan.metadata['assigned_stage_indices'], [1, 2])
        self.assertEqual(plan.separations_before, {'b1': [0], 'b2': [1]})

    def test_calibration_passes_through_unassigned(self):
        s0 = _stage(0, multi=True, propellant=20.0)
        reqs = [_calib('park', 1), _req(100.0, 'b1', 2)]
        plan = plan_post_parking_sequence('R', [s0], reqs, 26.0, 1.0)
        self.assertTrue(plan.success)
        self.assertEqual([r.burn_id for r in plan.requirements], ['park', 'b1'])
        self.assertIsNone(plan.requirements[0].preferred_stage_index)
        self.assertEqual(plan.requirements[0].source, 'rocket_calibration')
        self.assertEqual(plan.requirements[1].preferred_stage_index, 0)
        self.assertEqual(plan.separations_before, {})

    def test_non_contiguous_indices_follow_list_order(self):
        s3 = _stage(3, propellant=20.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        plan = plan_post_parking_sequence('R', [s3, s1], [_req(100.0, 'b1', 1)],
                                          36.0, 1.0)
        self.assertTrue(plan.success)
        self.assertEqual(plan.metadata['assigned_stage_indices'], [3])
        self.assertEqual(plan.metadata['stage_order'], [3, 1])

    def test_falls_through_to_next_candidate_when_lower_stage_cannot_complete_burn(self):
        # Candidate-level fall-through: for b3 the lower stage (stage 0
        # restart) is evaluated first and cannot complete the burn, so the
        # next candidate (stage 1) is tried and succeeds.  This is ordered
        # candidate evaluation, not cross-burn backtracking.
        s0 = _stage(0, multi=True, propellant=3.0, dry=1.0)
        s1 = _stage(1, multi=False, propellant=12.0, dry=1.0)
        plan = plan_post_parking_sequence(
            'R', [s0, s1],
            [_req(100.0, 'b1', 1), _req(200.0, 'b2', 2),
             _req(500.0, 'b3', 3)],
            18.0, 1.0)
        self.assertTrue(plan.success)
        self.assertEqual(plan.metadata['assigned_stage_indices'], [0, 0, 1])
        self.assertEqual(plan.separations_before, {'b3': [0]})
        # 4 candidate evaluations for 3 burns (one failed lower candidate)
        self.assertEqual(plan.metadata['search_nodes'], 4)
        # independent check: stage 0 restart cannot cover b3
        leftover = 3.0 - 18.0 * (-math.expm1(-100.0 / (300.0 * G)))
        leftover -= (18.0 - 18.0 * (-math.expm1(-100.0 / (300.0 * G)))) \
            * (-math.expm1(-200.0 / (300.0 * G)))
        needed = 16.0 * (-math.expm1(-500.0 / (300.0 * G)))
        self.assertLess(leftover, needed)
        # and the plan executes end-to-end
        final, mission = simulate_multistage_sequence(
            'R', [s0, s1], plan.requirements, 18.0, 1.0,
            separations_before=plan.separations_before)
        self.assertTrue(mission.success)
        self.assertEqual([b.stage_index for b in mission.burns], [0, 0, 1])


class FailureClassificationTests(unittest.TestCase):
    def test_missing_isp_returns_data_insufficient(self):
        s0 = _stage(0, isp=None)
        plan = plan_post_parking_sequence('R', [s0], [_req(100.0, 'b1', 1)],
                                          26.0, 1.0)
        self.assertFalse(plan.success)
        self.assertEqual(plan.failure_code, FailureCode.DATA_INSUFFICIENT)
        self.assertEqual(plan.data_quality, DataQuality.INSUFFICIENT)
        self.assertEqual(plan.separations_before, {})
        self.assertEqual(plan.planned_final_vehicle_mass_t, 26.0)
        for r in plan.requirements:
            self.assertIsNone(r.preferred_stage_index)
        self.assertEqual(plan.planned_final_stages[0].to_dict(),
                         s0.to_dict())

    def test_parallel_returns_data_insufficient(self):
        s0 = _stage(0, parallel=True)
        plan = plan_post_parking_sequence('R', [s0], [_req(100.0, 'b1', 1)],
                                          26.0, 1.0)
        self.assertFalse(plan.success)
        self.assertEqual(plan.failure_code, FailureCode.DATA_INSUFFICIENT)
        self.assertIn('并联', plan.failure_reason_zh)

    def test_search_limit_returns_data_insufficient(self):
        # 3 burns need 4 candidate evaluations; a limit of 3 stops the search.
        s0 = _stage(0, multi=True, propellant=3.0, dry=1.0)
        s1 = _stage(1, multi=False, propellant=12.0, dry=1.0)
        plan = plan_post_parking_sequence(
            'R', [s0, s1],
            [_req(100.0, 'b1', 1), _req(200.0, 'b2', 2),
             _req(500.0, 'b3', 3)],
            18.0, 1.0, max_search_nodes=3)
        self.assertFalse(plan.success)
        self.assertEqual(plan.failure_code, FailureCode.DATA_INSUFFICIENT)
        self.assertIn('候选级评估次数超过上限', plan.failure_reason_zh)
        # the search stops when the limit would be exceeded
        self.assertGreater(plan.metadata['search_nodes'],
                           plan.metadata['max_search_nodes'])
        self.assertLessEqual(plan.metadata['search_nodes'],
                             plan.metadata['max_search_nodes'] + 1)

    def test_no_attached_stage_returns_no_stage_assigned(self):
        s0 = _stage(0, attached=False)
        plan = plan_post_parking_sequence('R', [s0], [_req(100.0, 'b1', 1)],
                                          26.0, 1.0)
        self.assertFalse(plan.success)
        self.assertEqual(plan.failure_code, FailureCode.NO_STAGE_ASSIGNED)
        self.assertIn('没有仍连接', plan.failure_reason_zh)

    def test_restart_only_blocker_returns_restart_unsupported(self):
        s0 = _stage(0, count=1, multi=False, propellant=20.0)
        plan = plan_post_parking_sequence('R', [s0], [_req(100.0, 'b1', 1)],
                                          26.0, 1.0)
        self.assertFalse(plan.success)
        self.assertEqual(plan.failure_code, FailureCode.RESTART_UNSUPPORTED)
        self.assertIn('多次点火', plan.failure_reason_zh)
        self.assertTrue(plan.warnings)

    def test_propellant_shortfall_without_aggregate_rescue(self):
        s0 = _stage(0, isp=150.0, propellant=0.5, dry=5.0, multi=True)
        s1 = _stage(1, isp=150.0, propellant=0.5, dry=2.0, multi=True)
        plan = plan_post_parking_sequence('R', [s0, s1], [_req(500.0, 'b1', 1)],
                                          9.0, 1.0)
        self.assertFalse(plan.success)
        self.assertEqual(plan.failure_code, FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertIn('b1', plan.failure_reason_zh)
        self.assertIn('500', plan.failure_reason_zh)
        self.assertIn('候选级 [0, 1]', plan.failure_reason_zh)
        self.assertNotIn('whole_burn_per_stage', plan.failure_reason_zh)

    def test_whole_burn_per_stage_limitation_reported(self):
        # aggregate (both stages) can cover 300, but no single stage can:
        s0 = _stage(0, isp=150.0, propellant=0.5, dry=5.0, multi=True)
        s1 = _stage(1, isp=150.0, propellant=0.5, dry=2.0, multi=True)
        plan = plan_post_parking_sequence('R', [s0, s1], [_req(300.0, 'b1', 1)],
                                          9.0, 1.0)
        self.assertFalse(plan.success)
        self.assertEqual(plan.failure_code, FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertIn('whole_burn_per_stage', plan.failure_reason_zh)

    def test_failure_leaks_no_partial_plan(self):
        s0 = _stage(0, isp=150.0, propellant=0.5, dry=5.0, multi=True)
        s1 = _stage(1, isp=150.0, propellant=0.5, dry=2.0, multi=True)
        reqs = [_req(300.0, 'b1', 1), _req(100.0, 'b2', 2)]
        plan = plan_post_parking_sequence('R', [s0, s1], reqs, 9.0, 1.0)
        self.assertFalse(plan.success)
        self.assertEqual(plan.separations_before, {})
        for r in plan.requirements:
            self.assertIsNone(r.preferred_stage_index)
        self.assertEqual(plan.metadata['assigned_stage_indices'], [])


class ValidationAndSafetyTests(unittest.TestCase):
    def test_invalid_arguments_rejected(self):
        s0 = _stage(0)
        req = [_req(100.0, 'b1', 1)]
        with self.assertRaises(ValueError):
            plan_post_parking_sequence('', [s0], req, 26.0, 1.0)
        with self.assertRaises(ValueError):
            plan_post_parking_sequence('R', [], req, 26.0, 1.0)
        with self.assertRaises(ValueError):
            plan_post_parking_sequence('R', 'x', req, 26.0, 1.0)
        with self.assertRaises(ValueError):
            plan_post_parking_sequence('R', ['x'], req, 26.0, 1.0)
        with self.assertRaises(ValueError):
            plan_post_parking_sequence('R', [_stage(0), _stage(0)], req,
                                       26.0, 1.0)
        with self.assertRaises(ValueError):
            plan_post_parking_sequence('R', [s0], [], 26.0, 1.0)
        with self.assertRaises(ValueError):
            plan_post_parking_sequence('R', [s0], [_req(100.0, 'b1', 1),
                                                  _req(100.0, 'b1', 2)],
                                       26.0, 1.0)
        with self.assertRaises(ValueError):
            plan_post_parking_sequence('R', [s0], [_req(100.0, 'b1', 2),
                                                  _req(100.0, 'b2', 1)],
                                       26.0, 1.0)
        with self.assertRaises(ValueError):
            plan_post_parking_sequence('R', [s0], [BurnRequirement(
                burn_id='b1', order=1, target_dv_m_s=100.0, source='m',
                preferred_stage_index=0)], 26.0, 1.0)
        with self.assertRaises(ValueError):
            plan_post_parking_sequence('R', [s0], [_req(0.0, 'b1', 1)],
                                       26.0, 1.0)
        with self.assertRaises(ValueError):
            plan_post_parking_sequence('R', [s0], req, 0.0, 1.0)
        with self.assertRaises(ValueError):
            plan_post_parking_sequence('R', [s0], req, 26.0, -1.0)
        with self.assertRaises(ValueError):
            plan_post_parking_sequence('R', [s0], req, 25.0, 2.0)
        with self.assertRaises(ValueError):
            plan_post_parking_sequence('R', [s0], req, 26.0, 1.0,
                                       max_search_nodes=0)
        with self.assertRaises(ValueError):
            plan_post_parking_sequence('R', [s0], req, 26.0, 1.0,
                                       max_search_nodes=True)

    def test_broken_topology_rejected(self):
        s0 = _stage(0)
        s1 = _stage(1, attached=False)
        with self.assertRaises(ValueError):
            plan_post_parking_sequence('R', [s0, s1], [_req(100.0, 'b1', 1)],
                                       36.0, 1.0)

    def test_inputs_completely_unmodified(self):
        s0 = _stage(0, count=1, multi=False, propellant=20.0)
        s1 = _stage(1, multi=True, propellant=8.0, dry=2.0)
        reqs = _req2(100.0, 100.0)
        reqs[0].metadata = {'deep': {'a': [1, 2]}}
        stages_before = [s.to_dict() for s in (s0, s1)]
        reqs_before = [r.to_dict() for r in reqs]
        plan_post_parking_sequence('R', [s0, s1], reqs, 36.0, 1.0)
        plan_post_parking_sequence('R', [s0, s1], reqs, 36.0, 1.0,
                                   max_search_nodes=1)
        self.assertEqual([s.to_dict() for s in (s0, s1)], stages_before)
        self.assertEqual([r.to_dict() for r in reqs], reqs_before)

    def test_outputs_do_not_share_references(self):
        s0 = _stage(0, count=1, multi=False, propellant=20.0)
        s1 = _stage(1, multi=True, propellant=8.0, dry=2.0)
        reqs = _req2(100.0, 100.0)
        plan = plan_post_parking_sequence('R', [s0, s1], reqs, 36.0, 1.0)
        plan.planned_final_stages[1].metadata['mutated'] = True
        plan.metadata['stage_order'].append(99)
        plan.requirements[1].metadata['mutated'] = True
        self.assertNotIn('mutated', s1.metadata)
        self.assertEqual(s1.metadata, {'idx': 1})
        self.assertNotIn('mutated', reqs[1].metadata)
        self.assertEqual(reqs[1].metadata, {})

    def test_deterministic_planning(self):
        s0 = _stage(0, multi=False, propellant=20.0)
        s1 = _stage(1, multi=True, propellant=8.0, dry=2.0)
        plan1 = plan_post_parking_sequence('R', [s0, s1], _req2(100.0, 100.0),
                                           36.0, 1.0)
        plan2 = plan_post_parking_sequence('R', [s0, s1], _req2(100.0, 100.0),
                                           36.0, 1.0)
        self.assertEqual(plan1.to_dict(), plan2.to_dict())

    def test_results_json_dumpable(self):
        s0 = _stage(0, count=1, multi=False, propellant=20.0)
        s1 = _stage(1, multi=True, propellant=8.0, dry=2.0)
        cases = [
            plan_post_parking_sequence('R', [s0, s1], _req2(100.0, 100.0),
                                       36.0, 1.0),
            plan_post_parking_sequence('R', [_stage(0, count=1, multi=False)],
                                       [_req(100.0, 'b1', 1)], 26.0, 1.0),
            plan_post_parking_sequence('R', [_stage(0, isp=None)],
                                       [_req(100.0, 'b1', 1)], 26.0, 1.0),
        ]
        for plan in cases:
            self.assertIsInstance(plan, PostParkingPlanResult)
            json.dumps(plan.to_dict(), ensure_ascii=False)


class PlanExecutionTests(unittest.TestCase):
    def test_plan_executes_and_matches_predictions(self):
        s0 = _stage(0, multi=False, propellant=20.0)
        s1 = _stage(1, multi=True, propellant=8.0, dry=2.0)
        original = [s0, s1]
        reqs = _req2(100.0, 100.0)
        plan = plan_post_parking_sequence('R', original, reqs, 36.0, 1.0)
        self.assertTrue(plan.success)
        final, mission = simulate_multistage_sequence(
            'R', original, plan.requirements, 36.0, 1.0,
            separations_before=plan.separations_before)
        self.assertTrue(mission.success)
        # every real burn executed on the assigned stage
        self.assertEqual([b.stage_index for b in mission.burns],
                         plan.metadata['assigned_stage_indices'])
        # final vehicle mass matches prediction
        self.assertAlmostEqual(mission.metadata['final_vehicle_mass_t'],
                               plan.planned_final_vehicle_mass_t, places=9)
        # every final stage matches prediction exactly
        for predicted, executed in zip(plan.planned_final_stages, final):
            self.assertEqual(predicted.to_dict(), executed.to_dict())


class NeutronIntegrationTests(unittest.TestCase):
    def _parking_state(self):
        config = load_config(
            Path('rockets/neutron_i_2630.yaml').read_text(encoding='utf-8'))
        stages = normalize_stages(config['stages']).stages
        payload = 2.5
        mass_above = payload
        dv_total = 0.0
        for s in reversed(stages):
            wet = s.wet_mass_t + mass_above
            dry = s.dry_mass_t + mass_above
            dv_total += s.isp_vac_s * G * math.log(wet / dry)
            mass_above += s.wet_mass_t
        replay = replay_serial_ascent_to_parking(
            'Neutron I 2630', stages, payload, dv_total - 1581.0)
        self.assertTrue(replay.success)
        return replay.stages, replay.final_vehicle_mass_t, payload

    def test_feasible_small_post_parking_mission(self):
        stages, vehicle, payload = self._parking_state()
        reqs = [_calib('park', 1),
                _req(300.0, 'b1', 2),
                _req(200.0, 'b2', 3)]
        plan = plan_post_parking_sequence('Neutron I 2630', stages, reqs,
                                          vehicle, payload)
        self.assertTrue(plan.success)
        # planner picks the second stage and treats both burns as restarts
        self.assertEqual(plan.metadata['assigned_stage_indices'], [1, 1])
        self.assertEqual(plan.separations_before, {})
        final, mission = simulate_multistage_sequence(
            'Neutron I 2630', stages, plan.requirements, vehicle, payload,
            separations_before=plan.separations_before)
        self.assertTrue(mission.success)
        self.assertEqual([b.ignition_kind for b in mission.burns[1:]],
                         [IgnitionKind.RESTART, IgnitionKind.RESTART])
        self.assertAlmostEqual(mission.metadata['final_vehicle_mass_t'],
                               plan.planned_final_vehicle_mass_t, places=9)
        for predicted, executed in zip(plan.planned_final_stages, final):
            self.assertEqual(predicted.to_dict(), executed.to_dict())

    def test_infeasible_geo_retrograde_combined_mission(self):
        from compute_c3_mass import compute_orbit_mission
        from mission_adapters import burn_sequence_to_requirements
        stages, vehicle, payload = self._parking_state()
        mission_model = compute_orbit_mission(
            35786, 180, orbit_type='circular', arrival_strategy='combined')
        converted = burn_sequence_to_requirements(mission_model['burn_sequence'])
        plan = plan_post_parking_sequence(
            'Neutron I 2630', stages, converted.requirements, vehicle, payload)
        self.assertFalse(plan.success)
        self.assertEqual(plan.failure_code, FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertIn('2450.7', plan.failure_reason_zh)
        self.assertIn('orbit_burn_2', plan.failure_reason_zh)
        self.assertIn('候选级 [1]', plan.failure_reason_zh)
        # no fabricated assignments or partial plans
        self.assertEqual(plan.separations_before, {})
        self.assertEqual(plan.metadata['assigned_stage_indices'], [])
        for r in plan.requirements:
            self.assertIsNone(r.preferred_stage_index)
        json.dumps(plan.to_dict(), ensure_ascii=False)


class DiagnosisRegressionTests(unittest.TestCase):
    def test_single_stage_no_whole_burn_false_positive(self):
        # b1 consumes most of the single stage; b2 fails.  The old code
        # wrongly claimed "multi-stage combined propellant may suffice" using
        # the mission-start state.  Now the diagnosis uses the b2 snapshot.
        s0 = _stage(0, multi=True, propellant=100.0, dry=5.0)
        plan = plan_post_parking_sequence(
            'R', [s0], [_req(6000.0, 'b1', 1), _req(3000.0, 'b2', 2)],
            106.0, 1.0)
        self.assertFalse(plan.success)
        self.assertEqual(plan.failure_code, FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertEqual(plan.metadata['failure_burn_id'], 'b2')
        self.assertNotIn('whole_burn_per_stage', plan.failure_reason_zh)
        self.assertFalse(plan.metadata['whole_burn_limitation_triggered'])
        self.assertFalse(any('跨级' in w for w in plan.warnings))
        # aggregate uses the b2-start remaining propellant: 2941.995*ln(13.79/6)
        consumed1 = 106.0 * (-math.expm1(-6000.0 / (300.0 * G)))
        leftover = 100.0 - consumed1
        snapshot_mass = 106.0 - consumed1
        expected_agg = 300.0 * G * (-math.log1p(-leftover / snapshot_mass))
        self.assertAlmostEqual(plan.metadata['aggregate_reachable_dv_m_s'],
                               expected_agg, places=6)
        self.assertLess(plan.metadata['aggregate_reachable_dv_m_s'], 3000.0)
        self.assertAlmostEqual(plan.metadata['failure_snapshot_vehicle_mass_t'],
                               snapshot_mass, places=9)
        self.assertEqual(plan.metadata['candidate_failure_codes'],
                         ['propellant_insufficient'])

    def test_historical_propellant_not_reused_in_aggregate(self):
        # Initial combined capacity of both stages exceeds b2's target, but
        # after b1 consumed stage 0's propellant the snapshot aggregate is
        # insufficient -> no whole_burn claim.
        s0 = _stage(0, multi=True, propellant=10.0, dry=2.0)
        s1 = _stage(1, multi=True, propellant=4.0, dry=2.0)
        plan = plan_post_parking_sequence(
            'R', [s0, s1], [_req(1000.0, 'b1', 1), _req(3000.0, 'b2', 2)],
            23.0, 1.0)
        self.assertFalse(plan.success)
        self.assertEqual(plan.failure_code, FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertEqual(plan.metadata['failure_burn_id'], 'b2')
        self.assertNotIn('whole_burn_per_stage', plan.failure_reason_zh)
        self.assertFalse(plan.metadata['whole_burn_limitation_triggered'])
        # independent snapshot aggregate
        consumed1 = 23.0 * (-math.expm1(-1000.0 / (300.0 * G)))
        leftover0 = 10.0 - consumed1
        mass_after_b1 = 23.0 - consumed1
        agg0 = 300.0 * G * (-math.log1p(-leftover0 / mass_after_b1))
        mass_after_sep0 = mass_after_b1 - (2.0 + leftover0)
        agg1 = 300.0 * G * (-math.log1p(-4.0 / mass_after_sep0))
        expected_agg = agg0 + agg1
        self.assertAlmostEqual(plan.metadata['aggregate_reachable_dv_m_s'],
                               expected_agg, places=6)
        self.assertLess(expected_agg, 3000.0)

    def test_true_whole_burn_limitation_detected(self):
        # Two stages, neither can complete 300 m/s alone, but the sequential
        # aggregate (with staging) reaches it -> whole_burn limitation.
        s0 = _stage(0, isp=150.0, propellant=0.5, dry=5.0, multi=True)
        s1 = _stage(1, isp=150.0, propellant=0.5, dry=2.0, multi=True)
        plan = plan_post_parking_sequence('R', [s0, s1],
                                          [_req(300.0, 'b1', 1)], 9.0, 1.0)
        self.assertFalse(plan.success)
        self.assertEqual(plan.failure_code, FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertIn('whole_burn_per_stage', plan.failure_reason_zh)
        self.assertTrue(plan.metadata['whole_burn_limitation_triggered'])
        self.assertTrue(any('跨级' in w for w in plan.warnings))
        expected_agg = (150.0 * G * (-math.log1p(-0.5 / 9.0))
                        + 150.0 * G * (-math.log1p(-0.5 / 3.5)))
        self.assertAlmostEqual(plan.metadata['aggregate_reachable_dv_m_s'],
                               expected_agg, places=6)
        self.assertGreaterEqual(plan.metadata['aggregate_reachable_dv_m_s'],
                                300.0)

    def test_single_stage_never_triggers_cross_stage_hint(self):
        s0 = _stage(0, multi=True, propellant=1.0, dry=5.0)
        plan = plan_post_parking_sequence('R', [s0], [_req(500.0, 'b1', 1)],
                                          7.0, 1.0)
        self.assertFalse(plan.success)
        self.assertEqual(plan.failure_code, FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertNotIn('whole_burn_per_stage', plan.failure_reason_zh)
        self.assertFalse(plan.metadata['whole_burn_limitation_triggered'])
        self.assertEqual(plan.metadata['failure_attached_stage_indices'], [0])

    def test_restart_blocked_stages_excluded_from_aggregate(self):
        s0 = _stage(0, count=1, multi=False, propellant=20.0, dry=2.0)
        s1 = _stage(1, multi=True, propellant=0.2, dry=1.0)
        plan = plan_post_parking_sequence('R', [s0, s1],
                                          [_req(400.0, 'b1', 1)], 24.2, 1.0)
        self.assertFalse(plan.success)
        self.assertEqual(plan.failure_code, FailureCode.PROPELLANT_INSUFFICIENT)
        # only stage 1 can contribute: avail at 2.2 t with 0.2 t propellant
        expected_agg = 300.0 * G * (-math.log1p(-0.2 / 2.2))
        self.assertAlmostEqual(plan.metadata['aggregate_reachable_dv_m_s'],
                               expected_agg, places=6)
        self.assertLess(expected_agg, 400.0)
        self.assertFalse(plan.metadata['whole_burn_limitation_triggered'])

    def test_mixed_candidate_failures_priority(self):
        s0 = _stage(0, count=1, multi=False, propellant=20.0, dry=2.0)
        s1 = _stage(1, multi=True, propellant=0.2, dry=1.0)
        plan = plan_post_parking_sequence('R', [s0, s1],
                                          [_req(400.0, 'b1', 1)], 24.2, 1.0)
        self.assertEqual(plan.metadata['candidate_failure_codes'],
                         ['restart_unsupported', 'propellant_insufficient'])
        # DATA_INSUFFICIENT > PROPELLANT_INSUFFICIENT > RESTART_UNSUPPORTED
        self.assertEqual(plan.failure_code, FailureCode.PROPELLANT_INSUFFICIENT)

    def test_unexpected_failure_code_not_classified_as_restart(self):
        from mission_stage_planner import _classify_failure_code
        self.assertEqual(
            _classify_failure_code([FailureCode.NO_STAGE_ASSIGNED]),
            FailureCode.DATA_INSUFFICIENT)
        self.assertEqual(
            _classify_failure_code([FailureCode.PROPELLANT_INSUFFICIENT,
                                    FailureCode.RESTART_UNSUPPORTED]),
            FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertEqual(
            _classify_failure_code([FailureCode.RESTART_UNSUPPORTED,
                                    FailureCode.RESTART_UNSUPPORTED]),
            FailureCode.RESTART_UNSUPPORTED)
        self.assertEqual(_classify_failure_code([]),
                         FailureCode.NO_STAGE_ASSIGNED)

    def test_failure_diagnostics_name_first_unplannable_burn(self):
        s0 = _stage(0, multi=True, propellant=100.0, dry=5.0)
        plan = plan_post_parking_sequence(
            'R', [s0], [_req(6000.0, 'b1', 1), _req(3000.0, 'b2', 2),
                        _req(100.0, 'b3', 3)],
            106.0, 1.0)
        self.assertEqual(plan.metadata['failure_burn_id'], 'b2')
        self.assertEqual(plan.metadata['failure_burn_order'], 2)
        self.assertIn('b2', plan.failure_reason_zh)
        self.assertEqual(plan.metadata['assigned_stage_indices'], [])

    def test_candidate_available_dv_uses_post_separation_mass(self):
        s0 = _stage(0, count=1, multi=False, propellant=20.0, dry=2.0)
        s1 = _stage(1, multi=True, propellant=0.2, dry=1.0)
        plan = plan_post_parking_sequence('R', [s0, s1],
                                          [_req(400.0, 'b1', 1)], 24.2, 1.0)
        avail = plan.metadata['candidate_available_dv_m_s']
        self.assertEqual(len(avail), 2)
        # stage 0: no separation needed -> vehicle 24.2
        expected_s0 = 300.0 * G * (-math.log1p(-20.0 / 24.2))
        self.assertAlmostEqual(avail[0], expected_s0, places=6)
        # stage 1: stage 0 separated first -> vehicle 2.2
        expected_s1 = 300.0 * G * (-math.log1p(-0.2 / 2.2))
        self.assertAlmostEqual(avail[1], expected_s1, places=6)

    def test_untracked_mass_preserved_through_plan(self):
        s0 = _stage(0, multi=True, propellant=20.0, dry=5.0)
        s1 = _stage(1, multi=True, propellant=8.0, dry=2.0)
        plan = plan_post_parking_sequence('R', [s0, s1], _req2(100.0, 100.0),
                                          40.0, 1.0)
        self.assertTrue(plan.success)
        attached = sum(s.dry_mass_t + s.propellant_remaining_t
                       for s in plan.planned_final_stages if s.attached)
        self.assertAlmostEqual(plan.planned_final_vehicle_mass_t,
                               1.0 + 4.0 + attached, places=9)
        final, mission = simulate_multistage_sequence(
            'R', [s0, s1], plan.requirements, 40.0, 1.0,
            separations_before=plan.separations_before)
        self.assertTrue(mission.success)
        self.assertAlmostEqual(mission.metadata['final_vehicle_mass_t'],
                               plan.planned_final_vehicle_mass_t, places=9)

    def test_success_plan_still_executes_consistently(self):
        s0 = _stage(0, count=1, multi=False, propellant=20.0)
        s1 = _stage(1, multi=True, propellant=8.0, dry=2.0)
        original = [s0, s1]
        plan = plan_post_parking_sequence('R', original, _req2(100.0, 100.0),
                                          36.0, 1.0)
        self.assertTrue(plan.success)
        final, mission = simulate_multistage_sequence(
            'R', original, plan.requirements, 36.0, 1.0,
            separations_before=plan.separations_before)
        self.assertTrue(mission.success)
        self.assertEqual([b.stage_index for b in mission.burns],
                         plan.metadata['assigned_stage_indices'])
        self.assertAlmostEqual(mission.metadata['final_vehicle_mass_t'],
                               plan.planned_final_vehicle_mass_t, places=9)
        for predicted, executed in zip(plan.planned_final_stages, final):
            self.assertEqual(predicted.to_dict(), executed.to_dict())


if __name__ == '__main__':
    unittest.main()
