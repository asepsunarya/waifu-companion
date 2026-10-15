'use strict';

// Pure helpers for main.js: no electron import, so `node --test` runs them directly.

const CORNERS = ['bottom-right', 'bottom-left', 'top-right', 'top-left'];
const DEFAULT_CORNER = 'bottom-right';
// The same limits as avatar.width / avatar.height in Cyrene's config.
const WIDTH = { fallback: 360, min: 120, max: 2000 };
const HEIGHT = { fallback: 640, min: 160, max: 2000 };
// Any IPv4 loopback address (127.0.0.0/8), the rule avatar.host follows in Cyrene's config.
// URL has already turned the host into a canonical dotted quad, so the digits are valid.
const IPV4_LOOPBACK = /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/;
// A single drag step larger than this is not a drag (preload.js checks the same limit).
const MAX_DRAG_STEP = 2000;
// How much of the window, on each axis, a drag keeps inside a work area (clampToWorkArea,
// isOffScreen).
const KEEP_INSIDE_PX = 48;
// After her renderer or the GPU process is gone, main.js reloads her page at most this often.
const RELOAD_LIMIT = 5;
const RELOAD_WINDOW_MS = 10 * 60 * 1000;
// What the page may report through the preload's diagnostic(kind) (preload.js repeats the
// list): nothing the page writes itself ever reaches Cyrene's log.
const DIAGNOSTIC_KINDS = Object.freeze([
  'page-visible',
  'page-hidden',
  'webgl-context-lost',
  'webgl-context-restored',
  'webgl-unavailable',
  'page-reload',
]);
// Cyrene logs the mascot's output lines that start with this (waifu.avatar.window).
const DIAGNOSTIC_PREFIX = 'Cyrene mascot:';

/**
 * Read the mascot options from process.argv: `--url=` (required), `--width=`,
 * `--height=`, `--corner=`, `--always-on-top=1|0`. Only the `--key=value` form
 * counts; anything else (the electron path, chromium switches) is ignored.
 * Throws when the url is not Cyrene's loopback page server.
 */
function parseArgs(argv) {
  const values = new Map();
  for (const arg of argv) {
    const match = /^--([a-z-]+)=(.*)$/s.exec(arg);
    if (match) {
      values.set(match[1], match[2]);
    }
  }
  return {
    url: loopbackUrl(values.get('url')),
    width: clampedInt(values.get('width'), WIDTH),
    height: clampedInt(values.get('height'), HEIGHT),
    corner: CORNERS.includes(values.get('corner')) ? values.get('corner') : DEFAULT_CORNER,
    alwaysOnTop: values.get('always-on-top') !== '0',
  };
}

/**
 * The top-left pixel of a width x height window in `corner` of `workArea`
 * ({x, y, width, height}), `margin` px from its edges. The window never leaves
 * the work area: the margin shrinks first, and a window larger than the area
 * is pinned to its top-left corner.
 */
function cornerPosition(workArea, width, height, corner, margin = 16) {
  const side = CORNERS.includes(corner) ? corner : DEFAULT_CORNER;
  const [vertical, horizontal] = side.split('-');
  return {
    x: placeAlong(workArea.x, workArea.width, width, horizontal === 'left', margin),
    y: placeAlong(workArea.y, workArea.height, height, vertical === 'top', margin),
  };
}

function placeAlong(start, span, size, atStart, margin) {
  const wanted = atStart ? start + margin : start + span - size - margin;
  const lowest = Math.ceil(start);
  const highest = Math.max(lowest, Math.floor(start + span - size));
  return Math.min(Math.max(Math.round(wanted), lowest), highest);
}

/**
 * The screen `point` ({x, y}) relative to the top-left corner of `bounds`
 * ({x, y, width, height}); a cursor outside the window gives values outside it.
 */
function relativeCursor(point, bounds) {
  return { x: point.x - bounds.x, y: point.y - bounds.y };
}

/** The state of a cursor feed that has reported nothing yet (see cursorReport). */
const NEW_CURSOR_FEED = Object.freeze({ last: null, paused: false });

/**
 * One poll of the mascot's cursor feed. `feed` is its state ({last, paused}: the
 * last cursor reported, and whether the window was minimized or hidden since),
 * `cursor` the cursor relative to the window, `showing` whether the window can
 * be seen (`cursor` is not read when it cannot). Returns {feed, report}: the
 * next state and the message for the page, or null. A cursor is reported when it
 * moved; nothing while the window cannot be seen. The first report after such a
 * pause carries `resumed: true`, even when the cursor stands where it was: the
 * page missed everything meanwhile, so the cursor's new place is no move.
 */
