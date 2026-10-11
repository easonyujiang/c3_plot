"""Package a clean, tagged local release without user DB or dependency folders."""
import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin', type=Path, required=True)
    args = parser.parse_args()
    version = (ROOT / 'VERSION').read_text().strip()
    if git('status', '--porcelain').strip():
        raise SystemExit('Commit all source changes before packaging.')
    commit = git('rev-parse', 'HEAD').decode().strip()
    if git('rev-parse', f'v{version}^{{commit}}').decode().strip() != commit:
        raise SystemExit('Release tag must point to HEAD.')
    plugin = args.plugin.resolve(strict=True)
    if plugin.name != 'C3MassTelemetry.dll':
        raise SystemExit('Only the passive telemetry plugin belongs in a release.')
    out = ROOT / 'output' / 'release_20261011'
    out.mkdir(parents=True, exist_ok=True)
    prefix = f'c3-mass-{version}'
    source_zip = out / f'{prefix}-source.zip'
    subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=zip',
                    f'--prefix={prefix}/', f'--output={source_zip}', f'v{version}'], check=True)
    tracked = [ROOT / p.decode('utf-8') for p in git('ls-files', '-z').split(b'\0') if p]
    runtime_dirs = {'server', 'dashboard', 'static', 'engines', 'rockets', 'migrations', 'docs', 'licenses', 'game_plugin'}
    files = []
    for file in tracked:
        relative = file.relative_to(ROOT)
        if (len(relative.parts) == 1 and file.suffix in {'.py', '.txt', '.md', '.bat', '.cmd'}) or relative.name == 'VERSION':
            files.append(file)
        elif relative.parts[0] in runtime_dirs:
            files.append(file)
        elif relative.as_posix().startswith('frontend/dist/') or relative.as_posix() == 'tools/build_telemetry.ps1':
            files.append(file)
    hashes = {}
    app_zip = out / f'{prefix}-windows.zip'
    with zipfile.ZipFile(app_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file in sorted(files):
            name = file.relative_to(ROOT).as_posix()
            data = file.read_bytes()
            hashes[name] = hashlib.sha256(data).hexdigest()
            archive.writestr(f'{prefix}/{name}', data)
        name = 'GameData/C3MassTelemetry/Plugins/C3MassTelemetry.dll'
        data = plugin.read_bytes()
        hashes[name] = hashlib.sha256(data).hexdigest()
        archive.writestr(f'{prefix}/{name}', data)
        manifest = {'version': version, 'git_commit': commit, 'files': hashes}
        archive.writestr(f'{prefix}/RELEASE_MANIFEST.json', json.dumps(manifest, indent=2))
    result = {'version': version, 'git_commit': commit, 'files': hashes, 'archives': {}}
    for archive in (source_zip, app_zip):
        result['archives'][archive.name] = {'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'bytes': archive.stat().st_size}
    (out / 'release-manifest.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'version': version, 'git_commit': commit, 'archives': result['archives']}, indent=2))

if __name__ == '__main__':
    main()
