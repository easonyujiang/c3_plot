"""Installation diagnostics must work without touching live storage."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from c3_mass.doctor import diagnose


class FoundationDiagnosticsTests(unittest.TestCase):
    def test_missing_build_is_reported_without_initializing_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {'C3_MASS_KSP_ROOT': ''}):
                checks = diagnose(root)
            frontend = next(check for check in checks if check['name'] == 'Frontend')
            self.assertFalse(frontend['ok'])
            self.assertTrue(frontend['required'])
            self.assertEqual(list(root.iterdir()), [])

    def test_game_is_optional_and_local_build_is_recognized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'frontend/dist').mkdir(parents=True)
            (root / 'frontend/dist/index.html').write_text('<html></html>')
            with patch.dict(os.environ, {'C3_MASS_KSP_ROOT': str(root / 'absent')}):
                checks = diagnose(root)
            self.assertTrue(next(check for check in checks if check['name'] == 'Frontend')['ok'])
            game = next(check for check in checks if check['name'] == 'KSP')
            self.assertFalse(game['ok'])
            self.assertFalse(game['required'])


class RuntimeManifestTests(unittest.TestCase):
    def test_new_launchers_and_install_inputs_are_packaged(self):
        from tools.release.package_release import runtime_files
        inputs = [Path(path) for path in ('c3', 'pyproject.toml', 'uv.lock', '.env.example',
                  'scripts/setup', 'scripts/setup.ps1', 'frontend/package.json',
                  'frontend/src/main.tsx', 'c3_mass/__main__.py', 'c3_plot.db', '.env')]
        selected = runtime_files(inputs)
        self.assertEqual(selected, inputs[:-2])
