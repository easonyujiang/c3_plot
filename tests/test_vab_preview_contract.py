import io
import unittest
from pathlib import Path
from unittest import mock

from server import create_app, import_service
from server.vab_parser import parse_craft

VALID_CRAFT = Path('VAB/1T_Test.craft').read_bytes()
NO_PART_CRAFT = 'ship = Empty\nversion = 1.0\n'.encode('utf-8')
BAD_CRAFT = b'PART\n{'


def _file_storage(name, data):
    from werkzeug.datastructures import FileStorage
    return FileStorage(stream=io.BytesIO(data), filename=name)


class VabLinkEvidenceTests(unittest.TestCase):
    """Duplicate ``link`` fields must be extracted, order-stable, undeduped."""

    def test_link_extracted_from_real_craft_and_order_stable(self):
        text = VALID_CRAFT.decode('utf-8-sig')
        first = parse_craft(text, '1T_Test.craft')
        second = parse_craft(text, '1T_Test.craft')
        a = [p['connections']['link'] for p in first['parts']]
        b = [p['connections']['link'] for p in second['parts']]
        self.assertEqual(a, b)
        self.assertTrue(any(links for links in a))
        self.assertGreater(sum(len(links) for links in a), 0)

    def test_link_duplicates_preserved_not_deduped(self):
        text = """ship = dup
PART
{
    part = A_1
    link = B_2
    link = C_3
    link = B_2
}
"""
        parsed = parse_craft(text, 'dup.craft')
        links = parsed['parts'][0]['connections']['link']
        self.assertEqual(links, ['B_2', 'C_3', 'B_2'])

    def test_link_and_sym_not_counted_as_extra_parts(self):
        text = """ship = counts
PART
{
    part = A_1
    link = B_2
    sym = A_1
}
PART
{
    part = B_2
    link = A_1
}
"""
        parsed = parse_craft(text, 'counts.craft')
        self.assertEqual(parsed['source']['part_count'], 2)


class VabPreviewServiceTests(unittest.TestCase):
    """Shared single-file/batch craft preview contract."""

    def setUp(self):
        self.app = create_app()
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def test_no_part_single_file_error(self):
        item = import_service.preview_craft('empty.craft', NO_PART_CRAFT)
        self.assertEqual(item['status'], 'error')
        self.assertEqual(item['action'], 'error')
        self.assertEqual(item['error_code'], 'no_parts_found')
        self.assertNotIn('config', item)

    def test_no_part_batch_item_error(self):
        batch = import_service.preview_batch(
            [_file_storage('a.craft', NO_PART_CRAFT)])
        item = batch['items'][0]
        self.assertEqual(item['status'], 'error')
        self.assertEqual(item['action'], 'error')
        self.assertEqual(item['error_code'], 'no_parts_found')
        self.assertEqual(batch['errors'], 1)
        self.assertEqual(batch['review'], 0)
        self.assertEqual(batch['ready'], 0)

    def test_single_and_batch_error_structure_identical(self):
        single = import_service.preview_craft('bad.craft', BAD_CRAFT)
        batch = import_service.preview_batch(
            [_file_storage('bad.craft', BAD_CRAFT)])
        item = batch['items'][0]
        for key in ('error', 'error_code', 'line', 'column', 'node_path'):
            self.assertEqual(single[key], item[key], key)
        self.assertEqual(single['error_code'], 'unexpected_eof')
        self.assertIsNotNone(single['line'])

    def test_valid_craft_status_and_action_fixed(self):
        item = import_service.preview_craft('1T_Test.craft', VALID_CRAFT)
        self.assertEqual(item['status'], 'needs_review')
        self.assertEqual(item['action'], 'review')
        codes = {w['code'] for w in item['structured_warnings']}
        self.assertIn('mass_not_confirmed', codes)
        self.assertIn('stage_structure_unconfirmed', codes)
        self.assertFalse(item['status'] == 'needs_review'
                         and item['action'] == 'create')

    def test_structured_warnings_in_batch_items_and_summary(self):
        batch = import_service.preview_batch(
            [_file_storage('1T_Test.craft', VALID_CRAFT)])
        item = batch['items'][0]
        self.assertTrue(item.get('structured_warnings'))
        self.assertTrue(batch['warnings'])
        self.assertEqual(batch['warnings'][0]['severity'], 'warning')
        self.assertIn('file', batch['warnings'][0])

    def test_encoding_error_stable(self):
        item = import_service.preview_craft('bad.craft', b'\xff\xfe\x00')
        self.assertEqual(item['error_code'], 'invalid_encoding')
        self.assertIsNone(item['line'])

    def test_internal_exception_not_leaked(self):
        with mock.patch('server.import_service.parse_craft',
                        side_effect=RuntimeError('secret stack detail')):
            item = import_service.preview_craft('bad.craft', VALID_CRAFT)
        self.assertEqual(item['error_code'], 'internal_error')
        self.assertNotIn('secret stack detail', item['error'])


class VabPreviewApiTests(unittest.TestCase):
    """HTTP contract of the single-file preview endpoint."""

    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()

    def _post_vab(self, filename, data):
        return self.client.post(
            '/api/import/vab/preview',
            data={'file': (io.BytesIO(data), filename)},
            content_type='multipart/form-data')

    def test_no_part_returns_400_with_unified_body(self):
        response = self._post_vab('empty.craft', NO_PART_CRAFT)
        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertEqual(body['error_code'], 'no_parts_found')
        self.assertEqual(set(body), {'error', 'error_code', 'line',
                                     'column', 'node_path'})
        self.assertNotIn('draft', body)

    def test_parse_error_returns_400_with_location(self):
        response = self._post_vab('bad.craft', BAD_CRAFT)
        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertEqual(body['error_code'], 'unexpected_eof')
        self.assertEqual(body['line'], 2)

    def test_valid_craft_returns_needs_review_and_warnings(self):
        response = self._post_vab('1T_Test.craft', VALID_CRAFT)
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['status'], 'needs_review')
        self.assertEqual(body['action'], 'review')
        self.assertTrue(body['structured_warnings'])
        self.assertTrue(body['draft']['structured_warnings'])

    def test_preview_writes_nothing(self):
        from server import models
        with self.app.app_context():
            before_rockets = models.list_rockets()
        before_files = set(Path('rockets').glob('*.yaml'))
        self._post_vab('1T_Test.craft', VALID_CRAFT)
        self._post_vab('empty.craft', NO_PART_CRAFT)
        self.client.post(
            '/api/import/batch/preview',
            data={'files': [(io.BytesIO(VALID_CRAFT), 'b.craft')]},
            content_type='multipart/form-data')
        with self.app.app_context():
            after_rockets = models.list_rockets()
        after_files = set(Path('rockets').glob('*.yaml'))
        self.assertEqual(after_rockets, before_rockets)
        self.assertEqual(after_files, before_files)


if __name__ == '__main__':
    unittest.main()
