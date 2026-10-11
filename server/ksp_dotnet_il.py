"""
Read-only .NET PE / ECMA-335 metadata & IL reader (phase 6B.3C.2B-R2).
======================================================================

A minimal, dependency-free, READ-ONLY parser for .NET Framework
assemblies (Mono, as used by KSP 1.12): PE headers, CLI header, metadata
root, the key metadata tables (TypeDef / MethodDef / Field / MemberRef /
Assembly / AssemblyRef) and method-body IL bytes with a validated opcode
disassembly for evidence summaries.

Safety contract:

* opens files read-only, never writes, never executes code;
* no reflection loading, no type instantiation, no method invocation;
* never loads dependencies — only the explicitly requested file is parsed;
* reports type/method/field signatures, metadata tokens, IL method-body
  SHA-256 and short semantic summaries; never dumps full disassemblies or
  decompiled sources into reports.

Rework contract (6B.3C.2B-R2):

* opcode tables were fully re-derived from an independent authority
  (``System.Reflection.Emit.OpCodes``, dumped on this machine at
  development time and committed as ``tests/fixtures/opcodes_dotnet.json``
  — never generated from this module); every valid single-byte and 0xFE
  opcode name, operand type and operand SIZE must match that fixture, and
  values the runtime does NOT expose (reserved) are never treated as
  executable instructions;
* decoding is FAILURE-CLOSED: a reserved opcode, a truncated operand, an
  illegal switch table or an invalid branch target (outside the body or
  off an instruction boundary) sets ``decode_complete=false`` and stops
  the walk — remaining bytes are never re-interpreted as reliable
  instructions; every decode carries ``decode_complete`` /
  ``decode_errors`` / ``first_error_offset``;
* ``methods_writing_field()`` and ``methods_calling_method()`` report how
  many bodies were unparsed or decode-incomplete; a zero-writer result is
  only meaningful when ``scan_complete`` is true;
* tokens are normalized: integer tokens, ``0x04000002`` hex strings,
  zero-padded 8-digit hex strings (``'04000002'``) and decimal strings
  (``'67108866'``) are equivalent; unprefixed 8-digit all-digit strings
  NOT starting with ``0`` are read as DECIMAL (documented rule); the
  wrong table id is rejected; ``field_token`` is never rebuilt by
  re-pasting a table id;
* Field signature blobs that are missing / out of bounds are reported as
  an explicit error, never hashed as empty bytes.

Only the tables needed for mass-semantics forensics are decoded.
"""

from __future__ import annotations

import hashlib
import struct
from typing import Dict, List, Optional, Tuple

# metadata table ids (ECMA-335 II.22)
T_TYPEREF = 0x01
T_TYPEDEF = 0x02
T_FIELD = 0x04
T_METHODDEF = 0x06
T_MEMBERREF = 0x0A
T_MODULEREF = 0x1A
T_ASSEMBLY = 0x20
T_REFERENCES = 0x23

_TABLE_IDS = {
    0x00: 'Module', 0x01: 'TypeRef', 0x02: 'TypeDef', 0x03: 'FieldPtr',
    0x04: 'Field', 0x05: 'MethodPtr', 0x06: 'MethodDef', 0x07: 'ParamPtr',
    0x08: 'Param', 0x09: 'InterfaceImpl', 0x0A: 'MemberRef',
    0x0B: 'Constant', 0x0C: 'CustomAttribute', 0x0D: 'FieldMarshal',
    0x0E: 'DeclSecurity', 0x0F: 'ClassLayout', 0x10: 'FieldLayout',
    0x11: 'StandAloneSig', 0x12: 'EventMap', 0x13: 'EventPtr',
    0x14: 'Event', 0x15: 'PropertyMap', 0x16: 'PropertyPtr',
    0x17: 'Property', 0x18: 'MethodSemantics', 0x19: 'MethodImpl',
    0x1A: 'ModuleRef', 0x1B: 'TypeSpec', 0x1C: 'ImplMap',
    0x1D: 'FieldRVA', 0x1E: 'ENCLog', 0x1F: 'ENCMap', 0x20: 'Assembly',
    0x21: 'AssemblyProcessor', 0x22: 'AssemblyOS', 0x23: 'AssemblyRef',
    0x24: 'File', 0x25: 'ExportedType', 0x26: 'ManifestResource',
    0x27: 'NestedClass', 0x2A: 'GenericParam', 0x2B: 'MethodSpec',
    0x2C: 'GenericParamConstraint',
}

_TABLE_NAMES = _TABLE_IDS

# column descriptors: 'S' strings, 'B' blob, 'G' guid, 'R'<table> row idx,
# 'C'(bits, tables) coded, 'U1'/'U2'/'U4' fixed
_COLUMNS = {
    0x00: ['U2', 'S', 'G', 'G', 'G'],
    0x01: ['C(2,[0,26,35,1])', 'S', 'S'],
    # TypeDef: Flags U4, TypeName S, TypeNamespace S, Extends coded,
    # FieldList -> Field table (0x04), MethodList -> MethodDef table (0x06)
    0x02: ['U4', 'S', 'S', 'C(2,[2,1,27])', 'R4', 'R6'],
    0x03: ['R4'],
    0x04: ['U2', 'S', 'B'],
    0x05: ['R6'],
    0x06: ['U4', 'U2', 'U2', 'S', 'B', 'R8'],
    0x07: ['R8'],
    0x08: ['U2', 'U2', 'S'],
    0x09: ['R2', 'C(2,[2,1,27])'],
    0x0A: ['C(3,[2,1,26,6,27])', 'S', 'B'],
    0x0B: ['U1', 'U1', 'C(2,[4,8,23])', 'B'],
    0x0C: ['U2', 'U2', 'B'],
    0x0D: ['U2', 'B'],
    0x0E: ['U2', 'U2', 'B'],
    0x0F: ['U2', 'U4', 'R2'],
    0x10: ['U4', 'R4'],
    0x11: ['B'],
    0x12: ['R2', 'R20'],
    0x13: ['R20'],
    0x14: ['U2', 'S', 'C(2,[2,1,27])'],
    0x15: ['R2', 'R23'],
    0x16: ['R23'],
    0x17: ['U2', 'S', 'B'],
    0x18: ['U2', 'R6', 'U2'],
    0x19: ['R2', 'U2', 'U2'],
    0x1A: ['S'],
    0x1B: ['B'],
    0x1C: ['U2', 'U2', 'S', 'R26'],
    0x1D: ['U4', 'R4'],
    0x1E: ['U4', 'U4'],
    0x1F: ['U4', 'U4'],
    0x20: ['U4', 'U2', 'U2', 'U2', 'U2', 'U4', 'B', 'S', 'S'],
    0x21: ['U4'],
    0x22: ['U4', 'U4', 'U4'],
    0x23: ['U2', 'U2', 'U2', 'U2', 'U4', 'B', 'S', 'S', 'B'],
    0x24: ['U4', 'S', 'B'],
    0x25: ['U4', 'U4', 'S', 'S', 'U2'],
    0x26: ['U4', 'U4', 'S', 'U2'],
    0x27: ['R2', 'R2'],
    0x29: ['U2', 'B'],
    0x2A: ['U2', 'U2', 'U2', 'S'],
    0x2B: ['U2', 'B'],
    0x2C: ['R42', 'U2'],
}
_CODED_CACHE = {}


def _coded_tables(spec):
    """'C(2,[2,1,27])' -> (2, (2, 1, 27))"""
    if spec in _CODED_CACHE:
        return _CODED_CACHE[spec]
    bits = int(spec[2:spec.index(',')])
    tables = tuple(int(x) for x in spec[
        spec.index('[') + 1:spec.index(']')].split(','))
    _CODED_CACHE[spec] = (bits, tables)
    return _CODED_CACHE[spec]


# ---------------------------------------------------------------------------
# Opcode tables.
#
# AUTHORITY: ``System.Reflection.Emit.OpCodes`` (dumped on this machine via
# PowerShell Add-Type at development time; see
# ``tests/fixtures/opcodes_dotnet.json``).  Every entry is
# ``(name, operand_kind, operand_size, flow)`` where
#
#   operand_kind:  None | 'u1' | 'u2' | 'i1' | 'i4' | 'i8' | 'r4' | 'r8'
#                  | 'tok4' | 'br1' | 'br4' | 'switch'
#   operand_size:  encoded operand byte length (0 for None; switch = 4
#                  count-field length; actual size is 4 + 4*count)
#   flow:          'exec'   normal executable instruction
#                  'prefix' runtime-exposed prefix (FlowControl.Meta) —
#                           NOT a standalone executable instruction
#
# Values NOT listed here are reserved: the runtime does not expose them and
# they are never decoded as executable instructions.
# ---------------------------------------------------------------------------

