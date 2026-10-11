"""
Module mass semantics forensics (phase 6B.3C.2B-R).
====================================================

Read-only analysis of how the installed mods (RealChute 1.4.9.5,
RealFuels 15.14.0) modify PART mass, verified against method IL evidence
(produced by the read-only ECMA-335 parser in ``ksp_dotnet_il.py``) and
applied to the three special vehicles (1_2T / 1_5T / 2_5T Test.craft).

IL-verified chains (from the installed assemblies):

RealChuteModule (RealChute.dll, sha 884ACB3F...):

    GetModuleMass(defaultMass, sit)   -> ldfld massDelta; ret
    GetModuleMassChangeWhen()         -> ldc.i4.0 (CONSTANTLY)
    UpdateMass():                     if (partPrefab == null) { massDelta = 0 }
        massDelta = caseMass + Sum(p => p.ChuteMass) - partPrefab.mass
    Parachute.get_ChuteMass()         -> get_DeployedArea() * mat.get_AreaDensity()
    Parachute.get_DeployedArea()      -> RCUtils.GetArea(deployedDiameter)
    RCUtils.GetArea(d)                -> conv.r4(pi * d^2 / 4)   # double math,
                                                                 # final float cast
    MaterialDefinition.get_AreaDensity() -> ldfld areaDensity

ModuleFuelTanks (RealFuels.dll, sha 6C6629ED...):

    GetModuleMass(defaultMass, sit)  -> massIsAdditive ? mass : mass - defaultMass
    GetModuleMassChangeWhen()        -> ldc.i4.0 (CONSTANTLY)
    CalculateMass()                  -> massDirty gate; when
        basemassConst + basemassPV * volume + MLI term < 0:
            mass = 0; massIsAdditive = true
        else:
            mass = (basemassConst + basemassPV * volume + MLI term
                    + Sum(tank masses)) * MassMult
    ParseBaseMass(string)            -> basemassConst / basemassPV split
    (see ksp_dotnet_il evidence; massIsAdditive has NO writer in .ctor and
    NO value in patched config -> CLR zero-init false UNTIL CalculateMass
    conditionally assigns true)

Everything in this module is PURE: disk access happens only via the
caller-provided evidence objects; no mutation, no SQL/YAML, no Flask,
JSON-serializable and deterministic.  Hypotheses are evaluated with
external constraints (assembly IL, cfg, instance state) — collinear
features are never separated by regression.

Symbol convention (unambiguous, shared by JSON and docs):

    residual            = estimated_total_mass - loadmeta_totalMass
    baseline_residual_t = residual after the PDC candidate correction
                          (float32_craft_order total - pdc_diff_total
                          - loadmeta totalMass); missing pdc_diff_total
                          keeps the baseline MISSING (never `or 0.0`)
    predicted_mass_delta_t = the module semantics' predicted change to the
                          estimated total mass (a PART-mass modifier, NOT
                          the part mass and NOT the whole-ship residual)
    predicted_residual_t = baseline_residual_t + predicted_mass_delta_t

Hypotheses only apply to vehicles that actually contain the corresponding
candidate parts; for every other vehicle the hypothesis is
``not_applicable`` (a negative control can never be ``supported``).
"""

from __future__ import annotations

import math
import struct
from typing import Dict, List, Optional

SCHEMA_VERSION = 1
SOURCE_KIND = 'ksp_module_mass_semantics'

SUPPORTED = 'supported'
CONTRADICTED = 'contradicted'
INSUFFICIENT_EVIDENCE = 'insufficient_evidence'
ASSEMBLY_MISMATCH = 'assembly_mismatch'
AMBIGUOUS_MODULE_STATE = 'ambiguous_module_state'
NOT_APPLICABLE = 'not_applicable'

# RealChute 1.4.9.5 / RealFuels 15.14.0 expected hashes (task-provided).
REALCHUTE_EXPECTED_SHA256 = '884ACB3F4537A2164FA4B6D798BE538C6FED54997C5DE0A7674527BC973B56D5'
REALFUELS_EXPECTED_SHA256 = '6C6629ED95955F4F0BD88041319444DA45115223415BDC20FEBDB04A33377B5B'
# ConfigCache hash of the authoritative experiment fingerprint
# (attempt-002; current install GameData/ModuleManager.ConfigCache).
CONFIG_CACHE_EXPECTED_SHA256 = \
    '15C6D8DD36FD9310C964D67F0172906311956E19824AE38F66A1269A2BCCAFF3'

# Forensic comparison tolerance (loadmeta serialization precision only).
HYPOTHESIS_ABS_TOLERANCE_T = 5e-5

# Candidate parts for the module hypotheses.  Note the craft base_part
# uses a dot while the patched PART name in the ConfigCache uses an
# underscore (mk1pod.v2 vs mk1pod_v2).
REALCHUTE_CANDIDATE_PART = 'parachuteSingle'
MFT_CANDIDATE_PART = 'mk1pod.v2'
MFT_CONFIG_PART = 'mk1pod_v2'


def _finite(value):
    if isinstance(value, bool):
        return None
    if value is None or value == '':
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def to_float32(value):
    number = _finite(value)
    if number is None or number < 0:
        return None
    if number == 0:
        number = 0.0
    try:
        return struct.unpack('<f', struct.pack('<f', number))[0]
    except OverflowError:
        return None


def chute_deployed_area(deployed_diameter):
    """GetArea IL: conv.r4(pi * d^2 / 4) — double math, final float cast."""
    diameter = _finite(deployed_diameter)
    if diameter is None or diameter < 0:
        return None
    return to_float32(math.pi * diameter * diameter / 4.0)


def chute_mass(deployed_area, area_density):
    """ChuteMass = DeployedArea * AreaDensity (float multiply)."""
    area = _finite(deployed_area)
    density = _finite(area_density)
    if area is None or density is None or area < 0 or density < 0:
        return None
    return to_float32(area * density)


def realchute_settled_part_mass(case_mass, chute_masses):
    """UpdateMass IL accumulation WITHOUT the prefab subtraction.

    ``caseMass + Sum(ChuteMass)`` in the exact IL order (float32 sum of
    case first, then each chute mass).  This is the SETTLED part mass —
    what the part actually contributes once RealChute has run — while
    :func:`realchute_mass_delta` is the settled mass minus the
    ConfigCache prefab mass (the PART-mass modifier KSP applies).
    """
    case = _finite(case_mass)
    if case is None:
        return None, ['insufficient_realchute_evidence']
    values = [case]
    for chute in chute_masses:
        value = _finite(chute)
        if value is None:
            return None, ['invalid_chute_mass']
        values.append(value)
    accumulator = 0.0
    for value in values:
        accumulator = to_float32(accumulator + value)
        if accumulator is None:
            return None, ['float32_accumulation_overflow']
    return accumulator, []


def realchute_mass_delta(case_mass, chute_masses, prefab_part_mass):
    """UpdateMass IL: caseMass + Sum(ChuteMass) - partPrefab.mass (float).

    The settled part mass is accumulated first (identical float32 order
    to :func:`realchute_settled_part_mass`), then the prefab mass is
    subtracted with a final float32 cast — byte-for-byte the IL shape.
    """
    settled, reasons = realchute_settled_part_mass(case_mass, chute_masses)
    if settled is None:
        return None, reasons
    prefab = _finite(prefab_part_mass)
    if prefab is None:
        return None, ['insufficient_realchute_evidence']
    accumulator = to_float32(settled - prefab)
    if accumulator is None:
        return None, ['float32_accumulation_overflow']
    return accumulator, []


