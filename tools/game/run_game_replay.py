"""Run an armed isolated replay with a bounded lifetime/log and owned process."""
import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path


def run(ksp_root, output, expected_vessel='1T_Test', editor=False):
    output.mkdir(parents=True, exist_ok=True)
    log = output / 'game.log'
    if log.exists():
        raise ValueError('Preserve existing attempt output')
    status = ksp_root / 'GameData/C3MassValidation/status.log'
    status_prefix = status.read_text(encoding='utf-8') if status.exists() else ''
    def current_status():
        text = status.read_text(encoding='utf-8') if status.exists() else ''
        if not text.startswith(status_prefix):
            raise ValueError('Replay status log changed unexpectedly')
        return text[len(status_prefix):]
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    process = subprocess.Popen([str(ksp_root / 'KSP_x64.exe'), '-logFile', str(log.resolve())],
                               cwd=ksp_root, startupinfo=startup)
    start = time.monotonic()
    previous = ''
    reason = 'process_exited'
    print('Owned game process:', process.pid, flush=True)
    try:
        while process.poll() is None:
            current = current_status()
            if current != previous:
                print(current[len(previous):].strip(), flush=True)
                previous = current
            if time.monotonic() - start > 600:
                reason = 'startup_or_replay_timeout'
                break
            if log.exists() and log.stat().st_size > 96 * 1024 * 1024:
                reason = 'unity_log_growth_limit'
                break
            time.sleep(2)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
    current = current_status()
    # Original manifests cover only the real source save, never this test copy.
    manifest_path = output.parent / 'original-save-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    changed = [entry['path'] for entry in manifest
               if hashlib.sha256(Path(entry['path']).read_bytes()).hexdigest() != entry['sha256']]
    result = {'pid': process.pid, 'exit_code': process.returncode, 'reason': reason,
              'elapsed_s': round(time.monotonic() - start, 2), 'status_log': current,
              'original_save_files_checked': len(manifest), 'original_save_files_changed': changed,
              'expected_vessel': expected_vessel,
              'expected_vessel_observed': ('editor_saved: ' + expected_vessel if editor else 'flight_ready: ' + expected_vessel + ' ') in current,
              'flight_replay_completed': 'replay_complete' in current}
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result['flight_replay_completed'] and result['expected_vessel_observed'] and not changed else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ksp-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--expected-vessel', default='1T_Test')
    parser.add_argument('--editor', action='store_true')
    args = parser.parse_args()
    raise SystemExit(run(args.ksp_root, args.output, args.expected_vessel, args.editor))