_OPCODES: Dict[int, Tuple[str, Optional[str], int, str]] = {
    0x00: ('nop', None, 0, 'exec'), 0x01: ('break', None, 0, 'exec'),
    0x02: ('ldarg.0', None, 0, 'exec'), 0x03: ('ldarg.1', None, 0, 'exec'),
    0x04: ('ldarg.2', None, 0, 'exec'), 0x05: ('ldarg.3', None, 0, 'exec'),
    0x06: ('ldloc.0', None, 0, 'exec'), 0x07: ('ldloc.1', None, 0, 'exec'),
    0x08: ('ldloc.2', None, 0, 'exec'), 0x09: ('ldloc.3', None, 0, 'exec'),
    0x0A: ('stloc.0', None, 0, 'exec'), 0x0B: ('stloc.1', None, 0, 'exec'),
    0x0C: ('stloc.2', None, 0, 'exec'), 0x0D: ('stloc.3', None, 0, 'exec'),
    0x0E: ('ldarg.s', 'u1', 1, 'exec'), 0x0F: ('ldarga.s', 'u1', 1, 'exec'),
    0x10: ('starg.s', 'u1', 1, 'exec'), 0x11: ('ldloc.s', 'u1', 1, 'exec'),
    0x12: ('ldloca.s', 'u1', 1, 'exec'), 0x13: ('stloc.s', 'u1', 1, 'exec'),
    0x14: ('ldnull', None, 0, 'exec'),
    0x15: ('ldc.i4.m1', None, 0, 'exec'),
    0x16: ('ldc.i4.0', None, 0, 'exec'), 0x17: ('ldc.i4.1', None, 0, 'exec'),
    0x18: ('ldc.i4.2', None, 0, 'exec'), 0x19: ('ldc.i4.3', None, 0, 'exec'),
    0x1A: ('ldc.i4.4', None, 0, 'exec'), 0x1B: ('ldc.i4.5', None, 0, 'exec'),
    0x1C: ('ldc.i4.6', None, 0, 'exec'), 0x1D: ('ldc.i4.7', None, 0, 'exec'),
    0x1E: ('ldc.i4.8', None, 0, 'exec'),
    0x1F: ('ldc.i4.s', 'i1', 1, 'exec'), 0x20: ('ldc.i4', 'i4', 4, 'exec'),
    0x21: ('ldc.i8', 'i8', 8, 'exec'), 0x22: ('ldc.r4', 'r4', 4, 'exec'),
    0x23: ('ldc.r8', 'r8', 8, 'exec'),
    0x25: ('dup', None, 0, 'exec'), 0x26: ('pop', None, 0, 'exec'),
    0x27: ('jmp', 'tok4', 4, 'exec'), 0x28: ('call', 'tok4', 4, 'exec'),
    0x29: ('calli', 'tok4', 4, 'exec'), 0x2A: ('ret', None, 0, 'exec'),
    0x2B: ('br.s', 'br1', 1, 'exec'), 0x2C: ('brfalse.s', 'br1', 1, 'exec'),
    0x2D: ('brtrue.s', 'br1', 1, 'exec'), 0x2E: ('beq.s', 'br1', 1, 'exec'),
    0x2F: ('bge.s', 'br1', 1, 'exec'), 0x30: ('bgt.s', 'br1', 1, 'exec'),
    0x31: ('ble.s', 'br1', 1, 'exec'), 0x32: ('blt.s', 'br1', 1, 'exec'),
    0x33: ('bne.un.s', 'br1', 1, 'exec'), 0x34: ('bge.un.s', 'br1', 1, 'exec'),
    0x35: ('bgt.un.s', 'br1', 1, 'exec'), 0x36: ('ble.un.s', 'br1', 1, 'exec'),
    0x37: ('blt.un.s', 'br1', 1, 'exec'),
    0x38: ('br', 'br4', 4, 'exec'), 0x39: ('brfalse', 'br4', 4, 'exec'),
    0x3A: ('brtrue', 'br4', 4, 'exec'), 0x3B: ('beq', 'br4', 4, 'exec'),
    0x3C: ('bge', 'br4', 4, 'exec'), 0x3D: ('bgt', 'br4', 4, 'exec'),
    0x3E: ('ble', 'br4', 4, 'exec'), 0x3F: ('blt', 'br4', 4, 'exec'),
    0x40: ('bne.un', 'br4', 4, 'exec'), 0x41: ('bge.un', 'br4', 4, 'exec'),
    0x42: ('bgt.un', 'br4', 4, 'exec'), 0x43: ('ble.un', 'br4', 4, 'exec'),
    0x44: ('blt.un', 'br4', 4, 'exec'),
    0x45: ('switch', 'switch', 4, 'exec'),
    0x46: ('ldind.i1', None, 0, 'exec'), 0x47: ('ldind.u1', None, 0, 'exec'),
    0x48: ('ldind.i2', None, 0, 'exec'), 0x49: ('ldind.u2', None, 0, 'exec'),
    0x4A: ('ldind.i4', None, 0, 'exec'), 0x4B: ('ldind.u4', None, 0, 'exec'),
    0x4C: ('ldind.i8', None, 0, 'exec'), 0x4D: ('ldind.i', None, 0, 'exec'),
    0x4E: ('ldind.r4', None, 0, 'exec'), 0x4F: ('ldind.r8', None, 0, 'exec'),
    0x50: ('ldind.ref', None, 0, 'exec'), 0x51: ('stind.ref', None, 0,
                                                 'exec'),
    0x52: ('stind.i1', None, 0, 'exec'), 0x53: ('stind.i2', None, 0, 'exec'),
    0x54: ('stind.i4', None, 0, 'exec'), 0x55: ('stind.i8', None, 0, 'exec'),
    0x56: ('stind.r4', None, 0, 'exec'), 0x57: ('stind.r8', None, 0, 'exec'),
    0x58: ('add', None, 0, 'exec'), 0x59: ('sub', None, 0, 'exec'),
    0x5A: ('mul', None, 0, 'exec'), 0x5B: ('div', None, 0, 'exec'),
    0x5C: ('div.un', None, 0, 'exec'), 0x5D: ('rem', None, 0, 'exec'),
    0x5E: ('rem.un', None, 0, 'exec'), 0x5F: ('and', None, 0, 'exec'),
    0x60: ('or', None, 0, 'exec'), 0x61: ('xor', None, 0, 'exec'),
    0x62: ('shl', None, 0, 'exec'), 0x63: ('shr', None, 0, 'exec'),
    0x64: ('shr.un', None, 0, 'exec'), 0x65: ('neg', None, 0, 'exec'),
    0x66: ('not', None, 0, 'exec'),
    0x67: ('conv.i1', None, 0, 'exec'), 0x68: ('conv.i2', None, 0, 'exec'),
    0x69: ('conv.i4', None, 0, 'exec'), 0x6A: ('conv.i8', None, 0, 'exec'),
    0x6B: ('conv.r4', None, 0, 'exec'), 0x6C: ('conv.r8', None, 0, 'exec'),
    0x6D: ('conv.u4', None, 0, 'exec'), 0x6E: ('conv.u8', None, 0, 'exec'),
    0x6F: ('callvirt', 'tok4', 4, 'exec'), 0x70: ('cpobj', 'tok4', 4,
                                                  'exec'),
    0x71: ('ldobj', 'tok4', 4, 'exec'), 0x72: ('ldstr', 'tok4', 4, 'exec'),
    0x73: ('newobj', 'tok4', 4, 'exec'), 0x74: ('castclass', 'tok4', 4,
                                                'exec'),
    0x75: ('isinst', 'tok4', 4, 'exec'), 0x76: ('conv.r.un', None, 0,
                                                'exec'),
    0x79: ('unbox', 'tok4', 4, 'exec'), 0x7A: ('throw', None, 0, 'exec'),
    0x7B: ('ldfld', 'tok4', 4, 'exec'), 0x7C: ('ldflda', 'tok4', 4, 'exec'),
    0x7D: ('stfld', 'tok4', 4, 'exec'), 0x7E: ('ldsfld', 'tok4', 4, 'exec'),
    0x7F: ('ldsflda', 'tok4', 4, 'exec'), 0x80: ('stsfld', 'tok4', 4,
                                                 'exec'),
    0x81: ('stobj', 'tok4', 4, 'exec'),
    0x82: ('conv.ovf.i1.un', None, 0, 'exec'),
    0x83: ('conv.ovf.i2.un', None, 0, 'exec'),
    0x84: ('conv.ovf.i4.un', None, 0, 'exec'),
    0x85: ('conv.ovf.i8.un', None, 0, 'exec'),
    0x86: ('conv.ovf.u1.un', None, 0, 'exec'),
    0x87: ('conv.ovf.u2.un', None, 0, 'exec'),
    0x88: ('conv.ovf.u4.un', None, 0, 'exec'),
    0x89: ('conv.ovf.u8.un', None, 0, 'exec'),
    0x8A: ('conv.ovf.i.un', None, 0, 'exec'),
    0x8B: ('conv.ovf.u.un', None, 0, 'exec'),
    0x8C: ('box', 'tok4', 4, 'exec'), 0x8D: ('newarr', 'tok4', 4, 'exec'),
    0x8E: ('ldlen', None, 0, 'exec'), 0x8F: ('ldelema', 'tok4', 4, 'exec'),
    0x90: ('ldelem.i1', None, 0, 'exec'), 0x91: ('ldelem.u1', None, 0,
                                                 'exec'),
    0x92: ('ldelem.i2', None, 0, 'exec'), 0x93: ('ldelem.u2', None, 0,
                                                 'exec'),
    0x94: ('ldelem.i4', None, 0, 'exec'), 0x95: ('ldelem.u4', None, 0,
                                                 'exec'),
    0x96: ('ldelem.i8', None, 0, 'exec'), 0x97: ('ldelem.i', None, 0,
                                                 'exec'),
    0x98: ('ldelem.r4', None, 0, 'exec'), 0x99: ('ldelem.r8', None, 0,
                                                 'exec'),
    0x9A: ('ldelem.ref', None, 0, 'exec'),
    0x9B: ('stelem.i', None, 0, 'exec'), 0x9C: ('stelem.i1', None, 0,
                                                'exec'),
    0x9D: ('stelem.i2', None, 0, 'exec'), 0x9E: ('stelem.i4', None, 0,
                                                 'exec'),
    0x9F: ('stelem.i8', None, 0, 'exec'), 0xA0: ('stelem.r4', None, 0,
                                                 'exec'),
    0xA1: ('stelem.r8', None, 0, 'exec'), 0xA2: ('stelem.ref', None, 0,
                                                 'exec'),
    0xA3: ('ldelem', 'tok4', 4, 'exec'), 0xA4: ('stelem', 'tok4', 4,
                                                'exec'),
    0xA5: ('unbox.any', 'tok4', 4, 'exec'),
    0xB3: ('conv.ovf.i1', None, 0, 'exec'), 0xB4: ('conv.ovf.u1', None, 0,
                                                   'exec'),
    0xB5: ('conv.ovf.i2', None, 0, 'exec'), 0xB6: ('conv.ovf.u2', None, 0,
                                                   'exec'),
    0xB7: ('conv.ovf.i4', None, 0, 'exec'), 0xB8: ('conv.ovf.u4', None, 0,
                                                   'exec'),
    0xB9: ('conv.ovf.i8', None, 0, 'exec'), 0xBA: ('conv.ovf.u8', None, 0,
                                                   'exec'),
    0xC2: ('refanyval', 'tok4', 4, 'exec'), 0xC3: ('ckfinite', None, 0,
                                                   'exec'),
    0xC6: ('mkrefany', 'tok4', 4, 'exec'),
    0xD0: ('ldtoken', 'tok4', 4, 'exec'), 0xD1: ('conv.u2', None, 0,
                                                 'exec'),
    0xD2: ('conv.u1', None, 0, 'exec'), 0xD3: ('conv.i', None, 0, 'exec'),
    0xD4: ('conv.ovf.i', None, 0, 'exec'), 0xD5: ('conv.ovf.u', None, 0,
                                                  'exec'),
    0xD6: ('add.ovf', None, 0, 'exec'), 0xD7: ('add.ovf.un', None, 0,
                                               'exec'),
    0xD8: ('mul.ovf', None, 0, 'exec'), 0xD9: ('mul.ovf.un', None, 0,
                                               'exec'),
    0xDA: ('sub.ovf', None, 0, 'exec'), 0xDB: ('sub.ovf.un', None, 0,
                                               'exec'),
    0xDC: ('endfinally', None, 0, 'exec'),
    0xDD: ('leave', 'br4', 4, 'exec'), 0xDE: ('leave.s', 'br1', 1, 'exec'),
    0xDF: ('stind.i', None, 0, 'exec'), 0xE0: ('conv.u', None, 0, 'exec'),
    # runtime-exposed single-byte prefixes (FlowControl.Meta)
    0xF8: ('prefix7', None, 0, 'prefix'), 0xF9: ('prefix6', None, 0,
                                                 'prefix'),
    0xFA: ('prefix5', None, 0, 'prefix'), 0xFB: ('prefix4', None, 0,
                                                 'prefix'),
    0xFC: ('prefix3', None, 0, 'prefix'), 0xFD: ('prefix2', None, 0,
                                                 'prefix'),
    0xFE: ('prefix1', None, 0, 'prefix'), 0xFF: ('prefixref', None, 0,
                                                 'prefix'),
}

