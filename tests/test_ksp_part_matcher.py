import copy
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from server import create_app, import_service
from server.ksp_install_index import KspIndex
from server.ksp_part_matcher import (
    AMBIGUOUS,
    EXACT,
    INDEX_UNAVAILABLE,
    INVALID_PART,
    MISSING,
    NORMALIZED_UNIQUE,
    STRATEGY_DOT_TO_UNDERSCORE,
    STRATEGY_EXACT_NAME,
    STRATEGY_NONE,
    enrich_craft_draft,
    match_craft_parts,
)
from server.vab_parser import parse_craft

from tests.game_inputs import configured_game_root
REAL_KSP = configured_game_root()
REAL_CACHE = Path(REAL_KSP) / 'GameData' / 'ModuleManager.ConfigCache'


def make_index(part_specs):
    """part_specs: list of (name, parent_url) tuples; first of a name is primary."""
    parts = []
    seen = set()
    for order, (name, parent_url) in enumerate(part_specs):
        primary = name not in seen
        seen.add(name)
        parts.append({
            'kind': 'part', 'name': name, 'parent_url': parent_url,
            'cache_order': order, 'primary': primary, 'diagnostics': [],
        })
    metadata = {
        'schema_version': 1,
        'source_kind': 'module_manager_config_cache',
        'ksp_root': 'C:/fake',
        'config_cache_path': 'C:/fake/GameData/ModuleManager.ConfigCache',
        'file_size': 123, 'mtime_ns': 456, 'sha256': '0' * 64,
        'patched_node_count': 1, 'built_at': '2026-01-01T00:00:00+00:00',
        'status': 'ok',
        'record_counts': {'parts': len(parts), 'resources': 0,
                          'sstu_layouts': 0, 'anonymous_nodes': 0,
                          'duplicate_definitions': 0},
    }
    return KspIndex(status='ok', metadata=metadata, parts=parts,
                    resources=[], sstu_layouts=[], diagnostics=[])


def make_draft(part_names):
    parts = []
    for index, name in enumerate(part_names):
        base, _, tail = name.rpartition('_')
        instance_id = tail if tail.isdigit() else str(index)
        parts.append({
            'index': index, 'part': f'{base}_{instance_id}',
            'base_part': base, 'instance_id': instance_id,
            'persistent_id': f'p-{index}', 'istg': 0,
        })
    return {
        'rocket': {'name': 'Test Craft'},
        'stages': [],
        'source': {'part_count': len(parts)},
        'parts': parts,
        'warnings': [],
        'structured_warnings': [],
    }


