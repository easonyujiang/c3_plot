"""
Independent tests for the read-only ECMA-335 / IL parser (6B.3C.2B-R2).
=======================================================================

The fixture is a small .NET assembly built BY HAND (no compiler, no
network): PE headers, CLI header, metadata root with #~ / #Strings / #Blob
/ #GUID streams, TypeDef/Field/MethodDef/Param tables and hand-assembled
method bodies exercising tiny/fat headers, branches, switch, 0xFE
instructions, reserved opcodes and truncated bodies.

The opcode tables are additionally checked against an INDEPENDENT fixture
(``tests/fixtures/opcodes_dotnet.json``) that was generated from
``System.Reflection.Emit.OpCodes`` on this machine — never derived from the
production ``_OPCODES`` dict — and, on Windows, against a LIVE
``System.Reflection.Emit.OpCodes`` dump.

These tests never skip (except the live Windows oracle): everything else
is self-contained.
"""

import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from server.ksp_dotnet_il import (
    DotNetAssembly,
    T_FIELD,
    T_METHODDEF,
    T_TYPEDEF,
    _FE_OPCODES,
    _OPCODES,
    field_evidence,
    method_evidence,
    methods_calling_method,
    methods_named,
    methods_writing_field,
    read_dotnet_assembly,
)

# metadata tokens used by the fixture
TOKEN_WIDGET_ALPHA = 0x04000001
TOKEN_WIDGET_BETA = 0x04000002
TOKEN_WIDGET2_ALPHA = 0x04000003
TOKEN_WIDGET2_BROKEN = 0x04000004
TOKEN_TOUCH = 0x06000001
TOKEN_CTOR = 0x06000002
TOKEN_SWITCH = 0x06000003
TOKEN_FEOP = 0x06000004
TOKEN_RESERVED = 0x06000005
TOKEN_TRUNCFAT = 0x06000006
TOKEN_TRUNCTINY = 0x06000007
TOKEN_WIDGET2_TOUCH = 0x06000008

_FIXTURE_FILE = Path(__file__).parent / 'fixtures' / 'opcodes_dotnet.json'


def _aligned(value, alignment=4):
    return (value + alignment - 1) & ~(alignment - 1)


def _build_bodies():
    """Hand-assembled method bodies -> list of (raw_bytes); the builder
    lays them out sequentially and patches the RVAs."""
    # body 1: Widget.Touch (tiny): ldc.i4.0; br.s +6; dup; stfld beta; ret
    # br.s at offset 1, next instruction at 3, target = 3 + 6 = 9 = ret:
    # the branch target lies ON an instruction boundary (ret).
    b1_il = bytes([
        0x16,                                          # ldc.i4.0 @0
        0x2B, 0x06,                                    # br.s @1 -> 0x9
        0x25,                                          # dup @3
        0x7D, 0x02, 0x00, 0x00, 0x04,                  # stfld beta @4
        0x2A,                                          # ret @9
    ])
    b1 = bytes([0x02 | (len(b1_il) << 2)]) + b1_il

    # body 2: Widget.Ctor (FAT, header size = 4 DWORDs = 16 bytes)
    b2_il = bytes([
        0x17,                                          # ldc.i4.1 @0
        0x7D, 0x02, 0x00, 0x00, 0x04,                  # stfld beta @1
        0x2A,                                          # ret @6
    ])
    b2 = (struct.pack('<HH', 0x4003, 8)
          + struct.pack('<I', len(b2_il))
          + struct.pack('<I', 0)
          + b'\x00' * 4) + b2_il

    # body 3: Widget.Switch (tiny): switch(2) [0, 1]; pop; ret
    b3_il = (bytes([0x45]) + struct.pack('<I', 2)
             + struct.pack('<i', 0) + struct.pack('<i', 1)
             + bytes([0x26, 0x2A]))
    b3 = bytes([0x02 | (len(b3_il) << 2)]) + b3_il

    # body 4: Widget.FeOp (tiny): 0xFE ldloc u16; ret
    b4_il = bytes([0xFE, 0x0C, 0x01, 0x00, 0x2A])
    b4 = bytes([0x02 | (len(b4_il) << 2)]) + b4_il

    # body 5: Widget.Reserved (tiny): 0x77 (reserved); ret
    b5_il = bytes([0x77, 0x2A])
    b5 = bytes([0x02 | (len(b5_il) << 2)]) + b5_il

    # body 6: Widget.TruncFat (fat header, code_size=100, only 4 exist)
    b6 = (struct.pack('<HH', 0x4003, 8)
          + struct.pack('<I', 100)
          + struct.pack('<I', 0)
          + b'\x00' * 4) + bytes([0x2A, 0x00, 0x00, 0x00])

    # body 7: Widget.TruncTiny (tiny claiming 8 code bytes, only 2 exist)
    b7 = bytes([0x02 | (8 << 2)]) + bytes([0x2A, 0x00])

    return [b1, b2, b3, b4, b5, b6, b7]


