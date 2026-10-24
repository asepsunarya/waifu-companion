// The PC's quick-button page's own behaviour (tombol/tombol.js), run against a fake DOM and a fake fetch: no browser
// and no server.
import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { deskText } from "../../src/waifu/avatar/web/tombol/desk.js";

const TOMBOL_JS = new URL("../../src/waifu/avatar/web/tombol/tombol.js", import.meta.url);
const ELEMENT_IDS = ["message", "notice", "groups"];
const HIDDEN_AT_START = new Set(["notice", "groups"]);
const QUICK = {
  groups: [
    {
      name: "Lampu kamar",
      actions: [
        { id: "dev.lampu_kamar.on", label: "Nyala" },
        { id: "dev.lampu_kamar.off", label: "Mati" },
      ],
    },
    { name: "Rutinitas", actions: [{ id: "rt.tidur", label: "Mode tidur" }] },
  ],
};
const LAMP_ON = { ok: true, message: "Lampu kamar: perintah 'on' berhasil (simulasi)." };

class FakeElement {
  constructor(tagName, id = "") {
    this.tagName = tagName.toUpperCase();
    this.id = id;
    this.children = [];
    this.parentElement = null;
    this.hidden = HIDDEN_AT_START.has(id);
    this.textContent = "";
    this.className = "";
    this.type = "";
    this.attributes = new Map();
    this.listeners = new Map();
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  dispatch(type, fields = {}) {
    for (const listener of this.listeners.get(type) ?? []) listener({ type, preventDefault() {}, ...fields });
  }

  append(...nodes) {
    for (const node of nodes) {
      node.parentElement = this;
      this.children.push(node);
    }
  }

  replaceChildren(...nodes) {
    for (const child of this.children) child.parentElement = null;
    this.children = [];
    this.append(...nodes);
  }
}

function jsonResponse(body, status = 200) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}

async function settle() {
  for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

/** Lets pending promises run without timers, for tests that mock setTimeout. */
async function flush() {
  for (let i = 0; i < 20; i++) await new Promise((resolve) => setImmediate(resolve));
}

let loads = 0;
const saved = {};

/**
 * Loads tombol.js afresh against a page whose `actions` answers `actions` with `listStatus` (or fails like a closed
 * server with `listError`), and whose `action` answers `actionReply` with `actionStatus`. Every request is recorded in
 * `requests` ({route, init}).
 */
async function openPage({
  actions = QUICK,
  listStatus = 200,
  listError = null,
  actionReply = LAMP_ON,
  actionStatus = 200,
} = {}) {
  const elements = new Map(ELEMENT_IDS.map((id) => [id, new FakeElement(id === "groups" ? "section" : "p", id)]));
  elements.get("message").textContent = "Memuat tombol…";
  const requests = [];
  const fakes = {
    document: {
      getElementById: (id) => elements.get(id) ?? null,
      createElement: (tagName) => new FakeElement(tagName),
    },
    fetch: async (route, init) => {
      requests.push({ route, init });
      if (route === "actions") {
        if (listError) throw listError;
        return jsonResponse(actions, listStatus);
      }
      if (route === "action") return jsonResponse(actionReply, actionStatus);
      return jsonResponse({ error: "Tidak ditemukan." }, 404);
    },
  };
  for (const [name, value] of Object.entries(fakes)) {
    if (!(name in saved)) saved[name] = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
  }
  loads += 1;
  await import(`${TOMBOL_JS.href}?load=${loads}`);
  await settle();
  return { element: (id) => elements.get(id), requests };
}

afterEach(() => {
  for (const [name, descriptor] of Object.entries(saved)) {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor);
    else delete globalThis[name];
    delete saved[name];
  }
});

function groupsOf(page) {
  return page.element("groups").children.map((group) => {
    const [heading, list] = group.children;
    return { group, heading, buttons: list.children, labels: list.children.map((button) => button.textContent) };
  });
}

function buttonOf(page, label) {
  return groupsOf(page)
    .flatMap(({ buttons }) => buttons)
    .find((button) => button.textContent === label);
}

async function press(page, label) {
  buttonOf(page, label).dispatch("click");
  await settle();
}

const actionRequests = (page) => page.requests.filter(({ route }) => route === "action");

/** Holds every POST of `action` until `answer()`; the list goes to the page's fake as before. */
function holdActions(page) {
  const fakeFetch = globalThis.fetch;
  const held = { answer: null };
  globalThis.fetch = (route, init) => {
    if (route !== "action") return fakeFetch(route, init);
    page.requests.push({ route, init });
    return new Promise((resolve) => (held.answer = () => resolve(jsonResponse(LAMP_ON))));
  };
  return held;
}

