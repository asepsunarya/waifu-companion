'use strict';

// main.js runs here in a plain VM context, as preload.test.js runs the preload: its
// require('electron') gives a fake app, BrowserWindow, ipcMain and screen, and its timers run on a
// fake clock. So what the mascot asks of Electron (switches before ready, recovery, her window
// through display changes) is checked without Electron.

const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { describe, it } = require('node:test');
const vm = require('node:vm');

const MAIN = readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');
const URL_ARG = '--url=http://127.0.0.1:9882/?mode=mascot';
// How long after the last display change she is put on top once more.
const SETTLE_MS = 1500;

/** A display as Electron's screen gives it. */
function display(id, x, y, width, height, workHeight = height) {
  return {
    id,
    bounds: { x, y, width, height },
    workArea: { x, y, width, height: workHeight },
    scaleFactor: 1,
  };
}

// The owner's desk: a 2560x1440 primary display with its taskbar, a 1920x1080 one on its left.
const PRIMARY = display(1, 0, 0, 2560, 1440, 1392);
const LEFT = display(2, -1920, 0, 1920, 1080, 1032);

/** setTimeout and setInterval on a clock that moves only in advance(). */
class FakeClock {
  constructor() {
    this.now = 0;
    this.timers = new Map();
    this.nextId = 1;
    this.setTimeout = (callback, ms) => this.add(callback, ms, null);
    this.setInterval = (callback, ms) => this.add(callback, ms, ms);
    this.clear = (id) => this.timers.delete(id);
  }

  add(callback, ms, every) {
    const id = this.nextId;
    this.nextId += 1;
    this.timers.set(id, { at: this.now + ms, callback, every });
    return id;
  }

  /** Moves the clock `ms` ahead, running every timer that falls due on the way, in order. */
  advance(ms) {
    const end = this.now + ms;
    for (;;) {
      const due = [...this.timers].filter(([, timer]) => timer.at <= end);
      if (due.length === 0) break;
      const [id, timer] = due.reduce((first, next) => (next[1].at < first[1].at ? next : first));
      this.now = timer.at;
      if (timer.every === null) this.timers.delete(id);
      else timer.at += timer.every;
      timer.callback();
    }
    this.now = end;
  }
}

class FakeWebContents extends EventEmitter {
  constructor() {
    super();
    this.reloads = 0;
  }

  setWindowOpenHandler() {}

  send() {}

  reload() {
    this.reloads += 1;
  }
}

/** Her window: where it is, and every setAlwaysOnTop main.js made. */
class FakeWindow extends EventEmitter {
  constructor(options) {
    super();
    this.options = options;
    this.bounds = { x: options.x, y: options.y, width: options.width, height: options.height };
    this.pins = [];
    this.destroyed = false;
    this.webContents = new FakeWebContents();
  }

  setAlwaysOnTop(flag, level) {
    this.pins.push([flag, level]);
  }

  isDestroyed() {
    return this.destroyed;
  }

  isVisible() {
    return true;
  }

  isMinimized() {
    return false;
  }

  getBounds() {
    return { ...this.bounds };
  }

  getContentBounds() {
    return { ...this.bounds };
  }

  setBounds(bounds) {
    this.bounds = { ...bounds };
  }

  setIgnoreMouseEvents() {}

  loadURL() {
    return Promise.resolve();
  }

  /** The owner closes her: Electron destroys the window, then emits 'closed'. */
  close() {
    this.destroyed = true;
    this.emit('closed');
  }
}

/**
 * Runs main.js with `args` after the electron path. Returns the fake world: `calls` (what it asked
 * of app, in order), `lines` (its stdout and stderr), `clock`, `screen`, `ipcMain`, `displays`
 * (what the screen reports; change it, then emit a display event), `webgl`
 * (app.getGPUFeatureStatus().webgl), and `open()`, which makes the app ready and resolves to her
 * window.
 */
