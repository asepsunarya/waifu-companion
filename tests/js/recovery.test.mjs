// Keeping her on screen when the GPU drops the page's WebGL context (SPEC, "keeping her through fullscreen games
// (v1.15.1)"): recovery.js, run against fake canvases, a fake document and node's mock timers. No browser or GPU.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { afterEach, beforeEach, describe, it, mock } from "node:test";

import {
  CONTEXT_RESTORE_WAIT_MS,
  DIAGNOSTICS,
  WebGLUnavailableError,
  keepRenderer,
  reportDiagnostic,
  reportRendererFailure,
  watchContextLoss,
  watchVisibility,
} from "../../src/waifu/avatar/web/recovery.js";

const { isDiagnosticKind } = createRequire(import.meta.url)("../../avatar-app/lib.js");

/** A canvas: events, and the two DOM calls keepRenderer makes to draw on a fresh one. */
class FakeCanvas extends EventTarget {
  static made = 0;

  constructor() {
    super();
    FakeCanvas.made += 1;
    this.number = FakeCanvas.made;
    this.replacedBy = null;
  }

  cloneNode(deep) {
    assert.equal(deep, false);
    return new FakeCanvas();
  }

  replaceWith(node) {
    this.replacedBy = node;
  }

  /** Fires `type` as a browser does; returns the event, whose defaultPrevented tells whether restoring is allowed. */
  fire(type) {
    const event = new Event(type, { cancelable: true });
    this.dispatchEvent(event);
    return event;
  }
}

/** The page's document: `hidden`, and its visibilitychange events. */
class FakeDocument extends EventTarget {
  constructor() {
    super();
    this.hidden = false;
  }

  setHidden(hidden) {
    this.hidden = hidden;
    this.dispatchEvent(new Event("visibilitychange"));
  }
}

/** Lets pending promises run; setImmediate is not mocked. */
async function flush() {
  for (let i = 0; i < 10; i++) await new Promise((resolve) => setImmediate(resolve));
}

beforeEach(() => {
  mock.timers.enable({ apis: ["setTimeout"] });
});

afterEach(() => {
  mock.timers.reset();
});

describe("reportDiagnostic", () => {
  it("tells the mascot's bridge", () => {
    const kinds = [];

    reportDiagnostic(DIAGNOSTICS.contextLost, { diagnostic: (kind) => kinds.push(kind) });

    assert.deepEqual(kinds, ["webgl-context-lost"]);
  });

  it("does nothing without the bridge: Edge's app window, a browser, the phone", () => {
    assert.doesNotThrow(() => reportDiagnostic(DIAGNOSTICS.contextLost, undefined));
    assert.doesNotThrow(() => reportDiagnostic(DIAGNOSTICS.contextLost, null));
    assert.doesNotThrow(() => reportDiagnostic(DIAGNOSTICS.contextLost, {}));
  });

  it("uses window.cyrene by default", () => {
    const kinds = [];
    globalThis.cyrene = { diagnostic: (kind) => kinds.push(kind) };
    try {
      reportDiagnostic(DIAGNOSTICS.hidden);
    } finally {
      delete globalThis.cyrene;
    }

    assert.deepEqual(kinds, ["page-hidden"]);
  });

  it("reports only kinds the mascot's main process accepts", () => {
    for (const kind of Object.values(DIAGNOSTICS)) {
      assert.ok(isDiagnosticKind(kind), kind);
    }
  });
});

describe("reportRendererFailure", () => {
  it("tells the mascot's log that the window gave her renderer no WebGL", () => {
    const reported = [];
    const error = new WebGLUnavailableError("WebGL is not available", { cause: new Error("No available renderer") });

    reportRendererFailure(error, (kind) => reported.push(kind));

    assert.deepEqual(reported, ["webgl-unavailable"]);
    assert.ok(error instanceof Error);
    assert.equal(error.cause.message, "No available renderer");
  });

  it("reports nothing for any other failure: a model that cannot load is no GPU problem", () => {
    const reported = [];

    reportRendererFailure(new Error("model.vrm answered HTTP 404"), (kind) => reported.push(kind));
    reportRendererFailure(undefined, (kind) => reported.push(kind));

    assert.deepEqual(reported, []);
  });

  it("reports through window.cyrene by default", () => {
    const kinds = [];
    globalThis.cyrene = { diagnostic: (kind) => kinds.push(kind) };
    try {
      reportRendererFailure(new WebGLUnavailableError("WebGL is not available"));
    } finally {
      delete globalThis.cyrene;
    }

    assert.deepEqual(kinds, ["webgl-unavailable"]);
  });
});