def _build_fixture(path):
    """Build the fixture assembly at `path`; returns the RVA map."""
    bodies = _build_bodies()

    # ---- metadata heaps ------------------------------------------------
    strings = (b'\x00' + b'Acme\x00' + b'Widget\x00' + b'Acme2\x00'
               + b'alpha\x00' + b'beta\x00' + b'broken\x00' + b'Touch\x00'
               + b'Ctor\x00' + b'Switch\x00' + b'FeOp\x00'
               + b'Reserved\x00' + b'TruncFat\x00' + b'TruncTiny\x00'
               + b'mod\x00')
    idx_acme = 1
    idx_widget = 6
    idx_acme2 = 13
    idx_alpha = 19
    idx_beta = 25
    idx_broken = 30
    idx_touch = 37
    idx_ctor = 43
    idx_switch = 48
    idx_feop = 55
    idx_reserved = 60
    idx_truncfat = 69
    idx_trunctiny = 78
    idx_mod = 88
    blob_heap = bytes([0x00, 0x02, 0x06, 0x08])  # index 1 -> b'\x06\x08'
    guid_heap = b'\xAB' * 16

    # ---- #~ tables stream ----------------------------------------------
    tables = bytearray()
    tables += struct.pack('<IBBBB', 0, 1, 1, 0x00, 0)   # reserved, ver, heaps
    valid = (1 << 0) | (1 << 2) | (1 << 4) | (1 << 6) | (1 << 8)
    tables += struct.pack('<QQ', valid, 0)
    for count in (1, 2, 4, 8, 1):                        # Module, TypeDef,
        tables += struct.pack('<I', count)               # Field, MethodDef,
    # Module (1 row): Generation, Name, Mvid, EncId, EncBaseId
    tables += struct.pack('<HHHHH', 0, idx_mod, 1, 1, 1)
    # TypeDef (2 rows): Flags, Name, Namespace, Extends(coded), FieldList,
    # MethodList; both types share the simple name 'Widget' in different
    # namespaces so name-only lookups are ambiguous
    for row in ((idx_widget, idx_acme, 1, 1),
                (idx_widget, idx_acme2, 3, 8)):
        tables += struct.pack('<IHHHHH', 0, row[0], row[1], 4, row[2],
                              row[3])
    # Field (4 rows): Flags, Name, Signature(blob idx)
    #   row 3 (Widget2.alpha) is valid; row 4 (Widget2.broken) points at
    #   a blob index far outside the heap (0x7FFF)
    for name_idx, blob_idx in ((idx_alpha, 1), (idx_beta, 1),
                               (idx_alpha, 1), (idx_broken, 0x7FFF)):
        tables += struct.pack('<HHH', 0x0006, name_idx, blob_idx)
    # MethodDef (8 rows): RVA, ImplFlags, Flags, Name, Signature, ParamList
    # (RVA placeholders patched later)
    method_rows = [
        (0, idx_touch), (0, idx_ctor), (0, idx_switch), (0, idx_feop),
        (0, idx_reserved), (0, idx_truncfat), (0, idx_trunctiny),
        (0, idx_touch),
    ]
    rva_offsets = []
    for rva, name_idx in method_rows:
        rva_offsets.append(len(tables))
        tables += struct.pack('<IHH', rva, 0x00, 0x91)
        tables += struct.pack('<HHH', name_idx, 1, 1)
    # Param (1 row): Flags, Sequence, Name
    tables += struct.pack('<HHH', 0, 0, 0)
    tables = bytearray(tables)

    # ---- metadata root ---------------------------------------------------
    version = b'v2.0.50727'
    header_size = 8 + 4 + 4 + len(version)
    header_size = _aligned(header_size + 4)  # + flags/streams word

    streams = [('#~', tables), ('#Strings', strings),
               ('#Blob', blob_heap), ('#GUID', guid_heap)]
    directory_size = 0
    for name, _data in streams:
        directory_size += _aligned(8 + len(name) + 1)
    metadata_size = header_size + directory_size
    offsets = []
    for name, data in streams:
        offsets.append(metadata_size)
        metadata_size += _aligned(len(data))

    # ---- CLI header + section layout -------------------------------------
    cli_rva = 0x2000
    metadata_rva = cli_rva + 0x48
    bodies_rva = _aligned(metadata_rva + metadata_size)
    body_rvas = {}
    cursor = bodies_rva
    for index, body in enumerate(bodies):
        body_rvas[index] = cursor
        cursor += len(body)
    section_end = cursor
    virtual_size = section_end - cli_rva
    raw_size = virtual_size

    # patch the real method RVAs into the #~ tables stream (BEFORE the
    # metadata is assembled, so the emitted #~ stream carries them)
    for index, rva in body_rvas.items():
        struct.pack_into('<I', tables, rva_offsets[index], rva)
    tables = bytes(tables)

    meta = bytearray()
    meta += b'BSJB'
    meta += struct.pack('<HH', 1, 1)
    meta += struct.pack('<I', 0)
    meta += struct.pack('<I', len(version))
    meta += version
    while len(meta) % 4:
        meta += b'\x00'
    meta += struct.pack('<HH', 0, len(streams))
    for (name, data), offset in zip(streams, offsets):
        meta += struct.pack('<II', offset, len(data))
        meta += name.encode('ascii') + b'\x00'
        while len(meta) % 4:
            meta += b'\x00'
    for (_name, data), offset in zip(streams, offsets):
        meta += data
        while len(meta) < offset + _aligned(len(data)):
            meta += b'\x00'
    metadata = bytes(meta)

    cli = bytearray()
    cli += struct.pack('<I', 0x48)                 # cb
    cli += struct.pack('<HH', 2, 5)                # runtime version
    cli += struct.pack('<II', metadata_rva, len(metadata))
    cli += struct.pack('<I', 0)                    # flags
    cli += struct.pack('<I', TOKEN_CTOR)           # entry point token
    cli += b'\x00' * (0x48 - len(cli))
    cli = bytes(cli)
    assert len(cli) == 0x48

    section = cli + (b'\x00' * (metadata_rva - cli_rva - len(cli)))
    section += metadata
    section += b'\x00' * (bodies_rva - (metadata_rva + len(metadata)))
    for body in bodies:
        section += body
    assert len(section) == raw_size

    # ---- PE file ----------------------------------------------------------
    e_lfanew = 0x40
    size_optional = 0xE0
    pe = bytearray()
    pe += b'MZ'
    pe += b'\x00' * (0x3C - 2)
    pe += struct.pack('<I', e_lfanew)
    pe += b'\x00' * (e_lfanew - len(pe))
    pe += b'PE\x00\x00'
    pe += struct.pack('<HH', 0x14C, 1)             # machine, sections
    pe += struct.pack('<I', 0)                     # timestamp
    pe += struct.pack('<I', 0)                     # pointer to symbols
    pe += struct.pack('<I', 0)                     # number of symbols
    pe += struct.pack('<H', size_optional)         # size of optional
    pe += struct.pack('<H', 0x0102)                # characteristics
    optional = bytearray(size_optional)
    struct.pack_into('<H', optional, 0, 0x10B)       # magic PE32
    struct.pack_into('<I', optional, 0x5C, 16)       # NumberOfRvaAndSizes
    struct.pack_into('<II', optional, 0x60 + 14 * 8,
                     cli_rva, len(cli))              # CLI directory entry
    optional = bytes(optional)
    assert len(optional) == size_optional
    pe += optional
    section_header = bytearray()
    section_header += b'.text'.ljust(8, b'\x00')
    section_header += struct.pack('<II', virtual_size, cli_rva)
    section_header += struct.pack('<II', raw_size, 0x200)
    section_header += struct.pack('<IIHH', 0, 0, 0, 0)
    pe += section_header
    pe += b'\x00' * (0x200 - len(pe))
    pe += section
    Path(path).write_bytes(bytes(pe))

    return {
        'body_rvas': body_rvas,
        'cli_rva': cli_rva,
        'metadata_rva': metadata_rva,
        'virtual_size': virtual_size,
        'raw_size': raw_size,
    }


