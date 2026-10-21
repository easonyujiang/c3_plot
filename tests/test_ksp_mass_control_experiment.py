"""
Tests for the KSP controlled mass experiment tooling (6B.3C.3A-R).
====================================================================

All fixtures here are SYNTHETIC: they are generated at test runtime in
temporary directories, every craft/loadmeta body carries a ``// SYNTHETIC
FIXTURE - not real KSP data`` marker, and no result produced from them is
ever presented as a real KSP conclusion.  The synthetic KSP root contains
dummy byte files whose SHA-256 fingerprints are used only to exercise the
fingerprint-matching logic.

The float32 expectations in this file are computed with an INDEPENDENT
inline IEEE-754 float32 helper (not the module under test).
"""

import contextlib
import io
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path

from tools.research.control_mass_experiment import (
    CRAFT_NAMES,
    EXIT_INVALID,
    SCHEMA_VERSION,
    SERIALIZATION_MODEL,
    STATUS_ANALYZED,
    STATUS_AWAITING,
    STATUS_INVALID,
    TOLERANCES,
    VERDICT_CONTRADICTED,
    VERDICT_INSUFFICIENT,
    VERDICT_SUPPORTED,
    _binary32_analysis,
    _core_summary_sha256,
    _experiment_attachment_plan,
    analyze_experiment,
    main,
    preflight,
    prepare_experiment,
)

SYNTHETIC_MARKER = '// SYNTHETIC FIXTURE - not real KSP data'


def _f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def _chute_mass_expectation(diameter, density=0.0000565):
    area = _f32(3.141592653589793 * diameter * diameter / 4.0)
    return _f32(area * density)


def _craft_text(name, root='probeCoreHex', chute_diameters=(),
                case_mass=0.075000003, pre_dep=0.8, version='1.12.5',
                sym_chute_indices=(), chute_mass=10.0,
                chute_attachments=None, core_attn=(),
                chute_calc_select=(), chute_mod_masses=()):
    """Synthetic craft.  ``chute_attachments`` maps each chute to
    ``(target_part_name, target_node_index)`` or None (no attN row);
    ``core_attn`` are the CORE's own attN rows as ``(core_node_name,
    target_part_name)``; ``chute_calc_select`` / ``chute_mod_masses`` are
    per-chute values for the saved design/runtime fields."""
    lines = [
        f'ship = {name}',
        f'version = {version}',
        SYNTHETIC_MARKER,
        'PART',
        '{',
        f'\tpart = {root}_1',
        '\tistg = 0',
    ]
    for core_node, target in core_attn:
        lines.append(
            f'\tattN = {core_node},{target}_0|0|0|0_0|0|0_0|0|0')
    lines += [
        '\tMODULE',
        '\t{',
        '\t\tname = ModuleCommand',
        '\t}',
        '}',
    ]
    for index, diameter in enumerate(chute_diameters):
        lines += [
            'PART',
            '{',
            f'\tpart = parachuteSingle_{index + 2}',
            '\tistg = 0',
        ]
        if index < len(chute_mod_masses) and \
                chute_mod_masses[index] is not None:
            lines.append(f'\tmodMass = {chute_mod_masses[index]}')
        if index in sym_chute_indices:
            lines.append('\tsym = 0xDEADBEEF01')
        if chute_attachments is not None and \
                index < len(chute_attachments) and \
                chute_attachments[index] is not None:
            target_part, node_index = chute_attachments[index]
            lines.append(
                f'\tattN = bottom,{target_part}_{node_index}'
                f'|0|-0.0150811253|0_0|-0.125|0_0|-0.0150811253'
                f'|0_0|-0.125|0')
        lines += [
            '\tMODULE',
            '\t{',
            '\t\tname = RealChuteModule',
            f'\t\tcaseMass = {case_mass}',
            '\t\tspareChutes = 5',
            '\t}',
            '\tMODULE',
            '\t{',
            '\t\tname = ProceduralChute',
            '\t\tCHUTE',
            '\t\t{',
            '\t\t\tname = TestChute',
            '\t\t\tcalcSelect = '
            + str(bool(chute_calc_select[index])
                   if index < len(chute_calc_select)
                   else False),
            '\t\t\tgetMass = True',
            '\t\t\tuseDry = True',
            f'\t\t\tmass = {chute_mass}',
            '\t\t\tchuteCount = 1',
            f'\t\t\tdepDiam = {diameter}',
            f'\t\t\tpreDepDiam = {pre_dep}',
            '\t\t}',
            '\t}',
            '}',
        ]
    return '\n'.join(lines) + '\n'


def _loadmeta_text(name, total_mass, part_count, part_names,
                   version='1.12.5', ship_name=None, loadmeta_type='VAB'):
    lines = [
        f'shipName = {ship_name if ship_name is not None else name}',
        SYNTHETIC_MARKER,
        f'version = {version}',
        f'partCount = {part_count}',
    ]
    for part_name in part_names:
        lines.append(f'partNames = {part_name}')
    lines.append(f'totalMass = {total_mass}')
    lines.append(f'type = {loadmeta_type}')
    return '\n'.join(lines) + '\n'


def _write_fixture(experiment_dir, totals, chute_diameters,
                   root='probeCoreHex', case_mass=0.075000003,
                   variant=None, version='1.12.5'):
    """Write the 8 synthetic files.  ``variant`` selects contamination for
    invalid-design tests:
    * 'root_mismatch'      M0 uses a different root part (not the core);
    * 'extra_part'         M1 gains an extra fuel tank part;
    * 'extra_resource'     M0's core carries an extra RESOURCE;
    * 'wrong_chute_counts' M1 has two chutes, M2 has one;
    * 'm3_extra_change'    M3 changes caseMass instead of only diameter;
    * 'm3_mass_field'      M3 changes the saved CHUTE mass field;
    * 'm3_same_diameter'   M3 keeps the default diameter;
    * 'no_chute'           M1/M3 have no parachute at all;
    * 'symmetric'          M2's chutes carry sym connections;
    * 'core_mod_mass'      M1's control core has a modMass;
    * 'wrong_ship_name'    M1 craft internal ship name is wrong;
    * 'wrong_partcount'    M1 loadmeta partCount is wrong;
    * 'wrong_partnames'    M1 loadmeta partNames is wrong;
    * 'wrong_type'         M1 loadmeta type is not VAB;
    * 'wrong_version'      M1 craft/loadmeta version differs;
    * 'renamed_loadmeta'   M1.loadmeta is another ship's loadmeta.
    """
    input_dir = Path(experiment_dir) / 'input'
    input_dir.mkdir(parents=True, exist_ok=True)
    m0_root = root
    m0_diameters = ()
    m1_diameters = (chute_diameters[0],)
    m2_diameters = (chute_diameters[0], chute_diameters[0])
    m3_diameters = (chute_diameters[1],)
    sym_indices = ()
    m1_chute_mass = 10.0
    m3_chute_mass = 10.0
    m1_core_mod_mass = None
    # synthetic core ordered nodes: [bottom(0), top(1)] — top = index 1
    m1_attachments = ((f'{root}_1', 1),)
    m2_attachments = ((f'{root}_1', 1), (f'{root}_1', 0))
    m3_attachments = ((f'{root}_1', 1),)
    if variant == 'root_mismatch':
        m0_root = 'probeCoreHex2'
    if variant == 'wrong_chute_counts':
        m1_diameters = (chute_diameters[0], chute_diameters[0])
        m2_diameters = (chute_diameters[0],)
        m1_attachments = ((f'{root}_1', 1), (f'{root}_1', 0))
        m2_attachments = ((f'{root}_1', 1),)
    if variant == 'no_chute':
        m1_diameters = ()
        m3_diameters = ()
        m1_attachments = ()
        m3_attachments = ()
    if variant == 'm3_same_diameter':
        m3_diameters = (chute_diameters[0],)
    if variant == 'symmetric':
        sym_indices = (0, 1)
    if variant == 'core_mod_mass':
        m1_core_mod_mass = 0.05
    if variant == 'm3_mass_field':
        m3_chute_mass = 11.0
    if variant == 'daisy_chain':
        # M2's second chute attaches to the FIRST chute (forbidden)
        m2_attachments = ((f'{root}_1', 1), ('parachuteSingle_2', 0))
    if variant == 'no_version':
        # M1 craft + loadmeta lack the version field entirely
        pass

    m0_core_attn = (('bottom', 'Null'), ('top', 'Null'))
    m1_core_attn = (('top', 'parachuteSingle_2'),)
    m2_core_attn = (('bottom', 'parachuteSingle_3'),
                    ('top', 'parachuteSingle_2'))
    m3_core_attn = (('top', 'parachuteSingle_2'),)
    m1_calc = (False,)
    m2_calc = (False, False)
    m3_calc = (False,)
    m1_chute_mod = (3.7252903e-09,)
    m2_chute_mod = (0, 0)
    m3_chute_mod = (None,)
    if variant == 'daisy_chain':
        m2_core_attn = (('top', 'parachuteSingle_2'),)
    if variant == 'm2_automatic':
        m2_calc = (True, True)
    if variant == 'm3_runtime_mod_mass':
        m3_chute_mod = (0.0087,)
    if variant == 'duplicate_core_node':
        m2_core_attn = (('top', 'parachuteSingle_2'),
                        ('top', 'parachuteSingle_3'))
    if variant == 'wrong_chute_counts':
        m1_core_attn = (('bottom', 'parachuteSingle_3'),
                        ('top', 'parachuteSingle_2'))
        m2_core_attn = (('top', 'parachuteSingle_2'),)
    if variant == 'no_chute':
        m1_core_attn = ()
        m3_core_attn = ()

    def craft_file(name, diameters, root_part, case, chute_mass_value,
                   core_mod_mass, sym, attachments, core_attn,
                   calc_select, chute_mod):
        text = _craft_text(
            name, root=root_part, chute_diameters=diameters,
            case_mass=case, sym_chute_indices=sym,
            chute_mass=chute_mass_value, chute_attachments=attachments,
            core_attn=core_attn, chute_calc_select=calc_select,
            chute_mod_masses=chute_mod)
        if variant == 'no_version' and name == \
                'M1_one_chute_default':
            text = text.replace('version = 1.12.5\n', '', 1)
        if core_mod_mass is not None:
            text = text.replace('\tistg = 0\n',
                                f'\tistg = 0\n\tmodMass = {core_mod_mass}\n',
                                1)
        if variant == 'wrong_ship_name' and name == \
                'M1_one_chute_default':
            text = text.replace(f'ship = {name}', 'ship = OtherShip', 1)
        if variant == 'wrong_version' and name == \
                'M1_one_chute_default':
            text = text.replace('version = 1.12.5', 'version = 1.11.0', 1)
        (input_dir / f'{name}.craft').write_text(text, encoding='utf-8')

    craft_file('M0_control', m0_diameters, m0_root, case_mass, 10.0,
               None, (), (), m0_core_attn, (), ())
    craft_file('M1_one_chute_default', m1_diameters, root, case_mass,
               m1_chute_mass, m1_core_mod_mass, (), m1_attachments,
               m1_core_attn, m1_calc, m1_chute_mod)
    craft_file('M2_two_chutes_default', m2_diameters, root, case_mass,
               10.0, None, sym_indices, m2_attachments, m2_core_attn,
               m2_calc, m2_chute_mod)
    craft_file('M3_one_chute_changed_diameter', m3_diameters, root,
               case_mass if variant != 'm3_extra_change'
               else case_mass + 0.01, m3_chute_mass, None, (),
               m3_attachments, m3_core_attn, m3_calc, m3_chute_mod)

    if variant == 'extra_part':
        text = (input_dir / 'M1_one_chute_default.craft').read_text(
            encoding='utf-8')
        text += ('PART\n{\n\tpart = fuelTankSmall_9\n\tistg = 0\n'
                 '\tRESOURCE\n\t{\n\t\tname = LiquidFuel\n'
                 '\t\tamount = 0\n\t\tmaxAmount = 0\n\t}\n}\n')
        (input_dir / 'M1_one_chute_default.craft').write_text(
            text, encoding='utf-8')

    if variant == 'extra_resource':
        text = (input_dir / 'M0_control.craft').read_text(encoding='utf-8')
        text = text.replace(
            '\t}\n}\n',
            '\t}\n\tRESOURCE\n\t{\n\t\tname = ElectricCharge\n'
            '\t\tamount = 5\n\t\tmaxAmount = 5\n\t}\n}\n', 1)
        (input_dir / 'M0_control.craft').write_text(text, encoding='utf-8')

    from server.vab_parser import parse_craft
    count_of = {}
    names_of = {}
    for name in CRAFT_NAMES:
        draft = parse_craft(
            (input_dir / f'{name}.craft').read_text(
                encoding='utf-8-sig'), f'{name}.craft')
        count_of[name] = len(draft['parts'])
        names_of[name] = sorted(
            {part.get('base_part') for part in draft['parts']})

    for name in CRAFT_NAMES:
        lm_version = '1.11.0' if (variant == 'wrong_version'
                                  and name == 'M1_one_chute_default') \
            else version
        lm_type = 'FLIGHT' if (variant == 'wrong_type'
                               and name == 'M1_one_chute_default') \
            else 'VAB'
        ship_name = 'OtherShip' if (variant == 'wrong_ship_name'
                                    and name == 'M1_one_chute_default') \
            else name
        part_count = 99 if (variant == 'wrong_partcount'
                            and name == 'M1_one_chute_default') \
            else count_of[name]
        part_names = ['WrongPart'] if (variant == 'wrong_partnames'
                                       and name ==
                                       'M1_one_chute_default') \
            else names_of[name]
        text = _loadmeta_text(name, totals[name], part_count, part_names,
                              version=lm_version, ship_name=ship_name,
                              loadmeta_type=lm_type)
        if variant == 'no_version' and name == \
                'M1_one_chute_default':
            text = text.replace(f'version = {version}\n', '', 1)
        (input_dir / f'{name}.loadmeta').write_text(
            text, encoding='utf-8')

    if variant == 'renamed_loadmeta':
        # M1's loadmeta is actually M2's (renamed old file)
        m2_text = (input_dir / 'M2_two_chutes_default.loadmeta').read_text(
            encoding='utf-8')
        (input_dir / 'M1_one_chute_default.loadmeta').write_text(
            m2_text, encoding='utf-8')


