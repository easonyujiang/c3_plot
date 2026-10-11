"""Unified, idempotent import pipeline for YAML and KSP VAB files."""
from __future__ import annotations

import hashlib
import os

from compute_c3_mass import compute_rocket
from . import models
from .ksp_confignode import CraftParseError
from .ksp_craft_mass_reconciliation import reconcile_craft_mass
from .ksp_install_index import get_auto_ksp_index
from .ksp_mass_evidence import extract_part_mass_evidence
from .ksp_part_matcher import enrich_craft_draft, match_craft_parts
from .ksp_runtime_fingerprints import ksp_runtime_provenance
from .validation import validate_rocket
from .vab_parser import parse_craft
from .yaml_codec import load_config


MAX_FILES = 200
MAX_FILE_BYTES = 25 * 1024 * 1024
SUPPORTED = ('.yaml', '.yml', '.craft')


def _craft_error(code, message, line=None, column=None, node_path=None):
    """Stable, frontend-safe error payload shared by all preview paths."""
    return {
        'error': message,
        'error_code': code,
        'line': line,
        'column': column,
        'node_path': node_path,
    }


def _filename(file_obj):
    return os.path.basename(file_obj.filename or 'unnamed')


def _loadmeta_metadata(text):
    metadata = {}
    for line in text.splitlines():
        if ' = ' in line:
            key, value = line.split(' = ', 1)
            if key == 'partNames':
                metadata.setdefault('part_names', []).append(value)
            else:
                metadata[key] = value
    return metadata


def _read_files(file_objects):
    objects = {}
    for file_obj in file_objects:
        filename = _filename(file_obj)
        lowered = filename.lower()
        if lowered.endswith('.craft.original') or lowered.endswith('.yaml.original'):
            continue
        if not lowered.endswith(SUPPORTED) and not lowered.endswith('.loadmeta'):
            continue
        content = file_obj.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            objects[filename] = _craft_error(
                'file_too_large',
                f'文件超过 {MAX_FILE_BYTES // 1024 // 1024} MB 限制')
            continue
        objects[filename] = content
    return objects


def _source(filename, content, source_format, metadata=None, warnings=None):
    return {
        'filename': filename,
        'sha256': hashlib.sha256(content).hexdigest(),
        'format': source_format,
        'metadata': metadata or {},
        'warnings': warnings or [],
    }


