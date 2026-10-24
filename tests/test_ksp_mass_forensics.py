import copy
import json
import math
import unittest
from pathlib import Path

from server.ksp_install_index import KspIndex
from server.ksp_mass_forensics import (
    FORENSIC_ABS_TOLERANCE_T,
    INSUFFICIENT_EVIDENCE,
    SUPPORTED,
    analyze_vehicle_forensics,
    annular_ring_candidate_mass,
    fleet_forensics,
)
from server.vab_parser import parse_craft

from tests.game_inputs import configured_game_root
REAL_KSP = configured_game_root()
REAL_CACHE = Path(REAL_KSP) / 'GameData' / 'ModuleManager.ConfigCache'


class AnnularRingTests(unittest.TestCase):
    def test_default_pdc_parameters_exact_value(self):
        result = annular_ring_candidate_mass(1.25, 0.1, 0.2, 0.4)
        self.assertIsNotNone(result['mass_t'])
        self.assertEqual(result['mass_t'],
                         0.026389378290154267)
        self.assertEqual(result['diagnostics'], [])

    def test_uses_actual_fields_not_hardcoded_constant(self):
        cases = [
            ((1.875, 0.1, 0.2, 0.4), 0.0420973507),
            ((2.5, 0.1, 0.2, 0.4), 0.0578053086),
            ((2.5, 0.1, 0.5, 0.4), 0.1256637061),
        ]
        for (d, h, t, m), expected in cases:
            result = annular_ring_candidate_mass(d, h, t, m)
            self.assertIsNotNone(result['mass_t'], (d, h, t, m))
            self.assertAlmostEqual(result['mass_t'], expected, places=6)

    def test_missing_and_invalid_values(self):
        for args in ((None, 0.1, 0.2, 0.4), (1.25, None, 0.2, 0.4),
                     (1.25, 0.1, None, 0.4), (1.25, 0.1, 0.2, None),
                     ('oops', 0.1, 0.2, 0.4)):
            result = annular_ring_candidate_mass(*args)
            self.assertIsNone(result['mass_t'])
            self.assertTrue(result['diagnostics'])

    def test_bool_nan_infinity_rejected(self):
        for bad in (True, float('nan'), float('inf'), float('-inf')):
            result = annular_ring_candidate_mass(bad, 0.1, 0.2, 0.4)
            self.assertIsNone(result['mass_t'], bad)
            self.assertTrue(result['diagnostics'])

    def test_out_of_range_geometry(self):
        # thickness beyond outer radius
        result = annular_ring_candidate_mass(1.0, 0.1, 1.0, 0.4)
        self.assertIsNone(result['mass_t'])
        self.assertEqual(result['diagnostics'][0]['code'],
                         'invalid_pdc_geometry')
        # non-positive diameter / height
        self.assertIsNone(annular_ring_candidate_mass(0, 0.1, 0.2, 0.4)
                          ['mass_t'])
        self.assertIsNone(annular_ring_candidate_mass(1.0, 0, 0.2, 0.4)
                          ['mass_t'])
        # negative thickness / density
        self.assertIsNone(annular_ring_candidate_mass(1.0, 0.1, -0.1, 0.4)
                          ['mass_t'])
        self.assertIsNone(annular_ring_candidate_mass(1.0, 0.1, 0.2, -0.4)
                          ['mass_t'])

    def test_never_falls_back_to_constant(self):
        result = annular_ring_candidate_mass(3.0, 0.5, 1.4, 0.4)
        self.assertIsNotNone(result['mass_t'])
        # a different geometry must produce a different mass (no constant)
        other = annular_ring_candidate_mass(2.0, 0.5, 1.0, 0.4)
        self.assertNotEqual(result['mass_t'], other['mass_t'])


