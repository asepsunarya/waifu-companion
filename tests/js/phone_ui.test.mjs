// The phone page's own behaviour (phone.js), run against a fake DOM, a fake fetch, and a fake AudioContext: no
// browser, network, microphone, or speaker. The chat list has a simple layout (fixed line height, a 200 px window,
// 16 px top padding) so the tests can tell where it is scrolled.
import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

const PHONE_JS = new URL("../../src/waifu/avatar/web/phone/phone.js", import.meta.url);
const ELEMENT_IDS = [
  "name",
  "status",
  "credit",
  "message",
  "retry",
  "pairing",
  "pair-form",
  "pair-code",
  "pair-button",
  "pair-notice",
  "chat",
  "bubbles",
  "notice",
  "composer",
  "text",
  "send",
  "talk",
];
const HIDDEN_AT_START = new Set(["retry", "pairing", "chat", "notice", "pair-notice"]);
// phone.css: #bubbles has 16 px of top padding (its faded edge) and 6 px between bubbles.
const LIST_PADDING_TOP = 16;
const LIST_GAP = 6;
const LIST_HEIGHT = 200;
const LINE_HEIGHT = 21;
const BUBBLE_PADDING = 18;
const CHARACTERS_PER_LINE = 38;
const TYPING_HEIGHT = 40;

const LIVE2D_CONFIG = {
  engine: "live2d",
  model: "/model/Cyrene.model3.json",
  core: "/live2d/live2dcubismcore.min.js",
  credit: "",
  name: "Cyrene",
  owner_call: "Master",
  framing: "upper",
};
const SHORT_REPLY = "Lampu kamar sudah kunyalakan, Master.";
const LONG_REPLY = `${"Baik, Master, ini penjelasannya dari awal sampai akhir dengan cukup panjang. ".repeat(6)}Selesai.`;

class FakeElement {
  constructor(tagName, id = "") {
    this.tagName = tagName.toUpperCase();
    this.id = id;
    this.children = [];
    this.parentElement = null;
    this.hidden = HIDDEN_AT_START.has(id);
    this.disabled = false;
    this.textContent = "";
    this.value = "";
    this.placeholder = "";
    this.title = "";
    this.className = "";
    this.style = {};
    this.dataset = {};
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
      node.remove();
      node.parentElement = this;
      this.children.push(node);
    }
  }

  replaceChildren(...nodes) {
    for (const child of this.children) child.parentElement = null;
    this.children = [];
    this.append(...nodes);
  }

  remove() {
    if (this.parentElement === null) return;
    const siblings = this.parentElement.children;
    siblings.splice(siblings.indexOf(this), 1);
    this.parentElement = null;
  }

  focus() {}

  setPointerCapture() {}

  get offsetTop() {
    return this.parentElement instanceof FakeList ? this.parentElement.offsetOf(this) : 0;
  }
}

/** The chat list: its bubbles stacked with the gap below its top padding, scrolled like a browser clamps it. */
class FakeList extends FakeElement {
  constructor(id) {
    super("ol", id);
    this.clientHeight = LIST_HEIGHT;
    this.scrolled = 0;
  }

  static heightOf(item) {
    if (item.className.includes("typing")) return TYPING_HEIGHT;
    return BUBBLE_PADDING + LINE_HEIGHT * Math.max(1, Math.ceil(item.textContent.length / CHARACTERS_PER_LINE));
  }

  offsetOf(item) {
    let top = LIST_PADDING_TOP;
    for (const child of this.children) {
      if (child === item) return top;
      top += FakeList.heightOf(child) + LIST_GAP;
    }
    throw new Error("not in the list");
  }

  get scrollHeight() {
    const content = this.children.reduce((total, child) => total + FakeList.heightOf(child), 0);
    const gaps = Math.max(0, this.children.length - 1) * LIST_GAP;
    return Math.max(this.clientHeight, LIST_PADDING_TOP + content + gaps);
  }

  // Like a browser, the position stays within the content, also when the content shrinks.
  get scrollTop() {
    return Math.min(this.scrolled, this.scrollHeight - this.clientHeight);
  }

  set scrollTop(value) {
    this.scrolled = Math.min(Math.max(0, value), this.scrollHeight - this.clientHeight);
  }

  /** Whether `item` shows from its first line on: its top below the faded padding, inside the window. */
  showsTopOf(item) {
    const top = item.offsetTop - this.scrollTop;
    return top >= LIST_PADDING_TOP && top < this.clientHeight;
  }

  get atEnd() {
    return this.scrollTop === this.scrollHeight - this.clientHeight;
  }
}

class FakeSource {
  constructor() {
    this.listeners = [];
  }

  connect() {}

  start() {}

  stop() {}

  addEventListener(type, listener) {
    if (type === "ended") this.listeners.push(listener);
  }

  end() {
    for (const listener of this.listeners) listener();
  }
}

class FakeAudioContext {
  static sources = [];

  constructor() {
    this.state = "running";
    this.destination = {};
  }

  async resume() {}

  async decodeAudioData() {
    return { duration: 2 };
  }

  createBufferSource() {
    const source = new FakeSource();
    FakeAudioContext.sources.push(source);
    return source;
  }
}

function jsonResponse(body, status = 200) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}

