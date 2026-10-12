import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  BLINK_DURATION_S,
  BREATH_PERIOD_S,
  SWAY_PERIOD_S,
  approach,
  avatarTitle,
  blinkWeight,
  breathOffset,
  emotionTargets,
  framingFor,
  idleSway,
  listeningEndS,
  nextBlinkDelay,
  sampleVisemes,
  speechStartS,
} from "../../src/waifu/avatar/web/logic.js";

const CLOSE = 1e-9;

function assertClose(actual, expected, tolerance = CLOSE) {
  assert.ok(Math.abs(actual - expected) <= tolerance, `expected ${expected}, got ${actual}`);
}

function assertAllClose(actual, expected, tolerance = CLOSE) {
  assert.equal(actual.length, expected.length);
  actual.forEach((value, i) => assertClose(value, expected[i], tolerance));
}

describe("sampleVisemes", () => {
  const track = [
    [0.2, 0.0, 0.4, 0.0, 0.0],
    [0.6, 0.2, 0.0, 0.0, 1.0],
    [1.0, 0.0, 0.0, 0.8, 0.0],
  ];
  const fps = 10;
  const silent = [0, 0, 0, 0, 0];

  it("returns the frame itself at its own timestamp", () => {
    assertAllClose(sampleVisemes(track, fps, 0), track[0]);
    assertAllClose(sampleVisemes(track, fps, 0.1), track[1]);
    assertAllClose(sampleVisemes(track, fps, 0.2), track[2]);
  });

  it("interpolates linearly between neighbouring frames", () => {
    assertAllClose(sampleVisemes(track, fps, 0.05), [0.4, 0.1, 0.2, 0.0, 0.5]);
    assertAllClose(sampleVisemes(track, fps, 0.175), [0.9, 0.05, 0.0, 0.6, 0.25]);
  });

  it("fades the last frame out to a closed mouth at the end of the track", () => {
    assertAllClose(sampleVisemes(track, fps, 0.25), [0.5, 0.0, 0.0, 0.4, 0.0]);
  });

  it("is silent before the track starts and once it has ended", () => {
    assert.deepEqual(sampleVisemes(track, fps, -0.01), silent);
    assert.deepEqual(sampleVisemes(track, fps, 0.3), silent);
    assert.deepEqual(sampleVisemes(track, fps, 12), silent);
  });

  it("is silent for an empty track or a non-positive frame rate", () => {
    assert.deepEqual(sampleVisemes([], fps, 0), silent);
    assert.deepEqual(sampleVisemes(track, 0, 0.1), silent);
  });
});

describe("nextBlinkDelay", () => {
  it("maps the random source onto 2 to 6 seconds", () => {
    assertClose(nextBlinkDelay(() => 0), 2);
    assertClose(nextBlinkDelay(() => 0.5), 4);
    const longest = nextBlinkDelay(() => 0.999999);
    assert.ok(longest < 6 && longest > 5.99, `got ${longest}`);
  });

  it("stays within range for Math.random", () => {
    for (let i = 0; i < 200; i += 1) {
      const delay = nextBlinkDelay(Math.random);
      assert.ok(delay >= 2 && delay < 6, `got ${delay}`);
    }
  });
});

describe("blinkWeight", () => {
  const samples = Array.from({ length: 151 }, (_, i) => (i / 150) * BLINK_DURATION_S);

  it("lasts about 0.15 s", () => {
    assert.ok(BLINK_DURATION_S >= 0.1 && BLINK_DURATION_S <= 0.2);
  });

  it("is open before and after the blink", () => {
    assert.equal(blinkWeight(-0.5), 0);
    assert.equal(blinkWeight(0), 0);
    assert.equal(blinkWeight(BLINK_DURATION_S), 0);
    assert.equal(blinkWeight(BLINK_DURATION_S + 1), 0);
  });

  it("closes fully once, rising then falling, and closes faster than it opens", () => {
    const weights = samples.map(blinkWeight);
    const peak = Math.max(...weights);
    const peakIndex = weights.indexOf(peak);
    assertClose(peak, 1);
    for (let i = 1; i <= peakIndex; i += 1) assert.ok(weights[i] >= weights[i - 1]);
    for (let i = peakIndex + 1; i < weights.length; i += 1) assert.ok(weights[i] <= weights[i - 1]);
    assert.ok(samples[peakIndex] < BLINK_DURATION_S / 2, "the lids should close faster than they open");
    weights.forEach((weight) => assert.ok(weight >= 0 && weight <= 1));
  });
});

