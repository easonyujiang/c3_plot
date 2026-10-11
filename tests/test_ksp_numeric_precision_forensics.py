import json
import math
import unittest
from pathlib import Path

from server.ksp_numeric_precision_forensics import (
    INSUFFICIENT_EVIDENCE,
    MODELS,
    build_ordered_masses,
    classify_float32_input,
    evaluate_five_models,
    fleet_model_comparison,
    sum_float32_in_order,
    to_float32,
)

REAL_KSP = r'D:\SteamLibrary\steamapps\common\Kerbal Space Program'
REAL_CACHE = Path(REAL_KSP) / 'GameData' / 'ModuleManager.ConfigCache'


class Float32PrimitiveTests(unittest.TestCase):
    def test_known_float32_values(self):
        # exact binary32-representable values
        self.assertEqual(to_float32(1.0), 1.0)
        self.assertEqual(to_float32(0.5), 0.5)
        self.assertEqual(to_float32(0.1), struct_f32(0.1))
        # 0.1 in binary32 is not 0.1 in double
        self.assertNotEqual(to_float32(0.1), 0.1)
        self.assertEqual(to_float32(0.0), 0.0)

    def test_rejects_invalid(self):
        for bad in (True, False, float('nan'), float('inf'),
                    float('-inf'), 'oops', None):
            self.assertIsNone(to_float32(bad), bad)

    def test_rejects_negative(self):
        self.assertIsNone(to_float32(-1.0))
        self.assertIsNone(sum_float32_in_order([1.0, -2.0]))

    def test_stepwise_vs_final_cast_not_equal(self):
        values = [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1,
                  0.1, 0.1, 0.1, 0.1, 0.1]
        stepwise = sum_float32_in_order(values)
        final_cast = to_float32(sum(values))
        self.assertIsNotNone(stepwise)
        self.assertNotEqual(stepwise, final_cast)

    def test_order_sensitive(self):
        values = [0.1, 1e-6, 5.0, 1e-6, 0.2, 3.0]
        forward = sum_float32_in_order(values)
        backward = sum_float32_in_order(list(reversed(values)))
        self.assertIsNotNone(forward)
        self.assertIsNotNone(backward)
        self.assertNotEqual(forward, backward)

    def test_invalid_item_rejects_whole_sum(self):
        self.assertIsNone(sum_float32_in_order([1.0, 'bad', 2.0]))
        self.assertIsNone(sum_float32_in_order([1.0, float('nan')]))


class OrderedMassesTests(unittest.TestCase):
    def _draft(self, parts):
        return {'source': {'part_count': len(parts)}, 'parts': parts,
                'stages': [], 'warnings': [], 'structured_warnings': []}

    def _part(self, index, base='Pod'):
        return {'index': index, 'part': 'Pod_%d' % index,
                'base_part': base, 'persistent_id': 'p%d' % index}

    def _evidence(self, instances):
        return {'instances': instances, 'index_fingerprint': {}}

    def _instance(self, index, mass=1.0, base='Pod'):
        return {'craft_index': index, 'part': 'Pod_%d' % index,
                'base_part': base, 'persistent_id': 'p%d' % index,
                'current_part_mass_estimate_t': mass}

    def test_craft_order_strictly_preserved(self):
        parts = [self._part(5), self._part(2), self._part(9)]
        instances = [self._instance(5, 1.0), self._instance(2, 2.0),
                     self._instance(9, 3.0)]
        ordered = build_ordered_masses(self._draft(parts),
                                       self._evidence(instances))
        self.assertTrue(ordered['complete'])
        self.assertEqual(ordered['used_craft_indexes'], [5, 2, 9])
        self.assertEqual(ordered['ordered_masses'], [1.0, 2.0, 3.0])

    def test_missing_evidence_incomplete(self):
        parts = [self._part(0), self._part(1)]
        instances = [self._instance(0)]
        ordered = build_ordered_masses(self._draft(parts),
                                       self._evidence(instances))
        self.assertFalse(ordered['complete'])
        self.assertEqual(ordered['valid_mass_count'], 1)
        codes = [d['code'] for d in ordered['diagnostics']]
        self.assertIn('missing_evidence', codes)

    def test_duplicate_evidence_incomplete(self):
        parts = [self._part(0)]
        instances = [self._instance(0), self._instance(0)]
        ordered = build_ordered_masses(self._draft(parts),
                                       self._evidence(instances))
        self.assertFalse(ordered['complete'])
        self.assertIn('duplicate_evidence_index',
                      [d['code'] for d in ordered['diagnostics']])

    def test_unexpected_evidence_incomplete(self):
        parts = [self._part(0)]
        instances = [self._instance(0), self._instance(99)]
        ordered = build_ordered_masses(self._draft(parts),
                                       self._evidence(instances))
        self.assertFalse(ordered['complete'])
        self.assertIn('unexpected_evidence_index',
                      [d['code'] for d in ordered['diagnostics']])

    def test_identity_mismatch_incomplete(self):
        parts = [self._part(0)]
        instances = [self._instance(0, base='Other')]
        ordered = build_ordered_masses(self._draft(parts),
                                       self._evidence(instances))
        self.assertFalse(ordered['complete'])
        self.assertIn('evidence_identity_mismatch',
                      [d['code'] for d in ordered['diagnostics']])

    def test_invalid_mass_incomplete(self):
        for bad in (True, float('nan'), -0.5, 'x'):
            parts = [self._part(0)]
            instances = [self._instance(0, mass=bad)]
            ordered = build_ordered_masses(self._draft(parts),
                                           self._evidence(instances))
            self.assertFalse(ordered['complete'], bad)
            self.assertIn('invalid_mass_value',
                          [d['code'] for d in ordered['diagnostics']])


