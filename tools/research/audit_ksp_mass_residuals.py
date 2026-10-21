#!/usr/bin/env python
"""
Read-only KSP mass-residual forensics audit (phase 6B.3C.1 / 6B.3C.1-R).
========================================================================

Reads only the KSP install index (read-only), the VAB folder and the
paired .loadmeta files.  Never writes SQL, YAML or cache files; never
modifies KSP / GameData / VAB.

Usage:
    python -m tools.research.audit_ksp_mass_residuals [--ksp-root PATH] [--vab-dir PATH]
                                       [--json]

Exit codes: 0 = success; 2 = invalid input / runtime failure (details on
stderr; --json mode writes ONLY JSON to stdout).
"""
import argparse
import json
import sys
from pathlib import Path


def _stderr(text):
    print(text, file=sys.stderr)


def _fail(message):
    _stderr(f'错误：{message}')
    sys.exit(2)


def _resolve_path(raw, default, role):
    path = Path(raw) if raw is not None else Path(default)
    if not path.is_dir():
        _fail(f'{role} 不存在或不是目录: {path}')
    return path


def _assembly_provenance(ksp_root):
    """Read-only assembly provenance for RealChute / RealFuels DLLs."""
    import hashlib
    candidates = (
        ('RealChute', 'GameData/RealChute/Plugins/RealChute.dll'),
        ('RealFuels', 'GameData/RealFuels/Plugins/RealFuels.dll'),
    )
    evidence = {}
    for name, rel in candidates:
        path = Path(ksp_root) / rel
        if not path.is_file():
            continue
        sha256 = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        version = None
        try:
            from server.ksp_dotnet_il import read_dotnet_assembly
            assembly = read_dotnet_assembly(str(path))
            version = assembly.assembly_version
        except Exception:
            pass
        evidence[name] = {'name': name, 'version': version,
                          'sha256': sha256}
    from server.ksp_module_mass_semantics import assembly_provenance
    return {name: assembly_provenance(item)
            for name, item in evidence.items()}


def _realchute_il_evidence(ksp_root):
    """IL re-confirmation of the RealChute mass chain (read-only)."""
    from server import ksp_dotnet_il as il
    path = Path(ksp_root) / 'GameData' / 'RealChute' / 'Plugins' \
        / 'RealChute.dll'
    if not path.is_file():
        return {'status': 'missing'}
    try:
        assembly = il.read_dotnet_assembly(str(path))
    except Exception as exc:
        return {'status': 'error', 'message': str(exc)}
    t_module = 'RealChute.RealChuteModule'
    result = {
        'status': 'ok',
        'assembly_name': assembly.assembly_name,
        'assembly_version': assembly.assembly_version,
        'sha256': assembly.sha256,
        'get_module_mass': il.method_evidence(
            assembly, t_module, 'GetModuleMass'),
        'get_module_mass_change_when': il.method_evidence(
            assembly, t_module,
            'IPartMassModifier.GetModuleMassChangeWhen'),
        'update_mass': il.method_evidence(assembly, t_module, 'UpdateMass'),
        'parachute_chute_mass': il.method_evidence(
            assembly, 'RealChute.Parachute', 'get_ChuteMass'),
        'parachute_deployed_area': il.method_evidence(
            assembly, 'RealChute.Parachute', 'get_DeployedArea'),
        'rcutils_get_area': il.method_evidence(
            assembly, 'RealChute.RCUtils', 'GetArea'),
        'material_area_density': il.method_evidence(
            assembly, 'RealChute.Libraries.MaterialsLibrary.'
                      'MaterialDefinition', 'get_AreaDensity'),
    }
    for key, value in result.items():
        if isinstance(value, dict) and 'il_summary' in value:
            result[key] = {k: value[k] for k in value
                           if k != 'instructions'}
    return result