class PartMatcherTests(unittest.TestCase):
    def test_exact_unique_match(self):
        index = make_index([('Pod', '/Squad/Parts/Pod.cfg')])
        report = match_craft_parts(make_draft(['Pod_1']), index)
        instance = report['instances'][0]
        self.assertEqual(instance['status'], EXACT)
        self.assertEqual(instance['strategy'], STRATEGY_EXACT_NAME)
        self.assertEqual(instance['confidence'], 'high')
        self.assertEqual(instance['canonical_name'], 'Pod')
        self.assertEqual(instance['selected']['name'], 'Pod')
        self.assertEqual(instance['selected']['primary'], True)
        self.assertEqual(instance['selected']['parent_url'],
                         '/Squad/Parts/Pod.cfg')

    def test_dot_to_underscore_unique(self):
        index = make_index([('mk1pod_v2', '/Squad/Parts/mk1pod.cfg')])
        report = match_craft_parts(make_draft(['mk1pod.v2_7']), index)
        instance = report['instances'][0]
        self.assertEqual(instance['status'], NORMALIZED_UNIQUE)
        self.assertEqual(instance['strategy'], STRATEGY_DOT_TO_UNDERSCORE)
        self.assertEqual(instance['confidence'], 'medium')
        self.assertEqual(instance['canonical_name'], 'mk1pod_v2')
        self.assertEqual(instance['selected']['name'], 'mk1pod_v2')
        self.assertEqual(instance['requested_name'], 'mk1pod.v2')

    def test_original_name_ambiguous(self):
        index = make_index([
            ('Engine', '/A/engine.cfg'),
            ('Engine', '/B/engine.cfg'),
        ])
        report = match_craft_parts(make_draft(['Engine_1']), index)
        instance = report['instances'][0]
        self.assertEqual(instance['status'], AMBIGUOUS)
        self.assertEqual(len(instance['candidates']), 2)
        self.assertIsNone(instance['selected'])
        self.assertIn('ambiguous_definition',
                      [d['code'] for d in instance['diagnostics']])

    def test_normalized_name_ambiguous(self):
        index = make_index([
            ('probeCoreOcto_v2', '/A/probe.cfg'),
            ('probeCoreOcto_v2', '/B/probe.cfg'),
        ])
        report = match_craft_parts(make_draft(['probeCoreOcto.v2_3']), index)
        instance = report['instances'][0]
        self.assertEqual(instance['status'], AMBIGUOUS)
        self.assertEqual(instance['strategy'], STRATEGY_DOT_TO_UNDERSCORE)
        self.assertIsNone(instance['selected'])
        self.assertEqual(len(instance['candidates']), 2)

    def test_missing(self):
        index = make_index([('Pod', '/Squad/Pod.cfg')])
        report = match_craft_parts(make_draft(['NoSuchPart_1']), index)
        instance = report['instances'][0]
        self.assertEqual(instance['status'], MISSING)
        self.assertIsNone(instance['selected'])
        self.assertEqual(instance['candidates'], [])
        self.assertIn('no_candidates',
                      [d['code'] for d in instance['diagnostics']])

    def test_invalid_part_empty_name(self):
        index = make_index([('Pod', '/Squad/Pod.cfg')])
        draft = make_draft([])
        draft['parts'] = [{
            'index': 0, 'part': '_1', 'base_part': '',
            'instance_id': '1', 'persistent_id': 'x',
        }]
        report = match_craft_parts(draft, index)
        instance = report['instances'][0]
        self.assertEqual(instance['status'], INVALID_PART)
        self.assertEqual(instance['strategy'], STRATEGY_NONE)
        self.assertIn('invalid_base_part',
                      [d['code'] for d in instance['diagnostics']])

    def test_index_unavailable(self):
        report = match_craft_parts(make_draft(['Pod_1']), None)
        self.assertEqual(report['index_fingerprint'], None)
        self.assertEqual(report['by_status'][INDEX_UNAVAILABLE], 1)
        instance = report['instances'][0]
        self.assertEqual(instance['status'], INDEX_UNAVAILABLE)
        self.assertEqual(instance['strategy'], STRATEGY_NONE)
        self.assertIn('index_unavailable',
                      [d['code'] for d in instance['diagnostics']])

    def test_ambiguous_never_picks_primary(self):
        index = make_index([
            ('Engine', '/Z/original.cfg'),
            ('Engine', '/A/patch.cfg'),
        ])
        # primary (cache-order first) is /Z/original.cfg
        self.assertTrue(index.primary_part('Engine')['primary'])
        report = match_craft_parts(make_draft(['Engine_1']), index)
        instance = report['instances'][0]
        self.assertEqual(instance['status'], AMBIGUOUS)
        self.assertIsNone(instance['selected'])
        self.assertEqual([c['name'] for c in instance['candidates']],
                         ['Engine', 'Engine'])

    def test_inputs_not_modified(self):
        index = make_index([('Pod', '/Squad/Pod.cfg')])
        draft = make_draft(['Pod_1', 'NoSuch_2'])
        draft_before = copy.deepcopy(draft)
        index_before = index.to_dict()
        match_craft_parts(draft, index)
        enrich_craft_draft(draft, index)
        self.assertEqual(draft, draft_before)
        self.assertEqual(index.to_dict(), index_before)

    def test_json_serializable(self):
        index = make_index([('Pod', '/Squad/Pod.cfg'), ('b.c', '/X/b.cfg')])
        report = match_craft_parts(make_draft(['Pod_1', 'b.c_2']), index)
        json.dumps(report, ensure_ascii=False)

    def test_deterministic_and_candidate_order_stable(self):
        index = make_index([
            ('Engine', '/B/e.cfg'), ('Engine', '/A/e.cfg'),
            ('Decoupler_1', '/Squad/Dec.cfg'),
        ])
        draft = make_draft(['Engine_1', 'Decoupler.1_2'])
        first = match_craft_parts(draft, index)
        second = match_craft_parts(draft, index)
        self.assertEqual(first, second)
        self.assertEqual(
            [c['name'] for c in first['instances'][0]['candidates']],
            ['Engine', 'Engine'])
        self.assertEqual(
            [c['cache_order'] for c in first['instances'][0]['candidates']],
            [0, 1])

    def test_coverage_stats_instance_and_type_separate(self):
        index = make_index([
            ('A', '/X/a.cfg'),
            ('b_c', '/X/b.cfg'),
            ('C', '/X/c.cfg'),
        ])
        draft = make_draft(['A_1', 'A_2', 'b.c_3', 'NoSuch_4'])
        draft['parts'].append({
            'index': 4, 'part': '_5', 'base_part': '',
            'instance_id': '5', 'persistent_id': 'x',
        })
        report = match_craft_parts(draft, index)
        self.assertEqual(report['total_instances'], 5)
        self.assertEqual(report['exact_instances'], 2)     # A x2
        self.assertEqual(report['normalized_instances'], 1)  # b.c -> b_c
        self.assertEqual(report['missing_instances'], 1)
        self.assertEqual(report['invalid_instances'], 1)
        self.assertEqual(report['matched_instances'], 3)
        self.assertEqual(report['coverage_ratio'], 0.6)
        self.assertEqual(report['unique_part_types'], 3)  # A, b.c, NoSuch
        self.assertEqual(report['matched_unique_part_types'], 2)  # A, b.c
        self.assertEqual(report['unique_part_type_coverage_ratio'],
                         2 / 3)

    def test_probe_core_octo_trap_not_matched_to_octo2(self):
        index = make_index([
            ('probeCoreOcto_v2', '/Squad/probeCoreOcto.cfg'),
            ('probeCoreOcto2_v2', '/Squad/probeCoreOcto2.cfg'),
        ])
        report = match_craft_parts(make_draft(['probeCoreOcto.v2_1']), index)
        instance = report['instances'][0]
        self.assertEqual(instance['status'], NORMALIZED_UNIQUE)
        self.assertEqual(instance['canonical_name'], 'probeCoreOcto_v2')
        self.assertEqual(instance['selected']['name'], 'probeCoreOcto_v2')

    def test_case_variant_is_suggestion_only(self):
        index = make_index([('Pod', '/Squad/Pod.cfg')])
        report = match_craft_parts(make_draft(['pod_1']), index)
        instance = report['instances'][0]
        self.assertEqual(instance['status'], MISSING)
        codes = [d['code'] for d in instance['diagnostics']]
        self.assertIn('case_variant_suggestion', codes)
        self.assertIsNone(instance['selected'])

    def test_enrich_is_non_destructive_and_attaches_report(self):
        index = make_index([('Pod', '/Squad/Pod.cfg')])
        draft = make_draft(['Pod_1', 'NoSuch_2'])
        before = copy.deepcopy(draft)
        enriched = enrich_craft_draft(draft, index)
        self.assertEqual(draft, before)
        self.assertIsNot(draft, enriched)
        self.assertIn('installation_match', enriched)
        self.assertEqual(enriched['installation_match']['matched_instances'], 1)
        codes = [w['code'] for w in enriched['structured_warnings']]
        self.assertIn('part_match_missing', codes)
        self.assertNotIn('part_match_missing', draft['structured_warnings'])


class PreviewIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    CRAFT = ('ship = Mini\n'
             'PART\n{\n\tpart = Pod_1\n\tistg = 0\n}\n').encode('utf-8')

    def test_preview_craft_with_index_does_not_crash(self):
        index = make_index([('Pod', '/Squad/Pod.cfg')])
        item = import_service.preview_craft('mini.craft', self.CRAFT,
                                            ksp_index=index)
        self.assertEqual(item['status'], 'needs_review')
        self.assertEqual(item['action'], 'review')
        config = item['config']
        self.assertIn('installation_match', config)
        self.assertEqual(config['installation_match']['matched_instances'], 1)
        codes = [w['code'] for w in item['structured_warnings']]
        self.assertNotIn('part_match_missing', codes)
        self.assertNotIn('part_match_ambiguous', codes)

    def test_preview_craft_without_index_does_not_crash(self):
        with mock.patch('server.import_service.get_auto_ksp_index',
                        return_value=None):
            item = import_service.preview_craft('mini.craft', self.CRAFT)
        self.assertEqual(item['status'], 'needs_review')
        self.assertEqual(item['action'], 'review')
        self.assertIn('installation_match', item['config'])
        self.assertIsNone(item['config']['installation_match']
                          ['index_fingerprint'])
        codes = [w['code'] for w in item['structured_warnings']]
        self.assertIn('index_unavailable', codes)

    def test_preview_with_index_reports_missing_warning(self):
        index = make_index([('Pod', '/Squad/Pod.cfg')])
        craft = ('ship = Mini\nPART\n{\n\tpart = NoSuch_1\n\tistg = 0\n}\n'
                 ).encode('utf-8')
        item = import_service.preview_craft('mini.craft', craft,
                                            ksp_index=index)
        codes = [w['code'] for w in item['structured_warnings']]
        self.assertIn('part_match_missing', codes)


@unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
@unittest.skipUnless(REAL_CACHE.is_file(),
                     'real KSP install not present on this machine')
class RealVabSmokeTests(unittest.TestCase):
    """Match every instance of the real VAB folder against the real index."""

    def test_all_389_instances_match(self):
        from server.ksp_install_index import build_ksp_index
        index = build_ksp_index(REAL_KSP)
        self.assertEqual(index.status, 'ok')
        reports = []
        for craft_path in sorted(Path('VAB').glob('*.craft')):
            if craft_path.name.endswith('.original'):
                continue
            draft = parse_craft(craft_path.read_text(encoding='utf-8-sig'),
                                craft_path.name)
            reports.append(match_craft_parts(draft, index))
        totals = {}
        for status in ('exact_instances', 'normalized_instances',
                       'ambiguous_instances', 'missing_instances',
                       'invalid_instances'):
            totals[status] = sum(r[status] for r in reports)
        total = sum(r['total_instances'] for r in reports)
        self.assertEqual(total, 389)
        self.assertEqual(totals['exact_instances'], 381)
        self.assertEqual(totals['normalized_instances'], 8)
        self.assertEqual(totals['ambiguous_instances'], 0)
        self.assertEqual(totals['missing_instances'], 0)
        self.assertEqual(totals['invalid_instances'], 0)
        coverage = (totals['exact_instances']
                    + totals['normalized_instances']) / total
        self.assertEqual(coverage, 1.0)


if __name__ == '__main__':
    unittest.main()
