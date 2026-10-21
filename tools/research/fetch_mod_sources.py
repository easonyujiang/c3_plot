"""Pin and download selected mod sources without changing the game install."""
import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

REPOS = [
    ('shadowmage45/SSTULabs', '0.12.50.162', ['SSTUModularEngineCluster.cs']),
    ('KSP-RO/RealFuels', 'v15.14.0.0', ['ModuleEngineConfigs.cs', 'ModuleEnginesRF.cs']),
    ('MuMech/MechJeb2', '6f1f2a34ae035073d22185f53e23334ee59845d7',
     ['MechJebModuleAscentAutopilot.cs', 'MechJebModuleAscentPVG.cs',
      'MechJebModuleThrustController.cs', 'FuelFlowSimulation.cs']),
    ('KSP-RO/SolverEngines', 'v3.14.0', ['EngineModule.cs', 'EngineSolver.cs']),
]


def get(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'C3-Mass-source-audit'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def fetch(output):
    output.mkdir(parents=True, exist_ok=True)
    manifest = []
    for repo, ref, names in REPOS:
        entry = {'repo': repo, 'requested_ref': ref, 'files': []}
        try:
            commit = json.loads(get(f'https://api.github.com/repos/{repo}/commits/{ref}'))['sha']
            entry['commit'] = commit
            tree = json.loads(get(f'https://api.github.com/repos/{repo}/git/trees/{commit}?recursive=1'))
            if tree.get('truncated'):
                raise ValueError('GitHub returned a truncated source tree')
            for node in tree['tree']:
                path = node['path']
                if Path(path).name not in names:
                    continue
                raw = get(f'https://raw.githubusercontent.com/{repo}/{commit}/{path}')
                target = output / repo.replace('/', '_') / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                entry['files'].append({'path': path, 'sha256': hashlib.sha256(raw).hexdigest(),
                                       'url': f'https://github.com/{repo}/blob/{commit}/{path}'})
            if not entry['files']:
                entry['error'] = 'No requested source files found at this ref'
        except Exception as error:
            entry['error'] = str(error)
        manifest.append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
        (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    fetch(args.output)
