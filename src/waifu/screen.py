"""One screenshot of the main monitor, as a PNG, for Cyrene to look at when Master asks.

GDI through ctypes and a small PNG encoder on zlib and numpy, so there is no new dependency.
The libraries come from an injected loader: the real one loads user32 and gdi32 only when a
screenshot is taken, and tests pass fakes, so no test ever captures the owner's screen.
The image is never written to disk or logged.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import struct
import zlib
from collections.abc import Callable, Mapping
from ctypes import wintypes
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# From windef.h, winuser.h, and wingdi.h.
# python.exe is not DPI aware: on a scaled display Windows tells it a smaller, logical screen
# size, while the screen DC stays in physical pixels. Per-monitor awareness makes both physical.
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = -3
SM_CXSCREEN = 0
SM_CYSCREEN = 1
DIB_RGB_COLORS = 0
BI_RGB = 0
HALFTONE = 4
SRCCOPY = 0x00CC0020
# Also copies layered windows (transparent or always on top, like her mascot window).
CAPTUREBLT = 0x40000000
BGRA_BYTES = 4

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_BIT_DEPTH = 8
PNG_COLOR_TYPE_RGB = 2
PNG_COMPRESSION_DEFLATE = 0
PNG_FILTER_METHOD_ADAPTIVE = 0
PNG_INTERLACE_NONE = 0
PNG_ZLIB_LEVEL = 6
RGB_BYTES = 3  # bytes per pixel: the filters' distance to the pixel on the left


@dataclass(frozen=True)
class ScreenImage:
    """A screenshot as PNG bytes; the bytes stay out of its repr, so it never fills a log."""

    png: bytes = field(repr=False)
    width: int
    height: int


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", wintypes.BYTE),
        ("rgbGreen", wintypes.BYTE),
        ("rgbRed", wintypes.BYTE),
        ("rgbReserved", wintypes.BYTE),
    ]


class BITMAPINFO(ctypes.Structure):
    # A 32-bit BI_RGB bitmap has no color table; bmiColors only completes wingdi.h's layout.
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]


_Signature = tuple[list[Any], Any]

# Microsoft's documented signatures. Handles are pointer-sized: without these declarations
# ctypes would pass and return them as 32-bit ints and cut 64-bit handles short.
_USER32_FUNCTIONS: Mapping[str, _Signature] = {
    # DPI_AWARENESS_CONTEXT is a handle (DECLARE_HANDLE in windef.h).
    "SetThreadDpiAwarenessContext": ([wintypes.HANDLE], wintypes.HANDLE),
    "GetSystemMetrics": ([ctypes.c_int], ctypes.c_int),
    "GetDC": ([wintypes.HWND], wintypes.HDC),
    "ReleaseDC": ([wintypes.HWND, wintypes.HDC], ctypes.c_int),
}
_GDI32_FUNCTIONS: Mapping[str, _Signature] = {
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
    "SetBrushOrgEx": ([wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.LPPOINT], wintypes.BOOL),
    "StretchBlt": (
        [wintypes.HDC, *[ctypes.c_int] * 4, wintypes.HDC, *[ctypes.c_int] * 4, wintypes.DWORD],
        wintypes.BOOL,
    ),
    "GdiFlush": ([], wintypes.BOOL),
    "DeleteObject": ([wintypes.HGDIOBJ], wintypes.BOOL),
    "DeleteDC": ([wintypes.HDC], wintypes.BOOL),
}


def _load_library(name: str) -> Any:
    # ctypes.WinDLL is looked up here, when a screenshot is taken, never bound at import,
    # so the test suite's guard on it always applies.
    return ctypes.WinDLL(name, use_last_error=True)


def capture_screen(max_side: int, *, load: Callable[[str], Any] = _load_library) -> ScreenImage:
    """Take one screenshot of the primary monitor, its longer side at most `max_side` pixels.

    Raises OSError, with Windows' last error, when a GDI call fails.
    """
    user32 = _declare(load("user32"), _USER32_FUNCTIONS)
    gdi32 = _declare(load("gdi32"), _GDI32_FUNCTIONS)
    rgb = _grab(user32, gdi32, max_side)
    height, width = rgb.shape[:2]
    image = ScreenImage(png=encode_png(rgb), width=width, height=height)
    logger.info(
        "Captured the main monitor at %dx%d (PNG, %d KB)", width, height, len(image.png) // 1024
    )
    return image


def encode_png(rgb: np.ndarray) -> bytes:
    """A PNG of an HxWx3 uint8 RGB array: IHDR, one IDAT of adaptively filtered rows, IEND."""
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != RGB_BYTES or 0 in rgb.shape:
        raise ValueError(
            f"encode_png needs a non-empty HxWx3 uint8 array, not {rgb.shape} {rgb.dtype}"
        )
    height, width = rgb.shape[:2]
    rows = _filtered_rows(rgb.reshape(height, RGB_BYTES * width))
    header = struct.pack(
        ">IIBBBBB",
        width,
        height,
        PNG_BIT_DEPTH,
        PNG_COLOR_TYPE_RGB,
        PNG_COMPRESSION_DEFLATE,
        PNG_FILTER_METHOD_ADAPTIVE,
        PNG_INTERLACE_NONE,
    )
    return b"".join(
        (
            PNG_SIGNATURE,
            _chunk(b"IHDR", header),
            _chunk(b"IDAT", zlib.compress(rows.tobytes(), PNG_ZLIB_LEVEL)),
            _chunk(b"IEND", b""),
        )
    )


def _filtered_rows(raw: np.ndarray) -> np.ndarray:
    """Each row of bytes filtered with the filter that suits it best, after its type byte.

    The PNG specification's five filters predict every byte from the same byte of the pixel to
    its left (a), above (b), and above and to the left (c), 0 beyond the image, and store the
    difference modulo 256: None, Sub (a), Up (b), Average (the floor of (a + b) / 2), and Paeth
    (whichever of a, b, c is closest to a + b - c). Smooth areas leave small differences, which
    deflate packs far better. Each row takes the filter whose differences, read as signed
    bytes, have the smallest sum of absolute values: the heuristic the specification suggests.
    """
    left = np.zeros_like(raw)
    left[:, RGB_BYTES:] = raw[:, :-RGB_BYTES]
    above = np.zeros_like(raw)
    above[1:] = raw[:-1]
    above_left = np.zeros_like(raw)
    above_left[1:, RGB_BYTES:] = raw[:-1, :-RGB_BYTES]
    a, b, c = (neighbour.astype(np.int16) for neighbour in (left, above, above_left))
    # Paeth: |p - a|, |p - b|, and |p - c| for p = a + b - c; ties go to a, then b.
    to_a, to_b, to_c = np.abs(b - c), np.abs(a - c), np.abs(a + b - 2 * c)
    paeth = np.where(
        (to_a <= to_b) & (to_a <= to_c), left, np.where(to_b <= to_c, above, above_left)
    )
    average = ((a + b) // 2).astype(np.uint8)
    # Filter types 0-4 in order; uint8 subtraction wraps around, which is the modulo 256.
    filtered = np.stack([raw, raw - left, raw - above, raw - average, raw - paeth])
    costs = np.stack(
        [
            np.abs(candidate.view(np.int8).astype(np.int16)).sum(axis=1, dtype=np.int64)
            for candidate in filtered
        ]
    )
    kinds = costs.argmin(axis=0)  # the first of equal sums: the simpler filter
    height = raw.shape[0]
    rows = np.empty((height, 1 + raw.shape[1]), dtype=np.uint8)
    rows[:, 0] = kinds
    rows[:, 1:] = filtered[kinds, np.arange(height)]
    return rows


def _chunk(kind: bytes, data: bytes) -> bytes:
    """Length, type, data, and the CRC-32 of type and data, as the PNG specification lays out."""
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _declare(library: Any, signatures: Mapping[str, _Signature]) -> SimpleNamespace:
    """The library's functions, each with its full argtypes and restype."""
    functions = {}
    for name, (argtypes, restype) in signatures.items():
        function = getattr(library, name)
        function.argtypes = argtypes
        function.restype = restype
        functions[name] = function
    return SimpleNamespace(**functions)


