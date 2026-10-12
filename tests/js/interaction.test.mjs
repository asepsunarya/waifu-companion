import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, it, mock } from "node:test";

import { startInteractions } from "../../src/waifu/avatar/web/interaction.js";
import { DOUBLE_CLICK_MS } from "../../src/waifu/avatar/web/logic.js";

// A 360x640 window: her head is a circle around (180, 80), her body a box below it, the rest is empty.
const HEAD = { x: 180, y: 80 };
const BODY = { x: 180, y: 300 };
const EMPTY = { x: 20, y: 300 };
const TOKEN = "page-token";
const BORED_AFTER_MS = 180_000;

function regionAt(x, y) {
  if (Math.hypot(x - 180, y - 80) <= 40) return "head";
  if (x >= 120 && x <= 240 && y >= 120 && y <= 600) return "body";
  return null;
}

function fakeRenderer() {
  const canvas = new EventTarget();
  canvas.clientWidth = 360;
  canvas.clientHeight = 640;
  canvas.setPointerCapture = () => {};
  const calls = [];
  return {
    canvas,
    calls,
    opaqueQueries: 0,
    setEmotion(name, holdS) {
      calls.push(["setEmotion", name, holdS]);
    },
    speak() {},
    setState() {},
    lookAt(point) {
      calls.push(["lookAt", point]);
    },
    setIdlePhase(phase) {
      calls.push(["setIdlePhase", phase]);
    },
    hitTest: regionAt,
    isOpaqueAt(x, y) {
      this.opaqueQueries += 1;
      return regionAt(x, y) !== null;
    },
    callsOf(name) {
      return calls.filter(([method]) => method === name).map(([, ...args]) => args);
    },
  };
}

function fakeBridge() {
  return {
    cursorListener: null,
    drags: [],
    clickThrough: [],
    onCursor(callback) {
      this.cursorListener = callback;
      return () => {
        this.cursorListener = null;
      };
    },
    dragBy(dx, dy) {
      this.drags.push([dx, dy]);
    },
    setClickThrough(flag) {
      this.clickThrough.push(flag);
    },
    moveCursor(x, y) {
      this.cursorListener?.({ x, y });
    },
    // The first report after the feed paused (the mascot was minimized or hidden).
    resumeCursor(x, y) {
      this.cursorListener?.({ x, y, resumed: true });
    },
  };
}

function settings(overrides = {}) {
  return {
    enabled: true,
    cursor_follow: true,
    head_pat: true,
    body_touch: true,
    double_click_listen: true,
    idle: true,
    welcome_back_after_min: 10,
    ...overrides,
  };
}

function pointer(
  canvas,
  type,
  { x, y },
  { screenOffset = { x: 1000, y: 400 }, button = 0, isPrimary = true, pointerId = 1 } = {},
) {
  const event = new Event(`pointer${type}`);
  Object.assign(event, {
    clientX: x,
    clientY: y,
    screenX: x + screenOffset.x,
    screenY: y + screenOffset.y,
    button,
    pointerId,
    isPrimary,
  });
  canvas.dispatchEvent(event);
}

let running = [];

function start({ interactions = settings(), bridge = null, fetchImpl, elementAt = () => null } = {}) {
  const renderer = fakeRenderer();
  const posts = [];
  const fetchFake =
    fetchImpl ??
    (async (url, init) => {
      posts.push({ url, init, body: JSON.parse(init.body) });
      return { ok: true, status: 202 };
    });
  const controller = startInteractions({
    renderer,
    config: { token: TOKEN, interactions },
    fetchImpl: fetchFake,
    bridge,
    now: () => Date.now(),
    elementAt,
  });
  running.push(controller);
  return { renderer, posts, controller, canvas: renderer.canvas, kinds: () => posts.map((post) => post.body.kind) };
}

function click(canvas, point, holdMs = 80) {
  pointer(canvas, "down", point);
  mock.timers.tick(holdMs);
  pointer(canvas, "up", point);
}

