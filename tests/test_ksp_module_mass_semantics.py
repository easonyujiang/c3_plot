import copy
import json
import unittest
from pathlib import Path

from server.ksp_module_mass_semantics import (
    AMBIGUOUS_MODULE_STATE,
    CONTRADICTED,
    INSUFFICIENT_EVIDENCE,
    NOT_APPLICABLE,
    REALCHUTE_EXPECTED_SHA256,
    REALFUELS_EXPECTED_SHA256,
    SUPPORTED,
    assembly_provenance,
    chute_deployed_area,
    chute_mass,
    evaluate_hypotheses,
    mft_component_evidence,
    mft_mass_is_additive_determination,
    parse_base_mass_values,
    realchute_component_evidence,
    realchute_mass_delta,
)
from server.ksp_module_mass_semantics import _module_state

REAL_KSP = r'D:\SteamLibrary\steamapps\common\Kerbal Space Program'
REAL_CACHE = Path(REAL_KSP) / 'GameData' / 'ModuleManager.ConfigCache'


def make_index(parachute_single=None, mk1pod_v2=None, materials=None):
    """Minimal KspIndex with the two candidate parts + MATERIAL evidence."""
    from server.ksp_install_index import KspIndex

    def part_record(name, mass, parent_url, module_evidence, modules,
                    dynamic=()):
        return {
            'kind': 'part', 'name': name, 'parent_url': parent_url,
            'cache_order': 0, 'primary': True, 'diagnostics': [],
            'mass_raw': None if mass is None else str(mass),
            'mass_t': mass,
            'modules': list(modules),
            'module_evidence': module_evidence,
            'dynamic_mass_modules': list(dynamic),
            'resources': [], 'fuel_tanks': [], 'engines': [],
            'sstu_cluster': None,
        }

    parts = []
    if parachute_single is not None:
        spec = parachute_single
        rc_config = {'case_mass_raw': '0.075', 'case_mass': 0.075,
                     'spare_chutes_raw': '5', 'spare_chutes': 5.0,
                     'parachutes': [{'material': spec.get('material',
                                                          'Nylon'),
                                     'deployed_diameter_raw': '20.7',
                                     'deployed_diameter': 20.7,
                                     'pre_deployed_diameter_raw': '0.8',
                                     'pre_deployed_diameter': 0.8}]}
        parts.append(part_record(
            'parachuteSingle', spec.get('mass', 0.075),
            spec.get('parent_url', 'Squad/Parts/Utility/parachuteMk1/'
                                   'parachuteMk1.cfg'),
            {'RealChuteModule': [rc_config],
             'ProceduralChute': [{'chutes': []}]},
            ['RealChuteModule', 'ProceduralChute'],
            dynamic=('RealChuteModule',)))
    if mk1pod_v2 is not None:
        spec = mk1pod_v2
        mft_config = {'mass_raw': None, 'mass': None,
                      'basemass_raw': spec.get('basemass_raw', '-1'),
                      'basemass': float(spec.get('basemass_raw', '-1')),
                      'volume_raw': '50.05', 'volume': 50.05,
                      'utilization_raw': None, 'utilization': None,
                      'totalVolume_raw': None, 'totalVolume': None,
                      'type': 'ServiceModule',
                      'mass_is_additive_raw':
                          spec.get('mass_is_additive_raw'),
                      'mass_is_additive': spec.get('mass_is_additive'),
                      'tanks': [{'name': 'ElectricCharge', 'mass_raw': None,
                                 'mass': None, 'utilization_raw': '1000',
                                 'utilization': 1000.0,
                                 'amount_raw': 'full', 'amount': None,
                                 'max_amount_raw': '50',
                                 'max_amount': 50.0}]}
        parts.append(part_record(
            'mk1pod_v2', spec.get('mass', 0.4),
            'Squad/Parts/Command/mk1pod_v2/mk1Pod_v2.cfg',
            {'ModuleFuelTanks': [mft_config]},
            ['ModuleFuelTanks'], dynamic=('ModuleFuelTanks',)))
    material_records = []
    for name, density in (materials or {}).items():
        material_records.append({
            'kind': 'material', 'name': name,
            'area_density_raw': str(density), 'area_density': density,
            'parent_url': 'RealChute/Materials/ParachuteMaterials.cfg',
            'cache_order': 0, 'primary': True, 'diagnostics': []})
    metadata = {
        'schema_version': 1, 'source_kind': 'cache',
        'config_cache_path': 'x', 'file_size': 1, 'mtime_ns': 1,
        'sha256': '0' * 64, 'patched_node_count': 1, 'built_at': 'x',
        'ksp_root': 'x', 'status': 'ok',
        'record_counts': {'parts': len(parts), 'resources': 0,
                          'sstu_layouts': 0, 'materials': len(materials),
                          'anonymous_nodes': 0,
                          'duplicate_definitions': 0}}
    return KspIndex(status='ok', metadata=metadata, parts=parts,
                    resources=[], sstu_layouts=[],
                    materials=material_records, diagnostics=[])


def make_draft(part_specs):
    """part_specs: list of dicts with base_part + module_evidence."""
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
            'modules': list(spec.get('modules') or []),
            'module_evidence': dict(spec.get('module_evidence') or {}),
            'resources': spec.get('resources') or [],
            'tanks': spec.get('tanks') or [],
        })
    return {
        'rocket': {'name': 'Test'},
        'stages': [],
        'source': {'part_count': len(parts)},
        'parts': parts,
        'warnings': [],
        'structured_warnings': [],
    }


