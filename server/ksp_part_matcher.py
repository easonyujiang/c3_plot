"""
KSP craft PART instance ↔ installation index matcher (phase 6B.2).
====================================================================

Deterministic, pure matching between every PART instance in a parsed
``.craft`` draft and the KSP installation index built by
:mod:`server.ksp_install_index`.

Rules (strict, in order):

1. empty/invalid ``base_part`` -> ``invalid_part``, no guessing;
2. case-sensitive FULL-name match on the original ``base_part``:
   unique -> ``exact``; several -> ``ambiguous`` (never silently picks the
   index primary);
3. only when the original name has NO candidates at all: replace every
   ``.`` with ``_`` and match again (unique -> ``normalized_unique``,
   several -> ``ambiguous``);
4. still nothing -> ``missing``.

Forbidden: edit distance, prefix/suffix/substring matching, title
matching, case-insensitive auto-selection, dropping ``.v2``, mapping
``_`` back to ``.``, "looks most similar" heuristics, and masking
duplicate definitions behind ``primary_part``.  Case-variant names may
appear as diagnostic suggestions only — never auto-selected.

The module is pure: it never scans disks, never mutates the craft draft
or the index, never writes to the database or YAML, does not touch Flask,
and its output is JSON-serializable and deterministic for identical
inputs.

NOTE: no mass, propellant, staging, engine-count, delta-V or payload
solving happens here — that is 6B.3+.
"""

from __future__ import annotations

import copy
from typing import Dict, List, Optional

SCHEMA_VERSION = 1
SOURCE_KIND = 'ksp_part_matcher'

# statuses ----------------------------------------------------------------
EXACT = 'exact'
NORMALIZED_UNIQUE = 'normalized_unique'
AMBIGUOUS = 'ambiguous'
MISSING = 'missing'
INVALID_PART = 'invalid_part'
INDEX_UNAVAILABLE = 'index_unavailable'
_ALL_STATUSES = (EXACT, NORMALIZED_UNIQUE, AMBIGUOUS, MISSING,
                 INVALID_PART, INDEX_UNAVAILABLE)

# strategies --------------------------------------------------------------
STRATEGY_EXACT_NAME = 'exact_name'
STRATEGY_DOT_TO_UNDERSCORE = 'dot_to_underscore'
STRATEGY_NONE = 'none'

_CONFIDENCE = {
    EXACT: 'high',
    NORMALIZED_UNIQUE: 'medium',
    AMBIGUOUS: 'low',
    MISSING: 'none',
    INVALID_PART: 'none',
    INDEX_UNAVAILABLE: 'none',
}


def _candidate_dict(record: dict) -> dict:
    return {
        'name': record.get('name'),
        'parent_url': record.get('parent_url'),
        'cache_order': record.get('cache_order'),
        'primary': bool(record.get('primary')),
        'diagnostics': [d.get('code') for d in record.get('diagnostics', [])],
    }


def _match_one(part: dict, index, available: bool) -> dict:
    base_part = part.get('base_part') or ''
    requested = base_part
    record = {
        'craft_index': part.get('index'),
        'part': part.get('part'),
        'base_part': base_part,
        'instance_id': part.get('instance_id'),
        'persistent_id': part.get('persistent_id'),
        'requested_name': requested,
        'canonical_name': '',
        'status': MISSING,
        'strategy': STRATEGY_NONE,
        'confidence': 'none',
        'candidates': [],
        'selected': None,
        'diagnostics': [],
    }

    if not available:
        record.update({
            'status': INDEX_UNAVAILABLE,
            'strategy': STRATEGY_NONE,
            'confidence': _CONFIDENCE[INDEX_UNAVAILABLE],
            'canonical_name': requested,
            'diagnostics': [{
                'code': 'index_unavailable',
                'message': 'KSP 安装索引不可用，无法匹配该部件实例',
            }],
        })
        return record

    if not base_part:
        record.update({
            'status': INVALID_PART,
            'strategy': STRATEGY_NONE,
            'confidence': _CONFIDENCE[INVALID_PART],
            'diagnostics': [{
                'code': 'invalid_base_part',
                'message': 'PART 实例缺少部件名称，无法匹配',
            }],
        })
        return record

    candidates = index.find_parts(base_part)
    if candidates:
        return _finish(record, base_part, STRATEGY_EXACT_NAME, candidates)

    normalized = base_part.replace('.', '_')
    if normalized == base_part:
        return _missing(record, base_part, STRATEGY_EXACT_NAME, index)

    normalized_candidates = index.find_parts(normalized)
    if not normalized_candidates:
        return _missing(record, base_part, STRATEGY_DOT_TO_UNDERSCORE, index)

    record['diagnostics'].append({
        'code': 'name_normalized',
        'message': f'{base_part!r} → {normalized!r}（. 替换为 _ 后再次匹配）',
    })
    return _finish(record, normalized, STRATEGY_DOT_TO_UNDERSCORE,
                   normalized_candidates)