class EvidenceExtractionTests(unittest.TestCase):
    def test_craft_parser_keeps_instance_module_state(self):
        text = """ship = T
PART
{
    part = PDC_1
    MODULE
    {
        name = SSTUProceduralDecoupler
        diameter = 1.875
        height = 0.1
        thickness = 0.2
    }
}
PART
{
    part = Chute_2
    MODULE
    {
        name = RealChuteModule
        caseMass = 0.075000003
    }
    MODULE
    {
        name = ProceduralChute
        CHUTE
        {
            chuteID = 0
            getMass = True
            useDry = True
            mass = 10
            chuteCount = 1
        }
    }
}
"""
        draft = parse_craft(text, 't.craft')
        pdc = draft['parts'][0]['module_evidence']['SSTUProceduralDecoupler']
        self.assertEqual(pdc[0]['diameter_raw'], '1.875')
        self.assertEqual(pdc[0]['diameter'], 1.875)
        self.assertEqual(pdc[0]['height'], 0.1)
        self.assertEqual(pdc[0]['thickness'], 0.2)
        chute = draft['parts'][1]['module_evidence']
        self.assertEqual(chute['RealChuteModule'][0]['case_mass'],
                         0.075000003)
        c = chute['ProceduralChute'][0]['chutes'][0]
        self.assertEqual(c['get_mass'], True)
        self.assertEqual(c['use_dry'], True)
        self.assertEqual(c['mass'], 10.0)
        self.assertEqual(c['chute_count'], 1.0)
        # invalid values stay None
        text2 = ('ship = T\nPART\n{\n\tpart = A_1\n\tMODULE\n\t{\n'
                 '\t\tname = SSTUProceduralDecoupler\n\t\tdiameter = NaN\n'
                 '\t\theight = 0.1\n\t}\n}\n')
        draft2 = parse_craft(text2, 't2.craft')
        pdc2 = draft2['parts'][0]['module_evidence'][
            'SSTUProceduralDecoupler'][0]
        self.assertIsNone(pdc2['diameter'])
        self.assertEqual(pdc2['diameter_raw'], 'NaN')

    def test_index_keeps_special_module_config(self):
        parts = [{
            'kind': 'part', 'name': 'SSTU-SC-GEN-PDC',
            'parent_url': 'SSTU/PDC.cfg', 'cache_order': 0, 'primary': True,
            'diagnostics': [], 'mass_raw': '0.1', 'mass_t': 0.1,
            'modules': ['SSTUProceduralDecoupler'],
            'module_evidence': {
                'SSTUProceduralDecoupler': [{
                    'diameter_raw': '1.25', 'diameter': 1.25,
                    'height_raw': '0.1', 'height': 0.1,
                    'thickness_raw': '0.2', 'thickness': 0.2,
                    'massPerCubicMeter_raw': '0.4',
                    'massPerCubicMeter': 0.4,
                    'minDiameter': 0.625, 'maxDiameter': 1.25,
                }],
            },
            'dynamic_mass_modules': [], 'resources': [], 'fuel_tanks': [],
        }]
        metadata = {'schema_version': 1, 'source_kind': 'cache',
                    'config_cache_path': 'x', 'file_size': 1, 'mtime_ns': 1,
                    'sha256': '0' * 64, 'patched_node_count': 1,
                    'built_at': 'x', 'ksp_root': 'x', 'status': 'ok',
                    'record_counts': {'parts': 1, 'resources': 0,
                                      'sstu_layouts': 0,
                                      'anonymous_nodes': 0,
                                      'duplicate_definitions': 0}}
        index = KspIndex(status='ok', metadata=metadata, parts=parts,
                         resources=[], sstu_layouts=[], diagnostics=[])
        record = index.primary_part('SSTU-SC-GEN-PDC')
        entry = record['module_evidence']['SSTUProceduralDecoupler'][0]
        self.assertEqual(entry['massPerCubicMeter'], 0.4)
        self.assertEqual(entry['maxDiameter'], 1.25)
        self.assertEqual(entry['diameter'], 1.25)


