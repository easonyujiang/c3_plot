"""Bounded main-menu boot to verify repaired ModuleManager input files."""
import json
import subprocess
import time
from pathlib import Path

root = Path(r'D:\SteamLibrary\steamapps\common\Kerbal Space Program')
output = Path(__file__).resolve().parents[1] / 'output/automatic_import_20261010/clean-boot'
output.mkdir(parents=True, exist_ok=True)
log = output / 'game.log'
if log.exists():
    raise RuntimeError('Preserve earlier boot evidence')
for name in ('replay.arm', 'pad.arm'):
    if (root / 'GameData/C3MassValidation' / name).exists():
        raise RuntimeError('Validation replay is armed; do not boot')
startup = subprocess.STARTUPINFO()
startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
startup.wShowWindow = subprocess.SW_HIDE
process = subprocess.Popen([str(root / 'KSP_x64.exe'), '-logFile', str(log)], cwd=root, startupinfo=startup)
start = time.monotonic()
ready = False
print('Owned main-menu verification process:', process.pid, flush=True)
try:
    while process.poll() is None and time.monotonic() - start < 600:
        text = log.read_text(encoding='utf-8', errors='replace') if log.exists() else ''
        if 'OnSceneLoadedGUIReady: scene MAINMENU' in text:
            ready = True
            break
        time.sleep(2)
finally:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=20)
cache = root / 'GameData/ModuleManager.ConfigCache'
result = {'main_menu_ready': ready, 'cache_exists': cache.is_file(),
          'elapsed_s': round(time.monotonic() - start, 2), 'pid': process.pid,
          'telemetry_part_urls_present': 'C3MassTelemetry/Logs/' in cache.read_text(errors='replace') if cache.exists() else None}
(output / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result), flush=True)
raise SystemExit(0 if ready and cache.exists() and not result['telemetry_part_urls_present'] else 1)
