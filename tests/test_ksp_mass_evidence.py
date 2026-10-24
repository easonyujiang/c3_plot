import copy
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from server import create_app, import_service
from server.ksp_install_index import KspIndex
from server.ksp_mass_evidence import (
    DENSITY_AMBIGUOUS,
    DENSITY_MISSING,
    DYNAMIC_UNRESOLVED,
    ESTIMATED_WITH_MODIFIER,
    INVALID_MASS,
    MATCH_UNAVAILABLE,
    RESOLVED_STATIC,
    RESOURCE_UNRESOLVED,
    SOURCE_TANK_FALLBACK,
    extract_part_mass_evidence,
)
from server.ksp_part_matcher import match_craft_parts
from server.vab_parser import parse_craft

from tests.game_inputs import configured_game_root
REAL_KSP = configured_game_root()
REAL_CACHE = Path(REAL_KSP) / 'GameData' / 'ModuleManager.ConfigCache'


def make_index(part_specs=None, resource_specs=None):
    """Build a KspIndex from simple specs for mass-evidence tests."""
    part_specs = part_specs or []
    resource_specs = resource_specs or []
    parts = []
    seen = set()
    for order, spec in enumerate(part_specs):
        name = spec['name']
        primary = name not in seen
        seen.add(name)
        parts.append({
            'kind': 'part', 'name': name, 'parent_url': spec['parent_url'],
            'cache_order': order, 'primary': primary,
            'diagnostics': [],
            'mass_raw': spec.get('mass_raw'),
            'mass_t': spec.get('mass_t'),
            'modules': spec.get('modules') or [],
            'dynamic_mass_modules': spec.get('dynamic_mass_modules') or [],
            'resources': spec.get('resources') or [],
            'fuel_tanks': spec.get('fuel_tanks') or [],
            'module_evidence': spec.get('module_evidence') or {},
        })
    resources = []
    seen_res = set()
    for order, spec in enumerate(resource_specs):
        name = spec['name']
        primary = name not in seen_res
        seen_res.add(name)
        resources.append({
            'kind': 'resource', 'name': name,
            'parent_url': spec.get('parent_url', '/Resources/Generic.cfg'),
            'cache_order': order, 'primary': primary, 'diagnostics': [],
            'density_raw': spec.get('density_raw'),
            'density_t_per_unit': spec.get('density_t_per_unit'),
            'unit_cost_raw': spec.get('unit_cost_raw'),
            'unit_cost': spec.get('unit_cost'),
            'flow_mode': spec.get('flow_mode'),
            'transfer': spec.get('transfer'),
        })
    metadata = {
        'schema_version': 1, 'source_kind': 'module_manager_config_cache',
        'ksp_root': 'C:/fake',
        'config_cache_path': 'C:/fake/GameData/ModuleManager.ConfigCache',
        'file_size': 123, 'mtime_ns': 456, 'sha256': '0' * 64,
        'patched_node_count': 1, 'built_at': '2026-01-01T00:00:00+00:00',
        'status': 'ok',
        'record_counts': {'parts': len(parts), 'resources': len(resources),
                          'sstu_layouts': 0, 'anonymous_nodes': 0,
                          'duplicate_definitions': 0},
    }
    return KspIndex(status='ok', metadata=metadata, parts=parts,
                    resources=resources, sstu_layouts=[], diagnostics=[])


def make_draft(part_specs):
    """part_specs: list of dicts with base_part, mod fields, resources, tanks."""
    parts = []
    for index, spec in enumerate(part_specs):
        base = spec['base_part']
        parts.append({
            'index': index,
            'part': f'{base}_{index}',
            'base_part': base,
            'instance_id': str(index),
            'persistent_id': f'p-{index}',
            'istg': 0,
            'mod_mass_present': spec.get('mod_mass_present'),
            'mod_mass_raw': spec.get('mod_mass_raw'),
            'mod_mass_t': spec.get('mod_mass_t'),
            'resources': spec.get('resources') or [],
            'tanks': spec.get('tanks') or [],
            'module_evidence': spec.get('module_evidence') or {},
        })
    return {
        'rocket': {'name': 'Test'},
        'stages': [],
        'source': {'part_count': len(parts)},
        'parts': parts,
        'warnings': [],
        'structured_warnings': [],
    }


def extract(draft, index):
    return extract_part_mass_evidence(draft, index,
                                      match_craft_parts(draft, index))


