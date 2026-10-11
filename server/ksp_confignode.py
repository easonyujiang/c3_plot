"""
KSP ConfigNode parser — dependency-free, deterministic, read-only.
===================================================================

Parses KSP .cfg / .craft ConfigNode text into a structural tree:

    ConfigNode (name, ordered values with duplicate keys, ordered children,
                start_line / end_line)
    ConfigValue (key, value, line)

Supports UTF-8 BOM, CRLF and LF, blank lines, indentation, tabs, ``//``
line comments, arbitrary ``{}`` nesting, duplicate fields (``sym``,
``attN``, ...) and duplicate child nodes.  Values may contain spaces,
commas and any text after ``=`` (rest of the line).

Format errors raise :class:`CraftParseError` with a stable ``code``, a
Chinese ``message``, ``line`` / ``column`` and a ``node_path``.  Limits
protect against pathological inputs: maximum nesting depth, maximum node
count and maximum input size.  Parsing never mutates the input text and
is fully deterministic.

This module must not depend on Flask, SQLite, models or the frontend.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

MAX_INPUT_CHARS = 25 * 1024 * 1024   # same size gate as the import layer
MAX_DEPTH = 64
MAX_NODES = 50000

_ERROR_CODES = (
    'unexpected_eof',
    'unexpected_close',
    'missing_node_name',
    'invalid_assignment',
    'depth_exceeded',
    'node_limit_exceeded',
    'input_too_large',
    'dangling_node_name',
    'invalid_node_declaration',
    'invalid_input_type',
    'invalid_encoding',
    'unexpected_character',
)


class ParseOptions:
    """Parser behaviour switches.  The default is the strict craft mode:

    * anonymous ``{ ... }`` nodes are rejected (``missing_node_name``);
    * limits are the craft limits (25 MB / 50,000 nodes / depth 64).

    The KSP install index uses a permissive variant
    (:data:`server.ksp_install_index.CACHE_PARSE_OPTIONS`) that allows
    anonymous nodes (ModuleManager.ConfigCache contains them) and raises
    the node / size limits.  Changing options never loosens the default
    behaviour used for ``.craft`` files.
    """

    __slots__ = ('allow_anonymous_nodes', 'max_depth', 'max_nodes',
                 'max_input_chars')

    def __init__(self, allow_anonymous_nodes=False, max_depth=None,
                 max_nodes=None, max_input_chars=None):
        self.allow_anonymous_nodes = allow_anonymous_nodes
        self.max_depth = MAX_DEPTH if max_depth is None else max_depth
        self.max_nodes = MAX_NODES if max_nodes is None else max_nodes
        self.max_input_chars = (MAX_INPUT_CHARS if max_input_chars is None
                                else max_input_chars)

    def __repr__(self):
        return (f'ParseOptions(allow_anonymous_nodes='
                f'{self.allow_anonymous_nodes}, max_depth={self.max_depth}, '
                f'max_nodes={self.max_nodes}, '
                f'max_input_chars={self.max_input_chars})')


class CraftParseError(Exception):
    """Stable, locatable ConfigNode format error.

    Construction never raises (e.g. it must not turn into ValueError);
    the parser itself only ever emits codes from ``_ERROR_CODES``.
    """

    def __init__(self, code, message, line=None, column=None, node_path=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.line = line
        self.column = column
        self.node_path = node_path

    def __repr__(self):
        location = ''
        if self.line is not None:
            location = f' at line {self.line}'
            if self.column is not None:
                location += f', column {self.column}'
        if self.node_path:
            location += f' (node {self.node_path})'
        return f'CraftParseError[{self.code}]{location}: {self.message}'


class ConfigValue:
    """One ``key = value`` assignment, preserving order and duplicates."""

    __slots__ = ('key', 'value', 'line')

    def __init__(self, key, value, line):
        self.key = key
        self.value = value
        self.line = line

    def __repr__(self):
        return f'ConfigValue({self.key!r} = {self.value!r} @ {self.line})'


class ConfigNode:
    """One ``name { ... }`` node with ordered values and children."""

    __slots__ = ('name', 'values', 'children', 'start_line', 'end_line',
                 'parent')

    def __init__(self, name, start_line, parent=None):
        self.name = name
        self.values: List[ConfigValue] = []
        self.children: List[ConfigNode] = []
        self.start_line = start_line
        self.end_line = start_line
        self.parent = parent

    # ---- value access ------------------------------------------------------

    def value(self, key, default=None):
        """First value for ``key`` (or ``default`` when absent)."""
        for entry in self.values:
            if entry.key == key:
                return entry.value
        return default

    def values_named(self, key) -> List[str]:
        """All values for ``key``, in file order (duplicates preserved)."""
        return [entry.value for entry in self.values if entry.key == key]

    def all_values(self) -> List[Tuple[str, str]]:
        """(key, value) pairs in file order, duplicates preserved."""
        return [(entry.key, entry.value) for entry in self.values]

    # ---- child access ------------------------------------------------------

    def child(self, name) -> Optional['ConfigNode']:
        for child in self.children:
            if child.name == name:
                return child
        return None

    def children_named(self, name) -> List['ConfigNode']:
        return [child for child in self.children if child.name == name]

    # ---- introspection -----------------------------------------------------

    def to_dict(self):
        return {
            'name': self.name,
            'start_line': self.start_line,
            'end_line': self.end_line,
            'values': [[entry.key, entry.value] for entry in self.values],
            'children': [child.to_dict() for child in self.children],
        }

    def __repr__(self):
        return (f'ConfigNode({self.name!r}, {len(self.values)} values, '
                f'{len(self.children)} children, '
                f'lines {self.start_line}-{self.end_line})')


def _node_path(stack):
    parts = []
    for position in range(1, len(stack)):
        node = stack[position]
        parent = stack[position - 1]
        index = 0
        for sibling in parent.children:
            if sibling is node:
                break
            index += 1
        parts.append(f'{node.name or "?"}[{index}]')
    return '/'.join(parts)


def _split_inline_comment(raw):
    """Split ``//`` off a value only in a legal comment position.

    A comment must start at the beginning of the value or immediately after
    whitespace, so ``url = http://example/path`` keeps the full value while
    ``istg = 1 // inline comment`` still strips the comment.
    """
    position = 0
    while True:
        found = raw.find('//', position)
        if found < 0:
            return raw
        if found == 0 or raw[found - 1] in ' \t':
            return raw[:found]
        position = found + 2


def parse(text, options=None) -> ConfigNode:
    """Parse ConfigNode text and return the document root node.

    The document root has an empty ``name``; its ``children`` are the
    top-level nodes (e.g. ``PART`` in .craft files) and its ``values``
    hold top-level assignments (e.g. ``ship = ...``).

    Node-declaration state machine: once a node name has been read, only
    whitespace, ``//`` comments and ``{`` may follow.  An assignment,
    another node name, ``}`` or EOF after a name is a stable
    :class:`CraftParseError`.

    ``options`` defaults to the strict craft :class:`ParseOptions`
    (anonymous ``{ ... }`` rejected, 25 MB / 50,000 nodes).  The KSP
    install index passes a permissive option set.
    """
    if options is None:
        options = ParseOptions()
    if isinstance(text, str):
        if text.startswith('\ufeff'):
            text = text[1:]
    elif isinstance(text, bytes):
        try:
            text = text.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise CraftParseError(
                'invalid_encoding', '输入不是有效的 UTF-8 编码')
    else:
        raise CraftParseError(
            'invalid_input_type',
            f'不支持的类型 {type(text).__name__}，需要 str 或 bytes')
    if len(text) > options.max_input_chars:
        raise CraftParseError(
            'input_too_large',
            f'输入超过大小限制（{options.max_input_chars} 字符）')

    root = ConfigNode('', 1)
    stack = [root]
    node_count = 1
    pending_name = None
    pending_line = 1
    pending_column = 1
    line = 1
    line_start = 0
    i = 0
    n = len(text)

    while i < n:
        char = text[i]
        if char == '\n':
            line += 1
            line_start = i + 1
            i += 1
            continue
        if char in ' \t\r':
            i += 1
            continue
        if char == '/' and i + 1 < n and text[i + 1] == '/':
            # line comment: skip to (but not past) the newline
            while i < n and text[i] != '\n':
                i += 1
            continue
        if char == '{':
            if not pending_name:
                if not options.allow_anonymous_nodes:
                    raise CraftParseError(
                        'missing_node_name', '左花括号前缺少节点名称',
                        line=line, column=i - line_start + 1,
                        node_path=_node_path(stack))
            parent = stack[-1]
            if len(stack) >= options.max_depth:
                raise CraftParseError(
                    'depth_exceeded',
                    f'嵌套深度超过限制（{options.max_depth}）',
                    line=line, column=i - line_start + 1,
                    node_path=_node_path(stack))
            node_count += 1
            if node_count > options.max_nodes:
                raise CraftParseError(
                    'node_limit_exceeded',
                    f'节点数量超过限制（{options.max_nodes}）',
                    line=line, column=i - line_start + 1,
                    node_path=_node_path(stack))
            if pending_name:
                child = ConfigNode(pending_name, pending_line, parent=parent)
                pending_name = None
            else:
                # anonymous node: allowed only in permissive index mode
                child = ConfigNode('', line, parent=parent)
            parent.children.append(child)
            stack.append(child)
            i += 1
            continue
        if char == '}':
            if pending_name:
                raise CraftParseError(
                    'dangling_node_name',
                    f'节点名 {pending_name!r} 后出现右花括号（节点名后应紧跟 {{）',
                    line=pending_line, column=pending_column,
                    node_path=_node_path(stack))
            if len(stack) <= 1:
                raise CraftParseError(
                    'unexpected_close', '多余的右花括号',
                    line=line, column=i - line_start + 1,
                    node_path=_node_path(stack))
            node = stack.pop()
            node.end_line = line
            i += 1
            continue
        if char == '=':
            if pending_name:
                raise CraftParseError(
                    'invalid_node_declaration',
                    f'节点名 {pending_name!r} 后不允许出现赋值'
                    '（节点名后只允许空白、注释和 {）',
                    line=pending_line, column=pending_column,
                    node_path=_node_path(stack))
            raise CraftParseError(
                'invalid_assignment', '等号前缺少键名',
                line=line, column=i - line_start + 1,
                node_path=_node_path(stack))

        # identifier: a node name (followed by '{') or an assignment key
        token_start = i
        while i < n and text[i] not in ' \t\r\n{}=':
            i += 1
        token = text[token_start:i]
        if not token:
            raise CraftParseError(
                'unexpected_character', f'无法识别的字符 {char!r}',
                line=line, column=i - line_start + 1,
                node_path=_node_path(stack))

        # look ahead (skipping spaces) for '='
        j = i
        while j < n and text[j] in ' \t\r':
            j += 1
        if j < n and text[j] == '=':
            if pending_name:
                raise CraftParseError(
                    'invalid_node_declaration',
                    f'节点名 {pending_name!r} 后出现赋值'
                    '（节点名后只允许空白、注释和 {）',
                    line=line, column=token_start - line_start + 1,
                    node_path=_node_path(stack))
            i = j + 1
            value_start = i
            while i < n and text[i] not in '\r\n':
                i += 1
            raw = _split_inline_comment(text[value_start:i])
            stack[-1].values.append(ConfigValue(token, raw.strip(), line))
        else:
            if pending_name:
                raise CraftParseError(
                    'invalid_node_declaration',
                    f'节点名 {pending_name!r} 后出现另一个名称 {token!r}'
                    '（节点名后只允许空白、注释和 {）',
                    line=line, column=token_start - line_start + 1,
                    node_path=_node_path(stack))
            pending_name = token
            pending_line = line
            pending_column = token_start - line_start + 1

    if pending_name:
        raise CraftParseError(
            'dangling_node_name',
            f'节点名 {pending_name!r} 后文件意外结束（节点名后应紧跟 {{）',
            line=pending_line, column=pending_column,
            node_path=_node_path(stack))
    if len(stack) > 1:
        raise CraftParseError(
            'unexpected_eof',
            f'文件在节点 {stack[-1].name or "?"} 内意外结束',
            line=line, node_path=_node_path(stack))

    root.end_line = line
    return root
