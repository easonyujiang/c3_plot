import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from server.craft_library import craft_path, list_crafts, read_craft
from server.routes_import import bp


class CraftLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / 'saves' / '设计存档' / 'Ships' / 'VAB' / '测试火箭.craft'
        self.path.parent.mkdir(parents=True)
        self.raw = 'ship = 火箭一号\nPART\n{\n part = test_1\n}\n'.encode()
        self.path.write_bytes(self.raw)
        self.craft_id = '设计存档/Ships/VAB/测试火箭.craft'

    def test_saved_craft_discovered_and_changed_version_rejected(self):
        item = list_crafts(self.root)['items'][0]
        self.assertEqual(item['name'], '火箭一号')
        self.assertEqual(item['id'], self.craft_id)
        self.assertEqual(item['sha256'], hashlib.sha256(self.raw).hexdigest())
        self.path.write_bytes(self.raw + b'\n')
        with self.assertRaisesRegex(ValueError, '重新保存'):
            read_craft(self.root, item['id'], item['sha256'])
        self.assertNotEqual(item['sha256'], list_crafts(self.root)['items'][0]['sha256'])

    def test_source_untouched_and_paired_metadata_loaded(self):
        self.path.with_suffix('.loadmeta').write_text('totalMass = 123.45\npartNames = test\n')
        name, raw, metadata, digest = read_craft(self.root, self.craft_id)
        self.assertEqual(name, self.path.name)
        self.assertEqual(raw, self.raw)
        self.assertEqual(metadata['totalMass'], '123.45')
        self.assertEqual(metadata['part_names'], ['test'])
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_traversal_absolute_and_non_vab_paths_rejected(self):
        bad = ['../Ships/VAB/x.craft', 'x/Ships/VAB/../../x.craft',
               '/x/Ships/VAB/x.craft', 'C:/Ships/VAB/x.craft',
               'x\\Ships\\VAB\\x.craft', 'x/Ships/SPH/x.craft',
               'x//Ships/VAB/x.craft', './x/Ships/VAB/x.craft',
               'x/Ships/VAB/x.craft.original', None]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                craft_path(self.root, value)

    def test_test_save_and_backups_not_listed(self):
        self.path.with_suffix('.craft.original').write_bytes(self.raw)
        test = self.root / 'saves' / 'C3MassValidation20261010' / 'Ships' / 'VAB'
        test.mkdir(parents=True)
        (test / 'test.craft').write_bytes(self.raw)
        self.assertEqual(len(list_crafts(self.root)['items']), 1)

    def test_oversize_rejected_before_read(self):
        with patch('server.craft_library.MAX_CRAFT_BYTES', 1):
            self.assertFalse(list_crafts(self.root)['items'])
            with self.assertRaises(ValueError):
                read_craft(self.root, self.craft_id)

    def test_api_version_check_and_preview_passes_metadata(self):
        app = Flask(__name__)
        app.register_blueprint(bp)
        with patch('server.routes_import.configured_ksp_root', return_value=self.root):
            client = app.test_client()
            item = client.get('/api/import/library').get_json()['items'][0]
            with patch('server.routes_import.import_service.preview_craft', return_value={
                'status': 'needs_review', 'config': {'rocket': {'name': 'test'}}
            }) as preview:
                response = client.post('/api/import/library/preview', json=item)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(preview.call_args.args[:2], (self.path.name, self.raw))
                self.path.write_bytes(b'changed')
                self.assertEqual(client.post('/api/import/library/preview', json=item).status_code, 409)
                self.assertEqual(preview.call_count, 1)