class MassEvidenceTests(unittest.TestCase):
    def test_unique_part_base_mass(self):
        index = make_index(part_specs=[
            {'name': 'Pod', 'parent_url': '/S/Pod.cfg',
             'mass_raw': '0.5', 'mass_t': 0.5}])
        report = extract(make_draft([{'base_part': 'Pod'}]), index)
        inst = report['instances'][0]
        self.assertEqual(inst['resolution_status'], RESOLVED_STATIC)
        self.assertEqual(inst['base_mass_t'], 0.5)
        self.assertEqual(inst['static_dry_mass_estimate_t'], 0.5)
        self.assertEqual(inst['current_part_mass_estimate_t'], 0.5)
        self.assertEqual(inst['confidence'], 'medium')
        self.assertIn('mod_mass_absent',
                      [d['code'] for d in inst['diagnostics']])

    def test_legal_mod_mass_adds(self):
        index = make_index(part_specs=[
            {'name': 'Pod', 'parent_url': '/S/Pod.cfg',
             'mass_raw': '0.5', 'mass_t': 0.5}])
        draft = make_draft([{'base_part': 'Pod', 'mod_mass_present': True,
                             'mod_mass_raw': '0.2', 'mod_mass_t': 0.2}])
        inst = extract(draft, index)['instances'][0]
        self.assertEqual(inst['resolution_status'], RESOLVED_STATIC)
        self.assertEqual(inst['static_dry_mass_estimate_t'], 0.7)
        self.assertEqual(inst['confidence'], 'high')

    def test_mod_mass_missing_vs_zero_distinguished(self):
        parsed = parse_craft(
            'ship = T\n'
            'PART\n{\n\tpart = A_1\n}\n'
            'PART\n{\n\tpart = B_2\n\tmodMass = 0\n}\n', 't.craft')
        a, b = parsed['parts']
        self.assertFalse(a['mod_mass_present'])
        self.assertIsNone(a['mod_mass_raw'])
        self.assertIsNone(a['mod_mass_t'])
        self.assertTrue(b['mod_mass_present'])
        self.assertEqual(b['mod_mass_raw'], '0')
        self.assertEqual(b['mod_mass_t'], 0.0)

    def test_invalid_mod_mass_rejected(self):
        parsed = parse_craft(
            'ship = T\nPART\n{\n\tpart = A_1\n\tmodMass = NaN\n}\n'
            'PART\n{\n\tpart = B_2\n\tmodMass = oops\n}\n', 't.craft')
        for part in parsed['parts']:
            self.assertTrue(part['mod_mass_present'])
            self.assertIsNone(part['mod_mass_t'])
        codes = [w['code'] for w in parsed['structured_warnings']]
        self.assertEqual(codes.count('mod_mass_invalid'), 2)

    def test_invalid_base_mass_no_zero_fallback(self):
        index = make_index(part_specs=[
            {'name': 'Broken', 'parent_url': '/S/B.cfg',
             'mass_raw': 'not-a-mass', 'mass_t': None}])
        inst = extract(make_draft([{'base_part': 'Broken'}]), index
                       )['instances'][0]
        self.assertEqual(inst['resolution_status'], INVALID_MASS)
        self.assertIsNone(inst['static_dry_mass_estimate_t'])
        self.assertIsNone(inst['current_part_mass_estimate_t'])
        self.assertIn('invalid_base_mass',
                      [d['code'] for d in inst['diagnostics']])

    def test_negative_static_result_is_invalid(self):
        index = make_index(part_specs=[
            {'name': 'Pod', 'parent_url': '/S/Pod.cfg',
             'mass_raw': '0.1', 'mass_t': 0.1}])
        draft = make_draft([{'base_part': 'Pod', 'mod_mass_present': True,
                             'mod_mass_raw': '-0.5', 'mod_mass_t': -0.5}])
        inst = extract(draft, index)['instances'][0]
        self.assertEqual(inst['resolution_status'], INVALID_MASS)

    def test_dynamic_module_lowers_confidence(self):
        index = make_index(part_specs=[
            {'name': 'Tank', 'parent_url': '/S/T.cfg',
             'mass_raw': '0.3', 'mass_t': 0.3,
             'modules': ['ModuleFuelTanks'],
             'dynamic_mass_modules': ['ModuleFuelTanks']}])
        draft = make_draft([{'base_part': 'Tank', 'mod_mass_present': True,
                             'mod_mass_raw': '0.1', 'mod_mass_t': 0.1}])
        inst = extract(draft, index)['instances'][0]
        self.assertEqual(inst['resolution_status'],
                         ESTIMATED_WITH_MODIFIER)
        self.assertEqual(inst['static_dry_mass_estimate_t'], 0.4)
        self.assertEqual(inst['confidence'], 'medium')
        self.assertEqual(inst['dynamic_mass_modules'],
                         ['ModuleFuelTanks'])

    def test_dynamic_module_without_mod_is_dynamic_unresolved(self):
        index = make_index(part_specs=[
            {'name': 'Tank', 'parent_url': '/S/T.cfg',
             'mass_raw': '0.3', 'mass_t': 0.3,
             'modules': ['SSTUModularEngineCluster'],
             'dynamic_mass_modules': ['SSTUModularEngineCluster']}])
        inst = extract(make_draft([{'base_part': 'Tank'}]), index
                       )['instances'][0]
        self.assertEqual(inst['resolution_status'], DYNAMIC_UNRESOLVED)
        self.assertIsNone(inst['static_dry_mass_estimate_t'])
        self.assertIsNone(inst['current_part_mass_estimate_t'])
        self.assertIn('dynamic_mass_unresolved',
                      [d['code'] for d in inst['diagnostics']])

    def test_unique_density_computes_current_and_max(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1}],
            resource_specs=[{'name': 'LiquidFuel', 'density_raw': '0.005',
                             'density_t_per_unit': 0.005}])
        draft = make_draft([{'base_part': 'Tank',
                             'resources': [{'name': 'LiquidFuel',
                                            'amount': '10',
                                            'max_amount': '100'}]}])
        inst = extract(draft, index)['instances'][0]
        self.assertEqual(inst['current_resource_mass_t'], 0.05)
        self.assertEqual(inst['maximum_resource_mass_t'], 0.5)
        self.assertEqual(inst['current_part_mass_estimate_t'], 0.15)
        row = inst['resource_evidence'][0]
        self.assertEqual(row['density_status'], 'resolved')

    def test_zero_density_is_legal(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1}],
            resource_specs=[{'name': 'ElectricCharge', 'density_raw': '0',
                             'density_t_per_unit': 0.0}])
        draft = make_draft([{'base_part': 'Tank',
                             'resources': [{'name': 'ElectricCharge',
                                            'amount': '10',
                                            'max_amount': '10'}]}])
        inst = extract(draft, index)['instances'][0]
        self.assertEqual(inst['current_resource_mass_t'], 0.0)
        row = inst['resource_evidence'][0]
        self.assertEqual(row['density_status'], 'resolved')
        self.assertNotIn('missing_density',
                         [d['code'] for d in row['diagnostics']])

    def test_missing_density(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1}],
            resource_specs=[{'name': 'OtherFuel', 'density_raw': '0.001',
                             'density_t_per_unit': 0.001}])
        draft = make_draft([{'base_part': 'Tank',
                             'resources': [{'name': 'NoSuchResource',
                                            'amount': '10',
                                            'max_amount': '10'}]}])
        inst = extract(draft, index)['instances'][0]
        self.assertEqual(inst['resolution_status'], RESOURCE_UNRESOLVED)
        self.assertIsNone(inst['current_resource_mass_t'])
        row = inst['resource_evidence'][0]
        self.assertEqual(row['density_status'], DENSITY_MISSING)

    def test_ambiguous_density_never_picks_primary(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1}],
            resource_specs=[
                {'name': 'Fuel', 'density_raw': '0.001',
                 'density_t_per_unit': 0.001},
                {'name': 'Fuel', 'density_raw': '0.009',
                 'density_t_per_unit': 0.009},
            ])
        self.assertTrue(index.find_resource('Fuel')['primary'])
        draft = make_draft([{'base_part': 'Tank',
                             'resources': [{'name': 'Fuel', 'amount': '10',
                                            'max_amount': '10'}]}])
        inst = extract(draft, index)['instances'][0]
        self.assertEqual(inst['resolution_status'], RESOURCE_UNRESOLVED)
        self.assertIsNone(inst['current_resource_mass_t'])
        row = inst['resource_evidence'][0]
        self.assertEqual(row['density_status'], DENSITY_AMBIGUOUS)

    def test_negative_amounts_and_amount_exceeds_max(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1}],
            resource_specs=[{'name': 'Fuel', 'density_raw': '0.001',
                             'density_t_per_unit': 0.001}])
        draft = make_draft([{'base_part': 'Tank',
                             'resources': [
                                 {'name': 'FuelA', 'amount': '-1',
                                  'max_amount': '10'},
                                 {'name': 'FuelB', 'amount': '20',
                                  'max_amount': '10'},
                             ]}])
        inst = extract(draft, index)['instances'][0]
        self.assertIsNone(inst['current_resource_mass_t'])
        codes = [d['code'] for r in inst['resource_evidence']
                 for d in r['diagnostics']]
        self.assertIn('negative_amount', codes)
        self.assertIn('amount_exceeds_max', codes)

    def test_mirrored_resource_and_tank_counted_once(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1}],
            resource_specs=[{'name': 'LiquidFuel', 'density_raw': '0.005',
                             'density_t_per_unit': 0.005}])
        draft = make_draft([{'base_part': 'Tank',
                             'resources': [{'name': 'LiquidFuel',
                                            'amount': '10',
                                            'max_amount': '100'}],
                             'tanks': [{'name': 'LiquidFuel',
                                        'amount': '10',
                                        'max_amount': '100'}]}])
        inst = extract(draft, index)['instances'][0]
        rows = inst['resource_evidence']
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['mirrored'])
        self.assertEqual(inst['current_resource_mass_t'], 0.05)

    def test_tank_only_fallback(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1}],
            resource_specs=[{'name': 'LiquidFuel', 'density_raw': '0.005',
                             'density_t_per_unit': 0.005}])
        draft = make_draft([{'base_part': 'Tank',
                             'tanks': [{'name': 'LiquidFuel',
                                        'amount': '10',
                                        'max_amount': '100'}]}])
        inst = extract(draft, index)['instances'][0]
        row = inst['resource_evidence'][0]
        self.assertEqual(row['source'], SOURCE_TANK_FALLBACK)
        self.assertIn('tank_fallback',
                      [d['code'] for d in row['diagnostics']])
        self.assertEqual(inst['current_resource_mass_t'], 0.05)
        self.assertEqual(inst['confidence'], 'medium')

    def test_config_default_never_overrides_craft_amount(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1,
                         'resources': [{'name': 'LiquidFuel',
                                        'amount': '100',
                                        'max_amount': '100'}]}],
            resource_specs=[{'name': 'LiquidFuel', 'density_raw': '0.005',
                             'density_t_per_unit': 0.005}])
        draft = make_draft([{'base_part': 'Tank',
                             'resources': [{'name': 'LiquidFuel',
                                            'amount': '10',
                                            'max_amount': '100'}]}])
        inst = extract(draft, index)['instances'][0]
        row = inst['resource_evidence'][0]
        self.assertEqual(row['source'], 'craft_resource')
        self.assertEqual(inst['current_resource_mass_t'], 0.05)

    def test_unmatched_and_ambiguous_instances_no_mass(self):
        index = make_index(
            part_specs=[{'name': 'Pod', 'parent_url': '/S/Pod.cfg',
                         'mass_raw': '0.5', 'mass_t': 0.5},
                        {'name': 'Dup', 'parent_url': '/A/D.cfg',
                         'mass_raw': '1.0', 'mass_t': 1.0},
                        {'name': 'Dup', 'parent_url': '/B/D.cfg',
                         'mass_raw': '2.0', 'mass_t': 2.0}])
        draft = make_draft([{'base_part': 'NoSuch'},
                            {'base_part': 'Dup'}])
        report = extract(draft, index)
        for inst in report['instances']:
            self.assertEqual(inst['resolution_status'], MATCH_UNAVAILABLE)
            self.assertIsNone(inst['static_dry_mass_estimate_t'])
            self.assertIsNone(inst['current_part_mass_estimate_t'])

    def test_inputs_not_modified(self):
        index = make_index(part_specs=[
            {'name': 'Pod', 'parent_url': '/S/Pod.cfg',
             'mass_raw': '0.5', 'mass_t': 0.5}])
        draft = make_draft([{'base_part': 'Pod', 'mod_mass_present': True,
                             'mod_mass_raw': '0.1', 'mod_mass_t': 0.1,
                             'resources': [{'name': 'Fuel', 'amount': '1',
                                            'max_amount': '2'}]}])
        draft_before = copy.deepcopy(draft)
        index_before = index.to_dict()
        report = extract(draft, index)
        json.dumps(report, ensure_ascii=False)
        self.assertEqual(draft, draft_before)
        self.assertEqual(index.to_dict(), index_before)

    def test_deterministic_output(self):
        index = make_index(part_specs=[
            {'name': 'Pod', 'parent_url': '/S/Pod.cfg',
             'mass_raw': '0.5', 'mass_t': 0.5}])
        draft = make_draft([{'base_part': 'Pod'}])
        self.assertEqual(extract(draft, index), extract(draft, index))

    def test_reuses_provided_installation_match(self):
        index = make_index(part_specs=[
            {'name': 'Pod', 'parent_url': '/S/Pod.cfg',
             'mass_raw': '0.5', 'mass_t': 0.5}])
        draft = make_draft([{'base_part': 'Pod'}])
        match = match_craft_parts(draft, index)
        report = extract_part_mass_evidence(draft, index, match)
        self.assertEqual(report['instances'][0]['match_status'], 'exact')
        self.assertEqual(report['index_fingerprint']['sha256'], '0' * 64)

    def test_launch_clamp_flagged_by_module(self):
        index = make_index(part_specs=[
            {'name': 'clamp', 'parent_url': '/S/clamp.cfg',
             'mass_raw': '0.05', 'mass_t': 0.05,
             'modules': ['LaunchClamp']}])
        inst = extract(make_draft([{'base_part': 'clamp'}]), index
                       )['instances'][0]
        self.assertTrue(inst['contains_launch_clamp'])
        self.assertTrue(inst['possible_non_flight_part'])


class PreviewMassEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    CRAFT = ('ship = Mini\n'
             'PART\n{\n\tpart = Pod_1\n\tistg = 0\n}\n').encode('utf-8')

    def _index(self):
        return make_index(part_specs=[
            {'name': 'Pod', 'parent_url': '/S/Pod.cfg',
             'mass_raw': '0.5', 'mass_t': 0.5}])

    def test_preview_attaches_mass_evidence_and_stays_needs_review(self):
        item = import_service.preview_craft('mini.craft', self.CRAFT,
                                            ksp_index=self._index())
        self.assertEqual(item['status'], 'needs_review')
        self.assertEqual(item['action'], 'review')
        config = item['config']
        self.assertIn('mass_evidence', config)
        self.assertEqual(config['mass_evidence']['total_instances'], 1)
        self.assertTrue(config['requires_mass_confirmation'])
        self.assertIn('installation_match', config)

    def test_preview_does_not_touch_stage_masses(self):
        item = import_service.preview_craft('mini.craft', self.CRAFT,
                                            ksp_index=self._index())
        for stage in item['config']['stages']:
            self.assertEqual(stage['wet_mass_t'], 0.0)
            self.assertEqual(stage['dry_mass_t'], 0.0)

    def test_preview_without_index_does_not_crash(self):
        with mock.patch('server.import_service.get_auto_ksp_index',
                        return_value=None):
            item = import_service.preview_craft('mini.craft', self.CRAFT)
        self.assertEqual(item['status'], 'needs_review')
        self.assertIn('mass_evidence', item['config'])
        self.assertIsNotNone(item['config']['mass_evidence'])
        codes = [w['code'] for w in item['structured_warnings']]
        self.assertIn('index_unavailable', codes)


class MassEvidenceReworkTests(unittest.TestCase):
    """Regression tests for the 6B.3A-R fixes."""

    def test_config_resource_without_craft_state_is_unresolved(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1,
                         'resources': [{'name': 'LiquidFuel',
                                        'amount': '100',
                                        'max_amount': '100'}]}],
            resource_specs=[{'name': 'LiquidFuel', 'density_raw': '0.005',
                             'density_t_per_unit': 0.005}])
        inst = extract(make_draft([{'base_part': 'Tank'}]), index
                       )['instances'][0]
        self.assertEqual(inst['resolution_status'], RESOURCE_UNRESOLVED)
        self.assertIsNone(inst['current_resource_mass_t'])
        self.assertIsNone(inst['current_part_mass_estimate_t'])
        self.assertIsNotNone(inst['static_dry_mass_estimate_t'])
        codes = [d['code'] for d in inst['diagnostics']]
        self.assertIn('resource_state_missing', codes)
        row = inst['resource_evidence'][0]
        self.assertTrue(row['state_missing'])
        self.assertEqual(row['source'], 'config_default_only')

    def test_no_config_and_no_craft_resources_is_zero(self):
        index = make_index(part_specs=[
            {'name': 'Pod', 'parent_url': '/S/Pod.cfg',
             'mass_raw': '0.5', 'mass_t': 0.5}])
        inst = extract(make_draft([{'base_part': 'Pod'}]), index
                       )['instances'][0]
        self.assertEqual(inst['resolution_status'], RESOLVED_STATIC)
        self.assertEqual(inst['current_resource_mass_t'], 0.0)
        self.assertEqual(inst['current_part_mass_estimate_t'], 0.5)

    def test_config_default_only_keeps_capacity_but_not_amount(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1,
                         'resources': [{'name': 'LiquidFuel',
                                        'amount': '100',
                                        'max_amount': '100'}]}],
            resource_specs=[{'name': 'LiquidFuel', 'density_raw': '0.005',
                             'density_t_per_unit': 0.005}])
        inst = extract(make_draft([{'base_part': 'Tank'}]), index
                       )['instances'][0]
        row = inst['resource_evidence'][0]
        self.assertEqual(row['source'], 'config_default_only')
        self.assertIsNone(row['amount_raw'])
        self.assertIsNone(row['current_mass_t'])
        self.assertEqual(row['config_default_amount_raw'], '100')
        self.assertEqual(row['configured_maximum_mass_t'], 0.5)
        self.assertIsNone(inst['saved_maximum_resource_mass_t'])
        self.assertEqual(inst['configured_maximum_resource_mass_t'], 0.5)

    def test_craft_amount_wins_over_config_default(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1,
                         'resources': [{'name': 'LiquidFuel',
                                        'amount': '100',
                                        'max_amount': '100'}]}],
            resource_specs=[{'name': 'LiquidFuel', 'density_raw': '0.005',
                             'density_t_per_unit': 0.005}])
        draft = make_draft([{'base_part': 'Tank',
                             'resources': [{'name': 'LiquidFuel',
                                            'amount': '10',
                                            'max_amount': '10'}]}])
        inst = extract(draft, index)['instances'][0]
        row = inst['resource_evidence'][0]
        self.assertEqual(row['source'], 'craft_resource')
        self.assertEqual(inst['current_resource_mass_t'], 0.05)
        self.assertEqual(inst['saved_maximum_resource_mass_t'], 0.05)
        self.assertEqual(inst['configured_maximum_resource_mass_t'], 0.5)
        self.assertEqual(row['config_default_amount_raw'], '100')

    def test_explicit_invalid_mod_mass_static_part_is_invalid(self):
        index = make_index(part_specs=[
            {'name': 'Pod', 'parent_url': '/S/Pod.cfg',
             'mass_raw': '1.0', 'mass_t': 1.0}])
        draft = make_draft([{'base_part': 'Pod', 'mod_mass_present': True,
                             'mod_mass_raw': 'oops', 'mod_mass_t': None}])
        inst = extract(draft, index)['instances'][0]
        self.assertEqual(inst['resolution_status'], INVALID_MASS)
        self.assertIsNone(inst['static_dry_mass_estimate_t'])
        self.assertIsNone(inst['current_part_mass_estimate_t'])
        self.assertEqual(inst['confidence'], 'none')
        self.assertIn('invalid_mod_mass',
                      [d['code'] for d in inst['diagnostics']])

    def test_explicit_invalid_mod_mass_dynamic_part_is_dynamic_unresolved(self):
        index = make_index(part_specs=[
            {'name': 'Tank', 'parent_url': '/S/T.cfg',
             'mass_raw': '1.0', 'mass_t': 1.0,
             'modules': ['ModuleFuelTanks'],
             'dynamic_mass_modules': ['ModuleFuelTanks']}])
        draft = make_draft([{'base_part': 'Tank', 'mod_mass_present': True,
                             'mod_mass_raw': 'NaN', 'mod_mass_t': None}])
        inst = extract(draft, index)['instances'][0]
        self.assertEqual(inst['resolution_status'], DYNAMIC_UNRESOLVED)
        self.assertIsNone(inst['static_dry_mass_estimate_t'])
        self.assertEqual(inst['confidence'], 'low')

    def test_mod_mass_missing_vs_zero_in_evidence(self):
        index = make_index(part_specs=[
            {'name': 'Pod', 'parent_url': '/S/Pod.cfg',
             'mass_raw': '1.0', 'mass_t': 1.0}])
        missing = extract(make_draft([{'base_part': 'Pod'}]), index
                          )['instances'][0]
        self.assertEqual(missing['static_dry_mass_estimate_t'], 1.0)
        self.assertEqual(missing['confidence'], 'medium')
        zero = extract(make_draft([{'base_part': 'Pod',
                                    'mod_mass_present': True,
                                    'mod_mass_raw': '0',
                                    'mod_mass_t': 0.0}]), index
                       )['instances'][0]
        self.assertEqual(zero['static_dry_mass_estimate_t'], 1.0)
        self.assertEqual(zero['confidence'], 'high')

    def test_duplicate_craft_resources_not_overwritten(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1}],
            resource_specs=[{'name': 'Fuel', 'density_raw': '0.001',
                             'density_t_per_unit': 0.001}])
        draft = make_draft([{'base_part': 'Tank',
                             'resources': [
                                 {'name': 'Fuel', 'amount': '10',
                                  'max_amount': '10'},
                                 {'name': 'Fuel', 'amount': '20',
                                  'max_amount': '20'},
                             ]}])
        inst = extract(draft, index)['instances'][0]
        rows = inst['resource_evidence']
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['unresolved'] for r in rows))
        self.assertIsNone(inst['current_resource_mass_t'])
        self.assertEqual(inst['resolution_status'], RESOURCE_UNRESOLVED)
        row_codes = [d['code'] for r in rows for d in r['diagnostics']]
        self.assertIn('duplicate_craft_resource', row_codes)

    def test_duplicate_tanks_not_overwritten(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1}],
            resource_specs=[{'name': 'Fuel', 'density_raw': '0.001',
                             'density_t_per_unit': 0.001}])
        draft = make_draft([{'base_part': 'Tank',
                             'tanks': [
                                 {'name': 'Fuel', 'amount': '10',
                                  'max_amount': '10'},
                                 {'name': 'Fuel', 'amount': '10',
                                  'max_amount': '10'},
                             ]}])
        inst = extract(draft, index)['instances'][0]
        rows = inst['resource_evidence']
        self.assertEqual(len(rows), 2)
        codes = [d['code'] for r in rows for d in r['diagnostics']]
        self.assertIn('duplicate_tank_resource', codes)

    def test_unnamed_resource_diagnostic(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1}],
            resource_specs=[{'name': 'Fuel', 'density_raw': '0.001',
                             'density_t_per_unit': 0.001}])
        draft = make_draft([{'base_part': 'Tank',
                             'resources': [{'amount': '10',
                                            'max_amount': '10'}]}])
        inst = extract(draft, index)['instances'][0]
        row = inst['resource_evidence'][0]
        self.assertTrue(row['unnamed'])
        self.assertTrue(row['unresolved'])
        self.assertIn('unnamed_resource',
                      [d['code'] for d in row['diagnostics']])
        self.assertIsNone(inst['current_resource_mass_t'])

    def test_numeric_semantics_mirroring(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1}],
            resource_specs=[{'name': 'Fuel', 'density_raw': '0.001',
                             'density_t_per_unit': 0.001}])
        draft = make_draft([{'base_part': 'Tank',
                             'resources': [{'name': 'Fuel', 'amount': '10',
                                            'max_amount': '100'}],
                             'tanks': [{'name': 'Fuel', 'amount': '10.0',
                                        'max_amount': '100.0'}]}])
        inst = extract(draft, index)['instances'][0]
        row = inst['resource_evidence'][0]
        self.assertTrue(row['mirrored'])
        self.assertFalse(row['tank_mismatch'])

    def test_invalid_numbers_never_mirrored(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1}],
            resource_specs=[{'name': 'Fuel', 'density_raw': '0.001',
                             'density_t_per_unit': 0.001}])
        draft = make_draft([{'base_part': 'Tank',
                             'resources': [{'name': 'Fuel', 'amount': 'abc',
                                            'max_amount': '100'}],
                             'tanks': [{'name': 'Fuel', 'amount': 'abc',
                                        'max_amount': '100'}]}])
        inst = extract(draft, index)['instances'][0]
        row = inst['resource_evidence'][0]
        self.assertFalse(row['mirrored'])
        self.assertTrue(row['tank_mismatch'])
        self.assertIsNone(inst['current_resource_mass_t'])

    def test_invalid_density_lines_summary(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1}],
            resource_specs=[{'name': 'BadFuel', 'density_raw': 'not-a-number',
                             'density_t_per_unit': None}])
        draft = make_draft([{'base_part': 'Tank',
                             'resources': [{'name': 'BadFuel',
                                            'amount': '10',
                                            'max_amount': '10'}]}])
        report = extract(draft, index)
        self.assertEqual(report['invalid_density_lines'], 1)
        self.assertEqual(report['missing_density_lines'], 0)
        self.assertEqual(report['ambiguous_density_lines'], 0)
        row = report['instances'][0]['resource_evidence'][0]
        self.assertEqual(row['density_status'], 'invalid_density')

    def test_resolved_plus_unresolved_equals_total(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1,
                         'resources': [{'name': 'LiquidFuel',
                                        'amount': '100',
                                        'max_amount': '100'}]},
                        {'name': 'Pod', 'parent_url': '/S/Pod.cfg',
                         'mass_raw': '0.5', 'mass_t': 0.5}],
            resource_specs=[{'name': 'LiquidFuel', 'density_raw': '0.005',
                             'density_t_per_unit': 0.005}])
        draft = make_draft([{'base_part': 'Tank'},
                            {'base_part': 'Pod'},
                            {'base_part': 'NoSuch'}])
        report = extract(draft, index)
        self.assertEqual(report['resolved_instances']
                         + report['unresolved_instances'],
                         report['total_instances'])
        self.assertEqual(report['total_instances'], 3)
        self.assertEqual(report['resolved_instances'], 1)
        self.assertEqual(report['unresolved_instances'], 2)
        self.assertEqual(report['resource_state_missing_lines'], 1)
        self.assertEqual(report['config_default_only_lines'], 1)

    def test_config_default_only_and_tank_mismatch_counters(self):
        index = make_index(
            part_specs=[{'name': 'Tank', 'parent_url': '/S/T.cfg',
                         'mass_raw': '0.1', 'mass_t': 0.1,
                         'resources': [{'name': 'Fuel', 'amount': '100',
                                        'max_amount': '100'}]}],
            resource_specs=[{'name': 'Fuel', 'density_raw': '0.001',
                             'density_t_per_unit': 0.001}])
        draft = make_draft([{'base_part': 'Tank',
                             'resources': [{'name': 'Fuel', 'amount': '10',
                                            'max_amount': '10'}],
                             'tanks': [{'name': 'Fuel', 'amount': '20',
                                        'max_amount': '20'}]}])
        report = extract(draft, index)
        self.assertEqual(report['tank_mismatch_lines'], 1)
        self.assertEqual(report['config_default_only_lines'], 0)