class FiveModelTests(unittest.TestCase):
    def _draft_and_evidence(self, masses):
        parts = []
        instances = []
        for index, mass in enumerate(masses):
            parts.append({'index': index, 'part': 'P_%d' % index,
                          'base_part': 'P',
                          'persistent_id': 'p%d' % index})
            instances.append({
                'craft_index': index, 'part': 'P_%d' % index,
                'base_part': 'P', 'persistent_id': 'p%d' % index,
                'current_part_mass_estimate_t': mass})
        draft = {'source': {'filename': 't.craft',
                            'part_count': len(parts)}, 'parts': parts,
                 'stages': [], 'warnings': [], 'structured_warnings': []}
        evidence = {'instances': instances, 'index_fingerprint': {}}
        return draft, evidence

    def test_five_models_evaluated(self):
        draft, evidence = self._draft_and_evidence(
            [0.1] * 1000 + [0.026389378290154267, 0.05])
        report = evaluate_five_models(
            draft, evidence,
            reconciliation={'source_total_mass_t': 100.0},
            pdc_diff_total=0.0)
        self.assertTrue(report['complete'])
        for model in MODELS:
            entry = report['models'][model]
            self.assertTrue(entry['complete'], model)
            self.assertIsNotNone(entry['estimated_total_t'], model)
            self.assertIsNotNone(entry['residual_to_loadmeta_t'], model)
            self.assertIsNotNone(entry['residual_after_pdc_t'], model)
            self.assertIsNotNone(entry['delta_vs_double_order_t'], model)
        self.assertEqual(
            report['models']['double_order']['delta_vs_double_order_t'], 0.0)
        # stepwise float32 differs from final cast
        self.assertNotEqual(
            report['models']['float32_craft_order']['estimated_total_t'],
            report['models']['double_sum_then_float32']
            ['estimated_total_t'])

    def test_incomplete_model_returns_null_totals(self):
        draft, evidence = self._draft_and_evidence([1.0, 2.0])
        evidence['instances'] = evidence['instances'][:1]
        report = evaluate_five_models(
            draft, evidence, reconciliation={'source_total_mass_t': 3.0})
        self.assertFalse(report['complete'])
        for model in MODELS:
            entry = report['models'][model]
            self.assertFalse(entry['complete'])
            self.assertIsNone(entry['estimated_total_t'])
            self.assertIsNone(entry['residual_after_pdc_t'])

    def test_invalid_target_keeps_residuals_null(self):
        draft, evidence = self._draft_and_evidence([1.0])
        report = evaluate_five_models(
            draft, evidence, reconciliation={'source_total_mass_t': None})
        self.assertTrue(report['complete'])
        for model in MODELS:
            self.assertIsNone(report['models'][model]
                              ['residual_to_loadmeta_t'])
            self.assertIsNone(report['models'][model]
                              ['residual_after_pdc_t'])

    def test_empty_fleet_no_support(self):
        comparison = fleet_model_comparison([])
        for model in MODELS:
            stats = comparison['models'][model]
            self.assertEqual(stats['valid_sample_count'], 0)
            self.assertFalse(stats['all_within_tolerance'])
            self.assertIsNone(stats['max_abs_error_t'])

    def test_mae_rmse_max_computation(self):
        reports = []
        for name, after in (('a', 1e-5), ('b', -3e-5), ('c', 5e-5)):
            reports.append({
                'craft_filename': name,
                'parachute_single_count': 0,
                'complete': True,
                'diagnostics': [],
                'models': {model: {
                    'complete': True,
                    'residual_after_pdc_t': after,
                    'diagnostics': [],
                } for model in MODELS},
            })
        comparison = fleet_model_comparison(reports)
        for model in MODELS:
            stats = comparison['models'][model]
            self.assertEqual(stats['valid_sample_count'], 3)
            self.assertEqual(stats['max_abs_error_t'], 5e-5)
            self.assertAlmostEqual(stats['mae_t'], 3e-5)
            self.assertAlmostEqual(stats['rmse_t'],
                                   math.sqrt((1e-10 + 9e-10 + 25e-10) / 3))

    def test_deterministic_json(self):
        draft, evidence = self._draft_and_evidence([0.1] * 5)
        first = evaluate_five_models(
            draft, evidence, reconciliation={'source_total_mass_t': 0.5})
        second = evaluate_five_models(
            draft, evidence, reconciliation={'source_total_mass_t': 0.5})
        self.assertEqual(first, second)
        json.dumps(first, ensure_ascii=False)


