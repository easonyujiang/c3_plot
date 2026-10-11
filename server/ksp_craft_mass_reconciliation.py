"""
Craft mass reconciliation vs ``.loadmeta.totalMass`` (phase 6B.3B / 6B.3B-R).
============================================================================

Whole-craft audit layer on top of the per-PART mass evidence produced by
:mod:`server.ksp_mass_evidence`.  It reports residuals and evidence gaps —
it NEVER auto-corrects masses, never distributes a residual across parts
or stages, never fills ``stage.wet_mass_t`` / ``stage.dry_mass_t``.

Rework (6B.3B-R) contract: ``complete_evidence`` means "every PART has a
UNIQUE match and its mass fully resolved", not merely "a number happens to
be present":

* strict per-instance usability: match_status in {exact, normalized_unique}
  AND resolution_status in {resolved_static, estimated_with_modifier} AND a
  finite non-negative JSON number (never bool) AND a unique part_source;
* instance-count conservation: expected_instance_count (from
  ``craft_draft.source.part_count``, else ``len(craft_draft.parts)``) must
  equal the evidence count, craft_index must be unique and one-to-one;
  empty evidence is never complete;
* per-instance stable ``invalid_reasons`` (never just a single summary);
* booleans are rejected everywhere a mass or totalMass is read;
* possible_non_flight counting is separated from known-mass summing;
* target status and evidence status are independent dimensions.

Residual conventions: ``residual = estimated_total - source_total_mass_t``
(positive: estimates exceed the loadmeta target).  Only strict
floating/serialization tolerances are used (1e-6 t absolute, 1e-7
relative); never tuned to make real crafts pass.

The module is pure: no disk access, no SQL/YAML/cache writes, no Flask,
no mutation of inputs, JSON-serializable and deterministic.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Dict, List, Optional

SCHEMA_VERSION = 1
SOURCE_KIND = 'ksp_craft_mass_reconciliation'

# Strict tolerances for floating-point / serialization error only.
ABSOLUTE_TOLERANCE_T = 1e-6
RELATIVE_TOLERANCE = 1e-7

# statuses ----------------------------------------------------------------
TARGET_MISSING = 'target_missing'
TARGET_INVALID = 'target_invalid'
EVIDENCE_INCOMPLETE = 'evidence_incomplete'
MATCHED_INCLUDE_ALL = 'matched_include_all'
MATCHED_EXCLUDING_NON_FLIGHT = 'matched_excluding_non_flight'
AMBIGUOUS_SCENARIO = 'ambiguous_scenario'
UNRECONCILED = 'unreconciled'

# whitelists (stable statuses, not diagnostic text) ------------------------
_MATCH_WHITELIST = ('exact', 'normalized_unique')
_RESOLUTION_WHITELIST = ('resolved_static', 'estimated_with_modifier')

# stable per-instance invalid reasons ---------------------------------------
INVALID_MATCH_STATUS = 'invalid_match_status'
UNRESOLVED_MASS_STATUS = 'unresolved_mass_status'
INVALID_MASS_VALUE = 'invalid_mass_value'
MISSING_PART_SOURCE = 'missing_part_source'
DUPLICATE_CRAFT_INDEX = 'duplicate_craft_index'
MISSING_CRAFT_INSTANCE = 'missing_craft_instance'
UNEXPECTED_EVIDENCE_INSTANCE = 'unexpected_evidence_instance'


def _finite(value):
    """Finite float or None.  Booleans, NaN, +/-infinity and non-numeric
    values are always rejected; numeric strings are accepted (ConfigNode /
    loadmeta values arrive as strings)."""
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


def _is_json_number(value):
    """JSON number (int/float, never bool) — required for evidence masses."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_target(loadmeta) -> dict:
    """Strict parse of ``.loadmeta.totalMass`` (numeric strings allowed,
    booleans rejected)."""
    raw = None
    if isinstance(loadmeta, dict):
        raw = loadmeta.get('totalMass')
    if raw is None:
        return {'status': TARGET_MISSING, 'raw': None, 't': None}
    value = _finite(raw)
    if value is None or value < 0:
        return {'status': TARGET_INVALID, 'raw': str(raw), 't': None}
    return {'status': 'ok', 'raw': str(raw), 't': value}


def _instance_invalid_reasons(instance) -> List[str]:
    """Stable reasons why an instance may not participate in reconciliation."""
    reasons: List[str] = []
    if instance.get('match_status') not in _MATCH_WHITELIST:
        reasons.append(INVALID_MATCH_STATUS)
    if instance.get('resolution_status') not in _RESOLUTION_WHITELIST:
        reasons.append(UNRESOLVED_MASS_STATUS)
    value = instance.get('current_part_mass_estimate_t')
    if not _is_json_number(value) or not math.isfinite(float(value)) \
            or value < 0:
        reasons.append(INVALID_MASS_VALUE)
    part_source = instance.get('part_source')
    if not isinstance(part_source, dict) \
            or not part_source.get('parent_url') \
            or part_source.get('cache_order') is None:
        reasons.append(MISSING_PART_SOURCE)
    return reasons