function runMain(args = [URL_ARG]) {
  const clock = new FakeClock();
  const windows = [];
  let ready;
  const whenReady = new Promise((resolve) => {
    ready = resolve;
  });
  const world = {
    calls: [],
    lines: [],
    clock,
    displays: [PRIMARY, LEFT],
    webgl: 'enabled',
    async open() {
      ready();
      await new Promise((resolve) => setImmediate(resolve));
      assert.equal(windows.length, 1, 'main.js opens one window once ready');
      return windows[0];
    },
  };
  const appendSwitch = (...switchArgs) => world.calls.push(['appendSwitch', ...switchArgs]);
  const app = Object.assign(new EventEmitter(), {
    commandLine: { appendSwitch },
    disableDomainBlockingFor3DAPIs: () => world.calls.push(['disableDomainBlockingFor3DAPIs']),
    requestSingleInstanceLock: () => true,
    whenReady: () => {
      world.calls.push(['whenReady']);
      return whenReady;
    },
    getGPUFeatureStatus: () => ({ webgl: world.webgl }),
    quit: () => world.calls.push(['quit']),
    exit: (code) => world.calls.push(['exit', code]),
  });
  world.screen = Object.assign(new EventEmitter(), {
    getPrimaryDisplay: () => world.displays[0],
    getAllDisplays: () => world.displays,
    getCursorScreenPoint: () => ({ x: 0, y: 0 }),
    getDisplayMatching: () => world.displays[0],
  });
  world.ipcMain = new EventEmitter();
  class BrowserWindow extends FakeWindow {
    constructor(options) {
      super(options);
      windows.push(this);
    }
  }
  const electron = { app, BrowserWindow, ipcMain: world.ipcMain, screen: world.screen };
  const modules = { electron, 'node:path': path, './lib': require('../lib') };
  const write = (line) => world.lines.push(line);
  vm.runInNewContext(MAIN, {
    require: (name) => {
      if (!(name in modules)) throw new Error(`main.js requires ${name}, which this test lacks`);
      return modules[name];
    },
    __dirname: path.join(__dirname, '..'),
    process: {
      argv: ['electron.exe', 'avatar-app', ...args],
      stdout: new EventEmitter(),
      stderr: new EventEmitter(),
    },
    console: { log: write, error: write },
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clear,
    setInterval: clock.setInterval,
    clearInterval: clock.clear,
    performance: { now: () => clock.now },
    URL,
  });
  return world;
}

/** Electron's display-metrics-changed for `changed` (a display of `world.displays`). */
function metricsChanged(world, changed = world.displays[0]) {
  world.screen.emit('display-metrics-changed', {}, changed, ['bounds', 'workArea']);
}

/** Asserts main.js wrote `line`. */
function assertLogged(world, line) {
  assert.ok(world.lines.includes(line), `${line} not in:\n${world.lines.join('\n')}`);
}

/** The app.exit calls main.js made. */
function exits(world) {
  return world.calls.filter(([name]) => name === 'exit');
}

const FLOATING = [true, 'floating'];

describe('main.js before ready', () => {
  it("keeps her painted and turns off Chromium's per-domain WebGL blocking", () => {
    const { calls } = runMain();
    const beforeReady = calls.slice(0, calls.findIndex(([name]) => name === 'whenReady'));

    assert.deepEqual(beforeReady, [
      ['appendSwitch', 'disable-features', 'CalculateNativeWinOcclusion'],
      ['disableDomainBlockingFor3DAPIs'],
    ]);
  });
});

describe('main.js when her page has no WebGL', () => {
  function reportNoWebgl(world, mascot, sender = mascot.webContents) {
    world.ipcMain.emit('diagnostic', { sender }, 'webgl-unavailable');
  }

  it('exits with code 1 once Electron has no WebGL left: Cyrene opens a new mascot', async () => {
    const world = runMain();
    const mascot = await world.open();
    world.webgl = 'unavailable_software';

    reportNoWebgl(world, mascot);
    world.clock.advance(5000);

    assert.deepEqual(exits(world), [['exit', 1]]);
    assert.equal(mascot.webContents.reloads, 0);
    assertLogged(world, 'Cyrene mascot: webgl-unavailable');
    assertLogged(
      world,
      'Cyrene mascot: exiting webgl=unavailable_software: ' +
        'WebGL is gone for all of this Electron, so Cyrene opens a new one',
    );
  });

  it('reloads her page 1 s later while Electron still has WebGL', async () => {
    const world = runMain();
    const mascot = await world.open();

    reportNoWebgl(world, mascot);
    world.clock.advance(999);
    assert.equal(mascot.webContents.reloads, 0);
    world.clock.advance(1);

    assert.equal(mascot.webContents.reloads, 1);
    assert.deepEqual(exits(world), []);
  });

  it('keeps those reloads within the 5 in 10 minutes', async () => {
    const world = runMain();
    const mascot = await world.open();

    for (let report = 0; report < 7; report += 1) {
      reportNoWebgl(world, mascot);
      world.clock.advance(1000);
    }

    assert.equal(mascot.webContents.reloads, 5);
  });

  it('ignores the report from any other web contents', async () => {
    const world = runMain();
    const mascot = await world.open();
    world.webgl = 'disabled_off';

    reportNoWebgl(world, mascot, new FakeWebContents());
    world.clock.advance(5000);

    assert.deepEqual(exits(world), []);
    assert.equal(mascot.webContents.reloads, 0);
  });
});