def preview_craft(filename, content, loadmeta=None, ksp_index=None):
    """Parse one .craft file into a preview item; never writes anything.

    Shared by the single-file preview endpoint and the batch preview so the
    two can never drift apart.  Status contract:

    * invalid craft (no top-level PART, size/encoding/ConfigNode errors) ->
      ``status=error`` / ``action=error`` with a stable error payload;
    * valid craft -> ``status=needs_review`` / ``action=review`` until mass
      is confirmed (``requires_mass_confirmation``);
    * previously imported hash -> ``status=duplicate`` / ``action=duplicate``.

    When ``ksp_index`` is None the lazy auto-index provider is consulted;
    if no index is available the preview still succeeds and reports
    ``index_unavailable`` (never crashes, never rebuilds per part).
    """
    lowered = filename.lower()
    if not lowered.endswith('.craft'):
        return dict(file=filename, status='error', action='error',
                    **_craft_error('unsupported_format', '只支持 .craft 文件'))
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        return dict(file=filename, status='error', action='error',
                    **_craft_error('invalid_encoding', '文件不是有效的 UTF-8 编码'))
    except AttributeError:
        return dict(file=filename, status='error', action='error',
                    **_craft_error('invalid_input_type', '文件内容类型无效'))
    try:
        draft = parse_craft(text, filename)
    except CraftParseError as exc:
        return dict(file=filename, status='error', action='error',
                    **_craft_error(exc.code, exc.message,
                                   line=exc.line, column=exc.column,
                                   node_path=exc.node_path))
    except Exception:
        return dict(file=filename, status='error', action='error',
                    **_craft_error('internal_error', 'Craft 解析失败：文件无法处理'))

    structured = list(draft.get('structured_warnings') or [])
    fatal = [w for w in structured if w.get('severity') == 'error']
    if fatal or not (draft.get('source') or {}).get('part_count'):
        entry = fatal[0] if fatal else None
        return dict(file=filename, status='error', action='error',
                    **_craft_error(
                        entry['code'] if entry else 'no_parts_found',
                        entry['message'] if entry
                        else '未找到 PART 节点，这不是有效的 KSP .craft 文件'))

    if ksp_index is None:
        ksp_index = get_auto_ksp_index()
    draft = enrich_craft_draft(draft, ksp_index)
    provenance = None
    if ksp_index is not None:
        ksp_root = ((getattr(ksp_index, 'metadata', None) or {})
                    .get('ksp_root'))
        if ksp_root:
            provenance = ksp_runtime_provenance(ksp_root)
    draft['ksp_runtime_provenance'] = provenance
    try:
        draft['mass_evidence'] = extract_part_mass_evidence(
            draft, ksp_index,
            installation_match=draft.get('installation_match'),
            provenance=provenance)
    except Exception:
        # Mass evidence must never break the preview: degrade to a warning.
        draft['mass_evidence'] = None
        draft.setdefault('structured_warnings', []).append({
            'code': 'mass_evidence_failed',
            'severity': 'warning',
            'message': '逐部件质量证据提取失败，预览仍可继续',
            'part_name': None, 'part_id': None, 'module': None,
            'field': None,
            'impact': '质量证据缺失，草稿仍需人工确认',
            'details': {},
        })
    if draft.get('mass_evidence') is not None:
        loadmeta_filename = (os.path.splitext(filename)[0] + '.loadmeta'
                             if filename else None)
        try:
            draft['mass_reconciliation'] = reconcile_craft_mass(
                draft, draft['mass_evidence'], loadmeta,
                loadmeta_filename=loadmeta_filename)
        except Exception:
            draft['mass_reconciliation'] = None
            draft.setdefault('structured_warnings', []).append({
                'code': 'mass_reconciliation_failed',
                'severity': 'warning',
                'message': '整箭质量对账层异常，预览仍可继续',
                'part_name': None, 'part_id': None, 'module': None,
                'field': None,
                'impact': '质量对账信息缺失，草稿仍需人工确认',
                'details': {},
            })
        status = (draft.get('mass_reconciliation') or {}).get(
            'reconciliation_status')
        if status == 'unreconciled':
            reconciliation = draft['mass_reconciliation']
            details = {
                'source_total_mass_t': reconciliation.get(
                    'source_total_mass_t'),
                'estimated_total_include_all_t': reconciliation.get(
                    'estimated_total_include_all_t'),
                'residual_include_all_t': reconciliation.get(
                    'residual_include_all_t'),
                'estimated_total_excluding_non_flight_t':
                    reconciliation.get(
                        'estimated_total_excluding_non_flight_t'),
                'residual_excluding_non_flight_t': reconciliation.get(
                    'residual_excluding_non_flight_t'),
            }
            message = (
                '整箭质量对账未通过：loadmeta 总质量 '
                f'{details["source_total_mass_t"]} t，'
                'include_all 估计 '
                f'{details["estimated_total_include_all_t"]} t，'
                f'残差 {details["residual_include_all_t"]:+.6f} t；'
                '排除非飞行候选后估计 '
                f'{details["estimated_total_excluding_non_flight_t"]} t，'
                f'残差 {details["residual_excluding_non_flight_t"]:+.6f} t')
            draft.setdefault('structured_warnings', []).append({
                'code': 'mass_reconciliation_unreconciled',
                'severity': 'warning',
                'message': message,
                'part_name': None, 'part_id': None, 'module': None,
                'field': None,
                'impact': '质量证据存在无法解释的残差，草稿仍需人工确认',
                'details': details,
            })
        elif status == 'target_invalid':
            reconciliation = draft['mass_reconciliation']
            draft.setdefault('structured_warnings', []).append({
                'code': 'mass_reconciliation_target_invalid',
                'severity': 'warning',
                'message': (
                    f'.loadmeta.totalMass 非法（'
                    f'{reconciliation.get("source_total_mass_raw")!r}），'
                    '整箭质量对账不可用'),
                'part_name': None, 'part_id': None, 'module': None,
                'field': None,
                'impact': '无法与 loadmeta 总质量对账',
                'details': {'source_total_mass_raw': reconciliation.get(
                    'source_total_mass_raw')},
            })
    structured = list(draft.get('structured_warnings') or [])

    metadata = dict(draft.get('source') or {})
    metadata.update(loadmeta or {})
    draft['source'] = metadata
    if ksp_index is not None:
        root = (getattr(ksp_index, 'metadata', None) or {}).get('ksp_root')
        if root:
            from .editor_snapshot import read_snapshot, automatic_stages
            try:
                snapshot = read_snapshot(root, content)
                if snapshot is not None:
                    stages, topology = automatic_stages(snapshot)
                    draft['stages'] = stages
                    draft['automatic_identification'] = {'status':'verified_editor_snapshot',
                        'source':snapshot['source'], 'topology':topology,
                        'editor_dry_mass_t':snapshot['dry_mass_t'],
                        'editor_resource_mass_t':snapshot['resource_mass_t']}
                    draft['requires_mass_confirmation'] = False
                else:
                    draft['automatic_identification'] = {'status':'snapshot_missing',
                        'message':'请在安装采样插件的游戏编辑器内保存这份火箭，以读取 Mod 生效后的数据'}
            except (ValueError, OSError, KeyError) as exc:
                draft['automatic_identification'] = {'status':'needs_review','message':str(exc)}
    valid, errors = validate_rocket(draft)
    status = 'ready' if valid else 'needs_review'
    # Craft drafts have no mass/staging solution yet: they must always be
    # reviewed, even if the placeholder shape would validate.
    if draft.get('requires_mass_confirmation'):
        status = 'needs_review'
    action = 'create' if status == 'ready' else 'review'
    legacy_warnings = list(draft.get('warnings') or [])
    source = _source(filename, content, 'ksp-craft', metadata,
                     legacy_warnings + errors)
    source_hit = models.get_import_source_by_hash(source['sha256'])
    return dict(
        file=filename, name=draft.get('rocket', {}).get('name', filename),
        status='duplicate' if source_hit else status,
        action='duplicate' if source_hit else action,
        config=draft, source=source, warnings=legacy_warnings, errors=errors,
        structured_warnings=structured,
        existing=source_hit,
    )