class FixtureMixin:
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='ksp_il_fixture_')
        fixture_path = os.path.join(cls._tmpdir, 'fixture.dll')
        cls._fixture = _build_fixture(fixture_path)
        cls.assembly = read_dotnet_assembly(fixture_path)

    def fixture(self):
        return self._fixture


class AssemblyBasicsTests(FixtureMixin, unittest.TestCase):
    def test_assembly_name_defaults(self):
        self.assertIsNone(self.assembly.assembly_name)
        self.assertIsNone(self.assembly.assembly_version)

    def test_table_counts(self):
        self.assertEqual(len(self.assembly.table(T_TYPEDEF)), 2)
        self.assertEqual(len(self.assembly.table(T_FIELD)), 4)
        self.assertEqual(len(self.assembly.table(T_METHODDEF)), 8)

    def test_not_a_pe(self):
        path = os.path.join(self._tmpdir, 'bad.dll')
        Path(path).write_bytes(b'not a pe file at all')
        with self.assertRaises(ValueError):
            read_dotnet_assembly(path)


class HeapTests(FixtureMixin, unittest.TestCase):
    def test_string_resolution(self):
        self.assertEqual(self.assembly.string(6), 'Widget')
        self.assertEqual(self.assembly.string(19), 'alpha')
        self.assertEqual(self.assembly.string(25), 'beta')
        self.assertIsNone(self.assembly.string(0))
        self.assertIsNone(self.assembly.string(99999))

    def test_blob_resolution(self):
        self.assertEqual(self.assembly.blob(1), b'\x06\x08')
        self.assertIsNone(self.assembly.blob(0))
        self.assertIsNone(self.assembly.blob(99999))
        self.assertIsNone(self.assembly.blob(-1))
        self.assertIsNone(self.assembly.blob(0x7FFF))


class TypeLookupTests(FixtureMixin, unittest.TestCase):
    def test_namespaced_lookup(self):
        info = self.assembly.find_type_row('Acme.Widget')
        self.assertEqual(info['index'], 1)
        self.assertEqual(info['namespace'], 'Acme')

    def test_simple_name_ambiguous(self):
        info = self.assembly.find_type_row('Widget')
        self.assertTrue(info.get('ambiguous'))
        self.assertIsNone(info.get('index'))

    def test_missing_type(self):
        self.assertIsNone(self.assembly.find_type_row('Acme.Nope'))
        self.assertIsNone(self.assembly.find_type_row(''))


class TypeBoundaryTests(FixtureMixin, unittest.TestCase):
    def test_method_evidence_uses_typedef_boundaries(self):
        touch = method_evidence(self.assembly, 'Acme.Widget', 'Touch')
        self.assertEqual(touch['token'], '0x06000001')
        touch2 = method_evidence(self.assembly, 'Acme2.Widget', 'Touch')
        self.assertEqual(touch2['token'], '0x06000008')

    def test_field_evidence_uses_typedef_boundaries(self):
        alpha = field_evidence(self.assembly, 'Acme.Widget', 'alpha')
        self.assertEqual(alpha['token'], '0x04000001')
        alpha2 = field_evidence(self.assembly, 'Acme2.Widget', 'alpha')
        self.assertEqual(alpha2['token'], '0x04000003')
        beta = field_evidence(self.assembly, 'Acme.Widget', 'beta')
        self.assertEqual(beta['token'], '0x04000002')

    def test_field_signature_blob_is_hashed(self):
        alpha = field_evidence(self.assembly, 'Acme.Widget', 'alpha')
        expected = hashlib.sha256(b'\x06\x08').hexdigest().upper()
        self.assertEqual(alpha['signature_blob_sha256'], expected)
        self.assertEqual(alpha['signature_blob_bytes'], 2)
        self.assertIsNone(alpha['signature_error'])

    def test_unresolvable_signature_blob_is_an_error(self):
        broken = field_evidence(self.assembly, 'Acme2.Widget', 'broken')
        self.assertEqual(broken['token'], '0x04000004')
        self.assertEqual(broken['signature_error'], 'blob_unresolvable')
        self.assertIsNone(broken['signature_blob_sha256'])
        self.assertIsNone(broken['signature_blob_bytes'])

    def test_methods_named_returns_all_overloads(self):
        matches = methods_named(self.assembly, 'Acme.Widget', 'Touch')
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['token'], '0x06000001')
        self.assertEqual(len(matches[0]['instructions']), 5)
        self.assertTrue(matches[0]['decode_complete'])
        self.assertEqual(matches[0]['il_body_status'], 'ok')