describe("approach", () => {
  it("does not move when no time has passed", () => {
    assert.equal(approach(0.3, 1, 0, 8), 0.3);
  });

  it("moves part of the way toward the target without overshooting", () => {
    const next = approach(0, 1, 0.016, 8);
    assert.ok(next > 0 && next < 1, `got ${next}`);
    const down = approach(1, 0, 0.016, 8);
    assert.ok(down < 1 && down > 0, `got ${down}`);
  });

  it("converges on the target", () => {
    let value = 0;
    let previousGap = 1;
    for (let frame = 0; frame < 120; frame += 1) {
      value = approach(value, 1, 1 / 60, 8);
      const gap = 1 - value;
      assert.ok(gap < previousGap && gap >= 0);
      previousGap = gap;
    }
    assert.ok(previousGap < 1e-3, `gap ${previousGap}`);
    assertClose(approach(0, 1, 100, 8), 1);
  });

  it("does not depend on how the time is sliced into frames", () => {
    const once = approach(0, 1, 0.1, 6);
    const twice = approach(approach(0, 1, 0.05, 6), 1, 0.05, 6);
    assertClose(once, twice, 1e-12);
  });

  it("moves faster with a higher speed", () => {
    assert.ok(approach(0, 1, 0.05, 12) > approach(0, 1, 0.05, 3));
  });
});

describe("emotionTargets", () => {
  const expressions = ["neutral", "happy", "sad", "angry", "Surprised", "relaxed"];
  const cases = [
    ["neutral", "neutral"],
    ["happy", "happy"],
    ["sad", "sad"],
    ["angry", "angry"],
    ["surprised", "Surprised"],
    ["relaxed", "relaxed"],
  ];

  for (const [emotion, expression] of cases) {
    it(`drives only ${expression} for ${emotion}`, () => {
      const targets = emotionTargets(emotion);
      assert.deepEqual(Object.keys(targets).sort(), [...expressions].sort());
      assert.ok(targets[expression] > 0 && targets[expression] <= 1);
      const others = expressions.filter((name) => name !== expression);
      others.forEach((name) => assert.equal(targets[name], 0, name));
      const sum = Object.values(targets).reduce((total, weight) => total + weight, 0);
      assertClose(sum, targets[expression]);
    });
  }

  it("falls back to neutral for an unknown emotion", () => {
    assert.deepEqual(emotionTargets("bored"), emotionTargets("neutral"));
    assert.deepEqual(emotionTargets(undefined), emotionTargets("neutral"));
  });

  it("returns a fresh object each time", () => {
    const first = emotionTargets("happy");
    first.happy = 0;
    assert.ok(emotionTargets("happy").happy > 0);
  });
});

describe("breathOffset", () => {
  it("stays between 0 (exhaled) and 1 (inhaled)", () => {
    for (let t = 0; t < 30; t += 0.05) {
      const value = breathOffset(t);
      assert.ok(value >= 0 && value <= 1, `t=${t} gave ${value}`);
    }
  });

  it("breathes in and out once per period", () => {
    assertClose(breathOffset(0), 0);
    assertClose(breathOffset(BREATH_PERIOD_S / 2), 1);
    for (let t = 0; t < 10; t += 0.37) assertClose(breathOffset(t), breathOffset(t + BREATH_PERIOD_S), 1e-9);
  });
});

