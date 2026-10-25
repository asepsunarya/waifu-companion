// The phone page's own behaviour (phone.js), run against a fake DOM, a fake fetch, a fake AudioContext, and a fake
// camera and microphone: no browser, network, camera, microphone, or speaker. The chat list has a simple layout (fixed
// line height, a 200 px window, 16 px top padding) so the tests can tell where it is scrolled.
import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { problemText } from "../../src/waifu/avatar/web/phone/audio.js";
import { CAMERA_IDLE_MS, MAX_PHOTO_BYTES } from "../../src/waifu/avatar/web/phone/camera.js";

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
  "camera",
  "viewfinder",
  "camera-view",
  "camera-flip",
  "camera-close",
  "quick",
  "quick-toggle",
  "voice-toggle",
  "voice",
  "voice-close",
  "voice-mic",
  "voice-default",
  "voice-level",
  "voice-gate",
  "voice-level-text",
  "voice-device",
  "voice-switch",
  "voice-speaker",
  "voice-threshold",
  "voice-margin",
  "voice-save",
  "voice-reset",
  "voice-mic-note",
  "voice-note",
  "voice-records",
  "voice-records-empty",
];
const HIDDEN_AT_START = new Set([
  "retry",
  "pairing",
  "chat",
  "notice",
  "pair-notice",
  "camera",
  "viewfinder",
  "quick",
  "quick-toggle",
  "voice-toggle",
  "voice",
  "voice-records-empty",
]);
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
// A PC with the camera on (the chat alone, no avatar: these tests are about the camera).
const CAMERA_CONFIG = { ...LIVE2D_CONFIG, model: null, camera: { enabled: true, max_side: 1280 } };
const CAMERA_WISHES = { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } };
// What the fake cameras film: the back camera landscape, the front one portrait.
const FILM_SIZES = { environment: [1920, 1080], user: [1080, 1920] };
const HEARD = "ini apa";
// The PC's quick buttons (GET /actions) and its answer to the lamp's "Nyala" (POST /action).
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
// What GET /voice answers (the Suara sheet), and POST /voice/set's answer to a switch.
const VOICE = {
  mic: { name: "Headset (Polaris-BT)", host_api: "MME", value: "Headset (Polaris-BT), MME" },
  windows_default: "Microphone (High Definition Aud",
  preferences: ["Headset (Polaris-BT), MME", null],
  override_mic: null,
  devices: [
    { value: "Headset (Polaris-BT), MME", name: "Headset (Polaris-BT)", host_api: "MME" },
    { value: "Microphone (High Definition Aud, MME", name: "Microphone (High Definition Aud", host_api: "MME" },
  ],
  level: { rms_db: -51.2, peak_db: -30.0, loudest_db: -48.0 },
  vad: {
    floor_db: -60.0,
    bar_db: -40.0,
    threshold_db: -45.0,
    margin_db: 12.0,
    default_threshold_db: -45.0,
    default_margin_db: 12.0,
    threshold_bounds: [-70.0, -25.0],
    margin_bounds: [3.0, 30.0],
  },
  speaker: { enabled: true, threshold: 0.45, default: 0.45, bounds: [0.3, 0.7] },
  switch: { pending: false, last: null },
  records: [
    {
      at: "19:30:07",
      length_s: 1.2,
      peak_db: -20.0,
      rms_db: -26.0,
      outcome: "no-wake",
      transcript: "matikan lampu",
      wake: "no_wake_word",
      score: 0.71,
      threshold: 0.45,
      stt_s: 0.4,
      brain_s: null,
      tts_s: null,
    },
  ],
};
const SWITCHING = { ok: true, message: "Mic sedang diganti. Hasilnya muncul di sini sebentar lagi.", pending: true };
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

/** A JPEG's first bytes, telling the quality it was encoded at. */
function smallJpeg(quality) {
  return Uint8Array.of(0xff, 0xd8, 0xff, 0xe0, Math.round(quality * 100));
}

// What the page did to the camera's frame ("draw") and the microphone's worklet ("stop"), in order.
let timeline = [];
// The element the page last gave the focus to.
let focused = null;

class FakeText {
  constructor(text) {
    this.textContent = String(text);
    this.parentElement = null;
  }

