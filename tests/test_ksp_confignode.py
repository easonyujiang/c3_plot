import unittest
from pathlib import Path

from server.ksp_confignode import (
    MAX_DEPTH,
    MAX_NODES,
    ConfigNode,
    CraftParseError,
    parse,
)

FIXTURES = Path(__file__).parent / 'fixtures'


class ConfigNodeStructureTests(unittest.TestCase):
    def test_nested_structure_navigation(self):
        root = parse("""
ship = Test
PART
{
    part = Probe_123
    MODULE
    {
        name = ModuleEnginesFX
        PROPELLANT
        {
            name = LiquidFuel
            ratio = 1
        }
    }
    MODULE
    {
        name = ModuleFuelTanks
        TANK
        {
            name = Oxidizer
            amount = 50
        }
    }
}
""")
        self.assertEqual(root.name, '')
        self.assertEqual(root.value('ship'), 'Test')
        parts = root.children_named('PART')
        self.assertEqual(len(parts), 1)
        part = parts[0]
        self.assertEqual(part.value('part'), 'Probe_123')
        self.assertEqual([m.value('name')
                          for m in part.children_named('MODULE')],
                         ['ModuleEnginesFX', 'ModuleFuelTanks'])
        engine = part.children_named('MODULE')[0]
        prop = engine.child('PROPELLANT')
        self.assertEqual(prop.value('name'), 'LiquidFuel')
        self.assertEqual(prop.value('ratio'), '1')
        tank_module = part.children_named('MODULE')[1]
        tanks = tank_module.children_named('TANK')
        self.assertEqual(len(tanks), 1)
        self.assertEqual(tanks[0].value('amount'), '50')

    def test_duplicate_keys_and_children_preserved(self):
        root = parse("""
PART
{
    part = Pod_1
    attN = a_1
    attN = b_2
    sym = Pod_2
    sym = Pod_3
    MODULE
    {
        name = ModuleDecouple
    }
    MODULE
    {
        name = ModuleDecouple
    }
}
""")
        part = root.child('PART')
        self.assertEqual(part.values_named('attN'), ['a_1', 'b_2'])
        self.assertEqual(part.values_named('sym'), ['Pod_2', 'Pod_3'])
        self.assertEqual(len(part.children_named('MODULE')), 2)
        self.assertEqual([m.value('name')
                          for m in part.children_named('MODULE')],
                         ['ModuleDecouple', 'ModuleDecouple'])

    def test_bom_crlf_comments_and_indentation(self):
        text = ('\ufeff// header comment\r\n'
                'ship = BOM Test\r\n'
                '\r\n'
                'PART\r\n'
                '{\r\n'
                '\tpart = Engine_7\r\n'
                '\tistg = 1 // inline comment\r\n'
                '\tMODULE\r\n'
                '\t{\r\n'
                '\t\tname = ModuleEngines\r\n'
                '\t}\r\n'
                '}\r\n')
        root = parse(text)
        self.assertEqual(root.value('ship'), 'BOM Test')
        part = root.child('PART')
        self.assertEqual(part.value('part'), 'Engine_7')
        self.assertEqual(part.value('istg'), '1')
        self.assertEqual(part.child('MODULE').value('name'), 'ModuleEngines')
        # start_line survives the BOM + comment lines
        self.assertEqual(part.start_line, 4)
        self.assertEqual(part.child('MODULE').start_line, 8)

    def test_values_keep_spaces_commas_and_equals_tail(self):
        root = parse("""
size = 6.53,38.53,5.66
description = some text with spaces
rot = 0,0,0,0
url = TestMod/Parts/thing v2
""")
        self.assertEqual(root.value('size'), '6.53,38.53,5.66')
        self.assertEqual(root.value('description'), 'some text with spaces')
        self.assertEqual(root.value('rot'), '0,0,0,0')
        self.assertEqual(root.value('url'), 'TestMod/Parts/thing v2')

    def test_inline_empty_nodes_and_multiple_nodes_per_line(self):
        root = parse('PART { } PART { }')
        parts = root.children_named('PART')
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0].children, [])
        self.assertEqual(parts[1].children, [])