def _fake_ksp_root(base):
    """Dummy KSP root with fingerprintable byte files (never parsed)."""
    ksp_root = Path(base) / 'ksp'
    gamedata = ksp_root / 'GameData'
    gamedata.mkdir(parents=True, exist_ok=True)
    (gamedata / 'ModuleManager.ConfigCache').write_bytes(
        b'SYNTHETIC CONFIG CACHE FIXTURE\n')
    chute_dir = gamedata / 'RealChute' / 'Plugins'
    fuels_dir = gamedata / 'RealFuels' / 'Plugins'
    chute_dir.mkdir(parents=True, exist_ok=True)
    fuels_dir.mkdir(parents=True, exist_ok=True)
    (chute_dir / 'RealChute.dll').write_bytes(b'SYNTHETIC REALCHUTE DLL\n')
    (fuels_dir / 'RealFuels.dll').write_bytes(b'SYNTHETIC REALFUELS DLL\n')
    return ksp_root


def _synthetic_index(prefab_mass=0.075, density=0.0000565,
                     material='Nylon'):
    """Synthetic KspIndex: probeCoreHex control core + parachuteSingle
    config + Nylon material."""
    from server.ksp_install_index import KspIndex
    core_attach = {'raw': '1,0,1,1,0', 'valid': True, 'stack': True,
                   'srfAttach': False, 'allowStack': True,
                   'allowSrfAttach': True, 'allowCollision': False}
    chute_attach = {'raw': '1,0,0,1,0', 'valid': True, 'stack': True,
                    'srfAttach': False, 'allowStack': False,
                    'allowSrfAttach': True, 'allowCollision': False}
    parts = [{
        'kind': 'part', 'name': 'probeCoreHex',
        'title': '探针核心 (synthetic)',
        'manufacturer': 'Synthetic',
        'category': 'Pods',
        'parent_url': 'Squad/Parts/Command/probeCoreHex/probeCoreHex.cfg',
        'cache_order': 0, 'primary': True, 'diagnostics': [],
        'mass_raw': '0.1', 'mass_t': 0.1,
        'modules': ['ModuleCommand'],
        'module_evidence': {},
        'dynamic_mass_modules': [], 'resources': [], 'fuel_tanks': [],
        'engines': [], 'sstu_cluster': None,
        'attach_rules': core_attach, 'attach_rules_raw': '1,0,1,1,0',
        'bulkhead_profiles_raw': 'size0', 'bulkhead_profiles': ['size0'],
        'crew_capacity_raw': '0', 'crew_capacity': 0,
        'stack_nodes': ['node_stack_bottom', 'node_stack_top'],
        'stack_nodes_ordered': ['node_stack_bottom', 'node_stack_top'],
        'attach_node': False,
    }, {
        'kind': 'part', 'name': 'parachuteSingle',
        'title': 'Mk16 降落伞 (synthetic)',
        'parent_url': 'Squad/Parts/Utility/parachuteMk1/parachuteMk1.cfg',
        'cache_order': 1, 'primary': True, 'diagnostics': [],
        'mass_raw': str(prefab_mass), 'mass_t': float(prefab_mass),
        'modules': ['RealChuteModule', 'ProceduralChute'],
        'module_evidence': {
            'RealChuteModule': [{
                'case_mass_raw': '0.075', 'case_mass': 0.075,
                'spare_chutes_raw': '5', 'spare_chutes': 5.0,
                'parachutes': [{
                    'material': material,
                    'deployed_diameter_raw': '20.7',
                    'deployed_diameter': 20.7,
                    'pre_deployed_diameter_raw': '0.8',
                    'pre_deployed_diameter': 0.8,
                }],
            }],
            'ProceduralChute': [{'chutes': []}],
        },
        'dynamic_mass_modules': [], 'resources': [], 'fuel_tanks': [],
        'engines': [], 'sstu_cluster': None,
        'attach_rules': chute_attach, 'attach_rules_raw': '1,0,0,1,0',
        'bulkhead_profiles_raw': 'size0, srf',
        'bulkhead_profiles': ['size0', 'srf'],
        'crew_capacity_raw': None, 'crew_capacity': None,
        'stack_nodes': ['node_stack_bottom'],
        'stack_nodes_ordered': ['node_stack_bottom'],
        'attach_node': True,
    }]
    materials = [{
        'kind': 'material', 'name': material,
        'area_density_raw': str(density), 'area_density': float(density),
        'parent_url': 'RealChute/Materials/ParachuteMaterials.cfg',
        'cache_order': 0, 'primary': True, 'diagnostics': [],
    }]
    metadata = {
        'schema_version': 1, 'source_kind': 'cache',
        'config_cache_path': 'x', 'file_size': 1, 'mtime_ns': 1,
        'sha256': '0' * 64, 'patched_node_count': 1, 'built_at': 'x',
        'ksp_root': 'x', 'status': 'ok',
        'record_counts': {'parts': 2, 'resources': 0, 'sstu_layouts': 0,
                          'materials': 1, 'anonymous_nodes': 0,
                          'duplicate_definitions': 0},
    }
    return KspIndex(status='ok', metadata=metadata, parts=parts,
                    resources=[], sstu_layouts=[], materials=materials,
                    diagnostics=[])


def _manifest(ksp_root, tolerances=None, core_record=None,
              schema_version=SCHEMA_VERSION):
    import hashlib

    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if core_record is None:
        core_record = _synthetic_index().primary_part('probeCoreHex')
    return {
        'schema_version': schema_version,
        'experiment_name': 'control_mass_parachute',
        'status': 'awaiting_ksp_capture',
        'ksp': {
            'ksp_root': str(ksp_root),
            'config_cache_path': str(
                ksp_root / 'GameData' / 'ModuleManager.ConfigCache'),
            'config_cache_sha256': sha(
                ksp_root / 'GameData' / 'ModuleManager.ConfigCache'),
            'dlls': {
                'RealChute': {'sha256': sha(
                    ksp_root / 'GameData' / 'RealChute' / 'Plugins'
                    / 'RealChute.dll')},
                'RealFuels': {'sha256': sha(
                    ksp_root / 'GameData' / 'RealFuels' / 'Plugins'
                    / 'RealFuels.dll')},
            },
        },
        'selected_control_core': {
            'name': core_record.get('name'),
            'title': core_record.get('title'),
            'mass_t': core_record.get('mass_t'),
            'parent_url': core_record.get('parent_url'),
            'modules': sorted(core_record.get('modules') or []),
            'resources': [],
            'sha256': _core_summary_sha256(core_record),
        },
        'control_core_candidates': [
            {'name': core_record.get('name'),
             'title': core_record.get('title'),
             'mass_t': core_record.get('mass_t')}],
        'craft_specs': {name: {} for name in CRAFT_NAMES},
        'diameter_guidance': {'min_m': 15.0, 'max_m': 26.0},
        'tolerances': dict(TOLERANCES, **(tolerances or {})),
        'input_layout': {'directory': 'input', 'expected_files': []},
    }


class ExperimentSetup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix='ksp_exp_')
        self.base = Path(self._tmp)
        self.ksp_root = _fake_ksp_root(self.base)
        self.experiment_dir = self.base / 'experiment'
        self.index = _synthetic_index()
        manifest = _manifest(self.ksp_root)
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        (self.experiment_dir / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False), encoding='utf-8')

    def analyze(self, **kwargs):
        kwargs.setdefault('ksp_root', str(self.ksp_root))
        kwargs.setdefault('index', self.index)
        return analyze_experiment(str(self.experiment_dir), **kwargs)

    def analyze_twice(self):
        first = json.dumps(self.analyze(), ensure_ascii=False, sort_keys=True)
        second = json.dumps(self.analyze(), ensure_ascii=False,
                            sort_keys=True)
        self.assertEqual(first, second)


