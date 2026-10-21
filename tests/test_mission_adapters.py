import copy
import json
import math
import unittest
from pathlib import Path

import numpy as np

from c3_mass.compute_c3_mass import compute_orbit_mission
from c3_mass.engine_parser import extract_engine_params
from c3_mass.mission_adapters import (
    BurnSequenceConversionResult,
    StageNormalizationResult,
    burn_sequence_to_requirements,
    normalize_stages,
)
from c3_mass.mission_simulation import DataQuality
from server.yaml_codec import load_config


def _two_stage_cfg():
    return [
        {
            'wet_mass_t': 132.03, 'dry_mass_t': 6.046,
            'engines': {'cfg': 'SC-ENG-Merlin-1B.cfg', 'count': 6},
            'multi_ignition': False, 'parallel': False,
        },
        {
            'wet_mass_t': 19.467, 'dry_mass_t': 0.899,
            'engines': {'cfg': 'SC-ENG-LR81-8096.cfg', 'count': 3},
            'multi_ignition': True, 'parallel': False,
        },
    ]


def _standard_sequence():
    return [
        {'order': 1, 'name': '进入初始停泊轨道', 'source': 'rocket_calibration',
         'dv_m_s': 7797.8},
        {'order': 2, 'name': '霍曼转移出发点火', 'source': 'mission_model',
         'dv_m_s': 2451.0},
        {'order': 3, 'name': '目标点圆化与平面变化合并点火',
         'source': 'mission_model', 'dv_m_s': 1661.0},
    ]


