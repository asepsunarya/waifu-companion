"""Screen capture and PNG: no test captures the real screen.

`capture_screen` gets fake user32 and gdi32 libraries through its loader: they record every
call, hand out made-up handles, and write synthetic pixels into the DIB section when StretchBlt
runs, so the GDI code is checked by its structure and by what it does with the pixels
(`tests/conftest.py` also refuses looking up BitBlt, StretchBlt, or PrintWindow in the real
libraries). The PNG encoder gets synthetic arrays, and its output is decoded back with zlib
following the PNG specification.
"""

import ctypes
import os
import struct
import subprocess
import sys
import zlib
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY

import numpy as np
import pytest

from waifu.screen import BITMAPINFO, BITMAPINFOHEADER, ScreenImage, capture_screen, encode_png

# PNG specification (ISO/IEC 15948): the signature every PNG starts with, and the empty IEND
# chunk every PNG ends with (length 0, type, and the CRC of "IEND").
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
IEND_CHUNK = b"\x00\x00\x00\x00IEND\xaeB`\x82"
# The five filter types of filter method 0 (section 9.2): None, Sub, Up, Average, Paeth.
FILTER_TYPES = {0, 1, 2, 3, 4}
PAETH = 4

# Win32 values from Microsoft's documentation (winuser.h, wingdi.h).
SM_CXSCREEN = 0
SM_CYSCREEN = 1
DIB_RGB_COLORS = 0
BI_RGB = 0
HALFTONE = 4
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
ERROR_NOT_ENOUGH_MEMORY = 8
# windef.h: DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE is the pseudo-handle -3.
PER_MONITOR_AWARE = -3

# Made-up handles the fake libraries hand out.
SCREEN_DC = 0x1001
MEMORY_DC = 0x2002
DIB_BITMAP = 0x3003
STOCK_BITMAP = 0x4004  # the 1x1 bitmap a new memory DC holds
UNAWARE_CONTEXT = 0x6010  # the DPI awareness the thread had before (python.exe is unaware)

# Microsoft's documented signatures: handles are pointer-sized, never a 32-bit int.
EXPECTED_SIGNATURES = {
    "user32": {
        "SetThreadDpiAwarenessContext": ([wintypes.HANDLE], wintypes.HANDLE),
        "GetSystemMetrics": ([ctypes.c_int], ctypes.c_int),
        "GetDC": ([wintypes.HWND], wintypes.HDC),
        "ReleaseDC": ([wintypes.HWND, wintypes.HDC], ctypes.c_int),
    },
    "gdi32": {
        "CreateCompatibleDC": ([wintypes.HDC], wintypes.HDC),
        "CreateDIBSection": (
            [
                wintypes.HDC,
                ctypes.POINTER(BITMAPINFO),
                wintypes.UINT,
                ctypes.POINTER(ctypes.c_void_p),
                wintypes.HANDLE,
                wintypes.DWORD,
            ],
            wintypes.HBITMAP,
        ),
        "SelectObject": ([wintypes.HDC, wintypes.HGDIOBJ], wintypes.HGDIOBJ),
        "SetStretchBltMode": ([wintypes.HDC, ctypes.c_int], ctypes.c_int),
        "SetBrushOrgEx": (
            [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.LPPOINT],
            wintypes.BOOL,
        ),
        "StretchBlt": (
            [wintypes.HDC, *[ctypes.c_int] * 4, wintypes.HDC, *[ctypes.c_int] * 4, wintypes.DWORD],
            wintypes.BOOL,
        ),
        "GdiFlush": ([], wintypes.BOOL),
        "DeleteObject": ([wintypes.HGDIOBJ], wintypes.BOOL),
        "DeleteDC": ([wintypes.HDC], wintypes.BOOL),
    },
}
# What Windows returns when each function fails.
FAILURE_RESULTS = {
    "SetThreadDpiAwarenessContext": None,
    "GetSystemMetrics": 0,
    "GetDC": None,
    "ReleaseDC": 0,
    "CreateCompatibleDC": None,
    "CreateDIBSection": None,
    "SelectObject": None,
    "SetStretchBltMode": 0,
    "SetBrushOrgEx": 0,
    "StretchBlt": 0,
    "GdiFlush": 0,
    "DeleteObject": 0,
    "DeleteDC": 0,
}

