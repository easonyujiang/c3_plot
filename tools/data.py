"""Read-only SQL/YAML audit and verifiable backup/recovery, without app startup."""
import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from flask import Flask, g
from server import models
from server.yaml_codec import load_config
from server.validation import validate_rocket

from tools.paths import ROOT
def open_readonly(root):
    conn = sqlite3.connect('file:' + (root / 'c3_plot.db').as_posix() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def normalize(cfg):
    stages = []
    for s in cfg['stages']:
        stages.append({'wet_mass_t': s['wet_mass_t'], 'dry_mass_t': s['dry_mass_t'],
                       'engine_groups': s.get('engine_groups') or [s['engines']],
                       'multi_ignition': s.get('multi_ignition', False), 'parallel': s.get('parallel', False)})
    refs = {}
    for key, m in cfg.get('reference_missions', {}).items():
        # The solver uses apoapsis when present; c3/dv/payload are derived display values.
        target = {'apoapsis_km': m['apoapsis_km']} if m.get('apoapsis_km') is not None else {'c3': m.get('c3')}
        refs[key] = {'label': m['label'], 'color': m.get('color', '#888888'), **target}
    return {'rocket': cfg['rocket'], 'stages': stages,
            'calibration': sorted(cfg['calibration'], key=lambda p: p['payload_t']), 'reference_missions': refs}


def audit(root):
    conn = open_readonly(root)
    app = Flask(__name__)
    items = []
    with app.app_context():
        g.db = conn
        sql = {r['slug']: models.get_rocket_config(r['slug']) for r in models.list_rockets()}
        paths = {p.stem: p for p in (root / 'rockets').glob('*.yaml')}
        paths.update({p.stem: p for p in (root / 'rockets').glob('*.yml')})
        for slug in sorted(sql.keys() | paths.keys()):
            item = {'slug': slug}
            if slug not in paths:
                item['status'] = 'sql_only'
            else:
                try:
                    cfg = load_config(paths[slug].read_text(encoding='utf-8-sig'))
                    valid, errors = validate_rocket(cfg)
                    if not valid:
                        item.update(status='invalid', errors=errors)
                    elif slug not in sql:
                        item['status'] = 'yaml_only'
                    else:
                        a, b = normalize(sql[slug]), normalize(cfg)
                        item['status'] = 'matching' if a == b else 'different'
                        item['different_sections'] = [k for k in a if a[k] != b[k]]
                except Exception as exc:
                    item.update(status='invalid', errors=[str(exc)])
            items.append(item)
        integrity = [r[0] for r in conn.execute('PRAGMA integrity_check')]
        foreign_keys = [list(r) for r in conn.execute('PRAGMA foreign_key_check')]
        counts = {t: conn.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0]
                  for t in ['rockets', 'stages', 'engines', 'import_sources']}
        unknown = [r[0] for r in conn.execute('SELECT r.slug FROM rockets r WHERE NOT EXISTS (SELECT 1 FROM import_sources s WHERE s.rocket_id=r.id)')]
    conn.close()
    return {'integrity': integrity, 'foreign_key_errors': foreign_keys, 'counts': counts,
            'legacy_unknown': unknown, 'items': items,
            'ok': integrity == ['ok'] and not foreign_keys and all(i['status'] == 'matching' for i in items)}


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and p.name != 'backup_manifest.json'}


def backup(root, dest):
    # Quiesce writers before calling: SQLite snapshot and external YAML need one logical point in time.
    dest.mkdir(parents=True, exist_ok=False)
    source = open_readonly(root)
    target = sqlite3.connect(dest / 'c3_plot.db')
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    for name in ['rockets', 'engines', 'VAB', 'experiments']:
        if (root / name).is_dir():
            shutil.copytree(root / name, dest / name)
    report = audit(dest)
    manifest = {'schema_version': 1, 'files': hashes(dest), 'audit': report}
    (dest / 'backup_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def restore(source, dest):
    manifest = json.loads((source / 'backup_manifest.json').read_text(encoding='utf-8'))
    if hashes(source) != manifest['files']:
        raise ValueError('Backup hash verification failed')
    if dest.exists():
        raise ValueError('Restore destination must not exist; existing data will never be overwritten')
    shutil.copytree(source, dest)
    return audit(dest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('audit')
    for command in ['backup', 'restore']:
        cmd = sub.add_parser(command)
        cmd.add_argument('destination', type=Path)
    args = parser.parse_args()
    if args.command == 'audit':
        report = audit(args.root.resolve())
    elif args.command == 'backup':
        report = backup(args.root.resolve(), args.destination.resolve())
    else:
        report = restore(args.root.resolve(), args.destination.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
