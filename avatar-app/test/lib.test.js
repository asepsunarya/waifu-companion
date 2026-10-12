'use strict';

const assert = require('node:assert/strict');
const { describe, it } = require('node:test');

const { cornerPosition, parseArgs } = require('../lib');

const URL_ARG = '--url=http://127.0.0.1:9882/?mode=mascot';

describe('parseArgs', () => {
  it('uses the defaults when only the url is given', () => {
    assert.deepEqual(parseArgs([URL_ARG]), {
      url: 'http://127.0.0.1:9882/?mode=mascot',
      width: 360,
      height: 640,
      corner: 'bottom-right',
      alwaysOnTop: true,
    });
  });

  it('reads the options among the electron path, the app folder and chromium switches', () => {
    const argv = [
      'C:\\repo\\avatar-app\\node_modules\\electron\\dist\\electron.exe',
      'C:\\repo\\avatar-app',
      '--allow-file-access-from-files',
      URL_ARG,
      '--width=400',
      '--height=700',
      '--corner=top-left',
      '--always-on-top=0',
    ];

    assert.deepEqual(parseArgs(argv), {
      url: 'http://127.0.0.1:9882/?mode=mascot',
      width: 400,
      height: 700,
      corner: 'top-left',
      alwaysOnTop: false,
    });
  });

  it('accepts every corner', () => {
    for (const corner of ['bottom-right', 'bottom-left', 'top-right', 'top-left']) {
      assert.equal(parseArgs([URL_ARG, `--corner=${corner}`]).corner, corner);
    }
  });

  it('falls back to bottom-right for an unknown corner', () => {
    assert.equal(parseArgs([URL_ARG, '--corner=middle']).corner, 'bottom-right');
  });

  it('turns always-on-top on with 1 and keeps it on for an unreadable value', () => {
    assert.equal(parseArgs([URL_ARG, '--always-on-top=1']).alwaysOnTop, true);
    assert.equal(parseArgs([URL_ARG, '--always-on-top=maybe']).alwaysOnTop, true);
  });

  it('clamps the size to 120..2000 wide and 160..2000 high', () => {
    assert.deepEqual(
      pick(parseArgs([URL_ARG, '--width=50', '--height=100'])),
      { width: 120, height: 160 },
    );
    assert.deepEqual(
      pick(parseArgs([URL_ARG, '--width=5000', '--height=9000'])),
      { width: 2000, height: 2000 },
    );
    assert.deepEqual(
      pick(parseArgs([URL_ARG, '--width=-400', '--height=-1'])),
      { width: 120, height: 160 },
    );
  });

  it('keeps the default size for values that are not whole numbers', () => {
    for (const value of ['', 'wide', '12.5', '1e3', '0x200']) {
      assert.deepEqual(
        pick(parseArgs([URL_ARG, `--width=${value}`, `--height=${value}`])),
        { width: 360, height: 640 },
        `value ${JSON.stringify(value)}`,
      );
    }
  });

  it('ignores unknown keys and the --key value form', () => {
    const parsed = parseArgs([URL_ARG, '--colour=pink', '--width', '900', 'height=900']);

    assert.deepEqual(pick(parsed), { width: 360, height: 640 });
    assert.equal('colour' in parsed, false);
  });

  it('lets the last value of a repeated key win', () => {
    assert.equal(parseArgs([URL_ARG, '--width=200', '--width=300']).width, 300);
  });

  it('keeps an equals sign inside the url', () => {
    assert.equal(
      parseArgs(['--url=http://127.0.0.1:9882/?mode=app&x=1']).url,
      'http://127.0.0.1:9882/?mode=app&x=1',
    );
  });

  it('refuses a missing or empty url', () => {
    assert.throws(() => parseArgs([]), /--url/);
    assert.throws(() => parseArgs(['--url=']), /--url/);
    assert.throws(() => parseArgs(['--url', 'http://127.0.0.1:9882/']), /--url/);
  });

  it('accepts every IPv4 loopback address, as avatar.host in the config does', () => {
    for (const url of [
      'http://127.0.0.2:9882/?mode=mascot',
      'http://127.1.2.3:9882/',
      'http://127.255.255.254:9882/',
    ]) {
      assert.equal(parseArgs([`--url=${url}`]).url, url);
    }
  });

  it('reads a short loopback address in its normal dotted form', () => {
    assert.equal(parseArgs(['--url=http://127.1:9882/']).url, 'http://127.0.0.1:9882/');
  });

  it('refuses a url that is not plain http on an IPv4 loopback address with a port', () => {
    const refused = [
      'not a url',
      'https://127.0.0.1:9882/',
      'file:///C:/Users/Lynx/page.html',
      'http://localhost:9882/',
      'http://192.168.1.20:9882/',
      'http://128.0.0.1:9882/',
      'http://0.0.0.0:9882/',
      'http://[::1]:9882/',
      'http://example.com:9882/',
      'http://127.0.0.1.example.com:9882/',
      'http://127.0.0.1/',
      'javascript:alert(1)',
    ];
    for (const url of refused) {
      assert.throws(() => parseArgs([`--url=${url}`]), /--url must be http:\/\/127\.0\.0\.1/, url);
    }
  });
});

