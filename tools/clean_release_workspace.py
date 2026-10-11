"""Remove known caches and compress large historical logs, preserving their bytes."""
import gzip
import hashlib
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def checked(path):
    resolved = path.resolve(strict=True)
    if resolved == ROOT or not resolved.is_relative_to(ROOT):
        raise RuntimeError(f'Unsafe cleanup target: {path}')
    if path.is_symlink() or path.is_junction():
        raise RuntimeError(f'Refusing linked cleanup target: {path}')
    return resolved

def digest(file):
    with file.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def main():
    removed, compressed, blocked = [], [], []
    candidates = [ROOT / '.pytest_cache', ROOT / '.tmp', ROOT / 'output/automatic_import_20261010/test_tmp']
    for directory, subdirs, _ in os.walk(ROOT):
        subdirs[:] = [name for name in subdirs if name not in {'.git', '.venv', 'node_modules', 'output', 'backups', '.tmp'}]
        if '__pycache__' in subdirs:
            candidates.append(Path(directory) / '__pycache__')
            subdirs.remove('__pycache__')
    for path in candidates:
        if not path.exists():
            continue
        try:
            checked(path)
            files = list(path.rglob('*'))
            for child in files:
                checked(child)
        except PermissionError as error:
            blocked.append({'path': path.relative_to(ROOT).as_posix(), 'error': str(error)})
            continue
        size = sum(p.stat().st_size for p in files if p.is_file())
        removed.append({'path': path.relative_to(ROOT).as_posix(), 'bytes': size})
        shutil.rmtree(path)
    for log in sorted((ROOT / 'output').rglob('*.log')):
        if log.stat().st_size < 1024 * 1024:
            continue
        checked(log)
        target = log.with_suffix(log.suffix + '.gz')
        if target.exists():
            raise RuntimeError(f'Archive already exists: {target}')
        original_hash, size = digest(log), log.stat().st_size
        with log.open('rb') as source, gzip.open(target, 'wb', compresslevel=6) as destination:
            shutil.copyfileobj(source, destination)
        with gzip.open(target, 'rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != original_hash:
                raise RuntimeError(f'Compression verification failed: {log}')
        checked(log).unlink()
        compressed.append({'original': log.relative_to(ROOT).as_posix(), 'archive': target.relative_to(ROOT).as_posix(),
                           'sha256_uncompressed': original_hash, 'before_bytes': size, 'after_bytes': target.stat().st_size})
    result = {'removed_caches': removed, 'blocked_caches': blocked, 'compressed_logs': compressed,
              'saved_bytes': sum(p['bytes'] for p in removed) + sum(p['before_bytes'] - p['after_bytes'] for p in compressed)}
    out = ROOT / 'output/release_20261011'
    out.mkdir(parents=True, exist_ok=True)
    # Preserve previous cleanup manifests when running again.
    name = 'cleanup-manifest.json'
    if (out / name).exists():
        name = f'cleanup-manifest-{len(list(out.glob("cleanup-manifest*.json")))}.json'
    (out / name).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'removed_directories': len(removed), 'blocked_caches': blocked, 'compressed_logs': len(compressed), 'saved_bytes': result['saved_bytes']}))

if __name__ == '__main__':
    main()
