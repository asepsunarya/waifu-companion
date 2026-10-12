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

module.exports = { cornerPosition, parseArgs };
