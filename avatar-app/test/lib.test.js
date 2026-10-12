'use strict';

const assert = require('node:assert/strict');
const { describe, it } = require('node:test');

const {
  NEW_CURSOR_FEED,
  clampToWorkArea,
  cornerPosition,
  cursorReport,
  dragStep,
  isDragDelta,
  parseArgs,
  relativeCursor,
} = require('../lib');

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

describe('relativeCursor', () => {
  const window = { x: 1544, y: 384, width: 360, height: 640 };

  it('measures the cursor from the top-left corner of the window', () => {
    assert.deepEqual(relativeCursor({ x: 1544, y: 384 }, window), { x: 0, y: 0 });
    assert.deepEqual(relativeCursor({ x: 1700, y: 500 }, window), { x: 156, y: 116 });
  });

  it('reports a cursor outside the window too', () => {
    assert.deepEqual(relativeCursor({ x: 100, y: 50 }, window), { x: -1444, y: -334 });
    assert.deepEqual(relativeCursor({ x: 1919, y: 1079 }, window), { x: 375, y: 695 });
  });

  it('works on a display left of and above the primary one', () => {
    const left = { x: -1500, y: -200, width: 360, height: 640 };

    assert.deepEqual(relativeCursor({ x: -1400, y: -100 }, left), { x: 100, y: 100 });
    assert.deepEqual(relativeCursor({ x: 20, y: 30 }, left), { x: 1520, y: 230 });
  });
});

describe('cursorReport', () => {
  /** Polls the feed through `polls` ([cursor, showing]); returns every report sent to the page. */
  function run(polls) {
    let feed = NEW_CURSOR_FEED;
    const reports = [];
    for (const [cursor, showing] of polls) {
      const step = cursorReport(feed, cursor, showing);
      feed = step.feed;
      if (step.report !== null) {
        reports.push(step.report);
      }
    }
    return reports;
  }

  it('reports the first cursor and then every move, nothing while it stands still', () => {
    const reports = run([
      [{ x: 10, y: 20 }, true],
      [{ x: 10, y: 20 }, true],
      [{ x: 12, y: 20 }, true],
      [{ x: 12, y: 20 }, true],
      [{ x: -400, y: 900 }, true],
    ]);

    assert.deepEqual(reports, [
      { x: 10, y: 20 },
      { x: 12, y: 20 },
      { x: -400, y: 900 },
    ]);
  });

  it('reports nothing while the window is minimized or hidden', () => {
    const reports = run([
      [{ x: 10, y: 20 }, true],
      [null, false],
      [null, false],
    ]);

    assert.deepEqual(reports, [{ x: 10, y: 20 }]);
  });

  it('marks the first report after a pause as resumed, even for a cursor standing still', () => {
    assert.deepEqual(
      run([
        [{ x: 10, y: 20 }, true],
        [null, false],
        [{ x: 300, y: 200 }, true],
        [{ x: 305, y: 200 }, true],
      ]),
      [
        { x: 10, y: 20 },
        { x: 300, y: 200, resumed: true },
        { x: 305, y: 200 },
      ],
    );
    assert.deepEqual(
      run([
        [{ x: 10, y: 20 }, true],
        [null, false],
        [{ x: 10, y: 20 }, true],
        [{ x: 10, y: 20 }, true],
      ]),
      [
        { x: 10, y: 20 },
        { x: 10, y: 20, resumed: true },
      ],
    );
  });

  it('does not mark the very first report, even when the window started minimized', () => {
    assert.deepEqual(
      run([
        [null, false],
        [{ x: 10, y: 20 }, true],
      ]),
      [{ x: 10, y: 20 }],
    );
  });
});

