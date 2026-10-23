// The phone page's files: its security policy, the modules it loads, and the manifest that makes it installable.
// The server (phone/server.py) serves this page at / and its own files under /phone/, next to the avatar's web files
// (renderers, logic.js, vendor/) at their usual paths, so every URL here is absolute.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const WEB_ROOT = new URL("../../src/waifu/avatar/web/", import.meta.url);
const PAGE_URL = "https://pc.tailnet.ts.net/";

// The browser reads the page with its line breaks normalized to LF, and hashes inline scripts as it reads them.
const page = readFileSync(new URL("phone/index.html", WEB_ROOT), "utf8").replaceAll("\r\n", "\n");

const SCRIPT = /<script\b([^>]*)>([\s\S]*?)<\/script>/g;
const CSP_META = /<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]*)"\s*\/?>/i;
const STATIC_IMPORT = /(?:^|[;}])[ \t]*(?:import|export)\b\s*(?:[\w$*{}\s,]*?\bfrom\b\s*)?(["'])([^"'\n]+)\1/gm;
const DYNAMIC_IMPORT = /\bimport\(\s*(["'])([^"'\n]+)\1\s*\)/g;
// Sources that never name another computer.
const LOCAL_SOURCES = new Set(["'self'", "'none'", "'unsafe-eval'", "data:", "blob:"]);
const HASH_SOURCE = /^'sha256-[A-Za-z0-9+/]+={0,2}'$/;

/** The file a URL path of the phone server names: its own files under /phone/, the rest from the web root. */
function servedFile(path) {
  return fileURLToPath(new URL(path.replace(/^\//, ""), WEB_ROOT));
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

function importMap() {
  const [map] = scripts().filter(({ attributes }) => /type="importmap"/.test(attributes));
  return JSON.parse(map.body).imports;
}

function moduleImports(path) {
  const source = readFileSync(servedFile(path), "utf8");
  const found = [...source.matchAll(STATIC_IMPORT), ...source.matchAll(DYNAMIC_IMPORT)].map((match) => match[2]);
  return found.map((specifier) => new URL(specifier, new URL(path, PAGE_URL)).pathname);
}

/** The declarations of the stylesheet's rule whose selectors include exactly `selector`, or null. */
function cssRule(selector) {
  const css = readFileSync(new URL("phone/phone.css", WEB_ROOT), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  for (const [, selectors, declarations] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (selectors.split(",").some((one) => one.trim() === selector)) return declarations;
  }
  return null;
}

function manifest() {
  return JSON.parse(readFileSync(new URL("phone/manifest.webmanifest", WEB_ROOT), "utf8"));
}

describe("phone page", () => {
  it("sets its security policy before any script", () => {
    const meta = page.search(CSP_META);

    assert.ok(meta >= 0, "a Content-Security-Policy meta tag");
    assert.ok(meta < page.indexOf("<script"));
  });

  it("can reach only Cyrene's own server", () => {
    const directives = policy();
    const isLocal = (source) => LOCAL_SOURCES.has(source) || HASH_SOURCE.test(source);
    const foreign = Object.entries(directives).filter(([, sources]) => sources.length === 0 || !sources.every(isLocal));

    assert.deepEqual(directives["default-src"], ["'self'"]);
    for (const name of ["script-src", "connect-src", "img-src", "worker-src", "object-src", "base-uri"]) {
      assert.ok(name in directives, name);
    }
    assert.deepEqual(foreign, []);
  });

  it("runs no inline script but its import map, which the policy allows by its hash", () => {
    const inline = scripts().filter(({ attributes }) => !/\bsrc=/.test(attributes));
    const [map] = inline;
    const hash = createHash("sha256").update(map.body, "utf8").digest("base64");

    assert.equal(inline.length, 1);
    assert.match(map.attributes, /type="importmap"/);
    assert.ok(policy()["script-src"].includes(`'sha256-${hash}'`), `script-src needs 'sha256-${hash}'`);
    assert.ok(!policy()["script-src"].includes("'unsafe-inline'"));
  });

  it("loads its own module from /phone/ and every module it loads exists", () => {
    const pageModules = scripts()
      .map(({ attributes }) => attributes.match(/\bsrc="([^"]+)"/)?.[1])
      .filter(Boolean);
    const missing = [];
    const seen = new Set();
    const pending = [...pageModules];
    while (pending.length > 0) {
      const path = pending.pop();
      if (seen.has(path)) continue;
      seen.add(path);
      if (!existsSync(servedFile(path))) missing.push(path);
      else if (path.startsWith("/phone/")) pending.push(...moduleImports(path));
    }

    assert.deepEqual(pageModules, ["/phone/phone.js"]);
    assert.deepEqual(missing, []);
    assert.ok(seen.has("/phone/audio.js"));
    assert.ok(seen.has("/phone/camera.js"));
    assert.ok(seen.has("/phone/actions.js"), "the quick buttons");
    assert.ok(seen.has("/live2d.js"), "the Live2D renderer");
    assert.ok(seen.has("/avatar.js"), "the VRM renderer");
  });

  it("takes its styles from its own stylesheet under /phone/", () => {
    const sheets = [...page.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map(([, href]) => href);

    assert.deepEqual(sheets, ["/phone/phone.css"]);
    assert.ok(existsSync(servedFile(sheets[0])));
    assert.doesNotMatch(page, /<style\b|\sstyle="/);
  });

  it("maps the renderers' libraries to the vendored files by absolute paths", () => {
    const imports = importMap();

    assert.deepEqual(Object.keys(imports).sort(), [
      "@pixiv/three-vrm",
      "pixi.js",
      "three",
      "three/addons/",
      "untitled-pixi-live2d-engine/cubism",
    ]);
    for (const [specifier, path] of Object.entries(imports)) {
      assert.match(path, /^\/vendor\//, specifier);
      const stats = statSync(servedFile(path));
      assert.ok(specifier.endsWith("/") ? stats.isDirectory() : stats.isFile(), specifier);
    }
  });

  it("lets the photo thumbnails show, which are object URLs", () => {
    assert.ok(policy()["img-src"].includes("blob:"));
  });

  it("has a viewfinder that plays the camera inline and silently, by itself", () => {
    const videos = [...page.matchAll(/<video\b([^>]*)>/g)].map(([, attributes]) => attributes);

    assert.equal(videos.length, 1);
    assert.match(videos[0], /\bid="camera-view"/);
    for (const name of ["muted", "playsinline", "autoplay"]) {
      assert.match(videos[0], new RegExp(`\\s${name}(\\s|$)`), name);
    }
  });

  it("has a quick-button toggle that names, opens, and controls its panel, both hidden until filled", () => {
    const [toggle] = page.match(/<button\s[^>]*\bid="quick-toggle"[^>]*>/) ?? [];
    const [panel] = page.match(/<section\s[^>]*\bid="quick"[^>]*>/) ?? [];

    assert.ok(toggle, "the toggle");
    assert.match(toggle, /\stype="button"/);
    assert.match(toggle, /\saria-controls="quick"/);
    assert.match(toggle, /\saria-expanded="false"/);
    assert.match(toggle, /\saria-label="Tombol cepat"/);
    assert.match(toggle, /\shidden(\s|>)/);
    assert.ok(panel, "the panel");
    assert.match(panel, /\saria-label="Tombol cepat"/);
    assert.match(panel, /\shidden(\s|>)/);
  });

  it("puts the notice above the quick panel, so a toast never moves the buttons under the owner's finger", () => {
    // #chat is anchored at the bottom of the screen: what appears in it moves only what is above it.
    const chat = page.slice(page.indexOf('<main id="chat"'), page.indexOf("</main>"));
    const order = ["bubbles", "viewfinder", "notice", "quick", "composer"].map((id) => chat.indexOf(` id="${id}"`));

    assert.ok(order.every((index) => index >= 0), "every part is in the chat");
    assert.deepEqual(order, order.toSorted((a, b) => a - b));
  });

  it("gives the text box the quick toggle's room while the owner types with the panel closed", () => {
    // At 360 px with the camera button the toggle leaves the text box 116 px; without it, 168 px as before v1.35.
    const rule = cssRule('#composer:has(#text:focus:not(:placeholder-shown)) #quick-toggle[aria-expanded="false"]');

    assert.ok(rule, "the rule");
    assert.match(rule, /(^|;)\s*display:\s*none\s*(;|$)/);
  });

  it("has the canvas and the message the renderers draw into", () => {
    assert.match(page, /<canvas id="stage"/);
    assert.match(page, /id="message"/);
  });

  it("never names a Cubism Core file", () => {
    assert.doesNotMatch(page, /cubismcore[\w.-]*\.m?js/i);
  });
});

describe("phone manifest", () => {
  it("is linked from the page", () => {
    assert.match(page, /<link rel="manifest" href="\/manifest.webmanifest"/);
  });

  it("has what Chrome needs to install the page as the Cyrene app", () => {
    const app = manifest();

    assert.equal(app.name, "Cyrene");
    assert.equal(app.short_name, "Cyrene");
    assert.equal(app.start_url, "/");
    assert.equal(app.scope, "/");
    assert.equal(app.display, "standalone");
    assert.match(app.background_color, /^#[0-9a-f]{6}$/i);
    assert.match(app.theme_color, /^#[0-9a-f]{6}$/i);
    assert.equal(app.prefer_related_applications, undefined);
    assert.deepEqual(
      app.icons.map(({ src, sizes, type }) => ({ src, sizes, type })),
      [
        { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
        { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      ],
    );
  });

  it("matches the page's theme colour", () => {
    assert.match(page, new RegExp(`<meta name="theme-color" content="${manifest().theme_color}"`));
  });
});
