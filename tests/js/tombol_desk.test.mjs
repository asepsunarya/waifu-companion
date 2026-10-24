// The PC's quick-button page, its pure part (tombol/desk.js): the requests to the page's own server and what their
// problems tell the owner. `fetch` is a fake: nothing here reaches a server.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  PROBLEM_KINDS,
  REQUEST_TIMEOUT_MS,
  deskProblem,
  deskText,
  requestDesk,
} from "../../src/waifu/avatar/web/tombol/desk.js";

function jsonResponse(body, status = 200) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}

/** A fetch that records each call and answers with `answer(route, init)`. */
function recordingFetch(answer) {
  const calls = [];
  const fetchFn = async (route, init) => {
    calls.push({ route, init });
    return answer(route, init);
  };
  return { calls, fetchFn };
}

describe("deskProblem", () => {
  it("tells a press the PC no longer has, one pressed too often, and one that waits for the last", () => {
    assert.equal(deskProblem("action", 404), "action_unknown");
    assert.equal(deskProblem("action", 429), "action_limit");
    assert.equal(deskProblem("action", 409), "action_busy");
  });

  it("reads a list the server does not know as a page whose server was closed", () => {
    // Another `waifu tombol` on the same port has another token: this page's server is gone.
    assert.equal(deskProblem("actions", 404), "closed");
  });

  it("tells a refused address, a refused request, and anything else as a problem on the PC", () => {
    assert.equal(deskProblem("action", 403), "forbidden");
    assert.equal(deskProblem("actions", 403), "forbidden");
    assert.equal(deskProblem("action", 400), "bad_request");
    assert.equal(deskProblem("action", 415), "bad_request");
    assert.equal(deskProblem("action", 413), "bad_request");
    assert.equal(deskProblem("action", 411), "bad_request");
    for (const status of [500, 502, 405, 418]) assert.equal(deskProblem("action", status), "server", String(status));
  });

  it("only names problems that have a text", () => {
    const routes = ["action", "actions"];
    const statuses = [400, 403, 404, 405, 409, 411, 413, 415, 429, 500, 503];
    for (const route of routes) {
      for (const status of statuses) assert.ok(PROBLEM_KINDS.includes(deskProblem(route, status)));
    }
  });
});

describe("requestDesk", () => {
  it("asks for the list as a GET without cookies, never from the cache", async () => {
    const { calls, fetchFn } = recordingFetch(() => jsonResponse({ groups: [] }));

    const result = await requestDesk(fetchFn, "actions");

    assert.deepEqual(result, { data: { groups: [] } });
    const [{ route, init }] = calls;
    assert.equal(route, "actions");
    assert.equal(init.method, "GET");
    assert.equal(init.credentials, "omit");
    assert.equal(init.cache, "no-store");
    assert.ok(init.signal instanceof AbortSignal);
    assert.equal(init.body, undefined);
  });

  it("posts a body with its content type", async () => {
    const { calls, fetchFn } = recordingFetch(() => jsonResponse({ ok: true, message: "Lampu kamar dinyalakan." }));

    const result = await requestDesk(fetchFn, "action", { body: '{"id":"rt.tidur"}', contentType: "application/json" });

    assert.deepEqual(result, { data: { ok: true, message: "Lampu kamar dinyalakan." } });
    const [{ route, init }] = calls;
    assert.equal(route, "action");
    assert.equal(init.method, "POST");
    assert.deepEqual(init.headers, { "Content-Type": "application/json" });
    assert.equal(init.body, '{"id":"rt.tidur"}');
  });

  it("keeps the page's own Origin on every request, under the server's no-referrer policy", async () => {
    // The server answers with `Referrer-Policy: no-referrer` and refuses a POST whose Origin is not its own. Under the
    // Fetch spec, a POST that is not in "cors" mode carries `Origin: null` with that policy (Firefox follows it), so the
    // requests use "cors" mode (their URLs are relative, so still same-origin) and a same-origin referrer policy.
    const { calls, fetchFn } = recordingFetch(() => jsonResponse({ ok: true, message: "" }));

    await requestDesk(fetchFn, "actions");
    await requestDesk(fetchFn, "action", { body: "{}", contentType: "application/json" });

    assert.equal(calls.length, 2);
    for (const { route, init } of calls) {
      assert.equal(init.mode, "cors", route);
      assert.equal(init.referrerPolicy, "same-origin", route);
    }
  });

  it("turns an error status into its problem", async () => {
    const { fetchFn } = recordingFetch(() => jsonResponse({ error: "Terlalu sering." }, 429));

    assert.deepEqual(await requestDesk(fetchFn, "action", { body: "{}", contentType: "application/json" }), {
      problem: "action_limit",
    });
  });

  it("reads a server that does not answer as a page whose server was closed", async () => {
    const fetchFn = async () => {
      throw new TypeError("Failed to fetch");
    };

    assert.deepEqual(await requestDesk(fetchFn, "actions"), { problem: "closed" });
  });

  it("reads an answer that is not JSON as a bad reply", async () => {
    const fetchFn = async () => ({
      status: 200,
      ok: true,
      json: async () => JSON.parse("<!doctype html>"),
    });

    assert.deepEqual(await requestDesk(fetchFn, "actions"), { problem: "bad_reply" });
  });

  it("gives up after its time and says so", async () => {
    const fetchFn = (route, init) =>
      new Promise((resolve, reject) => {
        init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });

    assert.deepEqual(await requestDesk(fetchFn, "action", { body: "{}", contentType: "x", timeoutMs: 5 }), {
      problem: "timeout",
    });
  });

  it("waits a minute by default: a routine of several devices takes a while", () => {
    assert.equal(REQUEST_TIMEOUT_MS, 60_000);
  });
});

describe("deskText", () => {
  it("has an Indonesian text for every problem", () => {
    for (const kind of PROBLEM_KINDS) {
      const text = deskText(kind);
      assert.ok(text.length > 10, kind);
      assert.match(text, /\.$/, kind);
    }
  });

  it("tells a closed page how to open a new one", () => {
    assert.match(deskText("closed"), /waifu tombol/);
  });

  it("reads an unknown problem as a problem on the PC", () => {
    assert.equal(deskText("no-such-kind"), deskText("server"));
  });

  it("tells the PC's own refusals as the phone page does", () => {
    assert.equal(deskText("action_limit"), "Tombolnya terlalu sering ditekan. Tunggu sebentar, lalu coba lagi.");
    assert.equal(deskText("action_busy"), "Perintah tadi masih jalan. Tunggu sebentar.");
  });
});
