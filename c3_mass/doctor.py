"""Read-only installation diagnostics. Does not initialize the database."""
from importlib import metadata
import os
from pathlib import Path
import platform
import sys


def diagnose(root: Path):
    checks = []
    def add(name, ok, detail, required=True):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail, 'required': required})
    add('Python', (3, 12) <= sys.version_info[:2] < (3, 15), platform.python_version())
    for package in ('flask', 'numpy', 'pyyaml', 'waitress', 'python-dotenv'):
        try:
            add(package, True, metadata.version(package))
        except metadata.PackageNotFoundError:
            add(package, False, 'Missing; run the setup script')
    add('Frontend', (root / 'frontend/dist/index.html').is_file(), 'frontend/dist/index.html')
    # Keep diagnostics independent of the server and database.
    import re
    def native_path(value):
        if os.name == 'posix' and re.match(r'^[A-Za-z]:[\\/]', value):
            mount = Path('/mnt') / value[0].lower()
            if mount.is_dir():
                return str(mount / value[3:].replace('\\', '/'))
        return value
    game = os.environ.get('C3_MASS_KSP_ROOT', '')
    game_root = Path(native_path(game)) if game else None
    add('KSP', bool(game_root and (game_root / 'GameData').is_dir()), game or 'Set C3_MASS_KSP_ROOT in .env', False)
    return checks
