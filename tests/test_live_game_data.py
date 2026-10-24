"""Live game inputs are read in place. No archive, calibration or fixed fleet counts."""
import hashlib
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from c3_mass.game_data import craft_files, ksp_root


class EmptyStartupTests(unittest.TestCase):
    def test_initialization_creates_schema_without_any_business_rows(self):
        from flask import Flask
        from server.database import init_db
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / 'empty.db')
            with patch('server.database.DB_PATH', db_path):
                init_db(Flask(__name__))
                init_db(Flask(__name__))
            with closing(sqlite3.connect(db_path)) as connection:
                tables = [row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                self.assertTrue(tables)
                for table in tables:
                    self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0], 0, table)


class LiveGameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = ksp_root()
        except FileNotFoundError as error:
            raise unittest.SkipTest(str(error))

    def test_saved_crafts_parse_without_modifying_source(self):
        from server.vab_parser import parse_craft
        paths = craft_files()
        if not paths:
            self.skipTest('KSP saves 中暂无已保存的 VAB 火箭')
        for path in paths:
            with self.subTest(craft=str(path.relative_to(self.root))):
                raw = path.read_bytes()
                before = hashlib.sha256(raw).hexdigest()
                draft = parse_craft(raw.decode('utf-8-sig'), path.name)
                self.assertGreater(len(draft['parts']), 0)
                self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_live_craft_library_stays_inside_game_directory(self):
        from server.craft_library import craft_path, list_crafts
        library = list_crafts(self.root)
        for item in library['items']:
            path = craft_path(self.root, item['id'])
            self.assertTrue(path.is_relative_to(self.root))
            self.assertEqual(item['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
