"""The viewer page's files: every module it imports exists, and the vendored ones are listed.

A browser fails the whole module graph when one import cannot be fetched, so a
single missing file leaves the page without an avatar. These tests resolve the
graph the way the browser does (the import map in index.html, then relative
URLs) and look the files up under the web root; no browser and no server. The
page picks its renderer at run time, so the graph starts from every module it can
load: module scripts, the imports of its inline modules, and its `import("...")`
calls (the VRM renderer, the Live2D renderer, the interaction layer).
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
_INLINE_MODULE = re.compile(
    r'<script\b(?![^>]*\bsrc=)[^>]*\btype="module"[^>]*>(.*?)</script>', re.DOTALL
)
# A static `import ... from "x"`, `import "x"` or `export ... from "x"` at the start of a
# statement; `import(...)` calls and imports quoted inside comments do not match.
_STATIC_IMPORT = re.compile(
    r"""(?:^|[;}])[ \t]*(?:import|export)\b\s*(?:[\w$*{}\s,]*?\bfrom\b\s*)?(["'])([^"'\n]+)\1""",
    re.MULTILINE,
)
_DYNAMIC_IMPORT = re.compile(r"""\bimport\(\s*(["'])([^"'\n]+)\1\s*\)""")
_DRAG_REGION = re.compile(r"-webkit-app-region\s*:\s*drag\b")
_CSP_META = re.compile(
    r'<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]*)"\s*/?>', re.IGNORECASE
)
# Sources that never name another computer: the page's own server, nothing, inline code and
# eval (PixiJS compiles its shaders' uniform code, the Cubism Core is WebAssembly), and data
# the page made itself.
_LOCAL_SOURCES = {"'self'", "'none'", "'unsafe-inline'", "'unsafe-eval'", "data:", "blob:"}
# A file name like live2dcubismcore.min.js (the global Live2DCubismCore is not a file).
_CORE_FILE = re.compile(r"cubismcore[\w.-]*\.m?js", re.IGNORECASE)


def page_text() -> str:
    return (WEB_ROOT / "index.html").read_text(encoding="utf-8")


def import_map() -> dict[str, str]:
    (text,) = _IMPORT_MAP.findall(page_text())
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


def page_imports(page: str) -> list[str]:
    """Every specifier index.html itself loads, statically or with import()."""
    specifiers = list(_MODULE_SCRIPT.findall(page))
    for body in _INLINE_MODULE.findall(page):
        specifiers += [specifier for _, specifier in _STATIC_IMPORT.findall(body)]
    specifiers += [specifier for _, specifier in _DYNAMIC_IMPORT.findall(page)]
    return specifiers


def module_graph() -> tuple[set[str], list[str]]:
    """Every module URL the page loads, and the imports that cannot load (as text)."""
    imports = import_map()
    pending = [(PAGE_URL, specifier) for specifier in page_imports(page_text())]
    seen: set[str] = set()
    broken: list[str] = []
    while pending:
        referrer, specifier = pending.pop()
        target = resolve(specifier, referrer, imports)
        if target is None or urlsplit(target).netloc != urlsplit(PAGE_URL).netloc:
            broken.append(f"{urlsplit(referrer).path} imports {specifier!r}: not resolvable")
        elif not local_file(target).is_file():
            broken.append(f"{urlsplit(referrer).path} imports {urlsplit(target).path}: missing")
        elif target not in seen:
            seen.add(target)
            source = local_file(target).read_text(encoding="utf-8")
            pending += [(target, specifier) for _, specifier in _STATIC_IMPORT.findall(source)]
    return seen, broken


def loaded_paths() -> set[str]:
    loaded, _ = module_graph()
    return {urlsplit(url).path for url in loaded}


def test_every_module_the_page_imports_is_a_file_under_the_web_root() -> None:
    _, broken = module_graph()

    assert broken == []


def test_the_module_graph_reaches_three_the_gltf_loader_and_three_vrm() -> None:
    assert {
        "/avatar.js",
        "/logic.js",
        "/vendor/three/three.module.js",
        "/vendor/three/three.core.js",
        "/vendor/three/addons/loaders/GLTFLoader.js",
        "/vendor/three/addons/utils/BufferGeometryUtils.js",
        "/vendor/three-vrm/three-vrm.module.min.js",
    } <= loaded_paths()


def test_the_module_graph_reaches_the_live2d_renderer_pixi_and_the_live2d_engine() -> None:
    assert {
        "/live2d.js",
        "/logic.js",
        "/vendor/pixi/pixi.min.mjs",
        "/vendor/live2d-engine/cubism.es.js",
    } <= loaded_paths()


def test_the_page_loads_the_interaction_layer() -> None:
    assert "/interaction.js" in loaded_paths()


def test_the_page_never_references_a_cubism_core_file_in_the_repository() -> None:
    # Live2D's proprietary Cubism Core is never committed: the page loads it from the URL
    # in /config.json, which Cyrene serves from avatar.live2d_core outside the repository.
    assert _CORE_FILE.findall(page_text()) == []
    assert [path for path in WEB_ROOT.rglob("*") if "cubismcore" in path.name.lower()] == []


def content_security_policy() -> dict[str, list[str]]:
    (policy,) = _CSP_META.findall(page_text())
    directives = (part.split() for part in policy.split(";") if part.strip())
    return {name: sources for name, *sources in directives}


def test_the_page_can_reach_only_its_own_server() -> None:
    # A downloaded model3.json may name https://... or //host/... files, which the Live2D
    # engine would fetch as they are: the policy keeps the page on Cyrene's own server.
    policy = content_security_policy()

    assert {"default-src", "connect-src", "img-src"} <= policy.keys()
    assert policy["default-src"] == ["'self'"]
    assert {
        name: sources
        for name, sources in policy.items()
        if not sources or not set(sources) <= _LOCAL_SOURCES
    } == {}


def test_the_content_security_policy_comes_before_every_script() -> None:
    page = page_text()
    meta = _CSP_META.search(page)

    assert meta is not None
    assert meta.start() < page.index("<script")


def test_the_page_shows_when_she_does_not_hear_master_also_as_a_mascot() -> None:
    page = page_text()

    assert re.search(r'<p id="hearing"[^>]*\bhidden\b[^>]*>Tidak mendengarkan</p>', page)
    assert 'case "hearing":' in page
    assert re.search(r"import \{[^}]*\bhearingBadgeShown\b[^}]*\} from \"\./logic\.js\"", page)
    # The mascot fades #status and #close out until hovered: a fullscreen game's player must still
    # see that she does not listen.
    mascot_selectors = re.findall(r'html\[data-mode="mascot"\][^{]*\{', page)
    assert not any("#hearing" in selector for selector in mascot_selectors)


def test_a_new_connection_s_hello_hides_a_hearing_badge_left_by_an_earlier_run() -> None:
    # A browser tab outlives `waifu run`, whose restart clears the pause without a hearing event;
    # within a run, hello comes first and the bus then replays a pause still holding.
    (hello,) = re.findall(r'case "hello":(.*?)break;', page_text(), re.DOTALL)

    assert "hearingElement.hidden = true;" in hello


def test_the_page_has_no_window_drag_region() -> None:
    # The mascot is dragged by interaction.js through Electron; a drag region would
    # swallow the pats, pokes, and double-clicks on her.
    assert _DRAG_REGION.findall(page_text()) == []


def test_the_page_title_is_not_the_title_of_cyrene_s_console_window() -> None:
    # Closing the autostart console titled "Cyrene" stops her; closing the mascot does not,
    # so the mascot must not carry the same name in the taskbar and Alt+Tab.
    (title,) = re.findall(r"<title>(.*?)</title>", page_text())

    assert title == "Avatar Cyrene"


def test_vendor_md_lists_every_vendored_library_file() -> None:
    listed = (VENDOR / "VENDOR.md").read_text(encoding="utf-8")
    files = [
        path.relative_to(VENDOR).as_posix()
        for path in VENDOR.rglob("*")
        if path.suffix in {".js", ".mjs"} and path.is_file()
    ]

    assert files
    assert [name for name in files if f"`{name}`" not in listed] == []