def _instance_evidence(instance, invalid_reasons=None, synthetic=False) -> dict:
    return {
        'craft_index': instance.get('craft_index'),
        'base_part': instance.get('base_part'),
        'persistent_id': instance.get('persistent_id'),
        'match_status': instance.get('match_status'),
        'resolution_status': instance.get('resolution_status'),
        'current_part_mass_estimate_t':
            instance.get('current_part_mass_estimate_t'),
        'contains_launch_clamp': bool(instance.get('contains_launch_clamp')),
        'possible_non_flight_part':
            bool(instance.get('possible_non_flight_part')),
        'part_source': instance.get('part_source'),
        'diagnostics': instance.get('diagnostics'),
        'invalid_reasons': invalid_reasons or [],
        'synthetic': synthetic,
    }


def _synthetic_missing_record(part: dict) -> dict:
    """Auditable placeholder for a craft PART with no evidence at all."""
    return {
        'craft_index': part.get('index'),
        'part': part.get('part'),
        'base_part': part.get('base_part'),
        'persistent_id': part.get('persistent_id'),
        'match_status': None,
        'resolution_status': None,
        'current_part_mass_estimate_t': None,
        'contains_launch_clamp': False,
        'possible_non_flight_part': False,
        'part_source': None,
        'diagnostics': [],
        'invalid_reasons': [MISSING_CRAFT_INSTANCE],
        'synthetic': True,
    }


def _expected_instance_count(craft_draft, diagnostics: List[dict]) -> int:
    """expected count from source.part_count, validated against parts."""
    source = (craft_draft or {}).get('source') or {}
    source_count = source.get('part_count')
    parts_count = len((craft_draft or {}).get('parts') or [])
    if source_count is not None and source_count != parts_count:
        diagnostics.append({
            'code': 'input_structure_mismatch',
            'message': (f'source.part_count（{source_count}）与 '
                        f'len(craft.parts)（{parts_count}）不一致'),
        })
    if isinstance(source_count, int) and not isinstance(source_count, bool) \
            and source_count > 0:
        return source_count
    return parts_count


