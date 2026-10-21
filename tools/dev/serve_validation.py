"""Serve a disposable database snapshot for non-destructive UI checks."""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

from tools.paths import ROOT

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8878)
    parser.add_argument('--include-validation-crafts', action='store_true')
    args = parser.parse_args()
    if args.include_validation_crafts:
        os.environ['C3_MASS_INCLUDE_VALIDATION_CRAFTS']='1'
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'output'):
        parser.error('Validation output must be under project output/')
    output.mkdir(parents=True, exist_ok=True)
    db = output / 'validation.db'
    if db.exists():
        parser.error('Preserve previous validation database; choose a fresh output directory')
    with sqlite3.connect((ROOT / 'c3_plot.db').as_uri() + '?mode=ro', uri=True) as original:
        with sqlite3.connect(db) as duplicate:
            original.backup(duplicate)
    os.environ['C3_MASS_DB_PATH'] = str(db)
    # Set before importing server: its package initializer imports models and
    # database, which bind these paths at import time.
    os.environ['C3_MASS_ROCKETS_DIR'] = str(output / 'rockets')
    os.environ['C3_MASS_OUTPUT_DIR'] = str(output / 'generated')
    from server import create_app
    app = create_app()
    app.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False)