def assembly_provenance(assembly_evidence: Optional[dict]) -> dict:
    """Stable provenance for an assembly; mismatched hashes reject any
    known semantics."""
    if not assembly_evidence:
        return {'status': 'missing', 'semantics_usable': False}
    name = assembly_evidence.get('name')
    sha256 = (assembly_evidence.get('sha256') or '').upper()
    if name == 'RealChute':
        match = sha256 == REALCHUTE_EXPECTED_SHA256
    elif name == 'RealFuels':
        match = sha256 == REALFUELS_EXPECTED_SHA256
    else:
        match = False
    return {
        'name': name,
        'version': assembly_evidence.get('version'),
        'sha256': sha256,
        'expected_sha256': (REALCHUTE_EXPECTED_SHA256 if name == 'RealChute'
                            else REALFUELS_EXPECTED_SHA256
                            if name == 'RealFuels' else None),
        'hash_match': match,
        'semantics_usable': match,
    }


# --------------------------------------------------------------------------
# Module-state normalization (per module type)
# --------------------------------------------------------------------------

def _normalize_realchute_module(entry):
    parachutes = []
    for chute in entry.get('parachutes') or []:
        parachutes.append((
            chute.get('material'),
            _finite(chute.get('deployed_diameter')),
            _finite(chute.get('pre_deployed_diameter')),
            _finite(chute.get('mass')),
            _finite(chute.get('chuteCount'))))
    return (_finite(entry.get('case_mass')),
            _finite(entry.get('spare_chutes')),
            tuple(parachutes))


def _normalize_procedural_chute(entry):
    chutes = []
    for chute in entry.get('chutes') or []:
        chutes.append((
            _finite(chute.get('dep_diam')),
            _finite(chute.get('pre_dep_diam')),
            _finite(chute.get('mass')),
            _finite(chute.get('chute_count')),
            chute.get('get_mass'),
            chute.get('use_dry')))
    return tuple(chutes)


def _normalize_module_fuel_tanks(entry):
    tanks = []
    for tank in entry.get('tanks') or []:
        tanks.append((
            tank.get('name'),
            _finite(tank.get('mass')),
            _finite(tank.get('utilization')),
            _finite(tank.get('amount')),
            _finite(tank.get('max_amount'))))
    return (_finite(entry.get('mass')),
            _finite(entry.get('volume')),
            _finite(entry.get('basemass')),
            entry.get('mass_is_additive'),
            _finite(entry.get('utilization')),
            _finite(entry.get('totalVolume')),
            tuple(tanks))


_MODULE_NORMALIZERS = {
    'RealChuteModule': _normalize_realchute_module,
    'ProceduralChute': _normalize_procedural_chute,
    'ModuleFuelTanks': _normalize_module_fuel_tanks,
}


def _module_state(part, module_name):
    """Unique module state of a craft part, normalized PER MODULE TYPE.

    RealChuteModule compares (case_mass, spare_chutes, PARACHUTE rows);
    ProceduralChute compares the actual CHUTE rows (diameter, mass,
    chuteCount, getMass, useDry) that drive the calculation; ModuleFuelTanks
    compares (mass, volume, basemass, mass_is_additive, utilization,
    totalVolume, TANK rows).  Ambiguous states are rejected.
    """
    entries = (part.get('module_evidence') or {}).get(module_name, [])
    if not entries:
        return None, ['missing_module_state']
    normalizer = _MODULE_NORMALIZERS.get(module_name)
    if normalizer is None:
        return None, ['unsupported_module_state']
    first = normalizer(entries[0])
    for entry in entries[1:]:
        if normalizer(entry) != first:
            return None, [AMBIGUOUS_MODULE_STATE]
    return entries[0], []


def _config_module_entry(ksp_index, part_name, module_name):
    """Primary patched PART record's module config entry (evidence)."""
    if ksp_index is None:
        return None
    record = ksp_index.primary_part(part_name)
    if not record:
        return None
    entries = (record.get('module_evidence') or {}).get(module_name, [])
    return entries[0] if entries else None


def _config_part_mass(ksp_index, part_name):
    """Patched PART mass from the index (evidence), or None."""
    if ksp_index is None:
        return None
    record = ksp_index.primary_part(part_name)
    if not record:
        return None
    return _finite(record.get('mass_t'))


def _config_material_area_density(ksp_index, material):
    """MATERIAL areaDensity from the index (evidence), or None."""
    if ksp_index is None or not material:
        return None
    record = ksp_index.find_material(material)
    if not record:
        return None
    return _finite(record.get('area_density'))


# --------------------------------------------------------------------------
# RealChute component evidence
# --------------------------------------------------------------------------

def realchute_component_evidence(craft_draft, ksp_index) -> dict:
    """Per-vehicle RealChute evidence: case mass, chute mass, mass delta.

    All values are evidence-derived: caseMass from the craft module state,
    diameter from the craft ProceduralChute CHUTE row (fallback: patched
    PARACHUTE config), material from the patched RealChuteModule config,
    areaDensity from the MATERIAL index and prefab mass from the patched
    PART mass.  A missing value yields ``insufficient_realchute_evidence``
    — no hardcoded constants are ever substituted.
    """
    parts = list((craft_draft or {}).get('parts') or [])
    diagnostics: List[dict] = []
    components = []
    total_chute_mass = 0.0
    total_mass_delta = 0.0
    complete = True
    for part in parts:
        if part.get('base_part') != REALCHUTE_CANDIDATE_PART:
            continue
        entry, reasons = _module_state(part, 'RealChuteModule')
        procedural, proc_reasons = _module_state(part, 'ProceduralChute')
        if entry is None or procedural is None:
            complete = False
            diagnostics.append({
                'code': reasons[0] if entry is None else proc_reasons[0],
                'message': f'实例 {part.get("part")} 的 RealChute 状态不完整',
            })
            continue
        case_mass = _finite(entry.get('case_mass'))
        missing: List[str] = []

        # diameter: craft ProceduralChute CHUTE row wins, else config
        chute_rows = (procedural.get('chutes') or [])
        diameter_raw = None
        diameter_source = None
        for chute in chute_rows:
            value = _finite(chute.get('dep_diam'))
            if value is not None:
                diameter_raw = value
                diameter_source = 'craft_procedural_chute'
                break
        if diameter_raw is None:
            config = _config_module_entry(
                ksp_index, REALCHUTE_CANDIDATE_PART, 'RealChuteModule')
            for chute in (config or {}).get('parachutes') or []:
                value = _finite(chute.get('deployed_diameter'))
                if value is not None:
                    diameter_raw = value
                    diameter_source = 'patched_parachute_config'
                    break
        if diameter_raw is None:
            missing.append('deployed_diameter')

        # material: patched RealChuteModule config (craft CHUTE rows carry
        # no material evidence in the .craft format)
        material = None
        material_source = None
        config = _config_module_entry(
            ksp_index, REALCHUTE_CANDIDATE_PART, 'RealChuteModule')
        for chute in (config or {}).get('parachutes') or []:
            if chute.get('material'):
                material = chute.get('material')
                material_source = 'patched_parachute_config'
                break
        if material is None:
            missing.append('material')

        area_density = _config_material_area_density(ksp_index, material)
        if area_density is None:
            missing.append('material_area_density')

        prefab_mass = _config_part_mass(
            ksp_index, REALCHUTE_CANDIDATE_PART)
        if prefab_mass is None:
            missing.append('prefab_part_mass')

        diameter = diameter_raw
        if diameter is None or area_density is None:
            complete = False
            diagnostics.append({
                'code': 'insufficient_realchute_evidence',
                'message': f'实例 {part.get("part")} 缺少直径或材料密度'
                           f'（{", ".join(missing)}）',
            })
            continue
        deployed = chute_deployed_area(diameter)
        chute = chute_mass(deployed, area_density)
        delta, delta_diags = realchute_mass_delta(
            case_mass, [chute], prefab_mass)
        if chute is None or delta is None:
            complete = False
            diagnostics.append({
                'code': 'insufficient_realchute_evidence',
                'message': f'实例 {part.get("part")} 的降落伞质量无法计算'
                           f'（{", ".join(missing + delta_diags)}）',
            })
            continue
        total_chute_mass += chute
        total_mass_delta += delta
        components.append({
            'craft_index': part.get('index'),
            'part': part.get('part'),
            'case_mass_t': case_mass,
            'material': material,
            'material_source': material_source,
            'area_density_t_per_unit': area_density,
            'deployed_diameter_t': diameter,
            'diameter_source': diameter_source,
            'deployed_area_t': deployed,
            'chute_mass_t': chute,
            'prefab_part_mass_t': prefab_mass,
            'prefab_part_mass_source': 'patched_part_mass',
            'mass_delta_t': delta,
            'diagnostics': delta_diags,
        })
    return {
        'components': components,
        'total_chute_mass_t': total_chute_mass,
        'total_mass_delta_t': total_mass_delta,
        'complete': complete,
        'diagnostics': diagnostics,
    }


