"""
Static integrity tests for the dashboard frontend (phase 6B.1R).

Guards against a repeat of the encoding-corruption incident that destroyed
dashboard/index.html and dashboard/js/app.js:

1. Every text file under dashboard/ must decode as strict UTF-8.
2. No Unicode replacement characters (U+FFFD) or private-use mojibake.
3. No broken closing-tag patterns like ``?/button>`` (bytes lost to ``?``).
4. No known mojibake glyphs (閳 閸 娴 鐎 璺 ...) anywhere in the dashboard.
5. index.html must contain a well-formed ``<title>...</title>``.
6. All program contracts (ids / data attributes) must still exist.
7. Non-void tags in index.html must be properly closed (stack checker).
8. GET / must serve the dashboard shell with a valid title.
9. Every dashboard/js/*.js must pass a syntax check (Node, if available).
"""
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path

from server import create_app

BASE = Path(__file__).resolve().parent.parent
DASHBOARD = BASE / 'dashboard'
JS_DIR = DASHBOARD / 'js'
INDEX_HTML = DASHBOARD / 'index.html'

TEXT_SUFFIXES = {'.html', '.js', '.css'}

# Broken closing-tag patterns produced when the corrupting conversion
# replaced a byte with '?' before the tag text.
BROKEN_TAG_PATTERNS = [
    r'\?/title>',
    r'\?/button>',
    r'\?/span>',
    r'\?/label>',
    r'\?/h1>',
    r'\?/h2>',
    r'\?/h3>',
    r'\?/h4>',
]

# Character-level mojibake markers from the incident (GBK bytes read as UTF-8).
MOJIBAKE_CHARS = '閳閸娴鐎璺'
# Private-use-area code points also appeared in the corrupted files.
PUA_RE = re.compile('[\ue000-\uf8ff]')
REPLACEMENT_RE = re.compile('\ufffd')

# Non-void elements per the HTML5 spec (void elements are never closed).
VOID_ELEMENTS = {
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
    'link', 'meta', 'param', 'source', 'track', 'wbr',
}
# Elements whose content must not be treated as markup.
RAW_TEXT_ELEMENTS = {'script', 'style', 'textarea', 'pre'}

REQUIRED_IDS = [
    'app-sidebar',
    'cp-tabs',
    'view-planner',
    'mo-editor',
    'mo-import',
    'oc-run',
    'im-vab-btn',
]


def text_files():
    for path in sorted(DASHBOARD.rglob('*')):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            yield path