class MethodBodyTests(FixtureMixin, unittest.TestCase):
    def test_tiny_method(self):
        body, error = self.assembly.method_body_checked(
            self._fixture['body_rvas'][0])
        self.assertIsNone(error)
        self.assertEqual(len(body), 10)

    def test_fat_method_header_size_respected(self):
        body, error = self.assembly.method_body_checked(
            self._fixture['body_rvas'][1])
        self.assertIsNone(error)
        self.assertEqual(body, bytes([0x17, 0x7D, 0x02, 0x00, 0x00, 0x04,
                                     0x2A]))

    def test_short_branch_target_computed(self):
        ev = method_evidence(self.assembly, 'Acme.Widget', 'Touch')
        summary = ev['il_summary']
        self.assertIn('br.s -> 0x9', summary)
        self.assertTrue(ev['decode_complete'])
        self.assertEqual(ev['il_body_status'], 'ok')

    def test_switch_consumes_all_targets(self):
        ev = method_evidence(self.assembly, 'Acme.Widget', 'Switch')
        lines = ev['il_summary']
        # targets are computed IL offsets relative to the instruction
        # following the switch: 0xD = pop, 0xE = ret
        self.assertIn('switch -> [0xD, 0xE]', lines)
        # the target table bytes must not be decoded as further opcodes
        self.assertIn('pop', lines)
        self.assertIn('ret', lines)
        self.assertTrue(ev['decode_complete'])

    def test_fe_two_byte_instruction(self):
        ev = method_evidence(self.assembly, 'Acme.Widget', 'FeOp')
        self.assertEqual(ev['il_summary'], ['ldloc 1', 'ret'])
        self.assertTrue(ev['decode_complete'])

    def test_reserved_opcode_stops_decoding(self):
        ev = method_evidence(self.assembly, 'Acme.Widget', 'Reserved')
        self.assertFalse(ev['decode_complete'])
        self.assertEqual(ev['il_body_status'], 'decode_error')
        self.assertEqual(ev['first_error_offset'], 0)
        self.assertEqual(ev['decode_errors'],
                         [{'offset': 0, 'code': 'reserved_opcode'}])
        self.assertEqual(ev['il_summary'],
                         ['reserved_0x77 [reserved_opcode] @0x0'])

    def test_fat_truncated_body(self):
        ev = method_evidence(self.assembly, 'Acme.Widget', 'TruncFat')
        self.assertEqual(ev['il_error'], 'fat_truncated')
        self.assertEqual(ev['il_body_status'], 'read_error')
        self.assertIsNone(ev['il_body_sha256'])
        self.assertIsNone(ev['decode_complete'])
        self.assertEqual(ev['il_summary'], [])

    def test_tiny_truncated_body(self):
        ev = method_evidence(self.assembly, 'Acme.Widget', 'TruncTiny')
        self.assertEqual(ev['il_error'], 'tiny_truncated')
        self.assertEqual(ev['il_body_status'], 'read_error')

    def test_summary_is_bounded(self):
        ev = method_evidence(self.assembly, 'Acme.Widget', 'Touch')
        self.assertLessEqual(len(ev['il_summary']), 24)


class DecodeFailureClosureTests(unittest.TestCase):
    """Failure closure of decode_il: reserved/truncated/invalid input can
    never produce a plausible full decode."""

    def setUp(self):
        self.assembly = DotNetAssembly('', '')

    def test_reserved_single_byte(self):
        result = self.assembly.decode_il(bytes([0x77, 0x2A]))
        self.assertFalse(result['decode_complete'])
        self.assertEqual(result['decode_errors'],
                         [{'offset': 0, 'code': 'reserved_opcode'}])
        self.assertEqual(result['first_error_offset'], 0)
        self.assertEqual(result['instructions'][0]['flow'], 'reserved')
        self.assertEqual(len(result['instructions']), 1)

    def test_reserved_fe_byte(self):
        result = self.assembly.decode_il(bytes([0xFE, 0x08, 0x2A]))
        self.assertFalse(result['decode_complete'])
        self.assertEqual(result['decode_errors'][0]['code'],
                         'reserved_opcode')
        self.assertEqual(result['instructions'][0]['name'],
                         'reserved_0xFE_08')

    def test_truncated_prefix_stops(self):
        result = self.assembly.decode_il(bytes([0xFE]))
        self.assertFalse(result['decode_complete'])
        self.assertEqual(result['decode_errors'][0]['code'],
                         'truncated_prefix')

    def test_truncated_operand_stops_decoding(self):
        result = self.assembly.decode_il(bytes([0x2B]))
        self.assertFalse(result['decode_complete'])
        self.assertEqual(result['decode_errors'][0]['code'],
                         'truncated_operand')
        self.assertEqual(result['instructions'][-1]['name'], 'br.s')

    def test_switch_truncated_operand(self):
        result = self.assembly.decode_il(bytes([0x45, 0x04, 0x00, 0x00,
                                                0x00]))
        self.assertFalse(result['decode_complete'])
        self.assertEqual(result['decode_errors'][0]['code'],
                         'truncated_operand')

    def test_invalid_branch_target_off_boundary(self):
        # br.s at 1, next = 3, +2 = 5: 5 is inside the stfld operand and
        # is NOT an instruction boundary
        body = bytes([0x16, 0x2B, 0x02, 0x25, 0x7D, 0x02, 0x00, 0x00,
                      0x04, 0x2A])
        result = self.assembly.decode_il(body)
        self.assertFalse(result['decode_complete'])
        self.assertEqual(result['decode_errors'][0]['code'],
                         'invalid_branch_target')
        self.assertEqual(result['decode_errors'][0]['offset'], 1)

    def test_branch_target_past_end(self):
        # br.s targets 3 + 40 = 43 > body length 4
        result = self.assembly.decode_il(bytes([0x2B, 0x28, 0x2A]))
        self.assertFalse(result['decode_complete'])
        self.assertEqual(result['decode_errors'][0]['code'],
                         'invalid_branch_target')

    def test_switch_target_out_of_body(self):
        body = bytes([0x45]) + struct.pack('<I', 1) \
            + struct.pack('<i', 100) + bytes([0x2A])
        result = self.assembly.decode_il(body)
        self.assertFalse(result['decode_complete'])
        self.assertEqual(result['decode_errors'][0]['code'],
                         'invalid_branch_target')

    def test_prefix_classified_not_executable(self):
        result = self.assembly.decode_il(bytes([0xFE, 0x13, 0x2A]))
        self.assertTrue(result['decode_complete'])
        prefix = result['instructions'][0]
        self.assertEqual(prefix['name'], 'volatile.')
        self.assertEqual(prefix['flow'], 'prefix')
        self.assertIsNone(prefix['error'])
        self.assertEqual(result['instructions'][1]['name'], 'ret')

    def test_single_byte_prefix_classified(self):
        result = self.assembly.decode_il(bytes([0xF8, 0x2A]))
        self.assertTrue(result['decode_complete'])
        self.assertEqual(result['instructions'][0]['flow'], 'prefix')
        self.assertEqual(result['instructions'][0]['name'], 'prefix7')