# --------------------------------------------------------------------------
# Production authorization gate for realchute_settled_mass (6B.3C.4A)
# --------------------------------------------------------------------------

# Settlement statuses ------------------------------------------------------
RC_SETTLED = 'settled'
RC_UNSUPPORTED_DOMAIN = 'unsupported_domain'
RC_DYNAMIC_UNRESOLVED = 'dynamic_unresolved'
RC_NOT_APPLICABLE = 'not_applicable'

# Stable fail-closed diagnostic codes --------------------------------------
RC_FINGERPRINT_MISSING = 'realchute_fingerprint_missing'
RC_FINGERPRINT_MISMATCH = 'realchute_fingerprint_mismatch'
RC_MODULE_STATE_AMBIGUOUS = 'realchute_module_state_ambiguous'
RC_EVIDENCE_INCOMPLETE = 'realchute_evidence_incomplete'
RC_SAVED_MOD_MASS_INVALID = 'realchute_saved_mod_mass_invalid'
RC_SCOPE_UNSUPPORTED = 'realchute_scope_unsupported'

# Structured production authorization record (not comments): scope,
# fingerprints and the authoritative attempt-002 result SHA-256.  The gate
# only passes for this exact scope + fingerprint combination; everything
# else fails closed.
REALCHUTE_PRODUCTION_AUTHORIZATION = {
    'schema': 'realchute_production_authorization',
    'version': 1,
    'authorization_id': 'realchute_settled_mass_v1',
    'attempt_002_result_sha256':
        '2c1433b7183614385615dd65b6d59bf8be716c825c49b9b9ffbee24ab755e178',
    'supported_model': 'realchute_settled_mass',
    'settled_part_mass_formula': 'caseMass + Sum(ChuteMass)',
    'chute_mass_formula': 'DeployedArea * material.AreaDensity (float32)',
    'deployed_area_formula': 'conv.r4(pi * deployedDiameter^2 / 4)',
    'scope': {
        'canonical_part': REALCHUTE_CANDIDATE_PART,
        'material': 'Nylon',
        'calculation_mode': 'Manual',
        'calc_select': False,
        'get_mass': True,
        'use_dry': True,
        'chute_count': 1,
        'chutes_per_part': 1,
        'deployed_diameter_m_min': 20.7,
        'deployed_diameter_m_max': 25.0,
    },
    'fingerprints': {
        'config_cache_sha256': CONFIG_CACHE_EXPECTED_SHA256,
        'realchute_dll_sha256': REALCHUTE_EXPECTED_SHA256,
    },
    'not_authorized': [
        '其他 RealChute 部件',
        '其他材料',
        'Automatic 模式（calcSelect=True）',
        '多 canopy / 多 chute 单部件',
        '授权区间外的展开直径',
        '其他 RealChute.dll 或 ConfigCache 指纹',
    ],
}


def _upper(value):
    return (value or '').upper()


def realchute_config_material(config_record):
    """Material from the patched RealChuteModule PARACHUTE config rows."""
    entries = (config_record or {}).get('module_evidence', {}).get(
        'RealChuteModule') or []
    for entry in entries:
        for parachute in entry.get('parachutes') or []:
            if parachute.get('material'):
                return parachute['material']
    return None