def _preview_one(filename, content, loadmeta=None, ksp_index=None):
    lowered = filename.lower()
    if isinstance(content, dict) and content.get('error'):
        return dict(file=filename, status='error', action='error', **content)
    if lowered.endswith('.loadmeta'):
        return dict(file=filename, status='metadata', action='skip', message='已作为 .craft 的辅助元数据处理')

    if lowered.endswith('.craft'):
        return preview_craft(filename, content, loadmeta, ksp_index=ksp_index)

    try:
        cfg = load_config(content.decode('utf-8-sig'))
    except Exception as exc:
        return dict(file=filename, status='error', action='error',
                    error=f'YAML 解析失败: {exc}',
                    error_code='yaml_parse_error')
    valid, errors = validate_rocket(cfg)
    rocket = cfg.get('rocket') if isinstance(cfg, dict) else None
    name = rocket.get('name') if isinstance(rocket, dict) else filename
    source = _source(filename, content, 'yaml')
    source_hit = models.get_import_source_by_hash(source['sha256'])
    if not valid:
        return dict(file=filename, name=name or filename, status='error', action='error',
                    config=cfg, source=source, errors=errors)
    slug = models.slugify(name)
    existing = source_hit or ({'rocket_slug': slug, 'rocket_name': name} if models.rocket_exists(slug) else None)
    return dict(
        file=filename, name=name, slug=slug,
        status='duplicate' if source_hit else 'ready',
        action='duplicate' if source_hit else ('update' if existing else 'create'),
        config=cfg, source=source, warnings=[], errors=[], existing=existing,
    )


def preview_batch(file_objects):
    """Parse a mixed file selection without writing rocket data."""
    file_objects = list(file_objects)
    if len(file_objects) > MAX_FILES:
        raise ValueError(f'一次最多导入 {MAX_FILES} 个文件')
    raw = _read_files(file_objects)
    loadmeta_by_stem = {}
    for filename, content in raw.items():
        if filename.lower().endswith('.loadmeta') and isinstance(content, bytes):
            loadmeta_by_stem[os.path.splitext(filename)[0].casefold()] = _loadmeta_metadata(content.decode('utf-8-sig', errors='replace'))
    items = []
    ksp_index = get_auto_ksp_index()
    for filename, content in raw.items():
        stem = os.path.splitext(filename)[0].casefold()
        items.append(_preview_one(filename, content,
                                  loadmeta_by_stem.get(stem),
                                  ksp_index=ksp_index))
    summary = []
    for item in items:
        if item.get('status') == 'error':
            summary.append({
                'file': item.get('file'),
                'severity': 'error',
                'message': item.get('error', '预览失败'),
            })
            continue
        for warning in item.get('structured_warnings') or []:
            if warning.get('severity') == 'info':
                continue
            summary.append({
                'file': item.get('file'),
                'severity': warning.get('severity'),
                'code': warning.get('code'),
                'message': warning.get('message'),
            })
    return {
        'items': items,
        'count': len(items),
        'ready': sum(item.get('status') == 'ready' for item in items),
        'review': sum(item.get('status') == 'needs_review' for item in items),
        'duplicates': sum(item.get('status') == 'duplicate' for item in items),
        'errors': sum(item.get('status') == 'error' for item in items),
        'warnings': summary,
    }


def confirm_batch(items, allow_updates=False):
    """Compute and persist all approved preview items, reporting per-file errors."""
    results = []
    errors = []
    for item in items or []:
        filename = item.get('file', 'unnamed') if isinstance(item, dict) else 'unnamed'
        cfg = item.get('config') if isinstance(item, dict) else None
        source = item.get('source') if isinstance(item, dict) else None
        if not isinstance(cfg, dict):
            errors.append(dict(file=filename, error='缺少可导入配置'))
            continue
        valid, details = validate_rocket(cfg)
        if not valid:
            errors.append(dict(file=filename, error='仍需补全', details=details))
            continue
        if source and models.get_import_source_by_hash(source.get('sha256', '')):
            results.append(dict(file=filename, name=cfg['rocket']['name'], action='duplicate'))
            continue
        name = cfg['rocket']['name']
        slug = models.slugify(name)
        if models.rocket_exists(slug) and not allow_updates:
            errors.append(dict(file=filename, name=name, error='同名火箭已存在；请启用同名更新'))
            continue
        try:
            result = compute_rocket(cfg)
            if models.rocket_exists(slug):
                models.update_rocket(slug, cfg, result, source=source)
                action = 'updated'
            else:
                slug = models.create_rocket(name, cfg, result, source=source)
                action = 'created'
            results.append(dict(file=filename, name=name, slug=slug, action=action))
        except Exception as exc:
            errors.append(dict(file=filename, name=name, error=str(exc)))
    return {
        'imported': len(results),
        'errors': len(errors),
        'results': results,
        'error_details': errors,
    }