def _grab(user32: Any, gdi32: Any, max_side: int) -> np.ndarray:
    """Stretch the screen into a top-down 32-bit DIB section and copy its pixels out as RGB."""
    with contextlib.ExitStack() as release:  # every handle, in reverse order, however this ends
        awareness = _checked(
            user32.SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE),
            "SetThreadDpiAwarenessContext",
        )
        # Registered first, so the thread gets its own awareness back after every handle.
        release.callback(user32.SetThreadDpiAwarenessContext, awareness)
        screen_width = _checked(user32.GetSystemMetrics(SM_CXSCREEN), "GetSystemMetrics")
        screen_height = _checked(user32.GetSystemMetrics(SM_CYSCREEN), "GetSystemMetrics")
        width, height = _scaled_size(screen_width, screen_height, max_side)
        screen_dc = _checked(user32.GetDC(None), "GetDC")
        release.callback(user32.ReleaseDC, None, screen_dc)
        memory_dc = _checked(gdi32.CreateCompatibleDC(screen_dc), "CreateCompatibleDC")
        release.callback(gdi32.DeleteDC, memory_dc)
        bits = ctypes.c_void_p()
        bitmap = _checked(
            gdi32.CreateDIBSection(
                memory_dc, _top_down_bgra(width, height), DIB_RGB_COLORS, bits, None, 0
            ),
            "CreateDIBSection",
        )
        release.callback(gdi32.DeleteObject, bitmap)  # also frees the pixels behind `bits`
        previous = _checked(gdi32.SelectObject(memory_dc, bitmap), "SelectObject")
        # A bitmap still selected into a DC cannot be deleted: the DC's own goes back first.
        release.callback(gdi32.SelectObject, memory_dc, previous)
        _checked(gdi32.SetStretchBltMode(memory_dc, HALFTONE), "SetStretchBltMode")
        # HALFTONE needs the brush origin set after it, or brushes come out misaligned.
        _checked(gdi32.SetBrushOrgEx(memory_dc, 0, 0, None), "SetBrushOrgEx")
        _checked(
            gdi32.StretchBlt(
                memory_dc,
                0,
                0,
                width,
                height,
                screen_dc,
                0,
                0,
                screen_width,
                screen_height,
                SRCCOPY | CAPTUREBLT,
            ),
            "StretchBlt",
        )
        # GDI may still be drawing in a batch: flush before reading the DIB's memory.
        _checked(gdi32.GdiFlush(), "GdiFlush")
        return _bgra_to_rgb(bits.value, width, height)


def _scaled_size(width: int, height: int, max_side: int) -> tuple[int, int]:
    """The screen's size with its aspect kept and its longer side at most max_side."""
    longer = max(width, height)
    if longer <= max_side:
        return width, height  # never upscaled
    return round(width * max_side / longer), round(height * max_side / longer)


def _top_down_bgra(width: int, height: int) -> BITMAPINFO:
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height  # negative: the first row is the top of the screen
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 8 * BGRA_BYTES
    info.bmiHeader.biCompression = BI_RGB
    return info


def _bgra_to_rgb(address: int, width: int, height: int) -> np.ndarray:
    """Copy the DIB's BGRA pixels (32-bit rows have no padding) out as an RGB array."""
    bgra = np.frombuffer(ctypes.string_at(address, width * height * BGRA_BYTES), dtype=np.uint8)
    return bgra.reshape(height, width, BGRA_BYTES)[:, :, 2::-1]


def _checked(result: Any, function: str) -> Any:
    """The result of a Win32 call; OSError with Windows' last error when it failed (0 or NULL)."""
    if not result:
        raise OSError(f"{function} failed (Windows error {ctypes.get_last_error()})")
    return result
