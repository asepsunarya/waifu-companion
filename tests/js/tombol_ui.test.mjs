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
// The same buttons while her hearing is paused (Ctrl+Alt+Q): "Dengar lagi" comes first.
const PAUSED = {
  groups: [{ name: "Mikrofon", actions: [{ id: "mic.listen", label: "Dengar lagi" }] }, ...QUICK.groups],
};
// The same buttons while an alarm rings on the PC: its group comes first.
const RINGING = {
  groups: [
    {
      name: "Alarm",
      actions: [
        { id: "al.stop", label: "Matikan alarm" },
        { id: "al.snooze", label: "Tunda 5 menit" },
      ],
    },
    ...QUICK.groups,
  ],
};

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
 * server with `listError`; the test changes the list with `setActions`), and whose `action` answers `actionReply` with
 * `actionStatus`. Every request is recorded in `requests` ({route, init}). The page's timers that repeat are recorded
 * in `intervals` ({callback, ms}) and never run by themselves; `window` is a fake element.
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
  const intervals = [];
  const fakes = {
    document: {
      getElementById: (id) => elements.get(id) ?? null,
      createElement: (tagName) => new FakeElement(tagName),
    },
    window: new FakeElement("window"),
    setInterval: (callback, ms) => intervals.push({ callback, ms }),
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
  const setActions = (listed) => {
    actions = listed;
  };
  return { element: (id) => elements.get(id), requests, intervals, setActions };
}

const listRequests = (page) => page.requests.filter(({ route }) => route === "actions");

/** Runs the page's repeating read of the buttons once, as its interval would. */
async function readAgain(page) {
  const [{ callback }] = page.intervals;
  callback();
  await settle();
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

  it("reads the buttons again every fifteen seconds, an alarm's first", async () => {
    const page = await openPage();
    page.setActions(RINGING);

    await readAgain(page);

    assert.deepEqual(
      page.intervals.map(({ ms }) => ms),
      [15_000],
    );
    assert.deepEqual(
      groupsOf(page).map(({ heading }) => heading.textContent),
      ["Alarm", "Lampu kamar", "Rutinitas"],
    );
    assert.equal(page.element("message").hidden, true);
  });

  it("reads the buttons again when the window gets the focus", async () => {
    const page = await openPage();
    page.setActions(RINGING);

    window.dispatch("focus");
    await settle();

    assert.equal(groupsOf(page)[0].heading.textContent, "Alarm");
  });

  it("sends the alarm's stop like any button", async () => {
    const page = await openPage({ actions: RINGING, actionReply: { ok: true, message: "Alarm dimatikan." } });

    await press(page, "Matikan alarm");

    assert.equal(actionRequests(page).at(-1).init.body, '{"id":"al.stop"}');
    assert.equal(page.element("notice").textContent, "Alarm dimatikan.");
  });

  it("reads the buttons again after the alarm's button, so its group goes once the alarm stopped", async () => {
    const page = await openPage({ actions: RINGING, actionReply: { ok: true, message: "Alarm dimatikan." } });
    page.setActions(QUICK);

    await press(page, "Matikan alarm");

    assert.deepEqual(
      groupsOf(page).map(({ heading }) => heading.textContent),
      ["Lampu kamar", "Rutinitas"],
    );
    assert.equal(page.element("notice").textContent, "Alarm dimatikan.");
  });

  it("reads the buttons again after Dengar lagi, so its group goes once she listens again", async () => {
    const page = await openPage({ actions: PAUSED, actionReply: { ok: true, message: "Mendengarkan lagi." } });
    page.setActions(QUICK);

    await press(page, "Dengar lagi");

    assert.equal(actionRequests(page).at(-1).init.body, '{"id":"mic.listen"}');
    assert.deepEqual(
      groupsOf(page).map(({ heading }) => heading.textContent),
      ["Lampu kamar", "Rutinitas"],
    );
  });

  it("says so when the PC lists no buttons any more, and shows them when they come back", async () => {
    const page = await openPage();
    page.setActions({ groups: [] });

    await readAgain(page);
    const emptied = [page.element("message").hidden, page.element("message").textContent];
    const groupsHidden = page.element("groups").hidden;
    page.setActions(QUICK);
    await readAgain(page);

    assert.deepEqual(emptied, [false, "Belum ada tombol cepat di PC."]);
    assert.equal(groupsHidden, true);
    assert.equal(page.element("message").hidden, true);
    assert.equal(page.element("groups").hidden, false);
    assert.equal(groupsOf(page).length, 2);
  });

  it("keeps the buttons it shows when they cannot be read again", async () => {
    const page = await openPage();
    globalThis.fetch = async () => {
      throw new TypeError("Failed to fetch");
    };

    await readAgain(page);

    assert.equal(page.element("message").hidden, true);
    assert.equal(groupsOf(page).length, 2);
  });

  it("leaves the buttons as they are when the PC lists the same", async () => {
    const page = await openPage();
    const lamp = buttonOf(page, "Nyala");

    await readAgain(page);

    assert.equal(buttonOf(page, "Nyala"), lamp);
  });

  it("does not read the buttons again while a press runs", async () => {
    const page = await openPage();
    const held = holdActions(page);
    await press(page, "Nyala");
    const listed = listRequests(page).length;

    await readAgain(page);
    window.dispatch("focus");
    await settle();
    held.answer();
    await settle();

    assert.equal(listRequests(page).length, listed);
  });
});