class AwaitingCaptureTests(ExperimentSetup):
    def test_missing_all_files_returns_awaiting(self):
        report = self.analyze()
        self.assertEqual(report['experiment_status'], STATUS_AWAITING)
        self.assertEqual(len(report['input']['missing_files']), 8)
        # input evidence list records None for absent files
        self.assertTrue(all(
            sha is None for sha in report['input']['sha256'].values()))

    def test_partial_capture_returns_awaiting(self):
        (self.experiment_dir / 'input').mkdir(parents=True, exist_ok=True)
        (self.experiment_dir / 'input' / 'M0_control.craft').write_text(
            _craft_text('M0_control'), encoding='utf-8')
        report = self.analyze()
        self.assertEqual(report['experiment_status'], STATUS_AWAITING)
        self.assertIn('M1_one_chute_default.craft',
                      report['input']['missing_files'])
        self.assertIn('M0_control.loadmeta',
                      report['input']['missing_files'])
        # present files carry a real SHA-256
        self.assertIsNotNone(
            report['input']['sha256']['M0_control.craft'])

    def test_loadmeta_pairing_error_returns_awaiting(self):
        (self.experiment_dir / 'input').mkdir(parents=True, exist_ok=True)
        for name in CRAFT_NAMES:
            (self.experiment_dir / 'input' / f'{name}.craft').write_text(
                _craft_text(name), encoding='utf-8')
        report = self.analyze()
        self.assertEqual(report['experiment_status'], STATUS_AWAITING)
        self.assertEqual(len(report['input']['missing_files']), 4)

    def test_manifest_missing(self):
        (self.experiment_dir / 'manifest.json').unlink()
        report = self.analyze()
        self.assertEqual(report['experiment_status'],
                         'invalid_experiment_setup')

    def test_old_schema_manifest_rejected_with_reprepare_hint(self):
        manifest = _manifest(self.ksp_root, schema_version=1)
        (self.experiment_dir / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        report = self.analyze()
        self.assertEqual(report['experiment_status'],
                         'invalid_experiment_setup')
        self.assertIn('重新运行 prepare', report['error'])


class ToleranceModelTests(ExperimentSetup):
    def test_fixed_seven_decimal_constants_removed(self):
        # the fixed 7-decimal assumption must NOT participate in verdicts
        from tools.research.control_mass_experiment import TOLERANCES
        for obsolete in ('loadmeta_resolution_t',
                         'single_value_max_rounding_error_t',
                         'difference_max_rounding_error_t',
                         'linear_combination_max_rounding_error_t',
                         'model_tolerance_t', 'linearity_tolerance_t',
                         'diameter_tolerance_t', 'float32_model_margin_t',
                         'verdict_headroom_t', 'ulp_t',
                         'rounding_bound_t'):
            self.assertNotIn(obsolete, TOLERANCES)
        # ... and a manifest carrying them fails closed as unknown fields
        manifest = _manifest(self.ksp_root)
        manifest['tolerances'] = dict(TOLERANCES)
        manifest['tolerances']['loadmeta_resolution_t'] = 1e-7
        (self.experiment_dir / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        report = self.analyze()
        self.assertEqual(report['experiment_status'],
                         'invalid_experiment_setup')
        self.assertTrue(any('loadmeta_resolution_t' in error
                            for error in report['tolerance_errors']))

    def test_serialization_model_is_binary32_round_trip(self):
        self.assertEqual(SERIALIZATION_MODEL,
                         'ieee754_binary32_round_trip')
        manifest = _manifest(self.ksp_root)
        manifest['tolerances'] = dict(TOLERANCES)
        manifest['tolerances']['serialization_model'] = 'fixed_seven_digits'
        (self.experiment_dir / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        report = self.analyze()
        self.assertEqual(report['experiment_status'],
                         'invalid_experiment_setup')
        self.assertIn('tolerances_serialization_model_unsupported',
                      report['tolerance_errors'])

    def _independent_f32(self, value):
        """INDEPENDENT float32 normalization (struct-based, not the
        module-under-test implementation)."""
        import struct as _struct
        return _struct.unpack('<f', _struct.pack('<f', value))[0]

    def test_stored_f32_normalization_and_roundtrip_delta(self):
        result = _binary32_analysis(0.1)
        self.assertIsNotNone(result)
        expected_stored = self._independent_f32(0.1)
        self.assertEqual(result['stored_f32_t'], expected_stored)
        self.assertEqual(result['parsed_decimal_t'], 0.1)
        expected_delta = abs(0.1 - expected_stored)
        self.assertEqual(result['decimal_roundtrip_delta_t'],
                         expected_delta)
        self.assertGreater(expected_delta, 0)
        # round-trip display error is SEPARATE from the quantization bound
        self.assertNotEqual(result['quantization_bound_t'],
                            result['decimal_roundtrip_delta_t'])

    def test_quantization_uses_roundtrip_interval_midpoints(self):
        # 1.0 is a power of two with symmetric spacing; the quantization
        # bound is HALF the spacing (midpoint distance), not the full
        # distance to the neighbours.  The neighbours are computed with
        # INDEPENDENT bit-pattern manipulation.
        result = _binary32_analysis(1.0)
        self.assertIsNotNone(result)
        bits = self._independent_bits(self._independent_f32(1.0))
        lower = self._independent_from_bits(bits - 1)
        upper = self._independent_from_bits(bits + 1)
        self.assertEqual(result['lower_neighbour_t'], lower)
        self.assertEqual(result['upper_neighbour_t'], upper)
        self.assertEqual(result['lower_midpoint_t'],
                         (lower + 1.0) / 2.0)
        self.assertEqual(result['upper_midpoint_t'],
                         (1.0 + upper) / 2.0)
        self.assertEqual(result['quantization_bound_t'], 2 ** -24)

    def test_power_of_two_asymmetric_spacing(self):
        # at 2.0 the lower spacing is 2^-23, the upper spacing 2^-22:
        # the quantization bound takes the LARGER half (2^-22/2 = 2^-23)
        result = _binary32_analysis(2.0)
        self.assertIsNotNone(result)
        self.assertEqual(result['stored_f32_t'], 2.0)
        self.assertEqual(result['lower_neighbour_t'], 2.0 - 2 ** -23)
        self.assertEqual(result['upper_neighbour_t'], 2.0 + 2 ** -22)
        self.assertEqual(result['quantization_bound_t'], 2 ** -23)

    def test_independent_expectations_for_key_magnitudes(self):
        def neighbours(value):
            bits = self._independent_bits(self._independent_f32(value))
            return (self._independent_from_bits(bits - 1),
                    self._independent_from_bits(bits + 1))
        expectations = {
            0.5: (self._independent_f32(0.5), *neighbours(0.5), 2 ** -25),
            1.0: (self._independent_f32(1.0), *neighbours(1.0), 2 ** -24),
        }
        for value, (stored, lower, upper, bound) in expectations.items():
            result = _binary32_analysis(value)
            self.assertEqual(result['stored_f32_t'], stored)
            self.assertEqual(result['lower_neighbour_t'], lower)
            self.assertEqual(result['upper_neighbour_t'], upper)
            self.assertEqual(result['quantization_bound_t'], bound)

    def test_150_and_1000_independent_quantization(self):
        for value in (150.0, 1000.0):
            stored = self._independent_f32(value)
            bits = self._independent_bits(stored)
            lower = self._independent_from_bits(bits - 1)
            upper = self._independent_from_bits(bits + 1)
            expected_bound = max(
                stored - (lower + stored) / 2.0,
                (stored + upper) / 2.0 - stored)
            result = _binary32_analysis(value)
            self.assertEqual(result['stored_f32_t'], stored)
            self.assertEqual(result['lower_neighbour_t'], lower)
            self.assertEqual(result['upper_neighbour_t'], upper)
            self.assertEqual(result['quantization_bound_t'],
                             expected_bound)
        # bounds grow with magnitude
        self.assertGreater(
            _binary32_analysis(1000.0)['quantization_bound_t'],
            _binary32_analysis(150.0)['quantization_bound_t'])
        self.assertGreater(
            _binary32_analysis(150.0)['quantization_bound_t'],
            _binary32_analysis(1.0)['quantization_bound_t'])

    def _independent_bits(self, value):
        import struct as _struct
        return _struct.unpack('<I', _struct.pack('<f', value))[0]

    def _independent_from_bits(self, bits):
        import struct as _struct
        return _struct.unpack('<f', _struct.pack('<I', bits & 0xFFFFFFFF))[0]

    def test_positive_zero_handled(self):
        result = _binary32_analysis(0.0)
        self.assertIsNotNone(result)
        self.assertEqual(result['stored_f32_t'], 0.0)
        denormal = self._independent_from_bits(1)
        self.assertEqual(result['lower_neighbour_t'], -denormal)
        self.assertEqual(result['upper_neighbour_t'], denormal)
        self.assertEqual(result['quantization_bound_t'], denormal / 2.0)

    def test_subnormal_handled(self):
        result = _binary32_analysis(1e-40)
        self.assertIsNotNone(result)
        self.assertGreater(result['quantization_bound_t'], 0)
        self.assertLess(result['quantization_bound_t'], 1e-37)

    def test_inf_nan_negative_and_overflow_fail_closed(self):
        self.assertIsNone(_binary32_analysis(float('inf')))
        self.assertIsNone(_binary32_analysis(float('nan')))
        self.assertIsNone(_binary32_analysis(-1.0))
        self.assertIsNone(_binary32_analysis(1e39))  # beyond binary32

    def test_illegal_tolerance_fails_closed(self):
        manifest = _manifest(self.ksp_root)
        manifest['tolerances'] = dict(TOLERANCES)
        manifest['tolerances']['experiment_margin_t'] = -1.0
        (self.experiment_dir / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        report = self.analyze()
        self.assertEqual(report['experiment_status'],
                         'invalid_experiment_setup')
        self.assertTrue(any('experiment_margin_t' in error
                            for error in report['tolerance_errors']))

    def test_unknown_tolerance_field_fails_closed(self):
        manifest = _manifest(self.ksp_root)
        manifest['tolerances'] = dict(TOLERANCES)
        manifest['tolerances']['magic_tolerance_t'] = 0.5
        (self.experiment_dir / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        report = self.analyze()
        self.assertEqual(report['experiment_status'],
                         'invalid_experiment_setup')
        self.assertTrue(any('magic_tolerance_t' in error
                            for error in report['tolerance_errors']))

    def test_diameter_threshold_uses_meters(self):
        self.assertEqual(TOLERANCES['diameter_min_delta_m'], 2.0)
        self.assertNotIn('diameter_min_delta_t', TOLERANCES)


class ErrorPropagationTests(ExperimentSetup):
    """M1−M0, M2−2·M1+M0, M3−M1 propagation with dynamic binary32
    bounds."""

    def setUp(self):
        super().setUp()
        totals = {
            'M0_control': 0.1,
            'M1_one_chute_default': 0.175,
            'M2_two_chutes_default': 0.25,
            'M3_one_chute_changed_diameter': 0.1815,
        }
        _write_fixture(self.experiment_dir, totals,
                       chute_diameters=(20.7, 24.0))

    def _bounds(self):
        report = self.analyze()
        numbers = report['differential']['loadmeta_numbers']
        return {
            name: numbers[name]['quantization_bound_t']
            for name in CRAFT_NAMES
        }

    def test_observed_error_bounds_propagate_by_coefficients(self):
        report = self.analyze()
        bounds = self._bounds()
        differential = report['differential']
        one = differential['models']['prefab_base_only']
        self.assertAlmostEqual(one['observed_error_bound_t'],
                               bounds['M1_one_chute_default']
                               + bounds['M0_control'], places=12)
        linearity = differential['linearity_check']
        self.assertAlmostEqual(
            linearity['observed_error_bound_t'],
            bounds['M2_two_chutes_default']
            + 2 * bounds['M1_one_chute_default']
            + bounds['M0_control'], places=12)
        diameter = differential['diameter_check']
        self.assertAlmostEqual(
            diameter['observed_error_bound_t'],
            bounds['M3_one_chute_changed_diameter']
            + bounds['M1_one_chute_default'], places=12)

    def test_differentials_use_normalized_stored_f32(self):
        # the observed increments must come from stored_f32_t values, NOT
        # from the parsed decimal approximations
        import struct as _struct
        report = self.analyze()
        numbers = report['differential']['loadmeta_numbers']
        stored = {name: numbers[name]['stored_f32_t']
                  for name in CRAFT_NAMES}
        differential = report['differential']
        self.assertEqual(
            differential['observed_one_chute_t'],
            stored['M1_one_chute_default'] - stored['M0_control'])
        self.assertEqual(
            differential['observed_two_chutes_t'],
            stored['M2_two_chutes_default'] - stored['M0_control'])
        self.assertEqual(
            differential['observed_diameter_effect_t'],
            stored['M3_one_chute_changed_diameter']
            - stored['M1_one_chute_default'])
        # the round-trip deltas are real and non-zero for non-representable
        # totals, and are reported separately
        for name in CRAFT_NAMES:
            entry = numbers[name]
            self.assertEqual(
                entry['decimal_roundtrip_delta_t'],
                abs(entry['parsed_decimal_t'] - entry['stored_f32_t']))

    def test_verdict_tolerance_combines_bounds_and_margin(self):
        report = self.analyze()
        model = report['differential']['models']['prefab_base_only']
        expected = (model['observed_error_bound_t']
                    + model['predicted_error_t']
                    + TOLERANCES['experiment_margin_t'])
        self.assertAlmostEqual(model['verdict_tolerance_t'], expected,
                               places=12)
        # the quantization bound is dynamic and small — no fixed constant
        self.assertGreater(model['observed_error_bound_t'], 0)

    def test_loadmeta_numbers_report(self):
        report = self.analyze()
        numbers = report['differential']['loadmeta_numbers']
        entry = numbers['M0_control']
        self.assertEqual(entry['serialization_model'],
                         'ieee754_binary32_round_trip')
        self.assertEqual(entry['raw_decimal'], '0.1')
        self.assertEqual(entry['parsed_decimal_t'], 0.1)
        self.assertTrue(entry['serialization_valid'])
        self.assertIsNotNone(entry['stored_f32_t'])
        self.assertIsNotNone(entry['decimal_roundtrip_delta_t'])
        self.assertIsNotNone(entry['lower_neighbour_t'])
        self.assertIsNotNone(entry['upper_neighbour_t'])
        self.assertIsNotNone(entry['lower_midpoint_t'])
        self.assertIsNotNone(entry['upper_midpoint_t'])
        self.assertGreater(entry['quantization_bound_t'], 0)
        # quantization bound uses the round-trip interval midpoints
        self.assertEqual(
            entry['quantization_bound_t'],
            max(entry['stored_f32_t'] - entry['lower_midpoint_t'],
                entry['upper_midpoint_t'] - entry['stored_f32_t']))
        self.assertIsNotNone(entry['fractional_digit_count'])


class ValidDesignAnalysisTests(ExperimentSetup):
    """Analyzed path with a design where the PREFAB model closes the
    residual: M1−M0 = prefab mass, M2−M0 = 2×prefab, M3 diameter effect
    equals the chute-mass difference."""

    PREFAB = 0.075
    CORE = 0.1
    D1 = 20.7
    D3 = 24.0

    def setUp(self):
        super().setUp()
        chute_m1 = _chute_mass_expectation(self.D1)
        chute_m3 = _chute_mass_expectation(self.D3)
        self.chute_m1 = chute_m1
        self.chute_m3 = chute_m3
        self.diameter_delta = _f32(chute_m3 - chute_m1)
        totals = {
            'M0_control': self.CORE,
            'M1_one_chute_default': self.CORE + self.PREFAB,
            'M2_two_chutes_default': self.CORE + 2 * self.PREFAB,
            'M3_one_chute_changed_diameter':
                self.CORE + self.PREFAB + self.diameter_delta,
        }
        _write_fixture(self.experiment_dir, totals,
                       chute_diameters=(self.D1, self.D3))

    def test_analysis_status_and_determinism(self):
        report = self.analyze()
        self.assertEqual(report['experiment_status'], STATUS_ANALYZED)
        self.assertTrue(report['validation']['valid'])
        self.assertEqual(report['validation']['diffs'], [])
        self.assertTrue(report['fingerprints_matches_manifest'])
        self.analyze_twice()

    def test_input_sha256_evidence_list(self):
        report = self.analyze()
        sha256 = report['input']['sha256']
        self.assertEqual(len(sha256), 8)
        for name, digest in sha256.items():
            self.assertIsNotNone(digest)
            self.assertEqual(len(digest), 64)

    def test_observed_differentials(self):
        report = self.analyze()
        differential = report['differential']
        self.assertAlmostEqual(differential['observed_one_chute_t'],
                               self.PREFAB, places=6)
        self.assertAlmostEqual(differential['observed_two_chutes_t'],
                               2 * self.PREFAB, places=6)
        self.assertAlmostEqual(differential['observed_diameter_effect_t'],
                               self.diameter_delta, places=6)

    def test_single_chute_models(self):
        report = self.analyze()
        models = report['differential']['models']
        prefab = models['prefab_base_only']
        self.assertEqual(prefab['applicable'], True)
        self.assertTrue(prefab['evidence_complete'])
        self.assertAlmostEqual(prefab['predicted_increment_t'],
                               self.PREFAB, places=6)
        self.assertEqual(prefab['verdict'], VERDICT_SUPPORTED)

        settled = models['realchute_settled_mass']
        self.assertTrue(settled['evidence_complete'])
        self.assertAlmostEqual(settled['predicted_increment_t'],
                               0.075 + self.chute_m1, places=5)
        self.assertEqual(settled['verdict'], VERDICT_CONTRADICTED)

        excluded = models['part_effectively_excluded']
        self.assertEqual(excluded['predicted_increment_t'], 0.0)
        self.assertEqual(excluded['verdict'], VERDICT_CONTRADICTED)

    def test_two_chutes_linearity(self):
        report = self.analyze()
        linearity = report['differential']['linearity_check']
        self.assertAlmostEqual(linearity['predicted_two_chutes_t'],
                               2 * self.PREFAB, places=6)
        self.assertAlmostEqual(linearity['observed_two_chutes_t'],
                               2 * self.PREFAB, places=6)
        self.assertTrue(linearity['within_tolerance'])
        self.assertEqual(linearity['verdict'], VERDICT_SUPPORTED)

    def test_diameter_sensitivity(self):
        report = self.analyze()
        diameter = report['differential']['diameter_check']
        self.assertAlmostEqual(
            diameter['predicted_chute_mass_delta_t'],
            self.diameter_delta, places=6)
        self.assertAlmostEqual(
            diameter['observed_diameter_effect_t'],
            self.diameter_delta, places=6)
        self.assertEqual(diameter['verdict'], VERDICT_SUPPORTED)

    def test_chute_mass_evidence(self):
        report = self.analyze()
        evidence = report['differential']['evidence']
        self.assertEqual(evidence['prefab_part_mass_t'], self.PREFAB)
        self.assertEqual(evidence['case_mass_t'], 0.075000003)
        self.assertEqual(evidence['material'], 'Nylon')
        self.assertEqual(evidence['diameter_m1_m'], self.D1)
        self.assertEqual(evidence['diameter_m3_m'], self.D3)
        self.assertAlmostEqual(evidence['chute_mass_m1_t'],
                               self.chute_m1, places=6)
        self.assertAlmostEqual(evidence['chute_mass_m3_t'],
                               self.chute_m3, places=6)
        self.assertAlmostEqual(evidence['realchute_mass_delta_t'],
                               0.075 + self.chute_m1 - self.PREFAB,
                               places=5)
        self.assertAlmostEqual(
            evidence['predicted_settled_part_mass_t'],
            0.075 + self.chute_m1, places=5)


class ContradictedDesignTests(ExperimentSetup):
    """M1−M0 = prefab + 0.005: every model is contradicted."""

    def setUp(self):
        super().setUp()
        totals = {
            'M0_control': 0.1,
            'M1_one_chute_default': 0.1 + 0.075 + 0.005,
            'M2_two_chutes_default': 0.1 + 0.075 * 2 + 0.010,
            'M3_one_chute_changed_diameter': 0.1 + 0.075 + 0.005 + 0.007,
        }
        _write_fixture(self.experiment_dir, totals,
                       chute_diameters=(20.7, 24.0))

    def test_all_models_contradicted(self):
        report = self.analyze()
        self.assertEqual(report['experiment_status'], STATUS_ANALYZED)
        for model in report['differential']['models'].values():
            self.assertEqual(model['verdict'], VERDICT_CONTRADICTED)


class InsufficientEvidenceTests(ExperimentSetup):
    def _no_material_index(self):
        """Index with the control core + parachuteSingle record (so the
        attachment plan is derivable) but with the parachute's MASS and
        the MATERIAL evidence removed — prefab/chute-mass predictions are
        missing and the models are insufficient."""
        index = _synthetic_index()
        chute = index.primary_part('parachuteSingle')
        chute['mass_raw'] = None
        chute['mass_t'] = None
        index.materials = []
        index._materials_by_name = {}
        return index

    def test_missing_part_config_makes_models_insufficient(self):
        totals = {
            'M0_control': 0.1,
            'M1_one_chute_default': 0.175,
            'M2_two_chutes_default': 0.25,
            'M3_one_chute_changed_diameter': 0.1815,
        }
        _write_fixture(self.experiment_dir, totals,
                       chute_diameters=(20.7, 24.0))
        report = analyze_experiment(str(self.experiment_dir),
                                    ksp_root=str(self.ksp_root),
                                    index=self._no_material_index())
        self.assertEqual(report['experiment_status'], STATUS_ANALYZED)
        models = report['differential']['models']
        self.assertEqual(models['prefab_base_only']['verdict'],
                         VERDICT_INSUFFICIENT)
        self.assertEqual(models['realchute_settled_mass']['verdict'],
                         VERDICT_INSUFFICIENT)
        # model 3 needs no config evidence: the increment is 0.0 and the
        # observed 0.075 is known -> contradicted
        self.assertEqual(models['part_effectively_excluded']['verdict'],
                         VERDICT_CONTRADICTED)
        evidence = report['differential']['evidence']
        self.assertIn('prefab_part_mass', evidence['missing'])
        self.assertIn('material_area_density', evidence['missing'])
        self.assertIn('chute_mass_m1', evidence['missing'])
        self.assertEqual(evidence['missing'],
                         sorted(set(evidence['missing'])))

    def test_predicted_none_means_evidence_incomplete(self):
        totals = {
            'M0_control': 0.1,
            'M1_one_chute_default': 0.175,
            'M2_two_chutes_default': 0.25,
            'M3_one_chute_changed_diameter': 0.1815,
        }
        _write_fixture(self.experiment_dir, totals,
                       chute_diameters=(20.7, 24.0))
        report = analyze_experiment(str(self.experiment_dir),
                                    ksp_root=str(self.ksp_root),
                                    index=self._no_material_index())
        models = report['differential']['models']
        for name in ('prefab_base_only', 'realchute_settled_mass'):
            model = models[name]
            self.assertIsNone(model['predicted_increment_t'])
            self.assertFalse(model['evidence_complete'], name)
            self.assertIsNone(model['absolute_error_t'])
            self.assertEqual(model['verdict'], VERDICT_INSUFFICIENT, name)


class AttachmentPlanTests(unittest.TestCase):
    """Composite attachment plan decided by BOTH parts together."""

    def _record(self, name, attach):
        return {
            'name': name,
            'attach_rules': attach,
            'stack_nodes_ordered': ['node_stack_bottom',
                                    'node_stack_top'],
        }

    def _attach(self, stack=False, srf_attach=False, allow_stack=False,
                allow_srf_attach=False):
        return {'raw': 'x', 'valid': True, 'stack': stack,
                'srfAttach': srf_attach, 'allowStack': allow_stack,
                'allowSrfAttach': allow_srf_attach,
                'allowCollision': False}

    def test_chute_srf_attach_false_blocks_surface_path(self):
        # core allowSrfAttach=true is NOT enough: the CHUTE must be able
        # to surface-attach as a child
        core = self._record('Core', self._attach(allow_srf_attach=True,
                                                 allow_stack=True))
        chute = self._record('parachuteSingle',
                             self._attach(stack=True))
        plan = _experiment_attachment_plan(core, chute)
        self.assertFalse(plan['surface_path_available'])
        self.assertTrue(plan['stack_path_available'])
        self.assertEqual(plan['mode'], 'stack')

    def test_core_allow_srf_attach_alone_is_not_sufficient(self):
        # a core whose flags look right but whose CHUTE cannot surface
        # attach must NOT claim a surface path
        core = self._record('Core', self._attach(allow_srf_attach=True,
                                                 allow_stack=False))
        chute = self._record('parachuteSingle',
                             self._attach(srf_attach=False, stack=False))
        plan = _experiment_attachment_plan(core, chute)
        self.assertFalse(plan['surface_path_available'])
        self.assertFalse(plan['stack_path_available'])
        self.assertFalse(plan['supports_experiment'])

    def test_single_stack_node_cannot_complete_m2(self):
        core = {
            'name': 'SingleNodeCore',
            'attach_rules': self._attach(stack=True, allow_stack=True,
                                         allow_srf_attach=True),
            'stack_nodes_ordered': ['node_stack_top'],
        }
        chute = self._record('parachuteSingle',
                             self._attach(stack=True))
        plan = _experiment_attachment_plan(core, chute)
        self.assertEqual(plan['mode'], 'stack')
        self.assertFalse(plan['supports_experiment'])
        self.assertIsNone(plan['M2_two_chutes_default'])
        self.assertEqual(plan['M1_one_chute_default'],
                         ['node_stack_top'])

    def test_two_independent_stack_nodes_complete_m2(self):
        core = self._record('Core', self._attach(stack=True,
                                                 allow_stack=True,
                                                 allow_srf_attach=True))
        chute = self._record('parachuteSingle',
                             self._attach(stack=True))
        plan = _experiment_attachment_plan(core, chute)
        self.assertTrue(plan['supports_experiment'])
        self.assertEqual(plan['mode'], 'stack')
        self.assertEqual(plan['M1_one_chute_default'],
                         ['node_stack_top'])
        self.assertEqual(plan['M2_two_chutes_default'],
                         ['node_stack_top', 'node_stack_bottom'])
        self.assertEqual(plan['M3_one_chute_changed_diameter'],
                         ['node_stack_top'])

    def test_surface_mode_allows_two_independent_mounts(self):
        core = self._record('Core', self._attach(allow_srf_attach=True))
        chute = self._record('parachuteSingle',
                             self._attach(srf_attach=True))
        plan = _experiment_attachment_plan(core, chute)
        self.assertEqual(plan['mode'], 'surface')
        self.assertTrue(plan['surface_path_available'])
        self.assertTrue(plan['supports_experiment'])
        self.assertEqual(plan['M2_two_chutes_default'],
                         ['surface', 'surface'])


class InvalidDesignTests(ExperimentSetup):
    def _invalid(self, variant):
        totals = {
            'M0_control': 0.1,
            'M1_one_chute_default': 0.175,
            'M2_two_chutes_default': 0.25,
            'M3_one_chute_changed_diameter': 0.1815,
        }
        _write_fixture(self.experiment_dir, totals,
                       chute_diameters=(20.7, 24.0), variant=variant)
        report = self.analyze()
        self.assertEqual(report['experiment_status'], STATUS_INVALID)
        self.assertFalse(report['validation']['valid'])
        self.assertNotIn('differential', report)
        return report

    def _codes(self, report):
        return [d['code'] for d in report['validation']['diffs']]

    def test_root_part_not_selected_core(self):
        report = self._invalid('root_mismatch')
        self.assertIn('m0_root_not_selected_core', self._codes(report))

    def test_unexpected_part_difference(self):
        report = self._invalid('extra_part')
        self.assertIn('part_set_mismatch', self._codes(report))

    def test_resource_difference(self):
        report = self._invalid('extra_resource')
        self.assertIn('resource_mismatch', self._codes(report))

    def test_candidate_count_wrong(self):
        report = self._invalid('wrong_chute_counts')
        self.assertIn('chute_count_mismatch', self._codes(report))

    def test_m3_extra_changes(self):
        report = self._invalid('m3_extra_change')
        self.assertIn('m3_extra_changes', self._codes(report))

    def test_m3_mass_field_changed(self):
        report = self._invalid('m3_mass_field')
        self.assertIn('m3_extra_changes', self._codes(report))

    def test_m3_diameter_not_changed(self):
        report = self._invalid('m3_same_diameter')
        self.assertIn('m3_diameter_not_changed', self._codes(report))

    def test_no_chute_at_all(self):
        report = self._invalid('no_chute')
        self.assertIn('chute_count_mismatch', self._codes(report))

    def test_symmetric_chutes_forbidden(self):
        report = self._invalid('symmetric')
        self.assertIn('symmetry_forbidden', self._codes(report))

    def test_m2_daisy_chain_rejected(self):
        # M2's second chute hung on the FIRST chute is forbidden
        report = self._invalid('daisy_chain')
        self.assertIn('chute_daisy_chain_forbidden', self._codes(report))

    def test_core_mod_mass_inconsistent(self):
        report = self._invalid('core_mod_mass')
        self.assertIn('core_state_inconsistent', self._codes(report))

    def test_wrong_ship_name(self):
        report = self._invalid('wrong_ship_name')
        self.assertIn('craft_ship_name_mismatch', self._codes(report))

    def test_wrong_partcount(self):
        report = self._invalid('wrong_partcount')
        self.assertIn('loadmeta_partcount_mismatch', self._codes(report))

    def test_wrong_partnames(self):
        report = self._invalid('wrong_partnames')
        self.assertIn('loadmeta_partnames_mismatch', self._codes(report))

    def test_wrong_loadmeta_type(self):
        report = self._invalid('wrong_type')
        self.assertIn('loadmeta_type_mismatch', self._codes(report))

    def test_wrong_version(self):
        report = self._invalid('wrong_version')
        self.assertIn('ksp_version_inconsistent', self._codes(report))

    def test_missing_version_rejected(self):
        # craft/loadmeta without a version must be REPORTED as missing —
        # not silently treated as "consistent because no value entered
        # the set"
        report = self._invalid('no_version')
        codes = self._codes(report)
        self.assertIn('craft_version_missing', codes)
        self.assertIn('loadmeta_version_missing', codes)

    def test_renamed_old_loadmeta_rejected(self):
        report = self._invalid('renamed_loadmeta')
        codes = self._codes(report)
        # M1's loadmeta is really M2's: shipName and partCount mismatch
        # (partNames sets coincide because both crafts contain the same
        # distinct part names, so the pairing rejection rests on the
        # shipName/partCount evidence)
        self.assertIn('loadmeta_shipname_mismatch', codes)
        self.assertIn('loadmeta_partcount_mismatch', codes)

    def test_forbidden_mass_modifier(self):
        totals = {
            'M0_control': 0.1,
            'M1_one_chute_default': 0.175,
            'M2_two_chutes_default': 0.25,
            'M3_one_chute_changed_diameter': 0.1815,
        }
        _write_fixture(self.experiment_dir, totals,
                       chute_diameters=(20.7, 24.0))
        text = (self.experiment_dir / 'input' /
                'M1_one_chute_default.craft').read_text(encoding='utf-8')
        text = text.replace(
            '\tMODULE\n\t{\n\t\tname = RealChuteModule',
            '\tMODULE\n\t{\n\t\tname = SSTUProceduralDecoupler\n'
            '\t\tdiameter = 1.25\n\t}\n\tMODULE\n\t{\n'
            '\t\tname = RealChuteModule', 1)
        (self.experiment_dir / 'input' /
         'M1_one_chute_default.craft').write_text(text, encoding='utf-8')
        report = self.analyze()
        self.assertEqual(report['experiment_status'], STATUS_INVALID)
        self.assertIn('forbidden_mass_modifier', self._codes(report))

    def test_fingerprint_change(self):
        totals = {
            'M0_control': 0.1,
            'M1_one_chute_default': 0.175,
            'M2_two_chutes_default': 0.25,
            'M3_one_chute_changed_diameter': 0.1815,
        }
        _write_fixture(self.experiment_dir, totals,
                       chute_diameters=(20.7, 24.0))
        cache = self.ksp_root / 'GameData' / 'ModuleManager.ConfigCache'
        cache.write_bytes(b'MODIFIED SYNTHETIC CONFIG CACHE\n')
        report = self.analyze()
        self.assertEqual(report['experiment_status'], STATUS_INVALID)
        self.assertIn('fingerprint_mismatch', self._codes(report))
        self.assertFalse(report['fingerprints_matches_manifest'])

    def test_control_core_config_changed(self):
        totals = {
            'M0_control': 0.1,
            'M1_one_chute_default': 0.175,
            'M2_two_chutes_default': 0.25,
            'M3_one_chute_changed_diameter': 0.1815,
        }
        _write_fixture(self.experiment_dir, totals,
                       chute_diameters=(20.7, 24.0))
        # the current index has a MODIFIED core record (mass changed)
        modified = _synthetic_index()
        core = modified.primary_part('probeCoreHex')
        core['mass_raw'] = '0.2'
        core['mass_t'] = 0.2
        report = analyze_experiment(str(self.experiment_dir),
                                    ksp_root=str(self.ksp_root),
                                    index=modified)
        self.assertEqual(report['experiment_status'], STATUS_INVALID)
        self.assertIn('control_core_config_changed', self._codes(report))

    def test_control_core_no_longer_safe(self):
        totals = {
            'M0_control': 0.1,
            'M1_one_chute_default': 0.175,
            'M2_two_chutes_default': 0.25,
            'M3_one_chute_changed_diameter': 0.1815,
        }
        _write_fixture(self.experiment_dir, totals,
                       chute_diameters=(20.7, 24.0))
        modified = _synthetic_index()
        core = modified.primary_part('probeCoreHex')
        core['modules'] = ['ModuleCommand', 'ModuleEngines']
        report = analyze_experiment(str(self.experiment_dir),
                                    ksp_root=str(self.ksp_root),
                                    index=modified)
        self.assertEqual(report['experiment_status'], STATUS_INVALID)
        self.assertIn('control_core_no_longer_safe', self._codes(report))

    def test_invalid_design_gives_no_verdicts(self):
        report = self._invalid('extra_part')
        self.assertNotIn('differential', report)
        self.assertIn('不给出任何支持/反驳判定', report['design_note'])

    def test_invalid_design_cli_exit_code_nonzero(self):
        totals = {
            'M0_control': 0.1,
            'M1_one_chute_default': 0.175,
            'M2_two_chutes_default': 0.25,
            'M3_one_chute_changed_diameter': 0.1815,
        }
        _write_fixture(self.experiment_dir, totals,
                       chute_diameters=(20.7, 24.0), variant='extra_part')
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(['analyze', '--input',
                         str(self.experiment_dir), '--json'])
        self.assertEqual(code, EXIT_INVALID)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload['experiment_status'], STATUS_INVALID)


class ControlCoreSelectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix='ksp_exp_core_')
        self.base = Path(self._tmp)
        self.ksp_root = _fake_ksp_root(self.base)
        self.experiment_dir = self.base / 'experiment'

    def _index_with(self, parts):
        from server.ksp_install_index import KspIndex
        metadata = {
            'schema_version': 1, 'source_kind': 'cache',
            'config_cache_path': 'x', 'file_size': 1, 'mtime_ns': 1,
            'sha256': '0' * 64, 'patched_node_count': 1, 'built_at': 'x',
            'ksp_root': 'x', 'status': 'ok',
            'record_counts': {'parts': len(parts), 'resources': 0,
                              'sstu_layouts': 0, 'materials': 0,
                              'anonymous_nodes': 0,
                              'duplicate_definitions': 0}}
        return KspIndex(status='ok', metadata=metadata, parts=parts,
                        resources=[], sstu_layouts=[], materials=[],
                        diagnostics=[])

    def _record(self, name, modules, mass=0.1, category='Pods',
                attach_rules=None, stack_nodes=None):
        if attach_rules is None:
            attach_rules = {'raw': '1,0,1,1,0', 'valid': True,
                            'stack': True, 'srfAttach': False,
                            'allowStack': True, 'allowSrfAttach': True,
                            'allowCollision': False}
        if stack_nodes is None:
            stack_nodes = ['node_stack_bottom', 'node_stack_top']
        return {
            'kind': 'part', 'name': name, 'title': name,
            'parent_url': f'Squad/Parts/{name}/{name}.cfg',
            'cache_order': 0, 'primary': True, 'diagnostics': [],
            'mass_raw': str(mass), 'mass_t': float(mass),
            'modules': list(modules), 'module_evidence': {},
            'dynamic_mass_modules': [], 'resources': [], 'fuel_tanks': [],
            'engines': [], 'sstu_cluster': None,
            'category': category,
            'attach_rules': attach_rules,
            'attach_rules_raw': attach_rules.get('raw'),
            'bulkhead_profiles_raw': 'size0',
            'bulkhead_profiles': ['size0'],
            'crew_capacity_raw': '0', 'crew_capacity': 0,
            'stack_nodes': sorted(stack_nodes or []),
            'stack_nodes_ordered': list(stack_nodes or []),
            'attach_node': False,
        }

    def test_engine_modulecommand_part_excluded(self):
        index = self._index_with([
            self._record('cleanCore', ['ModuleCommand'], mass=0.1),
            self._record('engineCore', ['ModuleCommand', 'ModuleEngines'],
                         mass=0.3),
            self._record('engineFxCore', ['ModuleCommand',
                                          'ModuleEnginesFX'], mass=0.4),
        ])
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index)
        self.assertEqual(result['status'], STATUS_AWAITING)
        names = [c['name'] for c in result['control_core_candidates']]
        self.assertEqual(names, ['cleanCore'])

    def test_dynamic_mass_module_excluded(self):
        index = self._index_with([
            self._record('cleanCore', ['ModuleCommand'], mass=0.1),
            self._record('tweakCore', ['ModuleCommand', 'TweakScale'],
                         mass=0.2),
            self._record('mftCore', ['ModuleCommand', 'ModuleFuelTanks'],
                         mass=0.3),
        ])
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index)
        names = [c['name'] for c in result['control_core_candidates']]
        self.assertEqual(names, ['cleanCore'])

    def test_invalid_mass_excluded(self):
        index = self._index_with([
            self._record('zeroCore', ['ModuleCommand'], mass=0.0),
            self._record('nanCore', ['ModuleCommand'], mass=float('nan')),
            self._record('goodCore', ['ModuleCommand'], mass=0.1),
        ])
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index)
        names = [c['name'] for c in result['control_core_candidates']]
        self.assertEqual(names, ['goodCore'])

    def test_ar202_style_radial_shell_excluded(self):
        # mumech_MJ2_AR202: attachRules 0,1,0,0,0 — allowSrfAttach=false,
        # allowStack=false, no stack nodes: cannot host the parachute
        ar202 = {'raw': '0,1,0,0,0', 'valid': True, 'stack': False,
                 'srfAttach': True, 'allowStack': False,
                 'allowSrfAttach': False, 'allowCollision': False}
        index = self._index_with([
            self._record('ar202Shell', ['ModuleCommand'], mass=0.0001,
                         category='Control', attach_rules=ar202,
                         stack_nodes=[]),
            self._record('probeCore', ['ModuleCommand'], mass=0.04),
        ])
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index)
        names = [c['name'] for c in result['control_core_candidates']]
        self.assertNotIn('ar202Shell', names)
        self.assertEqual(names, ['probeCore'])
        self.assertEqual(result['selected_control_core']['name'],
                         'probeCore')

    def test_normal_probe_core_with_srf_attach_included(self):
        index = self._index_with([
            self._record('probeCore', ['ModuleCommand'], mass=0.04),
        ])
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index)
        core = result['selected_control_core']
        self.assertEqual(core['name'], 'probeCore')
        self.assertTrue(core['attach_rules']['allowSrfAttach'])
        self.assertIn('allow_surface_attach', core['safety_checks'])
        self.assertIn('has_stack_node', core['safety_checks'])
        self.assertIn('Pods', core['selection_reason'])

    def test_invalid_attach_rules_excluded_and_fail_closed(self):
        invalid = {'raw': '1,0,1', 'valid': False,
                   'reason': 'expected_5_fields_got_3'}
        index = self._index_with([
            self._record('badAttach', ['ModuleCommand'], mass=0.1,
                         attach_rules=invalid),
            self._record('goodCore', ['ModuleCommand'], mass=0.2),
        ])
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index)
        names = [c['name'] for c in result['control_core_candidates']]
        self.assertEqual(names, ['goodCore'])
        # explicit selection of the invalid part fails closed
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index, control_core='badAttach')
        self.assertEqual(result['status'], 'invalid_setup')

    def test_missing_attach_rules_excluded(self):
        index = self._index_with([
            self._record('noAttach', ['ModuleCommand'], mass=0.1,
                         attach_rules={'raw': None, 'valid': False,
                                       'reason': 'missing'}),
            self._record('goodCore', ['ModuleCommand'], mass=0.2),
        ])
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index)
        names = [c['name'] for c in result['control_core_candidates']]
        self.assertEqual(names, ['goodCore'])

    def test_core_summary_hash_includes_attach_rules(self):
        from tools.research.control_mass_experiment import _core_summary_sha256
        base = self._record('probeCore', ['ModuleCommand'], mass=0.04)
        first = _core_summary_sha256(base)
        modified = dict(base)
        attach = dict(base['attach_rules'])
        attach['allowSrfAttach'] = False
        modified['attach_rules'] = attach
        self.assertNotEqual(first, _core_summary_sha256(modified))

    def test_deterministic_ranking_stability_before_mass(self):
        # a Pods probe core is ranked BEFORE a lighter Control part
        ar202 = {'raw': '1,0,1,1,0', 'valid': True, 'stack': True,
                 'srfAttach': False, 'allowStack': True,
                 'allowSrfAttach': True, 'allowCollision': False}
        index = self._index_with([
            self._record('lightControlPart', ['ModuleCommand'], mass=0.0001,
                         category='Control', attach_rules=ar202),
            self._record('podsProbeCore', ['ModuleCommand'], mass=0.04),
        ])
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index)
        self.assertEqual(result['selected_control_core']['name'],
                         'podsProbeCore')
        first = prepare_experiment(self.ksp_root, self.experiment_dir,
                                   index=index)
        self.assertEqual(first['selected_control_core']['name'],
                         'podsProbeCore')

    def test_deterministic_ranking_by_mass(self):
        index = self._index_with([
            self._record('heavyCore', ['ModuleCommand'], mass=0.9),
            self._record('lightCore', ['ModuleCommand'], mass=0.05),
            self._record('mediumCore', ['ModuleCommand'], mass=0.2),
        ])
        first = prepare_experiment(self.ksp_root, self.experiment_dir,
                                   index=index)
        second_dir = self.base / 'experiment2'
        second = prepare_experiment(self.ksp_root, second_dir, index=index)
        self.assertEqual(first['control_core_candidates'],
                         second['control_core_candidates'])
        self.assertEqual(first['selected_control_core']['name'],
                         'lightCore')

    def test_explicit_control_core_selected(self):
        index = self._index_with([
            self._record('heavyCore', ['ModuleCommand'], mass=0.9),
            self._record('lightCore', ['ModuleCommand'], mass=0.05),
        ])
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index,
                                    control_core='heavyCore')
        self.assertEqual(result['selected_control_core']['name'],
                         'heavyCore')

    def test_explicit_unsafe_core_fails_closed(self):
        index = self._index_with([
            self._record('cleanCore', ['ModuleCommand'], mass=0.1),
            self._record('engineCore', ['ModuleCommand', 'ModuleEngines'],
                         mass=0.3),
        ])
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index,
                                    control_core='engineCore')
        self.assertEqual(result['status'], 'invalid_setup')
        self.assertIn('engineCore', result['error'])

    def test_manifest_binds_unique_control_core(self):
        index = self._index_with([
            self._record('cleanCore', ['ModuleCommand'], mass=0.1),
            self._record('otherCore', ['ModuleCommand'], mass=0.2),
        ])
        prepare_experiment(self.ksp_root, self.experiment_dir, index=index)
        manifest = json.loads(
            (self.experiment_dir / 'manifest.json').read_text(
                encoding='utf-8'))
        core = manifest['selected_control_core']
        self.assertEqual(core['name'], 'cleanCore')
        for key in ('name', 'title', 'mass_t', 'parent_url', 'modules',
                    'resources', 'sha256'):
            self.assertIn(key, core)
        self.assertEqual(len(manifest['control_core_candidates']), 2)
        self.assertNotIn('module_evidence',
                         manifest['control_core_candidates'][0])

    def test_selection_reason_no_misleading_radial_claim(self):
        # with chute.srfAttach=false the surface path is unavailable;
        # the selection reason must not claim "径向或 stack 安装"
        index = _synthetic_index()
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index)
        reason = result['selected_control_core']['selection_reason']
        self.assertNotIn('径向或 stack 安装', reason)
        self.assertNotIn('径向', reason)
        self.assertIn('stack 路径', reason)
        plan = result['attachment_plan']
        self.assertFalse(plan['surface_path_available'])
        self.assertTrue(plan['stack_path_available'])
        self.assertEqual(plan['M2_two_chutes_default'],
                         ['node_stack_top', 'node_stack_bottom'])
        # the generated instructions embed the reason without the claim
        # ("径向" may legitimately appear as "径向路径不可用")
        instructions = (self.experiment_dir / 'instructions.md').read_text(
            encoding='utf-8')
        self.assertNotIn('径向或 stack 安装', instructions)

    def test_prepare_non_empty_input_refuses_overwrite(self):
        index = self._index_with([
            self._record('cleanCore', ['ModuleCommand'], mass=0.1)])
        prepare_experiment(self.ksp_root, self.experiment_dir, index=index)
        manifest_path = self.experiment_dir / 'manifest.json'
        instructions_path = self.experiment_dir / 'instructions.md'
        before_manifest = manifest_path.read_bytes()
        before_instructions = instructions_path.read_bytes()
        (self.experiment_dir / 'input' / 'M0_control.craft').write_text(
            _craft_text('M0_control'), encoding='utf-8')
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index)
        self.assertEqual(result['status'], 'input_not_empty')
        self.assertEqual(manifest_path.read_bytes(), before_manifest)
        self.assertEqual(instructions_path.read_bytes(), before_instructions)

    def test_repeat_prepare_is_idempotent(self):
        index = self._index_with([
            self._record('cleanCore', ['ModuleCommand'], mass=0.1)])
        first = prepare_experiment(self.ksp_root, self.experiment_dir,
                                   index=index)
        manifest_path = self.experiment_dir / 'manifest.json'
        instructions_path = self.experiment_dir / 'instructions.md'
        first_manifest = manifest_path.read_bytes()
        first_instructions = instructions_path.read_bytes()
        second = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index)
        self.assertEqual(second['status'], STATUS_AWAITING)
        self.assertEqual(manifest_path.read_bytes(), first_manifest)
        self.assertEqual(instructions_path.read_bytes(),
                         first_instructions)
        self.assertEqual(first['selected_control_core'],
                         second['selected_control_core'])

    def test_old_schema_manifest_migrated_when_input_empty(self):
        index = self._index_with([
            self._record('cleanCore', ['ModuleCommand'], mass=0.1)])
        old = _manifest(self.ksp_root, schema_version=1)
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        (self.experiment_dir / 'manifest.json').write_text(
            json.dumps(old, ensure_ascii=False), encoding='utf-8')
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index)
        self.assertEqual(result['status'], STATUS_AWAITING)
        manifest = json.loads(
            (self.experiment_dir / 'manifest.json').read_text(
                encoding='utf-8'))
        self.assertEqual(manifest['schema_version'], SCHEMA_VERSION)
        self.assertIn('selected_control_core', manifest)

    def test_different_fingerprint_refuses_overwrite(self):
        index = self._index_with([
            self._record('cleanCore', ['ModuleCommand'], mass=0.1)])
        prepare_experiment(self.ksp_root, self.experiment_dir, index=index)
        cache = self.ksp_root / 'GameData' / 'ModuleManager.ConfigCache'
        cache.write_bytes(b'DIFFERENT CACHE CONTENT\n')
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index)
        self.assertEqual(result['status'], 'manifest_conflict')

    def test_different_control_core_refuses_overwrite(self):
        index = self._index_with([
            self._record('cleanCore', ['ModuleCommand'], mass=0.1),
            self._record('otherCore', ['ModuleCommand'], mass=0.2)])
        prepare_experiment(self.ksp_root, self.experiment_dir, index=index,
                           control_core='cleanCore')
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=index,
                                    control_core='otherCore')
        self.assertEqual(result['status'], 'manifest_conflict')

    def test_awaiting_and_analyzed_cli_exit_code_zero(self):
        index = self._index_with([
            self._record('cleanCore', ['ModuleCommand'], mass=0.1)])
        prepare_experiment(self.ksp_root, self.experiment_dir, index=index)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(['analyze', '--input',
                         str(self.experiment_dir), '--json'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buffer.getvalue())['experiment_status'],
                         STATUS_AWAITING)


