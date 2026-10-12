'use strict';

// Gives Cyrene's mascot page window.cyrene and nothing else: never ipcRenderer
// itself. It runs sandboxed, where require() only knows 'electron', 'events',
// 'timers' and 'url', so lib.js cannot be loaded here and the drag limit below
// repeats lib.isDragDelta (main.js checks every message again).

const { contextBridge, ipcRenderer } = require('electron');

const MAX_DRAG_STEP = 2000;

// main.js sends the cursor only when it moves: remember the last position so a
// page that subscribes later can still look at a cursor that is standing still.
let lastCursor = null;
ipcRenderer.on('cursor', (_event, cursor) => {
  lastCursor = { x: cursor.x, y: cursor.y };
});

function isDragStep(value) {
  return typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= MAX_DRAG_STEP;
}

contextBridge.exposeInMainWorld('cyrene', {
  /**
   * Calls `callback({x, y, resumed})` with the mouse cursor relative to the
   * window's top-left corner (outside the window too): at once when it is known,
   * then on every move. `resumed` is true on the first report after the window
   * was minimized or hidden, when nothing was reported. Returns the function
   * that stops it.
   */
  onCursor(callback) {
    if (typeof callback !== 'function') {
      throw new TypeError('cyrene.onCursor(callback) needs a function');
    }
    const listener = (_event, cursor) =>
      callback({ x: cursor.x, y: cursor.y, resumed: cursor.resumed === true });
    ipcRenderer.on('cursor', listener);
    if (lastCursor) {
      callback({ ...lastCursor, resumed: false });
    }
    return () => {
      ipcRenderer.removeListener('cursor', listener);
    };
  },

  /** Moves the window by (dx, dy) px; anything but finite numbers up to 2000 is ignored. */
  dragBy(dx, dy) {
    if (isDragStep(dx) && isDragStep(dy)) {
      ipcRenderer.send('drag-by', dx, dy);
    }
  },

  /**
   * true: clicks pass through the window to the desktop (pointer moves still
   * reach the page); false: clicks reach the page again. Only booleans are sent.
   */
  setClickThrough(flag) {
    if (typeof flag === 'boolean') {
      ipcRenderer.send('click-through', flag);
    }
  },
});
