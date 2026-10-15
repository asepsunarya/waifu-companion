'use strict';

// Cyrene's desktop mascot: her avatar page in a transparent, frameless window.
// waifu.avatar.window starts it as
//   electron.exe <this folder> --url=http://127.0.0.1:<port>/?mode=mascot
//     --width=W --height=H --corner=C --always-on-top=1|0

const path = require('node:path');

const { app, BrowserWindow, ipcMain, screen } = require('electron');

const {
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
  isWebglEnabled,
  parseArgs,
  placementAfterDisplayChange,
  relativeCursor,
  reloadAllowed,
} = require('./lib');

// The page gets the cursor about 30 times a second, so she can follow it smoothly.
const CURSOR_POLL_MS = 33;
// Above normal windows, below menus and the taskbar's pop-ups; a fullscreen game still covers her.
const ALWAYS_ON_TOP_LEVEL = 'floating';
// How long after her renderer or the GPU process is gone her page is reloaded.
const RELOAD_DELAY_MS = 1000;
// How long after the last display change she is put on top once more (keepOnDisplays).
const SETTLE_MS = 1500;

// Cyrene reads the mascot's stdout and stderr through pipes (waifu.avatar.window). Once she
// stops reading, every write fails with EPIPE, which Node emits as an 'error' event: unhandled,
// it would end the mascot in Electron's error dialog. Those lines have nowhere to go.
for (const stream of [process.stdout, process.stderr]) {
  stream.on('error', () => {});
}

// Before ready: Chromium must keep painting her whatever it believes covers her. A fullscreen
// game's display-mode switch can make native occlusion tracking (Windows) think every monitor
// is covered; background throttling (off in her webPreferences) would stop her page as well.
// After a GPU reset or crash, Chromium also refuses WebGL to the domain of a page that had a
// context then (Cyrene's 127.0.0.1) for a while, so the reload meant to bring her back would
// come back without her: that per-domain blocking is turned off.
app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion');
app.disableDomainBlockingFor3DAPIs();

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  let options;
  try {
    options = parseArgs(process.argv);
  } catch (error) {
    console.error(`Cyrene mascot: ${error.message}`);
    app.exit(2);
  }
  if (options) {
    app.on('window-all-closed', () => app.quit());
    app.whenReady().then(() => openMascot(options));
  }
}

/** Writes `Cyrene mascot: <event> <details>` on stdout, which Cyrene logs. */
function log(event, details) {
  console.log(diagnosticLine(event, details));
}

function openMascot({ url, width, height, corner, alwaysOnTop }) {
  const { x, y } = cornerPosition(screen.getPrimaryDisplay().workArea, width, height, corner);
  const mascot = new BrowserWindow({
    width,
    height,
    x,
    y,
    transparent: true,
    frame: false,
    resizable: false,
    hasShadow: false,
    alwaysOnTop,
    skipTaskbar: false,
    backgroundColor: '#00000000',
    // Not just "Cyrene": that is the console window that stops her when closed.
    title: 'Avatar Cyrene',
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      backgroundThrottling: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  });
  if (alwaysOnTop) {
    mascot.setAlwaysOnTop(true, ALWAYS_ON_TOP_LEVEL);
  }
  // The page is Cyrene's own loopback server: nothing may lead the window elsewhere.
  const origin = new URL(url).origin;
  mascot.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  mascot.webContents.on('will-navigate', (event) => {
    if (!URL.canParse(event.url) || new URL(event.url).origin !== origin) {
      event.preventDefault();
    }
  });
  const stopCursorFeed = feedCursor(mascot);
  const stopInput = acceptInput(mascot, { width, height });
  const stopReloads = reloadWhenGone(mascot);
  const stopDisplays = keepOnDisplays(mascot, { width, height, corner, alwaysOnTop });
  mascot.on('unresponsive', () => log('unresponsive'));
  mascot.on('responsive', () => log('responsive'));
  mascot.on('closed', () => {
    stopCursorFeed();
    stopInput();
    stopReloads();
    stopDisplays();
    app.quit();
  });
  mascot.loadURL(url).catch((error) => {
    console.error(`Cyrene mascot: cannot load ${url}: ${error.message}`);
  });
}

