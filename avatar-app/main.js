'use strict';

// Cyrene's desktop mascot: her avatar page in a transparent, frameless window.
// waifu.avatar.window starts it as
//   electron.exe <this folder> --url=http://127.0.0.1:<port>/?mode=mascot
//     --width=W --height=H --corner=C --always-on-top=1|0

const { app, BrowserWindow, screen } = require('electron');

const { cornerPosition, parseArgs } = require('./lib');

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
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false },
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
  mascot.on('closed', () => app.quit());
  mascot.loadURL(url).catch((error) => {
    console.error(`Cyrene mascot: cannot load ${url}: ${error.message}`);
  });
}
