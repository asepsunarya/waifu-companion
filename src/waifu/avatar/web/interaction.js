// Mouse interactions for both renderers (avatar.js for VRM, live2d.js for Live2D): a pat on her head, a poke
// on her body, a double-click that makes her listen, dragging the mascot window, clicks passing through her
// transparent pixels, her eyes following the cursor, and the bored / welcome-back idle tracking. Gestures come
// from logic.classifyPointer; she reacts with a face at once and tells Cyrene through POST /interact, which
// picks the spoken line.
import { DOUBLE_CLICK_MS, classifyPointer, idlePhase } from "./logic.js";

const INTERACT_URL = "/interact";

// The face she makes at once, before Cyrene answers with a spoken line.
const FEEDBACK_EMOTIONS = { head_pat: "happy", body_touch: "surprised" };
const FEEDBACK_HOLD_S = 3;
// The switch in config.interactions that allows each kind of interaction.
const SWITCHES = { head_pat: "head_pat", body_touch: "body_touch", double_click: "double_click_listen" };
const RUB_PAT_GAP_MS = 1500;
// isOpaqueAt casts a ray through the model, so the mascot asks at most 15 times a second.
const CLICK_THROUGH_GAP_MS = 1000 / 15;
const IDLE_CHECK_MS = 1000;
const MS_PER_MIN = 60_000;
const PRIMARY_BUTTON = 0;

/**
 * Starts the interactions on `renderer.canvas` (the renderer interface is described in live2d.js). `config` is
 * the page's /config.json: its `token` and `interactions` switches. `bridge` is the mascot's window.cyrene
 * (Electron): with it the cursor comes from its feed, a drag moves the window, and clicks pass through her
 * transparent pixels; without it (the app window) the cursor comes from pointermove and nothing moves.
 * Dragging and click-through stay on with interactions disabled, or the mascot could not be moved at all.
 * Only the mascot greets Master back: its feed sees the cursor anywhere on the screen, so a still cursor means
 * Master was away. The app window sees the pointer only over the page, and a feed that resumes after the
 * mascot was minimized (`resumed: true`) missed what happened meanwhile: both count as activity, never as
 * coming back.
 * `elementAt(x, y)` names the page element on top at a point (document.elementFromPoint): anything there but
 * her canvas, such as the close button, keeps its clicks even where she is transparent.
 * Returns `{handleEvent(event), stop()}`: index.html passes every /events event to handleEvent, where a speak
 * event counts as activity (but never greets: Cyrene talking is not the owner coming back).
 */
