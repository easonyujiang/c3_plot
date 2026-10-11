import copy
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from server import create_app, import_service
from server.ksp_craft_mass_reconciliation import (
    AMBIGUOUS_SCENARIO,
    EVIDENCE_INCOMPLETE,
    MATCHED_EXCLUDING_NON_FLIGHT,
    MATCHED_INCLUDE_ALL,
    TARGET_INVALID,
    TARGET_MISSING,
    UNRECONCILED,
    reconcile_craft_mass,
)


def make_evidence(instances, fingerprint='0' * 64):
    return {
        'schema_version': 1,
        'source_kind': 'ksp_mass_evidence',
        'index_fingerprint': {'sha256': fingerprint,
                              'config_cache_path': 'C:/fake/cache'},
        'instances': instances,
    }


def make_instance(**over):
    instance = {
        'craft_index': 0,
        'part': 'Pod_1',
        'base_part': 'Pod',
        'persistent_id': 'p-0',
        'match_status': 'exact',
        'canonical_name': 'Pod',
        'part_source': {'parent_url': '/S/Pod.cfg', 'cache_order': 0},
        'current_part_mass_estimate_t': 1.0,
        'static_dry_mass_estimate_t': 1.0,
        'current_resource_mass_t': 0.0,
        'resolution_status': 'resolved_static',
        'dynamic_mass_modules': [],
        'contains_launch_clamp': False,
        'possible_non_flight_part': False,
        'diagnostics': [],
    }
    instance.update(over)
    return instance


def make_draft(filename='test.craft', part_count=None):
    """Craft draft whose parts/part_count stay consistent (count conserved)."""
    if part_count is None:
        part_count = 1
    parts = [{
        'index': index, 'part': f'P_{index}', 'base_part': 'P',
        'instance_id': str(index), 'persistent_id': f'p-{index}',
    } for index in range(part_count)]
    return {
        'rocket': {'name': 'Test'},
        'source': {'filename': filename, 'part_count': part_count},
        'parts': parts,
        'stages': [],
        'warnings': [],
        'structured_warnings': [],
    }


class ReconciliationTargetTests(unittest.TestCase):
    def test_legal_total_mass_parsed(self):
        report = reconcile_craft_mass(make_draft(), make_evidence(
            [make_instance()]), {'totalMass': '12.5'})
        self.assertEqual(report['source_total_mass_raw'], '12.5')
        self.assertEqual(report['source_total_mass_t'], 12.5)

    def test_missing_total_mass(self):
        report = reconcile_craft_mass(make_draft(), make_evidence(
            [make_instance()]), None)
        self.assertEqual(report['reconciliation_status'], TARGET_MISSING)
        self.assertIsNone(report['source_total_mass_t'])
        report2 = reconcile_craft_mass(make_draft(), make_evidence(
            [make_instance()]), {'partCount': '5'})
        self.assertEqual(report2['reconciliation_status'], TARGET_MISSING)

    def test_non_numeric_total_mass(self):
        report = reconcile_craft_mass(make_draft(), make_evidence(
            [make_instance()]), {'totalMass': 'oops'})
        self.assertEqual(report['reconciliation_status'], TARGET_INVALID)
        self.assertEqual(report['source_total_mass_raw'], 'oops')
        self.assertIsNone(report['source_total_mass_t'])

    def test_nan_infinity_negative_total_mass(self):
        for bad in ('NaN', 'Infinity', '-3.5'):
            report = reconcile_craft_mass(make_draft(), make_evidence(
                [make_instance()]), {'totalMass': bad})
            self.assertEqual(report['reconciliation_status'], TARGET_INVALID,
                             bad)
            self.assertIsNone(report['source_total_mass_t'])