async function settle() {
  for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let loads = 0;
const saved = {};

/** Loads phone.js afresh against a page whose /config.json is `config` and whose /chat answers `replyText`. */
async function openPage({ config = LIVE2D_CONFIG, replyText = SHORT_REPLY } = {}) {
  const elements = new Map(
    ELEMENT_IDS.map((id) => [id, id === "bubbles" ? new FakeList(id) : new FakeElement("div", id)]),
  );
  elements.get("message").textContent = "Memuat Cyrene…";
  const scripts = [];
  const head = new FakeElement("head");
  head.append = (script) => {
    scripts.push(script.src);
    // No Cubism Core here: loading it fails, as it does on a PC without avatar.live2d_core.
    setTimeout(() => script.dispatch("error"), 0);
  };
  const fakes = {
    document: {
      body: new FakeElement("body"),
      head,
      hidden: false,
      getElementById: (id) => elements.get(id) ?? null,
      createElement: (tagName) => new FakeElement(tagName),
      addEventListener() {},
    },
    window: globalThis,
    location: { reload() {} },
    // The page logs that its renderer could not load: there is none here.
    console: { ...console, error() {}, warn() {} },
    AudioContext: FakeAudioContext,
    getComputedStyle: (element) => ({ paddingTop: element instanceof FakeList ? `${LIST_PADDING_TOP}px` : "0px" }),
    fetch: async (route) => {
      if (route === "/config.json") return jsonResponse(config);
      if (route === "/chat") {
        return jsonResponse({ text: replyText, emotion: "happy", audio: "UklGRg==", fps: 0, visemes: [], duration: 2 });
      }
      return jsonResponse({ error: "Tidak ditemukan." }, 404);
    },
  };
  for (const [name, value] of Object.entries(fakes)) {
    if (!(name in saved)) saved[name] = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
  }
  FakeAudioContext.sources = [];
  loads += 1;
  await import(`${PHONE_JS.href}?load=${loads}`);
  await settle();
  return { element: (id) => elements.get(id), scripts };
}

async function send(page, text) {
  page.element("text").value = text;
  page.element("composer").dispatch("submit");
  await settle();
}

afterEach(() => {
  for (const [name, descriptor] of Object.entries(saved)) {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor);
    else delete globalThis[name];
    delete saved[name];
  }
});

describe("phone page without a model on the PC", () => {
  it("shows the chat and says the avatar is not set up, instead of a reply it cannot read", async () => {
    const page = await openPage({ config: { ...LIVE2D_CONFIG, model: null } });

    assert.equal(page.element("chat").hidden, false);
    assert.match(page.element("message").textContent, /avatar\.model/);
    assert.match(page.element("message").textContent, /Chat tetap bisa dipakai/);
    assert.equal(page.element("retry").hidden, true);
    assert.deepEqual(page.scripts, [], "no renderer or Cubism Core is loaded");
  });

  it("still answers a message", async () => {
    const page = await openPage({ config: { ...LIVE2D_CONFIG, model: null } });

    await send(page, "halo");

    const texts = page.element("bubbles").children.map((item) => item.textContent);
    assert.deepEqual(texts, ["halo", SHORT_REPLY]);
  });
});

describe("phone chat list", () => {
  it("shows a long reply from its first line", async () => {
    const page = await openPage({ replyText: LONG_REPLY });
    const list = page.element("bubbles");

    await send(page, "jelaskan dari awal");

    const reply = list.children.find((item) => item.textContent === LONG_REPLY);
    assert.ok(FakeList.heightOf(reply) > LIST_HEIGHT, "the reply is taller than the list");
    assert.ok(list.showsTopOf(reply), `scrolled to ${list.scrollTop}, the reply starts at ${reply.offsetTop}`);
  });

  it("leaves the list where the owner scrolled it when her voice ends", async () => {
    const page = await openPage({ replyText: LONG_REPLY });
    const list = page.element("bubbles");
    await send(page, "jelaskan dari awal");
    const [voice] = FakeAudioContext.sources;

    list.scrollTop = 40;
    voice.end();

    assert.equal(page.element("status").textContent, "Siap");
    assert.equal(list.scrollTop, 40);
  });

  it("keeps a short exchange at the end of the list", async () => {
    const page = await openPage();
    const list = page.element("bubbles");

    for (const text of ["satu", "dua", "tiga", "empat"]) await send(page, text);

    assert.equal(list.children.length, 6, "at most six bubbles");
    assert.ok(list.atEnd);
    assert.equal(list.children.at(-1).textContent, SHORT_REPLY);
  });

  it("shows the typing dots at the end while she thinks, and drops them when she answers", async () => {
    let answer;
    const page = await openPage();
    const list = page.element("bubbles");
    globalThis.fetch = () =>
      new Promise((resolve) => {
        answer = () => resolve(jsonResponse({ text: SHORT_REPLY, emotion: "happy", audio: null }));
      });

    page.element("text").value = "halo";
    page.element("composer").dispatch("submit");
    await settle();
    const thinking = list.children.map((item) => item.className);
    answer();
    await settle();

    assert.deepEqual(thinking, ["bubble from-owner", "bubble from-cyrene typing"]);
    assert.ok(list.atEnd);
    assert.deepEqual(
      list.children.map((item) => item.className),
      ["bubble from-owner", "bubble from-cyrene"],
    );
  });
});