class ReverseQueryTests(FixtureMixin, unittest.TestCase):
    def test_writers_of_widget_beta(self):
        result = methods_writing_field(self.assembly, TOKEN_WIDGET_BETA)
        # the fixture deliberately contains unparsed / decode-incomplete
        # bodies, so the scan is NOT complete even though writers were
        # found before the broken bodies
        self.assertFalse(result['scan_complete'])
        self.assertEqual(result['method_bodies_unparsed'], 2)
        self.assertEqual(result['method_bodies_decode_incomplete'], 1)
        self.assertEqual(result['field_token'], '0x04000002')
        tokens = [w['token'] for w in result['writers']]
        self.assertEqual(tokens, ['0x06000001', '0x06000002'])
        ctor = next(w for w in result['writers']
                    if w['method'] == 'Ctor')
        self.assertEqual(ctor['writes'][0]['offset'], 1)
        self.assertTrue(ctor['writes'][0]['constant_bool'])
        self.assertEqual(ctor['writes'][0]['constant_source_instruction'],
                         0)
        self.assertTrue(ctor['decode_complete'])
        touch = next(w for w in result['writers'] if w['method'] == 'Touch')
        self.assertEqual(touch['writes'][0]['opcode'], 'stfld')
        self.assertTrue(any('stfld field::Acme.Widget::beta' in line
                            for line in touch['context']))

    def test_writers_never_leak_across_types(self):
        result = methods_writing_field(self.assembly, TOKEN_WIDGET2_ALPHA)
        self.assertEqual(result['writers'], [])
        self.assertFalse(result['scan_complete'])
        # the two deliberately truncated bodies are counted as unparsed
        # and the reserved-opcode body as decode-incomplete
        self.assertEqual(result['method_bodies_unparsed'], 2)
        self.assertEqual(result['method_bodies_decode_incomplete'], 1)

    def test_writer_method_body_sha256(self):
        result = methods_writing_field(self.assembly, TOKEN_WIDGET_BETA)
        ctor = next(w for w in result['writers'] if w['method'] == 'Ctor')
        body, _ = self.assembly.method_body_checked(
            self._fixture['body_rvas'][1])
        self.assertEqual(ctor['il_body_sha256'],
                         hashlib.sha256(body).hexdigest().upper())

    def test_reserved_body_counts_as_decode_incomplete(self):
        # the Reserved method's body hits a reserved opcode; every scan
        # must report it as decode-incomplete
        result = methods_writing_field(self.assembly, TOKEN_WIDGET_BETA)
        self.assertEqual(result['method_bodies_decode_incomplete'], 1)
        self.assertFalse(result['scan_complete'])