RESTORE_STOCK_BITMAP = ("SelectObject", MEMORY_DC, STOCK_BITMAP)
DELETE_DIB = ("DeleteObject", DIB_BITMAP)
DELETE_MEMORY_DC = ("DeleteDC", MEMORY_DC)
RELEASE_SCREEN_DC = ("ReleaseDC", None, SCREEN_DC)
RESTORE_DPI_AWARENESS = ("SetThreadDpiAwarenessContext", UNAWARE_CONTEXT)

SRC = Path(__file__).resolve().parent.parent / "src"
CHILD_TIMEOUT_S = 60


def synthetic_bgra(width: int, height: int) -> np.ndarray:
    """A screen whose every pixel differs in blue, green, and red; alpha is not opaque."""
    y, x = np.mgrid[0:height, 0:width]
    channels = [x % 256, y % 256, (3 * x + 5 * y) % 256, np.full_like(x, 0x7F)]
    return np.stack(channels, axis=-1).astype(np.uint8)


class FakeFunction:
    """One fake Win32 function: refuses to run undeclared, records each call, may fail."""

    def __init__(self, windows: "FakeWindows", name: str, behaviour) -> None:
        self.windows = windows
        self.name = name
        self.behaviour = behaviour
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        assert self.argtypes is not None, f"{self.name} called without argtypes"
        assert self.restype is not None, f"{self.name} called without restype"
        self.windows.calls.append((self.name, *args))
        if self.name == self.windows.failing:
            return FAILURE_RESULTS[self.name]
        return self.behaviour(*args)


class FakeWindows:
    """Fake user32 and gdi32 sharing one call log, a screen of the given size, and the DIB
    section's memory. `failing` names a function that fails as Windows' would (NULL or 0).

    `screen` is in physical pixels, as the screen DC is. With display scaling (`scale` 1.5 is
    Windows' 150%), a thread that is not per-monitor DPI aware is told a smaller, logical
    screen size by GetSystemMetrics, as Windows does."""

    def __init__(
        self,
        screen: tuple[int, int] = (2560, 1440),
        failing: str | None = None,
        scale: float = 1.0,
    ):
        self.screen = screen
        self.failing = failing
        self.scale = scale
        self.awareness = UNAWARE_CONTEXT  # the thread's DPI awareness context
        self.calls: list[tuple] = []
        self.loaded: list[str] = []
        self.header_bytes: bytes | None = None
        self.size: tuple[int, int] | None = None
        self.pixels = None  # the DIB section's memory
        self.libraries = {
            "user32": self._library(
                SetThreadDpiAwarenessContext=self._set_thread_dpi_awareness_context,
                GetSystemMetrics=self._get_system_metrics,
                GetDC=lambda window: SCREEN_DC,
                ReleaseDC=lambda window, dc: 1,
            ),
            "gdi32": self._library(
                CreateCompatibleDC=lambda dc: MEMORY_DC,
                CreateDIBSection=self._create_dib_section,
                SelectObject=lambda dc, obj: STOCK_BITMAP if obj == DIB_BITMAP else DIB_BITMAP,
                SetStretchBltMode=lambda dc, mode: 1,  # the previous mode, BLACKONWHITE
                SetBrushOrgEx=lambda dc, x, y, previous: 1,
                StretchBlt=self._stretch_blt,
                GdiFlush=lambda: 1,
                DeleteObject=self._delete_object,
                DeleteDC=lambda dc: 1,
            ),
        }

    def load(self, name: str) -> SimpleNamespace:
        self.loaded.append(name)
        return self.libraries[name]

    def call(self, name: str) -> tuple:
        return next(call for call in self.calls if call[0] == name)

    def signatures(self) -> dict:
        return {
            library: {
                name: (function.argtypes, function.restype)
                for name, function in vars(functions).items()
            }
            for library, functions in self.libraries.items()
        }

    def _library(self, **behaviours) -> SimpleNamespace:
        return SimpleNamespace(
            **{name: FakeFunction(self, name, behaviour) for name, behaviour in behaviours.items()}
        )

    def _set_thread_dpi_awareness_context(self, context):
        previous, self.awareness = self.awareness, context
        return previous

    def _get_system_metrics(self, index):
        if self.awareness == PER_MONITOR_AWARE:
            return self.screen[index]
        return round(self.screen[index] / self.scale)  # DPI virtualization

    def _create_dib_section(self, dc, info, usage, bits, section, offset):
        header = info.bmiHeader
        self.header_bytes = bytes(header)
        self.size = (header.biWidth, abs(header.biHeight))
        self.pixels = (ctypes.c_ubyte * (header.biWidth * abs(header.biHeight) * 4))()
        bits.value = ctypes.addressof(self.pixels)
        return DIB_BITMAP

    def _stretch_blt(self, dc, x, y, width, height, source_dc, sx, sy, swidth, sheight, rop):
        dib = np.frombuffer(self.pixels, dtype=np.uint8).reshape(height, width, 4)
        dib[:] = synthetic_bgra(width, height)
        return 1

    def _delete_object(self, obj):
        # Freed memory gets reused: pixels read after this are garbage.
        ctypes.memset(self.pixels, 0xEE, ctypes.sizeof(self.pixels))
        return 1