function cursorReport(feed, cursor, showing) {
  if (!showing) {
    return { feed: { last: feed.last, paused: feed.last !== null }, report: null };
  }
  const moved = feed.last === null || cursor.x !== feed.last.x || cursor.y !== feed.last.y;
  if (!moved && !feed.paused) {
    return { feed, report: null };
  }
  const point = { x: cursor.x, y: cursor.y };
  return {
    feed: { last: point, paused: false },
    report: feed.paused ? { ...point, resumed: true } : point,
  };
}

/**
 * `bounds` ({x, y, width, height}) moved the least so that at least `keep` px
 * of the window, on each axis, lie inside `workArea` (all of it when the window
 * or the work area is smaller): a dragged mascot can never be lost off-screen.
 * The size never changes; the position is in whole pixels.
 */
function clampToWorkArea(bounds, workArea, keep = KEEP_INSIDE_PX) {
  return {
    x: keepInside(bounds.x, bounds.width, workArea.x, workArea.width, keep),
    y: keepInside(bounds.y, bounds.height, workArea.y, workArea.height, keep),
    width: bounds.width,
    height: bounds.height,
  };
}

function keepInside(position, size, start, span, keep) {
  const inside = Math.min(keep, size, span);
  const lowest = Math.ceil(start + inside - size);
  const highest = Math.max(lowest, Math.floor(start + span - inside));
  return wholePixel(Math.min(Math.max(position, lowest), highest));
}

/**
 * One drag step of (dx, dy) px for a window at `bounds`: the new bounds in whole
 * pixels and the sub-pixel `rest` ({x, y}) to pass to the next step, so slow
 * drags on a scaled display add up instead of being rounded away or overshot.
 * Returns {bounds, rest}; the size never changes.
 */
function dragStep(bounds, dx, dy, rest = { x: 0, y: 0 }) {
  const x = bounds.x + dx + rest.x;
  const y = bounds.y + dy + rest.y;
  const moved = { ...bounds, x: wholePixel(x), y: wholePixel(y) };
  return { bounds: moved, rest: { x: x - moved.x, y: y - moved.y } };
}

// Math.round and Math.ceil can give -0; window positions are plain whole numbers.
function wholePixel(value) {
  return Math.round(value) + 0;
}

/** Whether (dx, dy) is a drag step main.js may apply: finite numbers of at most 2000 px. */
function isDragDelta(dx, dy) {
  return [dx, dy].every(
    (value) =>
      typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= MAX_DRAG_STEP,
  );
}

/**
 * Whether no work area in `workAreas` holds the window at `bounds` the way a drag keeps it
 * (clampToWorkArea): 48 px of it on each axis inside, all of it when it or the work area is
 * smaller. A fullscreen game switching its monitor's resolution, or a display unplugged, can
 * leave her like that.
 */
function isOffScreen(bounds, workAreas) {
  const holds = (area) =>
    overlap(bounds.x, bounds.width, area.x, area.width) >=
      Math.min(KEEP_INSIDE_PX, bounds.width, area.width) &&
    overlap(bounds.y, bounds.height, area.y, area.height) >=
      Math.min(KEEP_INSIDE_PX, bounds.height, area.height);
  return !workAreas.some(holds);
}

function overlap(start, size, areaStart, areaSize) {
  return Math.min(start + size, areaStart + areaSize) - Math.max(start, areaStart);
}

/**
 * Where the mascot at `bounds` goes once the displays changed, given `workAreas` (every display's)
 * and `placed`, what the last call remembered, or null. Her home is her own `size`
 * ({width, height}) in `corner` of `primaryWorkArea`, where she started (cornerPosition).
 * - While she still stands where she was put (`placed.at`: nobody moved her since) and the spot
 *   she was taken from (`placed.from`) is on a display again (isOffScreen), she goes back there:
 *   a fullscreen game gave her display its resolution back, or a sleeping monitor woke up.
 * - While she stands where she was put and that spot is still off-screen, she stays at home,
 *   which follows the primary work area.
 * - Otherwise she stays where she is while a display shows her, else she goes home and the spot
 *   she was taken from is remembered.
 * Returns {bounds, placed}: her new bounds, or null to leave her where she is, and what to pass
 * as `placed` next time ({from, at} or null).
 */
