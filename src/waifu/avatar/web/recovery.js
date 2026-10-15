// Keeping her on screen when the GPU drops the page's WebGL context (SPEC, "keeping her through fullscreen games
// (v1.15.1)"). A fullscreen game's display-mode switch, a GPU reset, or Android Chrome putting a page in the background
// can lose the context of her canvas; neither renderer comes back from that by itself (the Live2D engine never
// rebuilds its GPU resources), so the canvas would stay empty. The avatar page (index.html) reloads itself instead,
// the phone page draws her again on a fresh canvas (keepRenderer), so its chat stays. In the mascot, what happens is
// also reported to Cyrene's log through the Electron preload's window.cyrene.diagnostic.

// How long a lost context may take to come back before the page recovers without it.
export const CONTEXT_RESTORE_WAIT_MS = 3000;

// What the page reports (avatar-app/lib.js DIAGNOSTIC_KINDS lists what the mascot accepts).
export const DIAGNOSTICS = Object.freeze({
  visible: "page-visible",
  hidden: "page-hidden",
  contextLost: "webgl-context-lost",
  contextRestored: "webgl-context-restored",
  webglUnavailable: "webgl-unavailable",
  reload: "page-reload",
});

/**
 * What createRenderer (live2d.js, avatar.js) rejects with when the window gives it no WebGL context, after putting the
 * Indonesian message on the page; `cause` is the library's own error.
 */
export class WebGLUnavailableError extends Error {}

/** Tells the mascot's log `kind` (one of DIAGNOSTICS) through `bridge`; without the mascot's bridge, nothing. */
export function reportDiagnostic(kind, bridge = globalThis.cyrene) {
  if (typeof bridge?.diagnostic === "function") bridge.diagnostic(kind);
}

/**
 * Reports why her renderer could not start, when the page can do nothing about it: no WebGL (WebGLUnavailableError).
 * The mascot's main process then reloads the page, or opens a fresh mascot when Electron has no WebGL left.
 */
export function reportRendererFailure(error, report = reportDiagnostic) {
  if (error instanceof WebGLUnavailableError) report(DIAGNOSTICS.webglUnavailable);
}

/** Reports every visibility change of `doc`. Returns the function that stops it. */
export function watchVisibility(doc, report = reportDiagnostic) {
  const onChange = () => report(doc.hidden ? DIAGNOSTICS.hidden : DIAGNOSTICS.visible);
  doc.addEventListener("visibilitychange", onChange);
  return () => doc.removeEventListener("visibilitychange", onChange);
}

/**
 * Watches `canvas` for a lost WebGL context: lets the browser restore it (preventDefault), reports the loss and the
 * restore, and calls `recover()` once, when the context is restored or `waitMs` after the loss without that. Then it
 * stops watching: the page reloads, or draws on a fresh canvas, so at most one recovery is ever pending. Returns the
 * function that stops it.
 */
export function watchContextLoss(canvas, { recover, report = reportDiagnostic, waitMs = CONTEXT_RESTORE_WAIT_MS }) {
  let timer = null;

  function stop() {
    clearTimeout(timer);
    canvas.removeEventListener("webglcontextlost", onLost);
    canvas.removeEventListener("webglcontextrestored", onRestored);
  }

  function recoverOnce() {
    stop();
    recover();
  }

  function onLost(event) {
    event.preventDefault();
    report(DIAGNOSTICS.contextLost);
    timer ??= setTimeout(recoverOnce, waitMs);
  }

  function onRestored() {
    report(DIAGNOSTICS.contextRestored);
    recoverOnce();
  }

  canvas.addEventListener("webglcontextlost", onLost);
  canvas.addEventListener("webglcontextrestored", onRestored);
  return stop;
}

/** Runs `action` now while `doc` is visible, else once it becomes visible. */
function whenVisible(doc, action) {
  if (!doc.hidden) {
    action();
    return;
  }
  const onChange = () => {
    if (doc.hidden) return;
    doc.removeEventListener("visibilitychange", onChange);
    action();
  };
  doc.addEventListener("visibilitychange", onChange);
}

/**
 * Keeps her drawn through lost WebGL contexts without reloading the page. `renderer` (the interface at the top of
 * live2d.js) draws on the page's canvas. After a lost context (watchContextLoss), once the page is visible (`doc`: a
 * page in the background may get no context), it is destroyed, its canvas replaced by a fresh copy, and `start()`,
 * which resolves to a renderer on the page's canvas or null, draws her again. `onRenderer` gets null meanwhile, then
 * the new renderer (null when it did not start), which is kept the same way.
 */
export function keepRenderer(
  renderer,
  { start, onRenderer, doc = globalThis.document, waitMs = CONTEXT_RESTORE_WAIT_MS },
) {
  async function redraw(lost) {
    onRenderer(null);
    try {
      lost.destroy();
    } catch (error) {
      // A library may fail to let go of a lost context; the fresh canvas gets her back all the same.
      console.warn("Her renderer could not let go of its lost WebGL context", error);
    }
    lost.canvas.replaceWith(lost.canvas.cloneNode(false));
    const next = await start();
    onRenderer(next);
    if (next !== null) keep(next);
  }

  function keep(current) {
    watchContextLoss(current.canvas, { waitMs, recover: () => whenVisible(doc, () => redraw(current)) });
  }

  keep(renderer);
}
