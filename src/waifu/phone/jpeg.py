"""The pixel size of a JPEG, read from its frame header without decoding the image.

A JPEG (ITU-T T.81, Annex B) is a series of markers: 0xFF and a code. Most markers start a
segment, with a two-byte big-endian length that counts itself and the payload after it; SOI,
EOI, RST0-RST7, and TEM stand alone, without one, and any marker may be preceded by fill
bytes (more 0xFF). The frame header, any of SOF0-SOF15 (the codes 0xC0-0xCF but DHT 0xC4,
JPG 0xC8, and DAC 0xCC), comes before the first scan (SOS) and holds the number of lines
(the height) and the samples per line (the width). Segments are skipped by their length, so
a thumbnail inside an Exif segment is never read for the photo.
"""

from __future__ import annotations

import struct

MARKER = 0xFF
SOI = 0xD8
EOI = 0xD9
SOS = 0xDA
TEM = 0x01
RESTARTS = range(0xD0, 0xD8)
DHT = 0xC4
JPG = 0xC8
DAC = 0xCC
FRAME_HEADERS = frozenset(range(0xC0, 0xD0)) - {DHT, JPG, DAC}
# A frame header's fixed fields: its length, sample precision, height, width, and number of
# components.
_FRAME_FIELDS = struct.Struct(">HBHHB")
_LENGTH = struct.Struct(">H")


def jpeg_size(data: bytes) -> tuple[int, int]:
    """The (width, height) in pixels of the JPEG `data`, from its frame header.

    Raises ValueError when `data` does not start with SOI, ends before its frame header
    does, has anything but a marker where one belongs, reaches a scan, the end of the image,
    or another SOI before a frame header, or has a width or height of zero.
    """
    if data[:2] != bytes((MARKER, SOI)):
        raise ValueError("Not a JPEG: it does not start with SOI")
    position = 2
    while True:
        code, position = _marker(data, position)
        if code == TEM or code in RESTARTS:
            continue
        if code in (SOI, EOI, SOS):
            raise ValueError(f"The JPEG has no frame header before marker 0x{code:02X}")
        length = _segment_length(data, position)
        if code in FRAME_HEADERS:
            return _frame_size(data, position, length)
        position += length


def _marker(data: bytes, position: int) -> tuple[int, int]:
    """The code of the marker at `position`, after any fill bytes, and the position after it."""
    if position >= len(data):
        raise ValueError("The JPEG ends before its frame header")
    if data[position] != MARKER:
        raise ValueError(f"No JPEG marker at byte {position}")
    while position < len(data) and data[position] == MARKER:
        position += 1
    if position >= len(data):
        raise ValueError("The JPEG ends before its frame header")
    if data[position] == 0:  # 0xFF00 is a stuffed 0xFF of entropy-coded data, not a marker
        raise ValueError(f"No JPEG marker at byte {position - 1}")
    return data[position], position + 1


def _segment_length(data: bytes, position: int) -> int:
    """The length of the segment whose length field is at `position`; all of it must be there."""
    if position + _LENGTH.size > len(data):
        raise ValueError("The JPEG ends inside a segment length")
    (length,) = _LENGTH.unpack_from(data, position)
    if length < _LENGTH.size:
        raise ValueError(f"A JPEG segment length of {length} does not even cover itself")
    if position + length > len(data):
        raise ValueError("A JPEG segment reaches past the end of the data")
    return length


def _frame_size(data: bytes, position: int, length: int) -> tuple[int, int]:
    """The (width, height) of the frame header whose length field is at `position`."""
    if length < _FRAME_FIELDS.size:
        raise ValueError("The JPEG frame header is too short for its fields")
    _, _, height, width, _ = _FRAME_FIELDS.unpack_from(data, position)
    if width == 0 or height == 0:
        raise ValueError("The JPEG frame header gives a size of zero")
    return width, height