def read_chunks(png: bytes) -> list[tuple[bytes, bytes]]:
    """The PNG's chunks as (type, data), checking the signature, each length, and each CRC."""
    assert png[:8] == PNG_SIGNATURE
    chunks = []
    offset = 8
    while offset < len(png):
        (length,) = struct.unpack_from(">I", png, offset)
        kind = png[offset + 4 : offset + 8]
        data = png[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack_from(">I", png, offset + 8 + length)
        assert len(data) == length
        assert crc == zlib.crc32(kind + data), f"wrong CRC in the {kind!r} chunk"
        chunks.append((kind, data))
        offset += 12 + length
    assert offset == len(png)
    return chunks


def png_rows(png: bytes) -> np.ndarray:
    """The rows of an 8-bit RGB, non-interlaced PNG as stored: a filter type byte, then the
    filtered bytes of the row."""
    chunks = read_chunks(png)
    assert [kind for kind, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]
    width, height, depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", chunks[0][1]
    )
    assert (depth, color_type, compression, filter_method, interlace) == (8, 2, 0, 0, 0)
    raw = zlib.decompress(chunks[1][1])
    return np.frombuffer(raw, dtype=np.uint8).reshape(height, 1 + 3 * width)


def png_size(png: bytes) -> tuple[int, int]:
    """Width and height from the IHDR chunk, which directly follows the signature."""
    assert png[:8] == PNG_SIGNATURE and png[12:16] == b"IHDR"
    return struct.unpack(">II", png[16:24])