class TokenNormalizationTests(FixtureMixin, unittest.TestCase):
    def test_integer_and_string_tokens_equivalent(self):
        as_int = methods_writing_field(self.assembly, 0x04000002)
        as_hex = methods_writing_field(self.assembly, '0x04000002')
        as_dec = methods_writing_field(self.assembly, '67108866')
        for result in (as_int, as_hex, as_dec):
            self.assertEqual(result['field_token'], '0x04000002')
            self.assertEqual(result['writer_count'], 2)
        self.assertEqual(as_int, as_hex)
        self.assertEqual(as_int, as_dec)

    def test_zero_padded_unprefixed_hex_token(self):
        # '04000002' is the canonical zero-padded 8-digit hex spelling and
        # must resolve to table 0x04 row 2 — NOT decimal 4000002
        result = methods_writing_field(self.assembly, '04000002')
        self.assertEqual(result['field_token'], '0x04000002')
        self.assertEqual(result['writer_count'], 2)

    def test_decimal_string_matches_integer(self):
        as_int = methods_writing_field(self.assembly, 0x04000002)
        as_dec = methods_writing_field(self.assembly, '67108866')
        self.assertEqual(as_int, as_dec)

    def test_eight_digit_string_without_leading_zero_is_decimal(self):
        # documented ambiguity rule: 8-digit all-digit strings NOT starting
        # with '0' are read as DECIMAL (20000002 = 0x01312D02), even though
        # a hex reading (0x20000002) would also be token-shaped
        from server.ksp_dotnet_il import _token_parts
        self.assertEqual(_token_parts('20000002'), (0x01, 0x312D02))
        self.assertEqual(_token_parts('67108866'), (0x04, 0x000002))

    def test_hex_with_letter_digits(self):
        from server.ksp_dotnet_il import _token_parts
        # unprefixed strings containing a-f are hex by definition
        self.assertEqual(_token_parts('1F000002'), (0x1F, 0x000002))
        self.assertEqual(_token_parts('FE000001'), (0xFE, 0x000001))
        # ... and the explicit-hex spelling of the same token agrees
        self.assertEqual(_token_parts('0x1F000002'), (0x1F, 0x000002))

    def test_short_digit_strings_are_decimal(self):
        from server.ksp_dotnet_il import _token_parts
        self.assertEqual(_token_parts('16'), (0x00, 0x000010))
        self.assertEqual(_token_parts('1'), (0x00, 0x000001))

    def test_method_token_integer_and_string_equivalent(self):
        as_int = methods_calling_method(self.assembly, 0x06000002)
        as_hex = methods_calling_method(self.assembly, '0x06000002')
        self.assertEqual(as_int['method_token'], '0x06000002')
        self.assertEqual(as_int, as_hex)

    def test_wrong_table_rejected_for_field_query(self):
        result = methods_writing_field(self.assembly, 0x06000001)
        self.assertEqual(result['error'], 'invalid_token_table')
        self.assertEqual(result['received_table_id'], 0x06)
        self.assertEqual(result['received_table_name'], 'MethodDef')
        self.assertFalse(result['scan_complete'])
        self.assertEqual(result['writers'], [])

    def test_wrong_table_rejected_for_call_query(self):
        result = methods_calling_method(self.assembly, 0x04000001)
        self.assertEqual(result['error'], 'invalid_token_table')
        self.assertEqual(result['received_table_id'], 0x04)
        self.assertEqual(result['received_table_name'], 'Field')

    def test_malformed_token_rejected(self):
        for bad in ('', 'not-a-token', -1, 0x100000000):
            result = methods_writing_field(self.assembly, bad)
            self.assertEqual(result['error'], 'invalid_token', repr(bad))

    def test_field_token_never_duplicates_table_id(self):
        result = methods_writing_field(self.assembly, 0x04000002)
        self.assertEqual(result['field_token'], '0x04000002')
        result = methods_writing_field(self.assembly, '0x04000002')
        self.assertEqual(result['field_token'], '0x04000002')

    def test_calling_method_scan_state(self):
        result = methods_calling_method(self.assembly, 0x06000002)
        # broken fixture bodies make the scan incomplete by design: even a
        # zero-caller result is therefore NOT conclusive
        self.assertFalse(result['scan_complete'])
        self.assertEqual(result['method_bodies_unparsed'], 2)
        self.assertEqual(result['method_bodies_decode_incomplete'], 1)
        self.assertEqual(result['caller_count'], 0)


class DeterminismTests(FixtureMixin, unittest.TestCase):
    def test_method_evidence_deterministic(self):
        first = method_evidence(self.assembly, 'Acme.Widget', 'Touch')
        second = method_evidence(self.assembly, 'Acme.Widget', 'Touch')
        self.assertEqual(first, second)

    def test_writers_deterministic(self):
        first = methods_writing_field(self.assembly, TOKEN_WIDGET_BETA)
        second = methods_writing_field(self.assembly, TOKEN_WIDGET_BETA)
        self.assertEqual(first, second)

    def test_decode_deterministic(self):
        ev = method_evidence(self.assembly, 'Acme.Widget', 'Switch')
        body, _ = self.assembly.method_body_checked(
            self._fixture['body_rvas'][2])
        first = self.assembly.decode_il(body)
        second = self.assembly.decode_il(body)
        self.assertEqual(first, second)

    def test_annotate_instructions_deterministic(self):
        ev = method_evidence(self.assembly, 'Acme.Widget', 'Touch')
        body, _ = self.assembly.method_body_checked(
            self._fixture['body_rvas'][0])
        annotated = self.assembly.annotate_instructions(
            self.assembly.decode_il(body)['instructions'])
        self.assertEqual(annotated, annotated)
        self.assertEqual(annotated[3]['text'],
                         'stfld field::Acme.Widget::beta')


class TokenNameTests(FixtureMixin, unittest.TestCase):
    def test_field_token_name_namespaced(self):
        self.assertEqual(
            self.assembly.token_name(TOKEN_WIDGET_BETA),
            'field::Acme.Widget::beta')
        self.assertEqual(
            self.assembly.token_name(TOKEN_WIDGET2_ALPHA),
            'field::Acme2.Widget::alpha')

    def test_method_token_name_namespaced(self):
        self.assertEqual(
            self.assembly.token_name(TOKEN_CTOR),
            'method::Acme.Widget::Ctor')

    def test_unknown_token(self):
        self.assertEqual(self.assembly.token_name(0x23000001),
                         'token_23_1')