describe("watchVisibility", () => {
  it("reports every visibility change of the page", () => {
    const doc = new FakeDocument();
    const reported = [];
    watchVisibility(doc, (kind) => reported.push(kind));

    doc.setHidden(true);
    doc.setHidden(false);

    assert.deepEqual(reported, ["page-hidden", "page-visible"]);
  });

  it("stops when asked", () => {
    const doc = new FakeDocument();
    const reported = [];
    const stop = watchVisibility(doc, (kind) => reported.push(kind));

    stop();
    doc.setHidden(true);

    assert.deepEqual(reported, []);
  });
});

describe("watchContextLoss", () => {
  function watch(canvas = new FakeCanvas()) {
    const calls = { recovered: 0, reported: [] };
    const stop = watchContextLoss(canvas, {
      recover: () => {
        calls.recovered += 1;
      },
      report: (kind) => calls.reported.push(kind),
    });
    return { canvas, calls, stop };
  }

  it("lets the browser restore a lost context and reports the loss", () => {
    const { canvas, calls } = watch();

    const lost = canvas.fire("webglcontextlost");

    assert.equal(lost.defaultPrevented, true);
    assert.deepEqual(calls.reported, ["webgl-context-lost"]);
    assert.equal(calls.recovered, 0);
  });

  it("recovers as soon as the context is restored", () => {
    const { canvas, calls } = watch();
    canvas.fire("webglcontextlost");
    mock.timers.tick(1000);

    canvas.fire("webglcontextrestored");

    assert.deepEqual(calls.reported, ["webgl-context-lost", "webgl-context-restored"]);
    assert.equal(calls.recovered, 1);
  });

  it("recovers 3 s after a loss that is never restored", () => {
    const { canvas, calls } = watch();
    canvas.fire("webglcontextlost");

    mock.timers.tick(CONTEXT_RESTORE_WAIT_MS - 1);
    assert.equal(calls.recovered, 0);
    mock.timers.tick(1);

    assert.equal(CONTEXT_RESTORE_WAIT_MS, 3000);
    assert.equal(calls.recovered, 1);
  });

  it("recovers once for a restore within the 3 s", () => {
    const { canvas, calls } = watch();
    canvas.fire("webglcontextlost");
    canvas.fire("webglcontextrestored");

    mock.timers.tick(CONTEXT_RESTORE_WAIT_MS * 2);

    assert.equal(calls.recovered, 1);
  });

  it("keeps one recovery pending however often the context is lost", () => {
    const { canvas, calls } = watch();
    canvas.fire("webglcontextlost");
    mock.timers.tick(2000);
    canvas.fire("webglcontextlost");

    mock.timers.tick(1000);
    assert.equal(calls.recovered, 1, "3 s after the first loss");
    mock.timers.tick(CONTEXT_RESTORE_WAIT_MS * 2);

    assert.equal(calls.recovered, 1);
  });

  it("stops watching once it recovered: the page is reloading or drawing on a fresh canvas", () => {
    const { canvas, calls } = watch();
    canvas.fire("webglcontextlost");
    canvas.fire("webglcontextrestored");

    const lostAgain = canvas.fire("webglcontextlost");
    canvas.fire("webglcontextrestored");
    mock.timers.tick(CONTEXT_RESTORE_WAIT_MS);

    assert.equal(calls.recovered, 1);
    assert.equal(lostAgain.defaultPrevented, false);
    assert.deepEqual(calls.reported, ["webgl-context-lost", "webgl-context-restored"]);
  });

  it("does nothing after stop(), also for losses already waiting", () => {
    const { canvas, calls, stop } = watch();
    canvas.fire("webglcontextlost");
    mock.timers.tick(1000);
    canvas.fire("webglcontextlost");

    stop();
    mock.timers.tick(CONTEXT_RESTORE_WAIT_MS);
    canvas.fire("webglcontextrestored");

    assert.equal(calls.recovered, 0);
    assert.deepEqual(calls.reported, ["webgl-context-lost", "webgl-context-lost"]);
  });

  it("reports through window.cyrene by default", () => {
    const kinds = [];
    globalThis.cyrene = { diagnostic: (kind) => kinds.push(kind) };
    const canvas = new FakeCanvas();
    try {
      watchContextLoss(canvas, { recover() {} });
      canvas.fire("webglcontextlost");
    } finally {
      delete globalThis.cyrene;
    }

    assert.deepEqual(kinds, ["webgl-context-lost"]);
  });
});