class ReconciliationEstimateTests(unittest.TestCase):
    def test_include_all_sums_valid_instances(self):
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=1, base_part='Tank',
                          current_part_mass_estimate_t=2.5),
        ])
        report = reconcile_craft_mass(make_draft(part_count=2), evidence,
                                      {'totalMass': '3.5'})
        self.assertEqual(report['estimated_total_include_all_t'], 3.5)
        self.assertEqual(report['included_instance_count'], 2)
        self.assertEqual(report['complete_evidence'], True)

    def test_exclude_possible_non_flight(self):
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=1, base_part='launchClamp1',
                          current_part_mass_estimate_t=0.1,
                          possible_non_flight_part=True,
                          contains_launch_clamp=True),
        ])
        report = reconcile_craft_mass(make_draft(part_count=2), evidence,
                                      {'totalMass': '1.0'})
        self.assertEqual(report['estimated_total_include_all_t'], 1.1)
        self.assertEqual(report['estimated_total_excluding_non_flight_t'],
                         1.0)
        self.assertEqual(report['possible_non_flight_instance_count'], 1)
        self.assertEqual(report['possible_non_flight_mass_t'], 0.1)
        excluded = report['excluded_instance_evidence']
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]['base_part'], 'launchClamp1')
        self.assertIn('part_source', excluded[0])
        self.assertIn('diagnostics', excluded[0])
        self.assertIn('contains_launch_clamp', excluded[0])
        self.assertEqual(report['possible_non_flight_types'],
                         ['launchClamp1'])

    def test_evidence_incomplete_with_missing_instance(self):
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=1, base_part='Unknown',
                          current_part_mass_estimate_t=None,
                          resolution_status='resource_unresolved'),
        ])
        report = reconcile_craft_mass(make_draft(part_count=2), evidence,
                                      {'totalMass': '1.5'})
        self.assertEqual(report['reconciliation_status'],
                         EVIDENCE_INCOMPLETE)
        self.assertEqual(report['complete_evidence'], False)
        self.assertEqual(report['known_mass_subtotal_t'], 1.0)
        self.assertEqual(report['missing_mass_instance_count'], 1)
        self.assertEqual(len(report['unresolved_instance_evidence']), 1)
        self.assertIsNone(report['estimated_total_include_all_t'])
        self.assertIsNone(report['residual_include_all_t'])

    def test_missing_instance_not_treated_as_zero(self):
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=1, base_part='Unknown',
                          current_part_mass_estimate_t=None),
        ])
        report = reconcile_craft_mass(make_draft(part_count=2), evidence,
                                      {'totalMass': '1.0'})
        # would look "matched" if the missing instance were silently 0
        self.assertEqual(report['reconciliation_status'],
                         EVIDENCE_INCOMPLETE)
        self.assertNotEqual(report['reconciliation_status'],
                            MATCHED_INCLUDE_ALL)