def chute_part(case_mass=0.075000003, chutes=None, spare=5):
    """parachuteSingle part with RealChuteModule + ProceduralChute state."""
    entry = {'case_mass_raw': str(case_mass), 'case_mass': case_mass,
             'spare_chutes_raw': str(spare), 'spare_chutes': float(spare)}
    chutes = chutes or [{'get_mass': True, 'use_dry': True,
                         'mass_raw': '10', 'mass': 10.0,
                         'chute_count_raw': '1', 'chute_count': 1.0,
                         'dep_diam_raw': '20.7', 'dep_diam': 20.7,
                         'pre_dep_diam_raw': '0.8',
                         'pre_dep_diam': 0.8}]
    return {
        'base_part': 'parachuteSingle',
        'modules': ['RealChuteModule', 'ProceduralChute'],
        'module_evidence': {
            'RealChuteModule': [entry],
            'ProceduralChute': [{'chutes': chutes}],
        },
    }


def pod_part(mass=0.0, volume=50.05, additive=None, added_layers=0.0):
    return {
        'base_part': 'mk1pod.v2',
        'modules': ['ModuleFuelTanks'],
        'module_evidence': {'ModuleFuelTanks': [{
            'mass_raw': str(mass), 'mass': float(mass),
            'volume_raw': str(volume), 'volume': float(volume),
            'basemass_raw': None, 'basemass': None,
            'mass_is_additive_raw': None,
            'mass_is_additive': additive,
            'number_of_added_mlilayers_raw':
                None if added_layers is None else str(added_layers),
            'number_of_added_mlilayers': added_layers,
        }]},
    }


def default_index():
    return make_index(
        parachute_single={},
        mk1pod_v2={},
        materials={'Nylon': 0.0000565})


def il_evidence_for_zero_base_branch():
    """Static IL evidence mimicking the real CalculateMass shape: writers
    carry the constant-true write; the pattern matcher validates the
    zero-base-mass branch; ParseBaseMass IL parses a plain value; the MLI
    chain (get_totalMLILayers / get_numberOfAddedMLILayers /
    CalculateMassRF shapes + ServiceModule tank definition with zero base
    layers) proves the MLI term is zero."""
    return {
        'writers': {
            'writers': [{
                'type': 'RealFuels.Tanks.ModuleFuelTanks',
                'method': 'CalculateMass',
                'token': '0x06000309',
                'writes': [{'offset': 0x99, 'opcode': 'stfld',
                            'constant_bool': True,
                            'constant_source_instruction': 0x98}],
            }],
            'writer_count': 1,
            'method_bodies_total': 1,
            'method_bodies_unparsed': 0,
            'method_bodies_decode_incomplete': 0,
            'scan_complete': True,
        },
        'mass_is_additive_writers': None,
        'calculate_mass': {
            'decode_complete': True,
            'decode_errors': [],
            'first_error_offset': None,
            'mass_is_additive_field_token': 0x04000230,
            'mass_field_token': 0x0400022F,
            'instructions': [
                {'offset': 0x35, 'name': 'stloc.0', 'size': 1,
                 'operand': None, 'target': None, 'flow': 'exec',
                 'error': None, 'text': 'stloc.0'},
                {'offset': 0x3E, 'name': 'ldloc.0', 'size': 1,
                 'operand': None, 'target': None, 'flow': 'exec',
                 'error': None, 'text': 'ldloc.0'},
                {'offset': 0x3F, 'name': 'ldc.r8', 'size': 9,
                 'operand': ('r8', 0.0), 'target': None, 'flow': 'exec',
                 'error': None, 'text': 'ldc.r8 0.0'},
                {'offset': 0x48, 'name': 'blt.un.s', 'size': 2,
                 'operand': ('br1', -58), 'target': 0x8C, 'flow': 'exec',
                 'error': None, 'text': 'blt.un.s -> 0x8C'},
                {'offset': 0x8C, 'name': 'ldarg.0', 'size': 1,
                 'operand': None, 'target': None, 'flow': 'exec',
                 'error': None, 'text': 'ldarg.0'},
                {'offset': 0x8D, 'name': 'ldc.r4', 'size': 5,
                 'operand': ('r4', 0.0), 'target': None, 'flow': 'exec',
                 'error': None, 'text': 'ldc.r4 0.0'},
                {'offset': 0x92, 'name': 'stfld', 'size': 5,
                 'operand': ('tok4', 0x0400022F), 'target': None,
                 'flow': 'exec', 'error': None,
                 'text': 'stfld field::RealFuels.Tanks.ModuleFuelTanks::'
                         'mass'},
                {'offset': 0x97, 'name': 'ldarg.0', 'size': 1,
                 'operand': None, 'target': None, 'flow': 'exec',
                 'error': None, 'text': 'ldarg.0'},
                {'offset': 0x98, 'name': 'ldc.i4.1', 'size': 1,
                 'operand': None, 'target': None, 'flow': 'exec',
                 'error': None, 'text': 'ldc.i4.1'},
                {'offset': 0x99, 'name': 'stfld', 'size': 5,
                 'operand': ('tok4', 0x04000230), 'target': None,
                 'flow': 'exec', 'error': None,
                 'text': 'stfld field::RealFuels.Tanks.ModuleFuelTanks::'
                         'massIsAdditive'},
            ],
        },
        'parse_base_mass': {
            'instructions': [
                {'offset': 0x57, 'name': 'ldarg.1', 'size': 1,
                 'operand': None, 'target': None, 'flow': 'exec',
                 'error': None, 'text': 'ldarg.1'},
                {'offset': 0x5E, 'name': 'ldflda', 'size': 5,
                 'operand': ('tok4', 0x04000241), 'target': None,
                 'flow': 'exec', 'error': None,
                 'text': 'ldflda field::RealFuels.Tanks.ModuleFuelTanks::'
                         'basemassConst'},
                {'offset': 0x6B, 'name': 'ldflda', 'size': 5,
                 'operand': ('tok4', 0x0400023F), 'target': None,
                 'flow': 'exec', 'error': None,
                 'text': 'ldflda field::RealFuels.Tanks.ModuleFuelTanks::'
                         'basemassPV'},
            ],
        },
        'get_total_mlilayers': {
            'decode_complete': True,
            'instructions': [
                {'name': 'ldarg.0', 'text': 'ldarg.0'},
                {'name': 'ldfld',
                 'text': 'ldfld field::RealFuels.Tanks.ModuleFuelTanks::'
                         'numberOfMLILayers'},
                {'name': 'call',
                 'text': 'call method::RealFuels.Tanks.ModuleFuelTanks::'
                         'get_numberOfAddedMLILayers'},
                {'name': 'add', 'text': 'add'},
                {'name': 'ret', 'text': 'ret'},
            ],
        },
        'get_number_of_added': {
            'decode_complete': True,
            'instructions': [
                {'name': 'ldarg.0', 'text': 'ldarg.0'},
                {'name': 'ldfld',
                 'text': 'ldfld field::RealFuels.Tanks.ModuleFuelTanks::'
                         '_numberOfAddedMLILayers'},
                {'name': 'conv.i4', 'text': 'conv.i4'},
                {'name': 'ret', 'text': 'ret'},
            ],
        },
        'calculate_mass_rf': {
            'decode_complete': True,
            'instructions': [
                {'name': 'ldfld',
                 'text': 'ldfld field::RealFuels.Tanks.ModuleFuelTanks::'
                         'MLIArealDensity'},
                {'name': 'ldfld',
                 'text': 'ldfld field::RealFuels.Tanks.ModuleFuelTanks::'
                         'totalTankArea'},
                {'name': 'call',
                 'text': 'call method::RealFuels.Tanks.ModuleFuelTanks::'
                         'get_totalMLILayers'},
                {'name': 'mul', 'text': 'mul'},
                {'name': 'add', 'text': 'add'},
            ],
        },
        'tank_definition_mlilayers': {
            'ServiceModule': {
                'value': 0, 'determined': True,
                'source': 'config_absent_and_no_il_writer',
                'reason': '测试夹具：ServiceModule 配置无 numberOfMLILayers'
                          ' 键，且无 IL 写入者',
            },
        },
        'tank_type': 'ServiceModule',
    }