class ConfigNodeErrorTests(unittest.TestCase):
    def test_missing_close_brace(self):
        with self.assertRaises(CraftParseError) as ctx:
            parse('PART\n{\n\tpart = A\n')
        self.assertEqual(ctx.exception.code, 'unexpected_eof')
        self.assertIsNotNone(ctx.exception.line)

    def test_extra_close_brace(self):
        with self.assertRaises(CraftParseError) as ctx:
            parse('PART\n{\n}\n}\n')
        self.assertEqual(ctx.exception.code, 'unexpected_close')
        self.assertEqual(ctx.exception.line, 4)

    def test_missing_node_name(self):
        with self.assertRaises(CraftParseError) as ctx:
            parse('PART\n{\n\t{\n\t}\n}\n')
        self.assertEqual(ctx.exception.code, 'missing_node_name')

    def test_invalid_assignment(self):
        with self.assertRaises(CraftParseError) as ctx:
            parse('PART\n{\n\t= nokey\n}\n')
        self.assertEqual(ctx.exception.code, 'invalid_assignment')

    def test_depth_limit(self):
        text = 'N0\n{\n' + ''.join(
            f'\t{"\t" * i}N{i + 1}\n\t{"\t" * i}{{\n'
            for i in range(MAX_DEPTH + 2))
        with self.assertRaises(CraftParseError) as ctx:
            parse(text)
        self.assertEqual(ctx.exception.code, 'depth_exceeded')

    def test_node_limit(self):
        body = 'N\n{\n}\n' * (MAX_NODES + 10)
        with self.assertRaises(CraftParseError) as ctx:
            parse(body)
        self.assertEqual(ctx.exception.code, 'node_limit_exceeded')

    def test_input_size_limit(self):
        import server.ksp_confignode as module
        original = module.MAX_INPUT_CHARS
        module.MAX_INPUT_CHARS = 16
        try:
            with self.assertRaises(CraftParseError) as ctx:
                parse('part = aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
            self.assertEqual(ctx.exception.code, 'input_too_large')
        finally:
            module.MAX_INPUT_CHARS = original

    def test_parse_error_fields_are_stable(self):
        with self.assertRaises(CraftParseError) as exc:
            parse('A { B { } }\n}')  # extra close brace
        error = exc.exception
        self.assertIn(error.code, (
            'unexpected_eof', 'unexpected_close', 'missing_node_name',
            'invalid_assignment', 'depth_exceeded', 'node_limit_exceeded',
            'input_too_large'))
        self.assertIsInstance(error.message, str)
        self.assertIsInstance(error.line, int)


class ConfigNodeDeclarationTests(unittest.TestCase):
    """Node-declaration state machine: a name only allows whitespace,
    comments and ``{`` afterwards."""

    def test_dangling_node_name_at_eof(self):
        with self.assertRaises(CraftParseError) as ctx:
            parse('PART\n')
        error = ctx.exception
        self.assertEqual(error.code, 'dangling_node_name')
        self.assertEqual(error.line, 1)
        self.assertEqual(error.column, 1)
        self.assertEqual(error.node_path, '')

    def test_dangling_node_name_inside_node(self):
        with self.assertRaises(CraftParseError) as ctx:
            parse('PART\n{\n\tMODULE\n')
        self.assertEqual(ctx.exception.code, 'dangling_node_name')
        self.assertEqual(ctx.exception.line, 3)

    def test_dangling_node_name_before_close(self):
        with self.assertRaises(CraftParseError) as ctx:
            parse('PART\n{\nMODULE\n}\n')
        self.assertEqual(ctx.exception.code, 'dangling_node_name')
        self.assertEqual(ctx.exception.line, 3)

    def test_node_name_then_assignment(self):
        with self.assertRaises(CraftParseError) as ctx:
            parse('PART\nship = X\n{\n}\n')
        error = ctx.exception
        self.assertEqual(error.code, 'invalid_node_declaration')
        self.assertEqual(error.line, 2)
        self.assertEqual(error.column, 1)

    def test_node_name_then_another_name(self):
        with self.assertRaises(CraftParseError) as ctx:
            parse('A\nB\n{\n}\n')
        self.assertEqual(ctx.exception.code, 'invalid_node_declaration')
        self.assertEqual(ctx.exception.line, 2)

    def test_node_name_then_close_then_open(self):
        # PART 后紧跟 }（悬空），随后 { 会报 missing_node_name；先报悬空
        with self.assertRaises(CraftParseError) as ctx:
            parse('PART\n}\n{\n')
        self.assertEqual(ctx.exception.code, 'dangling_node_name')

    def test_comment_and_blank_lines_allowed_between_name_and_brace(self):
        root = parse('PART // name comment\n\n\t{ }\n')
        self.assertEqual(root.child('PART').name, 'PART')

    def test_same_line_empty_nodes_still_work(self):
        root = parse('PART { } MODULE { }')
        self.assertEqual(len(root.children), 2)


class ConfigNodeCommentTests(unittest.TestCase):
    """``//`` is only a comment in a legal position; it must not break
    ordinary values such as URLs."""

    def test_http_url_not_truncated(self):
        root = parse('url = http://example/path\n')
        self.assertEqual(root.value('url'), 'http://example/path')

    def test_https_url_with_double_slash_not_truncated(self):
        root = parse('url2 = https://x.org//a\n')
        self.assertEqual(root.value('url2'), 'https://x.org//a')

    def test_inline_comment_after_whitespace_still_works(self):
        root = parse('istg = 1 // inline comment\nx = a\n')
        self.assertEqual(root.value('istg'), '1')
        self.assertEqual(root.value('x'), 'a')

    def test_mixed_url_and_comment_on_one_line(self):
        root = parse('url = http://a/b  // note\n')
        self.assertEqual(root.value('url'), 'http://a/b')

    def test_standalone_comment_lines_still_work(self):
        root = parse('// header\nPART\n{\n\tpart = A\n}\n')
        self.assertEqual(root.child('PART').value('part'), 'A')


class ConfigNodeInputTests(unittest.TestCase):
    """Non-str/bytes input and invalid bytes must yield stable errors."""

    def test_non_string_inputs(self):
        for bad in (None, 123, 4.5, ['PART'], {'a': 1}, b'ok'):
            if isinstance(bad, bytes):
                continue
            with self.assertRaises(CraftParseError) as ctx:
                parse(bad)
            self.assertEqual(ctx.exception.code, 'invalid_input_type',
                             f'input {bad!r}')

    def test_invalid_utf8_bytes(self):
        with self.assertRaises(CraftParseError) as ctx:
            parse(b'PART\n{\n\tpart = \xff\xfe\n}\n')
        self.assertEqual(ctx.exception.code, 'invalid_encoding')

    def test_valid_utf8_bytes_with_bom_still_work(self):
        root = parse('\ufeffship = Bytes\n'.encode('utf-8'))
        self.assertEqual(root.value('ship'), 'Bytes')

    def test_constructor_never_raises_value_error(self):
        error = CraftParseError('unregistered_code', 'x', line=1, column=2)
        self.assertEqual(error.code, 'unregistered_code')
        self.assertEqual(error.line, 1)

    def test_all_raised_codes_are_registered(self):
        import server.ksp_confignode as module
        original = module.MAX_INPUT_CHARS
        module.MAX_INPUT_CHARS = 8
        collected = set()
        try:
            triggers = [
                lambda: parse('PART\n{\n'),           # unexpected_eof
                lambda: parse('}'),                    # unexpected_close
                lambda: parse('PART\n{\n\t{\n\t}\n}\n'),  # missing_node_name
                lambda: parse('='),                    # invalid_assignment
                lambda: parse('PART\nship = X\n{\n}\n'),  # invalid_node_declaration
                lambda: parse('PART\n'),               # dangling_node_name
                lambda: parse('PART\n}'),              # dangling_node_name
                lambda: parse(123),                    # invalid_input_type
                lambda: parse(b'\xff\xfe'),            # invalid_encoding
                lambda: parse('part = aaa'),           # input_too_large
            ]
            for trigger in triggers:
                with self.assertRaises(CraftParseError) as ctx:
                    trigger()
                collected.add(ctx.exception.code)
        finally:
            module.MAX_INPUT_CHARS = original
        self.assertTrue(collected.issubset(set(module._ERROR_CODES)),
                        collected - set(module._ERROR_CODES))
        for required in ('dangling_node_name', 'invalid_node_declaration',
                         'invalid_input_type', 'invalid_encoding',
                         'unexpected_character'):
            self.assertIn(required, module._ERROR_CODES)


class ConfigNodeSafetyTests(unittest.TestCase):
    def test_deterministic_and_no_mutation(self):
        text = 'ship = X\nPART\n{\n\tpart = A\n\tsym = B\n\tsym = C\n}\n'
        before = text
        root1 = parse(text)
        root2 = parse(text)
        self.assertEqual(root1.to_dict(), root2.to_dict())
        self.assertEqual(text, before)

    def test_fixture_simple_parses(self):
        text = (FIXTURES / 'anonymous_simple.craft').read_text(
            encoding='utf-8-sig')
        root = parse(text)
        part = root.child('PART')
        self.assertEqual(part.value('part'), 'TestEngine-1_4293000001')
        self.assertEqual(part.values_named('attN'),
                         ['decoupler1_4293000000', 'decoupler1_4293000000'])
        self.assertEqual(len(part.children_named('MODULE')), 1)
        self.assertEqual(len(part.children_named('RESOURCE')), 2)

    def test_to_dict_is_json_serializable(self):
        import json
        root = parse('ship = X\nPART\n{\n\tpart = A\n}\n')
        json.dumps(root.to_dict(), ensure_ascii=False)


if __name__ == '__main__':
    unittest.main()