@unittest.skipUnless(REAL_CACHE.is_file(),
                     'real KSP install not present on this machine')
class RealFleetNumericSmokeTests(unittest.TestCase):
    """Five-model comparison over the real 20 crafts."""

    def test_real_fleet_models(self):
        from server.import_service import _loadmeta_metadata
        from server.ksp_craft_mass_reconciliation import (
            reconcile_craft_mass)
        from server.ksp_install_index import build_ksp_index
        from server.ksp_mass_evidence import extract_part_mass_evidence
        from server.ksp_mass_forensics import analyze_vehicle_forensics
        from server.ksp_part_matcher import match_craft_parts
        from server.ksp_runtime_fingerprints import ksp_runtime_provenance
        from server.vab_parser import parse_craft
        index = build_ksp_index(REAL_KSP)
        provenance = ksp_runtime_provenance(REAL_KSP)
        reports = []
        for craft_path in sorted(Path('VAB').glob('*.craft')):
            if craft_path.name.endswith('.original'):
                continue
            draft = parse_craft(craft_path.read_text(encoding='utf-8-sig'),
                                craft_path.name)
            evidence = extract_part_mass_evidence(
                draft, index, match_craft_parts(draft, index),
                provenance=provenance)
            loadmeta = None
            lm = craft_path.with_suffix('.loadmeta')
            if lm.is_file():
                loadmeta = _loadmeta_metadata(
                    lm.read_text(encoding='utf-8-sig', errors='replace'))
            reconciliation = reconcile_craft_mass(draft, evidence, loadmeta)
            forensics = analyze_vehicle_forensics(
                draft, evidence, reconciliation, ksp_index=index)
            numeric = evaluate_five_models(
                draft, evidence, reconciliation,
                pdc_diff_total=forensics.get('pdc_candidate_diff_total_t'))
            numeric['craft_filename'] = craft_path.name
            numeric['parachute_single_count'] = \
                forensics['parachute_single_count']
            reports.append(numeric)

        comparison = fleet_model_comparison(reports)
        craft = comparison['models']['float32_craft_order']
        self.assertEqual(craft['valid_sample_count'], 17)
        self.assertEqual(craft['within_tolerance_count'], 17)
        self.assertAlmostEqual(craft['max_abs_error_t'], 2.3777e-5,
                               delta=1e-7)
        # float32_craft_order has the lowest MAE and RMSE of all five
        mae = {model: comparison['models'][model]['mae_t']
               for model in MODELS}
        rmse = {model: comparison['models'][model]['rmse_t']
                for model in MODELS}
        self.assertLess(mae['float32_craft_order'],
                        mae['double_order'])
        self.assertLess(rmse['float32_craft_order'],
                        rmse['double_order'])
        # every other model is NOT 17/17
        for model in MODELS:
            if model == 'float32_craft_order':
                continue
            self.assertLess(comparison['models'][model]
                            ['within_tolerance_count'], 17, model)

        # Neutrons II 354S enters the tolerance under float32_craft_order
        for report in reports:
            if report['craft_filename'] != 'Neutrons II 354S.craft':
                continue
            after = report['models']['float32_craft_order'][
                'residual_after_pdc_t']
            self.assertLessEqual(abs(after), 5e-5)
            self.assertAlmostEqual(after, 2.3777e-5, delta=1e-6)
        # special vehicles now fail closed: their parachutes are Automatic
        # (out of the authorized scope, 6B.3C.4A) so the production
        # evidence chain cannot estimate them — never a fake 0.075 band
        for report in reports:
            if report.get('parachute_single_count', 0) == 0:
                continue
            model = report['models']['float32_craft_order']
            self.assertFalse(model['complete'])
            self.assertIsNone(model['residual_after_pdc_t'])