# two-byte (0xFE xx) opcodes
_FE_OPCODES: Dict[int, Tuple[str, Optional[str], int, str]] = {
    0x00: ('arglist', None, 0, 'exec'),
    0x01: ('ceq', None, 0, 'exec'), 0x02: ('cgt', None, 0, 'exec'),
    0x03: ('cgt.un', None, 0, 'exec'), 0x04: ('clt', None, 0, 'exec'),
    0x05: ('clt.un', None, 0, 'exec'),
    0x06: ('ldftn', 'tok4', 4, 'exec'), 0x07: ('ldvirtftn', 'tok4', 4,
                                               'exec'),
    0x09: ('ldarg', 'u2', 2, 'exec'), 0x0A: ('ldarga', 'u2', 2, 'exec'),
    0x0B: ('starg', 'u2', 2, 'exec'), 0x0C: ('ldloc', 'u2', 2, 'exec'),
    0x0D: ('ldloca', 'u2', 2, 'exec'), 0x0E: ('stloc', 'u2', 2, 'exec'),
    0x0F: ('localloc', None, 0, 'exec'),
    0x11: ('endfilter', None, 0, 'exec'),
    0x12: ('unaligned.', 'i1', 1, 'prefix'),
    0x13: ('volatile.', None, 0, 'prefix'),
    0x14: ('tail.', None, 0, 'prefix'),
    0x15: ('initobj', 'tok4', 4, 'exec'),
    0x16: ('constrained.', 'tok4', 4, 'prefix'),
    0x17: ('cpblk', None, 0, 'exec'), 0x18: ('initblk', None, 0, 'exec'),
    0x1A: ('rethrow', None, 0, 'exec'),
    0x1C: ('sizeof', 'tok4', 4, 'exec'),
    0x1D: ('refanytype', None, 0, 'exec'),
    0x1E: ('readonly.', None, 0, 'prefix'),
}


def _decode_operand(kind, il, pos):
    """Decode one operand.  Returns (operand, next_pos) where operand is
    ``(kind_value, raw_int)`` or a switch target list; raises IndexError on
    truncation."""
    if kind == 'u1':
        return ('u1', il[pos]), pos + 1
    if kind == 'u2':
        return ('u2', struct.unpack_from('<H', il, pos)[0]), pos + 2
    if kind == 'i1':
        return ('i1', struct.unpack_from('<b', il, pos)[0]), pos + 1
    if kind == 'i4':
        return ('i4', struct.unpack_from('<i', il, pos)[0]), pos + 4
    if kind == 'i8':
        return ('i8', struct.unpack_from('<q', il, pos)[0]), pos + 8
    if kind == 'r4':
        return ('r4', struct.unpack_from('<f', il, pos)[0]), pos + 4
    if kind == 'r8':
        return ('r8', struct.unpack_from('<d', il, pos)[0]), pos + 8
    if kind == 'tok4':
        return ('tok4', struct.unpack_from('<I', il, pos)[0]), pos + 4
    if kind == 'br1':
        return ('br1', struct.unpack_from('<b', il, pos)[0]), pos + 1
    if kind == 'br4':
        return ('br4', struct.unpack_from('<i', il, pos)[0]), pos + 4
    raise ValueError(f'unknown operand kind {kind!r}')