describe("keepRenderer", () => {
  /**
   * A page whose renderers (made by `start`, after an await like the real module import) draw on the canvas the page
   * shows at that moment: the first one, or what replaced it. `failing` makes `start` fail. `keep()` starts the first
   * renderer and keeps it, as the phone page does.
   */
  function fakePage() {
    const first = new FakeCanvas();
    const page = { doc: new FakeDocument(), made: [], shown: [], failing: false };
    page.stage = () => {
      let canvas = first;
      while (canvas.replacedBy) canvas = canvas.replacedBy;
      return canvas;
    };
    page.start = async () => {
      await null;
      if (page.failing) return null;
      const renderer = {
        canvas: page.stage(),
        destroyed: false,
        destroy() {
          this.destroyed = true;
        },
      };
      page.made.push(renderer);
      return renderer;
    };
    page.keep = async () => {
      const renderer = await page.start();
      keepRenderer(renderer, {
        start: page.start,
        onRenderer: (next) => page.shown.push(next),
        doc: page.doc,
      });
      return renderer;
    };
    return page;
  }

  async function loseContext(page, { restore = true } = {}) {
    const canvas = page.made.at(-1).canvas;
    canvas.fire("webglcontextlost");
    if (restore) canvas.fire("webglcontextrestored");
    else mock.timers.tick(CONTEXT_RESTORE_WAIT_MS);
    await flush();
  }

  it("leaves a renderer whose context is never lost alone", async () => {
    const page = fakePage();

    const first = await page.keep();
    mock.timers.tick(CONTEXT_RESTORE_WAIT_MS * 10);
    await flush();

    assert.deepEqual(page.made, [first]);
    assert.equal(first.destroyed, false);
    assert.deepEqual(page.shown, []);
  });

  it("draws her again on a fresh canvas once a lost context is restored, without reloading the page", async () => {
    const page = fakePage();
    const first = await page.keep();

    await loseContext(page);

    assert.equal(first.destroyed, true);
    assert.ok(first.canvas.replacedBy instanceof FakeCanvas, "the lost canvas is replaced");
    assert.equal(page.made.length, 2);
    const [, second] = page.made;
    assert.equal(second.canvas, first.canvas.replacedBy);
    assert.equal(second.destroyed, false);
    assert.deepEqual(page.shown, [null, second]);
  });

  it("draws her again 3 s after a loss that is never restored", async () => {
    const page = fakePage();
    const first = await page.keep();

    await loseContext(page, { restore: false });

    assert.equal(page.made.length, 2);
    assert.equal(first.destroyed, true);
  });

  it("waits until the page is visible again (Android Chrome drops the context of a hidden page)", async () => {
    const page = fakePage();
    const first = await page.keep();
    page.doc.setHidden(true);

    await loseContext(page, { restore: false });
    assert.equal(page.made.length, 1);
    assert.equal(first.destroyed, false);
    page.doc.setHidden(false);
    await flush();

    assert.equal(page.made.length, 2);
    assert.equal(first.destroyed, true);
  });

  it("keeps her through every later loss too", async () => {
    const page = fakePage();
    await page.keep();
    await loseContext(page);
    const [, second] = page.made;

    await loseContext(page);

    assert.equal(page.made.length, 3);
    assert.equal(second.destroyed, true);
    assert.equal(page.made[2].canvas, second.canvas.replacedBy);
  });

  it("draws her again even when the lost renderer fails to let go", async (t) => {
    const page = fakePage();
    const first = await page.keep();
    first.destroy = () => {
      throw new TypeError("Cannot read properties of null (reading 'next')");
    };
    const warnings = t.mock.method(console, "warn", () => {});

    await loseContext(page);

    assert.equal(page.made.length, 2);
    assert.equal(page.made[1].canvas, first.canvas.replacedBy);
    assert.deepEqual(page.shown, [null, page.made[1]]);
    assert.equal(warnings.mock.callCount(), 1);
  });

  it("hands the page null when her new renderer cannot start, and then watches nothing", async () => {
    const page = fakePage();
    await page.keep();
    page.failing = true;

    await loseContext(page);
    const lostAgain = page.stage().fire("webglcontextlost");
    mock.timers.tick(CONTEXT_RESTORE_WAIT_MS);
    await flush();

    assert.deepEqual(page.shown, [null, null]);
    assert.equal(lostAgain.defaultPrevented, false);
    assert.equal(page.stage().replacedBy, null);
  });
});