class MassEvidenceRework2Tests(unittest.TestCase):
    """PART top-level RESOURCE vs ModuleFuelTanks/TANK template separation."""

    def _mk1pod_index(self, resources=None):
        """mk1pod_v2-like record: ModuleFuelTanks templates + optional
        top-level RESOURCE defaults."""
        spec = {
            'name': 'mk1pod_v2', 'parent_url': '/Squad/mk1pod_v2.cfg',
            'mass_raw': '0.8', 'mass_t': 0.8,
            'fuel_tanks': [{'volume': '50.05', 'tanks': [
                {'name': 'ElectricCharge', 'amount': 'full',
                 'max_amount': '50'},
                {'name': 'Hydrazine', 'amount': 'full',
                 'max_amount': '50'},
            ]}],
        }
        if resources:
            spec['resources'] = resources
        return make_index(
            part_specs=[spec],
            resource_specs=[
                {'name': 'ElectricCharge', 'density_raw': '0',
                 'density_t_per_unit': 0.0},
                {'name': 'Hydrazine', 'density_raw': '0.001004',
                 'density_t_per_unit': 0.001004},
                {'name': 'LiquidFuel', 'density_raw': '0.005',
                 'density_t_per_unit': 0.005},
            ])

    def test_tank_template_without_craft_state_creates_no_row(self):
        index = self._mk1pod_index()
        draft = make_draft([{'base_part': 'mk1pod_v2',
                             'resources': [{'name': 'ElectricCharge',
                                            'amount': '1000',
                                            'max_amount': '1000'}],
                             'tanks': [{'name': 'ElectricCharge',
                                        'amount': '1000',
                                        'max_amount': '1000'}]}])
        inst = extract(draft, index)['instances'][0]
        rows = inst['resource_evidence']
        self.assertEqual([r['name'] for r in rows], ['ElectricCharge'])
        self.assertEqual(inst['resolution_status'], RESOLVED_STATIC)
        self.assertNotIn('resource_state_missing',
                         [d['code'] for d in inst['diagnostics']])
        self.assertNotIn('resource_unresolved',
                         [d['code'] for d in inst['diagnostics']])
        template_names = [o['name'] for o in inst['module_fuel_tank_options']]
        self.assertEqual(template_names,
                         ['ElectricCharge', 'Hydrazine'])
        hydrazine_row = next((o for o in inst['module_fuel_tank_options']
                              if o['name'] == 'Hydrazine'), None)
        self.assertEqual(hydrazine_row['amount_raw'], 'full')
        self.assertEqual(hydrazine_row['max_amount_raw'], '50')

    def test_craft_state_beats_template_capacity(self):
        index = self._mk1pod_index()
        draft = make_draft([{'base_part': 'mk1pod_v2',
                             'resources': [{'name': 'ElectricCharge',
                                            'amount': '1000',
                                            'max_amount': '1000'}],
                             'tanks': [{'name': 'ElectricCharge',
                                        'amount': '1000',
                                        'max_amount': '1000'}]}])
        inst = extract(draft, index)['instances'][0]
        row = inst['resource_evidence'][0]
        self.assertEqual(inst['current_resource_mass_t'], 0.0)
        self.assertEqual(inst['saved_maximum_resource_mass_t'], 0.0)
        self.assertIsNone(inst['configured_maximum_resource_mass_t'])
        self.assertIsNone(row['configured_maximum_mass_t'])
        self.assertFalse(row['config_default_present'])
        self.assertFalse(row['tank_mismatch'])
        options = row['module_fuel_tank_options']
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]['max_amount_raw'], '50')

    def test_template_amount_full_not_invalid(self):
        index = self._mk1pod_index()
        draft = make_draft([{'base_part': 'mk1pod_v2',
                             'resources': [{'name': 'ElectricCharge',
                                            'amount': '1000',
                                            'max_amount': '1000'}]}])
        inst = extract(draft, index)['instances'][0]
        row_codes = [d['code'] for r in inst['resource_evidence']
                     for d in r['diagnostics']]
        self.assertNotIn('invalid_amount', row_codes)
        option = inst['module_fuel_tank_options'][0]
        self.assertEqual(option['amount_raw'], 'full')

    def test_top_level_resource_missing_state_still_unresolved(self):
        index = self._mk1pod_index(resources=[
            {'name': 'LiquidFuel', 'amount': 'full', 'max_amount': '100'}])
        draft = make_draft([{'base_part': 'mk1pod_v2',
                             'resources': [{'name': 'ElectricCharge',
                                            'amount': '1000',
                                            'max_amount': '1000'}]}])
        inst = extract(draft, index)['instances'][0]
        self.assertEqual(inst['resolution_status'], RESOURCE_UNRESOLVED)
        rows = inst['resource_evidence']
        self.assertEqual([r['name'] for r in rows],
                         ['ElectricCharge', 'LiquidFuel'])
        fuel_row = next(r for r in rows if r['name'] == 'LiquidFuel')
        self.assertTrue(fuel_row['state_missing'])
        self.assertEqual(fuel_row['source'], 'config_default_only')
        self.assertEqual(fuel_row['configured_maximum_mass_t'], 0.5)

    def test_same_name_default_and_template_stay_separate(self):
        index = self._mk1pod_index(resources=[
            {'name': 'ElectricCharge', 'amount': 'full', 'max_amount': '200'}])
        draft = make_draft([{'base_part': 'mk1pod_v2',
                             'resources': [{'name': 'ElectricCharge',
                                            'amount': '1000',
                                            'max_amount': '1000'}]}])
        inst = extract(draft, index)['instances'][0]
        row = inst['resource_evidence'][0]
        row_codes = [d['code'] for d in row['diagnostics']]
        self.assertNotIn('config_default_ambiguous', row_codes)
        self.assertTrue(row['config_default_present'])
        self.assertEqual(row['config_default_amount_raw'], 'full')
        self.assertEqual(row['config_default_max_amount_raw'], '200')
        self.assertEqual(inst['configured_maximum_resource_mass_t'], 0.0)
        options = row['module_fuel_tank_options']
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]['max_amount_raw'], '50')

    def test_template_only_no_resources_is_zero(self):
        index = self._mk1pod_index()
        inst = extract(make_draft([{'base_part': 'mk1pod_v2'}]), index
                       )['instances'][0]
        self.assertEqual(inst['resolution_status'], RESOLVED_STATIC)
        self.assertEqual(inst['current_resource_mass_t'], 0.0)
        self.assertEqual(inst['current_part_mass_estimate_t'], 0.8)

    def test_r2_summary_counters(self):
        index = self._mk1pod_index()
        draft = make_draft([{'base_part': 'mk1pod_v2',
                             'resources': [{'name': 'ElectricCharge',
                                            'amount': '1000',
                                            'max_amount': '1000'}],
                             'tanks': [{'name': 'ElectricCharge',
                                        'amount': '1000',
                                        'max_amount': '1000'}]}])
        report = extract(draft, index)
        self.assertEqual(report['resolved_instances'], 1)
        self.assertEqual(report['unresolved_instances'], 0)
        self.assertEqual(report['config_default_only_lines'], 0)
        self.assertEqual(report['resource_state_missing_lines'], 0)
        self.assertEqual(report['total_resource_lines'], 1)
        self.assertEqual(report['mirrored_resource_lines'], 1)
        self.assertEqual(report['tank_mismatch_lines'], 0)


@unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
@unittest.skipUnless(REAL_CACHE.is_file(),
                     'real KSP install not present on this machine')
class RealVabMassSmokeTests(unittest.TestCase):
    """Read-only per-part mass evidence over the real VAB folder."""

    def test_all_instances_get_evidence(self):
        from server.ksp_install_index import build_ksp_index
        index = build_ksp_index(REAL_KSP)
        self.assertEqual(index.status, 'ok')
        totals = {'instances': 0, 'matched': 0, 'resolved': 0,
                  'unresolved': 0}
        for craft_path in sorted(Path('VAB').glob('*.craft')):
            if craft_path.name.endswith('.original'):
                continue
            draft = parse_craft(craft_path.read_text(encoding='utf-8-sig'),
                                craft_path.name)
            report = extract(draft, index)
            totals['instances'] += report['total_instances']
            totals['matched'] += report['evidence_instances'] \
                + report['match_unavailable_instances']
            totals['resolved'] += report['resolved_instances']
            totals['unresolved'] += report['unresolved_instances']
        self.assertEqual(totals['instances'], 389)
        self.assertEqual(totals['matched'], 389)
        self.assertEqual(totals['resolved'] + totals['unresolved'],
                         totals['instances'])


# ---------------------------------------------------------------------------
# 6B.3C.4A: RealChute settled-mass production evidence chain
# ---------------------------------------------------------------------------

ATTEMPT_002_RESULT_SHA = (
    '2c1433b7183614385615dd65b6d59bf8be716c825c49b9b9ffbee24ab755e178')
AUTHORIZED_CACHE_SHA = (
    '15c6d8dd36fd9310c964d67f0172906311956e19824ae38f66a1269a2bccaff3')
AUTHORIZED_DLL_SHA = (
    '884acb3f4537a2164fa4b6d798be538c6fed54997c5de0a7674527bc973b56d5')
AUTHORIZED_PROVENANCE = {
    'config_cache_sha256': AUTHORIZED_CACHE_SHA,
    'realchute_dll_sha256': AUTHORIZED_DLL_SHA,
}
SETTLED_207_T = 0.0940142422914505
SETTLED_250_T = 0.10273437201976776
CHUTE_MASS_207_T = 0.01901424117386341
CHUTE_MASS_250_T = 0.02773437276482582


def _realchute_config_record(name='parachuteSingle', material='Nylon',
                             case_mass='0.075000003',
                             deployed_diameter='20.7'):
    return {
        'name': name,
        'parent_url': '/RealChute/Parachutes/parachuteSingle.cfg',
        'cache_order': 0,
        'mass_raw': '0.075',
        'mass_t': 0.075,
        'modules': ['RealChuteModule', 'ProceduralChute'],
        'dynamic_mass_modules': [],
        'resources': [],
        'fuel_tanks': [],
        'module_evidence': {
            'RealChuteModule': [{
                'case_mass': 0.075000003,
                'case_mass_raw': case_mass,
                'spare_chutes': 5.0,
                'spare_chutes_raw': '5',
                'parachutes': [{
                    'material': material,
                    'deployed_diameter': float(deployed_diameter),
                    'pre_deployed_diameter': 0.8,
                }],
            }],
            'ProceduralChute': [{'chutes': []}],
        },
    }


def _realchute_craft_part(mod_mass_t=3.7252903e-09,
                          calc_select=False, dep_diam='20.7',
                          chute_count='1', get_mass=True, use_dry=True,
                          case_mass=0.075000003, chutes=1):
    chute_rows = [{
        'calc_select': calc_select,
        'get_mass': get_mass,
        'use_dry': use_dry,
        'mass': 10.0,
        'chute_count': float(chute_count) if chute_count is not None
        else None,
        'pre_dep_diam': 0.8,
        'dep_diam': float(dep_diam) if dep_diam is not None else None,
    } for _ in range(chutes)]
    return {
        'mod_mass_present': mod_mass_t is not None,
        'mod_mass_raw': str(mod_mass_t) if mod_mass_t is not None
        else None,
        'mod_mass_t': mod_mass_t,
        'module_evidence': {
            'RealChuteModule': [{
                'case_mass': case_mass,
                'case_mass_raw': str(case_mass),
                'spare_chutes': 5.0,
                'spare_chutes_raw': '5',
                'parachutes': [],
            }],
            'ProceduralChute': [{'chutes': chute_rows}],
        },
    }