def realchute_part_settlement_evidence(craft_part, config_record,
                                       material_record, provenance=None,
                                       config_cache_sha256=None,
                                       authorization=None) -> dict:
    """PURE single RealChute part settlement evidence (no disk/SQL/KSP).

    All inputs are in-memory evidence objects:

    * ``craft_part``       — parsed PART dict (``module_evidence`` with
      RealChuteModule / ProceduralChute, ``mod_mass_*`` fields);
    * ``config_record``    — ConfigCache primary PART record dict;
    * ``material_record``  — MATERIAL record dict (``area_density``);
    * ``provenance``       — runtime fingerprint provider output
      (``realchute_dll_sha256``);
    * ``config_cache_sha256`` — ConfigCache SHA-256 from the caller;
    * ``authorization``    — the structured authorization record
      (defaults to :data:`REALCHUTE_PRODUCTION_AUTHORIZATION`).

    Returns a deterministic, JSON-serializable record with status:

    * ``settled``             — authorized and evidence complete;
    * ``unsupported_domain``  — scope violation (part/material/mode/chute
      count/diameter outside the authorization);
    * ``dynamic_unresolved``  — fingerprint mismatch/missing, ambiguous
      module state, missing/invalid evidence or invalid saved modMass;
    * ``not_applicable``      — canonical part not in the authorized scope.

    ``settled_part_mass_t`` uses the exact IL float32 accumulation
    (:func:`realchute_settled_part_mass`); the saved part-level
    ``modMass`` is returned as a diagnostic value only and NEVER added.
    """
    authorization = authorization or REALCHUTE_PRODUCTION_AUTHORIZATION
    scope = authorization['scope']
    fingerprints = authorization['fingerprints']
    source = {
        'module':
            'server.ksp_module_mass_semantics.'
            'realchute_part_settlement_evidence',
        'model': 'realchute_settled_mass',
        'authorization_id': authorization['authorization_id'],
        'attempt_002_result_sha256':
            authorization['attempt_002_result_sha256'],
    }
    diagnostics: List[dict] = []
    missing: List[str] = []

    def fail(status, code, message):
        diagnostics.append({'code': code, 'message': message})
        return _settlement_record(
            status, source, authorization, config_cache_sha256,
            provenance, craft_part, missing=missing,
            diagnostics=diagnostics, area_density=None,
            material_record=material_record)

    def scope_fail(message):
        return fail(RC_UNSUPPORTED_DOMAIN, RC_SCOPE_UNSUPPORTED, message)

    def evidence_fail(code, message):
        return fail(RC_DYNAMIC_UNRESOLVED, code, message)

    # ---- canonical part gate ----------------------------------------------
    canonical = (config_record or {}).get('name')
    if canonical != scope['canonical_part']:
        return _settlement_record(
            RC_NOT_APPLICABLE, source, authorization, config_cache_sha256,
            provenance, craft_part, missing=['canonical_part_out_of_scope'],
            diagnostics=[{'code': RC_SCOPE_UNSUPPORTED,
                          'message': f'canonical part {canonical!r} 不在'
                                     f'授权范围（{scope["canonical_part"]!r}）'}],
            area_density=None, material_record=material_record)

    # ---- fingerprint gate (fail closed) ------------------------------------
    cache_sha = _upper(config_cache_sha256)
    if not cache_sha:
        missing.append('config_cache_sha256')
        return evidence_fail(
            RC_FINGERPRINT_MISSING,
            '缺少 ConfigCache SHA-256（调用层未提供 provenance），'
            '不能使用 realchute_settled_mass')
    if cache_sha != fingerprints['config_cache_sha256']:
        missing.append('config_cache_sha256')
        return evidence_fail(
            RC_FINGERPRINT_MISMATCH,
            f'ConfigCache SHA-256 {cache_sha[:16]}… 与授权记录'
            f' {fingerprints["config_cache_sha256"][:16]}… 不匹配')
    dll_sha = _upper((provenance or {}).get('realchute_dll_sha256'))
    if not dll_sha:
        missing.append('realchute_dll_sha256')
        return evidence_fail(
            RC_FINGERPRINT_MISSING,
            '缺少 RealChute.dll SHA-256（调用层未提供 provenance）')
    if dll_sha != fingerprints['realchute_dll_sha256']:
        missing.append('realchute_dll_sha256')
        return evidence_fail(
            RC_FINGERPRINT_MISMATCH,
            f'RealChute.dll SHA-256 {dll_sha[:16]}… 与授权记录'
            f' {fingerprints["realchute_dll_sha256"][:16]}… 不匹配')

    # ---- module state (unique + complete) -----------------------------------
    module_evidence = (craft_part or {}).get('module_evidence') or {}
    rc_entries = module_evidence.get('RealChuteModule') or []
    proc_entries = module_evidence.get('ProceduralChute') or []
    if len(rc_entries) != 1 or len(proc_entries) != 1:
        return evidence_fail(
            RC_MODULE_STATE_AMBIGUOUS,
            f'RealChuteModule/ProceduralChute 状态数量异常'
            f'（RC={len(rc_entries)} PC={len(proc_entries)}），'
            '无法唯一确定模块状态')
    rc = rc_entries[0]
    proc = proc_entries[0]

    # ---- saved modMass (diagnostic only; explicit invalid -> fail) ----------
    saved_mod_mass = _finite((craft_part or {}).get('mod_mass_t'))
    if (craft_part or {}).get('mod_mass_present') and saved_mod_mass is None:
        return evidence_fail(
            RC_SAVED_MOD_MASS_INVALID,
            f'saved modMass 显式存在但非法'
            f'（{(craft_part or {}).get("mod_mass_raw")!r}），'
            '运行时质量修正不可用')
    if saved_mod_mass is not None:
        diagnostics.append({
            'code': 'realchute_saved_mod_mass_diagnostic',
            'message': f'saved modMass={saved_mod_mass!r} t 仅为 KSP 持久化'
                       '的运行时诊断值，不参与结算质量',
        })

    # ---- case mass ------------------------------------------------------------
    case_mass = _finite(rc.get('case_mass'))
    if case_mass is None or case_mass < 0:
        missing.append('case_mass')
        return evidence_fail(
            RC_EVIDENCE_INCOMPLETE,
            f'caseMass 缺失或非法（{rc.get("case_mass_raw")!r}），'
            '不得用常量回退')

    # ---- ProceduralChute rows -------------------------------------------------
    chute_rows = proc.get('chutes') or []
    if not chute_rows:
        missing.append('procedural_chute')
        return evidence_fail(
            RC_EVIDENCE_INCOMPLETE,
            'ProceduralChute 没有 CHUTE 行，无法取得设计状态')
    if len(chute_rows) > scope['chutes_per_part']:
        return scope_fail(
            f'单个部件有 {len(chute_rows)} 个 chute（授权范围内每部件'
            f'最多 {scope["chutes_per_part"]} 个）')
    chute_row = chute_rows[0]

    # ---- mode / flags (scope) ---------------------------------------------------
    if chute_row.get('calc_select') is not False:
        return scope_fail(
            f'calcSelect={chute_row.get("calc_select")!r}（授权范围要求 '
            f'Manual/calcSelect={scope["calc_select"]}）')
    if chute_row.get('get_mass') is not True:
        return scope_fail(
            f'getMass={chute_row.get("get_mass")!r}（授权范围要求 True）')
    if chute_row.get('use_dry') is not True:
        return scope_fail(
            f'useDry={chute_row.get("use_dry")!r}（授权范围要求 True）')
    chute_count = _finite(chute_row.get('chute_count'))
    if chute_count != scope['chute_count']:
        return scope_fail(
            f'chuteCount={chute_count!r}（授权范围要求 '
            f'{scope["chute_count"]}）')

    # ---- diameter (scope range, evidence only) --------------------------------
    diameter = _finite(chute_row.get('dep_diam'))
    if diameter is None or diameter < 0:
        missing.append('deployed_diameter')
        return evidence_fail(
            RC_EVIDENCE_INCOMPLETE,
            f'deployed diameter 缺失或非法（{chute_row.get("dep_diam")!r}）')
    if not (scope['deployed_diameter_m_min'] <= diameter
            <= scope['deployed_diameter_m_max']):
        return scope_fail(
            f'deployed diameter {diameter} m 超出授权区间 '
            f'[{scope["deployed_diameter_m_min"]}, '
            f'{scope["deployed_diameter_m_max"]}] m')

    # ---- material (scope) + area density (evidence) ----------------------------
    material = realchute_config_material(config_record)
    if not material:
        missing.append('material')
        return evidence_fail(
            RC_EVIDENCE_INCOMPLETE,
            '无法从 ConfigCache RealChuteModule 取得 material')
    if material != scope['material']:
        return scope_fail(
            f'material={material!r}（授权范围仅 {scope["material"]!r}）')
    area_density = _finite((material_record or {}).get('area_density'))
    if area_density is None or area_density < 0:
        missing.append('material_area_density')
        return evidence_fail(
            RC_EVIDENCE_INCOMPLETE,
            f'areaDensity 缺失或非法'
            f'（{(material_record or {}).get("area_density_raw")!r}），'
            '不得用常量回退')

    # ---- compute (exact IL float32 order) --------------------------------------
    deployed_area = chute_deployed_area(diameter)
    chute_mass_t = chute_mass(deployed_area, area_density)
    settled, reasons = realchute_settled_part_mass(case_mass,
                                                   [chute_mass_t])
    if settled is None or chute_mass_t is None or deployed_area is None:
        missing.extend(reasons)
        return evidence_fail(
            RC_EVIDENCE_INCOMPLETE,
            f'realchute_settled_mass 计算失败（{", ".join(reasons)}）')

    result = _settlement_record(
        RC_SETTLED, source, authorization, config_cache_sha256,
        provenance, craft_part, missing=[], diagnostics=diagnostics,
        area_density=area_density, material_record=material_record,
        case_mass_t=case_mass, chute_mass_t=chute_mass_t,
        deployed_diameter_m=diameter, material=material,
        settled_part_mass_t=settled)
    result['deployed_area_t'] = deployed_area
    return result


