"""Create a new isolated replay save; preserve and fingerprint originals."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def prepare(ksp_root, output):
    source = ksp_root / 'saves/RocketMaking'
    target = ksp_root / 'saves/C3MassValidation20261010'
    if target.exists():
        raise ValueError('Preserve existing validation save; inspect before reuse')
    manifest = [{'path': str(p.resolve()), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in source.rglob('*') if p.is_file()]
    shutil.copytree(source, target)
    expected = hashlib.sha256((target / 'quicksave.sfs').read_bytes()).hexdigest()
    plugin = ksp_root / 'GameData/C3MassValidation'
    plugin.mkdir(exist_ok=True)
    arm = plugin / 'replay.arm'
    with arm.open('x', encoding='ascii') as stream:
        stream.write(expected)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'original-save-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'replay-plan.json').write_text(json.dumps({'test_save': str(target.resolve()),
        'quicksave_sha256': expected, 'mode': 'passive_existing_flight_replay',
        'original_save_files': len(manifest)}, ensure_ascii=False, indent=2), encoding='utf-8')
    return len(manifest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ksp-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print('Original files preserved:', prepare(args.ksp_root, args.output))