class NormalizeStagesTests(unittest.TestCase):
    def test_two_serial_stages_created_one_to_one(self):
        result = normalize_stages(_two_stage_cfg())
        self.assertIsInstance(result, StageNormalizationResult)
        self.assertEqual(len(result.stages), 2)
        self.assertEqual([s.stage_index for s in result.stages], [0, 1])
        self.assertTrue(result.ready_for_serial_replay)
        self.assertEqual(result.data_quality, DataQuality.EXACT)
        self.assertEqual(result.warnings, [])
        s0, s1 = result.stages
        self.assertEqual(s0.wet_mass_t, 132.03)
        self.assertEqual(s0.dry_mass_t, 6.046)
        self.assertAlmostEqual(s0.propellant_remaining_t, 132.03 - 6.046,
                               places=9)
        self.assertEqual(s0.multi_ignition, False)
        self.assertEqual(s1.multi_ignition, True)
        self.assertEqual(s0.parallel, False)
        self.assertEqual(s0.ignition_count, 0)
        self.assertTrue(s0.attached)
        self.assertEqual(s1.wet_mass_t, 19.467)
        self.assertEqual(s1.dry_mass_t, 0.899)
        self.assertAlmostEqual(s1.propellant_remaining_t, 19.467 - 0.899,
                               places=9)

    def test_input_order_preserved_no_sorting(self):
        cfg = _two_stage_cfg()
        result = normalize_stages(cfg)
        self.assertEqual([s.wet_mass_t for s in result.stages],
                         [132.03, 19.467])

    def test_engine_groups_combined_not_reduced_to_first(self):
        cfg = {
            'wet_mass_t': 10.0, 'dry_mass_t': 2.0,
            'engines': {'cfg': 'SC-ENG-Merlin-1A.cfg', 'count': 1},
            'engine_groups': [
                {'cfg': 'SC-ENG-Merlin-1A.cfg', 'count': 2},
                {'cfg': 'SC-ENG-F1.cfg', 'count': 1},
            ],
        }
        result = normalize_stages([cfg])
        stage = result.stages[0]
        ep1 = extract_engine_params('SC-ENG-Merlin-1A.cfg')
        ep2 = extract_engine_params('SC-ENG-F1.cfg')
        thrust_total = 2.0 * ep1['thrust_kN'] + 1.0 * ep2['thrust_kN']
        mass_flow = (2.0 * ep1['thrust_kN'] / ep1['isp_vac_s']
                     + ep2['thrust_kN'] / ep2['isp_vac_s'])
        expected_isp = thrust_total / mass_flow
        self.assertAlmostEqual(stage.isp_vac_s, expected_isp, places=6)
        self.assertEqual(stage.metadata['engine_cfg'],
                         ['SC-ENG-Merlin-1A.cfg', 'SC-ENG-F1.cfg'])
        self.assertEqual(stage.metadata['engine_count_total'], 3)

    def test_inputs_completely_unmodified(self):
        cfg = _two_stage_cfg()
        before = copy.deepcopy(cfg)
        normalize_stages(cfg)
        self.assertEqual(cfg, before)

    def test_output_metadata_does_not_share_references(self):
        cfg = _two_stage_cfg()
        result = normalize_stages(cfg)
        result.stages[0].metadata['mutated'] = True
        result.metadata['extra'] = 1
        self.assertNotIn('mutated', cfg[0])
        self.assertEqual(result.metadata['stage_count'], 2)

    def test_parallel_stage_kept_one_to_one_not_expanded(self):
        cfg = _two_stage_cfg()
        cfg[0]['parallel'] = True
        result = normalize_stages(cfg)
        self.assertEqual(len(result.stages), 2)
        self.assertTrue(result.stages[0].parallel)
        self.assertFalse(result.ready_for_serial_replay)
        self.assertEqual(result.data_quality, DataQuality.INSUFFICIENT)
        self.assertTrue(any('并联' in w for w in result.warnings))

    def test_missing_engine_cfg_yields_none_isp(self):
        cfg = _two_stage_cfg()
        cfg[0]['engines'] = {'cfg': 'Nope.cfg', 'count': 1}
        result = normalize_stages(cfg)
        self.assertIsNone(result.stages[0].isp_vac_s)
        self.assertEqual(result.stages[1].isp_vac_s, 300.0)
        self.assertFalse(result.ready_for_serial_replay)
        self.assertEqual(result.data_quality, DataQuality.INSUFFICIENT)

    def test_engine_warning_contains_stage_index_and_reason(self):
        cfg = _two_stage_cfg()
        cfg[0]['engines'] = {'cfg': 'Nope.cfg', 'count': 1}
        result = normalize_stages(cfg)
        self.assertTrue(result.warnings)
        warning = result.warnings[0]
        self.assertIn('stage 0', warning)
        self.assertIn('Nope.cfg', warning)
        self.assertGreater(len(warning), 20)

    def test_invalid_arguments_raise_value_error(self):
        with self.assertRaises(ValueError):
            normalize_stages([])
        with self.assertRaises(ValueError):
            normalize_stages('not-a-list')
        with self.assertRaises(ValueError):
            normalize_stages([{'wet_mass_t': -1.0, 'dry_mass_t': 0.5,
                               'engines': {'cfg': 'SC-ENG-F1.cfg', 'count': 1}}])
        with self.assertRaises(ValueError):
            normalize_stages([{'wet_mass_t': 1.0, 'dry_mass_t': 2.0,
                               'engines': {'cfg': 'SC-ENG-F1.cfg', 'count': 1}}])
        with self.assertRaises(ValueError):
            normalize_stages([{'wet_mass_t': True, 'dry_mass_t': 0.5,
                               'engines': {'cfg': 'SC-ENG-F1.cfg', 'count': 1}}])
        with self.assertRaises(ValueError):
            normalize_stages([{'wet_mass_t': 1.0, 'dry_mass_t': 0.5,
                               'engines': {'cfg': 'SC-ENG-F1.cfg', 'count': 1},
                               'parallel': 'yes'}])
        with self.assertRaises(ValueError):
            normalize_stages(['not-a-dict'])

    def test_results_json_dumpable(self):
        result = normalize_stages(_two_stage_cfg())
        json.dumps(result.to_dict(), ensure_ascii=False)