def _finish(record: dict, canonical_name: str, strategy: str,
            candidates: List[dict]) -> dict:
    """Unique candidate -> exact/normalized_unique; several -> ambiguous."""
    candidate_dicts = [_candidate_dict(c) for c in candidates]
    record['candidates'] = candidate_dicts
    if len(candidates) == 1:
        status = EXACT if strategy == STRATEGY_EXACT_NAME else NORMALIZED_UNIQUE
        record.update({
            'status': status,
            'strategy': strategy,
            'confidence': _CONFIDENCE[status],
            'canonical_name': canonical_name,
            'selected': candidate_dicts[0],
        })
        return record
    record.update({
        'status': AMBIGUOUS,
        'strategy': strategy,
        'confidence': _CONFIDENCE[AMBIGUOUS],
        'canonical_name': canonical_name,
        'selected': None,
        'diagnostics': [{
            'code': 'ambiguous_definition',
            'message': (f'{canonical_name!r} 在索引中存在 '
                        f'{len(candidates)} 个定义，未自动选取；'
                        '后续质量计算不能安全使用该实例'),
        }] + record['diagnostics'],
    })
    return record


def _case_variant_suggestions(base_part: str, index) -> List[str]:
    """Case-insensitive name matches for diagnostics only (never selected)."""
    lowered = base_part.casefold()
    return sorted({record.get('name') for record in index.parts
                   if record.get('name')
                   and record['name'].casefold() == lowered
                   and record['name'] != base_part})


def _missing(record: dict, base_part: str, strategy: str, index) -> dict:
    suggestions = _case_variant_suggestions(base_part, index)
    diagnostics = [{
        'code': 'no_candidates',
        'message': f'索引中不存在部件 {base_part!r}',
    }]
    if suggestions:
        diagnostics.append({
            'code': 'case_variant_suggestion',
            'message': ('仅大小写不同的候选（仅供人工参考，未自动选中）: '
                        + ', '.join(repr(s) for s in suggestions[:3])),
        })
    record.update({
        'status': MISSING,
        'strategy': strategy,
        'confidence': _CONFIDENCE[MISSING],
        'canonical_name': base_part,
        'selected': None,
        'diagnostics': diagnostics,
    })
    return record