class DotNetAssembly:
    """Parsed .NET assembly (read-only)."""

    def __init__(self, path: str, sha256: str):
        self.path = path
        self.sha256 = sha256
        self.assembly_name = None
        self.assembly_version = None
        self.tables: Dict[int, List[list]] = {}
        self.strings: bytes = b''
        self.blobs: bytes = b''
        self.table_row_counts: Dict[int, int] = {}
        self._sections: List[Tuple[int, int, int]] = []
        self._ranges = None
        self._field_owner: Dict[int, Tuple[str, str]] = {}
        self._method_owner: Dict[int, Tuple[str, str]] = {}

    # ---- heaps -----------------------------------------------------------

    def string(self, offset) -> Optional[str]:
        if offset == 0:
            return None
        if offset < 0 or offset >= len(self.strings):
            return None
        end = self.strings.find(b'\x00', offset)
        if end < 0:
            return None
        return self.strings[offset:end].decode('utf-8', errors='replace')

    def blob(self, offset) -> Optional[bytes]:
        """Resolve a #Blob heap index (ECMA-335 II.24.2.4 compressed
        length).  Returns the blob CONTENT (length prefix stripped) or None
        when the index is absent / malformed / out of bounds."""
        if offset == 0:
            return None
        data = self.blobs
        if offset < 0 or offset >= len(data):
            return None
        try:
            first = data[offset]
            if first & 0x80 == 0:
                length = first
                pos = offset + 1
            elif first & 0xC0 == 0x80:
                length = ((first & 0x3F) << 8) | data[offset + 1]
                pos = offset + 2
            elif first & 0xE0 == 0xC0:
                length = (((first & 0x1F) << 24) | (data[offset + 1] << 16)
                          | (data[offset + 2] << 8) | data[offset + 3])
                pos = offset + 4
            else:
                return None
        except IndexError:
            return None
        if length < 0 or pos + length > len(data):
            return None
        return data[pos:pos + length]

    # ---- tables ----------------------------------------------------------

    def table(self, table_id):
        return self.tables.get(table_id, [])

    def row(self, table_id, row_index_1based):
        rows = self.tables.get(table_id, [])
        if 1 <= row_index_1based <= len(rows):
            return rows[row_index_1based - 1]
        return None

    # ---- type ranges -----------------------------------------------------

    def _build_ranges(self):
        """Precompute per-TypeDef field/method list ranges plus reverse
        owner maps (field/method row index -> (namespace, type name))."""
        if self._ranges is not None:
            return
        typedef_rows = self.table(T_TYPEDEF)
        field_count = len(self.table(T_FIELD))
        method_count = len(self.table(T_METHODDEF))
        ranges = []
        for i, row in enumerate(typedef_rows):
            namespace = self.string(row[2]) or ''
            name = self.string(row[1]) or ''
            field_start = row[4]
            method_start = row[5]
            if i + 1 < len(typedef_rows):
                field_end = typedef_rows[i + 1][4]
                method_end = typedef_rows[i + 1][5]
            else:
                field_end = field_count + 1
                method_end = method_count + 1
            ranges.append((namespace, name, field_start, field_end,
                           method_start, method_end))
        field_owner: Dict[int, Tuple[str, str]] = {}
        method_owner: Dict[int, Tuple[str, str]] = {}
        for namespace, name, fstart, fend, mstart, mend in ranges:
            for index in range(fstart, fend):
                field_owner[index] = (namespace, name)
            for index in range(mstart, mend):
                method_owner[index] = (namespace, name)
        self._ranges = ranges
        self._field_owner = field_owner
        self._method_owner = method_owner

    def type_ranges(self, type_row_index):
        """(field_start, field_end_exclusive, method_start,
        method_end_exclusive) for a 1-based TypeDef row."""
        self._build_ranges()
        namespace, name, fstart, fend, mstart, mend = \
            self._ranges[type_row_index - 1]
        return fstart, fend, mstart, mend

    def find_type_row(self, type_name):
        """Locate a TypeDef by ``Namespace.TypeName`` (or a bare type name).

        Returns ``{'index', 'namespace', 'name'}`` for a unique match,
        ``{'index': None, 'ambiguous': True, ...}`` when several types
        share the name (namespace required to disambiguate), or None.
        """
        if not type_name:
            return None
        if '.' in type_name:
            namespace, simple = type_name.rsplit('.', 1)
        else:
            namespace, simple = None, type_name
        matches = []
        for index, row in enumerate(self.table(T_TYPEDEF), 1):
            name = self.string(row[1])
            if name != simple:
                continue
            ns = self.string(row[2]) or ''
            if namespace is not None and ns != namespace:
                continue
            matches.append({'index': index, 'namespace': ns, 'name': name})
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return {'index': None, 'namespace': namespace or '',
                    'name': simple, 'ambiguous': True}
        return None

    # ---- name resolution -------------------------------------------------

    def _row_name(self, table_id, row_index):
        row = self.row(table_id, row_index)
        if row is None:
            return f'{_TABLE_NAMES.get(table_id, table_id)}[{row_index}]'
        if table_id == T_TYPEDEF:
            name = self.string(row[1])
        elif table_id in (T_METHODDEF, T_FIELD, T_MEMBERREF):
            name = self.string(row[3] if table_id == T_METHODDEF
                               else row[1])
        else:
            name = None
        return name or f'{_TABLE_NAMES.get(table_id, table_id)}[{row_index}]'

    def token_name(self, token):
        """Human-readable name for a metadata token (field/method/call),
        namespaced via the declaring TypeDef when resolvable."""
        table_id = (token >> 24) & 0xFF
        row_index = token & 0xFFFFFF
        if table_id == T_FIELD:
            self._build_ranges()
            owner = self._field_owner.get(row_index)
            name = self._row_name(T_FIELD, row_index)
            if owner and owner[0]:
                return f'field::{owner[0]}.{owner[1]}::{name}'
            if owner:
                return f'field::{owner[1]}::{name}'
            return f'field::{name}'
        if table_id == T_METHODDEF:
            self._build_ranges()
            owner = self._method_owner.get(row_index)
            name = self._row_name(T_METHODDEF, row_index)
            if owner and owner[0]:
                return f'method::{owner[0]}.{owner[1]}::{name}'
            if owner:
                return f'method::{owner[1]}::{name}'
            return f'method::{name}'
        if table_id == T_MEMBERREF:
            return f'member::{self._row_name(T_MEMBERREF, row_index)}'
        if table_id == T_TYPEDEF:
            row = self.row(T_TYPEDEF, row_index)
            if row is None:
                return f'type::TypeDef[{row_index}]'
            namespace = self.string(row[2]) or ''
            name = self.string(row[1]) or ''
            return f'type::{namespace}.{name}' if namespace \
                else f'type::{name}'
        return f'token_{table_id:02X}_{row_index}'

    # ---- method bodies (ECMA-335 II.25.4) --------------------------------

    def _rva_to_offset(self, rva):
        for virtual_address, virtual_size, raw_offset in self._sections:
            if virtual_address <= rva < virtual_address + virtual_size:
                return raw_offset + (rva - virtual_address)
        return None

    def method_body_checked(self, rva) -> Tuple[Optional[bytes],
                                                Optional[str]]:
        """Read the IL bytes of one method body.

        Returns ``(il_bytes, None)`` on success, or ``(None, error_code)``
        with a STABLE error code for: zero RVA, RVA outside any section,
        unreadable header, illegal header byte, truncated tiny body,
        truncated fat header, illegal fat header size, truncated fat body.
        Never returns a partial body that could be mistaken for valid IL.
        """
        if rva == 0:
            return None, 'rva_zero'
        offset = self._rva_to_offset(rva)
        if offset is None:
            return None, 'rva_out_of_section'
        try:
            with open(self.path, 'rb') as handle:
                handle.seek(offset)
                header = handle.read(1)
                if not header:
                    return None, 'header_unreadable'
                b0 = header[0]
                if b0 & 0x03 == 0x02:  # tiny format
                    code_size = b0 >> 2
                    if code_size == 0:
                        return None, 'tiny_empty_body'
                    body = handle.read(code_size)
                    if len(body) < code_size:
                        return None, 'tiny_truncated'
                    return body, None
                if b0 & 0x03 == 0x03:  # fat format
                    fat = header + handle.read(11)
                    if len(fat) < 12:
                        return None, 'fat_header_truncated'
                    flags = struct.unpack_from('<H', fat, 0)[0]
                    code_size = struct.unpack_from('<I', fat, 4)[0]
                    header_size_dwords = (flags >> 12) & 0xF
                    if header_size_dwords < 3:
                        return None, 'invalid_header_size'
                    il_offset = offset + header_size_dwords * 4
                    handle.seek(il_offset)
                    body = handle.read(code_size)
                    if len(body) < code_size:
                        return None, 'fat_truncated'
                    return body, None
                return None, 'invalid_method_header'
        except OSError:
            return None, 'read_failed'

    def method_body(self, rva) -> Optional[bytes]:
        """Raw IL bytes of a method body (header stripped), or None."""
        il, error = self.method_body_checked(rva)
        return il

    # ---- decoding (failure-closed) ---------------------------------------

    def decode_il(self, il: bytes) -> dict:
        """Decode an IL body with failure closure.

        Returns::

            {
              'instructions': [...],       # decoded prefix of the body
              'decode_complete': bool,     # whole body decoded AND every
                                           # branch target validated
              'decode_errors': [...],      # [{'offset', 'code'}, ...]
              'first_error_offset': int|None,
            }

        Instruction records: ``offset`` (byte offset in the body), ``name``,
        ``size`` (encoded bytes), ``operand`` (decoded value or None),
        ``target`` (computed IL-relative branch/switch target offset or
        None), ``flow`` (``exec`` | ``prefix`` | ``reserved``), ``error``
        (stable code or None).

        A reserved opcode, a truncated operand (incl. a truncated switch
        table) STOP the walk — the remaining bytes are never decoded.  A
        branch/switch target that is outside the body or off an instruction
        boundary yields ``invalid_branch_target`` and ``decode_complete``
        = false.
        """
        n = len(il)
        instructions: List[dict] = []
        errors: List[dict] = []
        complete = True
        first_error = None
        boundaries = []
        pos = 0

        def fail(offset, code):
            nonlocal complete, first_error
            complete = False
            errors.append({'offset': offset, 'code': code})
            if first_error is None:
                first_error = offset

        while pos < n:
            base = pos
            if il[pos] == 0xFE:
                pos += 1
                if pos >= n:
                    instructions.append({
                        'offset': base, 'name': 'unknown_0xFE', 'size': 1,
                        'operand': None, 'target': None,
                        'flow': 'reserved', 'error': 'truncated_prefix'})
                    fail(base, 'truncated_prefix')
                    break
                two = il[pos]
                pos += 1
                spec = _FE_OPCODES.get(two)
                if spec is None:
                    instructions.append({
                        'offset': base,
                        'name': f'reserved_0xFE_{two:02X}', 'size': 2,
                        'operand': None, 'target': None,
                        'flow': 'reserved', 'error': 'reserved_opcode'})
                    fail(base, 'reserved_opcode')
                    break
                name, kind, _size, flow = spec
            else:
                byte = il[pos]
                pos += 1
                spec = _OPCODES.get(byte)
                if spec is None:
                    instructions.append({
                        'offset': base, 'name': f'reserved_0x{byte:02X}',
                        'size': 1, 'operand': None, 'target': None,
                        'flow': 'reserved', 'error': 'reserved_opcode'})
                    fail(base, 'reserved_opcode')
                    break
                name, kind, _size, flow = spec

            if kind == 'switch':
                try:
                    count = struct.unpack_from('<I', il, pos)[0]
                    end = pos + 4 + 4 * count
                    if end > n:
                        raise IndexError
                    targets = [
                        end + struct.unpack_from(
                            '<i', il, pos + 4 + 4 * i)[0]
                        for i in range(count)
                    ]
                except IndexError:
                    instructions.append({
                        'offset': base, 'name': name,
                        'size': pos - base, 'operand': None,
                        'target': None, 'flow': flow,
                        'error': 'truncated_operand'})
                    fail(base, 'truncated_operand')
                    break
                instructions.append({
                    'offset': base, 'name': name,
                    'size': 1 + 4 + 4 * count,
                    'operand': ('switch', count), 'target': targets,
                    'flow': flow, 'error': None})
                boundaries.append(base)
                pos = end
                continue

            if kind is None:
                instructions.append({
                    'offset': base, 'name': name, 'size': pos - base,
                    'operand': None, 'target': None, 'flow': flow,
                    'error': None})
                boundaries.append(base)
                continue
            try:
                operand, next_pos = _decode_operand(kind, il, pos)
            except (IndexError, struct.error):
                instructions.append({
                    'offset': base, 'name': name, 'size': pos - base,
                    'operand': None, 'target': None, 'flow': flow,
                    'error': 'truncated_operand'})
                fail(base, 'truncated_operand')
                break
            target = None
            if kind in ('br1', 'br4'):
                target = next_pos + operand[1]
            instructions.append({
                'offset': base, 'name': name, 'size': next_pos - base,
                'operand': operand, 'target': target, 'flow': flow,
                'error': None})
            boundaries.append(base)
            pos = next_pos

        # branch target validation: targets must lie on instruction
        # boundaries inside the body
        boundary_set = set(boundaries)
        for insn in instructions:
            if insn['target'] is None:
                continue
            targets = insn['target'] \
                if isinstance(insn['target'], list) \
                else [insn['target']]
            for target in targets:
                if target not in boundary_set:
                    insn['error'] = 'invalid_branch_target'
                    fail(insn['offset'], 'invalid_branch_target')
                    break

        return {
            'instructions': instructions,
            'decode_complete': complete,
            'decode_errors': errors,
            'first_error_offset': first_error,
        }

    def _operand_text(self, name, operand):
        if operand is None:
            return None
        kind, value = operand
        if kind == 'switch':
            return value
        if kind == 'tok4':
            return self.token_name(value)
        if kind == 'r4':
            return repr(value)
        if kind == 'r8':
            return repr(value)
        if kind == 'i1':
            return str(value)
        if kind == 'i8':
            return str(value)
        if kind == 'i4':
            return str(value)
        return str(value)

    def annotate_instructions(self, instructions):
        """Return the instruction records with an added ``text`` field
        (self-describing, deterministically JSON-serializable).  Prefix
        opcodes are marked ``[prefix]`` and never presented as ordinary
        executable instructions."""
        annotated = []
        for insn in instructions:
            record = dict(insn)
            operand = self._operand_text(insn['name'], insn['operand'])
            text = insn['name']
            if insn.get('flow') == 'prefix':
                text = f'[prefix] {text}'
            if insn.get('target') is not None:
                if isinstance(insn['target'], list):
                    shown = ', '.join(f'0x{t:X}'
                                      for t in insn['target'][:8])
                    suffix = f' +{len(insn["target"]) - 8} more' \
                        if len(insn['target']) > 8 else ''
                    text += f' -> [{shown}{suffix}]'
                else:
                    text += f' -> 0x{insn["target"]:X}'
            elif operand is not None:
                if isinstance(operand, list):
                    shown = ', '.join(f'0x{t:X}' for t in operand[:8])
                    suffix = f' +{len(operand) - 8} more' \
                        if len(operand) > 8 else ''
                    text += f' -> [{shown}{suffix}]'
                else:
                    text += f' {operand}'
            if insn.get('error') is not None:
                text += f' [{insn["error"]}]'
            record['text'] = text
            annotated.append(record)
        return annotated

    def method_il_summary(self, il: bytes, max_tokens=24) -> List[str]:
        """Short, bounded opcode summary with resolved member names and
        computed branch targets.  When decoding fails mid-body the summary
        contains a stable error marker instead of fake instructions."""
        decoded = self.decode_il(il)
        lines: List[str] = []
        for instruction in decoded['instructions']:
            if len(lines) >= max_tokens:
                lines.append(f'...({len(il) - instruction["offset"]} '
                             f'bytes more)')
                break
            name = instruction['name']
            if instruction['error'] is not None:
                lines.append(f'{name} [{instruction["error"]}]'
                             f' @0x{instruction["offset"]:X}')
                if instruction['error'] in ('truncated_operand',
                                            'truncated_prefix',
                                            'reserved_opcode'):
                    break
                continue
            operand = self._operand_text(name, instruction['operand'])
            text = name
            if instruction['flow'] == 'prefix':
                text = f'[prefix] {text}'
            if name == 'switch' and isinstance(instruction.get('target'),
                                               list):
                shown = ', '.join(f'0x{t:X}'
                                  for t in instruction['target'][:8])
                suffix = f' +{len(instruction["target"]) - 8} more' \
                    if len(instruction['target']) > 8 else ''
                text += f' -> [{shown}{suffix}]'
            elif isinstance(instruction['target'], int):
                text += f' -> 0x{instruction["target"]:X}'
            elif operand is not None:
                text += f' {operand}'
            lines.append(text)
        if not decoded['decode_complete'] and \
                decoded['first_error_offset'] is not None:
            marker = f'...decode_incomplete @0x' \
                     f'{decoded["first_error_offset"]:X}'
            # only add the marker when the failing instruction itself was
            # not already emitted (bounded summaries can cut it off)
            error_line = f'@0x{decoded["first_error_offset"]:X}'
            if not any(error_line in line for line in lines):
                lines.append(marker)
        return lines