class RealChuteSettlementSemanticsTests(unittest.TestCase):
    """Pure settled-mass formula + settlement evidence entry (6B.3C.4A)."""

    def test_settled_mass_20_7(self):
        from server.ksp_module_mass_semantics import (
            realchute_settled_part_mass)
        settled, diags = realchute_settled_part_mass(
            0.075000003, [CHUTE_MASS_207_T])
        self.assertEqual(diags, [])
        self.assertEqual(settled, SETTLED_207_T)
        self.assertLessEqual(abs(settled - 0.094014243), 7.5e-9)

    def test_settled_mass_25_0(self):
        from server.ksp_module_mass_semantics import (
            realchute_settled_part_mass)
        settled, diags = realchute_settled_part_mass(
            0.075000003, [CHUTE_MASS_250_T])
        self.assertEqual(diags, [])
        self.assertEqual(settled, SETTLED_250_T)
        # the task's approximate figure is the double sum rounded; the
        # IL-verified result is the float32 cast, within one quantization
        # step
        self.assertLessEqual(abs(settled - 0.102734376), 7.5e-9)

    def test_binary32_accumulation_order(self):
        from server.ksp_module_mass_semantics import (
            realchute_settled_part_mass, to_float32)
        settled, _ = realchute_settled_part_mass(
            0.075000003, [CHUTE_MASS_207_T])
        self.assertEqual(
            settled,
            to_float32(0.075000003 + CHUTE_MASS_207_T))

    def test_missing_and_invalid_not_zero(self):
        from server.ksp_module_mass_semantics import (
            realchute_settled_part_mass)
        for case, chutes in ((None, [0.019]),
                             (float('nan'), [0.019]),
                             (float('inf'), [0.019]),
                             (-1.0, [0.019]),
                             (0.1, [None]),
                             (0.1, [float('nan')]),
                             (0.1, [float('inf')]),
                             (0.1, [-0.2])):
            value, diags = realchute_settled_part_mass(case, chutes)
            self.assertIsNone(value)
            self.assertTrue(diags)

    def test_settlement_evidence_settled(self):
        from server.ksp_module_mass_semantics import (
            realchute_part_settlement_evidence)
        record = realchute_part_settlement_evidence(
            craft_part=_realchute_craft_part(),
            config_record=_realchute_config_record(),
            material_record={'name': 'Nylon', 'area_density': 5.65e-05,
                             'area_density_raw': '0.0000565'},
            provenance=AUTHORIZED_PROVENANCE,
            config_cache_sha256=AUTHORIZED_CACHE_SHA)
        self.assertEqual(record['status'], 'settled')
        self.assertEqual(record['model'], 'realchute_settled_mass')
        self.assertEqual(record['settled_part_mass_t'], SETTLED_207_T)
        self.assertEqual(record['case_mass_t'], 0.075000003)
        self.assertEqual(record['chute_mass_t'], CHUTE_MASS_207_T)
        self.assertEqual(record['deployed_diameter_m'], 20.7)
        self.assertEqual(record['material'], 'Nylon')
        self.assertEqual(record['area_density'], 5.65e-05)
        self.assertEqual(record['saved_mod_mass_t'], 3.7252903e-09)
        self.assertEqual(record['evidence_missing'], [])
        self.assertEqual(
            record['source']['attempt_002_result_sha256'],
            ATTEMPT_002_RESULT_SHA)
        self.assertEqual(
            record['source']['authorization_id'],
            'realchute_settled_mass_v1')

    def test_settlement_evidence_fingerprint_mismatch(self):
        from server.ksp_module_mass_semantics import (
            realchute_part_settlement_evidence)
        record = realchute_part_settlement_evidence(
            craft_part=_realchute_craft_part(),
            config_record=_realchute_config_record(),
            material_record={'name': 'Nylon', 'area_density': 5.65e-05},
            provenance=AUTHORIZED_PROVENANCE,
            config_cache_sha256='f' * 64)
        self.assertEqual(record['status'], 'dynamic_unresolved')
        self.assertIn('realchute_fingerprint_mismatch',
                      [d['code'] for d in record['diagnostics']])
        self.assertIn('config_cache_sha256', record['evidence_missing'])

    def test_settlement_evidence_fingerprint_missing(self):
        from server.ksp_module_mass_semantics import (
            realchute_part_settlement_evidence)
        record = realchute_part_settlement_evidence(
            craft_part=_realchute_craft_part(),
            config_record=_realchute_config_record(),
            material_record={'name': 'Nylon', 'area_density': 5.65e-05},
            provenance=None, config_cache_sha256=None)
        self.assertEqual(record['status'], 'dynamic_unresolved')
        self.assertIn('realchute_fingerprint_missing',
                      [d['code'] for d in record['diagnostics']])

    def test_settlement_evidence_dll_mismatch(self):
        from server.ksp_module_mass_semantics import (
            realchute_part_settlement_evidence)
        record = realchute_part_settlement_evidence(
            craft_part=_realchute_craft_part(),
            config_record=_realchute_config_record(),
            material_record={'name': 'Nylon', 'area_density': 5.65e-05},
            provenance={'realchute_dll_sha256': 'a' * 64},
            config_cache_sha256=AUTHORIZED_CACHE_SHA)
        self.assertEqual(record['status'], 'dynamic_unresolved')
        self.assertIn('realchute_fingerprint_mismatch',
                      [d['code'] for d in record['diagnostics']])

    def test_settlement_evidence_automatic_unsupported(self):
        from server.ksp_module_mass_semantics import (
            realchute_part_settlement_evidence)
        record = realchute_part_settlement_evidence(
            craft_part=_realchute_craft_part(calc_select=True),
            config_record=_realchute_config_record(),
            material_record={'name': 'Nylon', 'area_density': 5.65e-05},
            provenance=AUTHORIZED_PROVENANCE,
            config_cache_sha256=AUTHORIZED_CACHE_SHA)
        self.assertEqual(record['status'], 'unsupported_domain')
        self.assertIn('realchute_scope_unsupported',
                      [d['code'] for d in record['diagnostics']])
        self.assertIsNone(record['settled_part_mass_t'])

    def test_settlement_evidence_non_nylon_unsupported(self):
        from server.ksp_module_mass_semantics import (
            realchute_part_settlement_evidence)
        record = realchute_part_settlement_evidence(
            craft_part=_realchute_craft_part(),
            config_record=_realchute_config_record(material='Silk'),
            material_record={'name': 'Silk', 'area_density': 5.65e-05},
            provenance=AUTHORIZED_PROVENANCE,
            config_cache_sha256=AUTHORIZED_CACHE_SHA)
        self.assertEqual(record['status'], 'unsupported_domain')

    def test_settlement_evidence_multi_chute_unsupported(self):
        from server.ksp_module_mass_semantics import (
            realchute_part_settlement_evidence)
        record = realchute_part_settlement_evidence(
            craft_part=_realchute_craft_part(chutes=2),
            config_record=_realchute_config_record(),
            material_record={'name': 'Nylon', 'area_density': 5.65e-05},
            provenance=AUTHORIZED_PROVENANCE,
            config_cache_sha256=AUTHORIZED_CACHE_SHA)
        self.assertEqual(record['status'], 'unsupported_domain')

    def test_settlement_evidence_diameter_out_of_range(self):
        from server.ksp_module_mass_semantics import (
            realchute_part_settlement_evidence)
        record = realchute_part_settlement_evidence(
            craft_part=_realchute_craft_part(dep_diam='26.0'),
            config_record=_realchute_config_record(),
            material_record={'name': 'Nylon', 'area_density': 5.65e-05},
            provenance=AUTHORIZED_PROVENANCE,
            config_cache_sha256=AUTHORIZED_CACHE_SHA)
        self.assertEqual(record['status'], 'unsupported_domain')

    def test_settlement_evidence_case_mass_missing(self):
        from server.ksp_module_mass_semantics import (
            realchute_part_settlement_evidence)
        record = realchute_part_settlement_evidence(
            craft_part=_realchute_craft_part(case_mass=None),
            config_record=_realchute_config_record(),
            material_record={'name': 'Nylon', 'area_density': 5.65e-05},
            provenance=AUTHORIZED_PROVENANCE,
            config_cache_sha256=AUTHORIZED_CACHE_SHA)
        self.assertEqual(record['status'], 'dynamic_unresolved')
        self.assertIn('case_mass', record['evidence_missing'])

    def test_settlement_evidence_material_missing(self):
        from server.ksp_module_mass_semantics import (
            realchute_part_settlement_evidence)
        record = realchute_part_settlement_evidence(
            craft_part=_realchute_craft_part(),
            config_record=_realchute_config_record(material=None),
            material_record=None,
            provenance=AUTHORIZED_PROVENANCE,
            config_cache_sha256=AUTHORIZED_CACHE_SHA)
        self.assertEqual(record['status'], 'dynamic_unresolved')
        self.assertIn('material', record['evidence_missing'])

    def test_settlement_evidence_area_density_missing(self):
        from server.ksp_module_mass_semantics import (
            realchute_part_settlement_evidence)
        record = realchute_part_settlement_evidence(
            craft_part=_realchute_craft_part(),
            config_record=_realchute_config_record(),
            material_record=None,
            provenance=AUTHORIZED_PROVENANCE,
            config_cache_sha256=AUTHORIZED_CACHE_SHA)
        self.assertEqual(record['status'], 'dynamic_unresolved')
        self.assertIn('material_area_density', record['evidence_missing'])

    def test_settlement_evidence_diameter_missing(self):
        from server.ksp_module_mass_semantics import (
            realchute_part_settlement_evidence)
        record = realchute_part_settlement_evidence(
            craft_part=_realchute_craft_part(dep_diam=None),
            config_record=_realchute_config_record(),
            material_record={'name': 'Nylon', 'area_density': 5.65e-05},
            provenance=AUTHORIZED_PROVENANCE,
            config_cache_sha256=AUTHORIZED_CACHE_SHA)
        self.assertEqual(record['status'], 'dynamic_unresolved')
        self.assertIn('deployed_diameter', record['evidence_missing'])

    def test_settlement_evidence_saved_mod_mass_invalid(self):
        from server.ksp_module_mass_semantics import (
            realchute_part_settlement_evidence)
        craft_part = _realchute_craft_part()
        craft_part['mod_mass_present'] = True
        craft_part['mod_mass_raw'] = 'NaN'
        craft_part['mod_mass_t'] = None
        record = realchute_part_settlement_evidence(
            craft_part=craft_part,
            config_record=_realchute_config_record(),
            material_record={'name': 'Nylon', 'area_density': 5.65e-05},
            provenance=AUTHORIZED_PROVENANCE,
            config_cache_sha256=AUTHORIZED_CACHE_SHA)
        self.assertEqual(record['status'], 'dynamic_unresolved')
        self.assertIn('realchute_saved_mod_mass_invalid',
                      [d['code'] for d in record['diagnostics']])

    def test_settlement_evidence_module_state_ambiguous(self):
        from server.ksp_module_mass_semantics import (
            realchute_part_settlement_evidence)
        craft_part = _realchute_craft_part()
        craft_part['module_evidence']['RealChuteModule'].append(
            dict(craft_part['module_evidence']['RealChuteModule'][0]))
        record = realchute_part_settlement_evidence(
            craft_part=craft_part,
            config_record=_realchute_config_record(),
            material_record={'name': 'Nylon', 'area_density': 5.65e-05},
            provenance=AUTHORIZED_PROVENANCE,
            config_cache_sha256=AUTHORIZED_CACHE_SHA)
        self.assertEqual(record['status'], 'dynamic_unresolved')
        self.assertIn('realchute_module_state_ambiguous',
                      [d['code'] for d in record['diagnostics']])

    def test_settlement_evidence_not_applicable(self):
        from server.ksp_module_mass_semantics import (
            realchute_part_settlement_evidence)
        record = realchute_part_settlement_evidence(
            craft_part=_realchute_craft_part(),
            config_record=_realchute_config_record(name='OtherPart'),
            material_record={'name': 'Nylon', 'area_density': 5.65e-05},
            provenance=AUTHORIZED_PROVENANCE,
            config_cache_sha256=AUTHORIZED_CACHE_SHA)
        self.assertEqual(record['status'], 'not_applicable')