class ProvenanceTests(unittest.TestCase):
    def test_hash_match(self):
        provenance = assembly_provenance({
            'name': 'RealChute', 'version': '1.4.9059.6703',
            'sha256': REALCHUTE_EXPECTED_SHA256})
        self.assertTrue(provenance['hash_match'])
        self.assertTrue(provenance['semantics_usable'])
        provenance = assembly_provenance({
            'name': 'RealFuels', 'version': '15.14.0',
            'sha256': REALFUELS_EXPECTED_SHA256})
        self.assertTrue(provenance['semantics_usable'])

    def test_hash_mismatch_rejects_semantics(self):
        provenance = assembly_provenance({
            'name': 'RealChute', 'version': '2.0.0',
            'sha256': 'A' * 64})
        self.assertFalse(provenance['hash_match'])
        self.assertFalse(provenance['semantics_usable'])

    def test_missing_evidence(self):
        provenance = assembly_provenance(None)
        self.assertFalse(provenance['semantics_usable'])
        self.assertEqual(provenance['status'], 'missing')

    def test_unknown_assembly_rejected(self):
        provenance = assembly_provenance({
            'name': 'Other', 'sha256': 'B' * 64})
        self.assertFalse(provenance['semantics_usable'])


class RealChuteChainTests(unittest.TestCase):
    def test_deployed_area_float32(self):
        area = chute_deployed_area(20.7)
        self.assertAlmostEqual(area, 336.5352478027344, places=4)
        self.assertEqual(chute_deployed_area(-1), None)
        self.assertEqual(chute_deployed_area('bad'), None)

    def test_chute_mass(self):
        mass = chute_mass(336.5352478027344, 0.0000565)
        self.assertAlmostEqual(mass, 0.01901424117386341, places=8)
        self.assertIsNone(chute_mass(None, 0.0000565))
        self.assertIsNone(chute_mass(1.0, -1))

    def test_mass_delta_formula(self):
        delta, diags = realchute_mass_delta(0.075, [0.01901424], 0.075)
        self.assertEqual(diags, [])
        self.assertAlmostEqual(delta, 0.01901424, places=6)
        delta, diags = realchute_mass_delta(None, [0.01901424], 0.075)
        self.assertIsNone(delta)
        self.assertIn('insufficient_realchute_evidence', diags)

    def test_float32_order_in_delta(self):
        delta, _ = realchute_mass_delta(0.075, [0.01901424, 0.0], 0.075)
        self.assertIsNotNone(delta)
        self.assertAlmostEqual(delta, 0.01901424, places=5)


class ModuleStateTests(unittest.TestCase):
    def test_ambiguous_realchute_state_rejected(self):
        part = chute_part()
        part['module_evidence']['RealChuteModule'] = [
            {'case_mass_raw': '0.075', 'case_mass': 0.075,
             'spare_chutes_raw': '5', 'spare_chutes': 5.0,
             'parachutes': []},
            {'case_mass_raw': '0.1', 'case_mass': 0.1,
             'spare_chutes_raw': '5', 'spare_chutes': 5.0,
             'parachutes': []},
        ]
        entry, reasons = _module_state(part, 'RealChuteModule')
        self.assertIsNone(entry)
        self.assertIn(AMBIGUOUS_MODULE_STATE, reasons)

    def test_procedural_chute_state_compared_by_chute_rows(self):
        part = chute_part()
        entry, reasons = _module_state(part, 'ProceduralChute')
        self.assertIsNotNone(entry)
        self.assertEqual(reasons, [])
        # differing CHUTE diameter -> ambiguous
        part['module_evidence']['ProceduralChute'] = [
            {'chutes': [{'dep_diam': 20.7, 'pre_dep_diam': 0.8,
                         'mass': 10.0, 'chute_count': 1.0,
                         'get_mass': True, 'use_dry': True}]},
            {'chutes': [{'dep_diam': 30.0, 'pre_dep_diam': 0.8,
                         'mass': 10.0, 'chute_count': 1.0,
                         'get_mass': True, 'use_dry': True}]},
        ]
        entry, reasons = _module_state(part, 'ProceduralChute')
        self.assertIsNone(entry)
        self.assertIn(AMBIGUOUS_MODULE_STATE, reasons)

    def test_missing_module_state(self):
        part = chute_part()
        del part['module_evidence']['ProceduralChute']
        entry, reasons = _module_state(part, 'ProceduralChute')
        self.assertIsNone(entry)
        self.assertEqual(reasons, ['missing_module_state'])


