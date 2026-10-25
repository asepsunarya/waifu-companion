// The phone page's service worker, run against a fake worker scope: it keeps a copy of the page's own files only,
// network first, and leaves Cyrene's answers, settings, model and Core to the network.
import assert from "node:assert/strict";
import { before, beforeEach, describe, it } from "node:test";

const ORIGIN = "https://pc.tailnet.ts.net";

class FakeCache {
  constructor() {
    this.entries = new Map();
  }

  async put(key, response) {
    this.entries.set(key, response);
  }

  async match(key) {
    return this.entries.get(key);
  }
}

const caches = {
  stores: new Map(),
  async open(name) {
    if (!this.stores.has(name)) this.stores.set(name, new FakeCache());
    return this.stores.get(name);
  },
  async keys() {
    return [...this.stores.keys()];
  },
  async delete(name) {
    return this.stores.delete(name);
  },
};

const scope = {
  location: { origin: ORIGIN, href: `${ORIGIN}/sw.js` },
  listeners: new Map(),
  caches,
  network: null,
  requests: [],
  skippedWaiting: false,
  clients: {
    claimed: false,
    async claim() {
      this.claimed = true;
    },
  },
  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  },
  async skipWaiting() {
    this.skippedWaiting = true;
  },
  async fetch(request) {
    this.requests.push(request);
    return this.network(request);
  },
};

function response(status = 200, type = "basic") {
  return {
    status,
    ok: status >= 200 && status < 300,
    type,
    clone() {
      return { ...this, copy: true };
    },
  };
}

function offline() {
  throw new TypeError("Failed to fetch");
}

/** Dispatches a fetch event; `responded` is what the worker answered with, or null when it let the request pass. */
function dispatchFetch(path, method = "GET") {
  const event = {
    request: { url: new URL(path, ORIGIN).href, method },
    responded: null,
    waits: [],
    respondWith(promise) {
      this.responded = Promise.resolve(promise);
    },
    waitUntil(promise) {
      this.waits.push(promise);
    },
  };
  scope.listeners.get("fetch")(event);
  return event;
}

async function settled(event) {
  const answer = await event.responded;
  await Promise.all(event.waits);
  return answer;
}

function cachedPaths() {
  return [...caches.stores.values()].flatMap((cache) => [...cache.entries.keys()]);
}

before(async () => {
  globalThis.self = scope;
  await import("../../src/waifu/avatar/web/phone/sw.js");
});

beforeEach(() => {
  caches.stores.clear();
  scope.requests = [];
  scope.network = () => response();
});

describe("phone service worker", () => {
  it("leaves Cyrene's answers, settings, model, Core and libraries to the network", () => {
    const passing = [
      ["/chat", "POST"],
      ["/talk", "POST"],
      ["/pair", "POST"],
      ["/action", "POST"],
      ["/", "POST"],
      ["/config.json", "GET"],
      ["/actions", "GET"],
      ["/model/Cyrene.model3.json", "GET"],
      ["/model/Cyrene.moc3", "GET"],
      ["/model/Cyrene.4096/texture_00.png", "GET"],
      ["/model.vrm", "GET"],
      ["/live2d/live2dcubismcore.min.js", "GET"],
      ["/live2d.js", "GET"],
      ["/vendor/pixi/pixi.min.mjs", "GET"],
      ["https://example.com/", "GET"],
    ];

    for (const [path, method] of passing) {
      assert.equal(dispatchFetch(path, method).responded, null, `${method} ${path}`);
    }
    assert.deepEqual(cachedPaths(), []);
  });

  it("serves the page from the network first and keeps a copy", async () => {
    const fresh = response();
    scope.network = () => fresh;

    const answer = await settled(dispatchFetch("/"));

    assert.equal(answer, fresh);
    assert.deepEqual(cachedPaths(), ["/"]);
  });

  it("opens the kept page when the PC cannot be reached", async () => {
    await settled(dispatchFetch("/phone/phone.js"));
    scope.network = offline;

    const answer = await settled(dispatchFetch("/phone/phone.js"));

    assert.equal(answer.copy, true);
  });

  it("opens the kept page when the PC answers with a server error", async () => {
    // tailscale serve answers 502 for the PC while Cyrene (waifu run) is not running.
    const shell = [
      "/",
      "/phone/phone.js",
      "/phone/audio.js",
      "/phone/camera.js",
      "/phone/actions.js",
      "/phone/voice.js",
      "/phone/card.js",
      "/phone/phone.css",
    ];
    for (const path of shell) await settled(dispatchFetch(path));

    for (const status of [500, 502, 503, 504]) {
      scope.network = () => response(status);
      for (const path of shell) {
        const answer = await settled(dispatchFetch(path));
        assert.equal(answer.copy, true, `${status} ${path}`);
        assert.equal(answer.status, 200, `${status} ${path}`);
      }
    }
  });

  it("passes on what the PC refuses, even with a copy kept", async () => {
    await settled(dispatchFetch("/"));
    const refused = response(403);
    scope.network = () => refused;

    assert.equal(await settled(dispatchFetch("/")), refused);
  });

  it("keeps one copy of the page whatever its query", async () => {
    await settled(dispatchFetch("/?source=homescreen"));
    scope.network = offline;

    const answer = await settled(dispatchFetch("/"));

    assert.equal(answer.copy, true);
    assert.deepEqual(cachedPaths(), ["/"]);
  });

  it("keeps every file of the page's shell", async () => {
    const shell = [
      "/",
      "/phone/phone.js",
      "/phone/audio.js",
      "/phone/camera.js",
      "/phone/actions.js",
      "/phone/voice.js",
      "/phone/card.js",
      "/phone/phone.css",
      "/manifest.webmanifest",
      "/icon-192.png",
      "/icon-512.png",
    ];

    for (const path of shell) await settled(dispatchFetch(path));

    assert.deepEqual(cachedPaths().sort(), [...shell].sort());
  });

  it("never keeps an error or a redirect", async () => {
    scope.network = () => response(500);
    const failed = await settled(dispatchFetch("/"));
    scope.network = () => response(0, "opaqueredirect");
    await settled(dispatchFetch("/phone/audio.js"));

    assert.equal(failed.status, 500);
    assert.deepEqual(cachedPaths(), []);
  });

  it("fails like the network when there is no copy", async () => {
    scope.network = offline;

    await assert.rejects(settled(dispatchFetch("/")), TypeError);
  });

  it("takes over at once and drops the copies of older versions only", async () => {
    await caches.open("cyrene-shell-v0");
    await caches.open("someone-else");
    const install = { waits: [], waitUntil(promise) { this.waits.push(promise); } };
    const activate = { waits: [], waitUntil(promise) { this.waits.push(promise); } };

    scope.listeners.get("install")(install);
    await Promise.all(install.waits);
    await settled(dispatchFetch("/"));
    scope.listeners.get("activate")(activate);
    await Promise.all(activate.waits);

    const names = await caches.keys();
    assert.equal(scope.skippedWaiting, true);
    assert.equal(scope.clients.claimed, true);
    assert.ok(!names.includes("cyrene-shell-v0"));
    assert.ok(names.includes("someone-else"));
    assert.equal(names.filter((name) => name.startsWith("cyrene-shell-")).length, 1);
  });
});
