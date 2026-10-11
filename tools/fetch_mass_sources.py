"""Pinned primary sources for the actual installation's dynamic mass audit.

Downloads only selected code into a new output directory; copies installed
SMURFF patches as evidence, without changing the game or old source archive.
"""
import argparse
import hashlib
import json
import sys
import io
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.fetch_mod_sources import get

TARGETS = [
    ('shadowmage45/SSTULabs', '0.12.50.162',
     ['SSTUModularEngineCluster.cs', 'SSTUModularFuelTank.cs',
      'SSTUVolumeContainer.cs', 'SSTUFuelSelection.cs', 'ContainerDefinition.cs',
      'SSTUModularPart.cs', 'SSTUModularTank.cs']),
    ('KSP-RO/RealFuels', 'v15.14.0.0',
     ['ModuleEngineConfigs.cs', 'ModuleFuelTanks.cs', 'ModuleFuelTanksRF.cs',
      'FuelTank.cs', 'RFSettings.cs']),
    ('KSP-RO/ProceduralParts', 'v2.8.0.0',
     ['ProceduralPart.cs', 'TankContentSwitcher.cs']),
    ('KSPModStewards/B9PartSwitch', '2.21.0.4',
     ['ModuleB9PartSwitch.cs', 'PartSubtype.cs', 'TankType.cs']),
]

PINNED_COMMITS = {
    'shadowmage45/SSTULabs': 'c90a2ffb9ca065a8e9fc577eb50c49b3076be0bc',
    'KSP-RO/RealFuels': 'f29cde5b38d1f9a4268f9f1447bf21ea65abecf5',
    'KSP-RO/ProceduralParts': 'c97060685b32dbc0c604b67f9ec5dd85e70d8474',
    'KSPModStewards/B9PartSwitch': '2742ba3d3198742fe98a496fd33d089787d7adbb',
}


def fetch(output, ksp_root):
    output.mkdir(parents=True, exist_ok=True)
    entries = []
    for repo, ref, names in TARGETS:
        entry = {'repo': repo, 'requested_ref': ref, 'files': []}
        try:
            commit = PINNED_COMMITS[repo]
            entry['commit'] = commit
            archive = zipfile.ZipFile(io.BytesIO(get(f'https://codeload.github.com/{repo}/zip/{commit}')))
            for member in archive.namelist():
                path = member.partition('/')[2]
                if Path(path).name not in names:
                    continue
                # Never extract arbitrary archive paths.
                if '..' in Path(path).parts or Path(path).is_absolute():
                    raise ValueError('Unsafe archive path')
                raw = archive.read(member)
                target = output / repo.replace('/', '_') / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                entry['files'].append({'path': path, 'sha256': hashlib.sha256(raw).hexdigest(),
                                      'url': f'https://github.com/{repo}/blob/{commit}/{path}'})
            entry['not_found'] = sorted(set(names) - {Path(f['path']).name for f in entry['files']})
            if not entry['files']:
                entry['error'] = 'No requested sources at installed version ref'
        except Exception as error:
            entry['error'] = str(error)
        entries.append(entry)
        (output / 'manifest.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'repo': repo, 'commit': entry.get('commit'),
                          'files': len(entry['files']), 'error': entry.get('error')}), flush=True)
    patches = []
    for path in sorted((ksp_root / 'GameData' / 'SMURFF').rglob('*.cfg')):
        raw = path.read_bytes()
        relative = path.relative_to(ksp_root / 'GameData')
        target = output / 'installed_patches' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        patches.append({'path': str(path), 'copy': str(target),
                        'sha256': hashlib.sha256(raw).hexdigest()})
    (output / 'installed-patches.json').write_text(json.dumps(patches, indent=2), encoding='utf-8')
    return entries


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--ksp-root', required=True, type=Path)
    args = parser.parse_args()
    fetch(args.output, args.ksp_root)