def _tank_definitions_from_config_cache(cache_path):
    """Read the FINAL ModuleManager.ConfigCache TANK_DEFINITION nodes.

    Returns a dict ``{tank_type: {'numberOfMLILayers': int|None,
    'definition_count': int, 'value_count': int}}`` built from the patched
    configuration (never from the raw RealFuels cfg).  Returns ``None``
    when the cache cannot be parsed.
    """
    if cache_path is None or not Path(cache_path).is_file():
        return None
    from server.ksp_confignode import ParseOptions, parse
    try:
        text = Path(cache_path).read_text(encoding='utf-8-sig')
        root = parse(text, options=ParseOptions(
            allow_anonymous_nodes=True,
            max_input_chars=64 * 1024 * 1024,
            max_nodes=150000))
    except Exception:
        return None
    definitions: dict = {}
    for top in root.children:
        for node in top.children:
            if node.name != 'TANK_DEFINITION':
                continue
            name = node.value('name')
            if not name:
                continue
            entry = definitions.setdefault(
                name, {'values': [], 'definition_count': 0})
            entry['definition_count'] += 1
            raw = node.value('numberOfMLILayers')
            if raw is not None:
                try:
                    entry['values'].append(int(raw))
                except ValueError:
                    entry['values'].append(None)
    return definitions


def _collect_mft_tank_types(ksp_root, index=None, vab_dir=None):
    """Tank types actually used by the evaluated vehicles.

    Sources (both required to agree where present):

    * craft instances: ``ModuleFuelTanks.type`` saved in the evaluated
      VAB crafts (``vab_dir``, default the KSP install's VAB folder);
    * patched configuration: the primary ``mk1pod_v2`` PART record's
      ``ModuleFuelTanks.type`` in the final ConfigCache.

    Returns ``{'types': {type: {'craft_instances': n, 'patched_config':
    bool}}, 'diagnostics': [...]}``.  A type seen in a craft but absent
    from the patched config is a diagnostic (fail closed downstream).
    """
    from server.vab_parser import parse_craft
    craft_types: dict = {}
    if vab_dir is None:
        vab_dir = Path(ksp_root) / 'VAB'
    vab_dir = Path(vab_dir)
    if vab_dir.is_dir():
        for craft_path in sorted(vab_dir.glob('*.craft')):
            if craft_path.name.endswith('.original'):
                continue
            try:
                draft = parse_craft(
                    craft_path.read_text(encoding='utf-8-sig'),
                    craft_path.name)
            except Exception:
                continue
            for part in draft.get('parts') or []:
                entries = (part.get('module_evidence') or {}).get(
                    'ModuleFuelTanks', [])
                for entry in entries:
                    tank_type = entry.get('type')
                    if tank_type:
                        craft_types[tank_type] = \
                            craft_types.get(tank_type, 0) + 1
    patched = set()
    if index is not None:
        for record in index.parts:
            entries = (record.get('module_evidence') or {}).get(
                'ModuleFuelTanks', [])
            for entry in entries:
                if entry.get('type'):
                    patched.add(entry['type'])
    diagnostics = []
    for tank_type in sorted(craft_types):
        if tank_type not in patched:
            diagnostics.append({
                'code': 'tank_type_missing_in_patched_config',
                'message': f'craft 使用 tank type {tank_type!r}，但补丁后 '
                           f'ModuleFuelTanks.type 中没有该类型',
                'tank_type': tank_type,
            })
    types = {tank_type: {'craft_instances': count,
                         'patched_config': tank_type in patched}
             for tank_type, count in craft_types.items()}
    for tank_type in sorted(patched):
        types.setdefault(tank_type, {'craft_instances': 0,
                                     'patched_config': True})
    return {'types': types, 'diagnostics': diagnostics}


