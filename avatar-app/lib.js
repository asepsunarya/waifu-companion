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
function clampToWorkArea(bounds, workArea, keep = 48) {
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
  NEW_CURSOR_FEED,
  clampToWorkArea,
  cornerPosition,
  cursorReport,
  dragStep,
  isDragDelta,
  parseArgs,
  relativeCursor,
};