class ReconciliationStatusTests(unittest.TestCase):
    def test_include_all_matched(self):
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=1, base_part='clamp',
                          current_part_mass_estimate_t=0.05,
                          possible_non_flight_part=True),
        ])
        report = reconcile_craft_mass(make_draft(part_count=2), evidence,
                                      {'totalMass': '1.05'})
        self.assertEqual(report['reconciliation_status'],
                         MATCHED_INCLUDE_ALL)
        self.assertAlmostEqual(report['residual_include_all_t'], 0.0)

    def test_exclude_matched(self):
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=1, base_part='clamp',
                          current_part_mass_estimate_t=0.05,
                          possible_non_flight_part=True),
        ])
        report = reconcile_craft_mass(make_draft(part_count=2), evidence,
                                      {'totalMass': '1.0'})
        self.assertEqual(report['reconciliation_status'],
                         MATCHED_EXCLUDING_NON_FLIGHT)
        self.assertAlmostEqual(report['residual_excluding_non_flight_t'], 0.0)

    def test_ambiguous_scenario(self):
        evidence = make_evidence(
            [make_instance(current_part_mass_estimate_t=1.0)])
        report = reconcile_craft_mass(make_draft(), evidence,
                                      {'totalMass': '1.0'})
        self.assertEqual(report['reconciliation_status'],
                         AMBIGUOUS_SCENARIO)

    def test_unreconciled(self):
        evidence = make_evidence(
            [make_instance(current_part_mass_estimate_t=1.0)])
        report = reconcile_craft_mass(make_draft(), evidence,
                                      {'totalMass': '1.5'})
        self.assertEqual(report['reconciliation_status'], UNRECONCILED)
        self.assertAlmostEqual(report['residual_include_all_t'], -0.5)
        self.assertAlmostEqual(report['relative_residual_include_all'],
                               -1 / 3)

    def test_zero_target_no_division(self):
        evidence = make_evidence(
            [make_instance(current_part_mass_estimate_t=0.0)])
        report = reconcile_craft_mass(make_draft(part_count=1), evidence,
                                      {'totalMass': '0'})
        self.assertEqual(report['source_total_mass_t'], 0.0)
        self.assertEqual(report['relative_residual_include_all'], None)
        self.assertEqual(report['relative_residual_excluding_non_flight'],
                         None)

    def test_residual_sign_conventions(self):
        high = reconcile_craft_mass(make_draft(), make_evidence(
            [make_instance(current_part_mass_estimate_t=2.0)]),
            {'totalMass': '1.0'})
        self.assertGreater(high['residual_include_all_t'], 0)   # +1.0
        low = reconcile_craft_mass(make_draft(), make_evidence(
            [make_instance(current_part_mass_estimate_t=0.5)]),
            {'totalMass': '1.0'})
        self.assertLess(low['residual_include_all_t'], 0)       # -0.5

    def test_nearest_scenario_not_authoritative(self):
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=1, base_part='clamp',
                          current_part_mass_estimate_t=0.05,
                          possible_non_flight_part=True),
        ])
        report = reconcile_craft_mass(make_draft(part_count=2), evidence,
                                      {'totalMass': '1.5'})
        self.assertEqual(report['reconciliation_status'], UNRECONCILED)
        self.assertEqual(report['nearest_is_authoritative'], False)
        self.assertIn('nearest_scenario',
                      [d['code'] for d in report['diagnostics']])

    def test_inputs_not_modified_and_deterministic(self):
        evidence = make_evidence([
            make_instance(current_part_mass_estimate_t=1.0),
            make_instance(craft_index=1, base_part='clamp',
                          current_part_mass_estimate_t=0.05,
                          possible_non_flight_part=True),
        ])
        draft = make_draft(part_count=2)
        draft_before = copy.deepcopy(draft)
        evidence_before = copy.deepcopy(evidence)
        first = reconcile_craft_mass(draft, evidence, {'totalMass': '1.5'})
        second = reconcile_craft_mass(draft, evidence, {'totalMass': '1.5'})
        self.assertEqual(first, second)
        json.dumps(first, ensure_ascii=False)
        self.assertEqual(draft, draft_before)
        self.assertEqual(evidence, evidence_before)

    def test_forensic_fields_present(self):
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0,
                          dynamic_mass_modules=['ModuleFuelTanks']),
            make_instance(craft_index=1, base_part='Pod',
                          current_part_mass_estimate_t=1.0),
        ])
        report = reconcile_craft_mass(make_draft('x.craft'), evidence,
                                      {'totalMass': '2.0'},
                                      loadmeta_filename='x.loadmeta')
        self.assertEqual(report['craft_filename'], 'x.craft')
        self.assertEqual(report['loadmeta_filename'], 'x.loadmeta')
        self.assertEqual(report['resolution_status_distribution'],
                         {'resolved_static': 2})
        self.assertEqual(report['dynamic_mass_modules_distribution'],
                         {'ModuleFuelTanks': 1})
        self.assertEqual(report['part_type_counts'], {'Pod': 2})
        self.assertEqual(report['resource_mass_total_t'], 0.0)
        self.assertEqual(report['static_or_modified_dry_mass_total_t'], 2.0)
        self.assertEqual(report['largest_mass_instance']['base_part'], 'Pod')


class PreviewReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    CRAFT = ('ship = Mini\n'
             'PART\n{\n\tpart = Pod_1\n\tistg = 0\n}\n').encode('utf-8')

    def _index(self):
        from tests.test_ksp_mass_evidence import make_index
        return make_index(part_specs=[
            {'name': 'Pod', 'parent_url': '/S/Pod.cfg',
             'mass_raw': '0.5', 'mass_t': 0.5}])

    def test_preview_without_loadmeta_succeeds(self):
        item = import_service.preview_craft('mini.craft', self.CRAFT,
                                            ksp_index=self._index())
        self.assertEqual(item['status'], 'needs_review')
        self.assertEqual(item['action'], 'review')
        rec = item['config']['mass_reconciliation']
        self.assertEqual(rec['reconciliation_status'], 'target_missing')

    def test_preview_with_loadmeta_attaches_reconciliation(self):
        loadmeta = {'totalMass': '0.5', 'partCount': '1'}
        item = import_service.preview_craft('mini.craft', self.CRAFT,
                                            loadmeta=loadmeta,
                                            ksp_index=self._index())
        rec = item['config']['mass_reconciliation']
        self.assertEqual(rec['source_total_mass_t'], 0.5)
        self.assertEqual(rec['estimated_total_include_all_t'], 0.5)
        self.assertEqual(rec['reconciliation_status'],
                         'ambiguous_scenario')

    def test_unreconciled_generates_single_warning(self):
        loadmeta = {'totalMass': '1.0', 'partCount': '1'}
        item = import_service.preview_craft('mini.craft', self.CRAFT,
                                            loadmeta=loadmeta,
                                            ksp_index=self._index())
        warnings = [w for w in item['structured_warnings']
                    if w['code'] == 'mass_reconciliation_unreconciled']
        self.assertEqual(len(warnings), 1)
        details = warnings[0]['details']
        self.assertIn('source_total_mass_t', details)
        self.assertIn('estimated_total_include_all_t', details)
        self.assertIn('residual_include_all_t', details)
        self.assertIn('estimated_total_excluding_non_flight_t', details)
        self.assertIn('residual_excluding_non_flight_t', details)

    def test_stage_masses_stay_zero(self):
        loadmeta = {'totalMass': '1.0', 'partCount': '1'}
        item = import_service.preview_craft('mini.craft', self.CRAFT,
                                            loadmeta=loadmeta,
                                            ksp_index=self._index())
        for stage in item['config']['stages']:
            self.assertEqual(stage['wet_mass_t'], 0.0)
            self.assertEqual(stage['dry_mass_t'], 0.0)
        self.assertTrue(item['config']['requires_mass_confirmation'])

    def test_preview_without_index_still_succeeds(self):
        with mock.patch('server.import_service.get_auto_ksp_index',
                        return_value=None):
            item = import_service.preview_craft('mini.craft', self.CRAFT)
        self.assertEqual(item['status'], 'needs_review')
        self.assertEqual(item['config']['mass_reconciliation']
                         ['reconciliation_status'], 'target_missing')