class RealChuteComponentTests(unittest.TestCase):
    def test_complete_evidence_from_config(self):
        index = default_index()
        draft = make_draft([chute_part()])
        evidence = realchute_component_evidence(draft, index)
        self.assertTrue(evidence['complete'])
        component = evidence['components'][0]
        self.assertEqual(component['case_mass_t'], 0.075000003)
        self.assertEqual(component['material'], 'Nylon')
        self.assertEqual(component['material_source'],
                         'patched_parachute_config')
        self.assertEqual(component['deployed_diameter_t'], 20.7)
        self.assertEqual(component['diameter_source'],
                         'craft_procedural_chute')
        self.assertEqual(component['prefab_part_mass_t'], 0.075)
        self.assertEqual(component['prefab_part_mass_source'],
                         'patched_part_mass')
        self.assertAlmostEqual(component['chute_mass_t'],
                               0.01901424, places=6)
        self.assertAlmostEqual(component['mass_delta_t'],
                               0.01901424, places=6)

    def test_no_material_hardcode(self):
        index = make_index(parachute_single={'material': None},
                           materials={'Nylon': 0.0000565})
        draft = make_draft([chute_part()])
        evidence = realchute_component_evidence(draft, index)
        self.assertFalse(evidence['complete'])
        self.assertEqual(evidence['components'], [])

    def test_missing_prefab_mass_is_insufficient(self):
        index = make_index(parachute_single={'mass': None},
                           materials={'Nylon': 0.0000565})
        draft = make_draft([chute_part()])
        evidence = realchute_component_evidence(draft, index)
        self.assertFalse(evidence['complete'])
        self.assertEqual(evidence['components'], [])

    def test_no_candidate_parts(self):
        index = default_index()
        draft = make_draft([pod_part()])
        evidence = realchute_component_evidence(draft, index)
        self.assertEqual(evidence['components'], [])
        self.assertTrue(evidence['complete'])

    def test_deterministic_and_json_serializable(self):
        index = default_index()
        draft = make_draft([chute_part()])
        first = realchute_component_evidence(draft, index)
        second = realchute_component_evidence(draft, index)
        self.assertEqual(first, second)
        json.dumps(first, ensure_ascii=False)


class MftDeterminationTests(unittest.TestCase):
    def _writers(self, rows, scan_complete=True):
        return {
            'writers': rows,
            'writer_count': len(rows),
            'method_bodies_total': 1 if rows else 0,
            'method_bodies_unparsed': 0,
            'method_bodies_decode_incomplete': 0,
            'scan_complete': scan_complete,
        }

    def _mli(self, evidence, added=0.0):
        from server.ksp_module_mass_semantics import mft_mli_term_evidence
        return mft_mli_term_evidence(evidence, added)

    def test_no_writers_clr_zero_init(self):
        result = mft_mass_is_additive_determination(
            {'writers': self._writers([], scan_complete=True)},
            -1.0, 0.0, 50.05)
        self.assertTrue(result['determined'])
        self.assertFalse(result['value'])
        self.assertEqual(result['source'], 'clr_zero_init_no_writers')

    def test_incomplete_writer_scan_blocks_zero_init(self):
        # a body that could not be read/decoded means "no writers" is NOT
        # conclusive — the CLR-zero-init conclusion must not be drawn
        result = mft_mass_is_additive_determination(
            {'writers': self._writers([], scan_complete=False)},
            -1.0, 0.0, 50.05)
        self.assertFalse(result['determined'])
        self.assertIsNone(result['value'])
        self.assertIn('writers_scan_incomplete', result['missing'])

    def test_zero_base_branch_determined_true(self):
        evidence = il_evidence_for_zero_base_branch()
        result = mft_mass_is_additive_determination(
            evidence, -1.0, 0.0, 50.05,
            mli_evidence=self._mli(evidence))
        self.assertTrue(result['determined'])
        self.assertTrue(result['value'])
        self.assertEqual(result['source'],
                         'calculate_mass_zero_base_branch')
        self.assertIn('已证明', ' '.join(result['reasons']))

    def test_zero_base_branch_positive_mass_insufficient(self):
        evidence = il_evidence_for_zero_base_branch()
        result = mft_mass_is_additive_determination(
            evidence, 0.4, 0.0, 50.05, mli_evidence=self._mli(evidence))
        self.assertFalse(result['determined'])
        self.assertIsNone(result['value'])

    def test_unproven_mli_blocks_determination(self):
        evidence = il_evidence_for_zero_base_branch()
        evidence.pop('get_total_mlilayers', None)
        result = mft_mass_is_additive_determination(
            evidence, -1.0, 0.0, 50.05,
            mli_evidence=self._mli(evidence))
        self.assertFalse(result['determined'])
        self.assertIn('get_total_mlilayers_shape', result['missing'])

    def test_nonzero_added_layers_blocks_determination(self):
        evidence = il_evidence_for_zero_base_branch()
        result = mft_mass_is_additive_determination(
            evidence, -1.0, 0.0, 50.05,
            mli_evidence=self._mli(evidence, added=2.0))
        self.assertFalse(result['determined'])
        self.assertIn('mli_layers_nonzero', result['missing'])

    def test_constructor_constant_write_wins(self):
        evidence = il_evidence_for_zero_base_branch()
        evidence['writers'] = self._writers([{
            'type': 'RealFuels.Tanks.ModuleFuelTanks',
            'method': '.ctor', 'token': '0x06000348',
            'writes': [{'offset': 10, 'opcode': 'stfld',
                        'constant_bool': False,
                        'constant_source_instruction': 9}],
        }])
        result = mft_mass_is_additive_determination(
            evidence, -1.0, 0.0, 50.05)
        self.assertTrue(result['determined'])
        self.assertFalse(result['value'])
        self.assertEqual(result['source'], 'constructor_constant_write')

    def test_missing_numbers_insufficient(self):
        evidence = il_evidence_for_zero_base_branch()
        result = mft_mass_is_additive_determination(
            evidence, None, 0.0, 50.05, mli_evidence=self._mli(evidence))
        self.assertFalse(result['determined'])
        self.assertIn('basemass_const', result['missing'])

    def test_mli_term_evidence_proves_zero(self):
        from server.ksp_module_mass_semantics import mft_mli_term_evidence
        evidence = il_evidence_for_zero_base_branch()
        result = mft_mli_term_evidence(evidence, 0.0)
        self.assertTrue(result['proven'])
        self.assertEqual(result['mli_term'], 0.0)
        self.assertEqual(result['missing'], [])

    def test_mli_term_none_means_unknown(self):
        from server.ksp_module_mass_semantics import mft_mli_term_evidence
        evidence = il_evidence_for_zero_base_branch()
        evidence['tank_definition_mlilaries'] = None  # typo key: ignored
        result = mft_mli_term_evidence(evidence, None)
        self.assertFalse(result['proven'])
        self.assertIsNone(result['mli_term'])
        self.assertIn('added_mlilayers', result['missing'])

    def test_parse_base_mass_plain_value(self):
        evidence = il_evidence_for_zero_base_branch()
        values = parse_base_mass_values(evidence['parse_base_mass'], '-1')
        self.assertEqual(values,
                         {'basemass_const': -1.0, 'basemass_pv': 0.0,
                          'shape': 'plain_value'})

    def test_parse_base_mass_division_form(self):
        evidence = il_evidence_for_zero_base_branch()
        values = parse_base_mass_values(evidence['parse_base_mass'],
                                        '0.2/50')
        self.assertEqual(values,
                         {'basemass_const': 0.0, 'basemass_pv': 0.004,
                          'shape': 'pv_division'})

    def test_parse_base_mass_rejects_wrong_il_shape(self):
        values = parse_base_mass_values({'instructions': []}, '-1')
        self.assertIsNone(values)