def _settlement_record(status, source, authorization, config_cache_sha256,
                       provenance, craft_part, *, missing, diagnostics,
                       area_density, material_record,
                       case_mass_t=None, chute_mass_t=None,
                       deployed_diameter_m=None, material=None,
                       settled_part_mass_t=None) -> dict:
    """Common record skeleton for every settlement status."""
    return {
        'status': status,
        'model': 'realchute_settled_mass',
        'settled_part_mass_t': settled_part_mass_t,
        'case_mass_t': case_mass_t,
        'chute_mass_t': chute_mass_t,
        'deployed_diameter_m': deployed_diameter_m,
        'material': material,
        'area_density': area_density,
        'area_density_raw': (material_record or {}).get('area_density_raw'),
        'saved_mod_mass_raw': (craft_part or {}).get('mod_mass_raw'),
        'saved_mod_mass_t': _finite((craft_part or {}).get('mod_mass_t')),
        'config_cache_sha256': config_cache_sha256,
        'realchute_dll_sha256': (provenance or {}).get(
            'realchute_dll_sha256'),
        'fingerprints_checked': authorization['fingerprints'],
        'evidence_missing': sorted(set(missing)),
        'diagnostics': diagnostics,
        'source': source,
    }


# --------------------------------------------------------------------------
# ModuleFuelTanks massIsAdditive determination (static evidence)
# --------------------------------------------------------------------------

def parse_base_mass_values(il_evidence, raw_basemass) -> Optional[dict]:
    """Interpret the ParseBaseMass IL with the raw config basemass value.

    Returns ``{'basemass_const': float, 'basemass_pv': float, 'shape': str}``
    or None when the IL shape or the value cannot be proven.  Supported IL
    shapes (verified against RealFuels 15.14.0):

    * plain value   -> basemassConst = TryParse(value), basemassPV = 0
    * ``A/B`` form  -> basemassPV = A/B, basemassConst = 0
    """
    instructions = (il_evidence or {}).get('instructions') or []
    if not instructions or raw_basemass is None:
        return None
    texts = [str(insn.get('text') or insn.get('name'))
             for insn in instructions]
    has_const_write = any(
        'ldflda' in text and 'basemassConst' in text for text in texts)
    has_pv_write = any(
        'ldflda' in text and 'basemassPV' in text for text in texts)
    value = str(raw_basemass).strip()
    if not value:
        return None
    if '/' in value:
        if not has_pv_write:
            return None
        parts = value.split('/')
        try:
            numerator = float(parts[0].strip())
            denominator = float(parts[1].strip())
        except (TypeError, ValueError, IndexError):
            return None
        if denominator == 0:
            return None
        return {'basemass_const': 0.0,
                'basemass_pv': numerator / denominator,
                'shape': 'pv_division'}
    if not has_const_write:
        return None
    try:
        const = float(value)
    except (TypeError, ValueError):
        return None
    return {'basemass_const': const, 'basemass_pv': 0.0,
            'shape': 'plain_value'}


def _instruction_texts(instructions):
    return [str(insn.get('text') or insn.get('name'))
            for insn in instructions or []]


def mft_mli_term_evidence(il_evidence, added_layers) -> dict:
    """Prove the effective MLI term of a ModuleFuelTanks instance.

    The MLI term in ``CalculateMassRF`` is
    ``MLIArealDensity * totalTankArea * totalMLILayers`` and
    ``get_totalMLILayers() == numberOfMLILayers + _numberOfAddedMLILayers``.
    The term is PROVABLY zero when (a) the IL shapes match (verified on the
    annotated instruction streams), (b) the tank-definition base layers and
    (c) the instance's added layers are both zero — then 0 * anything = 0
    regardless of area/density.  Any missing or non-zero input makes the
    term UNPROVEN (None) — ``None`` never silently becomes 0.0.

    ``il_evidence`` keys:

    * ``get_total_mlilayers``     — method evidence with 'instructions'
    * ``get_number_of_added``     — method evidence with 'instructions'
    * ``calculate_mass_rf``       — method evidence with 'instructions'
    * ``tank_definition_mlilayers`` — {'<tank type>': {'value': int,
      'determined': bool, 'source': str}} (from the read-only cfg scan +
      the no-IL-writer proof; see the audit)
    * ``tank_type``               — the instance's tank type string
    """
    missing: List[str] = []
    reasons: List[str] = []

    def shape(evidence, *fragments):
        texts = _instruction_texts((evidence or {}).get('instructions'))
        return all(any(fragment in text for text in texts)
                   for fragment in fragments)

    total_shape = shape(il_evidence.get('get_total_mlilayers'),
                        'numberOfMLILayers', 'get_numberOfAddedMLILayers',
                        'add')
    added_shape = shape(il_evidence.get('get_number_of_added'),
                        '_numberOfAddedMLILayers', 'conv.i4')
    rf_shape = shape(il_evidence.get('calculate_mass_rf'),
                     'MLIArealDensity', 'totalTankArea',
                     'get_totalMLILayers', 'mul', 'add')
    for code, ok in (('get_total_mlilayers_shape', total_shape),
                     ('get_number_of_added_shape', added_shape),
                     ('calculate_mass_rf_shape', rf_shape)):
        if not ok:
            missing.append(code)
            reasons.append(f'IL 形状校验失败：{code}')

    tank_type = (il_evidence or {}).get('tank_type')
    base = None
    base_source = None
    if not tank_type:
        missing.append('tank_type')
        reasons.append('缺少实例油箱类型（tank type）')
    else:
        definitions = (il_evidence or {}).get(
            'tank_definition_mlilayers') or {}
        entry = definitions.get(tank_type)
        if not entry:
            missing.append('tank_definition_mlilayers')
            reasons.append(
                f'没有 {tank_type} TANK_DEFINITION 的基础 MLI 层数证据')
        elif not entry.get('determined'):
            missing.append('tank_definition_mlilayers')
            reasons.append(
                f'{tank_type} 的基础 MLI 层数无法确定：'
                f'{entry.get("reason") or "?"}')
        else:
            base = _finite(entry.get('value'))
            base_source = entry.get('source')
            if base is None:
                missing.append('tank_definition_mlilayers')
                reasons.append(
                    f'{tank_type} 的基础 MLI 层数非法：{entry.get("value")!r}')

    added = _finite(added_layers)
    if added is None:
        missing.append('added_mlilayers')
        reasons.append('craft/配置中没有 _numberOfAddedMLILayers 的有效值')

    if missing:
        return {'mli_term': None, 'proven': False,
                'reasons': reasons, 'missing': missing}

    layers = base + added
    if layers != 0:
        return {'mli_term': None, 'proven': False,
                'reasons': [f'有效 MLI 层数非零（base={base} '
                            f'added={added}），无法证明 MLI 项为零'],
                'missing': ['mli_layers_nonzero']}
    return {
        'mli_term': 0.0,
        'proven': True,
        'reasons': [
            'CalculateMassRF 的 MLI 项 = MLIArealDensity × totalTankArea × '
            'totalMLILayers，而 get_totalMLILayers = numberOfMLILayers + '
            f'_numberOfAddedMLILayers；实例证据 base={base} '
            f'（{base_source}）added={added}（craft 状态）使有效层数为 0，'
            'MLI 项恒为 0（0 × 任意面积/密度 = 0）',
        ],
        'missing': [],
    }