describe("idleSway", () => {
  it("stays within a few degrees on both axes", () => {
    const limit = (4 * Math.PI) / 180;
    let largest = 0;
    for (let t = 0; t < 2 * SWAY_PERIOD_S; t += 0.05) {
      const { x, z } = idleSway(t);
      assert.ok(Math.abs(x) <= limit && Math.abs(z) <= limit, `t=${t} gave ${x}, ${z}`);
      largest = Math.max(largest, Math.abs(x), Math.abs(z));
    }
    assert.ok(largest > 0, "the sway should move");
  });

  it("repeats every period", () => {
    for (let t = 0; t < SWAY_PERIOD_S; t += 0.73) {
      const now = idleSway(t);
      const later = idleSway(t + SWAY_PERIOD_S);
      assertClose(now.x, later.x, 1e-9);
      assertClose(now.z, later.z, 1e-9);
    }
  });
});

describe("framingFor", () => {
  const headY = 1.17;

  function visibleHalfHeight(framing) {
    const distance = framing.position[2] - framing.target[2];
    return distance * Math.tan(((framing.fov / 2) * Math.PI) / 180);
  }

  it("frames the whole body from the feet to above the head for full", () => {
    const framing = framingFor("full", headY);
    assert.equal(framing.position[0], 0);
    assert.equal(framing.target[0], 0);
    assert.ok(framing.position[2] > 0, "the camera stands in front of her (+Z)");
    const half = visibleHalfHeight(framing);
    const centre = framing.target[1];
    assert.ok(centre - half <= 0, "feet in view");
    assert.ok(centre + half >= headY * 1.15, "top of the head in view");
    assert.ok(centre + half <= headY * 1.4, "not too much empty space above");
  });

  it("frames from the waist up, closer, for upper", () => {
    const full = framingFor("full", headY);
    const upper = framingFor("upper", headY);
    const half = visibleHalfHeight(upper);
    const centre = upper.target[1];
    assert.ok(upper.position[2] < full.position[2], "closer than full");
    assert.ok(centre - half > 0.3 * headY, "legs out of view");
    assert.ok(centre - half < 0.75 * headY, "chest in view");
    assert.ok(centre + half >= headY * 1.15, "top of the head in view");
  });

  it("scales with the model's height", () => {
    const small = framingFor("full", 0.8);
    const tall = framingFor("full", 1.6);
    assert.ok(tall.position[2] > small.position[2]);
    assert.ok(tall.target[1] > small.target[1]);
  });

  it("uses full framing for an unknown name", () => {
    assert.deepEqual(framingFor("portrait", headY), framingFor("full", headY));
  });
});

describe("listeningEndS", () => {
  it("ends the listening pose when the follow-up window closes", () => {
    assert.equal(listeningEndS(8, 100), 108);
    assert.equal(listeningEndS(0.5, 3), 3.5);
  });

  it("ends it at once without a follow-up window", () => {
    assert.equal(listeningEndS(0, 100), 100);
    assert.equal(listeningEndS(-2, 100), 100);
  });

  it("keeps listening until the next state when no window is given", () => {
    assert.equal(listeningEndS(undefined, 100), Infinity);
    assert.equal(listeningEndS(null, 100), Infinity);
    assert.equal(listeningEndS("8", 100), Infinity);
    assert.equal(listeningEndS(Number.NaN, 100), Infinity);
  });
});

describe("speechStartS", () => {
  it("starts a fresh track now", () => {
    assert.equal(speechStartS(undefined, 50), 50);
    assert.equal(speechStartS(0, 50), 50);
  });

  it("starts a replayed track as far back as it has already played", () => {
    assertClose(speechStartS(1.25, 50), 48.75);
  });

  it("ignores an elapsed time that is negative or not a number", () => {
    assert.equal(speechStartS(-1, 50), 50);
    assert.equal(speechStartS("1", 50), 50);
    assert.equal(speechStartS(Number.NaN, 50), 50);
    assert.equal(speechStartS(Infinity, 50), 50);
  });
});

describe("avatarTitle", () => {
  it("names the avatar window apart from the Cyrene console window", () => {
    assert.equal(avatarTitle("Cyrene"), "Avatar Cyrene");
    assert.equal(avatarTitle("Aiko"), "Avatar Aiko");
  });
});
