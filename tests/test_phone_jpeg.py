"""jpeg_size: a JPEG's width and height, read from its frame header without decoding it.

Every JPEG here is built by hand from marker segments (ITU-T T.81, Annex B): SOI, APP0, DQT,
a frame header (SOF0 baseline or SOF2 progressive) of a chosen size, DHT, SOS with a few
bytes of entropy-coded data, and EOI. No image library is involved.
"""

from __future__ import annotations

import struct

import pytest

from waifu.phone.jpeg import jpeg_size

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"
SOF0 = 0xC0
SOF2 = 0xC2
DHT = 0xC4
JPG = 0xC8
DAC = 0xCC
TEM = b"\xff\x01"


def segment(marker: int, payload: bytes) -> bytes:
    """A marker segment: 0xFF, the marker, a length counting itself, and the payload."""
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


APP0 = segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
DQT = segment(0xDB, b"\x00" + bytes(range(1, 65)))
# One table with a single one-bit code for the symbol 0.
HUFFMAN = segment(DHT, b"\x00" + b"\x01" + bytes(15) + b"\x00")
SCAN = segment(0xDA, b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00") + b"\x12\x34\xff\x00\x56\x78"


def frame_header(width: int, height: int, *, marker: int = SOF0, components: int = 3) -> bytes:
    """A frame header: 8-bit precision, the size, and `components` components."""
    payload = struct.pack(">BHHB", 8, height, width, components) + b"".join(
        bytes((number, 0x11, 0)) for number in range(1, components + 1)
    )
    return segment(marker, payload)


def jpeg(width: int = 640, height: int = 480, *, marker: int = SOF0) -> bytes:
    """A whole JPEG as a camera page's canvas would encode it, up to EOI."""
    return SOI + APP0 + DQT + frame_header(width, height, marker=marker) + HUFFMAN + SCAN + EOI


@pytest.mark.parametrize(
    ("width", "height"), [(640, 480), (480, 640), (1, 1), (16, 16), (1280, 720), (65535, 65535)]
)
def test_the_size_of_a_baseline_jpeg_is_width_then_height(width: int, height: int) -> None:
    assert jpeg_size(jpeg(width, height)) == (width, height)


def test_the_size_of_a_progressive_jpeg_is_read_from_sof2() -> None:
    assert jpeg_size(jpeg(1024, 768, marker=SOF2)) == (1024, 768)


FRAME_MARKERS = [code for code in range(0xC0, 0xD0) if code not in (DHT, JPG, DAC)]


@pytest.mark.parametrize("marker", FRAME_MARKERS, ids=[f"0x{code:02X}" for code in FRAME_MARKERS])
def test_every_start_of_frame_marker_holds_the_size(marker: int) -> None:
    assert len(FRAME_MARKERS) == 13
    assert jpeg_size(SOI + frame_header(300, 200, marker=marker) + SCAN + EOI) == (300, 200)


@pytest.mark.parametrize("marker", [DHT, JPG, DAC], ids=["DHT", "JPG", "DAC"])
def test_dht_jpg_and_dac_are_not_frame_headers_and_are_skipped(marker: int) -> None:
    # Shaped like a frame header of 9999 x 9999, but these codes are no start of frame.
    lookalike = segment(marker, struct.pack(">BHHB", 8, 9999, 9999, 1) + b"\x01\x11\x00")

    assert jpeg_size(SOI + lookalike + frame_header(320, 240) + SCAN + EOI) == (320, 240)


def test_a_thumbnail_inside_an_app_segment_is_not_mistaken_for_the_photo() -> None:
    # Exif may carry a whole small JPEG; its markers are payload, skipped by the length.
    exif = segment(0xE1, b"Exif\x00\x00" + jpeg(160, 120))

    assert jpeg_size(SOI + exif + DQT + frame_header(800, 600) + SCAN + EOI) == (800, 600)


def test_fill_bytes_before_a_marker_are_skipped() -> None:
    data = SOI + b"\xff\xff" + APP0 + b"\xff" * 5 + DQT + b"\xff" + frame_header(64, 48) + SCAN

    assert jpeg_size(data) == (64, 48)


@pytest.mark.parametrize(
    "standalone",
    [TEM, *(bytes((0xFF, code)) for code in range(0xD0, 0xD8))],
    ids=["TEM", *(f"RST{number}" for number in range(8))],
)
def test_standalone_markers_without_a_length_are_skipped(standalone: bytes) -> None:
    data = SOI + standalone + APP0 + standalone + frame_header(200, 100) + SCAN + EOI

    assert jpeg_size(data) == (200, 100)


def test_everything_after_the_frame_header_is_not_read() -> None:
    head = SOI + APP0 + DQT + frame_header(640, 480)

    assert jpeg_size(head) == (640, 480)
    assert jpeg_size(head + b"\x00not a segment") == (640, 480)


def test_a_jpeg_cut_anywhere_before_the_end_of_its_frame_header_is_refused() -> None:
    head = SOI + APP0 + b"\xff" + DQT + TEM + frame_header(640, 480)

    for cut in range(len(head)):
        with pytest.raises(ValueError):
            jpeg_size(head[:cut])


@pytest.mark.parametrize(
    "data",
    [
        SOI + APP0 + DQT + HUFFMAN + SCAN + EOI,
        SOI + APP0 + EOI,
        SOI + APP0 + SOI + frame_header(640, 480),
    ],
    ids=["scan first", "end of image", "second start of image"],
)
def test_a_jpeg_without_a_frame_header_before_its_scan_is_refused(data: bytes) -> None:
    with pytest.raises(ValueError):
        jpeg_size(data)


@pytest.mark.parametrize(("width", "height"), [(0, 480), (640, 0), (0, 0)])
def test_a_zero_width_or_height_is_refused(width: int, height: int) -> None:
    # Height 0 means a DNL marker after the first scan gives it: not a photo from the page.
    with pytest.raises(ValueError):
        jpeg_size(jpeg(width, height))


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\xff",
        b"\x89PNG\r\n\x1a\n" + bytes(32),
        b"\xff\xd9" + APP0 + frame_header(640, 480),
        b"\x00" + SOI + APP0 + frame_header(640, 480),
    ],
    ids=["empty", "one byte", "png", "eoi first", "leading zero"],
)
def test_data_that_does_not_start_with_soi_is_refused(data: bytes) -> None:
    with pytest.raises(ValueError):
        jpeg_size(data)