class ReconciliationReworkTests(unittest.TestCase):
    """6B.3B-R: complete_evidence means unique match + full resolution."""

    def test_resource_unresolved_with_number_is_incomplete(self):
        evidence = make_evidence([make_instance(
            resolution_status='resource_unresolved',
            current_part_mass_estimate_t=1.0)])
        report = reconcile_craft_mass(make_draft(), evidence,
                                      {'totalMass': '1.0'})
        self.assertEqual(report['complete_evidence'], False)
        self.assertEqual(report['reconciliation_status'],
                         EVIDENCE_INCOMPLETE)
        self.assertIsNone(report['estimated_total_include_all_t'])
        self.assertEqual(report['invalid_instance_count'], 1)
        reasons = report['unresolved_instance_evidence'][0]['invalid_reasons']
        self.assertIn('unresolved_mass_status', reasons)

    def test_invalid_mass_with_number_is_incomplete(self):
        evidence = make_evidence([make_instance(
            resolution_status='invalid_mass',
            current_part_mass_estimate_t=1.0)])
        report = reconcile_craft_mass(make_draft(), evidence,
                                      {'totalMass': '1.0'})
        self.assertEqual(report['complete_evidence'], False)
        self.assertEqual(report['reconciliation_status'],
                         EVIDENCE_INCOMPLETE)

    def test_ambiguous_match_with_number_is_incomplete(self):
        evidence = make_evidence([make_instance(
            match_status='ambiguous',
            resolution_status='resolved_static',
            current_part_mass_estimate_t=1.0)])
        report = reconcile_craft_mass(make_draft(), evidence,
                                      {'totalMass': '1.0'})
        self.assertEqual(report['complete_evidence'], False)
        self.assertIn('invalid_match_status',
                      report['unresolved_instance_evidence'][0]
                      ['invalid_reasons'])

    def test_missing_and_index_unavailable_match_unusable(self):
        for match_status in ('missing', 'index_unavailable', 'invalid_part'):
            evidence = make_evidence([make_instance(
                match_status=match_status,
                resolution_status='resolved_static',
                current_part_mass_estimate_t=1.0)])
            report = reconcile_craft_mass(make_draft(), evidence,
                                          {'totalMass': '1.0'})
            self.assertEqual(report['complete_evidence'], False, match_status)

    def test_normalized_unique_with_estimated_modifier_usable(self):
        evidence = make_evidence([make_instance(
            match_status='normalized_unique',
            resolution_status='estimated_with_modifier',
            current_part_mass_estimate_t=1.0)])
        report = reconcile_craft_mass(make_draft(), evidence,
                                      {'totalMass': '1.0'})
        self.assertEqual(report['complete_evidence'], True)
        self.assertEqual(report['reconciliation_status'],
                         AMBIGUOUS_SCENARIO)

    def test_empty_evidence_incomplete(self):
        report = reconcile_craft_mass(make_draft(), make_evidence([]),
                                      {'totalMass': '0'})
        self.assertEqual(report['complete_evidence'], False)
        self.assertEqual(report['reconciliation_status'],
                         EVIDENCE_INCOMPLETE)
        self.assertEqual(report['expected_instance_count'], 1)
        self.assertEqual(report['evidence_instance_count'], 0)

    def test_evidence_fewer_than_part_count(self):
        evidence = make_evidence([make_instance(craft_index=0,
                                                current_part_mass_estimate_t=1.0)])
        report = reconcile_craft_mass(make_draft(part_count=2), evidence,
                                      {'totalMass': '2.0'})
        self.assertEqual(report['complete_evidence'], False)
        self.assertIn('missing_craft_instance',
                      [d['code'] for d in report['diagnostics']] +
                      [r for inst in report['unresolved_instance_evidence']
                       for r in inst['invalid_reasons']])

    def test_evidence_more_than_part_count(self):
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=1, current_part_mass_estimate_t=1.0),
        ])
        report = reconcile_craft_mass(make_draft(part_count=1), evidence,
                                      {'totalMass': '2.0'})
        self.assertEqual(report['complete_evidence'], False)
        self.assertEqual(report['reconciliation_status'],
                         EVIDENCE_INCOMPLETE)

    def test_duplicate_craft_index(self):
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
        ])
        report = reconcile_craft_mass(make_draft(part_count=2), evidence,
                                      {'totalMass': '2.0'})
        self.assertEqual(report['complete_evidence'], False)
        self.assertIn('duplicate_craft_index',
                      [r for inst in report['unresolved_instance_evidence']
                       for r in inst['invalid_reasons']])

    def test_craft_parts_and_source_part_count_mismatch(self):
        draft = make_draft(part_count=2)
        draft['parts'] = draft['parts'] + [dict(draft['parts'][0])]
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=1, current_part_mass_estimate_t=1.0),
        ])
        report = reconcile_craft_mass(draft, evidence, {'totalMass': '2.0'})
        self.assertEqual(report['complete_evidence'], False)
        self.assertIn('input_structure_mismatch',
                      [d['code'] for d in report['diagnostics']])

    def test_unexpected_evidence_index(self):
        evidence = make_evidence([make_instance(craft_index=5,
                                                current_part_mass_estimate_t=1.0)])
        report = reconcile_craft_mass(make_draft(part_count=1), evidence,
                                      {'totalMass': '1.0'})
        self.assertEqual(report['complete_evidence'], False)
        records = report['unresolved_instance_evidence']
        # craft order first (synthetic missing), unexpected evidence last
        self.assertTrue(records[0]['synthetic'])
        self.assertEqual(records[0]['craft_index'], 0)
        unexpected = next(r for r in records if r['craft_index'] == 5)
        self.assertIn('unexpected_evidence_instance',
                      unexpected['invalid_reasons'])
        self.assertFalse(unexpected['synthetic'])
        self.assertEqual(report['unexpected_evidence_instance_count'], 1)
        self.assertEqual(report['missing_mass_instance_count'], 1)

    def test_missing_craft_index_in_evidence(self):
        evidence = make_evidence([make_instance(craft_index=0,
                                                current_part_mass_estimate_t=1.0)])
        report = reconcile_craft_mass(make_draft(part_count=2), evidence,
                                      {'totalMass': '2.0'})
        self.assertEqual(report['complete_evidence'], False)
        self.assertIn('missing_craft_instance',
                      [r for inst in report['unresolved_instance_evidence']
                       for r in inst['invalid_reasons']]
                      + [d['code'] for d in report['diagnostics']])

    def test_total_mass_boolean_is_invalid(self):
        report = reconcile_craft_mass(make_draft(), make_evidence(
            [make_instance()]), {'totalMass': True})
        self.assertEqual(report['reconciliation_status'], TARGET_INVALID)
        self.assertIsNone(report['source_total_mass_t'])

    def test_boolean_mass_value_is_invalid(self):
        evidence = make_evidence([make_instance(
            current_part_mass_estimate_t=True)])
        report = reconcile_craft_mass(make_draft(), evidence,
                                      {'totalMass': '1.0'})
        self.assertEqual(report['complete_evidence'], False)
        self.assertEqual(report['reconciliation_status'],
                         EVIDENCE_INCOMPLETE)
        self.assertIn('invalid_mass_value',
                      report['unresolved_instance_evidence'][0]
                      ['invalid_reasons'])

    def test_zero_mass_still_usable(self):
        evidence = make_evidence(
            [make_instance(current_part_mass_estimate_t=0.0)])
        report = reconcile_craft_mass(make_draft(), evidence,
                                      {'totalMass': '0'})
        self.assertEqual(report['complete_evidence'], True)
        self.assertEqual(report['valid_instance_count'], 1)
        self.assertEqual(report['reconciliation_status'],
                         AMBIGUOUS_SCENARIO)

    def test_invalid_reasons_stable_and_serializable(self):
        evidence = make_evidence([
            make_instance(craft_index=0, match_status='ambiguous',
                          current_part_mass_estimate_t='1.0'),
        ])
        report = reconcile_craft_mass(make_draft(), evidence,
                                      {'totalMass': '1.0'})
        json.dumps(report, ensure_ascii=False)
        reasons = report['unresolved_instance_evidence'][0]['invalid_reasons']
        self.assertEqual(sorted(reasons),
                         sorted(set(reasons)))

    def test_invalid_non_flight_still_counted(self):
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=1, base_part='clamp',
                          match_status='ambiguous',
                          current_part_mass_estimate_t=0.1,
                          possible_non_flight_part=True),
        ])
        report = reconcile_craft_mass(make_draft(part_count=2), evidence,
                                      {'totalMass': '1.0'})
        self.assertEqual(report['possible_non_flight_instance_count'], 1)
        self.assertEqual(report['known_possible_non_flight_mass_t'], 0.0)
        self.assertEqual(report['possible_non_flight_mass_complete'], False)
        self.assertIsNone(report['possible_non_flight_mass_t'])
        self.assertEqual(len(report['excluded_instance_evidence']), 1)

    def test_target_missing_still_reports_evidence_counts(self):
        evidence = make_evidence([
            make_instance(craft_index=0, match_status='ambiguous',
                          current_part_mass_estimate_t=1.0)])
        report = reconcile_craft_mass(make_draft(), evidence, None)
        self.assertEqual(report['reconciliation_status'], TARGET_MISSING)
        self.assertEqual(report['complete_evidence'], False)
        self.assertEqual(report['expected_instance_count'], 1)
        self.assertEqual(report['evidence_instance_count'], 1)
        self.assertEqual(report['invalid_instance_count'], 1)
        self.assertEqual(len(report['unresolved_instance_evidence']), 1)