def _tank_definition_mlilayers(ksp_root, tank_type, td_writers=None,
                               tank_definitions=None):
    """Base MLI layer count of one tank type from the FINAL patched
    configuration (ModuleManager.ConfigCache TANK_DEFINITION nodes).

    Returns ``{'value': int|None, 'determined': bool, 'source': str,
    'reason': str}``.  A value is only ``determined`` when the patched
    configs carry ONE unambiguous ``numberOfMLILayers`` or — when every
    patched definition for the type lacks the key — the IL proof that
    ``TankDefinition.numberOfMLILayers`` is never written by any method
    (``td_writers`` scan complete and empty) plus the reflection-based
    load path leave the field at its CLR zero default 0.  Missing or
    conflicting evidence fails closed (``determined=false``).
    """
    if tank_definitions is None:
        cache_path = Path(ksp_root) / 'GameData' \
            / 'ModuleManager.ConfigCache'
        tank_definitions = _tank_definitions_from_config_cache(cache_path)
    if not tank_definitions:
        return {'value': None, 'determined': False,
                'source': 'config_cache_unreadable',
                'reason': 'ModuleManager.ConfigCache 无法解析或缺失'}
    entry = tank_definitions.get(tank_type)
    if entry is None:
        return {'value': None, 'determined': False,
                'source': 'missing',
                'reason': f'最终配置中没有 {tank_type} 的 TANK_DEFINITION'}
    raw_values = entry['values']
    if raw_values:
        # ALL definitions' numberOfMLILayers values are kept and compared:
        # a conflict (or an illegal value) between definitions must NOT be
        # resolved by the last value seen
        if any(value is None for value in raw_values):
            return {'value': None, 'determined': False,
                    'source': 'ambiguous',
                    'reason': f'numberOfMLILayers 非法'
                              f'（{entry["definition_count"]} 处定义，'
                              f'原始值 {raw_values!r}）'}
        if all(value == raw_values[0] for value in raw_values):
            return {'value': raw_values[0], 'determined': True,
                    'source': 'config_cache_tank_definition',
                    'reason': f'补丁后配置 {len(raw_values)} 处定义一致，'
                              f'numberOfMLILayers={raw_values[0]}'}
        return {'value': None, 'determined': False,
                'source': 'ambiguous',
                'reason': f'numberOfMLILayers 冲突'
                          f'（{entry["definition_count"]} 处定义，'
                          f'值 {raw_values!r} 不一致）'}
    if td_writers is None or not td_writers.get('scan_complete', False):
        return {'value': None, 'determined': False,
                'source': 'writer_scan_incomplete',
                'reason': 'TankDefinition.numberOfMLILayers 无 IL 写入者'
                          '的扫描不完整'}
    if td_writers.get('writers'):
        return {'value': None, 'determined': False,
                'source': 'il_writer_present',
                'reason': 'TankDefinition.numberOfMLILayers 存在 IL 写入者'}
    return {'value': 0, 'determined': True,
            'source': 'config_absent_and_no_il_writer',
            'reason': '补丁后配置无 numberOfMLILayers 键，且程序集中无任何'
                      '写入方法（LoadObjectFromConfig 反射加载不改变'
                      '字段），字段保持 CLR 零初始化 0'}