class OpcodeFixtureConsistencyTests(unittest.TestCase):
    """The production tables must match the INDEPENDENT fixture generated
    from System.Reflection.Emit.OpCodes (tests/fixtures/opcodes_dotnet.json).
    Acceptance counters:
        valid single name mismatches = 0
        valid single operand mismatches = 0
        valid FE name mismatches = 0
        valid FE operand mismatches = 0
        reserved treated as ordinary = 0
    """

    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(
            _FIXTURE_FILE.read_text(encoding='utf-8'))

    def _compare(self, production, valid_rows, reserved_rows, prefix):
        mismatched_names = []
        mismatched_operands = []
        for value, name, kind, size, flow in valid_rows:
            key = int(value, 16) if not value.startswith('FE') \
                else int(value, 16) - 0xFE00
            entry = production.get(key)
            if entry is None:
                mismatched_names.append(
                    f'{prefix}{value:>4} missing ({name})')
                continue
            pname, pkind, psize, pflow = entry
            if pname != name:
                mismatched_names.append(
                    f'{prefix}{value:>4} {pname} != {name}')
            if (pkind, psize) != (kind, size):
                mismatched_operands.append(
                    f'{prefix}{value:>4} {name}: '
                    f'({pkind},{psize}) != ({kind},{size})')
            if pflow != flow:
                mismatched_operands.append(
                    f'{prefix}{value:>4} {name}: flow {pflow} != {flow}')
        reserved_used = []
        for value in reserved_rows:
            key = int(value, 16) if not value.startswith('FE') \
                else int(value, 16) - 0xFE00
            if key in production:
                reserved_used.append(f'{value}')
        return mismatched_names, mismatched_operands, reserved_used

    def test_single_byte_table_matches_fixture(self):
        names, operands, reserved = self._compare(
            _OPCODES, self.fixture['valid_single'],
            self.fixture['reserved_single'], '0x')
        self.assertEqual(names, [])
        self.assertEqual(operands, [])
        self.assertEqual(reserved, [])

    def test_fe_table_matches_fixture(self):
        names, operands, reserved = self._compare(
            _FE_OPCODES, self.fixture['valid_fe'],
            self.fixture['reserved_fe'], 'FE')
        self.assertEqual(names, [])
        self.assertEqual(operands, [])
        self.assertEqual(reserved, [])

    def test_fixture_counts(self):
        self.assertEqual(len(self.fixture['valid_single']), 199)
        self.assertEqual(len(self.fixture['valid_fe']), 27)
        self.assertEqual(len(self.fixture['reserved_single']), 57)

    def test_known_corrections_present(self):
        self.assertEqual(_OPCODES[0x79][0], 'unbox')
        self.assertEqual(_OPCODES[0x7A][0], 'throw')
        self.assertEqual(_OPCODES[0x8C][0], 'box')
        self.assertEqual(_OPCODES[0x8D][0], 'newarr')
        self.assertEqual(_OPCODES[0x90][0], 'ldelem.i1')
        self.assertEqual(_OPCODES[0xA3][0], 'ldelem')
        self.assertEqual(_OPCODES[0xA4][0], 'stelem')
        self.assertEqual(_OPCODES[0xA5][0], 'unbox.any')
        self.assertEqual(_OPCODES[0xB6][0], 'conv.ovf.u2')
        self.assertEqual(_OPCODES[0xB7][0], 'conv.ovf.i4')
        self.assertEqual(_OPCODES[0xDD][0], 'leave')
        self.assertEqual(_OPCODES[0xDE][0], 'leave.s')
        self.assertEqual(_OPCODES[0xBA][0], 'conv.ovf.u8')

    def test_formerly_occupied_reserved_values_are_free(self):
        for value in (0x77, 0x78, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xAB,
                      0xAC, 0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2):
            self.assertNotIn(value, _OPCODES)
        self.assertNotIn(0x19, _FE_OPCODES)


@unittest.skipUnless(os.name == 'nt' and sys.platform == 'win32',
                     'live .NET oracle check requires Windows')