def mft_mass_is_additive_determination(il_evidence, basemass_const,
                                       basemass_pv, volume,
                                       mli_evidence=None) -> dict:
    """Determine the runtime value of ``massIsAdditive`` from STATIC
    evidence only (assembly IL + config/instance numbers).

    Resolution order:

    1. the writers scan is INCOMPLETE (some method body could not be read
       or decoded) -> ``insufficient`` — a zero-writer list is only
       meaningful when every body was examined;
    2. no IL writer at all           -> CLR zero-init ``False``
    3. a writer with a constant bool in ``.ctor`` (field initializer)
       -> that constant
    4. the CalculateMass zero-base-mass branch: when the IL shows
       ``mass = 0.0; massIsAdditive = true`` inside the branch taken for
       ``basemassConst + basemassPV * volume + MLI < 0`` and the evidence
       numbers satisfy that inequality -> ``True`` (mass = 0)
    5. otherwise                     -> ``insufficient`` with reasons

    ``mli_evidence`` is the result of ``mft_mli_term_evidence``; the MLI
    term is only usable when it is PROVEN (never ``None -> 0.0``).

    The distinction the task requires is explicit in the output: a CLR
    zero-initialized ``false`` (no writer ever ran) is a different state
    from the post-``CalculateMass`` effective value ``true``.
    """
    missing: List[str] = []
    reasons: List[str] = []
    il_evidence = il_evidence or {}
    writers = il_evidence.get('writers') \
        or il_evidence.get('mass_is_additive_writers') or {}
    writer_rows = writers.get('writers') or []
    if not writers.get('scan_complete', False):
        return {
            'value': None,
            'source': 'insufficient',
            'determined': False,
            'reasons': [
                '写入者扫描不完整（无法读取或解码的方法体：'
                f'unparsed={writers.get("method_bodies_unparsed", "?")} '
                f'decode_incomplete='
                f'{writers.get("method_bodies_decode_incomplete", "?")}），'
                '“零写入者”或“唯一写入者”均无法证明',
            ],
            'missing': ['writers_scan_incomplete'],
        }
    if not writer_rows:
        return {
            'value': False,
            'source': 'clr_zero_init_no_writers',
            'determined': True,
            'reasons': ['massIsAdditive 没有任何 stfld/stsfld 写入方法；'
                        '实例字段由 CLR 零初始化为 false（在 CalculateMass '
                        '运行之前）'],
            'missing': [],
        }

    # 1) constant write in .ctor (field initializer)
    for writer in writer_rows:
        if writer.get('method') != '.ctor':
            continue
        for write in writer.get('writes') or []:
            if write.get('constant_bool') is not None:
                return {
                    'value': write['constant_bool'],
                    'source': 'constructor_constant_write',
                    'determined': True,
                    'reasons': [f'.ctor 在 IL 偏移 0x'
                                f'{write["constant_source_instruction"]:X} '
                                f'以常量写入 massIsAdditive'],
                    'missing': [],
                }

    # 2) CalculateMass zero-base-mass branch (conditional write)
    calculate = (il_evidence or {}).get('calculate_mass') or {}
    instructions = calculate.get('instructions') or []
    if not calculate.get('decode_complete', False):
        missing.append('calculate_mass_decode')
        reasons.append('CalculateMass 的 IL 解码不完整')
    zero_branch = _find_calculate_mass_zero_branch(
        instructions, calculate.get('mass_is_additive_field_token'),
        calculate.get('mass_field_token'))
    if zero_branch is None:
        missing.append('calculate_mass_zero_branch_shape')
        reasons.append('CalculateMass 中未找到 mass=0 且 massIsAdditive='
                       'true 的零基数分支模式')
    mli = mli_evidence or {}
    if not mli.get('proven', False):
        missing.extend(mli.get('missing') or ['mli_term'])
        reasons.extend(mli.get('reasons') or ['MLI 项未证明'])
    c = _finite(basemass_const)
    pv = _finite(basemass_pv)
    v = _finite(volume)
    if c is None:
        missing.append('basemass_const')
    if pv is None:
        missing.append('basemass_pv')
    if v is None:
        missing.append('volume')
    if missing:
        return {
            'value': None,
            'source': 'insufficient',
            'determined': False,
            'reasons': reasons or ['CalculateMass 的条件写入存在，'
                                   '但分支条件证据不足'],
            'missing': missing,
        }
    mli_term = mli.get('mli_term')
    accumulator = c + pv * v + (mli_term or 0.0)
    if accumulator < 0.0:
        return {
            'value': True,
            'source': 'calculate_mass_zero_base_branch',
            'determined': True,
            'reasons': [
                'CalculateMass 在 basemassConst + basemassPV*volume'
                f' + MLI < 0 时执行 mass=0 且 massIsAdditive='
                f'{zero_branch["constant"]}（写入 IL 偏移 '
                f'0x{zero_branch["write_offset"]:X}）；实例证据 '
                f'basemassConst={basemass_const} '
                f'basemassPV={basemass_pv} volume={volume} '
                f'MLI={mli_term}（已证明）使累加值为 {accumulator} < 0',
            ] + mli.get('reasons', []),
            'missing': [],
        }
    return {
        'value': None,
        'source': 'insufficient',
        'determined': False,
        'reasons': ['CalculateMass 的条件写入存在，但分支条件不满足：'
                    f'累加值为 {accumulator} >= 0'],
        'missing': ['basemass_negative_branch_not_taken'],
    }