@pytest.mark.parametrize(
    "data",
    [
        SOI + b"\x00" + APP0 + frame_header(640, 480),
        SOI + APP0 + b"\x12\x34" + frame_header(640, 480),
        # FF 00 followed by a length that would fit: only the stuffed-zero rule refuses it.
        SOI + b"\xff\x00\x00\x02" + frame_header(640, 480),
    ],
    ids=["zero byte", "stray bytes", "stuffed zero"],
)
def test_anything_but_a_marker_between_segments_is_refused(data: bytes) -> None:
    with pytest.raises(ValueError):
        jpeg_size(data)


@pytest.mark.parametrize("length", [0, 1])
def test_a_segment_length_below_its_own_two_bytes_is_refused(length: int) -> None:
    data = SOI + b"\xff\xe0" + struct.pack(">H", length) + bytes(16) + frame_header(640, 480)

    with pytest.raises(ValueError):
        jpeg_size(data)


def test_a_segment_reaching_past_the_end_is_refused() -> None:
    data = SOI + b"\xff\xe0" + struct.pack(">H", 5000) + frame_header(640, 480) + bytes(100)

    with pytest.raises(ValueError):
        jpeg_size(data)


@pytest.mark.parametrize("payload_length", [0, 1, 3, 5])
def test_a_frame_header_too_short_for_its_fixed_fields_is_refused(payload_length: int) -> None:
    # Precision, height, width, and component count must lie inside the segment, not in
    # the bytes of the next one.
    short = segment(SOF0, struct.pack(">BHHB", 8, 480, 640, 3)[:payload_length])

    with pytest.raises(ValueError):
        jpeg_size(SOI + short + APP0 + APP0)
