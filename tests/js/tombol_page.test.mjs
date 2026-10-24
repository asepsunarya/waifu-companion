// The PC's quick-button page files: its security policy, the modules it loads, and its desktop layout. The server
// (phone/desk.py) serves the page at /t/<token>/ and, under that prefix, the files of tombol/ and the phone page's
// actions.js, so every URL of the page is relative.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const WEB_ROOT = new URL("../../src/waifu/avatar/web/", import.meta.url);
const PREFIX = "/t/A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6/";
const PAGE_URL = `http://127.0.0.1:52000${PREFIX}`;
// What the server answers under its prefix besides the page, the list, and the press.
const SERVED = /^(tombol\/[^/]+|phone\/actions\.js)$/;

const page = readFileSync(new URL("tombol/index.html", WEB_ROOT), "utf8").replaceAll("\r\n", "\n");
const css = readFileSync(new URL("tombol/tombol.css", WEB_ROOT), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

const SCRIPT = /<script\b([^>]*)>([\s\S]*?)<\/script>/g;
const CSP_META = /<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]*)"\s*\/?>/i;
const STATIC_IMPORT = /(?:^|[;}])[ \t]*(?:import|export)\b\s*(?:[\w$*{}\s,]*?\bfrom\b\s*)?(["'])([^"'\n]+)\1/gm;
const DYNAMIC_IMPORT = /\bimport\(\s*(["'])([^"'\n]+)\1\s*\)/g;

/** The route under the prefix that an absolute URL path of the page names, or null when it is not under it. */
function routeOf(path) {
  return path.startsWith(PREFIX) ? path.slice(PREFIX.length) : null;
}

/** The file the server sends for a route under the prefix. */
function servedFile(route) {
  return fileURLToPath(new URL(route, WEB_ROOT));
}

function scripts() {
  return [...page.matchAll(SCRIPT)].map(([, attributes, body]) => ({ attributes, body }));
}

function policy() {
  const [, content] = page.match(CSP_META);
  return Object.fromEntries(
    content
      .split(";")
      .map((part) => part.trim().split(/\s+/))
      .filter((part) => part[0])
      .map(([name, ...sources]) => [name, sources]),
  );
}

function moduleImports(route) {
  const source = readFileSync(servedFile(route), "utf8");
  const found = [...source.matchAll(STATIC_IMPORT), ...source.matchAll(DYNAMIC_IMPORT)].map((match) => match[2]);
  return found.map((specifier) => new URL(specifier, new URL(route, PAGE_URL)).pathname);
}

/** The declarations of the stylesheet's rule whose selectors include exactly `selector`, or null. */
function cssRule(selector) {
  for (const [, selectors, declarations] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (selectors.split(",").some((one) => one.trim() === selector)) return declarations;
  }
  return null;
}

function tag(id) {
  return page.match(new RegExp(`<[a-z0-9]+\\s[^>]*\\bid="${id}"[^>]*>`))?.[0] ?? null;
}

describe("PC quick-button page", () => {
  it("is Indonesian and sets its security policy before any script", () => {
    const meta = page.search(CSP_META);

    assert.match(page, /<html lang="id">/);
    assert.ok(meta >= 0, "a Content-Security-Policy meta tag");
    assert.ok(meta < page.indexOf("<script"));
  });

  it("can reach only its own server and run only its own files", () => {
    assert.deepEqual(policy(), {
      "default-src": ["'self'"],
      "script-src": ["'self'"],
      "style-src": ["'self'"],
      "img-src": ["'self'", "data:"],
      "connect-src": ["'self'"],
      "object-src": ["'none'"],
      "base-uri": ["'none'"],
      "form-action": ["'none'"],
    });
  });

  it("runs no inline script and has no inline style", () => {
    const inline = scripts().filter(({ attributes }) => !/\bsrc=/.test(attributes));

    assert.deepEqual(inline, []);
    assert.doesNotMatch(page, /<style\b|\sstyle="/);
  });

  it("loads its module and stylesheet by relative URLs, so they keep the token's prefix", () => {
    const sources = [...page.matchAll(/\s(?:src|href)="([^"]*)"/g)].map(([, url]) => url);
    const modules = scripts().map(({ attributes }) => attributes.match(/\bsrc="([^"]+)"/)?.[1]);
    const sheets = [...page.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map(([, href]) => href);

    assert.deepEqual(modules, ["tombol/tombol.js"]);
    assert.match(scripts()[0].attributes, /type="module"/);
    assert.deepEqual(sheets, ["tombol/tombol.css"]);
    for (const url of sources) assert.doesNotMatch(url, /^(\/|[a-z]+:\/\/)/i, url);
  });

  it("loads only modules the server serves under its prefix, and every one exists", () => {
    const seen = new Set();
    const pending = [new URL("tombol/tombol.js", PAGE_URL).pathname];
    while (pending.length > 0) {
      const path = pending.pop();
      if (seen.has(path)) continue;
      seen.add(path);
      const route = routeOf(path);
      assert.ok(route !== null && SERVED.test(route), `${path} is not served`);
      assert.ok(existsSync(servedFile(route)), `${route} is missing`);
      pending.push(...moduleImports(route));
    }

    const routes = [...seen].map(routeOf).sort();
    assert.deepEqual(routes, ["phone/actions.js", "tombol/desk.js", "tombol/tombol.js"]);
  });

  it("asks the browser for no icon, which no route serves", () => {
    assert.match(page, /<link rel="icon" href="data:,"/);
  });

  it("has the message, the notice, and the buttons' section the script fills", () => {
    assert.match(tag("message"), /\srole="alert"/);
    assert.match(page, /id="message"[^>]*>Memuat tombol…</);
    assert.match(tag("notice"), /\srole="status"/);
    assert.match(tag("notice"), /\shidden(\s|>)/);
    assert.match(tag("groups"), /^<section\s/);
    assert.match(tag("groups"), /\saria-label="Tombol cepat"/);
    assert.match(tag("groups"), /\shidden(\s|>)/);
    assert.match(page, /<h1>Tombol cepat<\/h1>/);
  });
});

describe("PC quick-button page styles", () => {
  it("lays the buttons out in a grid wide enough for a desktop window", () => {
    const rule = cssRule(".quick-buttons");

    assert.ok(rule, "the rule");
    assert.match(rule, /display:\s*grid/);
    assert.match(rule, /grid-template-columns:\s*repeat\(auto-fill,\s*minmax\(120px,\s*1fr\)\)/);
  });

  it("gives every button at least 44 px to aim at", () => {
    assert.match(cssRule(".quick-action"), /min-height:\s*44px/);
  });

  it("puts the groups side by side, one column in a narrow window", () => {
    const rule = cssRule("#groups");

    assert.match(rule, /display:\s*grid/);
    assert.match(rule, /minmax\(min\(\d+px,\s*100%\),\s*1fr\)/);
  });

  it("shows the running button apart from the waiting ones", () => {
    assert.ok(cssRule('.quick-action[aria-busy="true"]'));
    assert.ok(cssRule('.quick-action[aria-disabled="true"]'));
  });

  it("follows the system's dark mode with a background of its own", () => {
    assert.match(css, /@media\s*\(prefers-color-scheme:\s*dark\)/);
    assert.match(cssRule("body"), /background/);
  });

  it("hides what is hidden, whatever its display", () => {
    assert.match(cssRule("[hidden]"), /display:\s*none\s*!important/);
  });
});