class BurnSequenceConversionTests(unittest.TestCase):
    def test_standard_three_burn_sequence(self):
        result = burn_sequence_to_requirements(_standard_sequence())
        self.assertIsInstance(result, BurnSequenceConversionResult)
        self.assertEqual(len(result.requirements), 3)
        self.assertEqual(result.skipped_entries, [])
        reqs = result.requirements
        self.assertEqual([r.order for r in reqs], [1, 2, 3])
        self.assertEqual([r.burn_id for r in reqs],
                         ['orbit_burn_1', 'orbit_burn_2', 'orbit_burn_3'])
        self.assertEqual([r.target_dv_m_s for r in reqs],
                         [7797.8, 2451.0, 1661.0])
        self.assertEqual(reqs[0].source, 'rocket_calibration')
        for r in reqs:
            self.assertIsNone(r.preferred_stage_index)

    def test_calibration_entry_preserved_with_zero_dv(self):
        seq = [{'order': 1, 'name': 'park', 'source': 'rocket_calibration',
                'dv_m_s': 0.0},
               {'order': 2, 'name': 'dep', 'source': 'mission_model',
                'dv_m_s': 500.0}]
        result = burn_sequence_to_requirements(seq)
        self.assertEqual(len(result.requirements), 2)
        self.assertEqual(result.requirements[0].source, 'rocket_calibration')
        self.assertEqual(result.requirements[0].target_dv_m_s, 0.0)
        self.assertIsNone(result.requirements[0].preferred_stage_index)
        self.assertEqual(result.requirements[1].target_dv_m_s, 500.0)

    def test_below_threshold_entries_skipped_and_orders_not_renumbered(self):
        seq = [
            {'order': 1, 'name': 'park', 'source': 'rocket_calibration',
             'dv_m_s': 0.0},
            {'order': 2, 'name': 'tiny', 'source': 'mission_model',
             'dv_m_s': 0.0},
            {'order': 3, 'name': 'real', 'source': 'mission_model',
             'dv_m_s': 500.0},
        ]
        result = burn_sequence_to_requirements(seq)
        self.assertEqual(len(result.requirements), 2)
        self.assertEqual([r.order for r in result.requirements], [1, 3])
        self.assertEqual(len(result.skipped_entries), 1)
        skipped = result.skipped_entries[0]
        self.assertEqual(skipped['order'], 2)
        self.assertEqual(skipped['name'], 'tiny')
        self.assertEqual(skipped['source'], 'mission_model')
        self.assertEqual(skipped['dv_m_s'], 0.0)
        self.assertEqual(skipped['reason'], 'below_ignition_threshold')
        self.assertTrue(any('跳过' in w for w in result.warnings))

    def test_exact_threshold_is_skipped(self):
        seq = [{'order': 1, 'name': 'edge', 'source': 'mission_model',
                'dv_m_s': 0.1}]
        result = burn_sequence_to_requirements(seq)
        self.assertEqual(len(result.requirements), 0)
        self.assertEqual(len(result.skipped_entries), 1)

    def test_burn_id_default_not_from_chinese_name(self):
        result = burn_sequence_to_requirements(_standard_sequence())
        self.assertEqual(result.requirements[0].burn_id, 'orbit_burn_1')
        self.assertNotIn('进入', result.requirements[0].burn_id)

    def test_explicit_burn_id_preserved(self):
        seq = [{'order': 1, 'name': 'park', 'source': 'rocket_calibration',
                'dv_m_s': 0.0, 'burn_id': 'ascent'}]
        result = burn_sequence_to_requirements(seq)
        self.assertEqual(result.requirements[0].burn_id, 'ascent')

    def test_invalid_sequences_rejected(self):
        base = {'order': 1, 'name': 'x', 'source': 'mission_model', 'dv_m_s': 10.0}
        with self.assertRaises(ValueError):
            burn_sequence_to_requirements([])
        with self.assertRaises(ValueError):
            burn_sequence_to_requirements('nope')
        with self.assertRaises(ValueError):
            burn_sequence_to_requirements([{'order': 0, 'name': 'x',
                                            'source': 'm', 'dv_m_s': 1.0}])
        with self.assertRaises(ValueError):
            burn_sequence_to_requirements([{'order': True, 'name': 'x',
                                            'source': 'm', 'dv_m_s': 1.0}])
        with self.assertRaises(ValueError):
            burn_sequence_to_requirements([{'order': 2, 'name': 'x',
                                            'source': 'm', 'dv_m_s': 1.0},
                                           {'order': 1, 'name': 'y',
                                            'source': 'm', 'dv_m_s': 1.0}])
        with self.assertRaises(ValueError):
            burn_sequence_to_requirements([{**base, 'dv_m_s': float('nan')}])
        with self.assertRaises(ValueError):
            burn_sequence_to_requirements([{**base, 'dv_m_s': float('inf')}])
        with self.assertRaises(ValueError):
            burn_sequence_to_requirements([{**base, 'dv_m_s': -1.0}])
        with self.assertRaises(ValueError):
            burn_sequence_to_requirements([{**base, 'source': ''}])
        with self.assertRaises(ValueError):
            burn_sequence_to_requirements([{**base, 'name': 5}])
        with self.assertRaises(ValueError):
            burn_sequence_to_requirements([{**base, 'burn_id': 'dup'},
                                           {**base, 'burn_id': 'dup'}])
        with self.assertRaises(ValueError):
            burn_sequence_to_requirements([{**base, 'order': 2,
                                            'burn_id': 'orbit_burn_1'},
                                           {**base, 'order': 1}])

    def test_numpy_float64_converted_to_python_float(self):
        seq = [
            {'order': 1, 'name': 'park', 'source': 'rocket_calibration',
             'dv_m_s': np.float64(0.0)},
            {'order': 2, 'name': 'dep', 'source': 'mission_model',
             'dv_m_s': np.float64(2451.0)},
        ]
        result = burn_sequence_to_requirements(seq)
        self.assertIsInstance(result.requirements[1].target_dv_m_s, float)
        self.assertNotIsInstance(result.requirements[1].target_dv_m_s,
                                 np.float64)
        json.dumps(result.to_dict(), ensure_ascii=False)

    def test_inputs_completely_unmodified(self):
        seq = _standard_sequence()
        before = copy.deepcopy(seq)
        burn_sequence_to_requirements(seq)
        self.assertEqual(seq, before)

    def test_results_json_dumpable(self):
        result = burn_sequence_to_requirements(_standard_sequence())
        json.dumps(result.to_dict(), ensure_ascii=False)


