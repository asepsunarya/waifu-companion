"""Small edits to config.yaml by lines: only the targeted values change.

`waifu setup` writes what the owner chose (a mic, Tuya keys, a blaster address, an ntfy topic)
into his config.yaml. A YAML round trip would drop his comments and reorder his keys, and
ruamel, which keeps them, is not installed; so `patch_text` finds each targeted key by its
indentation and replaces only its value, or inserts it beside its siblings, keeping every
other byte, comment, and line ending. What it cannot read for sure (tabs, flow mappings, block
scalars, anchors, duplicated keys) it refuses with `PatchError` rather than guess.

No value is ever put in an error or a log: a local key or the ntfy topic is a secret. Errors
name keys only, and the snippet for pasting by hand hides every secret value.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

ConfigValue = str | int | float | bool | list[str | int] | None

DEVICES = "devices"
# A mapping key at a given column: a plain word or a quoted name, then a colon that ends it.
_KEY = re.compile(r"""(?P<key>[A-Za-z_][A-Za-z0-9_-]*|"[^"]*"|'[^']*')[ \t]*:(?=[ \t]|$)""")
# A block sequence entry: the dash, then (unless the entry starts on the next line) its content.
_DASH = re.compile(r"(?P<indent> *)-(?:(?P<gap>[ \t]+)(?P<rest>.*))?$")
# What PyYAML would read as a line break or refuse inside a double-quoted scalar, escaped instead.
_UNSAFE_IN_QUOTES = re.compile("[\x7f-\x9f  \ud800-\udfff﻿￾￿]")
# A value written like this cannot be replaced on its line: a flow mapping, a block scalar, an
# anchor, an alias, a tag, or a reserved indicator.
_UNPATCHABLE_STARTS = frozenset("{|>&*!%@`")
SNIPPET_INDENT = 2


class PatchError(Exception):
    """config.yaml's layout cannot be patched safely. The message, in Indonesian, names keys and
    never a value."""


@dataclass(frozen=True)
class Change:
    """One value to set. `keys` start at the top level; under `devices` the second key is a
    device's id. `secret` marks a value never shown (a local key)."""

    keys: tuple[str, ...]
    value: ConfigValue = field(repr=False)
    secret: bool = False

    @property
    def path(self) -> str:
        """The key as output and logs name it: `devices[lampu_kamar].tuya.local_key`."""
        return _path(self.keys)


def _path(keys: tuple[str, ...]) -> str:
    if keys[0] == DEVICES and len(keys) > 1:
        return ".".join([f"{DEVICES}[{keys[1]}]", *keys[2:]])
    return ".".join(keys)


