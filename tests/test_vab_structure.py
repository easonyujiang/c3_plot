import copy
import json
import unittest
from pathlib import Path

from server.import_service import _preview_one
from server.ksp_confignode import CraftParseError
from server.vab_parser import parse_craft

FIXTURES = Path(__file__).parent / 'fixtures'
VAB = Path(__file__).parent.parent / 'VAB'


def _read_craft(path):
    return Path(path).read_text(encoding='utf-8-sig')


class EngineEvidenceTests(unittest.TestCase):
    @unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
    def test_multimode_engine_not_double_counted(self):
        draft = parse_craft(_read_craft(FIXTURES / 'anonymous_nested.craft'),
                            'anonymous_nested.craft')
        by_part = {p['base_part']: p for p in draft['parts']}
        multimode = by_part['TestMultiMode-2']
        self.assertEqual(multimode['engine']['kind'], 'multimode')
        self.assertEqual(multimode['engine']['count'], 1)
        self.assertEqual(multimode['engine']['count_source'],
                         'multimode_engine')
        self.assertEqual(multimode['engine']['modes'],
                         {'primary': 'air', 'secondary': 'rocket'})
        self.assertEqual(multimode['engine']['engine_ids'],
                         ['air', 'rocket'])
        codes = [w['code'] for w in draft['structured_warnings']]
        self.assertIn('multimode_single_engine', codes)

    @unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
    def test_sstu_cluster_layout_evidence(self):
        draft = parse_craft(_read_craft(FIXTURES / 'anonymous_nested.craft'),
                            'anonymous_nested.craft')
        by_part = {p['base_part']: p for p in draft['parts']}
        cluster = by_part['TestCluster-3']
        self.assertEqual(cluster['engine']['kind'], 'sstu_cluster')
        self.assertEqual(cluster['engine']['count'], 3)  # Triple
        self.assertEqual(cluster['engine']['layout'], 'Triple')
        self.assertEqual(cluster['engine']['layout_confidence'], 'low')
        self.assertEqual(cluster['engine']['count_source'], 'sstu_layout')
        # the ModuleEnginesFX inside the cluster part is NOT counted again
        codes = [w['code'] for w in draft['structured_warnings']]
        self.assertIn('sstu_layout_low_confidence', codes)

    @unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
    def test_module_fuel_tanks_tank_evidence(self):
        draft = parse_craft(_read_craft(FIXTURES / 'anonymous_nested.craft'),
                            'anonymous_nested.craft')
        by_part = {p['base_part']: p for p in draft['parts']}
        tank_part = by_part['TestTank-4']
        self.assertEqual(len(tank_part['tanks']), 2)
        self.assertEqual(tank_part['tanks'][0]['name'], 'LiquidFuel')
        self.assertEqual(tank_part['tanks'][0]['amount'], '200')
        self.assertEqual(tank_part['tanks'][0]['max_amount'], '200')
        self.assertEqual(tank_part['tanks'][1]['name'], 'Oxidizer')
        # duplicate RESOURCE children preserved
        self.assertEqual(len(tank_part['resources']), 2)
        self.assertEqual(tank_part['resources'][0]['name'], 'LiquidFuel')
        self.assertEqual(tank_part['resources'][1]['amount'], '0')

    @unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
    def test_symmetry_not_multiplied(self):
        draft = parse_craft(_read_craft(FIXTURES / 'anonymous_simple.craft'),
                            'anonymous_simple.craft')
        by_part = {p['base_part']: p for p in draft['parts']}
        engine = by_part['TestEngine-1']
        self.assertEqual(engine['engine']['count'], 1)
        self.assertEqual(engine['connections']['sym'],
                         ['TestEngine-1_4293000002'])
        self.assertEqual(engine['connections']['attN'],
                         ['decoupler1_4293000000', 'decoupler1_4293000000'])
        codes = [w['code'] for w in draft['structured_warnings']]
        self.assertIn('symmetry_not_multiplied', codes)

    @unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
    def test_single_module_engine_evidence(self):
        draft = parse_craft(_read_craft(FIXTURES / 'anonymous_simple.craft'),
                            'anonymous_simple.craft')
        by_part = {p['base_part']: p for p in draft['parts']}
        engine = by_part['TestEngine-1']['engine']
        self.assertEqual(engine['kind'], 'single')
        self.assertEqual(engine['count'], 1)
        self.assertEqual(engine['engine_ids'], ['main'])
        self.assertEqual(len(engine['propellants']), 2)


