"""Read game inputs on demand; never copy them into the project."""
from functools import lru_cache
import os
from pathlib import Path
import re


def ksp_root():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False)
    value = os.environ.get('C3_MASS_KSP_ROOT', '')
    if os.name == 'posix' and re.match(r'^[A-Za-z]:[\\/]', value):
        value = '/mnt/' + value[0].lower() + '/' + value[3:].replace('\\', '/')
    root = Path(value) if value else None
    if not root or not (root / 'GameData').is_dir():
        raise FileNotFoundError('未找到 KSP 游戏目录，请在 .env 设置 C3_MASS_KSP_ROOT')
    return root.resolve()


def craft_files():
    root = ksp_root()
    # Live saved designs only. Never fall back to a project VAB snapshot.
    return sorted(path for path in (root / 'saves').glob('*/Ships/VAB/*.craft')
                  if path.is_file() and path.resolve().is_relative_to(root))


@lru_cache(maxsize=2)
def _cfg_paths(root, signature):
    paths = {}
    cache = Path(root) / 'GameData/ModuleManager.ConfigCache'
    if cache.is_file():
        text = cache.read_text(encoding='utf-8-sig', errors='replace')
        sources = sorted(set(re.findall(r'^\s*parentUrl\s*=\s*(.+?\.cfg)\s*$', text, re.MULTILINE)))
        for source in sources:
            path = Path(root) / 'GameData' / source.lstrip('/')
            paths.setdefault(path.name, []).append(path)
        return paths
    for directory, _, filenames in os.walk(Path(root) / 'GameData', followlinks=False):
        for name in sorted(filenames):
            if name.endswith('.cfg'):
                paths.setdefault(name, []).append(Path(directory) / name)
    return paths


def _current_cfg_paths():
    root = ksp_root()
    cache = root / 'GameData/ModuleManager.ConfigCache'
    stamp = (cache if cache.is_file() else root / 'GameData').stat()
    return root, _cfg_paths(str(root), (stamp.st_mtime_ns, stamp.st_size))


def engine_cfg_path(filename):
    if not isinstance(filename, str) or Path(filename).name != filename or '\\' in filename or not filename.endswith('.cfg'):
        raise ValueError('发动机文件必须是 .cfg 文件名')
    root, paths = _current_cfg_paths()
    matches = paths.get(filename, [])
    if not matches:
        raise FileNotFoundError(f'KSP GameData 中未找到 {filename}')
    if len(matches) != 1:
        raise ValueError(f'KSP 中有多个同名配置 {filename}，请通过游戏导入使用已解析的运行时性能')
    path = matches[0].resolve()
    if not path.is_relative_to(root):
        raise ValueError('发动机配置超出游戏目录')
    return path


def engine_cfg_files():
    """Explicit reload only: enumerate live engine configs without persisting copies."""
    root, indexed = _current_cfg_paths()
    for paths in indexed.values():
        if len(paths) != 1:
            continue
        path = paths[0].resolve()
        if not path.is_relative_to(root) or not path.is_file():
            continue
        text = path.read_text(encoding='utf-8-sig', errors='replace')
        if re.search(r'name\s*=\s*ModuleEngines(?:FX)?\b', text):
            yield path