def _parachute_index(cache_sha=AUTHORIZED_CACHE_SHA,
                     realchute_dll=AUTHORIZED_DLL_SHA):
    """Index with parachuteSingle + Nylon for production evidence tests."""
    from server.ksp_install_index import KspIndex
    parts = [_realchute_config_record()]
    resources = [{
        'kind': 'resource', 'name': 'ElectricCharge',
        'parent_url': '/Resources/Generic.cfg', 'cache_order': 0,
        'primary': True, 'diagnostics': [],
        'density_raw': '0', 'density_t_per_unit': 0.0,
        'unit_cost_raw': None, 'unit_cost': None,
        'flow_mode': None, 'transfer': None,
    }]
    materials = [{
        'kind': 'material', 'name': 'Nylon',
        'parent_url': 'RealChute/Materials/ParachuteMaterials.cfg',
        'cache_order': 0, 'primary': True, 'diagnostics': [],
        'area_density_raw': '0.0000565', 'area_density': 5.65e-05,
    }]
    metadata = {
        'schema_version': 1, 'source_kind': 'module_manager_config_cache',
        'ksp_root': 'C:/fake',
        'config_cache_path': 'C:/fake/GameData/ModuleManager.ConfigCache',
        'file_size': 123, 'mtime_ns': 456,
        'sha256': cache_sha if cache_sha != AUTHORIZED_CACHE_SHA
        else AUTHORIZED_CACHE_SHA,
        'patched_node_count': 1, 'built_at': '2026-01-01T00:00:00+00:00',
        'status': 'ok',
        'record_counts': {'parts': len(parts), 'resources': len(resources),
                          'sstu_layouts': 0, 'anonymous_nodes': 0,
                          'duplicate_definitions': 0},
    }
    index = KspIndex(status='ok', metadata=metadata, parts=parts,
                     resources=resources, sstu_layouts=[],
                     diagnostics=[])
    index.materials = materials

    def find_material(name):
        for record in materials:
            if record['name'] == name:
                return record
        return None

    index.find_material = find_material
    return index


def _parachute_provenance(cache_sha=AUTHORIZED_CACHE_SHA,
                          realchute_dll=AUTHORIZED_DLL_SHA):
    return {
        'config_cache_sha256': cache_sha,
        'realchute_dll_sha256': realchute_dll,
    }


class RealChuteProductionEvidenceTests(unittest.TestCase):
    """Production per-part evidence with the settlement gate (6B.3C.4A)."""

    def _extract(self, part_specs, cache_sha=AUTHORIZED_CACHE_SHA,
                 realchute_dll=AUTHORIZED_DLL_SHA, provenance=None):
        index = _parachute_index(cache_sha=cache_sha,
                                 realchute_dll=realchute_dll)
        if provenance is None:
            provenance = _parachute_provenance(
                cache_sha=cache_sha, realchute_dll=realchute_dll)
        draft = make_draft(part_specs)
        return extract_part_mass_evidence(
            draft, index, match_craft_parts(draft, index),
            provenance=provenance)

    def test_settled_m1_uses_settled_mass(self):
        report = self._extract([
            {'base_part': 'parachuteSingle',
             'module_evidence': _realchute_craft_part()['module_evidence'],
             'mod_mass_present': True, 'mod_mass_raw': '3.7252903E-09',
             'mod_mass_t': 3.7252903e-09}])
        inst = report['instances'][0]
        self.assertEqual(inst['resolution_status'],
                         ESTIMATED_WITH_MODIFIER)
        self.assertEqual(inst['static_dry_mass_estimate_t'], SETTLED_207_T)
        self.assertEqual(inst['current_part_mass_estimate_t'],
                         round(SETTLED_207_T, 12))
        semantics = inst['mass_semantics_evidence']
        self.assertEqual(semantics['status'], 'settled')
        self.assertEqual(semantics['saved_mod_mass_t'], 3.7252903e-09)
        # base mass + saved modMass must NOT be used
        self.assertNotEqual(inst['static_dry_mass_estimate_t'],
                            0.075 + 3.7252903e-09)
        diag_codes = [d['code'] for d in inst['diagnostics']]
        self.assertIn('realchute_saved_mod_mass_diagnostic', diag_codes)

    def test_settled_m3_does_not_add_mod_mass_again(self):
        report = self._extract([
            {'base_part': 'parachuteSingle',
             'module_evidence': _realchute_craft_part(
                 dep_diam='25.0')['module_evidence'],
             'mod_mass_present': True, 'mod_mass_raw': '0.00872013345',
             'mod_mass_t': 0.00872013345}])
        inst = report['instances'][0]
        self.assertEqual(inst['resolution_status'],
                         ESTIMATED_WITH_MODIFIER)
        self.assertEqual(inst['static_dry_mass_estimate_t'], SETTLED_250_T)
        self.assertNotEqual(inst['static_dry_mass_estimate_t'],
                            SETTLED_250_T + 0.00872013345)

    def test_two_independent_chutes_linear(self):
        report = self._extract([
            {'base_part': 'parachuteSingle',
             'module_evidence': _realchute_craft_part()['module_evidence']},
            {'base_part': 'parachuteSingle',
             'module_evidence': _realchute_craft_part()['module_evidence']}])
        self.assertEqual(len(report['instances']), 2)
        for inst in report['instances']:
            self.assertEqual(inst['static_dry_mass_estimate_t'],
                             SETTLED_207_T)
        total = sum(i['static_dry_mass_estimate_t']
                    for i in report['instances'])
        self.assertEqual(total, 2 * SETTLED_207_T)

    def test_config_cache_mismatch_fails_closed(self):
        report = self._extract(
            [{'base_part': 'parachuteSingle',
              'module_evidence':
                  _realchute_craft_part()['module_evidence']}],
            cache_sha='f' * 64)
        inst = report['instances'][0]
        self.assertEqual(inst['resolution_status'], DYNAMIC_UNRESOLVED)
        self.assertIsNone(inst['static_dry_mass_estimate_t'])
        self.assertIsNone(inst['current_part_mass_estimate_t'])
        self.assertIn('realchute_fingerprint_mismatch',
                      [d['code'] for d in inst['diagnostics']])

    def test_dll_mismatch_fails_closed(self):
        report = self._extract(
            [{'base_part': 'parachuteSingle',
              'module_evidence':
                  _realchute_craft_part()['module_evidence']}],
            realchute_dll='a' * 64)
        inst = report['instances'][0]
        self.assertEqual(inst['resolution_status'], DYNAMIC_UNRESOLVED)
        self.assertIsNone(inst['current_part_mass_estimate_t'])

    def test_missing_provenance_fails_closed(self):
        report = self._extract(
            [{'base_part': 'parachuteSingle',
              'module_evidence':
                  _realchute_craft_part()['module_evidence']}],
            provenance={})
        inst = report['instances'][0]
        self.assertEqual(inst['resolution_status'], DYNAMIC_UNRESOLVED)
        self.assertIn('realchute_fingerprint_missing',
                      [d['code'] for d in inst['diagnostics']])

    def test_automatic_fails_closed(self):
        report = self._extract([
            {'base_part': 'parachuteSingle',
             'module_evidence': _realchute_craft_part(
                 calc_select=True)['module_evidence']}])
        inst = report['instances'][0]
        self.assertEqual(inst['resolution_status'], DYNAMIC_UNRESOLVED)
        self.assertIsNone(inst['current_part_mass_estimate_t'])
        self.assertEqual(inst['mass_semantics_evidence']['status'],
                         'unsupported_domain')

    def test_non_nylon_fails_closed(self):
        report = self._extract([
            {'base_part': 'parachuteSingle',
             'module_evidence': _realchute_craft_part()['module_evidence']}],
            cache_sha=AUTHORIZED_CACHE_SHA)
        # swap the config material record (index-level)
        index = _parachute_index()
        index.parts[0]['module_evidence']['RealChuteModule'][0][
            'parachutes'][0]['material'] = 'Silk'
        draft = make_draft([
            {'base_part': 'parachuteSingle',
             'module_evidence': _realchute_craft_part()['module_evidence']}])
        report = extract_part_mass_evidence(
            draft, index, match_craft_parts(draft, index),
            provenance=AUTHORIZED_PROVENANCE)
        inst = report['instances'][0]
        self.assertEqual(inst['resolution_status'], DYNAMIC_UNRESOLVED)
        self.assertEqual(inst['mass_semantics_evidence']['status'],
                         'unsupported_domain')

    def test_plain_static_part_unchanged(self):
        index = make_index(part_specs=[
            {'name': 'Pod', 'parent_url': '/S/Pod.cfg',
             'mass_raw': '0.5', 'mass_t': 0.5}])
        draft = make_draft([{'base_part': 'Pod'}])
        report = extract_part_mass_evidence(
            draft, index, match_craft_parts(draft, index),
            provenance=AUTHORIZED_PROVENANCE)
        inst = report['instances'][0]
        self.assertEqual(inst['resolution_status'], RESOLVED_STATIC)
        self.assertEqual(inst['static_dry_mass_estimate_t'], 0.5)
        self.assertIsNone(inst['mass_semantics_evidence'])


@unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
@unittest.skipUnless(REAL_CACHE.is_file(),
                     'real KSP install not present on this machine')
class RealChuteAttempt002EndToEndTests(unittest.TestCase):
    """Read-only end-to-end regression over the attempt-002 archive."""

    ATTEMPT_DIR = Path(
        (Path(__file__).resolve().parents[1] / 'experiments/control_mass/attempts/attempt-002'))

    @classmethod
    def setUpClass(cls):
        import hashlib
        from server.ksp_install_index import build_ksp_index
        from server.ksp_runtime_fingerprints import ksp_runtime_provenance
        cls.index = build_ksp_index(REAL_KSP)
        cls.provenance = ksp_runtime_provenance(REAL_KSP)
        cls.input_dir = cls.ATTEMPT_DIR / 'input'
        cls.hash = hashlib.sha256

    def test_evidence_hashes_match_archive(self):
        import json as _json
        if not self.ATTEMPT_DIR.is_dir():
            self.skipTest('attempt-002 archive missing')
        experiment_dir = self.ATTEMPT_DIR.parent.parent
        evidence = _json.loads((self.ATTEMPT_DIR / 'evidence_hashes.json')
                               .read_text(encoding='utf-8'))
        for fname, info in evidence['files'].items():
            path = experiment_dir / info['path'].replace('\\', '/')
            self.assertTrue(path.is_file(), fname)
            self.assertEqual(
                self.hash(path.read_bytes()).hexdigest(), info['sha256'],
                fname)

    def _load(self, name):
        from server.ksp_part_matcher import enrich_craft_draft
        draft = parse_craft(
            (self.input_dir / f'{name}.craft').read_text(
                encoding='utf-8-sig'), f'{name}.craft')
        draft = enrich_craft_draft(draft, self.index)
        evidence = extract_part_mass_evidence(
            draft, self.index,
            installation_match=draft.get('installation_match'),
            provenance=self.provenance)
        return draft, evidence

    def _assert_archive_applicability(self, chute):
        # A real game boot may regenerate the patch cache. Archived measured
        # values remain valid only for their original fingerprints; verify the
        # production refusal instead of silently reauthorizing stale evidence.
        if (self.provenance['config_cache_sha256'].lower() == AUTHORIZED_CACHE_SHA
                and self.provenance['realchute_dll_sha256'].lower() == AUTHORIZED_DLL_SHA):
            return True
        self.assertEqual(chute['resolution_status'], DYNAMIC_UNRESOLVED)
        self.assertIsNone(chute['static_dry_mass_estimate_t'])
        codes = [d['code'] for d in chute['mass_semantics_evidence']['diagnostics']]
        self.assertIn('realchute_fingerprint_mismatch', codes)
        return False

    def test_four_way_reconciliation_within_strict_tolerance(self):
        from server.import_service import _loadmeta_metadata
        from server.ksp_craft_mass_reconciliation import (
            reconcile_craft_mass)
        expected = {
            'M0_control': 0,
            'M1_one_chute_default': 1,
            'M2_two_chutes_default': 2,
            'M3_one_chute_changed_diameter': 1,
        }
        for name, chute_count in expected.items():
            draft, evidence = self._load(name)
            loadmeta = _loadmeta_metadata(
                (self.input_dir / f'{name}.loadmeta').read_text(
                    encoding='utf-8-sig'))
            reconciliation = reconcile_craft_mass(draft, evidence, loadmeta)
            chutes = [i for i in evidence['instances']
                      if i['canonical_name'] == 'parachuteSingle']
            self.assertEqual(len(chutes), chute_count, name)
            for chute in chutes:
                if not self._assert_archive_applicability(chute):
                    self.assertEqual(reconciliation['reconciliation_status'], 'evidence_incomplete')
                    continue
                self.assertEqual(chute['resolution_status'],
                                 ESTIMATED_WITH_MODIFIER, name)
                self.assertEqual(
                    chute['mass_semantics_evidence']['status'], 'settled',
                    name)
            if not chutes or self._assert_archive_applicability(chutes[0]):
                residual = reconciliation['residual_include_all_t']
                self.assertIsNotNone(residual, name)
                tolerance = reconciliation['tolerance_t']
                self.assertLessEqual(abs(residual), tolerance, name)
                self.assertIn(
                    reconciliation['reconciliation_status'],
                    ('matched_include_all', 'ambiguous_scenario'), name)
            # stage masses are never auto-filled by this pipeline: the
            # placeholder stages stay placeholders (nothing computed)
            for stage in draft['stages']:
                self.assertTrue(stage['legacy_placeholder'], name)
                self.assertTrue(stage['requires_mass_confirmation'], name)
                self.assertEqual(stage['wet_mass_t'], 0.0, name)
                self.assertEqual(stage['dry_mass_t'], 0.0, name)
            self.assertNotIn('stage_masses', reconciliation, name)
            self.assertTrue(draft['requires_mass_confirmation'], name)

    def test_m1_single_chute_settled_mass(self):
        draft, evidence = self._load('M1_one_chute_default')
        chute = next(i for i in evidence['instances']
                     if i['canonical_name'] == 'parachuteSingle')
        if not self._assert_archive_applicability(chute):
            return
        self.assertEqual(chute['static_dry_mass_estimate_t'], SETTLED_207_T)

    def test_m2_two_independent_instances(self):
        draft, evidence = self._load('M2_two_chutes_default')
        chutes = [i for i in evidence['instances']
                  if i['canonical_name'] == 'parachuteSingle']
        self.assertEqual(len(chutes), 2)
        self.assertEqual(len({i['part'] for i in chutes}), 2)
        applicable = [self._assert_archive_applicability(chute) for chute in chutes]
        if not all(applicable):
            return
        self.assertEqual(
            sum(i['static_dry_mass_estimate_t'] for i in chutes),
            2 * SETTLED_207_T)
        # distinct instances, both settled
        self.assertEqual(len({i['part'] for i in chutes}), 2)

    def test_m3_no_mod_mass_double_count(self):
        draft, evidence = self._load('M3_one_chute_changed_diameter')
        chute = next(i for i in evidence['instances']
                     if i['canonical_name'] == 'parachuteSingle')
        if not self._assert_archive_applicability(chute):
            return
        self.assertEqual(chute['static_dry_mass_estimate_t'], SETTLED_250_T)
        self.assertNotEqual(chute['static_dry_mass_estimate_t'],
                            SETTLED_250_T + 0.00872013345)
        semantics = chute['mass_semantics_evidence']
        self.assertEqual(semantics['saved_mod_mass_t'], 0.00872013345)


if __name__ == '__main__':
    unittest.main()