// Sends ('cursor', {x, y}), the cursor relative to the window's content (it may
// be outside), whenever it changed while she can be seen; the first one after
// she was minimized or hidden also has resumed: true (lib.cursorReport).
// Returns the stopper.
function feedCursor(mascot) {
  const page = mascot.webContents;
  let feed = NEW_CURSOR_FEED;
  // A (re)loaded page has a new preload that has not seen the cursor yet.
  page.on('dom-ready', () => {
    feed = NEW_CURSOR_FEED;
  });
  const timer = setInterval(() => {
    if (mascot.isDestroyed()) {
      return;
    }
    const showing = mascot.isVisible() && !mascot.isMinimized();
    const cursor = showing
      ? relativeCursor(screen.getCursorScreenPoint(), mascot.getContentBounds())
      : null;
    const step = cursorReport(feed, cursor, showing);
    feed = step.feed;
    if (step.report !== null) {
      page.send('cursor', step.report);
    }
  }, CURSOR_POLL_MS);
  return () => clearInterval(timer);
}

// Applies the page's 'drag-by' and 'click-through' messages and logs its 'diagnostic' ones
// (see preload.js); messages from any other web contents are ignored. Returns the stopper.
function acceptInput(mascot, size) {
  const page = mascot.webContents;
  const fromMascot = (event) => event.sender === page && !mascot.isDestroyed();
  let rest;
  const dragBy = (event, dx, dy) => {
    if (!fromMascot(event) || !isDragDelta(dx, dy)) {
      return;
    }
    // Her own size, not the current one: moving her must never resize her.
    const step = dragStep({ ...mascot.getBounds(), ...size }, dx, dy, rest);
    rest = step.rest;
    const { workArea } = screen.getDisplayMatching(step.bounds);
    mascot.setBounds(clampToWorkArea(step.bounds, workArea));
  };
  const clickThrough = (event, flag) => {
    if (fromMascot(event)) {
      mascot.setIgnoreMouseEvents(Boolean(flag), { forward: true });
    }
  };
  const diagnostic = (event, kind) => {
    if (fromMascot(event) && isDiagnosticKind(kind)) {
      log(kind);
    }
  };
  ipcMain.on('drag-by', dragBy);
  ipcMain.on('click-through', clickThrough);
  ipcMain.on('diagnostic', diagnostic);
  return () => {
    ipcMain.removeListener('drag-by', dragBy);
    ipcMain.removeListener('click-through', clickThrough);
    ipcMain.removeListener('diagnostic', diagnostic);
  };
}