def _mft_il_evidence(ksp_root, tank_types=None, for_json=False,
                     vab_dir=None):
    """Static IL evidence for ModuleFuelTanks.massIsAdditive (read-only).

    ``tank_types`` limits the TANK_DEFINITION evidence to the given types
    (collected from the evaluated crafts and patched config); when None
    they are collected from ``vab_dir`` (default the KSP install's VAB
    folder) + ConfigCache.  The FULL instruction streams are used
    in-process only; ``for_json`` strips every instruction array and keeps
    method tokens, body SHA-256, byte counts, decode state and bounded
    summaries.
    """
    from server import ksp_dotnet_il as il
    path = Path(ksp_root) / 'GameData' / 'RealFuels' / 'Plugins' \
        / 'RealFuels.dll'
    if not path.is_file():
        return {'status': 'missing'}
    try:
        assembly = il.read_dotnet_assembly(str(path))
    except Exception as exc:
        return {'status': 'error', 'message': str(exc)}
    t_mft = 'RealFuels.Tanks.ModuleFuelTanks'

    def stripped(evidence, keep_instructions=False):
        if not evidence:
            return None
        if keep_instructions:
            return evidence
        return {k: evidence[k] for k in evidence
                if k != 'instructions'}

    additive_field = il.field_evidence(assembly, t_mft, 'massIsAdditive')
    mass_field = il.field_evidence(assembly, t_mft, 'mass')
    writers = il.methods_writing_field(
        assembly, int(additive_field['token'], 16)) \
        if additive_field else {'writers': [], 'writer_count': 0,
                                'method_bodies_total': 0,
                                'method_bodies_unparsed': 0,
                                'method_bodies_decode_incomplete': 0,
                                'scan_complete': False}
    calculate = il.method_evidence(assembly, t_mft, 'CalculateMass')
    calculate_instructions = []
    calculate_decode = {'decode_complete': False, 'decode_errors': [],
                        'first_error_offset': None}
    if calculate and calculate.get('il_error') is None and \
            calculate.get('decode_complete'):
        body = assembly.method_body(calculate['rva'])
        if body is not None:
            decoded = assembly.decode_il(body)
            calculate_instructions = assembly.annotate_instructions(
                decoded['instructions'])
            calculate_decode = {
                'decode_complete': decoded['decode_complete'],
                'decode_errors': decoded['decode_errors'],
                'first_error_offset': decoded['first_error_offset'],
            }
    parse_overloads = il.methods_named(assembly, t_mft, 'ParseBaseMass')
    string_parse = None
    for overload in parse_overloads:
        texts = [str(insn.get('text') or insn.get('name'))
                 for insn in overload.get('instructions') or []]
        if any('basemassConst' in text for text in texts) and \
                any('basemassPV' in text for text in texts):
            string_parse = overload
            break
    get_module_mass = il.method_evidence(assembly, t_mft, 'GetModuleMass')
    get_module_mass_change_when = il.method_evidence(
        assembly, t_mft, 'GetModuleMassChangeWhen')
    calculate_callers = il.methods_calling_method(
        assembly, int(calculate['token'], 16)) if calculate else None

    def first_complete(matches):
        for entry in matches:
            if entry.get('decode_complete'):
                return entry
        return matches[0] if matches else None

    get_total_mlilayers = first_complete(il.methods_named(
        assembly, t_mft, 'get_totalMLILayers'))
    get_number_of_added = first_complete(il.methods_named(
        assembly, t_mft, 'get_numberOfAddedMLILayers'))
    calculate_mass_rf = first_complete(il.methods_named(
        assembly, t_mft, 'CalculateMassRF'))
    # tank types come from the evaluated crafts + patched config — never
    # hardcoded; only types that actually appear are processed
    index = None
    if tank_types is None:
        from server.ksp_install_index import build_ksp_index
        index = build_ksp_index(ksp_root)
        collection = _collect_mft_tank_types(ksp_root, index=index,
                                             vab_dir=vab_dir)
        # only types that ACTUALLY appear in craft instances get evidence
        # processing; patched-config-only types are not processed
        tank_types = sorted(
            tank_type for tank_type, info in collection['types'].items()
            if info['craft_instances'] and info['craft_instances'] > 0)
    else:
        collection = {'types': {t: {'craft_instances': None,
                                    'patched_config': None}
                                for t in tank_types},
                      'diagnostics': []}
    tank_definitions = _tank_definitions_from_config_cache(
        Path(ksp_root) / 'GameData' / 'ModuleManager.ConfigCache')
    td_field = il.field_evidence(
        assembly, 'RealFuels.Tanks.TankDefinition', 'numberOfMLILayers')
    td_writers = il.methods_writing_field(
        assembly, int(td_field['token'], 16)) if td_field else None
    tank_definition_mlilayers = {
        tank_type: _tank_definition_mlilayers(
            ksp_root, tank_type, td_writers,
            tank_definitions=tank_definitions)
        for tank_type in tank_types}

    full = {
        'status': 'ok',
        'assembly_name': assembly.assembly_name,
        'assembly_version': assembly.assembly_version,
        'sha256': assembly.sha256,
        'mass_is_additive_field': additive_field,
        'mass_field': mass_field,
        'mass_is_additive_writers': writers,
        'calculate_mass': {
            'method': stripped(calculate),
            'instructions': calculate_instructions,
            'decode_complete': calculate_decode['decode_complete'],
            'decode_errors': calculate_decode['decode_errors'],
            'first_error_offset': calculate_decode['first_error_offset'],
            'mass_is_additive_field_token':
                int(additive_field['token'], 16)
                if additive_field else None,
            'mass_field_token': int(mass_field['token'], 16)
                if mass_field else None,
            'callers': calculate_callers,
        },
        'parse_base_mass': {
            'method': stripped(string_parse),
            'instructions': (string_parse or {}).get('instructions') or [],
        },
        'get_module_mass': stripped(get_module_mass),
        'get_module_mass_change_when':
            stripped(get_module_mass_change_when),
        'get_total_mlilayers': stripped(get_total_mlilayers,
                                        keep_instructions=not for_json),
        'get_number_of_added': stripped(get_number_of_added,
                                        keep_instructions=not for_json),
        'calculate_mass_rf': stripped(calculate_mass_rf,
                                      keep_instructions=not for_json),
        'tank_definition_number_of_mlilayers': td_field,
        'tank_definition_number_of_mlilayers_writers': td_writers,
        'tank_definition_mlilayers': tank_definition_mlilayers,
        'tank_type_evidence': {
            'types': collection['types'],
            'diagnostics': collection['diagnostics'],
            'source': 'craft_instances_and_patched_config',
        },
        'constructor': stripped(
            il.method_evidence(assembly, t_mft, '.ctor')),
        'on_load': stripped(il.method_evidence(assembly, t_mft, 'OnLoad')),
        'calculate_mass_rf_evidence': stripped(calculate_mass_rf),
    }
    if for_json:
        # full instruction arrays never reach the public JSON
        full['calculate_mass'] = {
            k: v for k, v in full['calculate_mass'].items()
            if k != 'instructions'}
        full['parse_base_mass'] = {
            k: v for k, v in full['parse_base_mass'].items()
            if k != 'instructions'}
    return full