class LegacyDraftTests(unittest.TestCase):
    @unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
    def test_legacy_stages_are_placeholders(self):
        draft = parse_craft(_read_craft(FIXTURES / 'anonymous_nested.craft'),
                            'anonymous_nested.craft')
        self.assertTrue(draft['requires_mass_confirmation'])
        for stage in draft['stages']:
            self.assertTrue(stage['legacy_placeholder'])
            self.assertTrue(stage['requires_mass_confirmation'])
            self.assertEqual(stage['wet_mass_t'], 0.0)
            self.assertEqual(stage['dry_mass_t'], 0.0)
        codes = [w['code'] for w in draft['structured_warnings']]
        self.assertIn('mass_not_confirmed', codes)
        self.assertIn('stage_structure_unconfirmed', codes)

    def test_mod_mass_warned_as_delta_only(self):
        text = ('ship = T\nPART\n{\n\tpart = Engine_1\n\tmodMass = 0.5\n'
                '\tistg = 0\n\tMODULE\n\t{\n\t\tname = ModuleEngines\n'
                '\t}\n}\n')
        draft = parse_craft(text, 't.craft')
        part = draft['parts'][0]
        self.assertEqual(part['mod_mass_t'], 0.5)
        codes = [w['code'] for w in draft['structured_warnings']]
        self.assertIn('mod_mass_is_delta_only', codes)

    @unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
    def test_existing_multi_engine_group_behavior_preserved(self):
        draft = parse_craft(_read_craft(VAB / '1T_Test.craft'),
                            '1T_Test.craft')
        multi = [stage for stage in draft['stages']
                 if len(stage.get('engine_groups', [])) > 1]
        self.assertTrue(multi)
        self.assertEqual(sum(group['count']
                             for group in multi[0]['engine_groups']), 6)

    @unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
    def test_legacy_string_warnings_preserved(self):
        draft = parse_craft(_read_craft(FIXTURES / 'anonymous_nested.craft'),
                            'anonymous_nested.craft')
        self.assertIsInstance(draft['warnings'], list)
        self.assertTrue(all(isinstance(w, str) for w in draft['warnings']))


class SafetyAndInterfaceTests(unittest.TestCase):
    @unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
    def test_json_serializable_and_deterministic(self):
        text = _read_craft(FIXTURES / 'anonymous_nested.craft')
        first = parse_craft(text, 'a.craft')
        second = parse_craft(text, 'a.craft')
        self.assertEqual(json.dumps(first, ensure_ascii=False),
                         json.dumps(second, ensure_ascii=False))
        json.dumps(first, ensure_ascii=False)

    @unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
    def test_input_not_modified(self):
        text = _read_craft(FIXTURES / 'anonymous_simple.craft')
        before = text
        parse_craft(text, 'a.craft')
        self.assertEqual(text, before)

    def test_parse_errors_have_stable_codes(self):
        broken = 'PART\n{\n\tpart = A\n'  # missing close brace
        with self.assertRaises(CraftParseError) as exc:
            parse_craft(broken, 'broken.craft')
        self.assertEqual(exc.exception.code, 'unexpected_eof')
        self.assertIsInstance(exc.exception.line, int)

    @unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
    def test_real_neutron_sample_read_only_summary(self):
        draft = parse_craft(_read_craft(VAB / 'Neutrons I 2630.craft'),
                            'Neutrons I 2630.craft')
        self.assertEqual(draft['rocket']['name'], 'Neutrons I 2630')
        self.assertEqual(draft['source']['part_count'], 11)
        self.assertEqual(draft['source']['root_info']['version'], '1.12.5')
        engines = [p for p in draft['parts'] if p['engine']]
        # 2 SSTU engine parts in this craft
        self.assertEqual(len(engines), 2)
        layout_counts = sorted(p['engine']['count'] for p in engines)
        self.assertEqual(layout_counts, [3, 6])

    @unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
    def test_real_1t_sample_read_only_summary(self):
        draft = parse_craft(_read_craft(VAB / '1T_Test.craft'),
                            '1T_Test.craft')
        self.assertEqual(draft['source']['part_count'], 38)
        by_part = {p['base_part']: p for p in draft['parts']}
        self.assertEqual(by_part['SSTU-SC-ENG-Merlin-1A']['engine']['count'],
                         3)
        self.assertEqual(by_part['SSTU-SC-ENG-LMAE']['engine']['count'], 1)
        # RealFuels boosters with ModuleEnginesRF still counted once each
        boosters = [p for p in draft['parts']
                    if p['base_part'] == 'MassiveBooster' and p['engine']]
        self.assertEqual(len(boosters), 3)
        self.assertTrue(all(p['engine']['kind'] == 'single'
                            for p in boosters))
        # Double layout is a known fallback gap -> structured warning
        codes = {w['code'] for w in draft['structured_warnings']}
        self.assertIn('sstu_layout_unknown', codes)

    @unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
    def test_single_file_preview_and_batch_preview_no_writes(self):
        from unittest import mock
        # snapshot the rockets dir (the only writable project data area the
        # pipeline touches) and confirm nothing changes during previews.
        rockets_dir = Path(__file__).parent.parent / 'rockets'
        before = sorted(p.name for p in rockets_dir.iterdir())
        craft = FIXTURES / 'anonymous_nested.craft'
        raw = craft.read_bytes()
        with mock.patch('server.import_service.models.get_import_source_by_hash',
                        return_value=None):
            item = _preview_one(craft.name, raw)
        self.assertEqual(item['status'], 'needs_review')
        self.assertEqual(item['action'], 'review')
        self.assertTrue(item['config'].get('requires_mass_confirmation'))
        after = sorted(p.name for p in rockets_dir.iterdir())
        self.assertEqual(before, after)


class RootInfoTests(unittest.TestCase):
    @unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
    def test_root_info_extracted(self):
        draft = parse_craft(_read_craft(FIXTURES / 'anonymous_simple.craft'),
                            'anonymous_simple.craft')
        info = draft['source']['root_info']
        self.assertEqual(info['ship'], 'Anonymous Simple')
        self.assertEqual(info['version'], '1.12.5')
        self.assertEqual(info['type'], 'VAB')


if __name__ == '__main__':
    unittest.main()