def reconcile_craft_mass(craft_draft, mass_evidence, loadmeta=None,
                         loadmeta_filename=None) -> dict:
    """Reconcile per-part mass evidence against the loadmeta target.

    Returns a JSON-serializable, deterministic report.  ``craft_draft``
    and ``mass_evidence`` are never mutated.
    """
    instances = list((mass_evidence or {}).get('instances') or [])
    diagnostics: List[dict] = []

    # ---- instance-count conservation --------------------------------------
    expected_count = _expected_instance_count(craft_draft, diagnostics)
    craft_parts = (craft_draft or {}).get('parts') or []
    expected_indexes = [part.get('index') for part in craft_parts]
    expected_index_set = set(expected_indexes)
    indexes_unique = len(expected_index_set) == len(expected_indexes)
    if not indexes_unique:
        diagnostics.append({
            'code': 'duplicate_craft_index_in_craft',
            'message': 'craft parts 中存在重复 craft_index',
        })

    # Explicit one-to-one mapping by craft_index.
    craft_part_by_index = {}
    for part in craft_parts:
        craft_part_by_index.setdefault(part.get('index'), part)
    evidence_by_index: Dict[object, List[dict]] = defaultdict(list)
    for instance in instances:
        evidence_by_index[instance.get('craft_index')].append(instance)

    # ---- classification per craft PART (in craft order) --------------------
    unresolved: List[dict] = []
    valid_pairs = []            # (craft_part, evidence) fully valid
    missing_craft_count = 0
    invalid_evidence_count = 0
    duplicate_evidence_count = 0
    for part in craft_parts:
        index = part.get('index')
        records = evidence_by_index.get(index, [])
        if not records:
            missing_craft_count += 1
            unresolved.append(_synthetic_missing_record(part))
            continue
        if len(records) > 1:
            # no unique usable evidence for this craft PART
            duplicate_evidence_count += len(records)
            for record in records:
                unresolved.append(_instance_evidence(
                    record, [DUPLICATE_CRAFT_INDEX], synthetic=False))
            continue
        record = records[0]
        reasons = _instance_invalid_reasons(record)
        if reasons:
            invalid_evidence_count += 1
            unresolved.append(_instance_evidence(record, reasons,
                                                 synthetic=False))
        else:
            valid_pairs.append((part, record))
    # unexpected evidence (not in the craft) appended last, in evidence order
    unexpected_count = 0
    for instance in instances:
        if instance.get('craft_index') not in expected_index_set:
            unexpected_count += 1
            unresolved.append(_instance_evidence(
                instance, [UNEXPECTED_EVIDENCE_INSTANCE], synthetic=False))

    valid_count = len(valid_pairs)
    evidence_count = len(instances)
    # craft PARTs with no unique valid evidence (missing + invalid + dup)
    missing_mass_count = (missing_craft_count
                          + invalid_evidence_count
                          + len({part.get('index') for part in craft_parts
                                 if len(evidence_by_index.get(
                                     part.get('index'), [])) > 1}))
    unresolved_count = len(unresolved)

    counts_ok = (expected_count > 0
                 and evidence_count == expected_count
                 and indexes_unique
                 and unexpected_count == 0
                 and missing_craft_count == 0
                 and invalid_evidence_count == 0
                 and duplicate_evidence_count == 0
                 and not any(d['code'] == 'input_structure_mismatch'
                             for d in diagnostics))
    complete = counts_ok and valid_count == evidence_count

    # ---- candidate totals (valid unique evidence only) ---------------------
    include_all_t = 0.0
    excluding_t = 0.0
    non_flight_known_mass = 0.0
    non_flight_all: List[dict] = []
    for part, record in valid_pairs:
        value = record['current_part_mass_estimate_t']
        include_all_t += value
        if record.get('possible_non_flight_part'):
            non_flight_known_mass += value
            non_flight_all.append(_instance_evidence(
                record, [], synthetic=False))
        else:
            excluding_t += value
    for record in evidence_by_index.values():
        for instance in record:
            if not instance.get('possible_non_flight_part'):
                continue
            reasons = _instance_invalid_reasons(instance)
            if not reasons:
                continue
            non_flight_all.append(_instance_evidence(
                instance, reasons, synthetic=False))
    non_flight_complete = (all(
        not _instance_invalid_reasons(e)
        for e in non_flight_all) and complete)

    # ---- forensic aggregates ----------------------------------------------
    resolution_dist = Counter()
    dynamic_dist = Counter()
    part_type_count = Counter()
    resource_mass_total = 0.0
    static_mass_total = 0.0
    largest = None
    for instance in instances:
        resolution_dist[instance.get('resolution_status')] += 1
        base = instance.get('base_part')
        if base:
            part_type_count[base] += 1
        for module in instance.get('dynamic_mass_modules') or []:
            dynamic_dist[module] += 1
        resource_value = instance.get('current_resource_mass_t')
        if _is_json_number(resource_value) \
                and math.isfinite(float(resource_value)) \
                and resource_value >= 0:
            resource_mass_total += resource_value
        static_value = instance.get('static_dry_mass_estimate_t')
        if _is_json_number(static_value) \
                and math.isfinite(float(static_value)) \
                and static_value >= 0:
            static_mass_total += static_value
        part_value = instance.get('current_part_mass_estimate_t')
        if _is_json_number(part_value) \
                and math.isfinite(float(part_value)) \
                and part_value >= 0:
            if largest is None or part_value > largest['mass_t']:
                largest = {
                    'craft_index': instance.get('craft_index'),
                    'part': instance.get('part'),
                    'base_part': base,
                    'mass_t': part_value,
                }

    # ---- target ------------------------------------------------------------
    target = _parse_target(loadmeta)
    target_ok = target['status'] == 'ok'

    # ---- residuals (only valid target + complete evidence) -----------------
    residual_include_all = None
    residual_excluding = None
    relative_include_all = None
    relative_excluding = None
    tolerance_t = None
    if target_ok and complete:
        residual_include_all = include_all_t - target['t']
        residual_excluding = excluding_t - target['t']
        tolerance_t = max(ABSOLUTE_TOLERANCE_T,
                          abs(target['t']) * RELATIVE_TOLERANCE)
        if target['t'] != 0:
            relative_include_all = residual_include_all / target['t']
            relative_excluding = residual_excluding / target['t']

    # ---- status (target dimension and evidence dimension are separate) -----
    if not target_ok:
        status = target['status']
    elif not complete:
        status = EVIDENCE_INCOMPLETE
    else:
        include_matched = abs(residual_include_all) <= tolerance_t
        exclude_matched = abs(residual_excluding) <= tolerance_t
        if include_matched and exclude_matched:
            status = AMBIGUOUS_SCENARIO
        elif include_matched:
            status = MATCHED_INCLUDE_ALL
        elif exclude_matched:
            status = MATCHED_EXCLUDING_NON_FLIGHT
        else:
            status = UNRECONCILED

    # ---- diagnostics --------------------------------------------------------
    if status == TARGET_MISSING:
        diagnostics.append({
            'code': 'target_missing',
            'message': '.loadmeta.totalMass 缺失，无法对账',
        })
    elif status == TARGET_INVALID:
        diagnostics.append({
            'code': 'target_invalid',
            'message': f'.loadmeta.totalMass 非法（{target["raw"]!r}），'
                       '不得当作 0',
        })
    if not complete:
        reasons_summary = Counter(
            reason for record in unresolved
            for reason in record['invalid_reasons'])
        missing_indexes = sorted(
            part.get('index') for part in craft_parts
            if not evidence_by_index.get(part.get('index')))
        diagnostics.append({
            'code': 'evidence_incomplete',
            'message': (
                f'证据不完整：expected={expected_count} '
                f'evidence={evidence_count} valid={valid_count} '
                f'missing_craft={missing_craft_count} '
                f'invalid_evidence={invalid_evidence_count} '
                f'duplicate_evidence={duplicate_evidence_count} '
                f'unexpected_evidence={unexpected_count}；'
                '原因 ' + ', '.join(f'{k}={v}' for k, v
                                    in reasons_summary.items()) or '无'),
        })
        if missing_indexes:
            diagnostics.append({
                'code': 'missing_craft_instance',
                'message': '缺少 evidence 的 craft index: '
                           + ', '.join(repr(i) for i in missing_indexes),
            })
    if status == UNRECONCILED:
        diagnostics.append({
            'code': 'unreconciled',
            'message': (f'include_all 残差 {residual_include_all:+.6f} t，'
                        f'排除非飞行候选后残差 {residual_excluding:+.6f} t，'
                        '均在严格容差之外'),
        })
    nearest = None
    if target_ok and complete:
        if abs(residual_include_all) <= abs(residual_excluding):
            nearest = 'include_all'
        else:
            nearest = 'exclude_possible_non_flight'
        diagnostics.append({
            'code': 'nearest_scenario',
            'message': (f'最接近方案为 {nearest}（仅供参考，'
                        '不是已确认语义）'),
        })

    source = (craft_draft or {}).get('source') or {}
    return {
        'schema_version': SCHEMA_VERSION,
        'source_kind': SOURCE_KIND,
        'craft_filename': source.get('filename'),
        'loadmeta_filename': loadmeta_filename,
        'index_fingerprint': (mass_evidence or {}).get('index_fingerprint'),
        'source_total_mass_raw': target['raw'],
        'source_total_mass_t': target['t'],
        'reconciliation_status': status,
        'diagnostics': diagnostics,
        'nearest_scenario': nearest,
        'nearest_is_authoritative': False,
        # instance-count conservation
        'expected_instance_count': expected_count,
        'evidence_instance_count': evidence_count,
        'valid_instance_count': valid_count,
        'invalid_evidence_instance_count': invalid_evidence_count,
        'missing_craft_instance_count': missing_craft_count,
        'unexpected_evidence_instance_count': unexpected_count,
        'duplicate_evidence_instance_count': duplicate_evidence_count,
        'unresolved_instance_count': unresolved_count,
        'missing_mass_instance_count': missing_mass_count,
        # compatibility alias: invalid evidence records only (NOT the
        # total unresolved count — see missing_mass_instance_count)
        'invalid_instance_count': invalid_evidence_count,
        # scenario A
        'estimated_total_include_all_t': include_all_t if complete
        else None,
        'included_instance_count': valid_count,
        # scenario B
        'estimated_total_excluding_non_flight_t': excluding_t if complete
        else None,
        'possible_non_flight_instance_count': len(non_flight_all),
        'known_possible_non_flight_mass_t': round(non_flight_known_mass, 12),
        'possible_non_flight_mass_complete': non_flight_complete,
        'possible_non_flight_mass_t': (round(non_flight_known_mass, 12)
                                       if complete else None),
        'possible_non_flight_types': sorted(
            {e['base_part'] for e in non_flight_all if e['base_part']}),
        'excluded_instance_evidence': non_flight_all,
        # evidence completeness
        'complete_evidence': complete,
        'known_mass_subtotal_t': round(include_all_t, 12),
        'missing_mass_instance_count': missing_mass_count,
        'unresolved_instance_evidence': unresolved,
        # residuals
        'residual_include_all_t': residual_include_all,
        'residual_excluding_non_flight_t': residual_excluding,
        'relative_residual_include_all': relative_include_all,
        'relative_residual_excluding_non_flight': relative_excluding,
        'tolerance_t': tolerance_t,
        # forensic aggregates (for 6B.3C residual-source analysis)
        'resolution_status_distribution': dict(resolution_dist),
        'dynamic_mass_modules_distribution': dict(dynamic_dist),
        'part_type_counts': dict(part_type_count),
        'resource_mass_total_t': round(resource_mass_total, 12),
        'static_or_modified_dry_mass_total_t': round(static_mass_total, 12),
        'largest_mass_instance': largest,
    }