function rubHead(canvas, strokes) {
  pointer(canvas, "down", HEAD);
  for (let i = 0; i < strokes; i += 1) {
    mock.timers.tick(100);
    pointer(canvas, "move", { x: HEAD.x + (i % 2 === 0 ? 15 : -15), y: HEAD.y });
  }
}

beforeEach(() => {
  mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"], now: 1_000_000 });
  mock.method(console, "warn", () => {});
});

afterEach(() => {
  for (const controller of running) controller.stop();
  running = [];
  mock.timers.reset();
  mock.restoreAll();
});

describe("clicks", () => {
  it("pats her head: a happy face at once, then POST /interact with the token as JSON", () => {
    const { renderer, posts, canvas } = start();

    click(canvas, HEAD);

    assert.deepEqual(renderer.callsOf("setEmotion").map(([name]) => name), ["happy"]);
    assert.equal(posts.length, 0, "a click waits to see whether a second click follows");
    mock.timers.tick(DOUBLE_CLICK_MS);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].url, "/interact");
    assert.equal(posts[0].init.method, "POST");
    assert.equal(posts[0].init.headers["Content-Type"], "application/json");
    assert.deepEqual(posts[0].body, { token: TOKEN, kind: "head_pat" });
  });

  it("pokes her body: a surprised face and a body_touch", () => {
    const { renderer, canvas, kinds } = start();

    click(canvas, BODY);
    mock.timers.tick(DOUBLE_CLICK_MS);

    assert.deepEqual(renderer.callsOf("setEmotion").map(([name]) => name), ["surprised"]);
    assert.ok(renderer.callsOf("setEmotion")[0][1] > 0, "the face is held for a while");
    assert.deepEqual(kinds(), ["body_touch"]);
  });

  it("ignores presses beside her and other buttons", () => {
    const { renderer, canvas, kinds } = start();

    click(canvas, EMPTY);
    pointer(canvas, "down", HEAD, { button: 2 });
    pointer(canvas, "up", HEAD, { button: 2 });
    mock.timers.tick(1000);

    assert.deepEqual(kinds(), []);
    assert.deepEqual(renderer.callsOf("setEmotion"), []);
  });

  it("turns a double-click on her into one double_click, without the pat of its first click", () => {
    const { canvas, kinds } = start();

    click(canvas, HEAD, 60);
    mock.timers.tick(100);
    click(canvas, HEAD, 60);
    mock.timers.tick(2000);

    assert.deepEqual(kinds(), ["double_click"]);
  });

  it("sends a click at once when a slow second click shows it was no double-click", () => {
    const { canvas, kinds } = start();

    click(canvas, BODY, 60);
    mock.timers.tick(100);
    pointer(canvas, "down", { x: BODY.x + 40, y: BODY.y });
    assert.deepEqual(kinds(), [], "the first click waits while the second press is undecided");
    mock.timers.tick(60);
    pointer(canvas, "up", { x: BODY.x + 40, y: BODY.y });

    assert.deepEqual(kinds(), ["body_touch"]);
    mock.timers.tick(DOUBLE_CLICK_MS);
    assert.deepEqual(kinds(), ["body_touch", "body_touch"]);
  });

  it("sends clicks at once and a double-click as another click when double-click listening is off", () => {
    const { canvas, kinds } = start({ interactions: settings({ double_click_listen: false }) });

    click(canvas, HEAD, 60);
    assert.deepEqual(kinds(), ["head_pat"]);
    mock.timers.tick(100);
    click(canvas, HEAD, 60);

    assert.deepEqual(kinds(), ["head_pat", "head_pat"]);
  });

  it("honours the head_pat and body_touch switches, also for the face", () => {
    const { renderer, canvas, kinds } = start({ interactions: settings({ head_pat: false, body_touch: false }) });

    click(canvas, HEAD);
    mock.timers.tick(1000);
    click(canvas, BODY);
    mock.timers.tick(1000);

    assert.deepEqual(kinds(), []);
    assert.deepEqual(renderer.callsOf("setEmotion"), []);
  });

  it("sends a waiting click at once when the next press lands beside her", () => {
    const { canvas, kinds } = start();

    click(canvas, HEAD, 60);
    mock.timers.tick(100);
    pointer(canvas, "down", EMPTY);

    assert.deepEqual(kinds(), ["head_pat"]);
  });

  it("ignores a second finger or pen that is not the primary pointer", () => {
    const { renderer, canvas, kinds } = start();

    pointer(canvas, "down", HEAD, { isPrimary: false, pointerId: 2 });
    mock.timers.tick(80);
    pointer(canvas, "up", HEAD, { isPrimary: false, pointerId: 2 });
    mock.timers.tick(1000);

    assert.deepEqual(kinds(), []);
    assert.deepEqual(renderer.callsOf("setEmotion"), []);
  });

  it("recovers from a cancelled press: the waiting click is sent and the next press works", () => {
    const bridge = fakeBridge();
    const { canvas, kinds } = start({ bridge });

    click(canvas, HEAD, 60);
    mock.timers.tick(100);
    pointer(canvas, "down", HEAD);
    canvas.dispatchEvent(new Event("pointercancel"));
    assert.deepEqual(kinds(), ["head_pat"], "the cancel does not swallow the waiting click");

    mock.timers.tick(1000);
    click(canvas, BODY);
    mock.timers.tick(DOUBLE_CLICK_MS);
    assert.deepEqual(kinds(), ["head_pat", "body_touch"]);
    bridge.moveCursor(EMPTY.x, EMPTY.y);
    assert.deepEqual(bridge.clickThrough, [true], "click-through works again once no press is held");
  });

  it("does nothing on clicks when interactions are disabled", () => {
    const { renderer, canvas, kinds } = start({ interactions: settings({ enabled: false }) });

    click(canvas, HEAD);
    mock.timers.tick(100);
    click(canvas, HEAD);
    mock.timers.tick(1000);

    assert.deepEqual(kinds(), []);
    assert.deepEqual(renderer.callsOf("setEmotion"), []);
  });
});

