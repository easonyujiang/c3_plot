"""Read-only, process-cached runtime fingerprint provider (6B.3C.4A).

The DLL/ConfigCache SHA-256 fingerprints used by the production
RealChute authorization gate must NOT be recomputed per PART.  The
caller (``import_service.preview_craft`` / ``preview_batch``, the audit
tool) reads the provenance ONCE per request and passes it into the mass
evidence layer (``extract_part_mass_evidence(provenance=...)``).

Caching is keyed by (path, size, mtime_ns): a file whose stat signature
is unchanged reuses the cached digest.  No writes, no mutation of
inputs; thread-safe; deterministic.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

_cache: Dict[str, Tuple[int, int, str]] = {}
_lock = threading.Lock()


def sha256_cached(path) -> Optional[str]:
    """SHA-256 of a file, cached by (size, mtime_ns); None on OSError."""
    path = str(Path(path))
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    key = (stat.st_size, stat.st_mtime_ns)
    with _lock:
        cached = _cache.get(path)
        if cached is not None and cached[0] == key[0] \
                and cached[1] == key[1]:
            return cached[2]
    try:
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None
    with _lock:
        _cache[path] = (stat.st_size, stat.st_mtime_ns, digest)
    return digest


def ksp_runtime_provenance(ksp_root) -> dict:
    """Runtime provenance for the production RealChute gate.

    Returns ``{'config_cache_path', 'config_cache_sha256',
    'realchute_dll_path', 'realchute_dll_sha256'}``; missing files keep
    their SHA-256 ``None`` (the gate fails closed downstream).
    """
    ksp_root = str(Path(ksp_root))
    cache_path = Path(ksp_root) / 'GameData' / 'ModuleManager.ConfigCache'
    realchute_dll = (Path(ksp_root) / 'GameData' / 'RealChute' / 'Plugins'
                     / 'RealChute.dll')
    return {
        'config_cache_path': str(cache_path),
        'config_cache_sha256': sha256_cached(cache_path),
        'realchute_dll_path': str(realchute_dll),
        'realchute_dll_sha256': sha256_cached(realchute_dll),
    }