class MigrationTests(unittest.TestCase):
    """Schema migration + provenance protection of prepare."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix='ksp_exp_mig_')
        self.base = Path(self._tmp)
        self.ksp_root = _fake_ksp_root(self.base)
        self.experiment_dir = self.base / 'experiment'
        self.index = _synthetic_index()

    def _old_schema_manifest(self, schema=2, core_name='mumech_MJ2_AR202'):
        manifest = _manifest(self.ksp_root, schema_version=schema)
        manifest['selected_control_core']['name'] = core_name
        manifest['selected_control_core']['sha256'] = '0' * 64
        return manifest

    def test_empty_input_allows_schema_migration(self):
        # AR202-style old manifest (schema 2) with EMPTY input migrates to
        # the current schema and a NEW safe control core
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        (self.experiment_dir / 'manifest.json').write_text(
            json.dumps(self._old_schema_manifest(schema=2),
                       ensure_ascii=False), encoding='utf-8')
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=self.index)
        self.assertEqual(result['status'], STATUS_AWAITING)
        self.assertEqual(result['migrated_from_schema'], 2)
        self.assertEqual(result['migrated_control_core'],
                         'mumech_MJ2_AR202')
        manifest = json.loads(
            (self.experiment_dir / 'manifest.json').read_text(
                encoding='utf-8'))
        self.assertEqual(manifest['schema_version'], SCHEMA_VERSION)
        self.assertEqual(manifest['migrated_from_schema'], 2)
        self.assertEqual(manifest['migrated_control_core'],
                         'mumech_MJ2_AR202')
        self.assertEqual(manifest['selected_control_core']['name'],
                         'probeCoreHex')

    def test_old_schema_manifest_migrated_when_input_empty(self):
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        (self.experiment_dir / 'manifest.json').write_text(
            json.dumps(self._old_schema_manifest(schema=1),
                       ensure_ascii=False), encoding='utf-8')
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=self.index)
        self.assertEqual(result['status'], STATUS_AWAITING)
        manifest = json.loads(
            (self.experiment_dir / 'manifest.json').read_text(
                encoding='utf-8'))
        self.assertEqual(manifest['schema_version'], SCHEMA_VERSION)
        self.assertEqual(manifest['migrated_from_schema'], 1)
        self.assertIn('selected_control_core', manifest)

    def test_non_empty_input_forbids_core_migration(self):
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        (self.experiment_dir / 'manifest.json').write_text(
            json.dumps(self._old_schema_manifest(schema=2),
                       ensure_ascii=False), encoding='utf-8')
        (self.experiment_dir / 'input').mkdir(parents=True, exist_ok=True)
        (self.experiment_dir / 'input' / 'M0_control.craft').write_text(
            _craft_text('M0_control'), encoding='utf-8')
        before = (self.experiment_dir / 'manifest.json').read_bytes()
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=self.index)
        self.assertEqual(result['status'], 'input_not_empty')
        self.assertEqual(
            (self.experiment_dir / 'manifest.json').read_bytes(), before)

    def test_corrupt_manifest_not_overwritten(self):
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = self.experiment_dir / 'manifest.json'
        manifest_path.write_text('{ this is not json !!!', encoding='utf-8')
        before = manifest_path.read_bytes()
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=self.index)
        self.assertEqual(result['status'], 'manifest_corrupt')
        self.assertEqual(manifest_path.read_bytes(), before)

    def test_created_at_preserved_across_migration(self):
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        old = self._old_schema_manifest(schema=2)
        old['created_at'] = '2026-08-02T00:00:00+00:00'
        (self.experiment_dir / 'manifest.json').write_text(
            json.dumps(old, ensure_ascii=False), encoding='utf-8')
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=self.index)
        manifest = json.loads(
            (self.experiment_dir / 'manifest.json').read_text(
                encoding='utf-8'))
        self.assertEqual(manifest['created_at'],
                         '2026-08-02T00:00:00+00:00')

    def test_migration_history_append_only_and_idempotent(self):
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        (self.experiment_dir / 'manifest.json').write_text(
            json.dumps(self._old_schema_manifest(schema=2),
                       ensure_ascii=False), encoding='utf-8')
        first = prepare_experiment(self.ksp_root, self.experiment_dir,
                                   index=self.index)
        self.assertEqual(len(first['migration_history']), 1)
        record = first['migration_history'][0]
        self.assertEqual(record['from_schema'], 2)
        self.assertEqual(record['to_schema'], SCHEMA_VERSION)
        self.assertEqual(record['old_control_core'],
                         'mumech_MJ2_AR202')
        self.assertEqual(record['new_control_core'], 'probeCoreHex')
        manifest_path = self.experiment_dir / 'manifest.json'
        first_bytes = manifest_path.read_bytes()
        # idempotent re-run preserves the history (no duplicate append)
        second = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=self.index)
        self.assertEqual(len(second['migration_history']), 1)
        self.assertEqual(manifest_path.read_bytes(), first_bytes)

    def test_backfill_migration_history_once(self):
        # the audited schema-2->3 migration that lost its markers can be
        # backfilled ONCE (empty input only); re-runs keep it
        manifest = _manifest(self.ksp_root, schema_version=SCHEMA_VERSION)
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        (self.experiment_dir / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        backfill = [{
            'from_schema': 2, 'to_schema': 3,
            'old_control_core': 'mumech_MJ2_AR202',
            'new_control_core': 'probeCoreOcto2_v2',
            'reason': 'old core cannot host experiment chute',
        }]
        result = prepare_experiment(self.ksp_root, self.experiment_dir,
                                    index=self.index,
                                    backfill_migration_history=backfill)
        self.assertEqual(result['status'], STATUS_AWAITING)
        self.assertEqual(len(result['migration_history']), 1)
        manifest_path = self.experiment_dir / 'manifest.json'
        first_bytes = manifest_path.read_bytes()
        again = prepare_experiment(self.ksp_root, self.experiment_dir,
                                   index=self.index,
                                   backfill_migration_history=backfill)
        self.assertEqual(len(again['migration_history']), 1)
        self.assertEqual(manifest_path.read_bytes(), first_bytes)

    def test_non_empty_input_forbids_history_backfill(self):
        manifest = _manifest(self.ksp_root, schema_version=SCHEMA_VERSION)
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        (self.experiment_dir / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        (self.experiment_dir / 'input').mkdir(parents=True, exist_ok=True)
        (self.experiment_dir / 'input' / 'M0_control.craft').write_text(
            _craft_text('M0_control'), encoding='utf-8')
        result = prepare_experiment(
            self.ksp_root, self.experiment_dir, index=self.index,
            backfill_migration_history=[{
                'from_schema': 2, 'to_schema': 3,
                'old_control_core': 'mumech_MJ2_AR202',
                'new_control_core': 'probeCoreOcto2_v2',
                'reason': 'old core cannot host experiment chute',
            }])
        self.assertEqual(result['status'], 'input_not_empty')


class PreflightTests(unittest.TestCase):
    """preflight must report valid, mismatched and corrupt samples
    stably without crashing."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix='ksp_exp_preflight_')
        self.base = Path(self._tmp)
        self.ksp_root = _fake_ksp_root(self.base)
        self.vab_dir = self.base / 'Ships' / 'VAB'
        self.vab_dir.mkdir(parents=True, exist_ok=True)
        self.index = _synthetic_index()

    def _write_pair(self, name, total_mass, part_count, part_names,
                    ship_name=None, loadmeta_type='VAB', version='1.12.5',
                    corrupt_loadmeta=False, no_loadmeta=False):
        # the craft's internal ship name always matches the FILE name;
        # loadmeta overrides (ship_name=...) create genuine mispairs
        craft = _craft_text(name, chute_diameters=(20.7,))
        (self.vab_dir / f'{name}.craft').write_text(
            craft, encoding='utf-8')
        if no_loadmeta:
            return
        text = _loadmeta_text(name, total_mass, part_count, part_names,
                              version=version,
                              ship_name=ship_name, loadmeta_type=loadmeta_type)
        if corrupt_loadmeta:
            text = 'totalMass = ??? not json\n'
        (self.vab_dir / f'{name}.loadmeta').write_text(
            text, encoding='utf-8')

    def test_preflight_valid_and_rejected_samples(self):
        self._write_pair('GoodShip', 0.175, 2,
                         ['parachuteSingle', 'probeCoreHex'])
        # mispaired sample: loadmeta belongs to a different ship
        self._write_pair('BadShip', 0.25, 3,
                         ['parachuteSingle', 'probeCoreHex'],
                         ship_name='OtherShip')
        # corrupt loadmeta sample
        self._write_pair('CorruptShip', 0.25, 3,
                         ['parachuteSingle', 'probeCoreHex'],
                         corrupt_loadmeta=True)
        # missing loadmeta sample
        self._write_pair('NoLmShip', 0.25, 3,
                         ['parachuteSingle', 'probeCoreHex'],
                         no_loadmeta=True)
        report = preflight(str(self.ksp_root), str(self.vab_dir),
                           index=self.index)
        self.assertEqual(report['status'], 'preflight_complete')
        scan = report['scan']
        self.assertEqual(scan['pairs_total'], 4)
        self.assertEqual(scan['pairs_passed'], 1)
        self.assertEqual(scan['pairs_rejected'], 3)
        rejected = {sample['craft']: sample['codes']
                    for sample in scan['rejected_samples']}
        self.assertIn('ship_name_mismatch', rejected['BadShip.craft'])
        # the corrupt loadmeta parses with an invalid totalMass
        self.assertIn('total_mass_invalid', rejected['CorruptShip.craft'])
        self.assertIn('loadmeta_missing', rejected['NoLmShip.craft'])
        # valid totalMass values: GoodShip and BadShip (BadShip is rejected
        # for pairing, not for its totalMass)
        self.assertEqual(scan['total_mass']['valid_count'], 2)
        self.assertIsNotNone(scan['total_mass']['fractional_digit_range'])

    def test_preflight_core_and_attach_compatibility(self):
        self._write_pair('GoodShip', 0.175, 2,
                         ['parachuteSingle', 'probeCoreHex'])
        report = preflight(str(self.ksp_root), str(self.vab_dir),
                           index=self.index)
        core = report['selected_control_core']
        self.assertEqual(core['name'], 'probeCoreHex')
        self.assertTrue(core['attach_rules']['allowSrfAttach'])
        self.assertTrue(core['attach_rules']['allowStack'])
        self.assertIn('node_stack_top', core['stack_nodes'])
        compat = report['attach_compatibility']
        self.assertTrue(compat['stack_path_available'])
        self.assertFalse(compat['surface_path_available'])
        self.assertTrue(compat['supports_chute_installation'])
        plan = report['attachment_plan']
        self.assertEqual(plan['mode'], 'stack')
        self.assertEqual(plan['M1_one_chute_default'],
                         ['node_stack_top'])
        self.assertEqual(plan['M2_two_chutes_default'],
                         ['node_stack_top', 'node_stack_bottom'])
        self.assertEqual(plan['M3_one_chute_changed_diameter'],
                         ['node_stack_top'])
        self.assertIn('sha256', core)

    def test_preflight_fractional_digits_are_diagnostic_only(self):
        self._write_pair('A', 0.175, 2, ['parachuteSingle', 'probeCoreHex'])
        self._write_pair('B', 1.86859989, 2,
                         ['parachuteSingle', 'probeCoreHex'])
        report = preflight(str(self.ksp_root), str(self.vab_dir),
                           index=self.index)
        note = report['scan']['total_mass']['note']
        self.assertIn('仅用于诊断', note)
        self.assertEqual(
            report['scan']['total_mass']['fractional_digit_range'], [3, 8])

    def test_preflight_is_deterministic(self):
        for _ in range(2):
            self._write_pair('A', 0.175, 2,
                             ['parachuteSingle', 'probeCoreHex'])
        first = json.dumps(preflight(str(self.ksp_root),
                                     str(self.vab_dir), index=self.index),
                           ensure_ascii=False, sort_keys=True)
        second = json.dumps(preflight(str(self.ksp_root),
                                      str(self.vab_dir), index=self.index),
                            ensure_ascii=False, sort_keys=True)
        self.assertEqual(first, second)


