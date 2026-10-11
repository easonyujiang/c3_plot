"""Record the installed game's editor mass-method IL without running it."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.ksp_dotnet_il import read_dotnet_assembly, methods_named

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ksp-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    path = args.ksp_root / 'KSP_x64_Data/Managed/Assembly-CSharp.dll'
    assembly = read_dotnet_assembly(str(path))
    evidence = {'assembly': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'methods': []}
    for owner, name in [('ShipConstruct', 'GetShipMass'), ('ShipConstruct', 'GetTotalMass'),
                        ('ShipConstruction', 'GetSavePath'),
                        ('Part', 'GetMass'), ('Part', 'GetModuleMass'), ('Part', 'UpdateMass')]:
        evidence['methods'].extend(methods_named(assembly, owner, name))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([{'type': m['type'], 'method': m['method'], 'complete': m['decode_complete']} for m in evidence['methods']]))