class TagBalanceParser(HTMLParser):
    """Small stack checker: every start tag must see its matching end tag."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag in VOID_ELEMENTS:
            return
        self.stack.append((tag, self.getpos()[0]))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if not self.stack:
            self.errors.append(f'stray </{tag}> at line {self.getpos()[0]}')
            return
        open_tag, open_line = self.stack.pop()
        if open_tag != tag:
            self.errors.append(
                f'mismatch: <{open_tag}> opened at line {open_line} '
                f'closed by </{tag}> at line {self.getpos()[0]}'
            )

    def close(self):
        super().close()
        for tag, line in self.stack:
            self.errors.append(f'<{tag}> opened at line {line} never closed')


class DashboardStaticIntegrityTests(unittest.TestCase):
    maxDiff = None

    def _read(self, path):
        with open(path, 'rb') as fh:
            return fh.read()

    # ------------------------------------------------------------------ #
    # 1. UTF-8 strict decode
    # ------------------------------------------------------------------ #
    def test_all_dashboard_text_files_decode_as_strict_utf8(self):
        failures = []
        for path in text_files():
            try:
                self._read(path).decode('utf-8')
            except UnicodeDecodeError as exc:
                failures.append(f'{path.relative_to(BASE)}: {exc}')
        self.assertEqual([], failures, 'non-UTF-8 file(s) in dashboard/')

    # ------------------------------------------------------------------ #
    # 2. No replacement / private-use characters
    # ------------------------------------------------------------------ #
    def test_no_replacement_or_pua_characters(self):
        bad = []
        for path in text_files():
            text = self._read(path).decode('utf-8')
            for match in REPLACEMENT_RE.finditer(text):
                bad.append(f'{path.relative_to(BASE)}: U+FFFD at char {match.start()}')
            for match in PUA_RE.finditer(text):
                bad.append(f'{path.relative_to(BASE)}: PUA char at char {match.start()}')
        self.assertEqual([], bad)

    # ------------------------------------------------------------------ #
    # 3. No broken closing-tag patterns
    # ------------------------------------------------------------------ #
    def test_no_broken_closing_tag_patterns(self):
        bad = []
        for path in text_files():
            text = self._read(path).decode('utf-8')
            for pattern in BROKEN_TAG_PATTERNS:
                for match in re.finditer(pattern, text):
                    line = text.count('\n', 0, match.start()) + 1
                    bad.append(f'{path.relative_to(BASE)}:{line}: {pattern}')
        self.assertEqual([], bad)

    # ------------------------------------------------------------------ #
    # 4. No mojibake glyphs
    # ------------------------------------------------------------------ #
    def test_no_mojibake_glyphs(self):
        bad = []
        for path in text_files():
            text = self._read(path).decode('utf-8')
            for ch in MOJIBAKE_CHARS:
                if ch in text:
                    line = text.count('\n', 0, text.index(ch)) + 1
                    bad.append(f'{path.relative_to(BASE)}:{line}: {ch}')
        self.assertEqual([], bad)

    # ------------------------------------------------------------------ #
    # 5. Valid <title>...</title>
    # ------------------------------------------------------------------ #
    def test_index_has_valid_title(self):
        text = INDEX_HTML.read_text(encoding='utf-8')
        match = re.search(r'<title[^>]*>(.*?)</title>', text, re.IGNORECASE | re.DOTALL)
        self.assertIsNotNone(match, 'no <title>...</title> in index.html')
        title = match.group(1).strip()
        self.assertNotEqual('', title, '<title> is empty')
        self.assertNotIn('?', title, '<title> contains corrupt characters')

    # ------------------------------------------------------------------ #
    # 6. Program contracts still present
    # ------------------------------------------------------------------ #
    def test_key_contract_elements_exist(self):
        text = INDEX_HTML.read_text(encoding='utf-8')
        missing = [elem_id for elem_id in REQUIRED_IDS if f'id="{elem_id}"' not in text]
        self.assertEqual([], missing, f'missing id= elements in index.html: {missing}')

    def test_critical_data_attributes_exist(self):
        text = INDEX_HTML.read_text(encoding='utf-8')
        for attr in [
            'data-nav="home"', 'data-nav="planner"', 'data-nav="query"',
            'data-action="new-rocket"', 'data-action="import"',
            'data-action="new-folder"', 'data-action="toggle-theme"',
            'data-t="overview"', 'data-t="charts"', 'data-t="missions"', 'data-t="data"',
            'data-planner="find"', 'data-planner="orbit"',
            'data-orbit-preset="iss"', 'data-orbit-preset="sso"',
            'data-orbit-preset="gto"', 'data-orbit-preset="geo"',
            'data-ed-action="add-stage"', 'data-ed-action="add-cal"',
            'data-ed-action="add-mission"', 'data-ed-action="save"',
            'data-ed-action="cancel"', 'data-q="search"', 'data-q="reset"',
        ]:
            self.assertIn(attr, text, f'missing {attr} in index.html')

    def test_planner_orbit_presets_restored(self):
        text = INDEX_HTML.read_text(encoding='utf-8')
        for preset in [
            'ISS · 408 km · 51.6°',
            'SSO · 600 km · 97.8°',
            'GTO · 35786 km · 28.5°',
            'GEO · 35786 km · 0°',
        ]:
            self.assertIn(preset, text, f'missing orbit preset: {preset}')

    def test_planner_delivery_and_strategy_options_restored(self):
        text = INDEX_HTML.read_text(encoding='utf-8')
        for option in [
            '目标圆轨道（含圆化）',
            '转移轨道注入（不圆化）',
            '合并圆化与变轨（combined）',
            '分开圆化与变轨（separate）',
        ]:
            self.assertIn(option, text, f'missing option: {option}')

    def test_entry_point_and_plotly_references_present(self):
        text = INDEX_HTML.read_text(encoding='utf-8')
        self.assertIn('src="/static/plotly.min.js"', text)
        self.assertIn('src="/js/main-v2.js', text)
        self.assertIn('rockets_json', text)
        self.assertIn('engines_json', text)
        self.assertIn('missions_json', text)
        self.assertIn('folders_json', text)

    # ------------------------------------------------------------------ #
    # 7. Tag balance checker
    # ------------------------------------------------------------------ #
    def test_index_html_tags_are_balanced(self):
        text = INDEX_HTML.read_text(encoding='utf-8')
        parser = TagBalanceParser()
        parser.feed(text)
        parser.close()
        self.assertEqual([], parser.errors)

    # ------------------------------------------------------------------ #
    # 8. Default component shell and preserved legacy shell
    # ------------------------------------------------------------------ #
    def test_index_route_serves_dashboard_shell(self):
        app = create_app()
        client = app.test_client()
        response = client.get('/')
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        match = re.search(r'<title[^>]*>(.*?)</title>', body, re.IGNORECASE | re.DOTALL)
        self.assertIsNotNone(match, 'GET / response has no <title>')
        self.assertIn('id="root"', body)
        self.assertIn('/workspace/assets/', body)
        legacy = client.get('/legacy/')
        self.assertEqual(legacy.status_code, 200)
        body = legacy.get_data(as_text=True)
        self.assertIn('id="app-sidebar"', body)
        self.assertIn('id="cp-tabs"', body)
        self.assertIn('id="view-planner"', body)
        self.assertIn('id="mo-editor"', body)
        self.assertIn('id="mo-import"', body)
        self.assertNotIn('?/', body)

    # ------------------------------------------------------------------ #
    # 9. JavaScript syntax check (Node, if available)
    # ------------------------------------------------------------------ #
    def test_js_files_pass_syntax_check(self):
        node = shutil.which('node') or shutil.which('nodejs')
        if not node:
            self.skipTest('Node.js not available; other integrity checks still run')
        failures = []
        for path in sorted(JS_DIR.glob('*.js')):
            proc = subprocess.run(
                [node, '--check', str(path)],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                failures.append(f'{path.name}:\n{proc.stderr.strip()}')
        self.assertEqual([], failures)


if __name__ == '__main__':
    unittest.main()