def _okto2_index(prefab_mass=0.075, density=0.0000565,
                 material='Nylon'):
    """Synthetic index with the real capture's parts: probeCoreOcto2_v2
    (underscore, as in ConfigCache) + parachuteSingle + Nylon."""
    index = _synthetic_index(prefab_mass=prefab_mass, density=density,
                             material=material)
    core = index.parts[0]
    core['name'] = 'probeCoreOcto2_v2'
    core['title'] = '探测动力-OKTO2 (synthetic)'
    core['parent_url'] = ('Squad/Parts/Command/probeCoreOcto2_v2/'
                          'probeCoreOcto2_v2.cfg')
    index._parts_by_name = {
        'probeCoreOcto2_v2': [core],
        'parachuteSingle': [index.parts[1]],
    }
    return index


class AnalyzerFixTests(unittest.TestCase):
    """6B.3C.3B-R1: canonical names, core-attN topology, design state vs
    runtime modMass."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix='ksp_exp_fix_')
        self.base = Path(self._tmp)
        self.ksp_root = _fake_ksp_root(self.base)
        self.experiment_dir = self.base / 'experiment'
        self.index = _okto2_index()
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        (self.experiment_dir / 'manifest.json').write_text(
            json.dumps(_manifest(self.ksp_root, core_record=self.index
                                 .primary_part('probeCoreOcto2_v2')),
                       ensure_ascii=False), encoding='utf-8')

    def _analyze(self, variant=None, index=None):
        totals = {
            'M0_control': 0.1,
            'M1_one_chute_default': 0.175,
            'M2_two_chutes_default': 0.25,
            'M3_one_chute_changed_diameter': 0.1815,
        }
        _write_fixture(self.experiment_dir, totals,
                       chute_diameters=(20.7, 24.0), variant=variant,
                       root='probeCoreOcto2.v2')
        return analyze_experiment(str(self.experiment_dir),
                                  ksp_root=str(self.ksp_root),
                                  index=index or self.index)

    def _codes(self, report):
        return [d['code'] for d in
                report.get('validation', {}).get('diffs', [])]

    def test_canonical_map_dot_to_underscore_unique(self):
        from tools.research.control_mass_experiment import _canonical_part_map
        from server.vab_parser import parse_craft
        draft = parse_craft(
            open((Path(__file__).resolve().parents[1] / 'experiments/control_mass/input/M0_control.craft'), encoding='utf-8-sig').read(),
            'M0_control')
        cmap = _canonical_part_map(draft, self.index)
        core_info = cmap[draft['parts'][0]['index']]
        self.assertEqual(core_info['raw_base'], 'probeCoreOcto2.v2')
        self.assertEqual(core_info['canonical'], 'probeCoreOcto2_v2')
        self.assertEqual(core_info['status'], 'normalized_unique')
        self.assertEqual(core_info['strategy'], 'dot_to_underscore')

    def test_exact_name_wins_over_dot_normalization(self):
        from tools.research.control_mass_experiment import _canonical_part_map
        draft = {
            'parts': [{'index': 0, 'part': 'parachuteSingle_1',
                       'base_part': 'parachuteSingle'}]}
        cmap = _canonical_part_map(draft, self.index)
        self.assertEqual(cmap[0]['status'], 'exact')
        self.assertEqual(cmap[0]['canonical'], 'parachuteSingle')
        self.assertEqual(cmap[0]['strategy'], 'exact_name')

    def test_ambiguous_normalization_fails_closed(self):
        index = _okto2_index()
        duplicate = dict(index.parts[0])  # probeCoreOcto2_v2 duplicate
        duplicate['cache_order'] = 99
        duplicate['primary'] = False
        index.parts.append(duplicate)
        index._parts_by_name['probeCoreOcto2_v2'] = index.parts
        draft = {
            'parts': [{'index': 0, 'part': 'probeCoreOcto2.v2_1',
                       'base_part': 'probeCoreOcto2.v2'}]}
        from tools.research.control_mass_experiment import _canonical_part_map
        cmap = _canonical_part_map(draft, index)
        self.assertEqual(cmap[0]['status'], 'ambiguous')
        self.assertEqual(cmap[0]['raw_base'], 'probeCoreOcto2.v2')

    def test_unknown_part_name_fails_closed(self):
        draft = {
            'parts': [{'index': 0, 'part': 'noSuchPart_1',
                       'base_part': 'noSuchPart'}]}
        from tools.research.control_mass_experiment import _canonical_part_map
        cmap = _canonical_part_map(draft, self.index)
        self.assertEqual(cmap[0]['status'], 'missing')
        # the canonical name falls back to the requested raw name
        self.assertEqual(cmap[0]['canonical'], 'noSuchPart')

    def test_core_attn_topology_top_and_bottom(self):
        from tools.research.control_mass_experiment import (
            _canonical_part_map, _normalize_stack_topology)
        from server.vab_parser import parse_craft
        draft = parse_craft(
            open((Path(__file__).resolve().parents[1] / 'experiments/control_mass/input/M2_two_chutes_default.craft'),
                 encoding='utf-8-sig').read(), 'M2')
        cmap = _canonical_part_map(draft, self.index)
        topology = _normalize_stack_topology(
            draft, cmap, 'probeCoreOcto2_v2')
        nodes = sorted(a['core_node'] for a in topology['assignments'])
        self.assertEqual(nodes,
                         ['node_stack_bottom', 'node_stack_top'])
        self.assertEqual(topology['chute_to_chute'], [])
        self.assertEqual(topology['unconnected'], [])

    def test_child_trailing_node_index_not_used_for_core_node(self):
        # the chute's attN target ends with _0/_1; the core node must come
        # from the CORE's own attN rows ('top'/'bottom')
        from tools.research.control_mass_experiment import (
            _canonical_part_map, _normalize_stack_topology)
        from server.vab_parser import parse_craft
        draft = parse_craft(
            open((Path(__file__).resolve().parents[1] / 'experiments/control_mass/input/M1_one_chute_default.craft'),
                 encoding='utf-8-sig').read(), 'M1')
        cmap = _canonical_part_map(draft, self.index)
        topology = _normalize_stack_topology(
            draft, cmap, 'probeCoreOcto2_v2')
        self.assertEqual(
            [a['core_node'] for a in topology['assignments']],
            ['node_stack_top'])
        # ... and the chute still reciprocally connects to the core
        self.assertEqual(topology['unconnected'], [])

    def test_null_targets_ignored(self):
        from tools.research.control_mass_experiment import (
            _canonical_part_map, _normalize_stack_topology)
        from server.vab_parser import parse_craft
        draft = parse_craft(
            open((Path(__file__).resolve().parents[1] / 'experiments/control_mass/input/M0_control.craft'), encoding='utf-8-sig').read(),
            'M0_control')
        cmap = _canonical_part_map(draft, self.index)
        topology = _normalize_stack_topology(
            draft, cmap, 'probeCoreOcto2_v2')
        self.assertEqual(topology['assignments'], [])
        self.assertEqual(topology['unconnected'], [])

    def test_duplicate_core_node_rejected(self):
        report = self._analyze(variant='duplicate_core_node')
        self.assertEqual(report['experiment_status'], STATUS_INVALID)
        self.assertIn('duplicate_core_node', self._codes(report))

    def test_calc_select_parsed_into_design_state(self):
        report = self._analyze()  # all Manual
        self.assertEqual(report['experiment_status'], STATUS_ANALYZED)
        # the parsed design rows carry calc_select
        from server.vab_parser import parse_craft
        draft = parse_craft(
            (self.experiment_dir / 'input' /
             'M1_one_chute_default.craft').read_text(
                encoding='utf-8-sig'), 'M1')
        chute = next(p for p in draft['parts']
                     if p['base_part'] == 'parachuteSingle')
        rows = (chute['module_evidence']['ProceduralChute'][0]
                ['chutes'])
        self.assertEqual(rows[0]['calc_select'], False)

    def test_m2_automatic_rejected_with_specific_code(self):
        report = self._analyze(variant='m2_automatic')
        self.assertEqual(report['experiment_status'], STATUS_INVALID)
        codes = self._codes(report)
        self.assertIn('chute_calculation_mode_mismatch', codes)
        self.assertNotIn('m1_m2_params_mismatch', codes)
        diff = next(d for d in report['validation']['diffs']
                    if d['code'] == 'chute_calculation_mode_mismatch')
        self.assertEqual(diff['m1_calc_select'], False)
        self.assertEqual(diff['m2_calc_select'], True)

    def test_m3_runtime_mod_mass_no_extra_changes(self):
        # M3's chute modMass differs (runtime derived) but the design
        # state only changes the diameter -> no m3_extra_changes
        report = self._analyze(variant='m3_runtime_mod_mass')
        self.assertEqual(report['experiment_status'], STATUS_ANALYZED)
        self.assertNotIn('m3_extra_changes', self._codes(report))
        diags = report['runtime_mass_diagnostics']
        m3_chute = diags['M3_one_chute_changed_diameter']['chutes'][0]
        self.assertEqual(m3_chute['parsed_value_t'], 0.0087)
        self.assertFalse(m3_chute['within_binary32_zero_noise'])
        self.assertFalse(m3_chute['participates_in_design_validation'])

    def test_m1_mod_mass_zero_noise_flag(self):
        report = self._analyze()
        diags = report['runtime_mass_diagnostics']
        m1_chute = diags['M1_one_chute_default']['chutes'][0]
        self.assertTrue(m1_chute['within_binary32_zero_noise'])

    def test_core_mod_mass_inconsistent_still_rejected(self):
        report = self._analyze(variant='core_mod_mass')
        self.assertEqual(report['experiment_status'], STATUS_INVALID)
        self.assertIn('core_state_inconsistent', self._codes(report))

    def test_attempt_001_only_calculation_mode_diff(self):
        # the fixed analyzer on the REAL attempt-001 archive must not
        # produce naming/topology/modMass false positives — only the true
        # M2 calcSelect difference remains
        import json as _json
        from pathlib import Path as _Path
        attempt = (Path(__file__).resolve().parents[1] / 'experiments/control_mass/attempts/attempt-001')
        if not (attempt / 'result_after_analyzer_fix.json').is_file():
            self.skipTest('attempt-001 archive result missing')
        result = _json.loads(
            (attempt / 'result_after_analyzer_fix.json').read_text(
                encoding='utf-8'))
        self.assertEqual(result['experiment_status'], STATUS_INVALID)
        codes = [d['code'] for d in
                 result['validation']['diffs']]
        self.assertEqual(codes, ['chute_calculation_mode_mismatch'])


class AuditTankDefinitionTests(unittest.TestCase):
    """Duplicate TANK_DEFINITION MLI values must conflict, never
    last-value-wins."""

    def test_conflicting_mlilayers_values_are_ambiguous(self):
        from tools.research.audit_ksp_mass_residuals import _tank_definition_mlilayers
        tank_definitions = {
            'ServiceModule': {'values': [0, 10],
                              'definition_count': 2},
        }
        result = _tank_definition_mlilayers(
            r'C:\nope', 'ServiceModule', td_writers=None,
            tank_definitions=tank_definitions)
        self.assertFalse(result['determined'])
        self.assertEqual(result['source'], 'ambiguous')
        self.assertIsNone(result['value'])

    def test_duplicate_identical_values_are_determined(self):
        from tools.research.audit_ksp_mass_residuals import _tank_definition_mlilayers
        tank_definitions = {
            'ServiceModule': {'values': [0, 0], 'definition_count': 2},
        }
        result = _tank_definition_mlilayers(
            r'C:\nope', 'ServiceModule', td_writers=None,
            tank_definitions=tank_definitions)
        self.assertTrue(result['determined'])
        self.assertEqual(result['value'], 0)

    def test_illegal_value_is_ambiguous(self):
        from tools.research.audit_ksp_mass_residuals import _tank_definition_mlilayers
        tank_definitions = {
            'ServiceModule': {'values': [None], 'definition_count': 1},
        }
        result = _tank_definition_mlilayers(
            r'C:\nope', 'ServiceModule', td_writers=None,
            tank_definitions=tank_definitions)
        self.assertFalse(result['determined'])
        self.assertEqual(result['source'], 'ambiguous')

    def test_config_cache_parser_keeps_all_values(self):
        from tools.research.audit_ksp_mass_residuals import (
            _tank_definitions_from_config_cache)
        tmp = tempfile.mkdtemp(prefix='ksp_exp_td_')
        cache = Path(tmp) / 'ModuleManager.ConfigCache'
        cache.write_text(
            'UrlConfig\n{\n'
            '\tparentUrl = RealFuels/Resources/RealTankTypes.cfg\n'
            '\tTANK_DEFINITION\n\t{\n\t\tname = ServiceModule\n'
            '\t\tnumberOfMLILayers = 0\n\t}\n'
            '\tTANK_DEFINITION\n\t{\n\t\tname = ServiceModule\n'
            '\t\tnumberOfMLILayers = 10\n\t}\n'
            '\tTANK_DEFINITION\n\t{\n\t\tname = Cryogenic\n'
            '\t\tnumberOfMLILayers = 10\n\t}\n'
            '}\n', encoding='utf-8')
        definitions = _tank_definitions_from_config_cache(cache)
        self.assertEqual(definitions['ServiceModule']['values'], [0, 10])
        self.assertEqual(definitions['ServiceModule']['definition_count'], 2)
        self.assertEqual(definitions['Cryogenic']['values'], [10])


if __name__ == '__main__':
    unittest.main()