def _read_metadata_streams(data, offset):
    """Parse the metadata root; return (streams dict, heap sizes)."""
    signature, major, minor = struct.unpack_from('<IHH', data, offset)
    if signature != 0x424A5342:
        raise ValueError('not a valid metadata root (BSJB missing)')
    pos = offset + 8
    reserved = struct.unpack_from('<I', data, pos)[0]
    pos += 4
    version_length = struct.unpack_from('<I', data, pos)[0]
    pos += 4
    if pos + version_length > len(data):
        raise ValueError('metadata version string truncated')
    pos += version_length
    # padding to a 4-byte boundary RELATIVE to the metadata root start
    pos = offset + ((pos - offset + 3) & ~3)
    flags, num_streams = struct.unpack_from('<HH', data, pos)
    pos += 4
    streams = {}
    for _ in range(num_streams):
        stream_offset, stream_size = struct.unpack_from('<II', data, pos)
        pos += 8
        end = data.find(b'\x00', pos)
        if end < 0:
            raise ValueError('metadata stream name not terminated')
        name = data[pos:end].decode('utf-8', errors='replace')
        pos = offset + ((end + 1 - offset + 3) & ~3)
        streams[name] = (offset + stream_offset, stream_size)
    return streams


def _read_index_impl(data, pos, size):
    if size == 2:
        return struct.unpack_from('<H', data, pos)[0], pos + 2
    return struct.unpack_from('<I', data, pos)[0], pos + 4


