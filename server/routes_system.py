"""Small, read-only system summary; does not build the game index."""
from pathlib import Path
import platform
from flask import Blueprint, jsonify
from .config import BASE_DIR
from .craft_library import configured_ksp_root

bp = Blueprint('system', __name__)


@bp.get('/api/system')
def system_status():
    root = configured_ksp_root()
    return jsonify(
        status='ready',
        version=(Path(BASE_DIR) / 'VERSION').read_text().strip(),
        platform=platform.system(),
        ksp={
            'connected': root is not None,
            'path': str(root) if root else None,
            'config_available': bool(root and (root / 'GameData/ModuleManager.ConfigCache').is_file()),
            'exporter_available': bool(root and (root / 'GameData/C3MassTelemetry/Plugins/C3MassTelemetry.dll').is_file()),
        },
    )