def paeth(a: int, b: int, c: int) -> int:
    """The PNG specification's Paeth predictor (section 9.4)."""
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def predict(kind: int, a: int, b: int, c: int) -> int:
    """What filter type `kind` predicts a byte from: the byte of the pixel to its left (a),
    above (b), and above and to the left (c), each 0 beyond the image (section 9.2)."""
    assert kind in range(5), f"unknown filter type {kind}"
    if kind == PAETH:
        return paeth(a, b, c)
    return [0, a, b, (a + b) // 2][kind]  # None, Sub, Up, Average


def decode_png(png: bytes) -> np.ndarray:
    """Decode an 8-bit RGB, non-interlaced PNG, undoing each row's filter (3 bytes a pixel)."""
    rows = png_rows(png)
    height, stride = rows.shape[0], rows.shape[1] - 1
    previous = [0] * stride
    pixels = []
    for kind, *filtered in rows.tolist():
        current: list[int] = []
        for i, value in enumerate(filtered):
            a = current[i - 3] if i >= 3 else 0
            c = previous[i - 3] if i >= 3 else 0
            current.append((value + predict(kind, a, previous[i], c)) % 256)
        pixels.append(current)
        previous = current
    return np.array(pixels, dtype=np.uint8).reshape(height, stride // 3, 3)


# --- encode_png -------------------------------------------------------------------------------


def test_encode_png_writes_the_signature_ihdr_one_idat_and_iend():
    rgb = np.zeros((3, 5, 3), dtype=np.uint8)

    png = encode_png(rgb)

    chunks = read_chunks(png)  # signature, lengths, and every CRC
    assert [kind for kind, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]
    # Width, height, bit depth 8, color type 2 (RGB), deflate, filter method 0, no interlace.
    assert chunks[0][1] == struct.pack(">IIBBBBB", 5, 3, 8, 2, 0, 0, 0)
    assert png.endswith(IEND_CHUNK)


def noise(height: int, width: int, seed: int = 62) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, size=(height, width, 3), dtype=np.uint8)


def smooth_screen(height: int, width: int) -> np.ndarray:
    """Soft gradients, like a game's sky or a wallpaper."""
    y, x = np.mgrid[0:height, 0:width]
    channels = [x * 255 // width, y * 255 // height, (x + y) * 255 // (width + height)]
    return np.stack(channels, axis=-1).astype(np.uint8)


# Between them, these arrays get every filter type (checked below).
FILTERED_ARRAYS = {
    "noise": noise(7, 11),
    "flat": np.full((4, 4, 3), 200, dtype=np.uint8),
    "gradients": np.ascontiguousarray(synthetic_bgra(9, 4)[:, :, 2::-1]),
}


@pytest.mark.parametrize("rgb", FILTERED_ARRAYS.values(), ids=FILTERED_ARRAYS.keys())
def test_encode_png_keeps_every_pixel_through_its_row_filters(rgb):
    decoded = decode_png(encode_png(rgb))

    np.testing.assert_array_equal(decoded, rgb)


def test_encode_png_picks_among_all_five_filter_types_row_by_row():
    kinds = {kind for rgb in FILTERED_ARRAYS.values() for kind in png_rows(encode_png(rgb))[:, 0]}

    assert kinds == FILTER_TYPES


def test_encode_png_makes_a_smooth_screen_far_smaller_than_unfiltered_rows():
    rgb = smooth_screen(720, 1280)
    unfiltered = np.insert(rgb.reshape(720, 3 * 1280), 0, 0, axis=1)  # filter type 0 rows

    size = len(encode_png(rgb))

    unfiltered_size = len(zlib.compress(unfiltered.tobytes(), 6))
    assert size * 10 < unfiltered_size


def test_encode_png_takes_a_strided_view_such_as_reversed_bgra_channels():
    bgra = synthetic_bgra(9, 4)
    rgb_view = bgra[:, :, 2::-1]

    decoded = decode_png(encode_png(rgb_view))

    np.testing.assert_array_equal(decoded, np.ascontiguousarray(rgb_view))


def test_encode_png_compresses_the_rows_with_zlib_level_6():
    png = encode_png(np.full((4, 4, 3), 200, dtype=np.uint8))

    idat = dict(read_chunks(png))[b"IDAT"]
    # zlib header: deflate with a 32 KB window (0x78), and FLEVEL 2 (0x9C), which is level 6.
    assert idat[:2] == b"\x78\x9c"


@pytest.mark.parametrize(
    "array",
    [
        np.zeros((0, 4, 3), dtype=np.uint8),
        np.zeros((4, 0, 3), dtype=np.uint8),
        np.zeros((4, 4), dtype=np.uint8),
        np.zeros((4, 4, 4), dtype=np.uint8),
        np.zeros((4, 4, 1), dtype=np.uint8),
        np.zeros(12, dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.float32),
        np.zeros((4, 4, 3), dtype=np.uint16),
    ],
)
def test_encode_png_refuses_empty_wrongly_shaped_or_non_byte_arrays(array):
    with pytest.raises(ValueError):
        encode_png(array)


# --- capture_screen ---------------------------------------------------------------------------


def test_capture_stretches_the_screen_into_a_dib_and_releases_everything_in_reverse():
    windows = FakeWindows(screen=(2560, 1440))

    capture_screen(1280, load=windows.load)

    assert windows.calls == [
        # Physical pixels from here on, so the screen size matches the screen DC's.
        ("SetThreadDpiAwarenessContext", PER_MONITOR_AWARE),
        ("GetSystemMetrics", SM_CXSCREEN),
        ("GetSystemMetrics", SM_CYSCREEN),
        ("GetDC", None),
        ("CreateCompatibleDC", SCREEN_DC),
        ("CreateDIBSection", MEMORY_DC, ANY, DIB_RGB_COLORS, ANY, None, 0),
        ("SelectObject", MEMORY_DC, DIB_BITMAP),
        ("SetStretchBltMode", MEMORY_DC, HALFTONE),
        ("SetBrushOrgEx", MEMORY_DC, 0, 0, None),
        (
            "StretchBlt",
            MEMORY_DC,
            0,
            0,
            1280,
            720,
            SCREEN_DC,
            0,
            0,
            2560,
            1440,
            SRCCOPY | CAPTUREBLT,
        ),
        ("GdiFlush",),
        # The DC's own bitmap goes back first: a bitmap still selected cannot be deleted.
        RESTORE_STOCK_BITMAP,
        DELETE_DIB,
        DELETE_MEMORY_DC,
        RELEASE_SCREEN_DC,
        # The thread gets its own DPI awareness back last, after every handle.
        RESTORE_DPI_AWARENESS,
    ]


@pytest.mark.parametrize(
    ("screen", "scale", "size"),
    [
        ((3840, 2160), 1.5, (1280, 720)),  # a 4K monitor or TV at Windows' recommended 150%
        ((1920, 1080), 1.25, (1280, 720)),  # a laptop at 125%
        ((2560, 1600), 2.0, (1280, 800)),
    ],
)
def test_a_scaled_display_is_captured_whole_in_physical_pixels(screen, scale, size):
    windows = FakeWindows(screen=screen, scale=scale)

    image = capture_screen(1280, load=windows.load)

    _, _, _, _, dst_width, dst_height, _, sx, sy, src_width, src_height, _ = windows.call(
        "StretchBlt"
    )
    # A DPI-unaware thread is told the logical size (2560x1440 for 4K at 150%), but the
    # screen DC is physical: that size would copy only its top-left part.
    assert (sx, sy, src_width, src_height) == (0, 0, *screen)
    assert (dst_width, dst_height) == size
    assert (image.width, image.height) == size


def test_the_thread_gets_its_dpi_awareness_back_after_the_capture():
    windows = FakeWindows(screen=(3840, 2160), scale=1.5)

    capture_screen(1280, load=windows.load)

    assert windows.awareness == UNAWARE_CONTEXT


def test_capture_rop_is_srccopy_with_capture_blt():
    windows = FakeWindows()

    capture_screen(1280, load=windows.load)

    assert windows.call("StretchBlt")[-1] == 0x40CC0020


def test_dib_section_is_32_bit_top_down_rgb_of_the_scaled_size():
    windows = FakeWindows(screen=(2560, 1440))

    capture_screen(1280, load=windows.load)

    # biSize 40, biWidth, biHeight negative (top-down rows), biPlanes 1, biBitCount 32,
    # biCompression BI_RGB, then biSizeImage (may be 0 for BI_RGB) and the rest zero.
    assert windows.header_bytes == struct.pack(
        "<IiiHHIIiiII", 40, 1280, -720, 1, 32, BI_RGB, 0, 0, 0, 0, 0
    )


def test_bitmap_structures_match_wingdi_h():
    offsets = [getattr(BITMAPINFOHEADER, name).offset for name, _ in BITMAPINFOHEADER._fields_]

    assert ctypes.sizeof(BITMAPINFOHEADER) == 40
    assert offsets == [0, 4, 8, 12, 14, 16, 20, 24, 28, 32, 36]
    # BITMAPINFO: the header, then a color table of one RGBQUAD (unused at 32 bits).
    assert ctypes.sizeof(BITMAPINFO) == 44
    assert BITMAPINFO.bmiColors.offset == 40


@pytest.mark.parametrize(
    ("screen", "max_side", "size"),
    [
        ((2560, 1440), 1280, (1280, 720)),
        ((1920, 1080), 1280, (1280, 720)),
        ((1366, 768), 1280, (1280, 720)),  # 719.6 rounds to 720
        ((3440, 1440), 1568, (1568, 656)),  # ultrawide: 656.4
        ((1280, 1024), 1280, (1280, 1024)),  # already max_side
        ((1280, 720), 1568, (1280, 720)),  # never upscaled
        ((1440, 2560), 1280, (720, 1280)),  # portrait
        ((1080, 1920), 512, (288, 512)),
    ],
)
def test_capture_keeps_the_aspect_with_the_longer_side_at_most_max_side(screen, max_side, size):
    windows = FakeWindows(screen=screen)

    image = capture_screen(max_side, load=windows.load)

    assert (image.width, image.height) == size
    assert windows.size == size
    _, _, x, y, dst_width, dst_height, _, sx, sy, src_width, src_height, _ = windows.call(
        "StretchBlt"
    )
    assert (x, y, dst_width, dst_height) == (0, 0, *size)  # the destination: the whole DIB
    assert (sx, sy, src_width, src_height) == (0, 0, *screen)  # the source: the whole screen
    assert png_size(image.png) == size


def test_capture_turns_the_dib_bgra_pixels_into_an_rgb_png():
    windows = FakeWindows(screen=(320, 200))

    image = capture_screen(512, load=windows.load)

    bgra = synthetic_bgra(320, 200)
    assert isinstance(image, ScreenImage)
    assert (image.width, image.height) == (320, 200)
    # Copied before the DIB is deleted (the fake scribbles over it then); alpha dropped.
    np.testing.assert_array_equal(decode_png(image.png), bgra[:, :, [2, 1, 0]])


def test_every_function_is_declared_in_full_before_it_is_called():
    windows = FakeWindows()  # its functions refuse to run undeclared

    capture_screen(1280, load=windows.load)

    assert windows.signatures() == EXPECTED_SIGNATURES
    assert windows.loaded == ["user32", "gdi32"]
    create_dib_section = windows.libraries["gdi32"].CreateDIBSection
    # The structure and the bits variable are passed as they are; ctypes adds the byref.
    create_dib_section.argtypes[1].from_param(BITMAPINFO())
    create_dib_section.argtypes[3].from_param(ctypes.c_void_p())


def test_capture_loads_user32_and_gdi32_with_last_error_by_default(monkeypatch):
    windows = FakeWindows()  # never the real libraries: nothing here can capture the screen
    loads = []

    def fake_windll(name, **kwargs):
        loads.append((name, kwargs))
        return windows.load(name)

    monkeypatch.setattr(ctypes, "WinDLL", fake_windll, raising=False)

    image = capture_screen(1280)

    assert loads == [("user32", {"use_last_error": True}), ("gdi32", {"use_last_error": True})]
    assert (image.width, image.height) == (1280, 720)


ALL_RELEASES = [
    RESTORE_STOCK_BITMAP,
    DELETE_DIB,
    DELETE_MEMORY_DC,
    RELEASE_SCREEN_DC,
    RESTORE_DPI_AWARENESS,
]


@pytest.mark.parametrize(
    ("failing", "releases"),
    [
        ("SetThreadDpiAwarenessContext", []),  # NULL: the thread was left as it was
        ("GetSystemMetrics", [RESTORE_DPI_AWARENESS]),
        ("GetDC", [RESTORE_DPI_AWARENESS]),
        ("CreateCompatibleDC", [RELEASE_SCREEN_DC, RESTORE_DPI_AWARENESS]),
        ("CreateDIBSection", [DELETE_MEMORY_DC, RELEASE_SCREEN_DC, RESTORE_DPI_AWARENESS]),
        (
            "SelectObject",
            [DELETE_DIB, DELETE_MEMORY_DC, RELEASE_SCREEN_DC, RESTORE_DPI_AWARENESS],
        ),
        ("SetStretchBltMode", ALL_RELEASES),
        ("SetBrushOrgEx", ALL_RELEASES),
        ("StretchBlt", ALL_RELEASES),
        ("GdiFlush", ALL_RELEASES),
    ],
)
def test_a_failed_call_raises_os_error_and_releases_what_was_acquired(
    monkeypatch, failing, releases
):
    monkeypatch.setattr(ctypes, "get_last_error", lambda: ERROR_NOT_ENOUGH_MEMORY, raising=False)
    windows = FakeWindows(failing=failing)

    with pytest.raises(OSError, match=rf"{failing}.*error {ERROR_NOT_ENOUGH_MEMORY}\b"):
        capture_screen(1280, load=windows.load)

    failed_at = windows.calls.index(windows.call(failing))
    assert windows.calls[failed_at + 1 :] == releases


def test_screen_image_repr_leaves_out_the_png_bytes():
    image = ScreenImage(png=b"\x89PNG secret pixels", width=2, height=1)

    assert "secret" not in repr(image)
    assert "width=2" in repr(image)


IMPORT_PROBE = """
import ctypes
from pathlib import PurePath

real_windll = ctypes.WinDLL
loads = []


def refuse_gdi(name, *args, **kwargs):
    if PurePath(str(name)).stem.lower() in {"user32", "gdi32"}:
        loads.append(str(name))
        raise OSError("the import probe refuses user32 and gdi32")
    return real_windll(name, *args, **kwargs)


ctypes.WinDLL = refuse_gdi
ctypes.windll = ctypes.LibraryLoader(refuse_gdi)
import waifu.screen
print(loads)
"""


def test_importing_the_module_never_loads_user32_or_gdi32():
    # A fresh interpreter: this one imported the module before any guard was in place.
    paths = [str(SRC), os.environ.get("PYTHONPATH", "")]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(path for path in paths if path)}

    result = subprocess.run(
        [sys.executable, "-c", IMPORT_PROBE],
        env=env,
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_S,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