def _find_calculate_mass_zero_branch(instructions, additive_token,
                                     mass_token):
    """Locate the CalculateMass pattern:

        ... accumulator = basemassConst + basemassPV*volume [+ MLI]
        stloc.0
        ldloc.0
        ldc.r8 0.0
        blt.un.s -> Z          (branch when accumulator < 0)
        ... normal path ...
        Z: ldarg.0
           ldc.r4 0.0
           stfld mass
           ldarg.0
           ldc.i4.1
           stfld massIsAdditive

    Returns ``{'write_offset', 'constant'}`` or None.
    """
    if not instructions:
        return None
    write_indices = [
        i for i, insn in enumerate(instructions)
        if insn['name'] in ('stfld', 'stsfld')
        and insn.get('operand') is not None
        and insn['operand'][1] == additive_token]
    if not write_indices:
        return None
    for write_index in write_indices:
        write = instructions[write_index]
        # Walk backwards from the write (skipping nop / ldarg) looking for
        # `ldc.i4.const` directly before it, then `stfld mass` preceded by
        # `ldc.r4 0.0` — the zero-base-mass block shape:
        #     ldc.r4 0.0; stfld mass; ldarg.0; ldc.i4.1; stfld massIsAdditive
        j = write_index - 1
        while j >= 0 and instructions[j]['name'] in ('nop', 'ldarg.0',
                                                     'ldarg.1'):
            j -= 1
        constant = None
        if j >= 0 and instructions[j]['name'] in ('ldc.i4.1', 'ldc.i4.0',
                                                  'ldc.i4.s', 'ldc.i4'):
            constant = instructions[j]['operand'][1] != 0 \
                if instructions[j]['operand'] is not None else None
            if instructions[j]['name'] == 'ldc.i4.0':
                constant = False
            elif instructions[j]['name'] == 'ldc.i4.1':
                constant = True
            j -= 1
            while j >= 0 and instructions[j]['name'] in ('nop', 'ldarg.0',
                                                         'ldarg.1'):
                j -= 1
        if j < 0 or instructions[j]['name'] != 'stfld' or \
                instructions[j].get('operand') is None or \
                instructions[j]['operand'][1] != mass_token:
            continue
        k = j - 1
        while k >= 0 and instructions[k]['name'] in ('nop', 'ldarg.0',
                                                     'ldarg.1'):
            k -= 1
        if k < 0 or instructions[k]['name'] != 'ldc.r4' or \
                instructions[k].get('operand') is None or \
                instructions[k]['operand'][1] != 0.0:
            continue
        if constant is None:
            continue
        # a blt.un branch whose target is the start of this zero-mass block
        target_ok = False
        for insn in instructions:
            if insn['name'] in ('blt.un.s', 'blt.un') and \
                    insn.get('target') is not None and \
                    insn['target'] <= instructions[j]['offset'] and \
                    insn['target'] >= instructions[k]['offset'] - 12:
                target_ok = True
                break
        if not target_ok:
            continue
        return {
            'write_offset': write['offset'],
            'constant': constant,
        }
    return None


def mft_component_evidence(craft_draft, ksp_index,
                           il_evidence=None) -> dict:
    """ModuleFuelTanks GetModuleMass semantics for the special vehicles.

    ``il_evidence`` (optional) carries the static IL trace for
    ``massIsAdditive``; without it the field value stays
    ``insufficient_evidence`` unless the config/craft state carries it.
    """
    parts = list((craft_draft or {}).get('parts') or [])
    diagnostics: List[dict] = []
    components = []
    for part in parts:
        if part.get('base_part') != MFT_CANDIDATE_PART:
            continue
        entry, reasons = _module_state(part, 'ModuleFuelTanks')
        if entry is None:
            diagnostics.append({
                'code': reasons[0],
                'message': f'实例 {part.get("part")} 的 ModuleFuelTanks '
                           f'状态不完整',
            })
            continue
        saved_mass = _finite(entry.get('mass'))
        volume = _finite(entry.get('volume'))
        saved_additive = entry.get('mass_is_additive')
        added_layers = _finite(entry.get('number_of_added_mlilayers'))
        config = _config_module_entry(
            ksp_index, MFT_CONFIG_PART, 'ModuleFuelTanks')
        config_basemass = _finite((config or {}).get('basemass'))
        config_additive = (config or {}).get('mass_is_additive')
        config_added_layers = _finite(
            (config or {}).get('_numberOfAddedMLILayers'))
        prefab_mass = _config_part_mass(ksp_index, MFT_CONFIG_PART)

        # --- massIsAdditive determination (config/craft first) -----------
        missing: List[str] = []
        # effective MLI layers: craft state wins over config; the term must
        # be PROVEN when it is needed (never None -> 0.0)
        effective_added = None
        if added_layers is not None:
            effective_added = added_layers
        elif config_added_layers is not None:
            effective_added = config_added_layers
        if saved_additive is not None:
            additive = saved_additive
            additive_source = 'craft_module_state'
        elif config_additive is not None:
            additive = config_additive
            additive_source = 'patched_config'
        else:
            const_pv = parse_base_mass_values(
                (il_evidence or {}).get('parse_base_mass'),
                (config or {}).get('basemass_raw'))
            if const_pv is None:
                additive = None
                additive_source = 'insufficient'
                missing.append('basemass_parse')
                diagnostics.append({
                    'code': 'insufficient_mass_is_additive_evidence',
                    'message': f'实例 {part.get("part")} 的 basemass 无法'
                               f'按 ParseBaseMass IL 解析，massIsAdditive '
                               f'无法静态确定',
                })
            else:
                tank_type = (config or {}).get('type')
                instance_il = dict(il_evidence or {})
                if tank_type:
                    instance_il['tank_type'] = tank_type
                mli_evidence = mft_mli_term_evidence(
                    instance_il, effective_added)
                determination = mft_mass_is_additive_determination(
                    instance_il, const_pv['basemass_const'],
                    const_pv['basemass_pv'], volume,
                    mli_evidence=mli_evidence)
                if determination.get('determined'):
                    additive = determination['value']
                    additive_source = determination['source']
                else:
                    additive = None
                    additive_source = 'insufficient'
                    missing.extend(determination.get('missing')
                                   or ['mass_is_additive_static_value'])
                    diagnostics.append({
                        'code': 'insufficient_mass_is_additive_evidence',
                        'message': (f'实例 {part.get("part")} 的 '
                                    f'massIsAdditive 无法静态确定：'
                                    f'{"; ".join(determination.get("reasons") or [])}'),
                    })

        modifier = None
        if saved_mass is not None and additive is not None:
            # GetModuleMass IL: massIsAdditive ? mass : mass - defaultMass
            if additive:
                modifier = saved_mass
            elif prefab_mass is not None:
                modifier = saved_mass - prefab_mass
            else:
                missing.append('prefab_part_mass')
        else:
            if saved_mass is None:
                missing.append('saved_mass')
            if additive is None:
                missing.append('mass_is_additive')
        components.append({
            'craft_index': part.get('index'),
            'part': part.get('part'),
            'saved_mass_t': saved_mass,
            'volume_t': volume,
            'added_mlilayers_t': effective_added,
            'config_basemass_t': config_basemass,
            'config_mass_is_additive': config_additive,
            'mass_is_additive': additive,
            'mass_is_additive_source': additive_source,
            'prefab_part_mass_t': prefab_mass,
            'prefab_part_mass_source': 'patched_part_mass',
            'get_module_mass_t': modifier,
            'evidence_complete': not missing,
            'evidence_missing': missing,
            'diagnostics': [],
        })
    return {
        'components': components,
        'determined': bool(components) and all(
            c['evidence_complete'] for c in components),
        'diagnostics': diagnostics,
    }


# --------------------------------------------------------------------------
# Hypothesis evaluation (four hypotheses, symbol convention as documented)
# --------------------------------------------------------------------------

def _hypothesis(applicable, baseline, delta, missing):
    predicted_residual = None
    if baseline is not None and delta is not None:
        predicted_residual = baseline + delta
    if not applicable:
        verdict = NOT_APPLICABLE
    elif baseline is None or delta is None or missing:
        verdict = INSUFFICIENT_EVIDENCE
    elif predicted_residual is not None and \
            abs(predicted_residual) <= HYPOTHESIS_ABS_TOLERANCE_T:
        verdict = SUPPORTED
    else:
        verdict = CONTRADICTED
    return {
        'applicable': applicable,
        'predicted_mass_delta_t': delta,
        'baseline_residual_t': baseline,
        'predicted_residual_t': predicted_residual,
        'evidence_complete': applicable and baseline is not None
        and delta is not None and not missing,
        'evidence_missing': sorted(set(missing)),
        'verdict': verdict,
        'description': '',
    }