describe('clampToWorkArea', () => {
  const workArea = { x: 0, y: 0, width: 1920, height: 1040 };
  const at = (x, y) => ({ x, y, width: 360, height: 640 });

  it('leaves a window inside the work area where it is', () => {
    assert.deepEqual(clampToWorkArea(at(800, 200), workArea), at(800, 200));
    assert.deepEqual(clampToWorkArea(at(0, 0), workArea), at(0, 0));
    assert.deepEqual(clampToWorkArea(at(1560, 400), workArea), at(1560, 400));
  });

  it('lets a window hang over an edge while 48 px stay inside', () => {
    assert.deepEqual(clampToWorkArea(at(-312, 100), workArea), at(-312, 100));
    assert.deepEqual(clampToWorkArea(at(1872, 100), workArea), at(1872, 100));
    assert.deepEqual(clampToWorkArea(at(100, -592), workArea), at(100, -592));
    assert.deepEqual(clampToWorkArea(at(100, 992), workArea), at(100, 992));
  });

  it('keeps 48 px of a window dragged far beyond any edge inside the work area', () => {
    assert.deepEqual(clampToWorkArea(at(-5000, 100), workArea), at(-312, 100));
    assert.deepEqual(clampToWorkArea(at(9000, 100), workArea), at(1872, 100));
    assert.deepEqual(clampToWorkArea(at(100, -5000), workArea), at(100, -592));
    assert.deepEqual(clampToWorkArea(at(100, 9000), workArea), at(100, 992));
    assert.deepEqual(clampToWorkArea(at(-5000, 9000), workArea), at(-312, 992));
  });

  it('follows a work area beside a taskbar or on a second display', () => {
    const besideTaskbar = { x: 48, y: 40, width: 1872, height: 1040 };
    const leftDisplay = { x: -1280, y: -100, width: 1280, height: 984 };

    assert.deepEqual(clampToWorkArea(at(-5000, -5000), besideTaskbar), at(-264, -552));
    assert.deepEqual(clampToWorkArea(at(5000, 5000), leftDisplay), at(-48, 836));
    assert.deepEqual(clampToWorkArea(at(-5000, -5000), leftDisplay), at(-1592, -692));
  });

  it('never changes the size of the window', () => {
    const big = { x: -3000, y: 4000, width: 2000, height: 2000 };
    const { width, height } = clampToWorkArea(big, workArea);

    assert.deepEqual({ width, height }, { width: 2000, height: 2000 });
  });

  it('keeps all of a window or work area smaller than 48 px', () => {
    const narrow = { x: 0, y: 0, width: 30, height: 1040 };
    const thin = { x: 9000, y: 9000, width: 40, height: 20 };

    assert.deepEqual(clampToWorkArea(at(5000, 100), narrow), at(0, 100));
    assert.deepEqual(clampToWorkArea(at(-5000, 100), narrow), at(-330, 100));
    assert.deepEqual(clampToWorkArea(thin, workArea), { x: 1880, y: 1020, width: 40, height: 20 });
  });

  it('uses the given minimum', () => {
    assert.deepEqual(clampToWorkArea(at(-5000, 9000), workArea, 100), at(-260, 940));
  });

  it('returns whole pixels that keep at least 48 px inside a fractional work area', () => {
    const scaled = { x: 0.4, y: 0.6, width: 1536.5, height: 816.7 };

    for (const [x, y] of [[-5000, -5000], [5000, 5000], [-0.6, 3.5], [700.5, 300.5]]) {
      const clamped = clampToWorkArea({ x, y, width: 361, height: 641 }, scaled);
      const visibleX = Math.min(clamped.x + 361, 1536.9) - Math.max(clamped.x, 0.4);
      const visibleY = Math.min(clamped.y + 641, 817.3) - Math.max(clamped.y, 0.6);
      assert.ok(Number.isInteger(clamped.x) && Number.isInteger(clamped.y), `${x}, ${y}`);
      assert.ok(visibleX >= 48 && visibleY >= 48, `${x}, ${y}: ${visibleX} x ${visibleY}`);
    }
  });

  it('never returns minus zero', () => {
    const clamped = clampToWorkArea(at(-0.3, -0.4), workArea);
    // The lowest position here is Math.ceil(0.4 + 48 - 48.9), which is -0.
    const edge = clampToWorkArea(
      { x: -5, y: -5, width: 48.9, height: 48.9 },
      { ...workArea, x: 0.4, y: 0.4 },
    );

    assert.deepEqual(clamped, at(0, 0));
    assert.ok(Object.is(edge.x, 0) && Object.is(edge.y, 0), `${Object.is(edge.x, -0)}`);
  });
});

describe('dragStep', () => {
  const at = (x, y) => ({ x, y, width: 360, height: 640 });

  it('moves the window by whole-pixel steps exactly', () => {
    assert.deepEqual(dragStep(at(100, 200), 12, -7), {
      bounds: at(112, 193),
      rest: { x: 0, y: 0 },
    });
  });

  it('carries the sub-pixel part to the next step, so slow drags add up', () => {
    let bounds = at(100, 200);
    let rest;
    for (let step = 0; step < 10; step += 1) {
      ({ bounds, rest } = dragStep(bounds, 0.8, -0.4, rest));
      assert.ok(Number.isInteger(bounds.x) && Number.isInteger(bounds.y), `step ${step}`);
      assert.ok(Math.abs(rest.x) <= 0.5 && Math.abs(rest.y) <= 0.5, `step ${step}`);
    }

    assert.deepEqual(bounds, at(108, 196));
  });

  it('keeps the size of the window', () => {
    const { bounds } = dragStep({ x: 0, y: 0, width: 400, height: 700 }, 3.6, 4.4);

    assert.deepEqual(bounds, { x: 4, y: 4, width: 400, height: 700 });
  });

  it('never returns minus zero', () => {
    const { bounds } = dragStep(at(0, 0), -0.3, -0.2);

    assert.ok(Object.is(bounds.x, 0) && Object.is(bounds.y, 0), `${bounds.x}, ${bounds.y}`);
  });
});

describe('isDragDelta', () => {
  it('accepts finite moves up to 2000 px on each axis', () => {
    for (const [dx, dy] of [[0, 0], [3, -4], [0.5, -0.25], [2000, -2000], [-2000, 2000]]) {
      assert.equal(isDragDelta(dx, dy), true, `${dx}, ${dy}`);
    }
  });

  it('refuses moves beyond 2000 px', () => {
    assert.equal(isDragDelta(2000.5, 0), false);
    assert.equal(isDragDelta(0, -2001), false);
  });

  it('refuses anything that is not a finite number', () => {
    const refused = [NaN, Infinity, -Infinity, '5', null, undefined, true, [5], { x: 5 }, 5n];
    for (const value of refused) {
      assert.equal(isDragDelta(value, 0), false, `dx ${String(value)}`);
      assert.equal(isDragDelta(0, value), false, `dy ${String(value)}`);
    }
  });
});

function pick({ width, height }) {
  return { width, height };
}