class ReconciliationRework2Tests(unittest.TestCase):
    """6B.3B-R2: one-to-one craft_index mapping + synthetic missing records."""

    def test_missing_evidence_generates_synthetic_record(self):
        draft = make_draft(part_count=2)
        draft['parts'][1] = {
            'index': 1, 'part': 'Engine_1', 'base_part': 'Engine',
            'instance_id': '1', 'persistent_id': 'engine-p-1',
        }
        evidence = make_evidence([make_instance(craft_index=0,
                                                current_part_mass_estimate_t=1.0)])
        report = reconcile_craft_mass(draft, evidence, {'totalMass': '2.0'})
        self.assertEqual(report['missing_mass_instance_count'], 1)
        self.assertEqual(report['missing_craft_instance_count'], 1)
        records = report['unresolved_instance_evidence']
        self.assertEqual(len(records), 1)
        missing = records[0]
        self.assertTrue(missing['synthetic'])
        self.assertEqual(missing['craft_index'], 1)
        self.assertEqual(missing['part'], 'Engine_1')
        self.assertEqual(missing['base_part'], 'Engine')
        self.assertEqual(missing['persistent_id'], 'engine-p-1')
        self.assertEqual(missing['invalid_reasons'],
                         ['missing_craft_instance'])
        self.assertIsNone(missing['current_part_mass_estimate_t'])
        self.assertIsNone(missing['part_source'])
        self.assertEqual(report['unresolved_instance_count'], 1)

    def test_empty_evidence_generates_missing_for_every_part(self):
        report = reconcile_craft_mass(make_draft(part_count=3),
                                      make_evidence([]), {'totalMass': '0'})
        self.assertEqual(report['missing_craft_instance_count'], 3)
        self.assertEqual(report['missing_mass_instance_count'], 3)
        self.assertEqual(len(report['unresolved_instance_evidence']), 3)
        self.assertTrue(all(r['synthetic'] for r in
                            report['unresolved_instance_evidence']))
        self.assertEqual(
            [r['craft_index'] for r in report['unresolved_instance_evidence']],
            [0, 1, 2])

    def test_unique_invalid_evidence_not_synthetic(self):
        evidence = make_evidence([make_instance(
            craft_index=0, match_status='ambiguous',
            current_part_mass_estimate_t=1.0)])
        report = reconcile_craft_mass(make_draft(part_count=1), evidence,
                                      {'totalMass': '1.0'})
        self.assertEqual(report['missing_mass_instance_count'], 1)
        self.assertEqual(report['invalid_evidence_instance_count'], 1)
        record = report['unresolved_instance_evidence'][0]
        self.assertFalse(record['synthetic'])
        self.assertIn('invalid_match_status', record['invalid_reasons'])

    def test_duplicate_evidence_counts_per_records(self):
        draft = make_draft(part_count=2)
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=0, base_part='Dup',
                          current_part_mass_estimate_t=2.0),
            make_instance(craft_index=1, current_part_mass_estimate_t=1.0),
        ])
        report = reconcile_craft_mass(draft, evidence, {'totalMass': '2.0'})
        self.assertEqual(report['expected_instance_count'], 2)
        self.assertEqual(report['evidence_instance_count'], 3)
        self.assertEqual(report['valid_instance_count'], 1)
        self.assertEqual(report['duplicate_evidence_instance_count'], 2)
        self.assertEqual(report['missing_mass_instance_count'], 1)
        self.assertFalse(report['complete_evidence'])
        self.assertEqual(report['known_mass_subtotal_t'], 1.0)

    def test_valid_plus_unexpected_evidence(self):
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=99, base_part='Ghost',
                          current_part_mass_estimate_t=0.5),
        ])
        report = reconcile_craft_mass(make_draft(part_count=1), evidence,
                                      {'totalMass': '1.0'})
        self.assertEqual(report['valid_instance_count'], 1)
        self.assertEqual(report['missing_mass_instance_count'], 0)
        self.assertEqual(report['unexpected_evidence_instance_count'], 1)
        self.assertFalse(report['complete_evidence'])
        self.assertIsNone(report['estimated_total_include_all_t'])
        self.assertIsNone(report['residual_include_all_t'])
        ghost = next(r for r in report['unresolved_instance_evidence']
                     if r['craft_index'] == 99)
        self.assertIn('unexpected_evidence_instance',
                      ghost['invalid_reasons'])
        self.assertEqual(report['known_mass_subtotal_t'], 1.0)

    def test_all_four_issue_kinds_together(self):
        draft = make_draft(part_count=4)
        draft['parts'][1] = dict(draft['parts'][1], base_part='Missing')
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=1, match_status='ambiguous',
                          current_part_mass_estimate_t=1.0),
            make_instance(craft_index=2, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=2, base_part='Dup',
                          current_part_mass_estimate_t=1.0),
            make_instance(craft_index=77, base_part='Ghost',
                          current_part_mass_estimate_t=1.0),
        ])
        report = reconcile_craft_mass(draft, evidence, {'totalMass': '4.0'})
        self.assertEqual(report['valid_instance_count'], 1)        # index 0
        self.assertEqual(report['invalid_evidence_instance_count'], 1)
        self.assertEqual(report['missing_craft_instance_count'], 1)  # index 3
        self.assertEqual(report['duplicate_evidence_instance_count'], 2)
        self.assertEqual(report['unexpected_evidence_instance_count'], 1)
        self.assertEqual(report['missing_mass_instance_count'], 3)
        indexes = [r['craft_index'] for r in
                   report['unresolved_instance_evidence']]
        self.assertEqual(indexes[-1], 77)
        self.assertEqual(report['unresolved_instance_count'],
                         len(report['unresolved_instance_evidence']))

    def test_known_subtotal_excludes_duplicate_and_unexpected(self):
        draft = make_draft(part_count=2)
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=0, base_part='Dup',
                          current_part_mass_estimate_t=9.0),
            make_instance(craft_index=1, current_part_mass_estimate_t=2.0),
            make_instance(craft_index=55, current_part_mass_estimate_t=9.0),
        ])
        report = reconcile_craft_mass(draft, evidence, {'totalMass': '3.0'})
        # only index 1 (unique valid evidence) contributes
        self.assertEqual(report['known_mass_subtotal_t'], 2.0)

    def test_unresolved_count_matches_list_length(self):
        draft = make_draft(part_count=2)
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
        ])
        report = reconcile_craft_mass(draft, evidence, {'totalMass': '2.0'})
        self.assertEqual(report['unresolved_instance_count'],
                         len(report['unresolved_instance_evidence']))

    def test_incomplete_diagnostic_reasons_not_empty(self):
        draft = make_draft(part_count=2)
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
        ])
        report = reconcile_craft_mass(draft, evidence, {'totalMass': '2.0'})
        diagnostic = next(d for d in report['diagnostics']
                          if d['code'] == 'evidence_incomplete')
        marker = '原因 '
        self.assertIn(marker, diagnostic['message'])
        after = diagnostic['message'].split(marker, 1)[1]
        self.assertNotEqual(after.strip(), '')
        self.assertIn('missing_craft_instance=1', after)

    def test_inputs_not_modified_and_deterministic(self):
        import copy as copy_module
        draft = make_draft(part_count=2)
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
        ])
        draft_before = copy_module.deepcopy(draft)
        evidence_before = copy_module.deepcopy(evidence)
        first = reconcile_craft_mass(draft, evidence, {'totalMass': '2.0'})
        second = reconcile_craft_mass(draft, evidence, {'totalMass': '2.0'})
        self.assertEqual(first, second)
        json.dumps(first, ensure_ascii=False)
        self.assertEqual(draft, draft_before)
        self.assertEqual(evidence, evidence_before)

    def test_whitelist_tests_still_pass(self):
        evidence = make_evidence([make_instance(
            craft_index=0, current_part_mass_estimate_t=True)])
        report = reconcile_craft_mass(make_draft(part_count=1), evidence,
                                      {'totalMass': '1.0'})
        self.assertEqual(report['invalid_evidence_instance_count'], 1)
        self.assertIn('invalid_mass_value',
                      report['unresolved_instance_evidence'][0]
                      ['invalid_reasons'])

    def test_invalid_instance_count_is_alias_of_invalid_evidence(self):
        draft = make_draft(part_count=3)
        evidence = make_evidence([
            make_instance(craft_index=0, current_part_mass_estimate_t=1.0),
            make_instance(craft_index=1, match_status='ambiguous',
                          current_part_mass_estimate_t=1.0),
        ])
        report = reconcile_craft_mass(draft, evidence, {'totalMass': '3.0'})
        self.assertEqual(report['invalid_instance_count'],
                         report['invalid_evidence_instance_count'])
        self.assertEqual(report['invalid_instance_count'], 1)
        # missing_mass includes the invalid evidence AND the missing part
        self.assertEqual(report['missing_mass_instance_count'], 2)
        self.assertNotEqual(report['invalid_instance_count'],
                            report['missing_mass_instance_count'])


class PreviewReconciliationReworkTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    CRAFT = ('ship = Mini\n'
             'PART\n{\n\tpart = Pod_1\n\tistg = 0\n}\n').encode('utf-8')

    def _index(self):
        from tests.test_ksp_mass_evidence import make_index
        return make_index(part_specs=[
            {'name': 'Pod', 'parent_url': '/S/Pod.cfg',
             'mass_raw': '0.5', 'mass_t': 0.5}])

    def test_reconciliation_exception_gives_failed_warning(self):
        with mock.patch('server.import_service.reconcile_craft_mass',
                        side_effect=RuntimeError('boom')):
            item = import_service.preview_craft(
                'mini.craft', self.CRAFT, loadmeta={'totalMass': '1.0'},
                ksp_index=self._index())
        self.assertEqual(item['status'], 'needs_review')
        self.assertIsNone(item['config']['mass_reconciliation'])
        codes = [w['code'] for w in item['structured_warnings']]
        self.assertIn('mass_reconciliation_failed', codes)
        self.assertNotIn('mass_reconciliation_unreconciled', codes)

    def test_target_invalid_uses_separate_warning_code(self):
        item = import_service.preview_craft(
            'mini.craft', self.CRAFT, loadmeta={'totalMass': 'oops'},
            ksp_index=self._index())
        codes = [w['code'] for w in item['structured_warnings']]
        self.assertIn('mass_reconciliation_target_invalid', codes)
        self.assertNotIn('mass_reconciliation_unreconciled', codes)
        self.assertTrue(item['config']['requires_mass_confirmation'])


if __name__ == '__main__':
    unittest.main()