class VehicleForensicsUnitTests(unittest.TestCase):
    def _index(self):
        parts = [{
            'kind': 'part', 'name': 'SSTU-SC-GEN-PDC',
            'parent_url': 'SSTU/PDC.cfg', 'cache_order': 0, 'primary': True,
            'diagnostics': [], 'mass_raw': '0.1', 'mass_t': 0.1,
            'modules': ['SSTUProceduralDecoupler'],
            'module_evidence': {
                'SSTUProceduralDecoupler': [{
                    'diameter': 1.25, 'height': 0.1, 'thickness': 0.2,
                    'massPerCubicMeter': 0.4,
                }],
            },
            'dynamic_mass_modules': [], 'resources': [], 'fuel_tanks': [],
        }]
        metadata = {'schema_version': 1, 'source_kind': 'cache',
                    'config_cache_path': 'x', 'file_size': 1, 'mtime_ns': 1,
                    'sha256': '0' * 64, 'patched_node_count': 1,
                    'built_at': 'x', 'ksp_root': 'x', 'status': 'ok',
                    'record_counts': {'parts': 1, 'resources': 0,
                                      'sstu_layouts': 0,
                                      'anonymous_nodes': 0,
                                      'duplicate_definitions': 0}}
        return KspIndex(status='ok', metadata=metadata, parts=parts,
                        resources=[], sstu_layouts=[], diagnostics=[])

    def _draft(self, pdc_params=None, chute=False, mk1pod=False):
        parts = []
        if pdc_params:
            d, h, t = pdc_params
            parts.append({
                'index': 0, 'part': 'PDC_0', 'base_part': 'SSTU-SC-GEN-PDC',
                'instance_id': '0', 'persistent_id': 'p0',
                'module_evidence': {
                    'SSTUProceduralDecoupler': [{
                        'diameter_raw': str(d), 'diameter': d,
                        'height_raw': str(h), 'height': h,
                        'thickness_raw': str(t), 'thickness': t,
                    }],
                },
            })
        index = len(parts)
        if chute:
            parts.append({
                'index': index, 'part': 'Chute_%d' % index,
                'base_part': 'parachuteSingle',
                'instance_id': str(index), 'persistent_id': 'pc',
                'module_evidence': {
                    'RealChuteModule': [{'case_mass_raw': '0.075',
                                         'case_mass': 0.075}],
                    'ProceduralChute': [{'chutes': []}],
                },
            })
            index += 1
        if mk1pod:
            parts.append({
                'index': index, 'part': 'Pod_%d' % index,
                'base_part': 'mk1pod.v2', 'instance_id': str(index),
                'persistent_id': 'pm', 'module_evidence': {},
            })
        return {
            'rocket': {'name': 'T'},
            'source': {'filename': 't.craft', 'part_count': len(parts)},
            'parts': parts,
            'stages': [], 'warnings': [], 'structured_warnings': [],
        }

    def _evidence(self, masses):
        instances = [{
            'craft_index': i, 'part': 'PDC_%d' % i,
            'base_part': 'SSTU-SC-GEN-PDC',
            'persistent_id': 'p%d' % i, 'match_status': 'exact',
            'resolution_status': 'resolved_static',
            'canonical_name': 'SSTU-SC-GEN-PDC',
            'current_part_mass_estimate_t': mass,
            'static_dry_mass_estimate_t': mass,
            'current_resource_mass_t': 0.0,
            'contains_launch_clamp': False,
            'possible_non_flight_part': False,
            'part_source': {'parent_url': 'x', 'cache_order': 0},
            'diagnostics': [],
        } for i, mass in enumerate(masses)]
        return {'instances': instances, 'index_fingerprint': {'sha256': '0'}}

    def _reconciliation(self, residual):
        return {'reconciliation_status': 'unreconciled',
                'residual_include_all_t': residual}

    def test_vehicle_analysis_pdc_diff(self):
        index = self._index()
        draft = self._draft(pdc_params=(1.25, 0.1, 0.2))
        evidence = self._evidence([0.1])
        report = analyze_vehicle_forensics(
            draft, evidence, self._reconciliation(0.07361062),
            ksp_index=index)
        self.assertEqual(report['pdc_instance_count'], 1)
        inst = report['pdc_instances'][0]
        self.assertAlmostEqual(inst['candidate_mass_t'],
                               0.026389378290154267)
        self.assertAlmostEqual(inst['diff_t'], 0.07361062170984574)
        self.assertAlmostEqual(report['residual_after_pdc_t'],
                               0.0, places=6)
        self.assertEqual(report['parachute_single_count'], 0)
        self.assertEqual(report['mk1pod_v2_count'], 0)

    def test_inputs_not_modified_and_json_serializable(self):
        index = self._index()
        draft = self._draft(pdc_params=(1.875, 0.1, 0.2))
        evidence = self._evidence([0.115708])
        rec = self._reconciliation(0.07361062)
        draft_before = copy.deepcopy(draft)
        evidence_before = copy.deepcopy(evidence)
        first = analyze_vehicle_forensics(draft, evidence, rec,
                                          ksp_index=index)
        second = analyze_vehicle_forensics(draft, evidence, rec,
                                           ksp_index=index)
        self.assertEqual(first, second)
        json.dumps(first, ensure_ascii=False)
        self.assertEqual(draft, draft_before)
        self.assertEqual(evidence, evidence_before)

    def test_fleet_colinearity_detection(self):
        def vehicle(chute, mk1pod):
            return {'craft_filename': 'x', 'pdc_instance_count': 1,
                    'parachute_single_count': 1 if chute else 0,
                    'mk1pod_v2_count': 1 if mk1pod else 0,
                    'residual_after_pdc_t': 0.075}
        colinear = fleet_forensics([vehicle(True, True),
                                    vehicle(False, False),
                                    vehicle(True, True)])
        self.assertTrue(colinear['colinear_parachute_mk1pod'])
        self.assertEqual(colinear['remaining_residual_hypothesis_status'],
                         INSUFFICIENT_EVIDENCE)
        self.assertEqual(colinear['total_pdc_instances'], 3)
        separate = fleet_forensics([vehicle(True, False),
                                    vehicle(False, False)])
        self.assertFalse(separate['colinear_parachute_mk1pod'])

    def test_fleet_verdict_rules(self):
        def vehicle(after):
            return {'craft_filename': 'x', 'pdc_instance_count': 1,
                    'parachute_single_count': 0, 'mk1pod_v2_count': 0,
                    'residual_after_pdc_t': after}
        ok = fleet_forensics([vehicle(1e-6), vehicle(-2e-5)])
        self.assertEqual(ok['pdc_hypothesis_status'], SUPPORTED)
        self.assertEqual(ok['normal_vehicles_within_tolerance'], 2)
        out = fleet_forensics([vehicle(8e-5), vehicle(1e-6)])
        self.assertEqual(out['pdc_hypothesis_status'],
                         INSUFFICIENT_EVIDENCE)
        self.assertEqual(out['normal_vehicles_within_tolerance'], 1)
        way_out = fleet_forensics([vehicle(0.05), vehicle(1e-6)])
        self.assertEqual(way_out['pdc_hypothesis_status'], 'contradicted')


@unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
@unittest.skipUnless(REAL_CACHE.is_file(),
                     'real KSP install not present on this machine')
class RealVabForensicsSmokeTests(unittest.TestCase):
    """Read-only forensic audit over the real VAB folder."""

    def _run_fleet(self):
        from server.import_service import _loadmeta_metadata
        from server.ksp_craft_mass_reconciliation import (
            reconcile_craft_mass)
        from server.ksp_install_index import build_ksp_index
        from server.ksp_mass_evidence import extract_part_mass_evidence
        from server.ksp_part_matcher import match_craft_parts
        from server.ksp_runtime_fingerprints import ksp_runtime_provenance
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
            report = analyze_vehicle_forensics(
                draft, evidence, reconciliation, ksp_index=index)
            report['craft_filename'] = craft_path.name
            reports.append(report)
        return fleet_forensics(reports), reports

    def test_real_fleet_forensics(self):
        fleet, reports = self._run_fleet()
        self.assertEqual(len(reports), 20)
        self.assertEqual(fleet['total_pdc_instances'], 41)
        # 16 of 17 normal vehicles within the forensic tolerance
        self.assertEqual(fleet['normal_vehicle_count'], 17)
        self.assertEqual(fleet['normal_vehicles_within_tolerance'], 16)
        out_of_tolerance = [item for item in fleet['normal_vehicle_after_pdc']
                            if abs(item['residual_after_pdc_t']) >
                            FORENSIC_ABS_TOLERANCE_T]
        self.assertEqual(len(out_of_tolerance), 1)
        self.assertEqual(out_of_tolerance[0]['craft'],
                         'Neutrons II 354S.craft')
        self.assertLess(abs(out_of_tolerance[0]['residual_after_pdc_t']),
                        1e-4)
        # all special vehicles land in the 0.075 band
        self.assertEqual(len(fleet['special_vehicle_after_pdc']), 3)
        # 6B.3C.4A: the old VAB special vehicles keep their parachutes in
        # Automatic mode (calcSelect=True) which is OUT of the authorized
        # scope -> the production evidence chain fails closed and the
        # forensic residual is unavailable (never a fake 0.075 estimate)
        for item in fleet['special_vehicle_after_pdc']:
            self.assertIsNone(item['residual_after_pdc_t'])
        self.assertEqual(fleet['remaining_residual_hypothesis_status'],
                         INSUFFICIENT_EVIDENCE)
        # parachuteSingle and mk1pod.v2 are perfectly colinear here
        self.assertTrue(fleet['colinear_parachute_mk1pod'])
        self.assertEqual(fleet['remaining_residual_hypothesis_status'],
                         INSUFFICIENT_EVIDENCE)
        self.assertEqual(fleet['pdc_hypothesis_status'],
                         INSUFFICIENT_EVIDENCE)
        json.dumps(fleet, ensure_ascii=False)