  remove() {
    if (this.parentElement === null) return;
    const siblings = this.parentElement.children;
    siblings.splice(siblings.indexOf(this), 1);
    this.parentElement = null;
  }
}

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

  prepend(...nodes) {
    for (const node of nodes.toReversed()) {
      node.remove();
      node.parentElement = this;
      this.children.unshift(node);
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

  focus() {
    focused = this;
  }

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

/** The viewfinder's <video>: it shows the size its stream films once the stream is set. */
class FakeVideo extends FakeElement {
  constructor(id) {
    super("video", id);
    this.stream = null;
  }

  get srcObject() {
    return this.stream;
  }

  set srcObject(stream) {
    this.stream = stream;
  }

  get videoWidth() {
    return this.stream?.width ?? 0;
  }

  get videoHeight() {
    return this.stream?.height ?? 0;
  }
}

/**
 * A canvas the page draws a frame on and encodes; every one made and every encoding is recorded. While `held` is a
 * list, encodings wait in it (as functions that finish them) instead of finishing at once, as a slow phone's do.
 */
class FakeCanvas extends FakeElement {
  static made = [];
  static encodings = [];
  static held = null;
  // The JPEG bytes an encoding at `quality` gives.
  static jpeg = smallJpeg;

  constructor() {
    super("canvas");
    this.width = 300;
    this.height = 150;
    this.drawn = [];
    FakeCanvas.made.push(this);
  }

  getContext(kind) {
    assert.equal(kind, "2d");
    return {
      drawImage: (...args) => {
        timeline.push("draw");
        this.drawn.push(args);
      },
    };
  }

  toBlob(callback, type, quality) {
    FakeCanvas.encodings.push({ type, quality, width: this.width, height: this.height });
    const bytes = FakeCanvas.jpeg(quality);
    const finish = () => callback(new Blob([bytes], { type }));
    if (FakeCanvas.held) FakeCanvas.held.push(finish);
    else queueMicrotask(finish);
  }
}

class FakeTrack {
  constructor(kind) {
    this.kind = kind;
    this.readyState = "live";
  }

  stop() {
    this.readyState = "ended";
  }
}

class FakeStream {
  constructor(kind, facing = null) {
    this.tracks = [new FakeTrack(kind)];
    this.facing = facing;
    [this.width, this.height] = FILM_SIZES[facing] ?? [0, 0];
  }

  getTracks() {
    return this.tracks;
  }

  get live() {
    return this.tracks.every((track) => track.readyState === "live");
  }
}

/** navigator.mediaDevices: records what the page asks for; `answer(constraints)`, when set, decides the answer. */
class FakeMediaDevices {
  constructor() {
    this.asked = [];
    this.cameras = [];
    // How many cameras were still on at each request.
    this.liveWhenAsked = [];
    this.answer = null;
  }

  async getUserMedia(constraints) {
    this.asked.push(constraints);
    this.liveWhenAsked.push(this.cameras.filter((stream) => stream.live).length);
    if (this.answer) return this.answer(constraints);
    if (!constraints.video) return new FakeStream("audio");
    const stream = new FakeStream("video", constraints.video.facingMode.ideal);
    this.cameras.push(stream);
    return stream;
  }
}

/** The capture worklet: on "stop" it hands over half a second of the microphone, then its end marker. */
class FakeAudioWorkletNode {
  constructor() {
    this.port = {
      onmessage: null,
      postMessage: (message) => {
        if (message !== "stop") return;
        timeline.push("stop");
        queueMicrotask(() => {
          this.port.onmessage({ data: new Float32Array(8000).fill(0.1) });
          this.port.onmessage({ data: null });
        });
      },
      close() {},
    };
  }
}

class FakeAudioContext {
  static sources = [];

  constructor() {
    this.state = "running";
    this.destination = {};
    this.sampleRate = 16000;
    this.audioWorklet = { addModule: async () => {} };
  }

  createMediaStreamSource() {
    return { connect() {}, disconnect() {} };
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

/** Lets pending promises run without timers, for tests that mock setTimeout. */
async function flush() {
  for (let i = 0; i < 20; i++) await new Promise((resolve) => setImmediate(resolve));
}

let loads = 0;
const saved = {};
const savedObjectUrls = { create: URL.createObjectURL, revoke: URL.revokeObjectURL };

function chatAnswer(text, card = null) {
  return { text, emotion: "happy", audio: "UklGRg==", fps: 0, visemes: [], duration: 2, card };
}

/**
 * Loads phone.js afresh against a page whose /config.json is `config` (as it is at each request) and whose /chat and
 * /talk answer `replyText` (or the status `chatStatus`, `talkStatus`). /actions answers `actions` (404 when null; the
 * test changes it with `setActions`), and /action `actionReply` with `actionStatus`. Every request is recorded in
 * `requests` ({route, init}); object URLs in `objectUrls`.
 */
async function openPage({
  config = LIVE2D_CONFIG,
  replyText = SHORT_REPLY,
  replyCard = null,
  chatStatus = 200,
  talkStatus = 200,
  actions = null,
  actionReply = LAMP_ON,
  actionStatus = 200,
  voice = null,
  voiceStatus = 200,
  voiceReply = SWITCHING,
} = {}) {
  const elements = new Map(
    ELEMENT_IDS.map((id) => {
      if (id === "bubbles") return [id, new FakeList(id)];
      if (id === "camera-view") return [id, new FakeVideo(id)];
      return [id, new FakeElement("div", id)];
    }),
  );
  elements.get("message").textContent = "Memuat Cyrene…";
  const scripts = [];
  const head = new FakeElement("head");
  head.append = (script) => {
    scripts.push(script.src);
    // No Cubism Core here: loading it fails, as it does on a PC without avatar.live2d_core.
    setTimeout(() => script.dispatch("error"), 0);
  };
  const requests = [];
  const devices = new FakeMediaDevices();
  const objectUrls = { created: [], revoked: [] };
  const fakes = {
    document: Object.assign(new FakeElement("#document"), {
      body: new FakeElement("body"),
      head,
      getElementById: (id) => elements.get(id) ?? null,
      createElement: (tagName) => (tagName === "canvas" ? new FakeCanvas() : new FakeElement(tagName)),
      createTextNode: (text) => new FakeText(text),
    }),
    window: new FakeElement("window"),
    navigator: { mediaDevices: devices },
    location: { reload() {} },
    // The page logs that its renderer could not load: there is none here.
    console: { ...console, error() {}, warn() {} },
    AudioContext: FakeAudioContext,
    AudioWorkletNode: FakeAudioWorkletNode,
    getComputedStyle: (element) => ({ paddingTop: element instanceof FakeList ? `${LIST_PADDING_TOP}px` : "0px" }),
    fetch: async (route, init) => {
      requests.push({ route, init });
      if (route === "/config.json") return jsonResponse(config);
      if (route === "/chat") return jsonResponse(chatAnswer(replyText, replyCard), chatStatus);
      if (route === "/talk") return jsonResponse({ heard: HEARD, ...chatAnswer(replyText, replyCard) }, talkStatus);
      if (route === "/actions" && actions !== null) return jsonResponse(actions);
      if (route === "/action") return jsonResponse(actionReply, actionStatus);
      if (route === "/voice" && voice !== null) return jsonResponse(voice, voiceStatus);
      if (route === "/voice/set") return jsonResponse(voiceReply);
      return jsonResponse({ error: "Tidak ditemukan." }, 404);
    },
  };
  for (const [name, value] of Object.entries(fakes)) {
    if (!(name in saved)) saved[name] = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
  }
  URL.createObjectURL = (blob) => {
    const url = `blob:test/${objectUrls.created.length + 1}`;
    objectUrls.created.push({ url, blob });
    return url;
  };
  URL.revokeObjectURL = (url) => objectUrls.revoked.push(url);
  FakeAudioContext.sources = [];
  FakeCanvas.made = [];
  FakeCanvas.encodings = [];
  FakeCanvas.held = null;
  timeline = [];
  focused = null;
  loads += 1;
  await import(`${PHONE_JS.href}?load=${loads}`);
  await settle();
  const setActions = (listed) => {
    actions = listed;
  };
  const setVoice = (status, code = 200) => {
    voice = status;
    voiceStatus = code;
  };
  return { element: (id) => elements.get(id), scripts, requests, devices, objectUrls, setActions, setVoice };
}

async function send(page, text) {
  page.element("text").value = text;
  page.element("composer").dispatch("submit");
  await settle();
}

afterEach(() => {
  // Turns off a camera the test left on, and with it the timer that would keep the test process alive two minutes.
  if (globalThis.window instanceof FakeElement) window.dispatch("pagehide");
  for (const [name, descriptor] of Object.entries(saved)) {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor);
    else delete globalThis[name];
    delete saved[name];
  }
  URL.createObjectURL = savedObjectUrls.create;
  URL.revokeObjectURL = savedObjectUrls.revoke;
  FakeCanvas.jpeg = smallJpeg;
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

describe("phone text card", () => {
  const FORECAST = { title: "Cuaca 7 hari", body: "- Senin cerah\n- Rabu hujan\nSumber: https://bmkg.go.id/cuaca" };
  const GIST = "Ini ramalan seminggu, kutampilkan di layar ya.";
  const cardOf = (bubble) => bubble.children.find((child) => child.className === "card") ?? null;
  const theirBubbles = (page) => page.element("bubbles").children.filter((item) => item.className === "bubble from-cyrene");
  const flatten = (node) => [node, ...(node.children ?? []).flatMap(flatten)];

  it("shows her card under the gist in her bubble", async () => {
    const page = await openPage({ replyText: GIST, replyCard: FORECAST });

    await send(page, "cuaca seminggu?");

    const [bubble] = theirBubbles(page);
    assert.equal(bubble.textContent, GIST);
    const card = cardOf(bubble);
    assert.ok(card, "her bubble holds the card");
    assert.equal(bubble.children.at(-1), card, "after her words");
    const [title, list, source] = card.children;
    assert.equal(title.textContent, "Cuaca 7 hari");
    assert.deepEqual(
      list.children.map((item) => item.children.map((piece) => piece.textContent).join("")),
      ["Senin cerah", "Rabu hujan"],
    );
    const link = source.children.find((piece) => piece.tagName === "A");
    assert.equal(link.href, "https://bmkg.go.id/cuaca");
    assert.equal(link.target, "_blank");
    assert.equal(link.rel, "noopener noreferrer");
  });

  it("shows the card of an answer to a recording too", async () => {
    const page = await openPage({ replyText: GIST, replyCard: FORECAST });

    await talk(page);

    assert.ok(cardOf(theirBubbles(page).at(-1)));
  });

  it("keeps her card when the chat strip is drawn again for the next message", async () => {
    const page = await openPage({ replyText: GIST, replyCard: FORECAST });
    await send(page, "cuaca seminggu?");

    await send(page, "makasih");

    assert.equal(theirBubbles(page).filter(cardOf).length, 2);
  });

  it("shows no card for a reply without one", async () => {
    const page = await openPage();

    await send(page, "halo");

    assert.equal(cardOf(theirBubbles(page)[0]), null);
  });

  it("shows markup in a card as text", async () => {
    const hostile = { title: "<b>x</b>", body: '- <img src=x onerror="alert(1)">' };
    const page = await openPage({ replyText: GIST, replyCard: hostile });

    await send(page, "halo");

    const created = flatten(cardOf(theirBubbles(page)[0])).map((node) => node.tagName).filter(Boolean);
    assert.deepEqual(created, ["DIV", "H2", "UL", "LI"]);
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

/** The page with the camera on, its viewfinder opened. */
async function openViewfinder(options = {}) {
  const page = await openPage({ config: CAMERA_CONFIG, ...options });
  page.element("camera").dispatch("click");
  await settle();
  return page;
}

const lastRequest = (page, route) => page.requests.findLast((request) => request.route === route);
const sentJson = (page, route) => JSON.parse(lastRequest(page, route).init.body);
const fromBase64 = (text) => new Uint8Array(Buffer.from(text, "base64"));

function ownerBubbles(page) {
  return page.element("bubbles").children.filter((item) => item.className === "bubble from-owner");
}

function thumbnailOf(bubble) {
  return bubble.children.find((child) => child.tagName === "IMG") ?? null;
}

/** Holds the talk button with the keyboard (Space), then lets go: what the owner says is sent. */
async function talk(page) {
  page.element("talk").dispatch("keydown", { key: " ", repeat: false });
  await settle();
  page.element("talk").dispatch("keyup", { key: " " });
  await settle();
}

describe("phone camera button", () => {
  it("stays hidden when the PC offers no camera", async () => {
    for (const config of [LIVE2D_CONFIG, { ...CAMERA_CONFIG, camera: { enabled: false, max_side: null } }]) {
      const page = await openPage({ config });

      assert.equal(page.element("camera").hidden, true);
    }
  });

  it("shows when the PC offers the camera", async () => {
    const page = await openPage({ config: CAMERA_CONFIG });

    assert.equal(page.element("camera").hidden, false);
    assert.equal(page.element("viewfinder").hidden, true);
  });

  it("photographs nothing while the viewfinder is closed", async () => {
    const page = await openPage({ config: CAMERA_CONFIG });

    await send(page, "halo");

    assert.equal(lastRequest(page, "/chat").init.body, '{"text":"halo"}');
    assert.deepEqual(page.devices.asked, []);
    assert.deepEqual(FakeCanvas.made, []);
  });

  it("opens the back camera, without sound, into the viewfinder", async () => {
    const page = await openViewfinder();
    const [back] = page.devices.cameras;

    assert.deepEqual(page.devices.asked, [{ video: CAMERA_WISHES, audio: false }]);
    assert.equal(page.element("viewfinder").hidden, false);
    assert.equal(page.element("camera-view").srcObject, back);
    assert.equal(page.element("camera").attributes.get("aria-pressed"), "true");
  });
});

describe("phone photos", () => {
  it("sends one photo of the moment, scaled to the PC's size, with each message", async () => {
    const page = await openViewfinder();
    const video = page.element("camera-view");

    await send(page, "ini apa?");

    const [canvas] = FakeCanvas.made;
    assert.equal(lastRequest(page, "/chat").init.headers["Content-Type"], "application/json");
    assert.deepEqual(Object.keys(sentJson(page, "/chat")), ["text", "camera", "image"]);
    assert.equal(sentJson(page, "/chat").text, "ini apa?");
    assert.equal(sentJson(page, "/chat").camera, true);
    assert.deepEqual(fromBase64(sentJson(page, "/chat").image), smallJpeg(0.85));
    assert.deepEqual([canvas.width, canvas.height], [1280, 720]);
    assert.deepEqual(canvas.drawn, [[video, 0, 0, 1280, 720]]);
    assert.deepEqual(FakeCanvas.encodings, [{ type: "image/jpeg", quality: 0.85, width: 1280, height: 720 }]);

    await send(page, "kalau ini?");

    assert.equal(FakeCanvas.made.length, 2, "a new photo for the next message");
    assert.equal(sentJson(page, "/chat").text, "kalau ini?");
  });

  it("shows the photo she saw in the owner's bubble", async () => {
    const page = await openViewfinder();

    await send(page, "ini apa?");

    const [bubble] = ownerBubbles(page);
    const thumbnail = thumbnailOf(bubble);
    const [{ url, blob }] = page.objectUrls.created;
    assert.equal(bubble.textContent, "ini apa?");
    assert.equal(thumbnail.className, "photo");
    assert.equal(thumbnail.src, url);
    assert.deepEqual([thumbnail.width, thumbnail.height], [1280, 720]);
    assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), smallJpeg(0.85));
  });

  it("sends the message without the photo when even the lowest quality is too big, and says so", async () => {
    const page = await openViewfinder();
    FakeCanvas.jpeg = () => new Uint8Array(MAX_PHOTO_BYTES + 1);

    await send(page, "ini apa?");

    assert.deepEqual(FakeCanvas.encodings.map(({ quality }) => quality), [0.85, 0.7, 0.5]);
    // Still sent through the camera: "ini apa?" is about what the camera shows, not the PC screen.
    assert.equal(lastRequest(page, "/chat").init.body, '{"text":"ini apa?","camera":true}');
    assert.equal(thumbnailOf(ownerBubbles(page)[0]), null);
    assert.deepEqual(page.objectUrls.created, []);
    assert.equal(page.element("notice").hidden, false);
    assert.equal(page.element("notice").textContent, problemText("photo_too_big"));
  });

  it("sends the message without a photo while the camera is still opening, and says so", async () => {
    const page = await openPage({ config: CAMERA_CONFIG });
    page.devices.answer = () => new Promise(() => {});
    page.element("camera").dispatch("click");
    await settle();

    await send(page, "coba lihat ini");

    assert.equal(lastRequest(page, "/chat").init.body, '{"text":"coba lihat ini","camera":true}');
    assert.deepEqual(FakeCanvas.made, []);
    assert.equal(page.element("notice").textContent, problemText("photo_failed"));
  });

  it("keeps send and talk off while the photo of a typed message is encoded, so nothing else goes", async () => {
    const page = await openViewfinder();
    FakeCanvas.held = [];

    await send(page, "ini apa?");
    const whileEncoding = [page.element("send").disabled, page.element("talk").disabled];
    await send(page, "kalau ini?");
    for (const finish of FakeCanvas.held) finish();
    await settle();

    assert.deepEqual(whileEncoding, [true, true]);
    assert.deepEqual(
      page.requests.filter(({ route }) => route === "/chat").map(({ init }) => JSON.parse(init.body).text),
      ["ini apa?"],
    );
    assert.equal(page.element("text").value, "kalau ini?", "the second message waits in the text box");
  });

  it("lets go of a thumbnail when its bubble leaves the chat strip", async () => {
    const page = await openViewfinder();
    for (const text of ["satu", "dua", "tiga"]) await send(page, text);
    const urls = page.objectUrls.created.map(({ url }) => url);

    assert.deepEqual(page.objectUrls.revoked, []);
    await send(page, "empat");
    assert.deepEqual(page.objectUrls.revoked, [urls[0]]);
    await send(page, "lima");
    assert.deepEqual(page.objectUrls.revoked, [urls[0], urls[1]]);
  });

  it("lets go of the thumbnail of a message she did not answer", async () => {
    const page = await openViewfinder({ chatStatus: 500 });

    await send(page, "ini apa?");

    assert.deepEqual(ownerBubbles(page), []);
    assert.deepEqual(page.objectUrls.revoked, [page.objectUrls.created[0].url]);
    assert.equal(page.element("text").value, "ini apa?");
  });

  it("sends a photo with what the owner says", async () => {
    const page = await openViewfinder();

    await talk(page);

    const request = lastRequest(page, "/talk");
    const sent = sentJson(page, "/talk");
    assert.equal(request.init.headers["Content-Type"], "application/json");
    assert.deepEqual(Object.keys(sent), ["audio", "camera", "image"]);
    assert.equal(Buffer.from(fromBase64(sent.audio).subarray(0, 4)).toString("latin1"), "RIFF");
    assert.equal(sent.camera, true);
    assert.deepEqual(fromBase64(sent.image), smallJpeg(0.85));
    const [bubble] = ownerBubbles(page);
    assert.equal(bubble.textContent, HEARD);
    assert.equal(thumbnailOf(bubble).src, page.objectUrls.created[0].url);
  });

  it("photographs the moment the talk button is let go, before the recording ends", async () => {
    const page = await openViewfinder();

    await talk(page);

    assert.deepEqual(timeline, ["draw", "stop"]);
  });

  it("sends what the owner says through the camera without a photo too big to send, and says so", async () => {
    const page = await openViewfinder();
    FakeCanvas.jpeg = () => new Uint8Array(MAX_PHOTO_BYTES + 1);

    await talk(page);

    const sent = sentJson(page, "/talk");
    assert.equal(lastRequest(page, "/talk").init.headers["Content-Type"], "application/json");
    assert.deepEqual(Object.keys(sent), ["audio", "camera"]);
    assert.equal(Buffer.from(fromBase64(sent.audio).subarray(0, 4)).toString("latin1"), "RIFF");
    assert.equal(sent.camera, true);
    assert.equal(thumbnailOf(ownerBubbles(page)[0]), null);
    assert.equal(page.element("notice").hidden, false);
    assert.equal(page.element("notice").textContent, problemText("photo_too_big"));
  });

  it("keeps send and talk off while the photo of a released recording is encoded", async () => {
    const page = await openViewfinder();
    FakeCanvas.held = [];

    await talk(page);
    const whileEncoding = [page.element("send").disabled, page.element("talk").disabled];
    await send(page, "halo");
    page.element("talk").dispatch("keydown", { key: " ", repeat: false });
    await settle();
    const microphones = page.devices.asked.filter((constraints) => constraints.audio).length;
    for (const finish of FakeCanvas.held) finish();
    await settle();

    assert.deepEqual(whileEncoding, [true, true]);
    assert.equal(microphones, 1, "no second recording starts");
    assert.deepEqual(
      page.requests.filter(({ init }) => init.method === "POST").map(({ route }) => route),
      ["/talk"],
    );
    assert.equal(page.element("text").value, "halo", "the typed message waits in the text box");
  });

  it("sends what the owner says as a WAV, as before, while the viewfinder is closed", async () => {
    const page = await openPage({ config: CAMERA_CONFIG });

    await talk(page);

    const request = lastRequest(page, "/talk");
    assert.equal(request.init.headers["Content-Type"], "audio/wav");
    assert.ok(request.init.body instanceof Uint8Array);
    assert.equal(page.devices.asked.length, 1, "the microphone only");
    assert.equal(page.devices.asked[0].video, undefined);
    assert.deepEqual(FakeCanvas.made, []);
  });
});

describe("phone viewfinder", () => {
  it("flips between the back and the front camera", async () => {
    const page = await openViewfinder();
    const [back] = page.devices.cameras;

    page.element("camera-flip").dispatch("click");
    await settle();
    const front = page.devices.cameras[1];

    assert.equal(back.live, false);
    assert.deepEqual(page.devices.liveWhenAsked, [0, 0], "the back camera is off before the front one is asked for");
    assert.deepEqual(page.devices.asked[1].video.facingMode, { ideal: "user" });
    assert.equal(page.element("camera-view").srcObject, front);
    await send(page, "bajuku bagus?");
    assert.deepEqual([FakeCanvas.made[0].width, FakeCanvas.made[0].height], [720, 1280]);

    page.element("camera-flip").dispatch("click");
    await settle();

    assert.equal(front.live, false);
    assert.deepEqual(page.devices.asked[2].video.facingMode, { ideal: "environment" });
  });

  it("turns the camera off when the owner closes it, and photographs nothing after", async () => {
    const page = await openViewfinder();
    const [back] = page.devices.cameras;

    page.element("camera-close").dispatch("click");
    await send(page, "halo");

    assert.equal(back.live, false);
    assert.equal(page.element("viewfinder").hidden, true);
    assert.equal(page.element("camera-view").srcObject, null);
    assert.equal(page.element("camera").attributes.get("aria-pressed"), "false");
    assert.equal(lastRequest(page, "/chat").init.body, '{"text":"halo"}');
    assert.deepEqual(FakeCanvas.made, []);
  });

  it("turns the camera off when its button is tapped again", async () => {
    const page = await openViewfinder();

    page.element("camera").dispatch("click");

    assert.equal(page.devices.cameras[0].live, false);
    assert.equal(page.element("viewfinder").hidden, true);
  });

  it("turns the camera off when the page is hidden", async () => {
    const page = await openViewfinder();

    document.hidden = true;
    document.dispatch("visibilitychange");

    assert.equal(page.devices.cameras[0].live, false);
    assert.equal(page.element("viewfinder").hidden, true);
  });

  it("turns the camera off when the page is left", async () => {
    const page = await openViewfinder();

    window.dispatch("pagehide", { persisted: true });

    assert.equal(page.devices.cameras[0].live, false);
    assert.equal(page.element("viewfinder").hidden, true);
  });

  it("turns the camera off when this phone must be paired again", async () => {
    const page = await openViewfinder({ chatStatus: 401 });

    await send(page, "halo");

    assert.equal(page.element("pairing").hidden, false);
    assert.equal(page.devices.cameras[0].live, false);
  });

  it("turns the camera off two minutes after it opened", async (t) => {
    const page = await openPage({ config: CAMERA_CONFIG });
    t.mock.timers.enable({ apis: ["setTimeout"] });
    page.element("camera").dispatch("click");
    await flush();
    const [back] = page.devices.cameras;

    t.mock.timers.tick(CAMERA_IDLE_MS - 1);
    assert.equal(back.live, true);
    t.mock.timers.tick(1);

    assert.equal(back.live, false);
    assert.equal(page.element("viewfinder").hidden, true);
  });

  it("keeps the camera on for two minutes after the last photo", async (t) => {
    const page = await openPage({ config: CAMERA_CONFIG });
    t.mock.timers.enable({ apis: ["setTimeout"] });
    page.element("camera").dispatch("click");
    await flush();
    const [back] = page.devices.cameras;
    t.mock.timers.tick(60_000);

    page.element("text").value = "ini apa?";
    page.element("composer").dispatch("submit");
    await flush();
    t.mock.timers.tick(CAMERA_IDLE_MS - 1);
    assert.equal(back.live, true);
    t.mock.timers.tick(1);

    assert.equal(back.live, false);
  });

  it("gives a viewfinder opened again its own two minutes", async (t) => {
    const page = await openPage({ config: CAMERA_CONFIG });
    t.mock.timers.enable({ apis: ["setTimeout"] });
    page.element("camera").dispatch("click");
    await flush();
    t.mock.timers.tick(110_000);
    page.element("camera-close").dispatch("click");
    t.mock.timers.tick(5_000);

    page.element("camera").dispatch("click");
    await flush();
    t.mock.timers.tick(5_000);

    const [, again] = page.devices.cameras;
    assert.equal(again.live, true, "the first viewfinder's two minutes are over, not this one's");
    assert.equal(page.element("viewfinder").hidden, false);
    t.mock.timers.tick(CAMERA_IDLE_MS - 5_000);
    assert.equal(again.live, false);
  });

  it("keeps the camera on while the talk button is held, two minutes from its press", async (t) => {
    const page = await openPage({ config: CAMERA_CONFIG });
    t.mock.timers.enable({ apis: ["setTimeout"] });
    page.element("camera").dispatch("click");
    await flush();
    const [back] = page.devices.cameras;
    t.mock.timers.tick(110_000);

    page.element("talk").dispatch("keydown", { key: " ", repeat: false });
    await flush();
    t.mock.timers.tick(15_000);
    const heldPastTwoMinutes = back.live;
    page.element("talk").dispatch("keyup", { key: " " });
    await flush();

    assert.equal(heldPastTwoMinutes, true, "the press restarted the two minutes");
    assert.equal(sentJson(page, "/talk").camera, true);
    assert.deepEqual(fromBase64(sentJson(page, "/talk").image), smallJpeg(0.85));
  });

  it("hides the camera when the PC turned it off after the page loaded, and says it was not sent", async () => {
    const config = structuredClone(CAMERA_CONFIG);
    // A photo is over the 32 KB a chat may have on a PC without the camera.
    const page = await openViewfinder({ config, chatStatus: 413 });
    config.camera = { enabled: false, max_side: null };

    await send(page, "ini apa?");

    assert.equal(page.requests.filter(({ route }) => route === "/config.json").length, 2, "read again");
    assert.equal(page.element("notice").textContent, problemText("camera_off"));
    assert.equal(page.devices.cameras[0].live, false);
    assert.equal(page.element("viewfinder").hidden, true);
    assert.equal(page.element("camera").hidden, true);
    assert.equal(page.element("text").value, "ini apa?", "the message is back in the text box");
    assert.equal(page.element("send").disabled, false);

    await send(page, "ini apa?");

    assert.equal(lastRequest(page, "/chat").init.body, '{"text":"ini apa?"}', "sent again without the camera");
  });

  // 413: a long recording with a photo is over the 2 MB a recording may have on a PC without the camera.
  for (const talkStatus of [400, 413]) {
    it(`hides the camera when a PC that turned it off refuses a recording through it (${talkStatus})`, async () => {
      const config = structuredClone(CAMERA_CONFIG);
      const page = await openViewfinder({ config, talkStatus });
      config.camera = { enabled: false, max_side: null };

      await talk(page);

      assert.equal(page.element("notice").textContent, problemText("camera_off"));
      assert.equal(page.element("camera").hidden, true);
      assert.equal(page.element("viewfinder").hidden, true);
    });
  }

  it("keeps the camera and says what went wrong when the PC still offers it", async () => {
    const page = await openViewfinder({ chatStatus: 400 });

    await send(page, "ini apa?");

    assert.equal(page.requests.filter(({ route }) => route === "/config.json").length, 2);
    assert.equal(page.element("notice").textContent, problemText("bad_request"));
    assert.equal(page.element("camera").hidden, false);
    assert.equal(page.element("viewfinder").hidden, false);
    assert.equal(page.devices.cameras[0].live, true);
  });

  it("never asks the PC about its camera for a message sent without it", async () => {
    const page = await openPage({ config: CAMERA_CONFIG, chatStatus: 413 });

    await send(page, "halo");

    assert.equal(page.requests.filter(({ route }) => route === "/config.json").length, 1);
    assert.equal(page.element("notice").textContent, problemText("too_long_text"));
    assert.equal(page.element("camera").hidden, false);
  });

  it("stops a camera that opens only after the viewfinder closed", async () => {
    const page = await openPage({ config: CAMERA_CONFIG });
    let open;
    const late = new FakeStream("video", "environment");
    page.devices.answer = () => new Promise((resolve) => (open = () => resolve(late)));

    page.element("camera").dispatch("click");
    await settle();
    page.element("camera-close").dispatch("click");
    open();
    await settle();

    assert.equal(late.live, false);
    assert.equal(page.element("camera-view").srcObject, null);
    assert.equal(page.element("viewfinder").hidden, true);
  });

  it("says in Indonesian why the camera did not open, and closes the viewfinder", async () => {
    const page = await openPage({ config: CAMERA_CONFIG });
    page.devices.answer = async () => {
      throw Object.assign(new Error("Permission denied"), { name: "NotAllowedError" });
    };

    page.element("camera").dispatch("click");
    await settle();

    assert.equal(page.element("notice").textContent, problemText("camera_denied"));
    assert.equal(page.element("notice").hidden, false);
    assert.equal(page.element("viewfinder").hidden, true);
    assert.equal(page.element("camera").attributes.get("aria-pressed"), "false");
  });
});

/** The quick buttons' groups as the panel shows them: {heading, labels, group (its element), buttons}. */
function quickGroups(page) {
  return page.element("quick").children.map((group) => {
    const [heading, list] = group.children;
    const labels = list.children.map((button) => button.textContent);
    return { heading: heading.textContent, labels, group, buttons: list.children };
  });
}

function quickButton(page, label) {
  return quickGroups(page)
    .flatMap(({ buttons }) => buttons)
    .find((button) => button.textContent === label);
}

/** The page with the PC's quick buttons, their panel opened and the buttons it read again then. */
async function openQuick(options = {}) {
  const page = await openPage({ actions: QUICK, ...options });
  page.element("quick-toggle").dispatch("click");
  await settle();
  return page;
}

async function press(page, label) {
  quickButton(page, label).dispatch("click");
  await settle();
}

/** Holds every POST /action of the page until `answer()`; other requests go to the page's fake as before. */
function holdActions(page) {
  const fakeFetch = globalThis.fetch;
  const held = { answer: null };
  globalThis.fetch = (route, init) => {
    if (route !== "/action") return fakeFetch(route, init);
    page.requests.push({ route, init });
    return new Promise((resolve) => (held.answer = () => resolve(jsonResponse(LAMP_ON))));
  };
  return held;
}

describe("phone quick buttons", () => {
  it("stay hidden, without a word, when the PC has none or cannot list them", async () => {
    for (const actions of [null, { groups: [] }, { groups: "Lampu" }]) {
      const page = await openPage({ actions });

      assert.equal(page.element("quick-toggle").hidden, true);
      assert.equal(page.element("quick").hidden, true);
      assert.equal(page.element("notice").hidden, true);
      assert.equal(lastRequest(page, "/actions").init.method, "GET");
    }
  });

  it("show their toggle when the PC has buttons, with the panel closed", async () => {
    const page = await openPage({ actions: QUICK });

    assert.equal(page.element("quick-toggle").hidden, false);
    assert.equal(page.element("quick").hidden, true);
  });

  it("open and close with the toggle, which says whether the panel is open", async () => {
    const page = await openQuick();
    const toggle = page.element("quick-toggle");

    assert.equal(page.element("quick").hidden, false);
    assert.equal(toggle.attributes.get("aria-expanded"), "true");

    toggle.dispatch("click");

    assert.equal(page.element("quick").hidden, true);
    assert.equal(toggle.attributes.get("aria-expanded"), "false");
  });

  it("show a group per device or the routines, each named by its heading, with its buttons", async () => {
    const page = await openQuick();
    const groups = quickGroups(page);

    assert.deepEqual(
      groups.map(({ heading, labels }) => ({ heading, labels })),
      [
        { heading: "Lampu kamar", labels: ["Nyala", "Mati"] },
        { heading: "Rutinitas", labels: ["Mode tidur"] },
      ],
    );
    for (const { group } of groups) {
      const [heading] = group.children;
      assert.equal(group.attributes.get("role"), "group");
      assert.equal(heading.tagName, "H2");
      assert.ok(heading.id);
      assert.equal(group.attributes.get("aria-labelledby"), heading.id);
    }
    assert.notEqual(groups[0].group.children[0].id, groups[1].group.children[0].id);
    for (const button of groups.flatMap(({ buttons }) => buttons)) {
      assert.equal(button.tagName, "BUTTON");
      assert.equal(button.type, "button");
    }
  });

  it("send only the pressed button's id to the PC, and toast what it did", async () => {
    const page = await openQuick();

    await press(page, "Nyala");

    const request = lastRequest(page, "/action");
    assert.equal(request.init.method, "POST");
    assert.equal(request.init.headers["Content-Type"], "application/json");
    assert.equal(request.init.body, '{"id":"dev.lampu_kamar.on"}');
    assert.equal(page.element("notice").hidden, false);
    assert.equal(page.element("notice").textContent, LAMP_ON.message);
  });

  it("toast a device's failure as the PC tells it", async () => {
    const page = await openQuick({ actionReply: { ok: false, message: "Lampu kamar tidak bisa dihubungi." } });

    await press(page, "Mati");

    assert.equal(page.element("notice").textContent, "Lampu kamar tidak bisa dihubungi.");
  });

  it("hide the toast after six seconds", async (t) => {
    const page = await openQuick();
    t.mock.timers.enable({ apis: ["setTimeout"] });
    quickButton(page, "Nyala").dispatch("click");
    await flush();

    t.mock.timers.tick(5_999);
    assert.equal(page.element("notice").hidden, false);
    t.mock.timers.tick(1);

    assert.equal(page.element("notice").hidden, true);
  });

  it("mark the pressed button busy and every button unavailable until the PC answers", async () => {
    const page = await openQuick();
    const held = holdActions(page);
    const buttons = quickGroups(page).flatMap((group) => group.buttons);
    const pressed = quickButton(page, "Nyala");

    await press(page, "Nyala");
    const busy = pressed.attributes.get("aria-busy");
    const whileRunning = buttons.map((button) => button.attributes.get("aria-disabled"));
    await press(page, "Mode tidur");
    held.answer();
    await settle();

    assert.equal(busy, "true");
    assert.deepEqual(whileRunning, ["true", "true", "true"]);
    assert.equal(page.requests.filter(({ route }) => route === "/action").length, 1, "the second tap is ignored");
    assert.equal(pressed.attributes.get("aria-busy"), "false");
    assert.deepEqual(
      buttons.map((button) => button.attributes.get("aria-disabled")),
      ["false", "false", "false"],
    );
    await press(page, "Mode tidur");
    held.answer();
    await settle();
    assert.equal(lastRequest(page, "/action").init.body, '{"id":"rt.tidur"}');
  });

  it("leave the chat usable while a button runs", async () => {
    const page = await openQuick();
    const held = holdActions(page);

    await press(page, "Nyala");
    await send(page, "halo");
    held.answer();
    await settle();

    assert.equal(lastRequest(page, "/chat").init.body, '{"text":"halo"}');
    assert.equal(page.element("notice").textContent, LAMP_ON.message);
  });

  it("say when the PC no longer has the button", async () => {
    const page = await openQuick({ actionStatus: 404, actionReply: { error: "Tombol itu sudah tidak ada." } });

    await press(page, "Nyala");

    assert.equal(page.element("notice").textContent, problemText("action_unknown"));
  });

  it("say when a button was pressed too often", async () => {
    const page = await openQuick({ actionStatus: 429, actionReply: { error: "Terlalu sering." } });

    await press(page, "Nyala");

    assert.equal(page.element("notice").textContent, problemText("action_limit"));
  });

  it("say when the button's last press still runs on the PC, not that it was pressed too often", async () => {
    const page = await openQuick({ actionStatus: 409, actionReply: { error: "Perintah tadi masih jalan." } });

    await press(page, "Nyala");

    assert.equal(page.element("notice").textContent, "Perintah tadi masih jalan. Tunggu sebentar.");
  });

  it("say when the PC's answer cannot be read", async () => {
    const page = await openQuick({ actionReply: { done: true } });

    await press(page, "Nyala");

    assert.equal(page.element("notice").textContent, problemText("bad_reply"));
  });

  it("show the pairing form when the PC forgot this phone", async () => {
    const page = await openQuick({ actionStatus: 401, actionReply: { error: "Belum dipasangkan." } });

    await press(page, "Nyala");

    assert.equal(page.element("pairing").hidden, false);
    assert.equal(page.element("chat").hidden, true);
  });

  it("close with Escape and give the focus back to the toggle", async () => {
    const page = await openQuick();

    page.element("quick").dispatch("keydown", { key: "Escape" });

    assert.equal(page.element("quick").hidden, true);
    assert.equal(page.element("quick-toggle").attributes.get("aria-expanded"), "false");
    assert.equal(focused, page.element("quick-toggle"));
  });

  it("put the focus on the first button when the toggle opens the panel, which comes before the toggle", async () => {
    const page = await openQuick();

    assert.equal(focused, quickButton(page, "Nyala"));
  });

  it("close with Escape on the toggle too, which keeps the focus", async () => {
    const page = await openQuick();
    const toggle = page.element("quick-toggle");
    toggle.focus();

    toggle.dispatch("keydown", { key: "Escape" });

    assert.equal(page.element("quick").hidden, true);
    assert.equal(toggle.attributes.get("aria-expanded"), "false");
    assert.equal(focused, toggle);
  });

  it("leave the focus on the toggle when it closes the panel", async () => {
    const page = await openQuick();
    const toggle = page.element("quick-toggle");
    toggle.focus();

    toggle.dispatch("click");

    assert.equal(page.element("quick").hidden, true);
    assert.equal(focused, toggle);
  });

  it("ignore other keys", async () => {
    const page = await openQuick();

    page.element("quick").dispatch("keydown", { key: "Enter" });

    assert.equal(page.element("quick").hidden, false);
  });

  it("fit the text box's height again when it gains or loses the focus, as the toggle beside it hides or shows", async () => {
    // phone.css hides the closed panel's toggle while the owner types: the text box is wider then, so a message wraps
    // into fewer lines.
    const page = await openPage({ actions: QUICK });
    const text = page.element("text");
    text.value = "nyalakan lampu kamar dan AC jam sembilan malam";

    text.scrollHeight = 44;
    text.dispatch("focus");
    const focusedHeight = text.style.height;
    text.scrollHeight = 66;
    text.dispatch("blur");

    assert.equal(focusedHeight, "44px");
    assert.equal(text.style.height, "66px");
  });

  it("are read again when the page shows again, opening the panel on the alarm's buttons", async () => {
    const page = await openPage({ actions: QUICK });
    page.setActions(RINGING);

    document.hidden = false;
    document.dispatch("visibilitychange");
    await settle();

    assert.deepEqual(
      quickGroups(page).map(({ heading }) => heading),
      ["Alarm", "Lampu kamar", "Rutinitas"],
    );
    assert.equal(page.element("quick").hidden, false);
    assert.equal(page.element("quick-toggle").attributes.get("aria-expanded"), "true");
    assert.equal(focused, quickButton(page, "Matikan alarm"));
  });

  it("are read again when the panel opens", async () => {
    const page = await openPage({ actions: QUICK });
    page.setActions(RINGING);

    page.element("quick-toggle").dispatch("click");
    await settle();

    assert.equal(quickGroups(page)[0].heading, "Alarm");
    assert.equal(focused, quickButton(page, "Matikan alarm"));
  });

  it("send the alarm's stop like any button", async () => {
    const page = await openPage({ actions: RINGING, actionReply: { ok: true, message: "Alarm dimatikan." } });

    await press(page, "Matikan alarm");

    assert.equal(lastRequest(page, "/action").init.body, '{"id":"al.stop"}');
    assert.equal(page.element("notice").textContent, "Alarm dimatikan.");
  });

  it("are read again after the alarm's button, so its group goes once the alarm stopped", async () => {
    const page = await openPage({ actions: RINGING, actionReply: { ok: true, message: "Alarm dimatikan." } });
    page.setActions(QUICK);

    await press(page, "Matikan alarm");

    assert.deepEqual(
      quickGroups(page).map(({ heading }) => heading),
      ["Lampu kamar", "Rutinitas"],
    );
    assert.equal(page.element("notice").textContent, "Alarm dimatikan.");
  });

  it("are read again every fifteen seconds while the alarm's buttons show, and no longer once they went", async (t) => {
    t.mock.timers.enable({ apis: ["setInterval"] });
    const page = await openPage({ actions: RINGING });
    const listed = () => page.requests.filter(({ route }) => route === "/actions").length;
    const before = listed();
    page.setActions(QUICK); // Master stopped it by voice

    t.mock.timers.tick(14_999);
    await settle();
    const early = listed();
    t.mock.timers.tick(1);
    await settle();
    const read = listed();
    t.mock.timers.tick(60_000);
    await settle();

    assert.equal(early, before);
    assert.equal(read, before + 1);
    assert.deepEqual(
      quickGroups(page).map(({ heading }) => heading),
      ["Lampu kamar", "Rutinitas"],
    );
    assert.equal(listed(), read, "the alarm's buttons went, so the timer stopped");
  });

  it("are not read on a timer while the page is hidden, and open the panel on the alarm's when it shows", async (t) => {
    t.mock.timers.enable({ apis: ["setInterval"] });
    const page = await openPage({ actions: RINGING });
    const listed = () => page.requests.filter(({ route }) => route === "/actions").length;
    page.element("quick-toggle").dispatch("click"); // Master closed the panel
    document.hidden = true;
    document.dispatch("visibilitychange");
    const before = listed();

    t.mock.timers.tick(60_000);
    await settle();
    const whileHidden = listed();
    document.hidden = false;
    document.dispatch("visibilitychange");
    await settle();

    assert.equal(whileHidden, before);
    assert.equal(page.element("quick").hidden, false);
    assert.equal(focused, quickButton(page, "Matikan alarm"));
  });

  it("are read again after Dengar lagi, so its group goes once she listens again", async () => {
    const page = await openPage({ actions: PAUSED, actionReply: { ok: true, message: "Mendengarkan lagi." } });
    page.setActions(QUICK);

    await press(page, "Dengar lagi");

    assert.equal(lastRequest(page, "/action").init.body, '{"id":"mic.listen"}');
    assert.deepEqual(
      quickGroups(page).map(({ heading }) => heading),
      ["Lampu kamar", "Rutinitas"],
    );
    assert.equal(page.element("notice").textContent, "Mendengarkan lagi.");
  });

  it("are read again every fifteen seconds while Dengar lagi shows", async (t) => {
    t.mock.timers.enable({ apis: ["setInterval"] });
    const page = await openPage({ actions: PAUSED });
    const listed = () => page.requests.filter(({ route }) => route === "/actions").length;
    const before = listed();
    page.setActions(QUICK); // the pause ended by itself

    t.mock.timers.tick(15_000);
    await settle();
    const read = listed();
    t.mock.timers.tick(60_000);
    await settle();

    assert.equal(read, before + 1);
    assert.equal(listed(), read, "Dengar lagi went, so the timer stopped");
  });

  it("never open the closed panel by themselves for Dengar lagi", async () => {
    const page = await openPage({ actions: QUICK });
    page.setActions(PAUSED);

    document.hidden = false;
    document.dispatch("visibilitychange");
    await settle();

    assert.equal(quickGroups(page)[0].heading, "Mikrofon");
    assert.equal(page.element("quick").hidden, true);
  });

  it("hide their toggle and close the panel once the PC lists none", async () => {
    const page = await openQuick();
    page.setActions({ groups: [] });

    document.dispatch("visibilitychange");
    await settle();

    assert.equal(page.element("quick-toggle").hidden, true);
    assert.equal(page.element("quick").hidden, true);
  });

  it("keep the buttons shown when the PC cannot list them again", async () => {
    const page = await openQuick();
    page.setActions(null);

    document.dispatch("visibilitychange");
    await settle();

    assert.deepEqual(
      quickGroups(page).map(({ heading }) => heading),
      ["Lampu kamar", "Rutinitas"],
    );
    assert.equal(page.element("quick").hidden, false);
  });

  it("are left as they are when the PC lists the same buttons", async () => {
    const page = await openQuick();
    const lamp = quickButton(page, "Nyala");

    document.dispatch("visibilitychange");
    await settle();

    assert.equal(quickButton(page, "Nyala"), lamp);
  });

  it("are not read again while a press runs", async () => {
    const page = await openQuick();
    const held = holdActions(page);
    await press(page, "Nyala");
    const listed = page.requests.filter(({ route }) => route === "/actions").length;

    document.dispatch("visibilitychange");
    await settle();
    held.answer();
    await settle();

    assert.equal(page.requests.filter(({ route }) => route === "/actions").length, listed);
  });

  it("take the viewfinder's place, and give it back", async () => {
    const page = await openPage({ config: CAMERA_CONFIG, actions: QUICK });
    page.element("camera").dispatch("click");
    await settle();
    const [back] = page.devices.cameras;

    page.element("quick-toggle").dispatch("click");

    assert.equal(page.element("viewfinder").hidden, true);
    assert.equal(back.live, false, "the camera is off");
    assert.equal(page.element("quick").hidden, false);

    page.element("camera").dispatch("click");
    await settle();

    assert.equal(page.element("viewfinder").hidden, false);
    assert.equal(page.element("quick").hidden, true);
    assert.equal(page.element("quick-toggle").attributes.get("aria-expanded"), "false");
  });
});

async function openVoice(options = {}) {
  const page = await openPage({ voice: VOICE, ...options });
  page.element("voice-toggle").dispatch("click");
  await settle();
  return page;
}

const voiceReads = (page) => page.requests.filter(({ route }) => route === "/voice").length;

describe("phone Suara sheet", () => {
  it("shows its button once the PC answers the voice status, with the sheet closed", async () => {
    const page = await openPage({ voice: VOICE });

    assert.equal(page.element("voice-toggle").hidden, false);
    assert.equal(page.element("voice").hidden, true);
  });

  it("stays hidden without a word when the PC has none or guest mode hides it", async () => {
    for (const [voice, voiceStatus] of [
      [null, 200],
      [VOICE, 403],
      [{ mic: "Headset" }, 200],
    ]) {
      const page = await openPage({ voice, voiceStatus });

      assert.equal(page.element("voice-toggle").hidden, true);
      assert.equal(page.element("notice").hidden, true);
    }
  });

  it("opens on its button with the mic, the default, the level against the gate, and the records", async () => {
    const page = await openVoice();

    assert.equal(page.element("voice").hidden, false);
    assert.equal(page.element("voice-toggle").attributes.get("aria-expanded"), "true");
    assert.match(page.element("voice-mic").textContent, /Headset \(Polaris-BT\) · MME/);
    assert.match(page.element("voice-default").textContent, /Microphone \(High Definition Aud/);
    assert.equal(page.element("voice-level").style.width, "40.0%"); // the loudest frame, -48 dB
    assert.equal(page.element("voice-gate").style.left, "50.0%"); // the bar, -40 dB
    assert.match(page.element("voice-level-text").textContent, /-48 dB.*-40 dB/);
    const [record] = page.element("voice-records").children;
    assert.match(record.className, /outcome-no-wake/);
    const text = record.children.map((line) => line.textContent).join(" ");
    assert.match(text, /19:30:07 · tanpa nama/);
    assert.match(text, /“matikan lampu”/);
  });

  it("says what to try while nothing went past the gate yet, and not once something did", async (t) => {
    // The owner's case: with the wrong mic the VAD never opens, so there is no record at all.
    t.mock.timers.enable({ apis: ["setInterval"] });
    const page = await openVoice({ voice: { ...VOICE, records: [] } });

    assert.equal(page.element("voice-records").children.length, 0);
    assert.equal(page.element("voice-records-empty").hidden, false);

    page.setVoice(VOICE);
    t.mock.timers.tick(1_500);
    await settle();

    assert.equal(page.element("voice-records-empty").hidden, true);
  });

  it("lists the PC's mics to switch to, the config's order first, the one in use picked", async () => {
    const page = await openVoice();
    const picker = page.element("voice-device");

    assert.deepEqual(
      picker.children.map((option) => [option.value, option.textContent]),
      [
        ["", "Ikuti urutan di config"],
        ["Headset (Polaris-BT), MME", "Headset (Polaris-BT) · MME"],
        ["Microphone (High Definition Aud, MME", "Microphone (High Definition Aud · MME"],
      ],
    );
    assert.equal(picker.value, "");
  });

  it("keeps a phone pick that is not plugged in on the picker, so Pakai never drops it unasked", async () => {
    const unplugged = { ...VOICE, override_mic: "Headset (Polaris-BT), MME", devices: VOICE.devices.slice(1) };
    const page = await openVoice({ voice: unplugged });
    const picker = page.element("voice-device");

    assert.deepEqual(
      picker.children.map((option) => [option.value, option.textContent]),
      [
        ["", "Ikuti urutan di config"],
        ["Headset (Polaris-BT), MME", "Headset (Polaris-BT), MME (tidak tersambung)"],
        ["Microphone (High Definition Aud, MME", "Microphone (High Definition Aud · MME"],
      ],
    );
    assert.equal(picker.value, "Headset (Polaris-BT), MME");
  });

  it("shows the mic in effect again when the sheet opens after a pick that was never applied", async () => {
    const page = await openVoice();
    page.element("voice-device").value = "Microphone (High Definition Aud, MME";
    page.element("voice-close").dispatch("click");

    page.element("voice-toggle").dispatch("click");
    await settle();

    assert.equal(page.element("voice-device").value, "");
  });

  it("shows the mic in effect again after a switch the PC refused", async () => {
    const refused = { ok: false, message: "Mic itu tidak bisa dibuka. Tetap memakai Headset.", pending: false };
    const page = await openVoice({ voiceReply: refused });
    page.element("voice-device").value = "Microphone (High Definition Aud, MME";

    page.element("voice-switch").dispatch("click");
    await settle();

    assert.equal(page.element("voice-device").value, "");
  });

  it("fills the settings' boxes with the settings in use", async () => {
    const page = await openVoice();

    assert.equal(page.element("voice-speaker").value, "0.45");
    assert.equal(page.element("voice-threshold").value, "-45");
    assert.equal(page.element("voice-margin").value, "12");
  });

  it("reads the status every 1.5 s while it is open, and no longer once it closed", async (t) => {
    t.mock.timers.enable({ apis: ["setInterval"] });
    const page = await openVoice();
    const opened = voiceReads(page);

    t.mock.timers.tick(1_500);
    await settle();
    const polled = voiceReads(page);
    page.element("voice-close").dispatch("click");
    t.mock.timers.tick(15_000);
    await settle();

    assert.equal(polled, opened + 1);
    assert.equal(voiceReads(page), polled);
    assert.equal(page.element("voice").hidden, true);
  });

  it("is not read while the page is hidden, and again once it shows", async (t) => {
    t.mock.timers.enable({ apis: ["setInterval"] });
    const page = await openVoice();
    document.hidden = true;
    document.dispatch("visibilitychange");
    const before = voiceReads(page);

    t.mock.timers.tick(15_000);
    await settle();
    const whileHidden = voiceReads(page);
    document.hidden = false;
    document.dispatch("visibilitychange");
    t.mock.timers.tick(1_500);
    await settle();

    assert.equal(whileHidden, before);
    assert.ok(voiceReads(page) > before);
  });

  it("never asks twice at once while a reading is slow", async (t) => {
    t.mock.timers.enable({ apis: ["setInterval"] });
    const page = await openVoice();
    const fakeFetch = globalThis.fetch;
    const held = [];
    globalThis.fetch = (route, init) => {
      if (route !== "/voice") return fakeFetch(route, init);
      page.requests.push({ route, init });
      return new Promise((resolve) => held.push(() => resolve(jsonResponse(VOICE))));
    };
    const before = voiceReads(page);

    t.mock.timers.tick(6_000);
    await settle();

    assert.equal(voiceReads(page), before + 1);
    held[0]();
    await settle();
  });

  it("switches to the picked mic and shows the PC's answer", async () => {
    const page = await openVoice();
    page.element("voice-device").value = "Microphone (High Definition Aud, MME";

    page.element("voice-switch").dispatch("click");
    await settle();

    assert.deepEqual(sentJson(page, "/voice/set"), {
      action: "mic",
      device: "Microphone (High Definition Aud, MME",
    });
    assert.equal(page.element("voice-mic-note").textContent, SWITCHING.message); // beside the picker
  });

  it("shows how a switch that waited for her came out, a failure as one", async (t) => {
    t.mock.timers.enable({ apis: ["setInterval"] });
    const page = await openVoice();
    page.element("voice-device").value = "Microphone (High Definition Aud, MME";
    page.element("voice-switch").dispatch("click");
    await settle();
    page.setVoice({ ...VOICE, switch: { pending: true, last: null } });
    t.mock.timers.tick(1_500);
    await settle();

    assert.equal(page.element("voice-mic-note").textContent, SWITCHING.message); // still under way
    const failed = "Microphone (High Definition Aud, MME tidak bisa dibuka (Device unavailable). Tetap memakai X.";
    page.setVoice({ ...VOICE, switch: { pending: false, last: { ok: false, message: failed } } });
    t.mock.timers.tick(1_500);
    await settle();

    assert.equal(page.element("voice-mic-note").textContent, failed);
    assert.match(page.element("voice-mic-note").className, /\bproblem\b/);
  });

  it("drops a failed reading's problem once the status reads again", async (t) => {
    t.mock.timers.enable({ apis: ["setInterval"] });
    const page = await openVoice();
    page.setVoice(VOICE, 502);
    t.mock.timers.tick(1_500);
    await settle();
    assert.equal(page.element("voice-mic-note").textContent, problemText("offline", "Master"));

    page.setVoice(VOICE);
    t.mock.timers.tick(1_500);
    await settle();

    assert.equal(page.element("voice-mic-note").textContent, "");
  });

  it("saves the settings in the boxes, and refuses one out of its bounds without asking the PC", async () => {
    const page = await openVoice({ voiceReply: { ok: true, message: "Setelan suara dipakai dan disimpan.", pending: false } });
    page.element("voice-speaker").value = "0,5";
    page.element("voice-threshold").value = "-50";
    page.element("voice-margin").value = "10";

    page.element("voice-save").dispatch("click");
    await settle();

    assert.deepEqual(sentJson(page, "/voice/set"), {
      action: "tune",
      speaker_threshold: 0.5,
      vad_threshold_db: -50,
      vad_margin_db: 10,
    });
    assert.equal(page.element("voice-note").textContent, "Setelan suara dipakai dan disimpan.");

    const sent = page.requests.filter(({ route }) => route === "/voice/set").length;
    page.element("voice-margin").value = "40";
    page.element("voice-save").dispatch("click");
    await settle();

    assert.equal(page.requests.filter(({ route }) => route === "/voice/set").length, sent);
    assert.equal(page.element("voice-note").textContent, "Margin VAD harus 3 sampai 30 dB.");
  });

  it("puts the config's settings back", async () => {
    const page = await openVoice();

    page.element("voice-reset").dispatch("click");
    await settle();

    assert.deepEqual(sentJson(page, "/voice/set"), { action: "reset" });
  });

  it("closes and hides its button once guest mode hides it", async (t) => {
    t.mock.timers.enable({ apis: ["setInterval"] });
    const page = await openVoice();
    page.setVoice(VOICE, 403);

    t.mock.timers.tick(1_500);
    await settle();

    assert.equal(page.element("voice").hidden, true);
    assert.equal(page.element("voice-toggle").hidden, true);
  });
});

describe("phone Suara button", () => {
  it("comes back when the page shows again once guest mode ended", async () => {
    const page = await openPage({ voice: VOICE, voiceStatus: 403 });
    page.setVoice(VOICE, 200);

    document.dispatch("visibilitychange");
    await settle();

    assert.equal(page.element("voice-toggle").hidden, false);
  });
});