def match_craft_parts(craft_draft, ksp_index) -> dict:
    """Match every PART instance of a craft draft against the index.

    ``ksp_index`` may be None (every instance becomes
    ``index_unavailable``).  Returns a deterministic, JSON-serializable
    report; never mutates ``craft_draft`` or ``ksp_index``.
    """
    available = ksp_index is not None
    parts = list((craft_draft or {}).get('parts') or [])
    instances = [_match_one(part, ksp_index, available) for part in parts]

    by_status: Dict[str, int] = {}
    for status in _ALL_STATUSES:
        by_status[status] = sum(1 for r in instances
                                if r['status'] == status)
    exact = by_status[EXACT]
    normalized = by_status[NORMALIZED_UNIQUE]
    matched = exact + normalized
    total = len(instances)
    coverage = (matched / total) if total else 0.0

    unique_types = sorted({r['base_part'] for r in instances
                           if r['base_part']})
    matched_types = sorted({r['base_part'] for r in instances
                            if r['status'] in (EXACT, NORMALIZED_UNIQUE)
                            and r['base_part']})
    type_coverage = (len(matched_types) / len(unique_types)
                     if unique_types else 0.0)

    fingerprint = None
    if available and isinstance(getattr(ksp_index, 'metadata', None), dict):
        meta = ksp_index.metadata
        fingerprint = {
            'schema_version': meta.get('schema_version'),
            'source_kind': meta.get('source_kind'),
            'config_cache_path': meta.get('config_cache_path'),
            'file_size': meta.get('file_size'),
            'mtime_ns': meta.get('mtime_ns'),
            'sha256': meta.get('sha256'),
            'patched_node_count': meta.get('patched_node_count'),
            'built_at': meta.get('built_at'),
        }

    return {
        'schema_version': SCHEMA_VERSION,
        'source_kind': SOURCE_KIND,
        'index_fingerprint': fingerprint,
        'total_instances': total,
        'matched_instances': matched,
        'exact_instances': exact,
        'normalized_instances': normalized,
        'ambiguous_instances': by_status[AMBIGUOUS],
        'missing_instances': by_status[MISSING],
        'invalid_instances': by_status[INVALID_PART],
        'index_unavailable_instances': by_status[INDEX_UNAVAILABLE],
        'coverage_ratio': coverage,
        'unique_part_types': len(unique_types),
        'matched_unique_part_types': len(matched_types),
        'unique_part_type_coverage_ratio': type_coverage,
        'by_status': by_status,
        'instances': instances,
    }


def _structured_warning(code, message, *, severity='warning', part_name=None,
                        part_id=None, impact='', details=None):
    return {
        'code': code,
        'severity': severity,
        'message': message,
        'part_name': part_name,
        'part_id': part_id,
        'module': None,
        'field': None,
        'impact': impact,
        'details': details or {},
    }


def _match_warnings(report: dict) -> List[dict]:
    """Structured warnings derived from the match report (missing and
    ambiguous are warnings; normalized_unique is informational)."""
    warnings = []
    if report.get('index_fingerprint') is None:
        warnings.append(_structured_warning(
            'index_unavailable',
            'KSP 安装索引不可用，部件实例未与已安装配置匹配',
            severity='warning',
            impact='匹配与质量证据缺失；请先启动一次 KSP 生成 ModuleManager 缓存',
            details={'total_instances': report.get('total_instances')}))
        return warnings
    for instance in report.get('instances') or []:
        status = instance['status']
        if status == MISSING:
            warnings.append(_structured_warning(
                'part_match_missing',
                f'部件 {instance["base_part"]!r} 未在已安装配置中找到',
                severity='warning',
                part_name=instance['base_part'],
                part_id=instance['persistent_id'],
                impact='该实例无法进入质量求解，需要人工处理',
                details={'craft_index': instance['craft_index'],
                         'canonical_name': instance['canonical_name']}))
        elif status == AMBIGUOUS:
            warnings.append(_structured_warning(
                'part_match_ambiguous',
                f'部件 {instance["base_part"]!r} 在索引中有多个定义，未自动选取',
                severity='warning',
                part_name=instance['base_part'],
                part_id=instance['persistent_id'],
                impact='该实例无法安全用于后续质量计算',
                details={
                    'craft_index': instance['craft_index'],
                    'candidates': [c['name'] for c in instance['candidates']],
                }))
        elif status == NORMALIZED_UNIQUE:
            warnings.append(_structured_warning(
                'part_match_normalized',
                f'部件 {instance["base_part"]!r} 已按规则匹配到 '
                f'{instance["canonical_name"]!r}（. → _）',
                severity='info',
                part_name=instance['base_part'],
                part_id=instance['persistent_id'],
                impact='无',
                details={'craft_index': instance['craft_index'],
                         'canonical_name': instance['canonical_name']}))
    return warnings


def enrich_craft_draft(craft_draft, ksp_index, report=None) -> dict:
    """Return a NON-destructive copy of the craft draft with the match
    report attached as ``installation_match`` and match-derived structured
    warnings appended.  The original draft is never modified.
    """
    draft = copy.deepcopy(craft_draft)
    if report is None:
        report = match_craft_parts(craft_draft, ksp_index)
    draft['installation_match'] = report
    warnings = list(draft.get('structured_warnings') or [])
    warnings.extend(_match_warnings(report))
    draft['structured_warnings'] = warnings
    return draft