describe("rubbing her head", () => {
  it("pats at most every 1.5 s while the rub goes on", () => {
    const { renderer, canvas, kinds } = start();

    rubHead(canvas, 4);
    assert.deepEqual(kinds(), ["head_pat"], "the third turn makes it a rub");
    // 3.5 s more of rubbing: pats 1.5 s and 3 s after the first one.
    for (let i = 0; i < 35; i += 1) {
      mock.timers.tick(100);
      pointer(canvas, "move", { x: HEAD.x + (i % 2 === 0 ? 15 : -15), y: HEAD.y });
    }
    pointer(canvas, "up", HEAD);
    mock.timers.tick(1000);

    assert.deepEqual(kinds(), ["head_pat", "head_pat", "head_pat"]);
    assert.ok(renderer.callsOf("setEmotion").every(([name]) => name === "happy"));
  });

  it("sends a waiting click when a rub follows it, then pats no more often than every 1.5 s", () => {
    const { canvas, kinds } = start();

    click(canvas, HEAD, 60);
    mock.timers.tick(100);
    rubHead(canvas, 4);

    assert.deepEqual(kinds(), ["head_pat"], "the click's pat is sent; the rub's own pat comes too soon after it");
  });
});

describe("dragging", () => {
  it("moves the mascot by the pointer's screen movement once the press drags", () => {
    const bridge = fakeBridge();
    const { canvas, kinds } = start({ bridge });

    pointer(canvas, "down", BODY);
    pointer(canvas, "move", { x: BODY.x + 3, y: BODY.y });
    assert.deepEqual(bridge.drags, [], "within the slop it is no drag yet");
    pointer(canvas, "move", { x: BODY.x + 10, y: BODY.y + 4 });
    pointer(canvas, "move", { x: BODY.x + 25, y: BODY.y - 6 });
    pointer(canvas, "up", { x: BODY.x + 25, y: BODY.y - 6 });
    mock.timers.tick(1000);

    assert.deepEqual(bridge.drags, [
      [10, 4],
      [15, -10],
    ]);
    assert.deepEqual(kinds(), [], "a drag is no poke");
  });

  it("sends a waiting click when a drag follows it", () => {
    const bridge = fakeBridge();
    const { canvas, kinds } = start({ bridge });

    click(canvas, BODY, 60);
    mock.timers.tick(100);
    pointer(canvas, "down", BODY);
    pointer(canvas, "move", { x: BODY.x + 30, y: BODY.y });

    assert.deepEqual(kinds(), ["body_touch"]);
    assert.deepEqual(bridge.drags, [[30, 0]]);
  });

  it("still drags the mascot when interactions are disabled", () => {
    const bridge = fakeBridge();
    const { canvas } = start({ bridge, interactions: settings({ enabled: false }) });

    pointer(canvas, "down", BODY);
    pointer(canvas, "move", { x: BODY.x + 30, y: BODY.y });

    assert.deepEqual(bridge.drags, [[30, 0]]);
  });

  it("does not drag without the mascot bridge", () => {
    const { canvas, kinds } = start();

    pointer(canvas, "down", BODY);
    pointer(canvas, "move", { x: BODY.x + 30, y: BODY.y });
    pointer(canvas, "up", { x: BODY.x + 30, y: BODY.y });
    mock.timers.tick(1000);

    assert.deepEqual(kinds(), []);
  });
});

