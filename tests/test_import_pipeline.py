import unittest
from pathlib import Path

from compute_c3_mass import compute_rocket
from server.vab_parser import parse_craft
from server.validation import validate_rocket
from server.yaml_codec import load_config


class ImportPipelineTests(unittest.TestCase):
    def test_legacy_numpy_yaml_is_safe_and_valid(self):
        config = load_config((Path(__file__).parent / 'fixtures/legacy_numpy_rocket.yaml').read_text(encoding='utf-8'))
        valid, errors = validate_rocket(config)
        self.assertTrue(valid, errors)
        self.assertIsNone(config['reference_missions']['gto'].get('dv'))

    def test_vab_parser_preserves_multiple_engine_groups(self):
        craft = Path('VAB/1T_Test.craft')
        parsed = parse_craft(craft.read_text(encoding='utf-8-sig'), craft.name)
        multi = [stage for stage in parsed['stages'] if len(stage.get('engine_groups', [])) > 1]
        self.assertTrue(multi)
        self.assertEqual(sum(group['count'] for group in multi[0]['engine_groups']), 6)

    def test_compute_accepts_multiple_engine_groups(self):
        config = {
            'rocket': {'name': 'engine-group-test'},
            'stages': [{
                'wet_mass_t': 10.0,
                'dry_mass_t': 2.0,
                'engines': {'cfg': 'SC-ENG-Merlin-1A.cfg', 'count': 1},
                'engine_groups': [
                    {'cfg': 'SC-ENG-Merlin-1A.cfg', 'count': 2},
                    {'cfg': 'SC-ENG-F1.cfg', 'count': 1},
                ],
            }],
            'calibration': [
                {'payload_t': 0.2, 'orbit_alt_km': 220, 'dv_rem_m_s': 1000},
                {'payload_t': 0.5, 'orbit_alt_km': 220, 'dv_rem_m_s': 700},
                {'payload_t': 1.0, 'orbit_alt_km': 220, 'dv_rem_m_s': 200},
            ],
            'reference_missions': {},
        }
        result = compute_rocket(config)
        self.assertIn('m_pl_max', result)
        self.assertGreater(result['stages'][0]['thrust'], 0)


if __name__ == '__main__':
    unittest.main()
