"""The viewer page's files: every module it imports exists, and the vendored ones are listed.

A browser fails the whole module graph when one import cannot be fetched, so a
single missing file leaves the page without an avatar. These tests resolve the
graph the way the browser does (the import map in index.html, then relative
URLs) and look the files up under the web root; no browser and no server.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from waifu.avatar.server import WEB_ROOT

PAGE_URL = "http://127.0.0.1:9882/"
VENDOR = WEB_ROOT / "vendor"

_IMPORT_MAP = re.compile(r'<script type="importmap">(.*?)</script>', re.DOTALL)
_MODULE_SCRIPT = re.compile(r'<script\b[^>]*\btype="module"[^>]*\bsrc="([^"]+)"')
# A static `import ... from "x"`, `import "x"` or `export ... from "x"` at the start of a
# statement; `import(...)` calls and imports quoted inside comments do not match.
_STATIC_IMPORT = re.compile(
    r"""(?:^|[;}])[ \t]*(?:import|export)\b\s*(?:[\w$*{}\s,]*?\bfrom\b\s*)?(["'])([^"'\n]+)\1""",
    re.MULTILINE,
)


def import_map() -> dict[str, str]:
    (text,) = _IMPORT_MAP.findall((WEB_ROOT / "index.html").read_text(encoding="utf-8"))
    return json.loads(text)["imports"]


def resolve(specifier: str, referrer: str, imports: dict[str, str]) -> str | None:
    """The URL a browser fetches for `specifier` imported by `referrer`; None if it cannot."""
    if specifier.startswith(("./", "../", "/")):
        return urljoin(referrer, specifier)
    if specifier in imports:
        return urljoin(PAGE_URL, imports[specifier])
    prefixes = [key for key in imports if key.endswith("/") and specifier.startswith(key)]
    if not prefixes:
        return None
    prefix = max(prefixes, key=len)
    return urljoin(PAGE_URL, imports[prefix] + specifier.removeprefix(prefix))


def local_file(url: str) -> Path:
    return WEB_ROOT / urlsplit(url).path.lstrip("/")


def module_graph() -> tuple[set[str], list[str]]:
    """Every module URL the page loads, and the imports that cannot load (as text)."""
    imports = import_map()
    page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    pending = [urljoin(PAGE_URL, src) for src in _MODULE_SCRIPT.findall(page)]
    seen: set[str] = set()
    broken: list[str] = []
    while pending:
        url = pending.pop()
        if url in seen:
            continue
        seen.add(url)
        source = local_file(url).read_text(encoding="utf-8")
        for _, specifier in _STATIC_IMPORT.findall(source):
            target = resolve(specifier, url, imports)
            if target is None or urlsplit(target).netloc != urlsplit(PAGE_URL).netloc:
                broken.append(f"{urlsplit(url).path} imports {specifier!r}: not resolvable")
            elif not local_file(target).is_file():
                broken.append(f"{urlsplit(url).path} imports {urlsplit(target).path}: missing")
            else:
                pending.append(target)
    return seen, broken


def test_every_module_the_page_imports_is_a_file_under_the_web_root() -> None:
    _, broken = module_graph()

    assert broken == []


def test_the_module_graph_reaches_three_the_gltf_loader_and_three_vrm() -> None:
    loaded, _ = module_graph()

    paths = {urlsplit(url).path for url in loaded}
    assert {
        "/avatar.js",
        "/logic.js",
        "/vendor/three/three.module.js",
        "/vendor/three/three.core.js",
        "/vendor/three/addons/loaders/GLTFLoader.js",
        "/vendor/three/addons/utils/BufferGeometryUtils.js",
        "/vendor/three-vrm/three-vrm.module.min.js",
    } <= paths


def test_the_page_title_is_not_the_title_of_cyrene_s_console_window() -> None:
    # Closing the autostart console titled "Cyrene" stops her; closing the mascot does not,
    # so the mascot must not carry the same name in the taskbar and Alt+Tab.
    page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")

    (title,) = re.findall(r"<title>(.*?)</title>", page)

    assert title == "Avatar Cyrene"


def test_vendor_md_lists_every_vendored_library_file() -> None:
    listed = (VENDOR / "VENDOR.md").read_text(encoding="utf-8")
    files = [path.relative_to(VENDOR).as_posix() for path in VENDOR.rglob("*.js") if path.is_file()]

    assert files
    assert [name for name in files if f"`{name}`" not in listed] == []