describe("click-through", () => {
  it("lets clicks through where she is transparent, from the mascot's cursor feed", () => {
    const bridge = fakeBridge();
    start({ bridge });

    bridge.moveCursor(EMPTY.x, EMPTY.y);
    mock.timers.tick(100);
    bridge.moveCursor(BODY.x, BODY.y);

    assert.deepEqual(bridge.clickThrough, [true, false]);
  });

  it("keeps the clicks over the page's own controls, such as the close button, where she is transparent", () => {
    const bridge = fakeBridge();
    const closeButton = { id: "close" };
    start({ bridge, elementAt: (x, y) => (x >= 320 && y <= 40 ? closeButton : null) });

    bridge.moveCursor(EMPTY.x, EMPTY.y);
    mock.timers.tick(100);
    bridge.moveCursor(340, 16);

    assert.deepEqual(bridge.clickThrough, [true, false]);
  });

  it("asks isOpaqueAt at most 15 times a second and settles on the last cursor position", () => {
    const bridge = fakeBridge();
    const { renderer } = start({ bridge });

    for (let i = 0; i < 30; i += 1) {
      bridge.moveCursor(BODY.x, BODY.y + i);
      mock.timers.tick(10);
    }
    bridge.moveCursor(EMPTY.x, EMPTY.y);
    mock.timers.tick(200);

    assert.ok(renderer.opaqueQueries <= 6, `${renderer.opaqueQueries} queries in 0.5 s`);
    assert.deepEqual(bridge.clickThrough, [false, true]);
  });

  it("does not ask while the cursor is outside the window or a press is held", () => {
    const bridge = fakeBridge();
    const { renderer, canvas } = start({ bridge });

    bridge.moveCursor(-200, 300);
    mock.timers.tick(100);
    bridge.moveCursor(500, 900);
    mock.timers.tick(100);
    assert.equal(renderer.opaqueQueries, 0);

    pointer(canvas, "down", BODY);
    bridge.moveCursor(EMPTY.x, EMPTY.y);
    mock.timers.tick(100);

    assert.deepEqual(bridge.clickThrough, []);
  });
});

