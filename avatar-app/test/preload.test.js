'use strict';

// preload.js runs in Electron's sandbox, where require() knows only a few modules. Here it
// runs in a plain VM context whose require('electron') gives a fake contextBridge and
// ipcRenderer, so what the page can send to main.js is checked without Electron.

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { describe, it } = require('node:test');
const vm = require('node:vm');

const { DIAGNOSTIC_KINDS } = require('../lib');

const PRELOAD = readFileSync(path.join(__dirname, '..', 'preload.js'), 'utf8');

/** Runs preload.js; returns the page's window.cyrene and every message sent to main.js. */
function loadPreload() {
  const exposed = {};
  const sent = [];
  const electron = {
    contextBridge: {
      exposeInMainWorld(name, api) {
        exposed[name] = api;
      },
    },
    ipcRenderer: {
      on() {},
      removeListener() {},
      send(channel, ...args) {
        sent.push([channel, ...args]);
      },
    },
  };
  const require = (name) => {
    if (name !== 'electron') throw new Error(`the sandboxed preload cannot require ${name}`);
    return electron;
  };
  vm.runInNewContext(PRELOAD, { require });
  return { api: exposed.cyrene, sent };
}

describe('preload diagnostic', () => {
  it('sends every kind main.js accepts', () => {
    const { api, sent } = loadPreload();

    for (const kind of DIAGNOSTIC_KINDS) api.diagnostic(kind);

    assert.deepEqual(
      sent,
      DIAGNOSTIC_KINDS.map((kind) => ['diagnostic', kind]),
    );
  });

  it('sends nothing for any other kind', () => {
    const { api, sent } = loadPreload();
    const refused = ['page-crashed', 'page-hidden\nCyrene mascot: x', '', null, 7, { kind: 'x' }];

    for (const kind of refused) api.diagnostic(kind);

    assert.deepEqual(sent, []);
  });
});

describe('preload bridge', () => {
  it('gives the page the cursor, dragging, click-through and diagnostics, nothing else', () => {
    const { api } = loadPreload();

    assert.deepEqual(Object.keys(api).sort(), [
      'diagnostic',
      'dragBy',
      'onCursor',
      'setClickThrough',
    ]);
  });
});
