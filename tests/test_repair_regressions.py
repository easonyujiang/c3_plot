import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, render_template
from c3_mass.compute_c3_mass import compute_rocket
from server import models, create_app
from server.import_service import _preview_one, confirm_batch
from server.routes_import import bp
from server.routes_missions import _validate_mission
from server.yaml_codec import load_config

ROOT = Path(__file__).resolve().parents[1]


class RepairRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        for name in ['001_initial_schema.sql', '005_import_sources.sql', '006_folder_sibling_uniqueness.sql', '007_rocket_config_evidence.sql']:
            self.db.executescript((ROOT / 'migrations' / name).read_text(encoding='utf-8'))
        self.addCleanup(self.db.close)
        self.patches = [patch('server.models.get_db', return_value=self.db),
                        patch('server.models.ROCKETS_DIR', self.tmp.name)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_script_terminator_is_escaped_in_all_bootstrap_data(self):
        attack = '</script><script>window.auditMarker=1</script>'
        app = Flask(__name__, template_folder=str(ROOT / 'dashboard'))
        with app.app_context():
            body = render_template('index.html', rockets_json=[{'name': attack}],
                                   engines_json=[{'name': attack}],
                                   missions_json=[{'label': attack}], folders_json={'name': attack})
        self.assertNotIn(attack, body)
        self.assertIn('\\u003c/script\\u003e', body)

    def test_index_passes_objects_to_template(self):
        with patch('server.init_db'), patch('server.models.list_rockets', return_value=[]), \
                patch('server.models.list_engines', return_value=[]), \
                patch('server.models.list_standard_missions', return_value=[]), \
                patch('server.models.get_folder_tree', return_value={}):
            response = create_app().test_client().get('/legacy/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('window.__ROCKETS = [];', response.get_data(as_text=True))

    def test_duplicate_folders_rejected_at_root_and_child_levels(self):
        parent = models.create_folder('root')
        self.assertIsNone(models.create_folder('root'))
        child = models.create_folder('child', parent)
        self.assertIsNone(models.create_folder('child', parent))
        self.assertIsNotNone(models.create_folder('child'))
        other = models.create_folder('other')
        with self.assertRaises(sqlite3.IntegrityError):
            models.rename_folder(other, 'root')
        self.db.rollback()
        self.assertTrue(child)

    def test_invalid_yaml_does_not_abort_batch_preview(self):
        for raw in [b'rocket: []', b'rocket: null', b'[]', b'rocket: 12']:
            with self.subTest(raw=raw):
                item = _preview_one('bad.yaml', raw)
                self.assertEqual(item['status'], 'error')

    def test_non_object_confirm_body_returns_400(self):
        app = Flask(__name__)
        app.register_blueprint(bp)
        for route in ['/api/import/vab/confirm', '/api/import/batch/confirm']:
            for body in [[1], 'text', 1, None]:
                with self.subTest(route=route, body=body):
                    self.assertEqual(app.test_client().post(route, json=body).status_code, 400)
        r = app.test_client().post('/api/import/batch/confirm', json={'items': [], 'allow_updates': 'false'})
        self.assertEqual(r.status_code, 400)

    def test_mission_requires_finite_values_and_nonnegative_dv(self):
        for field in ['c3', 'dv']:
            for value in [float('inf'), float('-inf'), float('nan'), True, None]:
                data = {'label': 'mission', 'c3': -16.35, 'dv': 2451}
                data[field] = value
                self.assertIsNotNone(_validate_mission(data))
        self.assertIsNotNone(_validate_mission({'label': 'mission', 'c3': 0, 'dv': -1}))
        self.assertIsNone(_validate_mission({'label': 'GTO', 'c3': -16.35, 'dv': 2451}))

    def config_and_result(self):
        cfg = load_config((ROOT / 'rockets/neutron_i_2630.yaml').read_text(encoding='utf-8'))
        for group in [s['engines'] for s in cfg['stages']]:
            self.db.execute('INSERT OR IGNORE INTO engines(cfg_filename) VALUES(?)', (group['cfg'],))
        self.db.commit()
        return cfg, compute_rocket(cfg)

    def test_source_failure_rolls_back_create_and_yaml(self):
        cfg, result = self.config_and_result()
        with patch('server.models.record_import_source', side_effect=RuntimeError('source failure')):
            with self.assertRaisesRegex(RuntimeError, 'source failure'):
                models.create_rocket(cfg['rocket']['name'], cfg, result, source={'sha256': 'test'})
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM rockets').fetchone()[0], 0)
        self.assertEqual(list(Path(self.tmp.name).glob('*.yaml')), [])

    def test_source_failure_preserves_existing_rocket_and_yaml(self):
        cfg, result = self.config_and_result()
        slug = models.create_rocket(cfg['rocket']['name'], cfg, result)
        before = (Path(self.tmp.name) / (slug + '.yaml')).read_bytes()
        edited = copy.deepcopy(cfg)
        edited['stages'][0]['wet_mass_t'] += 1
        with patch('server.models.record_import_source', side_effect=RuntimeError('source failure')):
            with self.assertRaises(RuntimeError):
                models.update_rocket(slug, edited, result, source={'sha256': 'test'})
        self.assertEqual(models.get_rocket_config(slug)['stages'][0]['wet_mass_t'], cfg['stages'][0]['wet_mass_t'])
        self.assertEqual((Path(self.tmp.name) / (slug + '.yaml')).read_bytes(), before)

    def test_batch_duplicate_source_is_idempotent(self):
        cfg, result = self.config_and_result()
        item = {'file': 'demo.yaml', 'config': cfg, 'source': {'sha256': 'f' * 64, 'filename': 'demo.yaml'}}
        with patch('server.import_service.compute_rocket', return_value=result):
            first = confirm_batch([item])
            second = confirm_batch([item])
        self.assertEqual(first['errors'], 0)
        self.assertEqual(second['results'][0]['action'], 'duplicate')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM rockets').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM import_sources').fetchone()[0], 1)

    def test_failed_commit_restores_yaml_and_sql(self):
        cfg, result = self.config_and_result()
        class FailedCommit:
            def __getattr__(proxy, name):
                return getattr(self.db, name)
            def commit(proxy):
                raise sqlite3.OperationalError('injected commit failure')
        with patch('server.models.get_db', return_value=FailedCommit()):
            with self.assertRaises(sqlite3.OperationalError):
                models.create_rocket(cfg['rocket']['name'], cfg, result, source={'sha256': 'test'})
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM rockets').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM import_sources').fetchone()[0], 0)
        self.assertEqual(list(Path(self.tmp.name).glob('*.yaml')), [])


    def test_audit_detects_drift_missing_and_invalid_files(self):
        from data_tools import audit
        cfg, result = self.config_and_result()
        slug = models.create_rocket(cfg['rocket']['name'], cfg, result)
        root = Path(self.tmp.name)
        (root / 'engines').mkdir()
        import shutil
        for stage in cfg['stages']:
            filename = stage['engines']['cfg']
            shutil.copy2(ROOT / 'engines' / filename, root / 'engines' / filename)
        (root / 'rockets').mkdir()
        import shutil
        shutil.copy2(root / (models.slugify(cfg['rocket']['name']) + '.yaml'), root / 'rockets' / (models.slugify(cfg['rocket']['name']) + '.yaml'))
        disk = sqlite3.connect(root / 'c3_plot.db')
        self.db.backup(disk)
        disk.close()
        path = root / 'rockets' / (slug + '.yaml')
        self.assertEqual(audit(root)['items'][0]['status'], 'matching')
        original = path.read_text(encoding='utf-8')
        path.write_text(original.replace('132.03', '133.03'), encoding='utf-8')
        self.assertEqual(audit(root)['items'][0]['status'], 'different')
        path.unlink()
        self.assertEqual(audit(root)['items'][0]['status'], 'sql_only')
        path.write_text(original, encoding='utf-8')
        (root / 'rockets/extra.yaml').write_text(original, encoding='utf-8')
        (root / 'rockets/bad.yaml').write_text('rocket: []', encoding='utf-8')
        statuses = {i['slug']: i['status'] for i in audit(root)['items']}
        self.assertEqual(statuses['extra'], 'yaml_only')
        self.assertEqual(statuses['bad'], 'invalid')

    def test_recovery_refuses_tampering_and_existing_destination(self):
        from data_tools import backup, restore
        cfg, result = self.config_and_result()
        models.create_rocket(cfg['rocket']['name'], cfg, result)
        root = Path(self.tmp.name)
        (root / 'engines').mkdir()
        import shutil
        for stage in cfg['stages']:
            filename = stage['engines']['cfg']
            shutil.copy2(ROOT / 'engines' / filename, root / 'engines' / filename)
        (root / 'rockets').mkdir()
        import shutil
        shutil.copy2(root / (models.slugify(cfg['rocket']['name']) + '.yaml'), root / 'rockets' / (models.slugify(cfg['rocket']['name']) + '.yaml'))
        disk = sqlite3.connect(root / 'c3_plot.db')
        self.db.backup(disk)
        disk.close()
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / 'backup'
            manifest = backup(root, dest)
            recovered = Path(work) / 'restored'
            self.assertEqual(restore(dest, recovered), manifest['audit'])
            with self.assertRaises(ValueError):
                restore(dest, recovered)
            (dest / 'rockets/neutron_i_2630.yaml').write_text('tampered', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'hash'):
                restore(dest, Path(work) / 'other')

    def test_source_summary_distinguishes_legacy_and_recorded(self):
        cfg, result = self.config_and_result()
        slug = models.create_rocket(cfg['rocket']['name'], cfg, result)
        self.assertEqual(models.get_computed_data(slug)['source_summary']['status'], 'legacy_unknown')
        models.record_import_source(slug, {'sha256': 'test', 'filename': 'demo.yaml'})
        summary = models.get_computed_data(slug)['source_summary']
        self.assertEqual(summary['status'], 'recorded')
        self.assertEqual(summary['model_quality'], 'estimated')
        self.assertEqual(summary['sources'][0]['source_filename'], 'demo.yaml')

    def test_legacy_backup_configuration_read_needs_no_evidence_migration(self):
        cfg, result = self.config_and_result()
        slug = models.create_rocket(cfg['rocket']['name'], cfg, result)
        self.db.execute('DROP TABLE rocket_config_evidence')
        restored = models.get_rocket_config(slug)
        self.assertEqual(restored['rocket'], cfg['rocket'])
        self.assertEqual(restored['calibration'], sorted(cfg['calibration'], key=lambda p:p['payload_t'], reverse=True))
        self.assertEqual(restored['stages'][0]['wet_mass_t'], cfg['stages'][0]['wet_mass_t'])
        self.assertIsNone(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='rocket_config_evidence'").fetchone())


if __name__ == '__main__':
    unittest.main()
