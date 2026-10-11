"""Read-only discovery of saved VAB craft under the configured KSP install.

Only saves/<save>/Ships/VAB/<file>.craft is eligible. No arbitrary local
path is accepted by the HTTP API, and import rechecks the listed SHA-256.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path, PurePosixPath

from .config import KSP_INSTALL_CANDIDATES

MAX_CRAFT_BYTES = 25 * 1024 * 1024
MAX_LIST_ITEMS = 500


def configured_ksp_root():
    override = os.environ.get('C3_MASS_KSP_ROOT')
    candidates = [override] if override else KSP_INSTALL_CANDIDATES
    return next((Path(path).resolve() for path in candidates
                 if path and (Path(path) / 'saves').is_dir()), None)


def craft_path(root, craft_id):
    if not isinstance(craft_id, str) or '\\' in craft_id or ':' in craft_id:
        raise ValueError('无效的火箭文件标识')
    parts = PurePosixPath(craft_id).parts
    if (len(parts) != 4 or parts[1:3] != ('Ships', 'VAB')
            or any(part in ('.', '..') for part in parts)
            or PurePosixPath(craft_id).as_posix() != craft_id
            or not parts[-1].lower().endswith('.craft')):
        raise ValueError('只允许读取存档中的 VAB .craft 文件')
    saves = (Path(root) / 'saves').resolve()
    path = saves.joinpath(*parts).resolve()
    if not path.is_relative_to(saves):
        raise ValueError('火箭文件超出游戏存档目录')
    return path


def read_craft(root, craft_id, expected_sha256=None):
    path = craft_path(root, craft_id)
    if path.stat().st_size > MAX_CRAFT_BYTES:
        raise ValueError('火箭文件超过 25 MB 限制')
    with path.open('rb') as stream:
        content = stream.read(MAX_CRAFT_BYTES + 1)
    if len(content) > MAX_CRAFT_BYTES:
        raise ValueError('火箭文件超过 25 MB 限制')
    digest = hashlib.sha256(content).hexdigest()
    if expected_sha256 is not None and expected_sha256 != digest:
        raise ValueError('火箭已重新保存，请刷新列表后再导入')
    loadmeta_path = path.with_suffix('.loadmeta').resolve()
    loadmeta = None
    if loadmeta_path.is_relative_to((Path(root) / 'saves').resolve()) and loadmeta_path.is_file():
        with loadmeta_path.open('rb') as stream:
            raw = stream.read(MAX_CRAFT_BYTES + 1)
        if len(raw) <= MAX_CRAFT_BYTES:
            from .import_service import _loadmeta_metadata
            loadmeta = _loadmeta_metadata(raw.decode('utf-8-sig'))
    return path.name, content, loadmeta, digest


def list_crafts(root, *, include_validation=False):
    saves = (Path(root) / 'saves').resolve()
    items, warnings = [], []
    if not saves.is_dir():
        return {'items': [], 'warnings': ['未找到游戏存档目录'], 'truncated': False}
    truncated = False
    for save in sorted(saves.iterdir(), key=lambda p: p.name.casefold()):
        # The agent's isolated game-validation save is not a user's design.
        if not save.is_dir() or (save.name == 'C3MassValidation20261010' and not include_validation):
            continue
        directory = save / 'Ships' / 'VAB'
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
            if path.suffix.lower() != '.craft' or not path.is_file():
                continue
            if len(items) >= MAX_LIST_ITEMS:
                truncated = True
                break
            craft_id = '/'.join((save.name, 'Ships', 'VAB', path.name))
            try:
                eligible = craft_path(root, craft_id)
                stat = eligible.stat()
                if stat.st_size > MAX_CRAFT_BYTES:
                    raise ValueError('文件超过 25 MB 限制')
                with eligible.open('rb') as stream:
                    raw = stream.read(MAX_CRAFT_BYTES + 1)
                if len(raw) > MAX_CRAFT_BYTES:
                    raise ValueError('文件超过 25 MB 限制')
                header = raw[:16384].decode('utf-8-sig', errors='replace')
                name = re.search(r'^ship\s*=\s*(.+)$', header, re.MULTILINE)
                items.append({'id': craft_id, 'filename': path.name,
                              'name': name.group(1).strip() if name else path.stem,
                              'save': save.name, 'size_bytes': len(raw),
                              'modified_unix_s': stat.st_mtime,
                              'sha256': hashlib.sha256(raw).hexdigest()})
            except (OSError, ValueError) as exc:
                warnings.append(f'{save.name}/{path.name}: {exc}')
        if truncated:
            break
    items.sort(key=lambda item: (-item['modified_unix_s'], item['id']))
    return {'items': items, 'warnings': warnings, 'truncated': truncated}
