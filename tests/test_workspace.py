"""Check the shipped React workspace and Flask asset routing together."""
import re
import unittest
from pathlib import Path

from server import create_app


class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = create_app().test_client()

    def test_home_and_workspace_serve_the_same_built_application(self):
        home = self.client.get('/')
        workspace = self.client.get('/workspace/')
        self.assertEqual(home.status_code, 200)
        self.assertEqual(home.data, workspace.data)
        self.assertIn('id="root"', home.get_data(as_text=True))
        self.assertRegex(home.get_data(as_text=True), r'<title>[^<]+</title>')

    def test_shipped_assets_exist_and_are_served(self):
        page = self.client.get('/').get_data(as_text=True)
        assets = re.findall(r'(?:src|href)="([^"]+)"', page)
        self.assertTrue(any('/workspace/assets/' in asset for asset in assets))
        for asset in assets + ['/static/plotly.min.js']:
            with self.subTest(asset=asset):
                self.assertEqual(self.client.get(asset).status_code, 200)

    def test_retired_dashboard_routes_are_not_served(self):
        for route in ('/legacy/', '/js/main-v2.js', '/css/dashboard.css'):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 404)

    def test_workspace_cannot_serve_project_sources(self):
        for route in ('/workspace/../server/config.py', '/workspace/assets/missing.js'):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 404)

    def test_frontend_text_files_are_valid_utf8(self):
        frontend = Path(__file__).resolve().parents[1] / 'frontend'
        for directory in (frontend / 'src', frontend / 'dist'):
            for path in directory.rglob('*'):
                if path.is_file() and path.suffix in {'.html', '.tsx', '.ts', '.css', '.js'}:
                    with self.subTest(path=path.name):
                        self.assertNotIn('\ufffd', path.read_text(encoding='utf-8'))