def yaml_value(value: ConfigValue) -> str:
    """`value` as YAML that loads back exactly: a string double-quoted (a JSON string is a YAML
    double-quoted scalar), a number bare, a list as a flow list."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(yaml_value(item) for item in value) + "]"
    quoted = json.dumps(value, ensure_ascii=False)
    return _UNSAFE_IN_QUOTES.sub(lambda match: f"\\u{ord(match.group()):04x}", quoted)


def masked(secret: str) -> str:
    """How a secret is shown: its length only."""
    return f"disembunyikan, {len(secret)} karakter"


def snippet(changes: list[Change]) -> str:
    """The changes as YAML to paste into config.yaml by hand, each secret hidden."""
    tree: dict[str, object] = {}
    for change in changes:
        node = tree
        for key in change.keys[:-1]:
            child = node.setdefault(key, {})
            assert isinstance(child, dict)
            node = child
        node[change.keys[-1]] = change
    lines: list[str] = []
    _render(tree, 0, lines, top=True)
    return "".join(f"{line}\n" for line in lines)


def _render(node: dict[str, object], indent: int, lines: list[str], *, top: bool = False) -> None:
    pad = " " * indent
    for key, value in node.items():
        if isinstance(value, Change):
            shown = (
                f'"..."   # disembunyikan ({len(str(value.value))} karakter)'
                if value.secret
                else yaml_value(value.value)
            )
            lines.append(f"{pad}{key}: {shown}")
            continue
        assert isinstance(value, dict)
        lines.append(f"{pad}{key}:")
        if top and key == DEVICES:
            for device_id, device in value.items():
                lines.append(f"{pad}{' ' * SNIPPET_INDENT}- id: {device_id}")
                _render(device, indent + 2 * SNIPPET_INDENT, lines)
        else:
            _render(value, indent + SNIPPET_INDENT, lines)


# --- lines -----------------------------------------------------------------------------------


@dataclass
class _Line:
    body: str  # without its line ending
    ending: str  # "\r\n", "\n", "\r", or "" for a last line without one

    @property
    def indent(self) -> int:
        return len(self.body) - len(self.body.lstrip(" "))

    @property
    def is_content(self) -> bool:
        stripped = self.body.strip()
        return bool(stripped) and not stripped.startswith("#")

    @property
    def dash(self) -> re.Match[str] | None:
        return _DASH.match(self.body) if self.is_content else None


@dataclass(frozen=True)
class _Entry:
    line: int
    key: str
    column: int  # where the key starts
    value_start: int  # just after the colon


@dataclass(frozen=True)
class _Mapping:
    """The entries of a block mapping in `lines[start:end]` at `column`; `item` is the dash line
    of a sequence item whose first key sits after the dash."""

    start: int
    end: int
    column: int
    header: int  # the line of the key that holds it (-1 for the top level)
    item: int | None = None


def _split(text: str) -> list[_Line]:
    """Lines on \\r\\n, \\r, or \\n only (str.splitlines also breaks on characters YAML keeps)."""
    lines = []
    position = 0
    for ending in re.finditer(r"\r\n|\r|\n", text):
        lines.append(_Line(text[position : ending.start()], ending.group()))
        position = ending.end()
    if position < len(text):
        lines.append(_Line(text[position:], ""))
    return lines


def _usual_ending(lines: list[_Line]) -> str:
    return next((line.ending for line in lines if line.ending), "\n")


# --- the patch -------------------------------------------------------------------------------


def patch_text(text: str, changes: list[Change]) -> str:
    """`text` with each change made by lines; raises `PatchError` for a layout it cannot read
    for sure."""
    lines = _split(text)
    for line in lines:
        leading = line.body[: len(line.body) - len(line.body.lstrip(" \t"))]
        if line.is_content and "\t" in leading:
            raise PatchError("config.yaml memakai tab untuk indentasi")
    for change in changes:
        _apply(lines, change)
    return "".join(line.body + line.ending for line in lines)


def _apply(lines: list[_Line], change: Change) -> None:
    keys = change.keys
    root = _Mapping(0, len(lines), 0, -1)
    section = _single(lines, root, keys[0], (keys[0],))
    if section is None:
        if len(keys) != 2:
            raise PatchError(f"bagian {keys[0]} tidak ada")
        _append_section(lines, keys[0])
        section = _single(lines, _Mapping(0, len(lines), 0, -1), keys[0], (keys[0],))
        assert section is not None
    if keys[0] == DEVICES:
        if len(keys) < 3:
            raise PatchError(f"{change.path} bukan kunci di dalam perangkat")
        mapping = _device(lines, section, keys[1])
        walked = keys[:2]
        rest = keys[2:]
    else:
        section_end = _block_end(lines, section.line + 1, 0, len(lines))
        _refuse_node_properties(lines, section.line, section_end, keys[0])
        mapping = _child_mapping(lines, section, len(lines), (keys[0],))
        walked = keys[:1]
        rest = keys[1:]
    for key in rest[:-1]:
        walked = (*walked, key)
        entry = _single(lines, mapping, key, walked)
        if entry is None:
            raise PatchError(f"{_path(walked)} tidak ada")
        mapping = _child_mapping(lines, entry, mapping.end, walked)
    target = _single(lines, mapping, rest[-1], keys)
    if target is None:
        _insert(lines, mapping, rest[-1], yaml_value(change.value))
    else:
        _replace(lines, target, mapping, yaml_value(change.value), change.path)


def _entries(lines: list[_Line], mapping: _Mapping) -> list[_Entry]:
    entries = []
    for index in range(mapping.start, mapping.end):
        line = lines[index]
        if not line.is_content or (index != mapping.item and line.indent != mapping.column):
            continue
        match = _KEY.match(line.body, mapping.column)
        if match is not None:
            key = match["key"]
            if key[0] in "\"'":
                key = key[1:-1]
            entries.append(_Entry(index, key, mapping.column, match.end()))
    return entries


def _single(
    lines: list[_Line], mapping: _Mapping, key: str, walked: tuple[str, ...]
) -> _Entry | None:
    found = [entry for entry in _entries(lines, mapping) if entry.key == key]
    if len(found) > 1:
        raise PatchError(f"{_path(walked)} ditulis lebih dari sekali")
    return found[0] if found else None


def _block_end(lines: list[_Line], start: int, column: int, end: int) -> int:
    """Where a value that begins after a key at `column` ends: the next content line further
    left, or at that column and not a sequence entry (a sequence may sit at its key's column)."""
    for index in range(start, end):
        line = lines[index]
        if line.is_content and (
            line.indent < column or (line.indent == column and line.dash is None)
        ):
            return index
    return end


def _inline_value(lines: list[_Line], entry: _Entry) -> str:
    """What follows the key's colon on its line, without a comment."""
    rest = lines[entry.line].body[entry.value_start :]
    stripped = rest.strip()
    return "" if stripped.startswith("#") else stripped


def _child_mapping(
    lines: list[_Line], entry: _Entry, end: int, walked: tuple[str, ...]
) -> _Mapping:
    """The block mapping under `entry`, whose block ends by `end`."""
    if _inline_value(lines, entry):
        raise PatchError(f"{_path(walked)} tidak ditulis sebagai blok bertingkat")
    stop = _block_end(lines, entry.line + 1, entry.column, end)
    first = next((i for i in range(entry.line + 1, stop) if lines[i].is_content), None)
    if first is None:
        return _Mapping(entry.line + 1, stop, entry.column + 2, entry.line)
    if lines[first].dash is not None:
        raise PatchError(f"{_path(walked)} berisi daftar, bukan blok kunci")
    return _Mapping(entry.line + 1, stop, lines[first].indent, entry.line)


def _device(lines: list[_Line], devices: _Entry, device_id: str) -> _Mapping:
    """The mapping of the `devices` item whose id is `device_id`."""
    if _inline_value(lines, devices):
        raise PatchError(f"{DEVICES} tidak ditulis sebagai daftar bertingkat")
    stop = _block_end(lines, devices.line + 1, 0, len(lines))
    starts = [i for i in range(devices.line + 1, stop) if lines[i].is_content]
    if not starts or lines[starts[0]].dash is None:
        raise PatchError(f"perangkat {device_id} tidak ditemukan di bawah {DEVICES}")
    dash_column = lines[starts[0]].indent
    items = [i for i in starts if lines[i].indent == dash_column and lines[i].dash is not None]
    found = []
    for position, item_start in enumerate(items):
        item_end = items[position + 1] if position + 1 < len(items) else stop
        item = _item_mapping(lines, item_start, item_end)
        if item is None:
            continue
        id_entry = next((entry for entry in _entries(lines, item) if entry.key == "id"), None)
        if id_entry is not None and _scalar_text(lines, id_entry) == device_id:
            found.append(item)
    if not found:
        raise PatchError(f"perangkat {device_id} tidak ditemukan di bawah {DEVICES}")
    if len(found) > 1:
        raise PatchError(f"perangkat {device_id} ditulis lebih dari sekali")
    item = found[0]
    _refuse_node_properties(lines, item.start, item.end, f"{DEVICES}[{device_id}]")
    return item


def _item_mapping(lines: list[_Line], start: int, end: int) -> _Mapping | None:
    dash = lines[start].dash
    assert dash is not None
    rest = dash["rest"] or ""
    if rest.strip() and not rest.strip().startswith("#"):
        column = dash.start("rest")
        return _Mapping(start, end, column, start, item=start)
    first = next((i for i in range(start + 1, end) if lines[i].is_content), None)
    return None if first is None else _Mapping(start + 1, end, lines[first].indent, start)


def _scalar_text(lines: list[_Line], entry: _Entry) -> str:
    """A one-line scalar's text as written, unquoted (enough to compare an id)."""
    body = lines[entry.line].body
    start, end = _value_span(body, entry.value_start, "id")
    text = body[start:end]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _refuse_node_properties(lines: list[_Line], start: int, end: int, where: str) -> None:
    """Anchors, aliases, tags, and merge keys can tie the touched block to other values."""
    for index in range(start, end):
        line = lines[index]
        if not line.is_content:
            continue
        text = line.body.lstrip(" ")
        while (dash := _DASH.match(text)) is not None:
            text = (dash["rest"] or "").lstrip(" \t")
        if text.startswith("<<"):
            raise PatchError(f"{where} memakai <<, jadi tidak diubah otomatis")
        key = _KEY.match(text)
        value = text[key.end() :].lstrip(" \t") if key is not None else text
        if value[:1] in ("&", "*", "!"):
            raise PatchError(f"{where} memakai anchor, alias, atau tag, jadi tidak diubah otomatis")


def _value_span(body: str, start: int, path: str) -> tuple[int, int]:
    """Where the value after a key's colon starts and ends on its line; the rest of the line is
    spacing and maybe a comment. Equal ends for an empty value."""
    index = start
    while index < len(body) and body[index] in " \t":
        index += 1
    if index == len(body) or body[index] == "#":
        return index, index
    first = body[index]
    if first in _UNPATCHABLE_STARTS:
        raise PatchError(f"nilai {path} ditulis dalam bentuk yang tidak bisa diubah otomatis")
    if first == '"':
        end = _double_quoted_end(body, index)
    elif first == "'":
        end = _single_quoted_end(body, index)
    elif first == "[":
        end = _flow_list_end(body, index)
    else:
        end = _plain_end(body, index)
    if end is None:
        raise PatchError(f"nilai {path} tidak selesai di barisnya sendiri")
    rest = body[end:]
    if rest.strip() and not (rest[0] in " \t" and rest.lstrip(" \t").startswith("#")):
        raise PatchError(f"nilai {path} ditulis dalam bentuk yang tidak bisa diubah otomatis")
    return index, end


def _double_quoted_end(body: str, start: int) -> int | None:
    index = start + 1
    while index < len(body):
        if body[index] == "\\":
            index += 2
            continue
        if body[index] == '"':
            return index + 1
        index += 1
    return None


def _single_quoted_end(body: str, start: int) -> int | None:
    index = start + 1
    while index < len(body):
        if body[index] == "'":
            if body[index + 1 : index + 2] == "'":
                index += 2
                continue
            return index + 1
        index += 1
    return None


def _flow_list_end(body: str, start: int) -> int | None:
    depth = 0
    index = start
    while index < len(body):
        char = body[index]
        if char in "\"'":
            closing = (_double_quoted_end if char == '"' else _single_quoted_end)(body, index)
            if closing is None:
                return None
            index = closing
            continue
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _plain_end(body: str, start: int) -> int:
    end = len(body)
    for index in range(start + 1, len(body)):
        if body[index] == "#" and body[index - 1] in " \t":
            end = index
            break
    while end > start and body[end - 1] in " \t":
        end -= 1
    return end


def _replace(lines: list[_Line], entry: _Entry, mapping: _Mapping, value: str, path: str) -> None:
    under = _block_end(lines, entry.line + 1, entry.column, mapping.end)
    if any(lines[index].is_content for index in range(entry.line + 1, under)):
        raise PatchError(f"{path} berisi daftar atau blok di baris-baris bawahnya")
    line = lines[entry.line]
    start, end = _value_span(line.body, entry.value_start, path)
    if start == end:  # an empty value: the comment after it, if any, keeps its spacing
        comment = line.body[entry.value_start :].rstrip()
        line.body = f"{line.body[: entry.value_start]} {value}{comment}"
    else:
        line.body = line.body[:start] + value + line.body[end:]


def _insert(lines: list[_Line], mapping: _Mapping, key: str, value: str) -> None:
    """Add `key: value` after the mapping's last line, past the comments indented as deep as
    its keys, at their indentation."""
    content = [i for i in range(mapping.start, mapping.end) if lines[i].is_content]
    after = content[-1] if content else mapping.header
    for index in range(after + 1, mapping.end):
        line = lines[index]
        if line.is_content:
            break
        if line.body.strip():
            if line.indent < mapping.column:
                break
            after = index
    before = lines[after]
    ending = before.ending
    if not ending:
        before.ending = _usual_ending(lines)
    lines.insert(after + 1, _Line(f"{' ' * mapping.column}{key}: {value}", ending))


def _append_section(lines: list[_Line], key: str) -> None:
    ending = _usual_ending(lines)
    if lines and not lines[-1].ending:
        lines[-1].ending = ending
    if lines and lines[-1].body.strip():
        lines.append(_Line("", ending))
    lines.append(_Line(f"{key}:", ending))
