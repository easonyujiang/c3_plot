"""Back up original data, then publish an installed-engine curve locally."""
import json
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flask import Flask
from data_tools import backup, audit, normalize
from server import models
from server.database import close_db
from server.yaml_codec import load_config


def main():
    out = ROOT / 'output/curve_accuracy_20261010'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--curve-dir', type=Path, default=out / 'neutron_i_2630')
    parser.add_argument('--backup-dir', type=Path, default=out / 'original_data_backup')
    parser.add_argument('--receipt', type=Path, default=out / 'applied-curve.json')
    args = parser.parse_args()
    if args.receipt.exists():
        raise ValueError('Existing update receipt preserved; choose a new receipt path')
    curve_dir = args.curve_dir
    evidence = json.loads((curve_dir / 'evidence.json').read_text(encoding='utf-8'))
    data = json.loads((curve_dir / 'curve.json').read_text(encoding='utf-8'))
    backup_dir = args.backup_dir
    if backup_dir.exists():
        raise ValueError('Existing original backup preserved; inspect before repeating this operation')
    report = backup(ROOT, backup_dir)
    if not report['audit']['ok']:
        raise ValueError('Pre-update data audit failed; original backup preserved')
    data['engine_provenance'] = evidence['installed_engines']
    data['calibration_provenance'] = {'status': 'legacy_unknown', 'config_sha256': evidence['config_sha256']}
    data['computation_version'] = 'curve_accuracy_20261010_v1'
    app = Flask(__name__)
    app.teardown_appcontext(close_db)
    with app.app_context():
        cfg = models.get_rocket_config('neutron_i_2630')
        disk = load_config(Path(evidence['config_path']).read_text(encoding='utf-8'))
        if normalize(cfg) != normalize(disk):
            raise ValueError('Curve input no longer matches the current stored rocket')
        models.save_computed_data('neutron_i_2630', data)
    result = {'backup': str(backup_dir.resolve()), 'after': audit(ROOT)}
    if not result['after']['ok']:
        raise ValueError('Post-update audit failed; preserve backup and investigate')
    args.receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