def _read_tables(assembly, data, stream_offset):
    """Generic ECMA-335 table parser driven by column descriptors."""
    if stream_offset + 24 > len(data):
        raise ValueError('#~ stream truncated')
    reserved, major, minor, heap_sizes = struct.unpack_from(
        '<IBBB', data, stream_offset)
    pos = stream_offset + 8
    valid, sorted_mask = struct.unpack_from('<QQ', data, pos)
    pos += 16
    counts = {}
    for table_id in range(64):
        if valid & (1 << table_id):
            count = struct.unpack_from('<I', data, pos)[0]
            pos += 4
            counts[table_id] = count
            assembly.table_row_counts[table_id] = count

    strings_size = 4 if heap_sizes & 0x01 else 2
    guid_size = 4 if heap_sizes & 0x02 else 2
    blob_size = 4 if heap_sizes & 0x04 else 2

    def idx_size(table_id):
        return 4 if counts.get(table_id, 0) >= 65536 else 2

    def coded_size(bits, tables):
        max_rows = max((counts.get(table, 0) for table in tables),
                       default=0)
        return 4 if max_rows >= (1 << (16 - bits)) else 2

    def read_any(kind):
        """Read one column value and advance `pos` past it."""
        nonlocal pos
        if kind == 'S':
            size = strings_size
            value, pos = _read_index_impl(data, pos, size)
            return value
        if kind == 'B':
            size = blob_size
            value, pos = _read_index_impl(data, pos, size)
            return value
        if kind == 'G':
            size = guid_size
            value, pos = _read_index_impl(data, pos, size)
            return value
        if kind.startswith('R'):
            size = idx_size(int(kind[1:]))
            value, pos = _read_index_impl(data, pos, size)
            return value
        if kind.startswith('C'):
            bits, tables = _coded_tables(kind)
            size = coded_size(bits, tables)
            value, pos = _read_index_impl(data, pos, size)
            return (value >> bits), tables[value & ((1 << bits) - 1)]
        if kind == 'U1':
            value = data[pos]
            pos += 1
            return value
        if kind == 'U2':
            value, pos = struct.unpack_from('<H', data, pos), pos + 2
            return value[0]
        if kind == 'U4':
            value, pos = struct.unpack_from('<I', data, pos), pos + 4
            return value[0]
        raise ValueError(f'unknown column kind {kind!r}')

    for table_id in range(64):
        if not (valid & (1 << table_id)):
            continue
        columns = _COLUMNS.get(table_id)
        if columns is None:
            raise ValueError(
                f'unsupported metadata table 0x{table_id:02X} '
                f'({_TABLE_IDS.get(table_id)})')
        rows = []
        for _ in range(counts[table_id]):
            row = []
            for column in columns:
                row.append(read_any(column))
            rows.append(row)
        assembly.tables[table_id] = rows
    return assembly


