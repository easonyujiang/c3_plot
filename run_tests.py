"""Run unittest with an isolated database and temporary output directory."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent
    os.chdir(root)
    scratch = root / '.tmp'
    scratch.mkdir(exist_ok=True)
    # Individual tests also use tempfile; keep their files inside the workspace.
    tempfile.tempdir = str(scratch)
    os.environ['TEMP'] = os.environ['TMP'] = str(scratch)
    with tempfile.TemporaryDirectory(prefix='c3-tests-', dir=scratch) as temporary:
        temporary = Path(temporary)
        shutil.copytree(root / 'rockets', temporary / 'rockets')
        os.environ['C3_MASS_DB_PATH'] = str(temporary / 'test.db')
        os.environ['C3_MASS_ROCKETS_DIR'] = str(temporary / 'rockets')
        os.environ['C3_MASS_OUTPUT_DIR'] = str(temporary / 'output')
        suite = unittest.defaultTestLoader.discover('tests')
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        print(json.dumps({'run': result.testsRun, 'failures': len(result.failures),
                          'errors': len(result.errors), 'skipped': len(result.skipped)}))
        return 0 if result.wasSuccessful() else 1

if __name__ == '__main__':
    raise SystemExit(main())
