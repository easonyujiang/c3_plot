"""Freeze one audited C3Baseline flight into a versioned, hash-bound bundle.

The live evidence stays in the KSP install. This command copies the minimum
set needed to re-audit one flight into ``docs/evidence/<folder>`` and writes a
manifest with every copied file's SHA-256. It never writes to the game save
and never invents a capacity result beyond the audited flight.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from tools.game.audit_baseline import audit
from tools.paths import ROOT


EVIDENCE_ROOT = (ROOT / 'docs' / 'evidence').resolve()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def safe_folder(folder: str) -> str:
    if not folder.startswith('C3Baseline') or Path(folder).name != folder:
        raise ValueError('Require an isolated C3Baseline folder')
    return folder


def status_lines(status_log: Path, folder: str) -> list[str]:
    """Return one contiguous harness run, not just lines that name the folder."""
    if not status_log.is_file():
        return []
    lines = status_log.read_text(encoding='utf-8', errors='replace').splitlines()
    start = None
    for index, line in enumerate(lines):
        if f'armed_pad_inspection {folder}' in line:
            start = index
    if start is None:
        return [line for line in lines if folder in line]
    run = []
    for line in lines[start:]:
        if run and 'armed_pad_inspection ' in line:
            break
        run.append(line)
    return run


def copy_file(source: Path, destination: Path, role: str, files: list[dict]) -> None:
    if not source.is_file():
        return
    shutil.copy2(source, destination)
    files.append({
        'path': destination.name,
        'role': role,
        'bytes': destination.stat().st_size,
        'sha256': sha256(destination),
    })


def freeze(ksp_root: Path, folder: str, output: Path, *, force: bool = False) -> dict:
    folder = safe_folder(folder)
    ksp_root = Path(ksp_root).resolve()
    output = output.resolve()
    if not output.is_relative_to(EVIDENCE_ROOT):
        raise ValueError(f'Evidence must be written under {EVIDENCE_ROOT}')
    if output.exists() and any(output.iterdir()) and not force:
        raise ValueError('Evidence bundle already exists; pass --force to overwrite files')
    output.mkdir(parents=True, exist_ok=True)

    save = ksp_root / 'saves' / folder
    inputs_path = save / 'baseline-inputs.json'
    if not inputs_path.is_file():
        raise ValueError(f'Baseline inputs missing: {inputs_path}')
    inputs = json.loads(inputs_path.read_text(encoding='utf-8'))
    report = audit(ksp_root, folder)
    if not report.get('samples'):
        raise ValueError('No flight telemetry to freeze')

    files: list[dict] = []
    data = ksp_root / 'PluginData' / 'C3Baseline'
    for name, role in (
        (f'{folder}_flight.csv', 'flight_telemetry'),
        (f'{folder}.c3inspection', 'pad_initial_snapshot'),
        (f'{folder}_before_ballast.c3inspection', 'pad_before_ballast_snapshot'),
        (f'{folder}_configured.c3inspection', 'pad_configured_snapshot'),
        (f'{folder}_final.c3inspection', 'flight_final_snapshot'),
    ):
        copy_file(data / name, output / name.replace(folder + '_', ''), role, files)

    copy_file(inputs_path, output / 'prepared-inputs.json', 'prepared_input_manifest', files)

    craft_dir = save / 'Ships' / 'VAB'
    for craft in sorted(craft_dir.glob('*.craft')):
        copy_file(craft, output / 'flown-craft.craft' if len(list(craft_dir.glob('*.craft'))) == 1
                  else output / craft.name, 'flown_craft', files)

    craft_hash = (report.get('initial') or {}).get('craft_sha256', '').lower()
    snapshot = (ksp_root / 'PluginData' / 'C3MassTelemetry' / 'EditorSnapshots'
                / f'{craft_hash}.c3snapshot')
    copy_file(snapshot, output / 'editor-snapshot.c3snapshot', 'editor_snapshot', files)

    lines = status_lines(data / 'status.log', folder)
    (output / 'harness-status.txt').write_text('\n'.join(lines) + ('\n' if lines else ''),
                                               encoding='utf-8')
    files.append({
        'path': 'harness-status.txt',
        'role': 'harness_status',
        'bytes': (output / 'harness-status.txt').stat().st_size,
        'sha256': sha256(output / 'harness-status.txt'),
    })

    (output / 'audit.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    files.append({
        'path': 'audit.json',
        'role': 'audit_result',
        'bytes': (output / 'audit.json').stat().st_size,
        'sha256': sha256(output / 'audit.json'),
    })

    def as_bool(value):
        return value is True or str(value).lower() == 'true'

    final = report.get('final') or {}
    last = report.get('last_sample') or {}
    initial = report.get('initial') or {}
    manifest = {
        'schema': 1,
        'folder': folder,
        'status': report.get('status'),
        'flight': {
            'payload_t': float(final.get('payload_mass_t', 0)),
            'payload_t_nominal': 1.0,
            'payload_parts': final.get('payload_parts'),
            'payload_intact': final.get('payload_parts') == 11,
            'fairing_shielded_on_pad': as_bool(initial.get('payload_shielded')),
            'fairing_shielded_at_end': as_bool(final.get('payload_shielded')),
            'target_periapsis_m': inputs.get('target_pe_m'),
            'target_apoapsis_m': inputs.get('target_ap_m'),
            'target_inclination_deg': inputs.get('inclination_deg'),
            'final_periapsis_m': last.get('periapsis_m'),
            'final_apoapsis_m': last.get('apoapsis_m'),
            'final_inclination_deg': last.get('inclination_deg'),
            'orbit_hold_s': report.get('orbit_hold_s'),
            'samples': report.get('samples'),
            'final_mass_t': last.get('mass_t'),
            'final_stage': last.get('stage'),
            'stage_events': report.get('stage_events'),
        },
        'provenance': {
            'craft_sha256_prepared': inputs.get('craft_sha256'),
            'craft_sha256_flown': final.get('craft_sha256'),
            'source_save_sha256': inputs.get('source_save_sha256'),
            'environment_sha256': inputs.get('environment_sha256'),
            'source_files_checked': report.get('source_files_checked'),
            'source_files_changed': report.get('source_files_changed'),
            'environment_files_changed': report.get('environment_files_changed'),
        },
        'files': files,
        'caveats': [
            'One flight; it verifies one delivered payload point, not maximum payload or a success rate.',
            'The C3 curve is not calibrated from this point by this command.',
            'The final mass includes remaining propellant; remaining delta-v is a model output, not an orbit reading.',
            'The flown craft differs from the prepared input after the editor fairing rebuild; both hashes are recorded.',
        ],
    }
    (output / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ksp-root', type=Path, required=True)
    parser.add_argument('--folder', required=True)
    parser.add_argument('--output', type=Path, help='Defaults to docs/evidence/<folder>')
    parser.add_argument('--force', action='store_true', help='Overwrite files in an existing bundle')
    args = parser.parse_args()
    output = args.output or (EVIDENCE_ROOT / args.folder)
    manifest = freeze(args.ksp_root, args.folder, output, force=args.force)
    print(json.dumps({'folder': manifest['folder'], 'status': manifest['status'],
                      'files': len(manifest['files']), 'output': str(output.resolve())},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
