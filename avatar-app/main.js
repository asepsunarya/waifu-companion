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
  dragStep,
  isDragDelta,
  parseArgs,
  relativeCursor,
} = require('./lib');

// The page gets the cursor about 30 times a second, so she can follow it smoothly.
const CURSOR_POLL_MS = 33;

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
      preload: path.join(__dirname, 'preload.js'),
    },
  });
  if (alwaysOnTop) {
    // Above normal windows, below menus and the taskbar's pop-ups.
    mascot.setAlwaysOnTop(true, 'floating');
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
  mascot.on('closed', () => {
    stopCursorFeed();
    stopInput();
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

// Applies the page's 'drag-by' and 'click-through' messages (see preload.js);
// messages from any other web contents are ignored. Returns the stopper.
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
  ipcMain.on('drag-by', dragBy);
  ipcMain.on('click-through', clickThrough);
  return () => {
    ipcMain.removeListener('drag-by', dragBy);
    ipcMain.removeListener('click-through', clickThrough);
  };
}