describe("cursor look", () => {
  it("looks at the mascot's cursor, also outside the window", () => {
    const bridge = fakeBridge();
    const { renderer } = start({ bridge });

    bridge.moveCursor(-300, 50);

    assert.deepEqual(renderer.callsOf("lookAt"), [[{ x: -300, y: 50 }]]);
  });

  it("looks at the pointer in the app window and ahead again when it leaves", () => {
    const { renderer, canvas } = start();

    pointer(canvas, "move", { x: 30, y: 40 });
    canvas.dispatchEvent(new Event("pointerleave"));

    assert.deepEqual(renderer.callsOf("lookAt"), [[{ x: 30, y: 40 }], [null]]);
  });

  it("never moves her eyes when cursor_follow is off", () => {
    const bridge = fakeBridge();
    const { renderer } = start({ bridge, interactions: settings({ cursor_follow: false }) });

    bridge.moveCursor(10, 10);

    assert.deepEqual(renderer.callsOf("lookAt"), []);
  });
});

describe("idle and welcome back", () => {
  it("gets bored without activity and active again on a cursor move", () => {
    const bridge = fakeBridge();
    const { renderer } = start({ bridge });
    bridge.moveCursor(10, 10);

    mock.timers.tick(BORED_AFTER_MS - 2000);
    assert.deepEqual(renderer.callsOf("setIdlePhase"), []);
    mock.timers.tick(3000);
    assert.deepEqual(renderer.callsOf("setIdlePhase"), [["bored"]]);
    bridge.moveCursor(11, 10);

    assert.deepEqual(renderer.callsOf("setIdlePhase"), [["bored"], ["active"]]);
  });

  it("counts a speak event as activity without greeting", () => {
    const bridge = fakeBridge();
    const { renderer, controller, kinds } = start({ bridge });
    bridge.moveCursor(10, 10);

    mock.timers.tick(11 * 60_000);
    controller.handleEvent({ type: "speak", emotion: "happy", fps: 60, duration: 1, visemes: [] });
    assert.deepEqual(kinds(), [], "her own announcement is not Master coming back");
    bridge.moveCursor(12, 10);

    assert.deepEqual(kinds(), [], "Master was there while she spoke");
    assert.deepEqual(renderer.callsOf("setIdlePhase").at(-1), ["active"]);
  });

  it("does not count other events as activity", () => {
    const { renderer, controller } = start();

    mock.timers.tick(BORED_AFTER_MS - 1000);
    controller.handleEvent({ type: "state", state: "idle" });
    controller.handleEvent({ type: "emotion", emotion: "happy", hold_s: 4 });
    mock.timers.tick(2000);

    assert.deepEqual(renderer.callsOf("setIdlePhase"), [["bored"]]);
  });

  it("never gets bored with the idle switch off", () => {
    const { renderer } = start({ interactions: settings({ idle: false }) });

    mock.timers.tick(BORED_AFTER_MS * 2);

    assert.deepEqual(renderer.callsOf("setIdlePhase"), []);
  });

  it("greets once when the owner moves the mouse after welcome_back_after_min idle minutes", () => {
    const bridge = fakeBridge();
    const { kinds } = start({ bridge, interactions: settings({ welcome_back_after_min: 10 }) });
    bridge.moveCursor(10, 10);

    mock.timers.tick(9 * 60_000);
    bridge.moveCursor(12, 10);
    assert.deepEqual(kinds(), [], "nine minutes is no absence");
    mock.timers.tick(10 * 60_000);
    bridge.moveCursor(14, 10);
    bridge.moveCursor(16, 10);

    assert.deepEqual(kinds(), ["welcome_back"]);
  });

  it("greets on a press in the mascot after the absence, before anything else", () => {
    const bridge = fakeBridge();
    const { canvas, kinds } = start({ bridge, interactions: settings({ welcome_back_after_min: 1 }) });
    bridge.moveCursor(BODY.x, BODY.y);

    mock.timers.tick(61_000);
    pointer(canvas, "down", BODY);

    assert.deepEqual(kinds(), ["welcome_back"]);
  });

  it("never greets in the app window, which sees the pointer only while it is over the page", () => {
    const { renderer, canvas, kinds } = start({ interactions: settings({ welcome_back_after_min: 1 }) });

    mock.timers.tick(11 * 60_000);
    pointer(canvas, "move", EMPTY);
    mock.timers.tick(11 * 60_000);
    click(canvas, BODY);
    mock.timers.tick(DOUBLE_CLICK_MS);

    assert.deepEqual(kinds(), ["body_touch"]);
    assert.deepEqual(renderer.callsOf("setIdlePhase"), [["bored"], ["active"], ["bored"], ["active"]]);
  });

  it("wakes up on a press in the app window", () => {
    const { renderer, canvas } = start();

    mock.timers.tick(BORED_AFTER_MS + 1000);
    pointer(canvas, "down", EMPTY);

    assert.deepEqual(renderer.callsOf("setIdlePhase"), [["bored"], ["active"]]);
  });

  it("does not greet when the mascot's cursor feed resumes after she was minimized", () => {
    const bridge = fakeBridge();
    const { renderer, kinds } = start({ bridge, interactions: settings({ welcome_back_after_min: 10 }) });
    bridge.moveCursor(10, 10);

    mock.timers.tick(11 * 60_000);
    bridge.resumeCursor(300, 200);
    bridge.moveCursor(305, 200);

    assert.deepEqual(kinds(), [], "Master was busy elsewhere, not away");
    assert.deepEqual(renderer.callsOf("setIdlePhase"), [["bored"], ["active"]]);
    assert.deepEqual(renderer.callsOf("lookAt").at(-1), [{ x: 305, y: 200 }]);
  });

  it("never greets with welcome_back_after_min 0 or interactions disabled", () => {
    for (const interactions of [settings({ welcome_back_after_min: 0 }), settings({ enabled: false })]) {
      const bridge = fakeBridge();
      const { kinds } = start({ bridge, interactions });
      bridge.moveCursor(10, 10);
      mock.timers.tick(24 * 60 * 60_000);
      bridge.moveCursor(20, 10);

      assert.deepEqual(kinds(), []);
    }
  });

  it("does not take the mascot's first cursor report for a move", () => {
    const bridge = fakeBridge();
    const { kinds } = start({ bridge, interactions: settings({ welcome_back_after_min: 1 }) });

    mock.timers.tick(5 * 60_000);
    bridge.moveCursor(10, 10);

    assert.deepEqual(kinds(), []);
  });
});

describe("robustness", () => {
  it("never throws when Cyrene cannot be reached or refuses", async () => {
    const failures = [
      async () => {
        throw new TypeError("Failed to fetch");
      },
      async () => ({ ok: false, status: 403 }),
      () => {
        throw new Error("synchronous failure");
      },
    ];
    for (const fetchImpl of failures) {
      const { canvas, renderer } = start({ fetchImpl, interactions: settings({ double_click_listen: false }) });
      assert.doesNotThrow(() => click(canvas, BODY));
      assert.deepEqual(renderer.callsOf("setEmotion").map(([name]) => name), ["surprised"]);
    }
    await new Promise((resolve) => setImmediate(resolve));
  });

  it("stops listening, polling and waiting on stop()", () => {
    const bridge = fakeBridge();
    const { renderer, canvas, controller, kinds } = start({ bridge });

    click(canvas, HEAD);
    controller.stop();
    mock.timers.tick(BORED_AFTER_MS * 2);
    click(canvas, BODY);
    pointer(canvas, "move", { x: 30, y: 40 });

    assert.equal(bridge.cursorListener, null);
    assert.deepEqual(kinds(), []);
    assert.deepEqual(renderer.callsOf("setIdlePhase"), []);
  });
});