function placementAfterDisplayChange(
  bounds,
  workAreas,
  primaryWorkArea,
  size,
  corner,
  placed = null,
) {
  const { x, y } = cornerPosition(primaryWorkArea, size.width, size.height, corner);
  const home = { x, y, width: size.width, height: size.height };
  if (placed !== null && samePlace(bounds, placed.at)) {
    if (!isOffScreen(placed.from, workAreas)) {
      return { bounds: placed.from, placed: null };
    }
    if (samePlace(bounds, home)) {
      return { bounds: null, placed };
    }
    return { bounds: home, placed: { from: placed.from, at: home } };
  }
  if (!isOffScreen(bounds, workAreas)) {
    return { bounds: null, placed: null };
  }
  return { bounds: home, placed: { from: { ...bounds, ...size }, at: home } };
}

function samePlace(bounds, other) {
  return bounds.x === other.x && bounds.y === other.y;
}

/**
 * Whether `status`, the WebGL status of Electron's app.getGPUFeatureStatus() (chrome://gpu), says
 * pages can have WebGL: 'enabled' or one of its variants ('enabled_on', 'enabled_readback',
 * 'enabled_force', 'enabled_force_on'). 'disabled_*' and 'unavailable_*' (say, after Chromium gave
 * up on the GPU when its process crashed too often) mean no page of this Electron gets it again.
 */
function isWebglEnabled(status) {
  return typeof status === 'string' && status.startsWith('enabled');
}

/**
 * Whether the mascot may reload her page at `now` (ms on a monotonic clock) after her renderer or
 * the GPU process was gone, given `history`, the times of the reloads before it: at most 5 in any
 * 10 minutes, so a page that crashes on every load is not reloaded forever. Returns
 * {allowed, history}: the answer and the history to keep, the reloads still inside those 10
 * minutes plus `now` when it is allowed.
 */
function reloadAllowed(history, now) {
  const recent = history.filter((time) => now - time < RELOAD_WINDOW_MS);
  const allowed = recent.length < RELOAD_LIMIT;
  return { allowed, history: allowed ? [...recent, now] : recent };
}

/** Whether `kind` is one the page may report through the preload's diagnostic(kind). */
function isDiagnosticKind(kind) {
  return DIAGNOSTIC_KINDS.includes(kind);
}

/** A line for Cyrene's log, which reads the mascot's stdout: `Cyrene mascot: <event> <details>`. */
function diagnosticLine(event, details = '') {
  return details ? `${DIAGNOSTIC_PREFIX} ${event} ${details}` : `${DIAGNOSTIC_PREFIX} ${event}`;
}

/** The details of a process that is gone (Electron's render-process-gone / child-process-gone). */
function goneDetails({ reason, exitCode }) {
  return `reason=${reason} exit-code=${exitCode}`;
}

/** A display (Electron's Display) for the log: its id, bounds, work area and scale factor. */
function describeDisplay({ id, bounds, workArea, scaleFactor }) {
  const area = rectangle(workArea);
  return `id=${id} bounds=${rectangle(bounds)} work-area=${area} scale=${scaleFactor}`;
}

function rectangle({ x, y, width, height }) {
  return `${x},${y} ${width}x${height}`;
}

function loopbackUrl(text) {
  if (!text) {
    throw new Error('--url=http://127.0.0.1:<port>/ is required');
  }
  let url;
  try {
    url = new URL(text);
  } catch {
    url = null;
  }
  if (!url || url.protocol !== 'http:' || !IPV4_LOOPBACK.test(url.hostname) || !url.port) {
    throw new Error(
      `--url must be http://127.0.0.1:<port>/... (or another 127.x.y.z address), not ${JSON.stringify(text)}`,
    );
  }
  return url.href;
}

function clampedInt(text, { fallback, min, max }) {
  if (!/^-?\d+$/.test(text ?? '')) {
    return fallback;
  }
  return Math.min(Math.max(Number(text), min), max);
}

module.exports = {
  DIAGNOSTIC_KINDS,
  NEW_CURSOR_FEED,
  clampToWorkArea,
  cornerPosition,
  cursorReport,
  describeDisplay,
  diagnosticLine,
  dragStep,
  goneDetails,
  isDiagnosticKind,
  isDragDelta,
  isOffScreen,
  isWebglEnabled,
  parseArgs,
  placementAfterDisplayChange,
  relativeCursor,
  reloadAllowed,
};