class ForensicReworkTests(unittest.TestCase):
    """6B.3C.1-R: completeness gates, one-to-one mapping, boundaries."""

    def _index(self, mpm_values=None, mpm_raises=False):
        entries = []
        if mpm_values:
            for value in mpm_values:
                entries.append({'diameter': 1.25, 'height': 0.1,
                                'thickness': 0.2,
                                'massPerCubicMeter': value})
        elif not mpm_raises:
            entries.append({'diameter': 1.25, 'height': 0.1,
                            'thickness': 0.2, 'massPerCubicMeter': 0.4})
        parts = [{
            'kind': 'part', 'name': 'SSTU-SC-GEN-PDC',
            'parent_url': 'SSTU/PDC.cfg', 'cache_order': 0, 'primary': True,
            'diagnostics': [], 'mass_raw': '0.1', 'mass_t': 0.1,
            'modules': ['SSTUProceduralDecoupler'],
            'module_evidence': {'SSTUProceduralDecoupler': entries},
            'dynamic_mass_modules': [], 'resources': [], 'fuel_tanks': [],
        }]
        metadata = {'schema_version': 1, 'source_kind': 'cache',
                    'config_cache_path': 'x', 'file_size': 1, 'mtime_ns': 1,
                    'sha256': '0' * 64, 'patched_node_count': 1,
                    'built_at': 'x', 'ksp_root': 'x', 'status': 'ok',
                    'record_counts': {'parts': 1, 'resources': 0,
                                      'sstu_layouts': 0,
                                      'anonymous_nodes': 0,
                                      'duplicate_definitions': 0}}
        return KspIndex(status='ok', metadata=metadata, parts=parts,
                        resources=[], sstu_layouts=[], diagnostics=[])

    def _pdc_part(self, index, **over):
        return {
            'index': 0, 'part': 'PDC_0', 'base_part': 'SSTU-SC-GEN-PDC',
            'instance_id': '0', 'persistent_id': 'p0',
            'module_evidence': {
                'SSTUProceduralDecoupler': [{
                    'diameter_raw': '1.25', 'diameter': 1.25,
                    'height_raw': '0.1', 'height': 0.1,
                    'thickness_raw': '0.2', 'thickness': 0.2,
                }],
            },
        }

    def _draft(self, parts):
        return {
            'rocket': {'name': 'T'},
            'source': {'filename': 't.craft', 'part_count': len(parts)},
            'parts': parts,
            'stages': [], 'warnings': [], 'structured_warnings': [],
        }

    def _evidence(self, instances):
        return {'instances': instances,
                'index_fingerprint': {
                    'sha256': '0' * 64, 'config_cache_path': 'x',
                    'file_size': 1, 'mtime_ns': 1, 'schema_version': 1,
                    'source_kind': 'cache', 'patched_node_count': 1,
                    'built_at': 'SHOULD-NOT-LEAK'}}

    def _instance(self, index=0, mass=0.1, base='SSTU-SC-GEN-PDC'):
        return {
            'craft_index': index, 'part': 'PDC_%d' % index,
            'base_part': base, 'persistent_id': 'p%d' % index,
            'match_status': 'exact', 'resolution_status': 'resolved_static',
            'canonical_name': 'SSTU-SC-GEN-PDC',
            'current_part_mass_estimate_t': mass,
            'static_dry_mass_estimate_t': mass,
            'current_resource_mass_t': 0.0,
            'contains_launch_clamp': False,
            'possible_non_flight_part': False,
            'part_source': {'parent_url': 'x', 'cache_order': 0},
            'diagnostics': [],
        }

    def test_missing_pdc_parameter_keeps_totals_null(self):
        index = self._index()
        part = self._pdc_part(index)
        part['module_evidence']['SSTUProceduralDecoupler'][0]['diameter'] = None
        report = analyze_vehicle_forensics(
            self._draft([part]), self._evidence([self._instance()]),
            {'residual_include_all_t': 0.07361062}, ksp_index=index)
        self.assertFalse(report['pdc_candidate_complete'])
        self.assertEqual(report['pdc_expected_instance_count'], 1)
        self.assertEqual(report['pdc_evidence_instance_count'], 1)
        self.assertEqual(report['pdc_valid_candidate_count'], 0)
        self.assertIsNone(report['pdc_candidate_mass_total_t'])
        self.assertIsNone(report['pdc_candidate_diff_total_t'])
        self.assertIsNone(report['residual_after_pdc_t'])
        self.assertEqual(report['known_pdc_candidate_mass_subtotal_t'], None)

    def test_known_subtotals_still_available(self):
        index = self._index()
        parts = [self._pdc_part(index),
                 self._pdc_part(index).copy()]
        parts[1]['index'] = 1
        parts[1]['module_evidence']['SSTUProceduralDecoupler'][0]['diameter'] = None
        report = analyze_vehicle_forensics(
            self._draft(parts),
            self._evidence([self._instance(0), self._instance(1)]),
            {'residual_include_all_t': 0.14722124}, ksp_index=index)
        self.assertFalse(report['pdc_candidate_complete'])
        self.assertIsNotNone(report['known_pdc_candidate_mass_subtotal_t'])
        self.assertAlmostEqual(
            report['known_pdc_candidate_mass_subtotal_t'],
            0.026389378290154267)
        self.assertIsNone(report['pdc_candidate_mass_total_t'])
        self.assertIsNone(report['residual_after_pdc_t'])

    def test_duplicate_evidence_index_not_silently_merged(self):
        index = self._index()
        report = analyze_vehicle_forensics(
            self._draft([self._pdc_part(index)]),
            self._evidence([self._instance(0), self._instance(0)]),
            {'residual_include_all_t': 0.07}, ksp_index=index)
        self.assertFalse(report['pdc_candidate_complete'])
        reasons = [r for item in report['pdc_missing_or_invalid_instances']
                   for r in item['reasons']]
        self.assertIn('duplicate_evidence_index', reasons)
        self.assertIsNone(report['residual_after_pdc_t'])

    def test_missing_evidence_makes_candidate_incomplete(self):
        index = self._index()
        report = analyze_vehicle_forensics(
            self._draft([self._pdc_part(index)]), self._evidence([]),
            {'residual_include_all_t': 0.07}, ksp_index=index)
        self.assertFalse(report['pdc_candidate_complete'])
        self.assertEqual(report['pdc_evidence_instance_count'], 0)
        reasons = [r for item in report['pdc_missing_or_invalid_instances']
                   for r in item['reasons']]
        self.assertIn('missing_evidence', reasons)

    def test_numeric_strings_parsed(self):
        index = self._index()
        report = analyze_vehicle_forensics(
            self._draft([self._pdc_part(index)]),
            self._evidence([self._instance(mass='0.1')]),
            {'residual_include_all_t': 0.07361062}, ksp_index=index)
        self.assertTrue(report['pdc_candidate_complete'])
        self.assertAlmostEqual(report['residual_after_pdc_t'], 0.0, places=6)

    def test_bool_nan_infinity_negative_rejected(self):
        for bad in (True, float('nan'), float('inf'), -0.05):
            index = self._index()
            report = analyze_vehicle_forensics(
                self._draft([self._pdc_part(index)]),
                self._evidence([self._instance(mass=bad)]),
                {'residual_include_all_t': 0.07}, ksp_index=index)
            self.assertFalse(report['pdc_candidate_complete'], bad)
            self.assertIsNone(report['residual_after_pdc_t'], bad)

    def test_identical_mpm_values_accepted(self):
        index = self._index(mpm_values=[0.4, 0.4])
        report = analyze_vehicle_forensics(
            self._draft([self._pdc_part(index)]),
            self._evidence([self._instance()]),
            {'residual_include_all_t': 0.07361062}, ksp_index=index)
        self.assertTrue(report['pdc_candidate_complete'])

    def test_conflicting_mpm_ambiguous(self):
        index = self._index(mpm_values=[0.4, 0.9])
        report = analyze_vehicle_forensics(
            self._draft([self._pdc_part(index)]),
            self._evidence([self._instance()]),
            {'residual_include_all_t': 0.07}, ksp_index=index)
        self.assertFalse(report['pdc_candidate_complete'])
        codes = [d['code'] for inst in report['pdc_instances']
                 for d in inst['diagnostics']]
        self.assertIn('ambiguous_mass_per_cubic_meter', codes)
        self.assertIsNone(report['residual_after_pdc_t'])

    def test_identity_mismatch_detected(self):
        index = self._index()
        report = analyze_vehicle_forensics(
            self._draft([self._pdc_part(index)]),
            self._evidence([self._instance(base='Other')]),
            {'residual_include_all_t': 0.07}, ksp_index=index)
        self.assertFalse(report['pdc_candidate_complete'])
        reasons = [r for item in report['pdc_missing_or_invalid_instances']
                   for r in item['reasons']]
        self.assertIn('evidence_identity_mismatch', reasons)

    def test_empty_fleet_never_supported(self):
        fleet = fleet_forensics([])
        self.assertEqual(fleet['pdc_hypothesis_status'],
                         INSUFFICIENT_EVIDENCE)
        self.assertEqual(fleet['collinearity_status'],
                         'insufficient_variation')
        self.assertFalse(fleet['colinear_parachute_mk1pod'])
        self.assertEqual(fleet['pdc_hypothesis_diagnostics'][0]['code'],
                         'empty_fleet')

    def test_all_zero_features_not_perfect_collinearity(self):
        def vehicle():
            return {'craft_filename': 'x', 'pdc_instance_count': 0,
                    'pdc_candidate_complete': True,
                    'parachute_single_count': 0, 'mk1pod_v2_count': 0,
                    'residual_after_pdc_t': 1e-6}
        fleet = fleet_forensics([vehicle(), vehicle()])
        self.assertEqual(fleet['collinearity_status'],
                         'insufficient_variation')
        self.assertFalse(fleet['colinear_parachute_mk1pod'])
        # residuals within tolerance are still a valid PDC signal
        self.assertEqual(fleet['pdc_hypothesis_status'], SUPPORTED)

    def test_unequal_counts_not_perfect(self):
        def vehicle(p, m):
            return {'craft_filename': 'x', 'pdc_instance_count': 0,
                    'pdc_candidate_complete': True,
                    'parachute_single_count': p, 'mk1pod_v2_count': m,
                    'residual_after_pdc_t': 1e-6}
        fleet = fleet_forensics([vehicle(2, 1), vehicle(0, 0)])
        self.assertEqual(fleet['collinearity_status'], 'not_collinear')
        self.assertFalse(fleet['colinear_parachute_mk1pod'])

    def test_stable_fingerprint_excludes_built_at(self):
        index = self._index()
        report = analyze_vehicle_forensics(
            self._draft([self._pdc_part(index)]),
            self._evidence([self._instance()]),
            {'residual_include_all_t': 0.07361062}, ksp_index=index)
        fingerprint = report['index_fingerprint']
        self.assertNotIn('built_at', fingerprint)
        self.assertEqual(fingerprint['sha256'], '0' * 64)
        self.assertEqual(fingerprint['source_kind'], 'cache')
        json.dumps(report, ensure_ascii=False)

    def test_zero_mass_candidate_is_valid(self):
        # thickness=0 -> zero-volume ring -> legal zero-mass candidate
        index = self._index()
        part = self._pdc_part(index)
        part['module_evidence']['SSTUProceduralDecoupler'][0]['thickness'] = 0
        report = analyze_vehicle_forensics(
            self._draft([part]), self._evidence([self._instance(mass=0.1)]),
            {'residual_include_all_t': 0.07361062}, ksp_index=index)
        self.assertTrue(report['pdc_candidate_complete'])
        inst = report['pdc_instances'][0]
        self.assertEqual(inst['candidate_mass_t'], 0.0)
        self.assertAlmostEqual(inst['diff_t'], 0.1, places=9)

    def test_zero_mpm_candidate_is_valid(self):
        index = self._index()
        # rebuild index with massPerCubicMeter=0
        from server.ksp_install_index import KspIndex as K
        record = index.primary_part('SSTU-SC-GEN-PDC')
        record['module_evidence']['SSTUProceduralDecoupler'][0][
            'massPerCubicMeter'] = 0.0
        part = self._pdc_part(index)
        report = analyze_vehicle_forensics(
            self._draft([part]), self._evidence([self._instance(mass=0.1)]),
            {'residual_include_all_t': 0.1}, ksp_index=index)
        self.assertTrue(report['pdc_candidate_complete'])
        self.assertEqual(report['pdc_instances'][0]['candidate_mass_t'], 0.0)

    def test_unexpected_evidence_index_makes_incomplete(self):
        index = self._index()
        evidence = self._evidence([self._instance()])
        evidence['instances'].append(self._instance(index=77))
        report = analyze_vehicle_forensics(
            self._draft([self._pdc_part(index)]), evidence,
            {'residual_include_all_t': 0.07}, ksp_index=index)
        self.assertFalse(report['pdc_candidate_complete'])
        reasons = [r for item in report['pdc_missing_or_invalid_instances']
                   for r in item['reasons']]
        self.assertIn('unexpected_evidence_index', reasons)

    def test_global_duplicate_part_index_makes_incomplete(self):
        index = self._index()
        part = self._pdc_part(index)
        part['index'] = 0
        other = self._pdc_part(index)
        other['index'] = 0  # duplicate craft index
        report = analyze_vehicle_forensics(
            self._draft([part, other]),
            self._evidence([self._instance(0), self._instance(0)]),
            {'residual_include_all_t': 0.1}, ksp_index=index)
        self.assertFalse(report['pdc_candidate_complete'])
        reasons = [r for item in report['pdc_missing_or_invalid_instances']
                   for r in item['reasons']]
        self.assertIn('duplicate_craft_part_index', reasons)

    def test_multiple_pdc_states_conflict_ambiguous(self):
        index = self._index()
        part = self._pdc_part(index)
        part['module_evidence']['SSTUProceduralDecoupler'].append(
            {'diameter': 2.5, 'height': 0.1, 'thickness': 0.2})
        report = analyze_vehicle_forensics(
            self._draft([part]), self._evidence([self._instance()]),
            {'residual_include_all_t': 0.07}, ksp_index=index)
        self.assertFalse(report['pdc_candidate_complete'])
        codes = [d['code'] for d in report['diagnostics']]
        self.assertIn('pdc_evidence_incomplete', codes)
        reasons = [r for item in report['pdc_missing_or_invalid_instances']
                   for r in item['reasons']]
        self.assertIn('ambiguous_pdc_module_state', reasons)

    def test_identical_multiple_pdc_states_accepted(self):
        index = self._index()
        part = self._pdc_part(index)
        part['module_evidence']['SSTUProceduralDecoupler'].append(
            {'diameter': 1.25, 'height': 0.1, 'thickness': 0.2})
        report = analyze_vehicle_forensics(
            self._draft([part]), self._evidence([self._instance()]),
            {'residual_include_all_t': 0.07361062}, ksp_index=index)
        self.assertTrue(report['pdc_candidate_complete'])