describe('cornerPosition', () => {
  const screen = { x: 0, y: 0, width: 1920, height: 1040 };

  it('places the window 16 px inside each corner of the work area', () => {
    assert.deepEqual(cornerPosition(screen, 360, 640, 'bottom-right'), { x: 1544, y: 384 });
    assert.deepEqual(cornerPosition(screen, 360, 640, 'bottom-left'), { x: 16, y: 384 });
    assert.deepEqual(cornerPosition(screen, 360, 640, 'top-right'), { x: 1544, y: 16 });
    assert.deepEqual(cornerPosition(screen, 360, 640, 'top-left'), { x: 16, y: 16 });
  });

  it('follows a work area that does not start at the screen origin', () => {
    // Taskbar docked at the top-left: the work area starts below and right of it.
    const workArea = { x: 48, y: 40, width: 1872, height: 1040 };

    assert.deepEqual(cornerPosition(workArea, 360, 640, 'top-left'), { x: 64, y: 56 });
    assert.deepEqual(cornerPosition(workArea, 360, 640, 'bottom-right'), { x: 1544, y: 424 });
  });

  it('uses the given margin', () => {
    assert.deepEqual(cornerPosition(screen, 360, 640, 'bottom-right', 0), { x: 1560, y: 400 });
    assert.deepEqual(cornerPosition(screen, 360, 640, 'top-left', 40), { x: 40, y: 40 });
  });

  it('falls back to bottom-right for an unknown corner', () => {
    assert.deepEqual(cornerPosition(screen, 360, 640, 'centre'), { x: 1544, y: 384 });
  });

  it('gives up the margin before leaving a small work area', () => {
    const small = { x: 0, y: 0, width: 370, height: 650 };

    for (const corner of ['bottom-right', 'bottom-left', 'top-right', 'top-left']) {
      const { x, y } = cornerPosition(small, 360, 640, corner);
      assert.ok(x >= 0 && x + 360 <= 370, `${corner}: x ${x}`);
      assert.ok(y >= 0 && y + 640 <= 650, `${corner}: y ${y}`);
    }
  });

  it('pins a window larger than the work area to its top-left corner', () => {
    const tiny = { x: 100, y: 50, width: 300, height: 400 };

    for (const corner of ['bottom-right', 'bottom-left', 'top-right', 'top-left']) {
      assert.deepEqual(cornerPosition(tiny, 360, 640, corner), { x: 100, y: 50 }, corner);
    }
  });

  it('returns whole pixels inside a fractional work area', () => {
    const scaled = { x: 0.4, y: 0.6, width: 1536.5, height: 816.7 };

    for (const corner of ['bottom-right', 'bottom-left', 'top-right', 'top-left']) {
      const { x, y } = cornerPosition(scaled, 361, 641, corner);
      assert.ok(Number.isInteger(x) && Number.isInteger(y), `${corner}: ${x}, ${y}`);
      assert.ok(x >= 0.4 && x + 361 <= 1536.9, `${corner}: x ${x}`);
      assert.ok(y >= 0.6 && y + 641 <= 817.3, `${corner}: y ${y}`);
    }
  });
});

function pick({ width, height }) {
  return { width, height };
}