def read_dotnet_assembly(path: str) -> DotNetAssembly:
    """Parse a .NET assembly read-only; returns evidence-level structures."""
    with open(path, 'rb') as handle:
        data = handle.read()
    sha256 = hashlib.sha256(data).hexdigest().upper()

    if data[0:2] != b'MZ':
        raise ValueError('not a PE file')
    e_lfanew = struct.unpack_from('<I', data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b'PE\x00\x00':
        raise ValueError('PE signature missing')
    pos = e_lfanew + 4
    machine, num_sections = struct.unpack_from('<HH', data, pos)
    pos += 16
    size_optional = struct.unpack_from('<H', data, pos)[0]
    pos += 2 + 2  # sizeOfOptionalHeader + characteristics
    magic = struct.unpack_from('<H', data, pos)[0]
    if magic == 0x20B:  # PE32+
        directories_offset = pos + 112
    elif magic == 0x10B:  # PE32
        directories_offset = pos + 96
    else:
        raise ValueError(f'unsupported optional header magic 0x{magic:X}')
    cli_rva, cli_size = struct.unpack_from(
        '<II', data, directories_offset + 14 * 8)

    # section headers
    sections = []
    sec_pos = e_lfanew + 24 + size_optional
    for _ in range(num_sections):
        name = data[sec_pos:sec_pos + 8].rstrip(b'\x00').decode(
            'ascii', errors='replace')
        virtual_size, virtual_address = struct.unpack_from(
            '<II', data, sec_pos + 8)
        raw_size, raw_offset = struct.unpack_from('<II', data, sec_pos + 16)
        sections.append((virtual_address, virtual_size, raw_offset, name))
        sec_pos += 40

    assembly = DotNetAssembly(path, sha256)
    assembly._sections = [(va, vs, ro) for va, vs, ro, _ in sections]

    def rva_to_offset(rva):
        for va, vs, ro, _ in sections:
            if va <= rva < va + vs:
                return ro + (rva - va)
        return None

    cli_offset = rva_to_offset(cli_rva)
    if cli_offset is None:
        raise ValueError('CLI header RVA outside sections')
    # IMAGE_COR20_HEADER: cb(4) MajorRuntimeVersion(2) MinorRuntimeVersion(2)
    # MetaData RVA(4) MetaData Size(4) Flags(4) EntryPointToken(4) ...
    metadata_rva, metadata_size = struct.unpack_from(
        '<II', data, cli_offset + 8)
    meta_offset = rva_to_offset(metadata_rva)
    if meta_offset is None:
        raise ValueError('CLI metadata RVA outside sections')
    streams = _read_metadata_streams(data, meta_offset)
    for name in ('#Strings', '#Blob', '#~'):
        if name not in streams:
            raise ValueError(f'metadata stream {name} missing')
    strings_start, strings_size = streams['#Strings']
    blob_start, blob_size = streams['#Blob']
    if strings_start + strings_size > len(data) or \
            blob_start + blob_size > len(data):
        raise ValueError('metadata heap out of file bounds')
    assembly.strings = data[strings_start:strings_start + strings_size]
    assembly.blobs = data[blob_start:blob_start + blob_size]
    _read_tables(assembly, data, streams['#~'][0])

    for row in assembly.table(T_ASSEMBLY):
        assembly.assembly_name = assembly.string(row[7])
        assembly.assembly_version = (
            f'{row[1]}.{row[2]}.{row[3]}.{row[4]}')
    return assembly


# ---------------------------------------------------------------------------
# Token normalization
# ---------------------------------------------------------------------------

def _token_parts(token) -> Tuple[int, int]:
    """Normalize a metadata token to ``(table_id, row_id)``.

    Accepted spellings (all equivalent for the same token):

    * an integer, e.g. ``0x04000002``;
    * an explicit-hex string, e.g. ``'0x04000002'``;
    * a zero-padded 8-digit unprefixed hex string, e.g. ``'04000002'``;
    * a decimal string, e.g. ``'67108866'`` (= 0x04000002).

    Unprefixed-string rules (documented, deterministic):

    1. ``0x``/``0X`` prefix          -> hex;
    2. any ``[a-fA-F]`` character    -> hex (only possible for hex);
    3. exactly 8 characters, all hex
       digits, FIRST character '0'   -> hex (the canonical zero-padded
                                        token spelling, e.g. '04000002');
    4. otherwise                     -> decimal.

    Ambiguity note: an 8-digit all-digit string NOT starting with ``0`` is
    ALWAYS read as DECIMAL, even when its hex reading would also look like
    a token (e.g. ``'20000002'`` -> decimal 20000002, NOT 0x20000002).
    To spell such a token as hex without ``0x``, zero-pad it to 8 digits
    (e.g. ``'02000002'``).  The four canonical spellings above all resolve
    ``0x04000002`` to ``(table_id=0x04, row_id=2)``.

    Raises ValueError for malformed input or out-of-range values.
    """
    if isinstance(token, bool):
        raise ValueError('token must be an integer or a token string')
    if isinstance(token, int):
        value = token
    elif isinstance(token, str):
        text = token.strip()
        if not text:
            raise ValueError('empty token')
        lowered = text.lower()
        if lowered.startswith('0x'):
            value = int(text, 16)
        elif any(character in text for character in 'abcdefABCDEF'):
            value = int(text, 16)
        elif len(text) == 8 and text[0] == '0' and \
                all(character in '0123456789abcdefABCDEF'
                    for character in text):
            value = int(text, 16)
        else:
            value = int(text, 10)
    else:
        raise ValueError(
            f'unsupported token type {type(token).__name__}')
    if value < 0 or value > 0xFFFFFFFF:
        raise ValueError('token out of 32-bit range')
    return (value >> 24) & 0xFF, value & 0xFFFFFF


def _token_error(payload, message, table_id=None,
                 error='invalid_token'):
    result = {
        'error': error,
        'message': message,
        'writers': [],
        'writer_count': 0,
        'callers': [],
        'caller_count': 0,
        'method_bodies_total': 0,
        'method_bodies_unparsed': 0,
        'method_bodies_decode_incomplete': 0,
        'scan_complete': False,
    }
    if table_id is not None:
        result['received_table_id'] = table_id
        result['received_table_name'] = _TABLE_IDS.get(table_id, '?')
    return result


def _decode_method_body(assembly, rva):
    """(il, read_error, decoded_dict_or_None)."""
    il, error = assembly.method_body_checked(rva)
    if il is None:
        return il, error, None
    return il, None, assembly.decode_il(il)


def methods_named(assembly: DotNetAssembly, type_name: str,
                  method_name: str) -> List[dict]:
    """ALL methods with the given name inside the TypeDef's own method list
    range (one record per overload), in token order."""
    type_info = assembly.find_type_row(type_name)
    if type_info is None or type_info.get('ambiguous'):
        return []
    _, _, method_start, method_end = assembly.type_ranges(
        type_info['index'])
    matches = []
    for index in range(method_start, method_end):
        method_row = assembly.row(T_METHODDEF, index)
        if method_row is None:
            break
        if assembly.string(method_row[3]) != method_name:
            continue
        rva = method_row[0]
        il, error, decoded = _decode_method_body(assembly, rva)
        digest = None
        il_bytes = None
        instructions = []
        decode_complete = None
        decode_errors = []
        first_error_offset = None
        il_body_status = 'read_error' if error else None
        if il is not None:
            digest = hashlib.sha256(il).hexdigest().upper()
            il_bytes = len(il)
            decode_complete = decoded['decode_complete']
            decode_errors = decoded['decode_errors']
            first_error_offset = decoded['first_error_offset']
            il_body_status = 'ok' if decode_complete else 'decode_error'
            instructions = assembly.annotate_instructions(
                decoded['instructions'])
        matches.append({
            'type': f'{type_info["namespace"]}.{type_info["name"]}'
            if type_info['namespace'] else type_info['name'],
            'method': method_name,
            'rva': rva,
            'token': f'0x{(T_METHODDEF << 24) | index:08X}',
            'il_body_sha256': digest,
            'il_body_bytes': il_bytes,
            'il_error': error,
            'il_body_status': il_body_status,
            'decode_complete': decode_complete,
            'decode_errors': decode_errors,
            'first_error_offset': first_error_offset,
            'instructions': instructions,
        })
    return matches


def method_evidence(assembly: DotNetAssembly, type_name: str,
                    method_name: str) -> Optional[dict]:
    """Evidence for one method of a type: RVA, token, IL hash, summary.

    The method is searched ONLY inside the TypeDef's own method list range
    (FieldList/MethodList of this TypeDef up to the next TypeDef).  Returns
    None when the type is missing/ambiguous or the method does not exist.
    The status combines the body-read state (``il_error``) with the IL
    decode state (``il_body_status`` / ``decode_complete`` /
    ``decode_errors`` / ``first_error_offset``).
    """
    type_info = assembly.find_type_row(type_name)
    if type_info is None or type_info.get('ambiguous'):
        return None
    full_type = f'{type_info["namespace"]}.{type_info["name"]}' \
        if type_info['namespace'] else type_info['name']
    _, _, method_start, method_end = assembly.type_ranges(
        type_info['index'])
    for index in range(method_start, method_end):
        method_row = assembly.row(T_METHODDEF, index)
        if method_row is None:
            break
        if assembly.string(method_row[3]) != method_name:
            continue
        rva = method_row[0]
        il, error, decoded = _decode_method_body(assembly, rva)
        digest = None
        summary = []
        il_bytes = None
        decode_complete = None
        decode_errors = []
        first_error_offset = None
        il_body_status = 'read_error' if error else None
        if il is not None:
            digest = hashlib.sha256(il).hexdigest().upper()
            summary = assembly.method_il_summary(il)
            il_bytes = len(il)
            decode_complete = decoded['decode_complete']
            decode_errors = decoded['decode_errors']
            first_error_offset = decoded['first_error_offset']
            il_body_status = 'ok' if decode_complete else 'decode_error'
        return {
            'type': full_type,
            'method': method_name,
            'rva': rva,
            'token': f'0x{(T_METHODDEF << 24) | index:08X}',
            'il_body_sha256': digest,
            'il_body_bytes': il_bytes,
            'il_error': error,
            'il_body_status': il_body_status,
            'decode_complete': decode_complete,
            'decode_errors': decode_errors,
            'first_error_offset': first_error_offset,
            'il_summary': summary,
        }
    return None


def field_evidence(assembly: DotNetAssembly, type_name: str,
                   field_name: str) -> Optional[dict]:
    """Evidence for one field of a type.

    The Field table stores a #Blob HEAP INDEX in its Signature column; the
    signature bytes are fetched through ``assembly.blob(index)`` before the
    hash is computed.  When the blob index is present but cannot be
    resolved (missing / malformed / out of bounds), ``signature_error`` is
    set and NO empty-byte hash is fabricated.  The field is searched only
    inside the TypeDef's own field list range.  Returns None when
    missing/ambiguous.
    """
    type_info = assembly.find_type_row(type_name)
    if type_info is None or type_info.get('ambiguous'):
        return None
    full_type = f'{type_info["namespace"]}.{type_info["name"]}' \
        if type_info['namespace'] else type_info['name']
    field_start, field_end, _, _ = assembly.type_ranges(type_info['index'])
    for index in range(field_start, field_end):
        field_row = assembly.row(T_FIELD, index)
        if field_row is None:
            break
        if assembly.string(field_row[1]) != field_name:
            continue
        signature = assembly.blob(field_row[2])
        result = {
            'type': full_type,
            'field': field_name,
            'token': f'0x{(T_FIELD << 24) | index:08X}',
            'flags': field_row[0],
            'signature_blob_sha256': None,
            'signature_blob_bytes': None,
            'signature_error': None,
        }
        if signature is not None:
            result['signature_blob_sha256'] = hashlib.sha256(
                signature).hexdigest().upper()
            result['signature_blob_bytes'] = len(signature)
        else:
            result['signature_error'] = 'blob_unresolvable'
        return result
    return None


def methods_writing_field(assembly: DotNetAssembly,
                          field_token) -> dict:
    """Reverse query: every method whose body writes the given field token
    with ``stfld`` / ``stsfld``.

    ``field_token`` may be an integer (``0x04000002``), a ``0x04000002``
    hex string or a plain decimal/hex string — all spellings normalize to
    the same (table_id, row_id) and are equivalent.  A token whose table id
    is not Field is rejected with ``error='invalid_token_table'``.

    Returns per writer: full type name, method name and token, body
    SHA-256, the IL offsets of the write instructions and a bounded context
    summary, plus the writer's own decode state.  Bodies that could not be
    read are counted in ``method_bodies_unparsed`` and bodies that decoded
    incompletely in ``method_bodies_decode_incomplete``; ``scan_complete``
    is true only when every body was read AND decoded completely.  A zero
    writer list is only conclusive when ``scan_complete`` is true.
    """
    try:
        table_id, row_id = _token_parts(field_token)
    except ValueError as exc:
        return _token_error({}, f'{exc}')
    if table_id != T_FIELD:
        return _token_error({}, 'not a Field token',
                            table_id=table_id,
                            error='invalid_token_table')
    target = (T_FIELD << 24) | row_id
    assembly._build_ranges()
    writers: List[dict] = []
    unparsed = 0
    decode_incomplete = 0
    bodies = 0
    for index, method_row in enumerate(assembly.table(T_METHODDEF), 1):
        rva = method_row[0]
        if rva == 0:
            continue
        bodies += 1
        il, error, decoded = _decode_method_body(assembly, rva)
        if il is None:
            unparsed += 1
            continue
        if not decoded['decode_complete']:
            decode_incomplete += 1
        digest = hashlib.sha256(il).hexdigest().upper()
        instructions = decoded['instructions']
        write_records = []
        context_lines: List[str] = []
        for write_index, instruction in enumerate(instructions):
            if instruction['name'] not in ('stfld', 'stsfld') or \
                    instruction['operand'] is None or \
                    instruction['operand'][1] != target:
                continue
            constant, constant_src = _preceding_constant(instructions,
                                                         write_index)
            write_records.append({
                'offset': instruction['offset'],
                'opcode': instruction['name'],
                'constant_bool': constant,
                'constant_source_instruction': constant_src,
            })
        if not write_records:
            continue
        owner = assembly._method_owner.get(index)
        if owner:
            full_type = f'{owner[0]}.{owner[1]}' if owner[0] else owner[1]
        else:
            full_type = '<unknown>'
        for write in write_records:
            write_index = next(
                (i for i, insn in enumerate(instructions)
                 if insn['offset'] == write['offset']), None)
            if write_index is None:
                continue
            start = max(0, write_index - 4)
            end = min(len(instructions), write_index + 5)
            for insn in instructions[start:end]:
                if len(context_lines) >= 24:
                    break
                operand = assembly._operand_text(
                    insn['name'], insn['operand'])
                text = f'0x{insn["offset"]:X} {insn["name"]}'
                if insn['flow'] == 'prefix':
                    text = f'0x{insn["offset"]:X} [prefix] {insn["name"]}'
                if insn.get('target') is not None:
                    text += f' -> 0x{insn["target"]:X}'
                elif operand is not None:
                    text += f' {operand}'
                if insn.get('error') is not None:
                    text += f' [{insn["error"]}]'
                context_lines.append(text)
        writers.append({
            'type': full_type,
            'method': assembly.string(method_row[3]),
            'token': f'0x{(T_METHODDEF << 24) | index:08X}',
            'il_body_sha256': digest,
            'il_error': None,
            'il_body_status': 'ok' if decoded['decode_complete']
            else 'decode_error',
            'decode_complete': decoded['decode_complete'],
            'decode_errors': decoded['decode_errors'],
            'first_error_offset': decoded['first_error_offset'],
            'writes': write_records,
            'context': context_lines,
        })
    writers.sort(key=lambda item: item['token'])
    return {
        'field_token': f'0x{target:08X}',
        'writers': writers,
        'writer_count': len(writers),
        'method_bodies_total': bodies,
        'method_bodies_unparsed': unparsed,
        'method_bodies_decode_incomplete': decode_incomplete,
        'scan_complete': unparsed == 0 and decode_incomplete == 0,
    }


def _preceding_constant(instructions, write_index):
    """Nearest ``ldc.i4.{0,1,m1,s}`` before ``write_index`` within the same
    basic block (no branch, ret or jump in between).  Returns
    ``(constant_bool_or_None, source_offset_or_None)``."""
    for i in range(write_index - 1, max(-1, write_index - 6), -1):
        insn = instructions[i]
        if insn['name'] == 'nop':
            continue
        if insn['name'] in ('ret', 'br.s', 'br', 'leave', 'leave.s',
                            'brtrue.s', 'brfalse.s', 'beq.s', 'bne.un.s',
                            'blt.s', 'bge.s', 'bgt.s', 'ble.s',
                            'brtrue', 'brfalse', 'beq', 'bne.un', 'blt',
                            'bge', 'bgt', 'ble', 'switch', 'throw',
                            'endfinally'):
            return None, None
        if insn['name'] in ('ldc.i4.0', 'ldc.i4.1', 'ldc.i4.m1',
                            'ldc.i4.s'):
            if insn['name'] == 'ldc.i4.0':
                return False, insn['offset']
            if insn['name'] == 'ldc.i4.1':
                return True, insn['offset']
            value = insn['operand'][1] if insn['operand'] is not None \
                else None
            return (value != 0, insn['offset']) if value is not None \
                else (None, None)
        if insn['name'] == 'ldc.i4':
            value = insn['operand'][1] if insn['operand'] is not None \
                else None
            return (value != 0, insn['offset']) if value is not None \
                else (None, None)
        return None, None
    return None, None


def methods_calling_method(assembly: DotNetAssembly,
                           method_token) -> dict:
    """Reverse query: every method whose body calls the given method token
    via ``call`` / ``callvirt``.

    ``method_token`` may be an integer, a ``0x060000XX`` hex string or a
    plain decimal/hex string (all equivalent).  A token whose table id is
    not MethodDef is rejected with ``error='invalid_token_table'``.

    Returns per caller: full type name, method name, token, the IL offset
    of the call site and the caller's decode state.  ``scan_complete`` is
    true only when every body was read AND decoded completely.
    """
    try:
        table_id, row_id = _token_parts(method_token)
    except ValueError as exc:
        return _token_error({}, f'{exc}')
    if table_id != T_METHODDEF:
        return _token_error({}, 'not a MethodDef token',
                            table_id=table_id,
                            error='invalid_token_table')
    target = (T_METHODDEF << 24) | row_id
    assembly._build_ranges()
    callers: List[dict] = []
    unparsed = 0
    decode_incomplete = 0
    bodies = 0
    for index, method_row in enumerate(assembly.table(T_METHODDEF), 1):
        rva = method_row[0]
        if rva == 0:
            continue
        bodies += 1
        il, error, decoded = _decode_method_body(assembly, rva)
        if il is None:
            unparsed += 1
            continue
        if not decoded['decode_complete']:
            decode_incomplete += 1
        for insn in decoded['instructions']:
            if insn['name'] in ('call', 'callvirt') and \
                    insn['operand'] is not None and \
                    insn['operand'][1] == target:
                owner = assembly._method_owner.get(index)
                full_type = f'{owner[0]}.{owner[1]}' if owner and owner[0] \
                    else (owner[1] if owner else '<unknown>')
                callers.append({
                    'type': full_type,
                    'method': assembly.string(method_row[3]),
                    'token': f'0x{(T_METHODDEF << 24) | index:08X}',
                    'call_site_offset': insn['offset'],
                    'decode_complete': decoded['decode_complete'],
                    'decode_errors': decoded['decode_errors'],
                    'first_error_offset': decoded['first_error_offset'],
                })
                break
    callers.sort(key=lambda item: item['token'])
    return {
        'method_token': f'0x{target:08X}',
        'callers': callers,
        'caller_count': len(callers),
        'method_bodies_total': bodies,
        'method_bodies_unparsed': unparsed,
        'method_bodies_decode_incomplete': decode_incomplete,
        'scan_complete': unparsed == 0 and decode_incomplete == 0,
    }