describe('main.js through display changes', () => {
  it('puts her on top again at once, and once more when the displays settled', async () => {
    const world = runMain();
    const mascot = await world.open();
    const pinsAtStart = mascot.pins.length;

    metricsChanged(world);
    assert.deepEqual(mascot.pins.slice(pinsAtStart), [FLOATING]);
    world.clock.advance(SETTLE_MS - 1);
    assert.equal(mascot.pins.length, pinsAtStart + 1);
    world.clock.advance(1);

    assert.deepEqual(mascot.pins.slice(pinsAtStart), [FLOATING, FLOATING]);
    assertLogged(world, 'Cyrene mascot: re-pinned after the displays settled');
  });

  it('waits for the last of several display changes to put her on top once more', async () => {
    const world = runMain();
    const mascot = await world.open();
    const pinsAtStart = mascot.pins.length;

    metricsChanged(world);
    world.clock.advance(1000);
    world.screen.emit('display-added', {}, LEFT);
    world.clock.advance(1000);
    world.screen.emit('display-removed', {}, LEFT);
    world.clock.advance(SETTLE_MS - 1);
    assert.equal(mascot.pins.length, pinsAtStart + 3, 'once at each change, none settled yet');
    world.clock.advance(1);

    assert.equal(mascot.pins.length, pinsAtStart + 4);
  });

  it('stops once her window is closed', async () => {
    const world = runMain();
    const mascot = await world.open();
    metricsChanged(world);
    const pinsAtClose = mascot.pins.length;

    mascot.close();
    const leftScheduled = world.clock.timers.size;
    world.clock.advance(SETTLE_MS * 2);
    metricsChanged(world);
    world.clock.advance(SETTLE_MS * 2);

    assert.equal(leftScheduled, 0, 'no timer of hers runs on');
    assert.equal(mascot.pins.length, pinsAtClose);
  });

  it('puts her on top again whenever her window is shown or restored', async () => {
    const world = runMain();
    const mascot = await world.open();
    const pinsAtStart = mascot.pins.length;

    mascot.emit('show');
    mascot.emit('restore');

    assert.deepEqual(mascot.pins.slice(pinsAtStart), [FLOATING, FLOATING]);
    assertLogged(world, 'Cyrene mascot: re-pinned after show');
    assertLogged(world, 'Cyrene mascot: re-pinned after restore');
  });

  it('never puts her on top when always-on-top is off', async () => {
    const world = runMain([URL_ARG, '--always-on-top=0']);
    const mascot = await world.open();

    metricsChanged(world);
    world.clock.advance(SETTLE_MS);
    mascot.emit('show');
    mascot.emit('restore');

    assert.deepEqual(mascot.pins, []);
  });

  it('takes her back to her own spot once a fullscreen game gives the display back', async () => {
    const world = runMain();
    const mascot = await world.open();
    assert.deepEqual(mascot.getBounds(), { x: 2184, y: 736, width: 360, height: 640 });

    world.displays = [display(1, 0, 0, 1920, 1080), LEFT];
    metricsChanged(world);
    assert.deepEqual(mascot.getBounds(), { x: 1544, y: 424, width: 360, height: 640 });
    world.displays = [PRIMARY, LEFT];
    metricsChanged(world);

    assert.deepEqual(mascot.getBounds(), { x: 2184, y: 736, width: 360, height: 640 });
    assert.deepEqual(
      world.lines.filter((line) => line.startsWith('Cyrene mascot: placed-back')),
      [
        'Cyrene mascot: placed-back bottom-right of the primary display at 1544,424',
        'Cyrene mascot: placed-back where she was before, at 2184,736',
      ],
    );
  });

  it('leaves her where the owner dragged her meanwhile', async () => {
    const world = runMain();
    const mascot = await world.open();
    world.displays = [display(1, 0, 0, 1920, 1080), LEFT];
    metricsChanged(world);

    world.ipcMain.emit('drag-by', { sender: mascot.webContents }, -300, -200);
    world.displays = [PRIMARY, LEFT];
    metricsChanged(world);

    assert.deepEqual(mascot.getBounds(), { x: 1244, y: 224, width: 360, height: 640 });
  });
});