describe("PC quick-button page", () => {
  it("asks its own server for the buttons by a relative URL", async () => {
    const page = await openPage();

    const [request] = page.requests;
    assert.equal(request.route, "actions");
    assert.equal(request.init.method, "GET");
    assert.equal(request.init.mode, "cors");
  });

  it("shows a group per device and the routines, each named by its heading, in place of the message", async () => {
    const page = await openPage();
    const groups = groupsOf(page);

    assert.equal(page.element("message").hidden, true);
    assert.equal(page.element("groups").hidden, false);
    assert.deepEqual(
      groups.map(({ heading, labels }) => ({ heading: heading.textContent, labels })),
      [
        { heading: "Lampu kamar", labels: ["Nyala", "Mati"] },
        { heading: "Rutinitas", labels: ["Mode tidur"] },
      ],
    );
    for (const { group, heading, buttons } of groups) {
      assert.equal(group.attributes.get("role"), "group");
      assert.equal(heading.tagName, "H2");
      assert.ok(heading.id);
      assert.equal(group.attributes.get("aria-labelledby"), heading.id);
      for (const button of buttons) {
        assert.equal(button.tagName, "BUTTON");
        assert.equal(button.type, "button");
        assert.equal(button.className, "quick-action");
      }
    }
    assert.notEqual(groups[0].heading.id, groups[1].heading.id);
  });

  it("says so when the PC has no buttons", async () => {
    const page = await openPage({ actions: { groups: [] } });

    assert.equal(page.element("message").hidden, false);
    assert.equal(page.element("message").textContent, "Belum ada tombol cepat di PC.");
    assert.equal(page.element("groups").hidden, true);
  });

  it("says why the buttons cannot show", async () => {
    const cases = [
      [{ listError: new TypeError("Failed to fetch") }, "closed"],
      [{ listStatus: 404, actions: { error: "Tidak ditemukan." } }, "closed"],
      [{ listStatus: 403, actions: { error: "Hanya 127.0.0.1." } }, "forbidden"],
      [{ listStatus: 500, actions: { error: "Rusak." } }, "server"],
      [{ actions: { groups: "Lampu" } }, "bad_reply"],
    ];
    for (const [options, kind] of cases) {
      const page = await openPage(options);

      assert.equal(page.element("message").textContent, deskText(kind), kind);
      assert.equal(page.element("message").hidden, false);
      assert.equal(page.element("groups").hidden, true);
    }
  });

  it("sends only the clicked button's id, and toasts what the PC did", async () => {
    const page = await openPage();

    await press(page, "Nyala");

    const [request] = actionRequests(page);
    assert.equal(request.init.method, "POST");
    assert.equal(request.init.headers["Content-Type"], "application/json");
    assert.equal(request.init.body, '{"id":"dev.lampu_kamar.on"}');
    assert.equal(page.element("notice").hidden, false);
    assert.equal(page.element("notice").textContent, LAMP_ON.message);
  });

  it("toasts a device's failure as the PC tells it", async () => {
    const page = await openPage({ actionReply: { ok: false, message: "Lampu kamar tidak bisa dihubungi." } });

    await press(page, "Mati");

    assert.equal(page.element("notice").textContent, "Lampu kamar tidak bisa dihubungi.");
  });

  it("hides the toast after six seconds", async (t) => {
    const page = await openPage();
    t.mock.timers.enable({ apis: ["setTimeout"] });
    buttonOf(page, "Nyala").dispatch("click");
    await flush();

    t.mock.timers.tick(5_999);
    assert.equal(page.element("notice").hidden, false);
    t.mock.timers.tick(1);

    assert.equal(page.element("notice").hidden, true);
  });

  it("marks the clicked button busy and every button unavailable until the PC answers", async () => {
    const page = await openPage();
    const held = holdActions(page);
    const buttons = groupsOf(page).flatMap((group) => group.buttons);
    const pressed = buttonOf(page, "Nyala");

    await press(page, "Nyala");
    const busy = pressed.attributes.get("aria-busy");
    const whileRunning = buttons.map((button) => button.attributes.get("aria-disabled"));
    await press(page, "Mode tidur");
    held.answer();
    await settle();

    assert.equal(busy, "true");
    assert.deepEqual(whileRunning, ["true", "true", "true"]);
    assert.equal(actionRequests(page).length, 1, "the second click is ignored");
    assert.equal(pressed.attributes.get("aria-busy"), "false");
    assert.deepEqual(
      buttons.map((button) => button.attributes.get("aria-disabled")),
      ["false", "false", "false"],
    );
    await press(page, "Mode tidur");
    held.answer();
    await settle();
    assert.equal(actionRequests(page).at(-1).init.body, '{"id":"rt.tidur"}');
  });

  it("toasts why a press did not run", async () => {
    const cases = [
      [{ actionStatus: 404, actionReply: { error: "Tombol itu sudah tidak ada." } }, "action_unknown"],
      [{ actionStatus: 429, actionReply: { error: "Terlalu sering." } }, "action_limit"],
      [{ actionStatus: 409, actionReply: { error: "Perintah tadi masih jalan." } }, "action_busy"],
      [{ actionStatus: 403, actionReply: { error: "Asal permintaan ditolak." } }, "forbidden"],
      [{ actionStatus: 500, actionReply: { error: "Maaf, Master, ada masalah di PC." } }, "server"],
      [{ actionReply: { done: true } }, "bad_reply"],
    ];
    for (const [options, kind] of cases) {
      const page = await openPage(options);

      await press(page, "Nyala");

      assert.equal(page.element("notice").textContent, deskText(kind), kind);
    }
  });

  it("toasts that the page was closed when its server is gone", async () => {
    const page = await openPage();
    globalThis.fetch = async () => {
      throw new TypeError("Failed to fetch");
    };

    await press(page, "Nyala");

    assert.equal(page.element("notice").textContent, deskText("closed"));
    assert.equal(buttonOf(page, "Nyala").attributes.get("aria-busy"), "false");
  });
});