class ModuleFuelTanksTests(unittest.TestCase):
    def test_determined_from_il_evidence(self):
        index = default_index()
        draft = make_draft([pod_part()])
        evidence = mft_component_evidence(
            draft, index, il_evidence=il_evidence_for_zero_base_branch())
        self.assertTrue(evidence['determined'])
        component = evidence['components'][0]
        self.assertTrue(component['mass_is_additive'])
        self.assertEqual(component['mass_is_additive_source'],
                         'calculate_mass_zero_base_branch')
        self.assertEqual(component['saved_mass_t'], 0.0)
        self.assertEqual(component['prefab_part_mass_t'], 0.4)
        self.assertEqual(component['get_module_mass_t'], 0.0)
        self.assertTrue(component['evidence_complete'])

    def test_no_il_evidence_is_insufficient(self):
        index = default_index()
        draft = make_draft([pod_part()])
        evidence = mft_component_evidence(draft, index)
        component = evidence['components'][0]
        self.assertFalse(component['evidence_complete'])
        self.assertIn('mass_is_additive', component['evidence_missing'])
        self.assertIsNone(component['get_module_mass_t'])

    def test_craft_state_value_wins(self):
        index = default_index()
        draft = make_draft([pod_part(additive=False)])
        evidence = mft_component_evidence(draft, index)
        component = evidence['components'][0]
        self.assertFalse(component['mass_is_additive'])
        self.assertEqual(component['mass_is_additive_source'],
                         'craft_module_state')
        self.assertEqual(component['get_module_mass_t'], -0.4)

    def test_config_value_used_when_no_craft_value(self):
        index = make_index(parachute_single={},
                           mk1pod_v2={'mass_is_additive': True,
                                      'mass_is_additive_raw': 'True'},
                           materials={'Nylon': 0.0000565})
        draft = make_draft([pod_part(additive=None)])
        evidence = mft_component_evidence(draft, index)
        component = evidence['components'][0]
        self.assertTrue(component['mass_is_additive'])
        self.assertEqual(component['mass_is_additive_source'],
                         'patched_config')
        self.assertEqual(component['get_module_mass_t'], 0.0)

    def test_ambiguous_mft_state(self):
        index = default_index()
        draft = make_draft([pod_part()])
        draft['parts'][0]['module_evidence']['ModuleFuelTanks'] = [
            {'mass_raw': '0', 'mass': 0.0, 'volume_raw': '50.05',
             'volume': 50.05, 'basemass_raw': None, 'basemass': None,
             'mass_is_additive_raw': None, 'mass_is_additive': None},
            {'mass_raw': '0', 'mass': 0.0, 'volume_raw': '60.05',
             'volume': 60.05, 'basemass_raw': None, 'basemass': None,
             'mass_is_additive_raw': None, 'mass_is_additive': None},
        ]
        evidence = mft_component_evidence(
            draft, index, il_evidence=il_evidence_for_zero_base_branch())
        self.assertEqual(evidence['components'], [])
        self.assertTrue(any(d['code'] == AMBIGUOUS_MODULE_STATE
                            for d in evidence['diagnostics']))

    def test_inputs_not_modified_and_json_serializable(self):
        index = default_index()
        draft = make_draft([pod_part()])
        before_draft = copy.deepcopy(draft)
        before_index = index.to_dict()
        evidence = mft_component_evidence(
            draft, index, il_evidence=il_evidence_for_zero_base_branch())
        json.dumps(evidence, ensure_ascii=False)
        self.assertEqual(draft, before_draft)
        self.assertEqual(index.to_dict(), before_index)