export function startInteractions({
  renderer,
  config,
  fetchImpl = fetch,
  bridge = globalThis.cyrene,
  now = () => performance.now(),
  elementAt = (x, y) => globalThis.document?.elementFromPoint(x, y) ?? null,
}) {
  const settings = config?.interactions ?? {};
  const enabled = settings.enabled === true;
  const allowed = (name) => enabled && settings[name] === true;
  const welcomeBackMs =
    enabled && settings.welcome_back_after_min > 0 ? settings.welcome_back_after_min * MS_PER_MIN : Infinity;
  const canvas = renderer.canvas;
  const mascot = bridge != null;
  const cleanups = [];

  function listen(target, type, handler) {
    target.addEventListener(type, handler);
    cleanups.push(() => target.removeEventListener(type, handler));
  }

  async function send(kind) {
    try {
      const response = await fetchImpl(INTERACT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: config.token, kind }),
      });
      if (!response.ok) console.warn(`Cyrene refused the ${kind} interaction (HTTP ${response.status})`);
    } catch (error) {
      console.warn(`The ${kind} interaction did not reach Cyrene`, error);
    }
  }

  // --- Idle: bored after a while without activity, a greeting when the owner comes back ----------------

  let lastActivityMs = now();
  let phase = "active";

  function setPhase(next) {
    if (next === phase) return;
    phase = next;
    renderer.setIdlePhase(next);
  }

  /** Activity resets the idle clock; `greet`: it shows Master coming back, after welcomeBackMs away. */
  function noteActivity({ greet }) {
    const timeMs = now();
    if (greet && timeMs - lastActivityMs >= welcomeBackMs) send("welcome_back");
    lastActivityMs = timeMs;
    setPhase("active");
  }

  if (allowed("idle")) {
    const timer = setInterval(() => setPhase(idlePhase((now() - lastActivityMs) / 1000)), IDLE_CHECK_MS);
    cleanups.push(() => clearInterval(timer));
  }

  // --- Reactions -------------------------------------------------------------------------------------

  // A click waits DOUBLE_CLICK_MS (and through a second press) before it is sent: as the first half of a
  // double-click it is dropped, so a double-click only makes her listen.
  let heldClick = null;
  let lastHeadPatMs = -Infinity;
  const holdClicks = allowed(SWITCHES.double_click);

  function releaseHeldClick() {
    if (heldClick === null) return;
    clearTimeout(heldClick.timer);
    const { kind } = heldClick;
    heldClick = null;
    send(kind);
  }

  function dropHeldClick() {
    if (heldClick !== null) clearTimeout(heldClick.timer);
    heldClick = null;
  }

  function react(kind, { hold = false } = {}) {
    if (!allowed(SWITCHES[kind])) return;
    renderer.setEmotion(FEEDBACK_EMOTIONS[kind], FEEDBACK_HOLD_S);
    if (kind === "head_pat") lastHeadPatMs = now();
    if (hold) heldClick = { kind, timer: setTimeout(releaseHeldClick, DOUBLE_CLICK_MS) };
    else send(kind);
  }

  // --- Pointer gestures ------------------------------------------------------------------------------

  // The samples of the last press while it may be the first half of a double-click, and of the current one.
  let previousSamples = [];
  let press = null;
  // classifyPointer replays every sample on each call; hitTest may cast a ray, so ask once per point.
  let headAt = new Map();

  function inHead(x, y) {
    const key = `${x},${y}`;
    if (!headAt.has(key)) headAt.set(key, renderer.hitTest(x, y) === "head");
    return headAt.get(key);
  }

  function sample(type, event) {
    return { t: now(), x: event.clientX, y: event.clientY, type };
  }

  function gesture() {
    return classifyPointer([...previousSamples, ...press.samples], inHead);
  }

  function follow(kind, event) {
    if (kind === "drag") {
      if (!press.dragging) {
        press.dragging = true;
        releaseHeldClick();
      }
      // Screen coordinates: the window moves under the pointer, so its client coordinates stand still.
      const dx = event.screenX - press.screen.x;
      const dy = event.screenY - press.screen.y;
      if (mascot && (dx !== 0 || dy !== 0)) bridge.dragBy(dx, dy);
      press.screen = { x: event.screenX, y: event.screenY };
    } else if (kind === "rub") {
      releaseHeldClick();
      if (now() - lastHeadPatMs >= RUB_PAT_GAP_MS) react("head_pat");
    }
  }

  function onPointerDown(event) {
    if (event.button !== PRIMARY_BUTTON || event.isPrimary === false || press !== null) return;
    noteActivity({ greet: mascot });
    const target = renderer.hitTest(event.clientX, event.clientY);
    if (target === null) {
      releaseHeldClick();
      previousSamples = [];
      return;
    }
    if (heldClick !== null) clearTimeout(heldClick.timer);
    const down = sample("down", event);
    if (previousSamples.length === 0 || down.t - previousSamples[0].t > DOUBLE_CLICK_MS) {
      previousSamples = [];
      headAt = new Map();
    }
    press = { target, samples: [down], screen: { x: event.screenX, y: event.screenY }, dragging: false };
    canvas.setPointerCapture?.(event.pointerId);
  }

  function onPointerMove(event) {
    if (!mascot) {
      if (allowed("cursor_follow")) renderer.lookAt({ x: event.clientX, y: event.clientY });
      noteActivity({ greet: false });
    }
    if (press === null) return;
    press.samples.push(sample("move", event));
    follow(gesture(), event);
  }

  function onPointerUp(event) {
    if (press === null) return;
    press.samples.push(sample("up", event));
    const kind = gesture();
    if (kind === "click" || (kind === "double_click" && !holdClicks)) {
      releaseHeldClick();
      react(press.target === "head" ? "head_pat" : "body_touch", { hold: holdClicks });
    } else if (kind === "double_click") {
      dropHeldClick();
      send("double_click");
    } else {
      follow(kind, event);
    }
    previousSamples = kind === "click" ? press.samples : [];
    press = null;
  }

  function onPointerCancel() {
    if (press === null) return;
    releaseHeldClick();
    previousSamples = [];
    press = null;
  }

  listen(canvas, "pointerdown", onPointerDown);
  listen(canvas, "pointermove", onPointerMove);
  listen(canvas, "pointerup", onPointerUp);
  listen(canvas, "pointercancel", onPointerCancel);
  if (!mascot && allowed("cursor_follow")) listen(canvas, "pointerleave", () => renderer.lookAt(null));
  cleanups.push(dropHeldClick);

  // --- The mascot's cursor feed: look, activity, click-through ---------------------------------------

  if (mascot) {
    let cursor = null;
    let clickThrough = null;
    let lastCheckMs = -Infinity;
    let checkTimer = null;

    const checkClickThrough = () => {
      clearTimeout(checkTimer);
      checkTimer = null;
      lastCheckMs = now();
      const inside = cursor.x >= 0 && cursor.y >= 0 && cursor.x < canvas.clientWidth && cursor.y < canvas.clientHeight;
      // Outside the window there is nothing to decide; during a press she must keep the pointer.
      if (!inside || press !== null) return;
      const element = elementAt(cursor.x, cursor.y);
      const overPage = element !== null && element !== canvas;
      const through = !overPage && !renderer.isOpaqueAt(cursor.x, cursor.y);
      if (through === clickThrough) return;
      clickThrough = through;
      bridge.setClickThrough(through);
    };

    const onCursor = (point) => {
      const moved = cursor !== null && (point.x !== cursor.x || point.y !== cursor.y);
      cursor = { x: point.x, y: point.y };
      if (allowed("cursor_follow")) renderer.lookAt({ ...cursor });
      if (point.resumed === true) noteActivity({ greet: false });
      else if (moved) noteActivity({ greet: true });
      // Throttled, but the last position is always checked, so she never stays click-through under the cursor.
      const waitMs = lastCheckMs + CLICK_THROUGH_GAP_MS - now();
      if (waitMs <= 0) checkClickThrough();
      else if (checkTimer === null) checkTimer = setTimeout(checkClickThrough, waitMs);
    };

    const unsubscribe = bridge.onCursor(onCursor);
    cleanups.push(() => {
      if (typeof unsubscribe === "function") unsubscribe();
      clearTimeout(checkTimer);
    });
  }

  return {
    handleEvent(event) {
      if (event?.type === "speak") noteActivity({ greet: false });
    },
    stop() {
      for (const cleanup of cleanups.splice(0)) cleanup();
    },
  };
}