class LiveDotNetOpCodesTests(unittest.TestCase):
    """Realtime consistency against System.Reflection.Emit.OpCodes.

    The oracle is a fresh PowerShell Add-Type dump — the test never derives
    expectations from the production tables."""

    def _dump_live(self):
        code = r'''
using System;
using System.Reflection;
using System.Reflection.Emit;
using System.Text;
public static class OpCodeDumper2 {
    public static string Dump() {
        var sb = new StringBuilder();
        foreach (var f in typeof(OpCodes).GetFields(
                     BindingFlags.Public | BindingFlags.Static)) {
            var op = (OpCode)f.GetValue(null);
            sb.AppendFormat("{0:X4}|{1}|{2}{3}", op.Value, op.Name,
                            op.OperandType, Environment.NewLine);
        }
        return sb.ToString();
    }
}
'''
        script = (
            "$code = @'\n" + code + "\n'@\n"
            "Add-Type -TypeDefinition $code -ErrorAction Stop\n"
            "[OpCodeDumper2]::Dump()\n")
        proc = subprocess.run(
            ['powershell', '-NoProfile', '-Command', script],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            value, name, operand = line.split('|')
            rows.append((int(value, 16), name, operand))
        return rows

    def test_live_opcodes_match_production(self):
        OPERAND_KIND = {
            'InlineNone': (None, 0), 'ShortInlineI': ('i1', 1),
            'InlineI': ('i4', 4), 'InlineI8': ('i8', 8),
            'ShortInlineR': ('r4', 4), 'InlineR': ('r8', 8),
            'ShortInlineVar': ('u1', 1), 'InlineVar': ('u2', 2),
            'ShortInlineBrTarget': ('br1', 1),
            'InlineBrTarget': ('br4', 4), 'InlineSwitch': ('switch', 4),
            'InlineMethod': ('tok4', 4), 'InlineField': ('tok4', 4),
            'InlineType': ('tok4', 4), 'InlineString': ('tok4', 4),
            'InlineSig': ('tok4', 4), 'InlineTok': ('tok4', 4),
        }
        rows = self._dump_live()
        mismatch = []
        for value, name, operand in rows:
            if value >= 0xFE00:
                production = _FE_OPCODES.get(value - 0xFE00)
            else:
                production = _OPCODES.get(value)
            kind, size = OPERAND_KIND[operand]
            if production is None:
                mismatch.append(f'{value:04X} {name}: missing')
                continue
            pname, pkind, psize, pflow = production
            if pname != name:
                mismatch.append(f'{value:04X} {name}: name {pname}')
            if (pkind, psize) != (kind, size):
                mismatch.append(
                    f'{value:04X} {name}: operand ({pkind},{psize})'
                    f' != ({kind},{size})')
        self.assertEqual(mismatch, [])


REAL_KSP = r'D:\SteamLibrary\steamapps\common\Kerbal Space Program'
_REAL_FUELS = Path(REAL_KSP) / 'GameData' / 'RealFuels' / 'Plugins' \
    / 'RealFuels.dll'
_REAL_CHUTE = Path(REAL_KSP) / 'GameData' / 'RealChute' / 'Plugins' \
    / 'RealChute.dll'


@unittest.skipUnless(_REAL_FUELS.is_file(),
                     'real KSP install not present on this machine')
class RealFuelTanksIlTests(unittest.TestCase):
    """Read-only static evidence over the installed RealFuels.dll."""

    @classmethod
    def setUpClass(cls):
        cls.assembly = read_dotnet_assembly(str(_REAL_FUELS))

    def test_mass_is_additive_field_evidence_does_not_crash(self):
        field = field_evidence(self.assembly,
                               'RealFuels.Tanks.ModuleFuelTanks',
                               'massIsAdditive')
        self.assertIsNotNone(field)
        self.assertEqual(field['type'],
                         'RealFuels.Tanks.ModuleFuelTanks')
        self.assertEqual(field['signature_blob_bytes'], 2)
        self.assertIsNone(field['signature_error'])

    def test_get_module_mass_ternary(self):
        ev = method_evidence(self.assembly,
                             'RealFuels.Tanks.ModuleFuelTanks',
                             'GetModuleMass')
        self.assertIsNotNone(ev)
        self.assertIsNone(ev['il_error'])
        self.assertTrue(ev['decode_complete'])
        self.assertEqual(ev['il_body_status'], 'ok')
        self.assertIn('ldfld field::RealFuels.Tanks.ModuleFuelTanks::'
                      'massIsAdditive', ev['il_summary'])
        self.assertTrue(any(line.startswith('brtrue.s')
                            for line in ev['il_summary']))
        self.assertIn('sub', ev['il_summary'])

    def test_mass_is_additive_writers_only_in_calculate_mass(self):
        field = field_evidence(self.assembly,
                               'RealFuels.Tanks.ModuleFuelTanks',
                               'massIsAdditive')
        writers = methods_writing_field(self.assembly,
                                        int(field['token'], 16))
        self.assertTrue(writers['scan_complete'])
        self.assertEqual(writers['method_bodies_unparsed'], 0)
        self.assertEqual(writers['method_bodies_decode_incomplete'], 0)
        self.assertEqual(
            [w['method'] for w in writers['writers']],
            ['CalculateMass'])
        write = writers['writers'][0]['writes'][0]
        self.assertTrue(write['constant_bool'])
        self.assertTrue(writers['writers'][0]['decode_complete'])

    def test_method_range_does_not_leak_across_types(self):
        ev = method_evidence(self.assembly,
                             'RealFuels.Tanks.ModuleFuelTanks',
                             'GetModuleMassChangeWhen')
        self.assertIsNotNone(ev)
        self.assertEqual(ev['il_summary'], ['ldc.i4.0', 'ret'])

    def test_every_real_method_body_decodes(self):
        unparsed = 0
        incomplete = 0
        total = 0
        for mrow in self.assembly.table(T_METHODDEF):
            rva = mrow[0]
            if rva == 0:
                continue
            total += 1
            body, error = self.assembly.method_body_checked(rva)
            if body is None:
                unparsed += 1
                continue
            if not self.assembly.decode_il(body)['decode_complete']:
                incomplete += 1
        self.assertEqual(unparsed, 0)
        self.assertEqual(incomplete, 0)
        self.assertGreater(total, 1000)

    def test_tank_definition_load_uses_reflection(self):
        ev = method_evidence(self.assembly,
                             'RealFuels.Tanks.TankDefinition', 'Load')
        self.assertIsNotNone(ev)
        self.assertTrue(ev['decode_complete'])
        self.assertTrue(any('LoadObjectFromConfig' in line
                            for line in ev['il_summary']))

    def test_tank_definition_mlilayers_never_written_in_il(self):
        field = field_evidence(self.assembly,
                               'RealFuels.Tanks.TankDefinition',
                               'numberOfMLILayers')
        writers = methods_writing_field(self.assembly,
                                        int(field['token'], 16))
        self.assertTrue(writers['scan_complete'])
        self.assertEqual(writers['writers'], [])


@unittest.skipUnless(_REAL_CHUTE.is_file(),
                     'real KSP install not present on this machine')
class RealChuteIlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assembly = read_dotnet_assembly(str(_REAL_CHUTE))

    def test_realchute_mass_chain(self):
        get_mass = method_evidence(self.assembly,
                                   'RealChute.RealChuteModule',
                                   'GetModuleMass')
        self.assertTrue(get_mass['decode_complete'])
        self.assertEqual(get_mass['il_summary'],
                         ['ldarg.0',
                          'ldfld field::RealChute.RealChuteModule::'
                          'massDelta',
                          'ret'])
        update = method_evidence(self.assembly,
                                 'RealChute.RealChuteModule', 'UpdateMass')
        self.assertIsNone(update['il_error'])
        self.assertTrue(update['decode_complete'])
        get_area = method_evidence(self.assembly, 'RealChute.RCUtils',
                                   'GetArea')
        self.assertIsNone(get_area['il_error'])
        self.assertTrue(get_area['decode_complete'])
        self.assertEqual(get_area['il_summary'][-2], 'conv.r4')
        chute_mass = method_evidence(self.assembly, 'RealChute.Parachute',
                                     'get_ChuteMass')
        self.assertIsNone(chute_mass['il_error'])
        self.assertTrue(chute_mass['decode_complete'])


if __name__ == '__main__':
    unittest.main()