class HypothesisTests(unittest.TestCase):
    def _rc_evidence(self, delta, components=None):
        return {'components': components if components is not None
                else [{'mass_delta_t': delta}],
                'total_mass_delta_t': delta, 'complete': True,
                'diagnostics': []}

    def _mft_evidence(self, modifier=0.0, complete=True):
        return {'components': [{'get_module_mass_t': modifier,
                                'evidence_complete': complete,
                                'evidence_missing': []}],
                'determined': complete,
                'diagnostics': []}

    def _no_candidates(self):
        return {'components': [], 'determined': False, 'diagnostics': []}

    def _empty_draft(self):
        return make_draft([])

    def _chute_draft(self):
        return make_draft([chute_part()])

    def _pod_draft(self):
        return make_draft([pod_part()])

    def test_new_symbol_convention(self):
        hypotheses = evaluate_hypotheses(
            {}, self._rc_evidence(0.01901424),
            self._mft_evidence(0.0),
            float32_total=10.0, source_total_mass_t=9.925,
            pdc_diff_total=0.0, craft_draft=self._chute_draft())
        # residual = estimated - loadmeta = 0.075
        self.assertAlmostEqual(
            hypotheses['baseline_residual_t'], 0.075, places=4)
        h2 = hypotheses['hypotheses']['realchute_prefab_modifier']
        for key in ('applicable', 'predicted_mass_delta_t',
                    'baseline_residual_t', 'predicted_residual_t',
                    'evidence_complete', 'evidence_missing', 'verdict'):
            self.assertIn(key, h2)
        self.assertAlmostEqual(h2['predicted_mass_delta_t'],
                               0.01901424, places=4)
        self.assertAlmostEqual(h2['predicted_residual_t'],
                               0.09401424, places=4)
        self.assertEqual(h2['verdict'], CONTRADICTED)

    def test_supported_when_predicted_residual_closes(self):
        hypotheses = evaluate_hypotheses(
            {}, self._rc_evidence(-0.075),
            self._mft_evidence(0.0),
            float32_total=10.0, source_total_mass_t=9.925,
            pdc_diff_total=0.0, craft_draft=self._chute_draft())
        h2 = hypotheses['hypotheses']['realchute_prefab_modifier']
        self.assertEqual(h2['verdict'], SUPPORTED)
        self.assertAlmostEqual(h2['predicted_residual_t'], 0.0, places=6)

    def test_missing_pdc_diff_keeps_baseline_missing(self):
        hypotheses = evaluate_hypotheses(
            {}, self._rc_evidence(0.01901424),
            self._mft_evidence(0.0),
            float32_total=10.0, source_total_mass_t=9.925,
            pdc_diff_total=None, craft_draft=self._chute_draft())
        self.assertIsNone(hypotheses['baseline_residual_t'])
        self.assertIn('pdc_diff_total',
                      hypotheses['baseline_evidence_missing'])
        # applicable hypotheses keep their missing baseline (insufficient);
        # non-applicable ones stay not_applicable
        for name in ('parachute_base_mass_only',
                     'realchute_prefab_modifier'):
            h = hypotheses['hypotheses'][name]
            self.assertIsNone(h['predicted_residual_t'])
            self.assertEqual(h['verdict'], INSUFFICIENT_EVIDENCE, name)
        for name in ('mk1pod_module_fuel_tanks_modifier',
                     'combined_realchute_and_mft_semantics'):
            h = hypotheses['hypotheses'][name]
            self.assertEqual(h['verdict'], NOT_APPLICABLE, name)

    def test_no_candidates_all_not_applicable(self):
        hypotheses = evaluate_hypotheses(
            {}, self._rc_evidence(0.0, components=[]),
            self._no_candidates(),
            float32_total=10.0, source_total_mass_t=9.925,
            pdc_diff_total=0.0, craft_draft=self._empty_draft())
        self.assertEqual(hypotheses['candidate_parts']
                         ['realchute_parachute_single'], 0)
        self.assertEqual(hypotheses['candidate_parts']
                         ['mk1pod_v2_module_fuel_tanks'], 0)
        for name, h in hypotheses['hypotheses'].items():
            self.assertFalse(h['applicable'])
            self.assertEqual(h['verdict'], NOT_APPLICABLE, name)

    def test_negative_control_never_supported(self):
        hypotheses = evaluate_hypotheses(
            {}, self._rc_evidence(-0.075, components=[]),
            self._no_candidates(),
            float32_total=10.0, source_total_mass_t=9.925,
            pdc_diff_total=0.0, craft_draft=self._empty_draft())
        for h in hypotheses['hypotheses'].values():
            self.assertEqual(h['verdict'], NOT_APPLICABLE)

    def test_candidate_with_missing_module_state_is_insufficient(self):
        # the craft CONTAINS the candidate part but its module state is
        # missing (empty RealChuteModule entries): applicable=true,
        # evidence incomplete -> insufficient, never not_applicable
        part = chute_part()
        part['module_evidence']['RealChuteModule'] = []
        draft = make_draft([part])
        hypotheses = evaluate_hypotheses(
            {}, self._rc_evidence(0.0, components=[]),
            self._no_candidates(),
            float32_total=10.0, source_total_mass_t=9.925,
            pdc_diff_total=0.0, craft_draft=draft)
        self.assertEqual(hypotheses['candidate_parts']
                         ['realchute_parachute_single'], 1)
        for name in ('parachute_base_mass_only',
                     'realchute_prefab_modifier'):
            h = hypotheses['hypotheses'][name]
            self.assertTrue(h['applicable'])
            self.assertFalse(h['evidence_complete'])
            self.assertEqual(h['verdict'], INSUFFICIENT_EVIDENCE, name)

    def test_candidate_with_missing_config_evidence_is_insufficient(self):
        # the craft has full module state but the index lacks material /
        # prefab evidence: RealChute components stay empty and the
        # hypotheses are applicable-but-incomplete
        draft = make_draft([chute_part()])
        hypotheses = evaluate_hypotheses(
            {}, self._rc_evidence(0.0, components=[]),
            self._no_candidates(),
            float32_total=10.0, source_total_mass_t=9.925,
            pdc_diff_total=0.0, craft_draft=draft)
        h = hypotheses['hypotheses']['realchute_prefab_modifier']
        self.assertTrue(h['applicable'])
        self.assertFalse(h['evidence_complete'])
        self.assertEqual(h['verdict'], INSUFFICIENT_EVIDENCE)

    def test_two_mft_components_summed(self):
        draft = make_draft([pod_part(mass=0.0, additive=True),
                            pod_part(mass=0.1, additive=True)])
        hypotheses = evaluate_hypotheses(
            {}, self._rc_evidence(0.0, components=[]),
            {'components': [
                {'get_module_mass_t': 0.0, 'evidence_complete': True,
                 'evidence_missing': []},
                {'get_module_mass_t': 0.1, 'evidence_complete': True,
                 'evidence_missing': []},
            ], 'determined': True, 'diagnostics': []},
            float32_total=10.0, source_total_mass_t=9.925,
            pdc_diff_total=0.0, craft_draft=draft)
        self.assertEqual(hypotheses['candidate_parts']
                         ['mk1pod_v2_module_fuel_tanks'], 2)
        h3 = hypotheses['hypotheses'][
            'mk1pod_module_fuel_tanks_modifier']
        self.assertAlmostEqual(h3['predicted_mass_delta_t'], 0.1)
        self.assertTrue(h3['evidence_complete'])

    def test_one_complete_one_incomplete_mft_component(self):
        # a single incomplete component keeps the whole MFT hypothesis
        # incomplete — the first component must never be used alone
        draft = make_draft([pod_part(mass=0.0, additive=True),
                            pod_part(mass=0.0, additive=None)])
        hypotheses = evaluate_hypotheses(
            {}, self._rc_evidence(0.0, components=[]),
            {'components': [
                {'get_module_mass_t': 0.0, 'evidence_complete': True,
                 'evidence_missing': []},
                {'get_module_mass_t': None, 'evidence_complete': False,
                 'evidence_missing': ['mass_is_additive']},
            ], 'determined': False, 'diagnostics': []},
            float32_total=10.0, source_total_mass_t=9.925,
            pdc_diff_total=0.0, craft_draft=draft)
        h3 = hypotheses['hypotheses'][
            'mk1pod_module_fuel_tanks_modifier']
        self.assertTrue(h3['applicable'])
        self.assertFalse(h3['evidence_complete'])
        self.assertIsNone(h3['predicted_mass_delta_t'])
        self.assertEqual(h3['verdict'], INSUFFICIENT_EVIDENCE)

    def test_combined_hypothesis_needs_both_candidates(self):
        # only the chute candidate exists -> combined is not applicable
        hypotheses = evaluate_hypotheses(
            {}, self._rc_evidence(0.01901424),
            self._no_candidates(),
            float32_total=10.0, source_total_mass_t=9.925,
            pdc_diff_total=0.0, craft_draft=self._chute_draft())
        combined = hypotheses['hypotheses'][
            'combined_realchute_and_mft_semantics']
        self.assertFalse(combined['applicable'])
        self.assertEqual(combined['verdict'], NOT_APPLICABLE)

    def test_deterministic(self):
        first = evaluate_hypotheses({}, self._rc_evidence(0.02),
                                    self._mft_evidence(0.0), 10.0, 9.9,
                                    0.0, craft_draft=self._chute_draft())
        second = evaluate_hypotheses({}, self._rc_evidence(0.02),
                                     self._mft_evidence(0.0), 10.0, 9.9,
                                     0.0, craft_draft=self._chute_draft())
        self.assertEqual(first, second)
        json.dumps(first, ensure_ascii=False)