class RealOrbitConversionTests(unittest.TestCase):
    def test_geosynchronous_retrograde_combined(self):
        mission = compute_orbit_mission(35786, 180, orbit_type='circular',
                                        arrival_strategy='combined')
        result = burn_sequence_to_requirements(mission['burn_sequence'])
        self.assertEqual(len(result.requirements), 3)
        self.assertEqual(result.requirements[0].source, 'rocket_calibration')
        self.assertEqual([r.source for r in result.requirements[1:]],
                         ['mission_model', 'mission_model'])
        for r in result.requirements:
            self.assertIsNone(r.preferred_stage_index)
        self.assertEqual(result.skipped_entries, [])
        json.dumps(result.to_dict(), ensure_ascii=False)

    def test_separate_strategy_zero_plane_change_not_an_ignition(self):
        mission = compute_orbit_mission(35786, 19.81, orbit_type='circular',
                                        arrival_strategy='separate')
        result = burn_sequence_to_requirements(mission['burn_sequence'])
        self.assertEqual(len(result.skipped_entries), 1)
        skipped = result.skipped_entries[0]
        self.assertEqual(skipped['reason'], 'below_ignition_threshold')
        self.assertEqual(skipped['dv_m_s'], 0.0)
        self.assertIn('平面变化', skipped['name'])
        self.assertEqual(len(result.requirements), 3)
        self.assertEqual(result.requirements[0].source, 'rocket_calibration')


class NeutronIntegrationTests(unittest.TestCase):
    def test_neutron_i_2630_normalization(self):
        config = load_config(
            Path('rockets/neutron_i_2630.yaml').read_text(encoding='utf-8'))
        result = normalize_stages(config['stages'])
        self.assertEqual(len(result.stages), 2)
        self.assertEqual([s.stage_index for s in result.stages], [0, 1])
        self.assertEqual(result.stages[1].multi_ignition, True)
        self.assertEqual(result.stages[0].multi_ignition, False)
        self.assertTrue(result.ready_for_serial_replay)
        self.assertEqual(result.data_quality, DataQuality.EXACT)
        self.assertAlmostEqual(result.stages[0].isp_vac_s, 305.0, places=3)
        self.assertAlmostEqual(result.stages[1].isp_vac_s, 300.0, places=3)
        self.assertAlmostEqual(result.stages[0].propellant_remaining_t,
                               132.03 - 6.046, places=6)
        self.assertEqual(result.stages[0].metadata['engine_cfg'],
                         ['SC-ENG-Merlin-1B.cfg'])
        self.assertEqual(result.stages[1].metadata['engine_count_total'], 3)
        # the normalization never touches the computed virtual stages
        self.assertNotIn('stages', result.metadata)


if __name__ == '__main__':
    unittest.main()