class CliContractTests(unittest.TestCase):
    """Mass-residual module CLI contract (determinism / exit codes)."""

    def _run_cli(self, *args):
        import subprocess
        import sys
        return subprocess.run(
            [sys.executable, '-m', 'tools.research.audit_ksp_mass_residuals', *args],
            capture_output=True, text=True, encoding='utf-8',
            cwd=str(Path(__file__).resolve().parent.parent))

    @unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
    @unittest.skipUnless(REAL_CACHE.is_file(),
                         'real KSP install not present on this machine')
    def test_cli_json_bytes_deterministic_and_utf8(self):
        first = self._run_cli('--json')
        second = self._run_cli('--json')
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        payload = json.loads(first.stdout)
        self.assertEqual(len(payload['vehicles']), 20)
        # the Chinese craft filename must survive the UTF-8 round-trip
        names = [v['craft_filename'] for v in payload['vehicles']]
        self.assertIn('自动存档飞船.craft', names)
        self.assertIn('fleet_numeric_comparison', payload)
        self.assertIn('numeric_models', payload)
        craft_stats = payload['fleet_numeric_comparison']['models'][
            'float32_craft_order']
        self.assertEqual(craft_stats['within_tolerance_count'], 17)
        self.assertEqual(craft_stats['expected_sample_count'], 17)
        self.assertEqual(craft_stats['valid_sample_count'], 17)
        self.assertEqual(craft_stats['incomplete_sample_count'], 0)
        self.assertTrue(craft_stats['all_samples_complete'])
        self.assertTrue(craft_stats['all_within_tolerance'])
        # machine-readable conclusion embedded in fleet_numeric_comparison
        conclusion = payload['fleet_numeric_comparison']['conclusion']
        self.assertEqual(conclusion['preferred_candidate'],
                         'float32_craft_order')
        self.assertEqual(conclusion['conclusion_strength'],
                         'supported_by_dataset')
        self.assertTrue(conclusion['all_required_samples_complete'])
        self.assertFalse(conclusion['authoritative_ksp_semantics_confirmed'])
        self.assertFalse(conclusion['production_use_authorized'])
        # no built_at anywhere in the payload
        self.assertNotIn('built_at', first.stdout)

    def test_cli_invalid_path_exits_2(self):
        result = self._run_cli('--vab-dir', './does_not_exist_xyz')
        self.assertEqual(result.returncode, 2)
        self.assertNotEqual(result.stderr.strip(), '')

    @unittest.skipUnless(REAL_CACHE.is_file(),
                         'real KSP install not present on this machine')
    def test_cli_corrupt_craft_exits_2(self):
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as tmp:
            broken = P(tmp) / 'broken.craft'
            broken.write_text('PART\n{', encoding='utf-8')
            result = self._run_cli('--vab-dir', tmp)
            self.assertEqual(result.returncode, 2)
            self.assertIn('broken.craft', result.stderr)


if __name__ == '__main__':
    unittest.main()