@unittest.skipUnless(REAL_CACHE.is_file(),
                     'real KSP install not present on this machine')
class RealSpecialVehicleTests(unittest.TestCase):
    """Real three special vehicles + negative control (6B.3C.2B-R)."""

    @classmethod
    def setUpClass(cls):
        from server.ksp_install_index import build_ksp_index
        from tools.research.audit_ksp_mass_residuals import _mft_il_evidence
        cls.index = build_ksp_index(REAL_KSP)
        cls.mft_il_evidence = _mft_il_evidence(REAL_KSP,
                                               vab_dir='VAB')

    def _evaluate(self, craft_name):
        from server.import_service import _loadmeta_metadata
        from server.ksp_craft_mass_reconciliation import (
            reconcile_craft_mass)
        from server.ksp_mass_evidence import extract_part_mass_evidence
        from server.ksp_mass_forensics import analyze_vehicle_forensics
        from server.ksp_numeric_precision_forensics import (
            evaluate_five_models)
        from server.ksp_part_matcher import match_craft_parts
        from server.ksp_runtime_fingerprints import ksp_runtime_provenance
        from server.vab_parser import parse_craft
        index = self.index
        provenance = ksp_runtime_provenance(REAL_KSP)
        path = Path('VAB') / craft_name
        draft = parse_craft(path.read_text(encoding='utf-8-sig'),
                            path.name)
        evidence = extract_part_mass_evidence(
            draft, index, match_craft_parts(draft, index),
            provenance=provenance)
        loadmeta = None
        lm = path.with_suffix('.loadmeta')
        if lm.is_file():
            loadmeta = _loadmeta_metadata(
                lm.read_text(encoding='utf-8-sig', errors='replace'))
        rec = reconcile_craft_mass(draft, evidence, loadmeta)
        fo = analyze_vehicle_forensics(draft, evidence, rec,
                                       ksp_index=index)
        numeric = evaluate_five_models(
            draft, evidence, rec,
            pdc_diff_total=fo.get('pdc_candidate_diff_total_t'))
        rc = realchute_component_evidence(draft, index)
        mft = mft_component_evidence(draft, index,
                                     il_evidence=self.mft_il_evidence)
        hypotheses = evaluate_hypotheses(
            fo, rc, mft,
            numeric['models']['float32_craft_order']['estimated_total_t'],
            rec['source_total_mass_t'],
            fo.get('pdc_candidate_diff_total_t'),
            craft_draft=draft)
        return {
            'craft': craft_name,
            'rc': rc,
            'mft': mft,
            'hypotheses': hypotheses,
            'chute_mass_t': rc['total_chute_mass_t'],
            'reconciliation_status': rec['reconciliation_status'],
            'parachute_evidence': next(
                (i for i in evidence['instances']
                 if i.get('canonical_name') == 'parachuteSingle'),
                None),
        }

    def test_three_special_vehicles_consistent(self):
        results = [self._evaluate(name) for name in
                   ('1_2T Test.craft', '1_5T Test.craft',
                    '2_5T Test.craft')]
        for result in results:
            self.assertTrue(result['rc']['complete'], result['craft'])
            self.assertAlmostEqual(result['chute_mass_t'],
                                   0.01901424, places=5)
            # MFT is DETERMINED from static IL only: the writers scan is
            # complete, CalculateMass decodes fully, the MLI term is
            # PROVEN zero (craft _numberOfAddedMLILayers=0 + ServiceModule
            # base layers 0) and the negative-base-mass branch executes:
            # massIsAdditive=true, saved mass = 0 -> modifier = 0.0
            component = result['mft']['components'][0]
            self.assertTrue(result['mft']['determined'], result['craft'])
            self.assertTrue(component['mass_is_additive'], result['craft'])
            self.assertEqual(component['mass_is_additive_source'],
                             'calculate_mass_zero_base_branch',
                             result['craft'])
            self.assertEqual(component['added_mlilayers_t'], 0.0,
                             result['craft'])
            self.assertEqual(component['get_module_mass_t'], 0.0,
                             result['craft'])
            self.assertEqual(component['evidence_missing'], [],
                             result['craft'])
            self.assertEqual(
                result['hypotheses']['candidate_parts']
                ['mk1pod_v2_module_fuel_tanks'], 1, result['craft'])
            # production gate (6B.3C.4A): these OLD VAB crafts keep their
            # parachutes in Automatic mode (calcSelect=True), which is OUT
            # of the authorized scope -> the production evidence chain must
            # fail closed (dynamic_unresolved, no estimate), never fall
            # back to base mass or base + saved modMass
            parachute = result['parachute_evidence']
            self.assertIsNotNone(parachute, result['craft'])
            self.assertEqual(parachute['resolution_status'],
                             'dynamic_unresolved', result['craft'])
            self.assertIsNone(parachute['static_dry_mass_estimate_t'],
                              result['craft'])
            semantics = parachute['mass_semantics_evidence']
            codes = [d['code'] for d in semantics['diagnostics']]
            if 'realchute_fingerprint_mismatch' in codes:
                # The live install may no longer match the archived cache.
                # Fingerprint refusal correctly precedes domain validation.
                self.assertEqual(semantics['status'], 'dynamic_unresolved', result['craft'])
            else:
                self.assertEqual(semantics['status'], 'unsupported_domain', result['craft'])
                self.assertIn('realchute_scope_unsupported', codes, result['craft'])
            self.assertEqual(result['reconciliation_status'],
                             'evidence_incomplete', result['craft'])
            # without a parachute mass estimate the numeric models cannot
            # produce a baseline -> the forensic hypotheses are
            # insufficient_evidence (never contradicted/supported)
            hypotheses = result['hypotheses']
            self.assertIsNone(hypotheses['baseline_residual_t'],
                              result['craft'])
            for hypothesis in hypotheses['hypotheses'].values():
                self.assertEqual(hypothesis['verdict'],
                                 INSUFFICIENT_EVIDENCE, result['craft'])
                self.assertFalse(hypothesis['evidence_complete'],
                                 result['craft'])

    def test_negative_control_all_not_applicable(self):
        from server.import_service import _loadmeta_metadata
        from server.ksp_craft_mass_reconciliation import (
            reconcile_craft_mass)
        from server.ksp_mass_evidence import extract_part_mass_evidence
        from server.ksp_mass_forensics import analyze_vehicle_forensics
        from server.ksp_numeric_precision_forensics import (
            evaluate_five_models)
        from server.ksp_part_matcher import match_craft_parts
        from server.vab_parser import parse_craft
        index = self.index
        path = Path('VAB') / 'Electron I 2112XL.craft'
        draft = parse_craft(path.read_text(encoding='utf-8-sig'),
                            path.name)
        evidence = extract_part_mass_evidence(
            draft, index, match_craft_parts(draft, index))
        lm = path.with_suffix('.loadmeta')
        loadmeta = _loadmeta_metadata(
            lm.read_text(encoding='utf-8-sig', errors='replace'))
        rec = reconcile_craft_mass(draft, evidence, loadmeta)
        fo = analyze_vehicle_forensics(draft, evidence, rec,
                                       ksp_index=index)
        rc = realchute_component_evidence(draft, index)
        mft = mft_component_evidence(draft, index,
                                     il_evidence=self.mft_il_evidence)
        self.assertEqual(rc['components'], [])
        self.assertEqual(mft['components'], [])
        hypotheses = evaluate_hypotheses(
            fo, rc, mft,
            fo.get('residual_after_pdc_t', 0.0), 0.0, None,
            craft_draft=draft)
        for name, hypothesis in hypotheses['hypotheses'].items():
            self.assertEqual(hypothesis['verdict'], NOT_APPLICABLE, name)
        self.assertEqual(hypotheses['candidate_parts']
                         ['realchute_parachute_single'], 0)
        self.assertEqual(hypotheses['candidate_parts']
                         ['mk1pod_v2_module_fuel_tanks'], 0)
        # float32 residual after PDC stays within the forensic tolerance
        numeric = evaluate_five_models(
            draft, evidence, rec,
            pdc_diff_total=fo.get('pdc_candidate_diff_total_t'))
        after = numeric['models']['float32_craft_order'][
            'residual_after_pdc_t']
        self.assertLessEqual(abs(after), 5e-5)


if __name__ == '__main__':
    unittest.main()