def candidate_part_counts(craft_draft) -> dict:
    """Candidate counts from the RAW craft draft (module presence), never
    from parsed evidence components: a candidate whose evidence failed to
    resolve must still make the hypotheses applicable (with incomplete
    evidence), not ``not_applicable``."""
    parts = list((craft_draft or {}).get('parts') or [])
    rc = sum(1 for part in parts
             if part.get('base_part') == REALCHUTE_CANDIDATE_PART
             and 'RealChuteModule' in
             (part.get('module_evidence') or {}))
    mft = sum(1 for part in parts
              if part.get('base_part') == MFT_CANDIDATE_PART
              and 'ModuleFuelTanks' in
              (part.get('module_evidence') or {}))
    return {
        'realchute_parachute_single': rc,
        'mk1pod_v2_module_fuel_tanks': mft,
    }


def evaluate_hypotheses(vehicle_forensics, realchute_evidence,
                        mft_evidence, float32_total, source_total_mass_t,
                        pdc_diff_total, craft_draft=None) -> dict:
    """Four hypotheses over the float32_craft_order total.

    ``residual = estimated_total_mass - loadmeta_totalMass`` and the module
    hypotheses predict a PART-mass modifier ``predicted_mass_delta_t`` that
    shifts the baseline residual:

        baseline_residual_t = float32_total - pdc_diff_total
                              - source_total_mass_t
        predicted_residual_t = baseline_residual_t + predicted_mass_delta_t

    A MISSING ``pdc_diff_total`` keeps ``baseline_residual_t`` missing —
    it is never replaced by ``or 0.0``.

    Applicability comes from the RAW craft draft (``candidate_part_counts``):
    a craft that CONTAINS a candidate part/module is ``applicable`` even
    when its evidence failed to resolve (verdict ``insufficient_evidence``);
    only a craft with NO candidate parts gets ``not_applicable``.  When
    several candidates exist their modifiers are summed, and the evidence
    is complete only when EVERY applicable component resolved.
    """
    candidates = candidate_part_counts(craft_draft)
    rc_candidate_count = candidates['realchute_parachute_single']
    mft_candidate_count = candidates['mk1pod_v2_module_fuel_tanks']
    has_rc_candidates = rc_candidate_count > 0
    has_mft_candidates = mft_candidate_count > 0

    rc_components = (realchute_evidence or {}).get('components') or []
    mft_components = (mft_evidence or {}).get('components') or []

    pdc = _finite(pdc_diff_total)
    baseline = None
    baseline_missing = []
    if pdc is not None and float32_total is not None and \
            source_total_mass_t is not None:
        estimated = _finite(float32_total)
        loadmeta = _finite(source_total_mass_t)
        if estimated is not None and loadmeta is not None:
            baseline = (estimated - pdc) - loadmeta
        else:
            baseline_missing.append('estimated_or_loadmeta_mass')
    else:
        baseline_missing.append('pdc_diff_total' if pdc is None
                                else 'estimated_or_loadmeta_mass')

    # H2: RealChute modifier — summed over ALL components; complete only
    # when every candidate produced a delta
    rc_delta = None
    rc_missing = list(baseline_missing)
    if has_rc_candidates:
        if len(rc_components) != rc_candidate_count:
            rc_missing.append('realchute_evidence_incomplete')
        if not (realchute_evidence or {}).get('complete'):
            rc_missing.append('realchute_evidence_incomplete')
        deltas = [c.get('mass_delta_t') for c in rc_components]
        if any(delta is None for delta in deltas) or not deltas:
            rc_missing.append('realchute_mass_delta')
        elif all(delta is not None for delta in deltas):
            rc_delta = sum(deltas)

    # H3: MFT modifier — summed over ALL components; any incomplete or
    # missing component keeps the whole hypothesis incomplete
    mft_delta = None
    mft_missing = list(baseline_missing)
    if has_mft_candidates:
        if len(mft_components) != mft_candidate_count:
            mft_missing.append('mft_evidence_incomplete')
        incomplete = [c for c in mft_components
                      if not c.get('evidence_complete')]
        if incomplete:
            for c in incomplete:
                mft_missing.extend(c.get('evidence_missing') or [])
            mft_missing.append('mft_evidence_incomplete')
        modifiers = [c.get('get_module_mass_t') for c in mft_components]
        if any(modifier is None for modifier in modifiers) or \
                not modifiers:
            mft_missing.append('mft_get_module_mass')
        else:
            mft_delta = sum(modifiers)

    # H1: base-only hypothesis — completeness still depends on the
    # candidate evidence when candidates exist
    h1_missing = list(baseline_missing)
    if has_rc_candidates:
        if len(rc_components) != rc_candidate_count:
            h1_missing.append('realchute_evidence_incomplete')
        if not (realchute_evidence or {}).get('complete'):
            h1_missing.append('realchute_evidence_incomplete')

    descriptions = {
        'parachute_base_mass_only':
            '无模块修正；基线即整箭（PDC 校正后）残差',
        'realchute_prefab_modifier':
            'RealChuteModule.massDelta = caseMass + ΣChuteMass − '
            'partPrefab.mass 作为部件质量修正',
        'mk1pod_module_fuel_tanks_modifier':
            'ModuleFuelTanks.GetModuleMass 返回值作为部件质量修正',
        'combined_realchute_and_mft_semantics':
            'RealChute massDelta 与 MFT modifier 之和',
    }

    hypotheses = {
        'parachute_base_mass_only': _hypothesis(
            has_rc_candidates, baseline, 0.0, h1_missing),
        'realchute_prefab_modifier': _hypothesis(
            has_rc_candidates, baseline, rc_delta, rc_missing),
        'mk1pod_module_fuel_tanks_modifier': _hypothesis(
            has_mft_candidates, baseline, mft_delta, mft_missing),
    }
    combined_delta = None
    combined_missing = list(baseline_missing)
    if has_rc_candidates and has_mft_candidates:
        if rc_delta is not None and mft_delta is not None:
            combined_delta = rc_delta + mft_delta
        if rc_delta is None:
            combined_missing.append('realchute_mass_delta')
        if mft_delta is None:
            combined_missing.append('mft_get_module_mass')
    hypotheses['combined_realchute_and_mft_semantics'] = _hypothesis(
        has_rc_candidates and has_mft_candidates,
        baseline, combined_delta, combined_missing)

    for name, hypothesis in hypotheses.items():
        hypothesis['description'] = descriptions[name]

    return {
        'schema_version': SCHEMA_VERSION,
        'source_kind': SOURCE_KIND,
        'estimated_total_mass_t': _finite(float32_total),
        'loadmeta_total_mass_t': _finite(source_total_mass_t),
        'pdc_diff_total_t': pdc,
        'baseline_residual_t': baseline,
        'baseline_evidence_missing': sorted(set(baseline_missing)),
        'candidate_parts': candidates,
        'tolerance_t': HYPOTHESIS_ABS_TOLERANCE_T,
        'hypotheses': hypotheses,
    }