def _module_semantics(index, craft_path, draft, evidence, reconciliation,
                      forensics, mft_il_evidence=None):
    """Per-vehicle module mass semantics + hypotheses (read-only)."""
    from server.ksp_module_mass_semantics import (
        evaluate_hypotheses, mft_component_evidence,
        realchute_component_evidence)
    from server.ksp_numeric_precision_forensics import evaluate_five_models
    numeric = evaluate_five_models(
        draft, evidence, reconciliation,
        pdc_diff_total=forensics.get('pdc_candidate_diff_total_t'))
    rc = realchute_component_evidence(draft, index)
    mft = mft_component_evidence(draft, index,
                                 il_evidence=mft_il_evidence)
    hypotheses = evaluate_hypotheses(
        forensics, rc, mft,
        numeric['models']['float32_craft_order']['estimated_total_t'],
        reconciliation.get('source_total_mass_t'),
        forensics.get('pdc_candidate_diff_total_t'),
        craft_draft=draft)
    return {
        'craft_filename': craft_path.name,
        'realchute': rc,
        'module_fuel_tanks': mft,
        'hypotheses': hypotheses,
    }


def _process_vehicle(craft_path, index, mft_il_evidence=None,
                     provenance=None):
    """Parse + evidence + reconcile + forensics for one craft; any failure
    becomes a contextual exit-2 error (never a fake success)."""
    try:
        text = craft_path.read_text(encoding='utf-8-sig')
    except OSError as exc:
        _fail(f'读取 {craft_path.name} 失败: {exc}')
    from server.vab_parser import parse_craft
    try:
        draft = parse_craft(text, craft_path.name)
    except Exception as exc:
        _fail(f'解析 {craft_path.name} 失败: {exc}')

    from server.ksp_part_matcher import match_craft_parts
    from server.ksp_mass_evidence import extract_part_mass_evidence
    try:
        evidence = extract_part_mass_evidence(
            draft, index, match_craft_parts(draft, index),
            provenance=provenance)
    except Exception as exc:
        _fail(f'质量证据提取 {craft_path.name} 失败: {exc}')

    loadmeta = None
    loadmeta_path = craft_path.with_suffix('.loadmeta')
    if loadmeta_path.is_file():
        from server.import_service import _loadmeta_metadata
        try:
            loadmeta = _loadmeta_metadata(
                loadmeta_path.read_text(encoding='utf-8-sig',
                                        errors='replace'))
        except OSError as exc:
            _fail(f'读取 {loadmeta_path.name} 失败: {exc}')

    from server.ksp_craft_mass_reconciliation import reconcile_craft_mass
    try:
        reconciliation = reconcile_craft_mass(
            draft, evidence, loadmeta,
            loadmeta_filename=loadmeta_path.name if loadmeta_path.is_file()
            else None)
    except Exception as exc:
        _fail(f'质量对账 {craft_path.name} 失败: {exc}')

    from server.ksp_mass_forensics import analyze_vehicle_forensics
    try:
        forensics = analyze_vehicle_forensics(
            draft, evidence, reconciliation, ksp_index=index)
    except Exception as exc:
        _fail(f'取证分析 {craft_path.name} 失败: {exc}')

    try:
        semantics = _module_semantics(
            index, craft_path, draft, evidence, reconciliation,
            forensics, mft_il_evidence=mft_il_evidence)
    except Exception as exc:
        _fail(f'模块语义取证 {craft_path.name} 失败: {exc}')

    from server.ksp_numeric_precision_forensics import evaluate_five_models
    try:
        numeric = evaluate_five_models(
            draft, evidence, reconciliation,
            pdc_diff_total=forensics.get('pdc_candidate_diff_total_t'))
    except Exception as exc:
        _fail(f'数值精度取证 {craft_path.name} 失败: {exc}')
    numeric['craft_filename'] = craft_path.name
    numeric['parachute_single_count'] = forensics['parachute_single_count']

    forensics['craft_filename'] = craft_path.name
    return forensics, numeric, semantics


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='KSP craft mass-residual forensics audit (read-only)')
    parser.add_argument('--ksp-root', default=None,
                        help='KSP install root (default: '
                             r'D:\SteamLibrary\steamapps\common\Kerbal '
                             r'Space Program)')
    parser.add_argument('--vab-dir', default=None,
                        help='VAB craft directory (default: ./VAB)')
    parser.add_argument('--json', action='store_true',
                        help='emit a single JSON document instead of a table')
    args = parser.parse_args(argv)

    # Deterministic UTF-8 output on all platforms (including Windows).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass

    ksp_root = _resolve_path(
        args.ksp_root,
        r'D:\SteamLibrary\steamapps\common\Kerbal Space Program',
        'KSP 根目录')
    vab_dir = _resolve_path(args.vab_dir, 'VAB', 'VAB 目录')

    from server.ksp_install_index import build_ksp_index
    try:
        index = build_ksp_index(ksp_root)
    except Exception as exc:
        _fail(f'KSP 索引构建失败: {exc}')
    if index.status != 'ok':
        _fail(f'KSP 索引构建失败（status={index.status}）')

    mft_il_evidence = _mft_il_evidence(ksp_root, vab_dir=vab_dir)
    realchute_il_evidence = _realchute_il_evidence(ksp_root)
    mft_il_evidence_json = _mft_il_evidence(ksp_root, for_json=True,
                                            vab_dir=vab_dir)

    crafts = sorted(p for p in vab_dir.glob('*.craft')
                    if not p.name.endswith('.original'))
    if not crafts:
        _fail(f'{vab_dir} 中没有 .craft 文件')

    from server.ksp_runtime_fingerprints import ksp_runtime_provenance
    provenance = ksp_runtime_provenance(ksp_root)

    vehicle_reports = []
    numeric_reports = []
    semantics_reports = []
    for craft_path in crafts:
        forensics, numeric, semantics = _process_vehicle(
            craft_path, index, mft_il_evidence=mft_il_evidence,
            provenance=provenance)
        vehicle_reports.append(forensics)
        numeric_reports.append(numeric)
        semantics_reports.append(semantics)

    from server.ksp_mass_forensics import fleet_forensics
    from server.ksp_numeric_precision_forensics import (
        fleet_model_comparison)
    fleet = fleet_forensics(vehicle_reports)
    fleet_numeric = fleet_model_comparison(numeric_reports)
    assembly_provenance = _assembly_provenance(ksp_root)

    if args.json:
        payload = json.dumps({
            'vehicles': vehicle_reports,
            'fleet': fleet,
            'numeric_models': numeric_reports,
            'fleet_numeric_comparison': fleet_numeric,
            'module_mass_semantics': semantics_reports,
            'module_mass_hypotheses': [
                {key: report[key] for key in
                 ('craft_filename', 'hypotheses')}
                for report in semantics_reports],
            'assembly_provenance': assembly_provenance,
            'realchute_il_evidence': realchute_il_evidence,
            'module_fuel_tanks_il_evidence': mft_il_evidence_json,
        }, ensure_ascii=False, indent=1)
        sys.stdout.write(payload + '\n')
        sys.stdout.flush()
        return 0

    header = ('craft | residual_include_all_t | pdc | pdc_diff_total_t | '
              'residual_after_pdc_t | parachute | mk1pod')
    print(header)
    print('-' * len(header))
    for report in vehicle_reports:
        def fmt(value):
            return 'None' if value is None else f'{value:.8f}'
        print(f'{report["craft_filename"]} '
              f'| {fmt(report["residual_include_all_t"])} '
              f'| {report["pdc_instance_count"]} '
              f'| {fmt(report["pdc_candidate_diff_total_t"])} '
              f'| {fmt(report["residual_after_pdc_t"])} '
              f'| {report["parachute_single_count"]} '
              f'| {report["mk1pod_v2_count"]}')
    print()
    print(f'总 PDC 实例数: {fleet["total_pdc_instances"]}')
    print(f'普通载具（无 parachuteSingle）: '
          f'{fleet["normal_vehicles_within_tolerance"]}/'
          f'{fleet["normal_vehicle_count"]} 在取证容差 '
          f'{fleet["forensic_abs_tolerance_t"]} t 内')
    for item in fleet['normal_vehicle_after_pdc']:
        if abs(item['residual_after_pdc_t']) > fleet[
                'forensic_abs_tolerance_t']:
            print(f'  超差: {item["craft"]} '
                  f'{item["residual_after_pdc_t"]:+.8f} t')
    print('特殊载具（含 parachuteSingle）扣除后残差:')
    for item in fleet['special_vehicle_after_pdc']:
        print(f'  {item["craft"]} {item["residual_after_pdc_t"]:+.8f} t '
              f'(预期 ~{fleet["special_residual_band_t"]} t)')
    print(f'共线性状态: {fleet["collinearity_status"]}')
    print(f'PDC 假设: {fleet["pdc_hypothesis_status"]}')
    print(f'剩余 ~0.075 t 归因: {fleet["remaining_residual_hypothesis_status"]}')
    print(f'  {fleet["remaining_residual_message"]}')
    print()
    print('五模型比较（17 个无 parachuteSingle 载具，PDC 后残差）:')
    print('model | within | max_abs_err | MAE | RMSE | worst')
    for model, stats in fleet_numeric['models'].items():
        def fmt_stat(value):
            return 'None' if value is None else f'{value:.4e}'
        print(f"{model} | {stats['within_tolerance_count']}/"
              f"{stats['valid_sample_count']} "
              f"| {fmt_stat(stats['max_abs_error_t'])} "
              f"| {fmt_stat(stats['mae_t'])} "
              f"| {fmt_stat(stats['rmse_t'])} "
              f"| {stats['worst_vehicle'] or 'None'}")
    print()
    print('每载具五模型 PDC 后残差:')
    print('craft | double | in_f32_double | f32_order | f32_reverse | '
          'f32_final_cast')
    for numeric in numeric_reports:
        def model_value(model):
            value = numeric['models'][model]['residual_after_pdc_t']
            return 'None/证据不足' if value is None else f'{value:.8f}'
        print(f"{numeric['craft_filename']} "
              f"| {model_value('double_order')} "
              f"| {model_value('input_float32_then_double')} "
              f"| {model_value('float32_craft_order')} "
              f"| {model_value('float32_reverse_order')} "
              f"| {model_value('double_sum_then_float32')}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