def struct_f32(value):
    import struct
    return struct.unpack('<f', struct.pack('<f', value))[0]


class FleetContractReworkTests(unittest.TestCase):
    """6B.3C.2A-R: sample completeness, overflow, tolerance, conclusion."""

    def _vehicle(self, name, after=None, complete=True, chutes=0):
        return {
            'craft_filename': name,
            'parachute_single_count': chutes,
            'complete': complete,
            'diagnostics': [{'code': 'empty_craft', 'message': 'x'}]
            if not complete else [],
            'models': {model: {
                'complete': complete and after is not None,
                'residual_after_pdc_t': after,
                'diagnostics': [{'code': 'model_incomplete',
                                 'message': 'x'}] if after is None else [],
            } for model in MODELS},
        }

    def test_missing_sample_never_dropped_from_denominator(self):
        reports = [self._vehicle(f'v{i}', 1e-6) for i in range(16)]
        reports.append(self._vehicle('v16', None, complete=False))
        comparison = fleet_model_comparison(reports)
        for model in MODELS:
            stats = comparison['models'][model]
            self.assertEqual(stats['expected_sample_count'], 17, model)
            self.assertEqual(stats['valid_sample_count'], 16, model)
            self.assertEqual(stats['incomplete_sample_count'], 1, model)
            self.assertFalse(stats['all_samples_complete'], model)
            self.assertFalse(stats['all_within_tolerance'], model)
            self.assertEqual(stats['incomplete_vehicles'][0]['craft'],
                             'v16')
            self.assertIn('reason', stats['incomplete_vehicles'][0])
        self.assertEqual(comparison['conclusion']['conclusion_strength'],
                         INSUFFICIENT_EVIDENCE)
        self.assertIsNone(comparison['conclusion']['preferred_candidate'])

    def test_empty_craft_all_models_incomplete(self):
        draft = {'source': {'part_count': 0}, 'parts': [], 'stages': []}
        evidence = {'instances': [], 'index_fingerprint': {}}
        report = evaluate_five_models(
            draft, evidence, {'source_total_mass_t': 0.0})
        self.assertFalse(report['complete'])
        self.assertEqual(report['input_part_count'], 0)
        self.assertEqual(report['valid_mass_count'], 0)
        self.assertIn('empty_craft',
                      [d['code'] for d in report['diagnostics']])
        for model in MODELS:
            self.assertFalse(report['models'][model]['complete'])
            self.assertIsNone(report['models'][model]['estimated_total_t'])
            self.assertIsNone(report['models'][model]['residual_after_pdc_t'])

    def test_zero_mass_part_still_valid(self):
        draft = {'source': {'part_count': 1},
                 'parts': [{'index': 0, 'part': 'P_0', 'base_part': 'P',
                            'persistent_id': 'p0'}],
                 'stages': []}
        evidence = {'instances': [{
            'craft_index': 0, 'part': 'P_0', 'base_part': 'P',
            'persistent_id': 'p0', 'current_part_mass_estimate_t': 0.0}],
            'index_fingerprint': {}}
        report = evaluate_five_models(
            draft, evidence, {'source_total_mass_t': 0.0})
        self.assertTrue(report['complete'])
        self.assertEqual(
            report['models']['float32_craft_order']['estimated_total_t'], 0.0)

    def test_float32_overflow_no_unhandled_exception(self):
        self.assertIsNone(to_float32(1e100))
        self.assertIsNone(to_float32(3.5e38))
        self.assertEqual(classify_float32_input(1e100),
                         'float32_overflow')
        self.assertEqual(classify_float32_input('bad'),
                         'invalid_float32_input')

    def test_accumulation_overflow_incomplete(self):
        parts = [{'index': 0, 'part': 'P_0', 'base_part': 'P',
                  'persistent_id': 'p0'},
                 {'index': 1, 'part': 'P_1', 'base_part': 'P',
                  'persistent_id': 'p1'}]
        instances = [
            {'craft_index': 0, 'part': 'P_0', 'base_part': 'P',
             'persistent_id': 'p0',
             'current_part_mass_estimate_t': 2e38},
            {'craft_index': 1, 'part': 'P_1', 'base_part': 'P',
             'persistent_id': 'p1',
             'current_part_mass_estimate_t': 2e38},
        ]
        # each input is representable in binary32, but their SUM overflows
        self.assertIsNotNone(to_float32(2e38))
        draft = {'source': {'filename': 't.craft',
                            'part_count': 2}, 'parts': parts,
                 'stages': [], 'warnings': [], 'structured_warnings': []}
        evidence = {'instances': instances, 'index_fingerprint': {}}
        report = evaluate_five_models(draft, evidence)
        self.assertTrue(report['complete'])  # mapping itself is complete
        entry = report['models']['float32_craft_order']
        self.assertFalse(entry['complete'])
        self.assertIsNone(entry['estimated_total_t'])
        codes = [d['code'] for d in entry['diagnostics']]
        self.assertIn('float32_accumulation_overflow', codes)
        # other models may still be computed
        self.assertIsNotNone(
            report['models']['double_order']['estimated_total_t'])

    def test_bool_nan_infinity_negative_still_rejected(self):
        for bad in (True, False, float('nan'), float('inf'), -1.0):
            self.assertIsNone(to_float32(bad), bad)

    def test_invalid_tolerance_no_conclusion(self):
        for bad in (True, float('nan'), 'oops', -1e-6, 0.0):
            comparison = fleet_model_comparison([], tolerance_t=bad)
            self.assertEqual(comparison['status'], 'invalid_input', bad)
            self.assertIsNone(comparison['tolerance_t'])
            self.assertIsNone(
                comparison['conclusion']['preferred_candidate'])
            self.assertEqual(
                comparison['conclusion']['conclusion_strength'],
                INSUFFICIENT_EVIDENCE)

    def test_tied_mae_rmse_no_preferred_candidate(self):
        reports = []
        for name in ('a', 'b', 'c'):
            reports.append(self._vehicle(name, 1e-6))
        # give every model identical stats -> ties everywhere
        comparison = fleet_model_comparison(reports)
        self.assertIsNone(comparison['conclusion']['preferred_candidate'])
        self.assertEqual(comparison['conclusion']['conclusion_strength'],
                         INSUFFICIENT_EVIDENCE)

    def test_lowest_mae_but_not_rmse_not_selected(self):
        # craft order: MAE 5e-6 (strictly lowest) but RMSE 7.07e-6 (NOT the
        # lowest — double_order has 5.1e-6). No unique best => no selection.
        def vehicle(name, craft_after, double_after):
            return {
                'craft_filename': name,
                'parachute_single_count': 0,
                'complete': True,
                'diagnostics': [],
                'models': {
                    'float32_craft_order': {
                        'complete': True,
                        'residual_after_pdc_t': craft_after,
                        'diagnostics': []},
                    'double_order': {
                        'complete': True,
                        'residual_after_pdc_t': double_after,
                        'diagnostics': []},
                    'input_float32_then_double': {
                        'complete': True,
                        'residual_after_pdc_t': 5.2e-6,
                        'diagnostics': []},
                    'float32_reverse_order': {
                        'complete': True,
                        'residual_after_pdc_t': 5.2e-6,
                        'diagnostics': []},
                    'double_sum_then_float32': {
                        'complete': True,
                        'residual_after_pdc_t': 5.2e-6,
                        'diagnostics': []},
                },
            }
        reports = [vehicle('a', 0.0, 5.1e-6),
                   vehicle('b', 10e-6, 5.1e-6)]
        comparison = fleet_model_comparison(reports)
        craft_stats = comparison['models']['float32_craft_order']
        self.assertEqual(craft_stats['mae_t'], 5e-6)
        self.assertGreater(craft_stats['rmse_t'],
                           comparison['models']['double_order']['rmse_t'])
        # craft order has the lowest MAE but NOT the lowest RMSE -> no pick
        self.assertIsNone(comparison['conclusion']['preferred_candidate'])
        self.assertEqual(comparison['conclusion']['conclusion_strength'],
                         INSUFFICIENT_EVIDENCE)

    @unittest.skipUnless(REAL_CACHE.is_file(), 'Installed ConfigCache is required for live fleet evidence')
    def test_real_data_conclusion_machine_readable(self):
        from server.import_service import _loadmeta_metadata
        from server.ksp_craft_mass_reconciliation import (
            reconcile_craft_mass)
        from server.ksp_install_index import build_ksp_index
        from server.ksp_mass_evidence import extract_part_mass_evidence
        from server.ksp_mass_forensics import analyze_vehicle_forensics
        from server.ksp_part_matcher import match_craft_parts
        from server.ksp_runtime_fingerprints import ksp_runtime_provenance
        from server.vab_parser import parse_craft
        index = build_ksp_index(REAL_KSP)
        provenance = ksp_runtime_provenance(REAL_KSP)
        reports = []
        for craft_path in sorted(Path('VAB').glob('*.craft')):
            if craft_path.name.endswith('.original'):
                continue
            draft = parse_craft(craft_path.read_text(encoding='utf-8-sig'),
                                craft_path.name)
            evidence = extract_part_mass_evidence(
                draft, index, match_craft_parts(draft, index),
                provenance=provenance)
            loadmeta = None
            lm = craft_path.with_suffix('.loadmeta')
            if lm.is_file():
                loadmeta = _loadmeta_metadata(
                    lm.read_text(encoding='utf-8-sig', errors='replace'))
            reconciliation = reconcile_craft_mass(draft, evidence, loadmeta)
            forensics = analyze_vehicle_forensics(
                draft, evidence, reconciliation, ksp_index=index)
            numeric = evaluate_five_models(
                draft, evidence, reconciliation,
                pdc_diff_total=forensics.get('pdc_candidate_diff_total_t'))
            numeric['craft_filename'] = craft_path.name
            numeric['parachute_single_count'] = \
                forensics['parachute_single_count']
            reports.append(numeric)
        comparison = fleet_model_comparison(reports)
        conclusion = comparison['conclusion']
        self.assertEqual(conclusion['preferred_candidate'],
                         'float32_craft_order')
        self.assertEqual(conclusion['conclusion_strength'],
                         'supported_by_dataset')
        self.assertTrue(conclusion['all_required_samples_complete'])
        self.assertFalse(conclusion['authoritative_ksp_semantics_confirmed'])
        self.assertFalse(conclusion['production_use_authorized'])
        self.assertEqual(conclusion['selection_reasons'],
                         ['all_normal_samples_within_tolerance',
                          'lowest_mae', 'lowest_rmse'])
        json.dumps(comparison, ensure_ascii=False)


if __name__ == '__main__':
    unittest.main()
