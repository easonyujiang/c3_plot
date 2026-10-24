import copy
import json
import math
import unittest
from pathlib import Path

from c3_mass.mission_adapters import normalize_stages
from c3_mass.mission_parking_replay import (
    DV_TOLERANCE_M_S,
    ParkingOrbitReplayResult,
    replay_serial_ascent_to_parking,
)
from c3_mass.mission_simulation import DataQuality, FailureCode, StageState
from server.yaml_codec import load_config

G = 9.80665


def _stage(index, propellant=20.0, isp=300.0, multi=False, count=0,
           attached=True, parallel=False, dry=5.0, wet=None):
    if wet is None:
        wet = dry + propellant  # full tank: wet - dry == propellant
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


def _avail(vehicle, propellant, isp=300.0):
    return isp * G * (-math.log1p(-propellant / vehicle))


def _req(vehicle, dv, isp=300.0):
    return vehicle * (-math.expm1(-dv / (isp * G)))


class SingleStageTests(unittest.TestCase):
    def test_partial_burn_reaches_parking(self):
        stage = _stage(0, propellant=20.0, dry=5.0, multi=False)
        result = replay_serial_ascent_to_parking('R', [stage], 1.0, 100.0)
        self.assertTrue(result.success)
        self.assertEqual(result.failure_code, FailureCode.NONE)
        self.assertEqual(result.active_stage_index, 0)
        self.assertEqual(result.achieved_dv_m_s, 100.0)
        self.assertEqual(result.remaining_dv_m_s, 0.0)
        self.assertEqual(len(result.burn_segments), 1)
        self.assertEqual(result.separation_events, [])
        expected_consumed = _req(26.0, 100.0)
        seg = result.burn_segments[0]
        self.assertEqual(seg['stage_index'], 0)
        self.assertEqual(seg['target_remaining_dv_m_s'], 100.0)
        self.assertEqual(seg['actual_dv_m_s'], 100.0)
        self.assertAlmostEqual(seg['propellant_consumed_t'],
                               expected_consumed, places=9)
        self.assertEqual(seg['ignition_count_after'], 1)
        self.assertFalse(seg['exhausted'])
        self.assertFalse(seg['continued_to_next_stage'])
        final = result.stages[0]
        self.assertTrue(final.attached)
        self.assertAlmostEqual(final.propellant_remaining_t,
                               20.0 - expected_consumed, places=9)
        self.assertAlmostEqual(result.final_vehicle_mass_t,
                               26.0 - expected_consumed, places=9)

    def test_exactly_exhausted_success_no_auto_separation(self):
        stage = _stage(0, propellant=10.0, dry=5.0)
        required_dv = _avail(16.0, 10.0)
        result = replay_serial_ascent_to_parking('R', [stage], 1.0,
                                                 required_dv)
        self.assertTrue(result.success)
        self.assertEqual(result.active_stage_index, 0)
        self.assertEqual(result.burn_segments[0]['exhausted'], True)
        self.assertEqual(result.separation_events, [])
        self.assertTrue(result.stages[0].attached)
        self.assertLessEqual(result.stages[0].propellant_remaining_t,
                             DV_TOLERANCE_M_S)
        self.assertEqual(result.remaining_dv_m_s, 0.0)

    def test_upper_stage_untouched_when_lower_completes(self):
        s0 = _stage(0, propellant=20.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        result = replay_serial_ascent_to_parking('R', [s0, s1], 1.0, 100.0)
        self.assertTrue(result.success)
        self.assertEqual(len(result.burn_segments), 1)
        self.assertEqual(result.active_stage_index, 0)
        upper = result.stages[1]
        self.assertEqual(upper.ignition_count, 0)
        self.assertEqual(upper.propellant_remaining_t, 8.0)
        self.assertTrue(upper.attached)

    def test_multi_ignition_false_does_not_block_first_ignition(self):
        stage = _stage(0, propellant=20.0, dry=5.0, multi=False)
        result = replay_serial_ascent_to_parking('R', [stage], 1.0, 100.0)
        self.assertTrue(result.success)
        self.assertEqual(result.burn_segments[0]['ignition_count_after'], 1)

    def test_tiny_positive_dv_consumes_positive_propellant(self):
        stage = _stage(0, propellant=20.0, dry=5.0)
        result = replay_serial_ascent_to_parking('R', [stage], 1.0, 1e-5)
        self.assertTrue(result.success)
        self.assertEqual(len(result.burn_segments), 1)
        self.assertGreater(result.burn_segments[0]['propellant_consumed_t'], 0.0)
        self.assertEqual(result.burn_segments[0]['actual_dv_m_s'], 1e-5)
        self.assertEqual(result.remaining_dv_m_s, 0.0)

    def test_sub_tolerance_remaining_produces_no_fake_burn(self):
        stage = _stage(0, propellant=20.0, dry=5.0)
        result = replay_serial_ascent_to_parking('R', [stage], 1.0, 1e-7)
        self.assertTrue(result.success)
        self.assertEqual(result.burn_segments, [])
        self.assertEqual(result.achieved_dv_m_s, 0.0)
        self.assertEqual(result.remaining_dv_m_s, 0.0)
        self.assertEqual(result.active_stage_index, 0)

    def test_tiny_remainder_after_exhaustion_is_tolerance_arrival(self):
        # stage 0 exhausts leaving 5e-7 m/s (< DV_TOLERANCE_M_S): this is a
        # successful arrival within tolerance.  The exhausted current stage
        # must stay attached and active — no separation, no extra burn.
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        available0 = _avail(17.0, 1.0)
        required_dv = available0 + 5e-7
        result = replay_serial_ascent_to_parking('R', [s0, s1], 1.0,
                                                 required_dv)
        self.assertTrue(result.success)
        self.assertEqual(result.failure_code, FailureCode.NONE)
        self.assertEqual(len(result.burn_segments), 1)
        seg = result.burn_segments[0]
        self.assertEqual(seg['stage_index'], 0)
        self.assertEqual(seg['continued_to_next_stage'], False)
        self.assertTrue(seg['exhausted'])
        # current stage stays attached and becomes active
        self.assertTrue(result.stages[0].attached)
        self.assertEqual(result.active_stage_index, 0)
        # no separation event, upper stage untouched
        self.assertEqual(result.separation_events, [])
        self.assertEqual(result.stages[1].ignition_count, 0)
        self.assertEqual(result.stages[1].propellant_remaining_t, 8.0)
        # achieved is the real value; gap to target is within tolerance
        self.assertAlmostEqual(result.achieved_dv_m_s, available0, places=9)
        self.assertLessEqual(abs(required_dv - result.achieved_dv_m_s),
                             DV_TOLERANCE_M_S)
        self.assertEqual(result.remaining_dv_m_s, 0.0)

    def test_remaining_above_tolerance_still_separates_and_continues(self):
        # gap 5e-6 m/s (> DV_TOLERANCE_M_S): the stage really cannot finish
        # the ascent, so it separates and the upper stage ignites.
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        available0 = _avail(17.0, 1.0)
        required_dv = available0 + 5e-6
        result = replay_serial_ascent_to_parking('R', [s0, s1], 1.0,
                                                 required_dv)
        self.assertTrue(result.success)
        self.assertEqual(len(result.burn_segments), 2)
        self.assertEqual(result.burn_segments[0]['continued_to_next_stage'],
                         True)
        self.assertEqual(len(result.separation_events), 1)
        self.assertEqual(result.separation_events[0]['stage_index'], 0)
        self.assertFalse(result.stages[0].attached)
        self.assertEqual(result.active_stage_index, 1)
        self.assertEqual(result.stages[1].ignition_count, 1)
        self.assertGreater(result.stages[1].propellant_remaining_t, 0.0)


class MultiStageTests(unittest.TestCase):
    def test_two_stage_exhaust_separate_ignite(self):
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        result = replay_serial_ascent_to_parking('R', [s0, s1], 1.0, 300.0)
        self.assertTrue(result.success)
        self.assertEqual(result.active_stage_index, 1)
        self.assertEqual(len(result.burn_segments), 2)
        self.assertEqual(len(result.separation_events), 1)
        available0 = _avail(17.0, 1.0)
        seg0, seg1 = result.burn_segments
        self.assertEqual(seg0['stage_index'], 0)
        self.assertAlmostEqual(seg0['actual_dv_m_s'], available0, places=9)
        self.assertTrue(seg0['exhausted'])
        self.assertTrue(seg0['continued_to_next_stage'])
        self.assertEqual(seg0['ignition_count_after'], 1)
        remaining = 300.0 - available0
        expected1 = _req(11.0, remaining)
        self.assertEqual(seg1['stage_index'], 1)
        self.assertAlmostEqual(seg1['target_remaining_dv_m_s'], remaining,
                               places=9)
        self.assertAlmostEqual(seg1['actual_dv_m_s'], remaining, places=9)
        self.assertAlmostEqual(seg1['propellant_consumed_t'], expected1,
                               places=9)
        self.assertFalse(seg1['exhausted'])
        self.assertFalse(seg1['continued_to_next_stage'])
        event = result.separation_events[0]
        self.assertEqual(event['stage_index'], 0)
        self.assertEqual(event['reason'], 'ascent_stage_exhausted')
        self.assertEqual(event['discarded_mass_t'], 5.0)
        self.assertAlmostEqual(event['mass_before_t'], 16.0, places=9)
        self.assertAlmostEqual(event['mass_after_t'], 11.0, places=9)
        final0, final1 = result.stages
        self.assertFalse(final0.attached)
        self.assertEqual(final0.propellant_remaining_t, 0.0)
        self.assertEqual(final0.ignition_count, 1)
        self.assertTrue(final1.attached)
        self.assertEqual(final1.ignition_count, 1)
        self.assertAlmostEqual(final1.propellant_remaining_t,
                               8.0 - expected1, places=9)
        self.assertAlmostEqual(result.final_vehicle_mass_t,
                               11.0 - expected1, places=9)

    def test_three_stage_two_separations_third_completes(self):
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=1.0, dry=3.0)
        s2 = _stage(2, propellant=8.0, dry=2.0, multi=True)
        result = replay_serial_ascent_to_parking(
            'R', [s0, s1, s2], 1.0, 400.0)
        self.assertTrue(result.success)
        self.assertEqual(result.active_stage_index, 2)
        self.assertEqual(len(result.burn_segments), 3)
        self.assertEqual(len(result.separation_events), 2)
        self.assertEqual([e['stage_index'] for e in result.separation_events],
                         [0, 1])
        self.assertEqual([e['reason'] for e in result.separation_events],
                         ['ascent_stage_exhausted', 'ascent_stage_exhausted'])
        available0 = _avail(21.0, 1.0)
        available1 = _avail(15.0, 1.0)
        remaining2 = 400.0 - available0 - available1
        expected2 = _req(11.0, remaining2)
        self.assertAlmostEqual(result.burn_segments[2]['actual_dv_m_s'],
                               remaining2, places=9)
        self.assertAlmostEqual(result.burn_segments[2]['propellant_consumed_t'],
                               expected2, places=9)
        self.assertEqual([s.ignition_count for s in result.stages], [1, 1, 1])
        self.assertEqual([s.attached for s in result.stages],
                         [False, False, True])
        self.assertAlmostEqual(result.final_vehicle_mass_t,
                               11.0 - expected2, places=9)

    def test_lower_stage_must_exhaust_before_upper_ignites(self):
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        result = replay_serial_ascent_to_parking('R', [s0, s1], 1.0, 300.0)
        seg0, seg1 = result.burn_segments
        self.assertEqual(seg0['stage_index'], 0)
        self.assertEqual(seg1['stage_index'], 1)
        # upper stage burns only after the lower one was separated
        available0 = _avail(17.0, 1.0)
        self.assertAlmostEqual(seg1['mass_before_t'], 11.0, places=9)
        self.assertEqual(seg0['continued_to_next_stage'], True)

    def test_non_contiguous_indices_follow_list_order(self):
        s3 = _stage(3, propellant=20.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        result = replay_serial_ascent_to_parking('R', [s3, s1], 1.0, 100.0)
        self.assertTrue(result.success)
        self.assertEqual(result.burn_segments[0]['stage_index'], 3)
        self.assertEqual(result.active_stage_index, 3)
        self.assertEqual(result.metadata['stage_order'], [3, 1])
        self.assertEqual(result.stages[1].ignition_count, 0)

    def test_burn_segment_fields_complete_and_ordered(self):
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        result = replay_serial_ascent_to_parking('R', [s0, s1], 1.0, 300.0)
        required_keys = {
            'stage_index', 'target_remaining_dv_m_s', 'actual_dv_m_s',
            'propellant_consumed_t', 'propellant_remaining_t',
            'mass_before_t', 'mass_after_burn_t', 'ignition_count_after',
            'exhausted', 'continued_to_next_stage',
        }
        for seg in result.burn_segments:
            self.assertEqual(set(seg), required_keys)
        event_keys = {'stage_index', 'reason', 'discarded_mass_t',
                      'mass_before_t', 'mass_after_t'}
        for event in result.separation_events:
            self.assertEqual(set(event), event_keys)


class InsufficientTests(unittest.TestCase):
    def test_total_propellant_shortfall_returns_insufficient(self):
        stage = _stage(0, propellant=1.0, dry=5.0)
        result = replay_serial_ascent_to_parking('R', [stage], 1.0, 500.0)
        self.assertFalse(result.success)
        self.assertEqual(result.failure_code,
                         FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertIn('500', result.failure_reason_zh)
        self.assertIn('推进剂', result.failure_reason_zh)
        available = _avail(7.0, 1.0)
        self.assertAlmostEqual(result.achieved_dv_m_s, available, places=9)
        self.assertAlmostEqual(result.remaining_dv_m_s, 500.0 - available,
                               places=9)
        self.assertGreater(result.remaining_dv_m_s, 0.0)
        final = result.stages[0]
        self.assertTrue(final.attached)
        self.assertEqual(final.propellant_remaining_t, 0.0)
        self.assertEqual(final.ignition_count, 1)
        self.assertIsNone(result.active_stage_index)
        self.assertEqual(len(result.separation_events), 0)

    def test_failure_preserves_prior_burns_and_separations(self):
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=1.0, dry=3.0)
        s2 = _stage(2, propellant=8.0, dry=2.0)
        result = replay_serial_ascent_to_parking('R', [s0, s1, s2], 1.0, 4200.0)
        self.assertFalse(result.success)
        self.assertEqual(result.failure_code,
                         FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertEqual(len(result.burn_segments), 3)
        self.assertEqual(len(result.separation_events), 2)
        self.assertEqual([s.attached for s in result.stages],
                         [False, False, True])
        self.assertEqual([s.ignition_count for s in result.stages],
                         [1, 1, 1])
        self.assertEqual(result.stages[2].propellant_remaining_t, 0.0)
        available0 = _avail(21.0, 1.0)
        available1 = _avail(15.0, 1.0)
        available2 = _avail(11.0, 8.0)
        self.assertAlmostEqual(
            result.achieved_dv_m_s,
            available0 + available1 + available2, places=9)


class DataInsufficientTests(unittest.TestCase):
    def test_no_partial_burns_or_separations_on_data_insufficient(self):
        for stage in (_stage(0, parallel=True), _stage(0, isp=None)):
            result = replay_serial_ascent_to_parking('R', [stage], 1.0, 300.0)
            self.assertFalse(result.success)
            self.assertEqual(result.failure_code, FailureCode.DATA_INSUFFICIENT)
            self.assertEqual(result.data_quality, DataQuality.INSUFFICIENT)
            self.assertEqual(result.burn_segments, [])
            self.assertEqual(result.separation_events, [])
            self.assertEqual(result.achieved_dv_m_s, 0.0)
            self.assertEqual(result.remaining_dv_m_s, 300.0)
            self.assertIsNone(result.active_stage_index)
            self.assertEqual(result.stages[0].propellant_remaining_t, 20.0)
            self.assertEqual(result.stages[0].ignition_count, 0)
            self.assertTrue(result.stages[0].attached)
            self.assertTrue(result.warnings)

    def test_data_insufficient_warnings_name_stage_and_reason(self):
        stage = _stage(0, isp=None)
        result = replay_serial_ascent_to_parking('R', [stage], 1.0, 300.0)
        warning = result.failure_reason_zh
        self.assertIn('stage 0', warning)
        self.assertIn('比冲', warning)
        stage2 = _stage(1, parallel=True)
        result2 = replay_serial_ascent_to_parking('R', [stage2], 1.0, 300.0)
        self.assertIn('stage 0', result2.failure_reason_zh)
        self.assertIn('并联', result2.failure_reason_zh)


class ValidationTests(unittest.TestCase):
    def test_invalid_calling_arguments_rejected(self):
        stage = _stage(0)
        with self.assertRaises(ValueError):
            replay_serial_ascent_to_parking('', [stage], 1.0, 300.0)
        with self.assertRaises(ValueError):
            replay_serial_ascent_to_parking('R', [], 1.0, 300.0)
        with self.assertRaises(ValueError):
            replay_serial_ascent_to_parking('R', 'nope', 1.0, 300.0)
        with self.assertRaises(ValueError):
            replay_serial_ascent_to_parking('R', ['x'], 1.0, 300.0)
        with self.assertRaises(ValueError):
            replay_serial_ascent_to_parking('R', [_stage(0), _stage(0)], 1.0,
                                            300.0)
        with self.assertRaises(ValueError):
            replay_serial_ascent_to_parking('R', [stage], -1.0, 300.0)
        with self.assertRaises(ValueError):
            replay_serial_ascent_to_parking('R', [stage], 1.0, 0.0)
        with self.assertRaises(ValueError):
            replay_serial_ascent_to_parking('R', [stage], 1.0, float('nan'))

    def test_partially_flown_states_rejected(self):
        with self.assertRaises(ValueError):
            replay_serial_ascent_to_parking('R', [_stage(0, attached=False)],
                                            1.0, 300.0)
        with self.assertRaises(ValueError):
            replay_serial_ascent_to_parking('R', [_stage(0, count=1)], 1.0,
                                            300.0)
        with self.assertRaises(ValueError):
            replay_serial_ascent_to_parking(
                'R', [_stage(0, propellant=19.0, wet=25.0)], 1.0, 300.0)


class BookkeepingTests(unittest.TestCase):
    def test_mass_conservation_per_burn_and_separation(self):
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        result = replay_serial_ascent_to_parking('R', [s0, s1], 1.0, 300.0)
        for seg in result.burn_segments:
            self.assertAlmostEqual(seg['mass_after_burn_t'],
                                   seg['mass_before_t']
                                   - seg['propellant_consumed_t'], places=9)
        for event in result.separation_events:
            self.assertAlmostEqual(event['mass_after_t'],
                                   event['mass_before_t']
                                   - event['discarded_mass_t'], places=9)
        self.assertTrue(result.metadata['mass_conservation_verified'])

    def test_separation_discards_only_dry_mass(self):
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        result = replay_serial_ascent_to_parking('R', [s0, s1], 1.0, 300.0)
        event = result.separation_events[0]
        self.assertEqual(event['discarded_mass_t'], 5.0)

    def test_final_mass_equals_payload_plus_attached_masses(self):
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        result = replay_serial_ascent_to_parking('R', [s0, s1], 1.0, 300.0)
        attached = sum(s.dry_mass_t + s.propellant_remaining_t
                       for s in result.stages if s.attached)
        self.assertAlmostEqual(result.final_vehicle_mass_t, 1.0 + attached,
                               places=9)

    def test_inputs_never_modified(self):
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        s0.metadata = {'deep': {'a': [1, 2]}}
        before = [s.to_dict() for s in (s0, s1)]
        replay_serial_ascent_to_parking('R', [s0, s1], 1.0, 300.0)
        replay_serial_ascent_to_parking('R', [s0, s1], 1.0, 9999.0)
        replay_serial_ascent_to_parking('R', [_stage(0, isp=None)], 1.0, 300.0)
        self.assertEqual([s.to_dict() for s in (s0, s1)], before)

    def test_outputs_do_not_share_references(self):
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        result = replay_serial_ascent_to_parking('R', [s0, s1], 1.0, 300.0)
        result.stages[0].metadata['mutated'] = True
        result.metadata['stage_order'].append(99)
        self.assertNotIn('mutated', s0.metadata)
        self.assertEqual(s0.metadata, {'idx': 0})
        self.assertEqual(result.metadata['stage_order'], [0, 1, 99])
        self.assertIsNot(result.stages[0].metadata, s0.metadata)

    def test_results_json_dumpable(self):
        s0 = _stage(0, propellant=1.0, dry=5.0)
        s1 = _stage(1, propellant=8.0, dry=2.0, multi=True)
        for args in (([s0, s1], 300.0), ([s0, s1], 9999.0),
                     ([_stage(0, isp=None)], 300.0)):
            result = replay_serial_ascent_to_parking('R', args[0], 1.0,
                                                     args[1])
            self.assertIsInstance(result, ParkingOrbitReplayResult)
            json.dumps(result.to_dict(), ensure_ascii=False)


class NeutronIntegrationTests(unittest.TestCase):
    @unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
    def test_neutron_i_2630_parking_replay_probe(self):
        config = load_config(
            Path('rockets/neutron_i_2630.yaml').read_text(encoding='utf-8'))
        normalized = normalize_stages(config['stages'])
        stages = normalized.stages
        payload = 2.5

        # independent total vacuum delta-v for payload 2.5 t
        mass_above = payload
        dv_total = 0.0
        for s in reversed(stages):
            wet = s.wet_mass_t + mass_above
            dry = s.dry_mass_t + mass_above
            dv_total += s.isp_vac_s * G * math.log(wet / dry)
            mass_above += s.wet_mass_t

        # calibration point: 2.5 t / 220 km / dv_rem 1581
        ascent_required = dv_total - 1581.0

        result = replay_serial_ascent_to_parking(
            'Neutron I 2630', stages, payload, ascent_required)
        self.assertTrue(result.success)
        self.assertEqual(result.failure_code, FailureCode.NONE)
        # first stage exhausted and separated, second stage active
        self.assertFalse(result.stages[0].attached)
        self.assertEqual(result.stages[0].ignition_count, 1)
        self.assertEqual(result.active_stage_index, 1)
        self.assertEqual(result.stages[1].ignition_count, 1)
        self.assertTrue(result.stages[1].attached)
        self.assertGreater(result.stages[1].propellant_remaining_t, 0.0)
        self.assertEqual(len(result.separation_events), 1)
        self.assertEqual(result.separation_events[0]['stage_index'], 0)

        # independent remaining delta-v from the replayed state ~= 1581
        active = result.stages[1]
        remaining_vehicle = result.final_vehicle_mass_t
        remaining_dv = active.isp_vac_s * G * math.log(
            remaining_vehicle / (remaining_vehicle
                                 - active.propellant_remaining_t))
        self.assertAlmostEqual(remaining_dv, 1581.0, delta=2.0)

        json.dumps(result.to_dict(), ensure_ascii=False)


if __name__ == '__main__':
    unittest.main()