// Once her page's renderer process or Electron's GPU process is gone, the transparent window
// shows nothing: her page is reloaded RELOAD_DELAY_MS later, at most 5 times in 10 minutes
// (lib.reloadAllowed); after that it is only logged. A page whose renderer got no WebGL reports
// 'webgl-unavailable': it is reloaded the same way while Electron still offers WebGL
// (lib.isWebglEnabled). When Electron has none left (it gave up on the GPU after too many GPU
// process crashes), no reload can bring her back: the mascot exits with code 1, and Cyrene opens
// a fresh one (waifu.avatar.window). Returns the stopper.
function reloadWhenGone(mascot) {
  const page = mascot.webContents;
  let reloads = [];
  let timer = null;
  const reloadSoon = () => {
    if (timer !== null || mascot.isDestroyed()) {
      return;
    }
    const step = reloadAllowed(reloads, performance.now());
    reloads = step.history;
    if (!step.allowed) {
      log('page-reload', 'skipped: too many in the last 10 minutes');
      return;
    }
    log('page-reload', `in ${RELOAD_DELAY_MS / 1000} s`);
    timer = setTimeout(() => {
      timer = null;
      if (!mascot.isDestroyed()) {
        page.reload();
      }
    }, RELOAD_DELAY_MS);
  };
  const rendererGone = (_event, details) => {
    log('render-process-gone', goneDetails(details));
    reloadSoon();
  };
  const childGone = (_event, details) => {
    if (details.type === 'GPU') {
      log('gpu-process-gone', goneDetails(details));
      reloadSoon();
    }
  };
  const webglUnavailable = (event, kind) => {
    if (event.sender !== page || kind !== 'webgl-unavailable' || mascot.isDestroyed()) {
      return;
    }
    const { webgl } = app.getGPUFeatureStatus();
    if (isWebglEnabled(webgl)) {
      reloadSoon();
      return;
    }
    log(
      'exiting',
      `webgl=${webgl}: WebGL is gone for all of this Electron, so Cyrene opens a new one`,
    );
    app.exit(1);
  };
  page.on('render-process-gone', rendererGone);
  app.on('child-process-gone', childGone);
  ipcMain.on('diagnostic', webglUnavailable);
  return () => {
    clearTimeout(timer);
    app.removeListener('child-process-gone', childGone);
    ipcMain.removeListener('diagnostic', webglUnavailable);
  };
}

// A fullscreen game switching its monitor's resolution moves windows and may take her off the
// top. After every display change she goes to her corner of the primary display when no display
// shows her any more, and back to the spot she was taken from once a display shows it again
// while nobody moved her (lib.placementAfterDisplayChange). She is put on top again at the same
// level (a fullscreen game still covers her) at once, again SETTLE_MS after the last change, and
// whenever her window is shown or restored: on Windows that level puts her right behind the
// taskbar, which keeps her on top only while the taskbar is, and Explorer takes the taskbar off
// the top while a fullscreen game runs on the primary display. Returns the stopper.
function keepOnDisplays(mascot, { width, height, corner, alwaysOnTop }) {
  const size = { width, height };
  let placed = null;
  let settled = null;
  const pin = () => {
    if (alwaysOnTop && !mascot.isDestroyed()) {
      mascot.setAlwaysOnTop(true, ALWAYS_ON_TOP_LEVEL);
      return true;
    }
    return false;
  };
  const repin = (reason) => {
    if (pin()) {
      log('re-pinned', `after ${reason}`);
    }
  };
  const settle = () => {
    if (mascot.isDestroyed()) {
      return;
    }
    const step = placementAfterDisplayChange(
      { ...mascot.getBounds(), ...size },
      screen.getAllDisplays().map((display) => display.workArea),
      screen.getPrimaryDisplay().workArea,
      size,
      corner,
      placed,
    );
    placed = step.placed;
    if (step.bounds !== null) {
      const { x, y } = step.bounds;
      mascot.setBounds(step.bounds);
      log(
        'placed-back',
        placed === null
          ? `where she was before, at ${x},${y}`
          : `${corner} of the primary display at ${x},${y}`,
      );
    }
    pin();
    clearTimeout(settled);
    settled = setTimeout(() => repin('the displays settled'), SETTLE_MS);
  };
  const metricsChanged = (_event, display, changedMetrics) => {
    const changed = changedMetrics.join(',');
    log('display-metrics-changed', `${describeDisplay(display)} changed=${changed}`);
    settle();
  };
  const added = (_event, display) => {
    log('display-added', describeDisplay(display));
    settle();
  };
  const removed = (_event, display) => {
    log('display-removed', describeDisplay(display));
    settle();
  };
  screen.on('display-metrics-changed', metricsChanged);
  screen.on('display-added', added);
  screen.on('display-removed', removed);
  mascot.on('show', () => repin('show'));
  mascot.on('restore', () => repin('restore'));
  return () => {
    clearTimeout(settled);
    screen.removeListener('display-metrics-changed', metricsChanged);
    screen.removeListener('display-added', added);
    screen.removeListener('display-removed', removed);
  };
}
