import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  BLINK_DURATION_S,
  BREATH_PERIOD_S,
  DOUBLE_CLICK_MS,
  SWAY_PERIOD_S,
  adaptCubismCore,
  approach,
  avatarTitle,
  blinkWeight,
  breathOffset,
  classifyPointer,
  cursorLook,
  emotionEndAfterState,
  emotionTargets,
  frameDue,
  framingFor,
  hearingBadgeShown,
  idlePhase,
  idleSway,
  listeningEndS,
  live2dEmotionParams,
  live2dHeadBox,
  live2dMouth,
  live2dPlacement,
  live2dRegionAt,
  mapToRange,
  maxFpsFor,
  nextBlinkDelay,
  sampleVisemes,
  speechAfterState,
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

describe("speechAfterState", () => {
  const speech = { visemes: [[0.5, 0, 0, 0, 0]], fps: 60, duration: 9, startS: 10 };

  it("keeps the speak track while she is speaking", () => {
    assert.equal(speechAfterState(speech, "speaking"), speech);
  });

  it("ends the track at once on any other state, so a cut-off mouth closes", () => {
    for (const state of ["idle", "listening", "thinking", "unknown", undefined]) {
      assert.equal(speechAfterState(speech, state), null, String(state));
    }
  });

  it("leaves no track as none", () => {
    assert.equal(speechAfterState(null, "speaking"), null);
    assert.equal(speechAfterState(null, "listening"), null);
  });
});

describe("emotionEndAfterState", () => {
  const TAIL_S = 1.5;
  // A 40 s [sedih] reply that started at 10 s: its sad face was planned to end at 51.5 s.
  const speech = { visemes: [], fps: 60, duration: 40, startS: 10, emotionUntilS: 51.5 };

  it("ends a cut-off speech's own emotion a short tail after the cut", () => {
    for (const state of ["idle", "listening", "thinking"]) {
      assert.equal(emotionEndAfterState(51.5, speech, state, 15, TAIL_S), 16.5, state);
    }
  });

  it("keeps the speech's emotion while she is speaking", () => {
    assert.equal(emotionEndAfterState(51.5, speech, "speaking", 15, TAIL_S), 51.5);
  });

  it("leaves the tail of a speech that played out alone", () => {
    assert.equal(emotionEndAfterState(51.5, null, "listening", 50.2, TAIL_S), 51.5);
    assert.equal(emotionEndAfterState(51.5, speech, "listening", 50.2, TAIL_S), 51.5);
  });

  it("never shortens an emotion set after the speech started", () => {
    // A click's 3 s feedback face at 14 s, or an emotion event held until further notice.
    assert.equal(emotionEndAfterState(17, speech, "listening", 15, TAIL_S), 17);
    assert.equal(emotionEndAfterState(Infinity, speech, "listening", 15, TAIL_S), Infinity);
  });
});

describe("avatarTitle", () => {
  it("names the avatar window apart from the Cyrene console window", () => {
    assert.equal(avatarTitle("Cyrene"), "Avatar Cyrene");
    assert.equal(avatarTitle("Aiko"), "Avatar Aiko");
  });
});

describe("hearingBadgeShown", () => {
  it("shows the badge while she does not hear Master", () => {
    assert.equal(hearingBadgeShown({ type: "hearing", on: false }), true);
  });

  it("hides it once she hears him again", () => {
    assert.equal(hearingBadgeShown({ type: "hearing", on: true }), false);
  });

  it("never shows it for an event that does not say she stopped hearing", () => {
    assert.equal(hearingBadgeShown({ type: "hearing" }), false);
    assert.equal(hearingBadgeShown({ type: "hearing", on: "false" }), false);
  });
});

describe("live2dMouth", () => {
  it("keeps the mouth closed and neutral for silence", () => {
    assert.deepEqual(live2dMouth([0, 0, 0, 0, 0]), { open: 0, form: 0 });
  });

  it("opens the mouth fully for aa and less for the other vowels", () => {
    assertClose(live2dMouth([1, 0, 0, 0, 0]).open, 1);
    assertClose(live2dMouth([0, 0, 0, 0, 1]).open, 0.9);
    assertClose(live2dMouth([0, 0, 1, 0, 0]).open, 0.7);
    assertClose(live2dMouth([0, 0, 0, 1, 0]).open, 0.6);
    assertClose(live2dMouth([0, 1, 0, 0, 0]).open, 0.5);
  });

  it("adds the vowels together and caps the opening at 1", () => {
    assertClose(live2dMouth([0.2, 0.2, 0.2, 0.2, 0.2]).open, 0.2 * (1 + 0.5 + 0.7 + 0.6 + 0.9));
    assertClose(live2dMouth([0.5, 0, 0, 0, 0.3]).open, 0.77);
    assert.equal(live2dMouth([1, 1, 1, 1, 1]).open, 1);
    assert.equal(live2dMouth([0.8, 0, 0, 0, 0.8]).open, 1);
  });

  it("widens the mouth for ee and ih and rounds it for ou and oh", () => {
    assertClose(live2dMouth([0, 0, 0, 0.7, 0]).form, 0.7);
    assertClose(live2dMouth([0, 0.4, 0, 0, 0]).form, 0.4);
    assertClose(live2dMouth([0, 0, 0.6, 0, 0]).form, -0.6);
    assertClose(live2dMouth([0, 0, 0, 0, 0.5]).form, -0.5);
    assertClose(live2dMouth([0.9, 0, 0, 0, 0]).form, 0);
    assertClose(live2dMouth([0.5, 0.3, 0.1, 0.2, 0.1]).form, 0.3);
  });

  it("caps the form between -1 and 1", () => {
    assert.equal(live2dMouth([0, 1, 0, 1, 0]).form, 1);
    assert.equal(live2dMouth([0, 0, 1, 0, 1]).form, -1);
  });

  it("treats a missing or empty frame as a closed, neutral mouth", () => {
    for (const frame of [undefined, null, [], "aa", 0.5, {}]) {
      assert.deepEqual(live2dMouth(frame), { open: 0, form: 0 }, String(frame));
    }
  });

  it("reads a missing or non-numeric weight as 0", () => {
    assert.deepEqual(live2dMouth([0.5]), { open: 0.5, form: 0 });
    const mouth = live2dMouth([Number.NaN, undefined, 0.4, null, "1"]);
    assertClose(mouth.open, 0.28);
    assertClose(mouth.form, -0.4);
  });

  it("clamps each weight to 0..1 before mixing", () => {
    const mouth = live2dMouth([-1, 0, 0, 2, Infinity]);
    assertClose(mouth.open, 1);
    assertClose(mouth.form, 0);
    assert.deepEqual(live2dMouth([-1, -1, -1, -1, -1]), { open: 0, form: 0 });
  });

  it("follows a sampled viseme track frame by frame", () => {
    const track = [
      [0.6, 0.1, 0.0, 0.2, 0.0],
      [0.0, 0.0, 0.5, 0.0, 0.3],
    ];
    const first = live2dMouth(sampleVisemes(track, 10, 0));
    const second = live2dMouth(sampleVisemes(track, 10, 0.1));
    assert.ok(first.form > 0 && second.form < 0);
    assert.ok(first.open > 0 && second.open > 0);
  });
});

describe("mapToRange", () => {
  it("maps -1..1 onto a range around 0, keeping 0 at 0", () => {
    assert.equal(mapToRange(0, -30, 30), 0);
    assert.equal(mapToRange(1, -30, 30), 30);
    assert.equal(mapToRange(-1, -30, 30), -30);
    assertClose(mapToRange(0.5, -30, 30), 15);
    assertClose(mapToRange(-0.25, -30, 30), -7.5);
  });

  it("scales each side on its own for a lopsided range", () => {
    assertClose(mapToRange(-0.5, -10, 30), -5);
    assertClose(mapToRange(0.5, -10, 30), 15);
  });

  it("maps 0..1 onto a range that starts at 0", () => {
    assert.equal(mapToRange(0, 0, 1), 0);
    assert.equal(mapToRange(1, 0, 2), 2);
    assertClose(mapToRange(0.5, 0, 2), 1);
    assertClose(mapToRange(0.85, 0, 1), 0.85);
  });

  it("clamps values beyond the range to its ends", () => {
    assert.equal(mapToRange(1.2, 0, 1), 1);
    assert.equal(mapToRange(1.2, 0, 2), 2);
    assert.equal(mapToRange(-0.5, 0, 1), 0);
    assert.equal(mapToRange(5, -30, 30), 30);
    assert.equal(mapToRange(-5, -30, 30), -30);
    assert.equal(mapToRange(Infinity, -30, 30), 30);
    assert.equal(mapToRange(-Infinity, 0, 1), 0);
  });

  it("maps 0..1 from the minimum of a range that lies above 0", () => {
    assert.equal(mapToRange(0, 0.5, 2.5), 0.5);
    assertClose(mapToRange(0.5, 0.5, 2.5), 1.5);
    assert.equal(mapToRange(1, 0.5, 2.5), 2.5);
  });

  it("maps -1..0 onto a range that ends at or below 0", () => {
    assert.equal(mapToRange(-1, -2, 0), -2);
    assertClose(mapToRange(-0.5, -2, 0), -1);
    assert.equal(mapToRange(0, -2, 0), 0);
    assert.equal(mapToRange(0.5, -2, 0), 0);
    assert.equal(mapToRange(-1, -3, -1), -3);
    assert.equal(mapToRange(1, -3, -1), -1);
  });

  it("keeps a value that is not a number at the neutral point", () => {
    assert.equal(mapToRange(Number.NaN, -30, 30), 0);
    assert.equal(mapToRange(undefined, 0, 1), 0);
    assert.equal(mapToRange("1", 0, 1), 0);
    assert.equal(mapToRange(null, 0.5, 2), 0.5);
  });

  it("returns 0 for a range that is not a range", () => {
    assert.equal(mapToRange(0.5, Number.NaN, 1), 0);
    assert.equal(mapToRange(0.5, 0, undefined), 0);
    assert.equal(mapToRange(0.5, 1, -1), 0);
    assert.equal(mapToRange(0.5, -Infinity, Infinity), 0);
  });

  it("returns the only value of a range with one value", () => {
    assert.equal(mapToRange(0.7, 1, 1), 1);
  });
});

describe("live2dEmotionParams", () => {
  const ids = [
    "ParamEyeLSmile",
    "ParamEyeRSmile",
    "ParamMouthForm",
    "ParamCheek",
    "ParamBrowLY",
    "ParamBrowRY",
    "ParamBrowLAngle",
    "ParamBrowRAngle",
    "ParamEyeLOpen",
    "ParamEyeROpen",
    "ParamEyeBallForm",
  ];
  const neutral = {
    ParamEyeLSmile: 0,
    ParamEyeRSmile: 0,
    ParamMouthForm: 0,
    ParamCheek: 0,
    ParamBrowLY: 0,
    ParamBrowRY: 0,
    ParamBrowLAngle: 0,
    ParamBrowRAngle: 0,
    ParamEyeLOpen: 1,
    ParamEyeROpen: 1,
    ParamEyeBallForm: 0,
  };
  const recipes = {
    neutral,
    happy: {
      ...neutral,
      ParamEyeLSmile: 1,
      ParamEyeRSmile: 1,
      ParamMouthForm: 0.8,
      ParamCheek: 0.6,
      ParamBrowLY: 0.3,
      ParamBrowRY: 0.3,
    },
    sad: {
      ...neutral,
      ParamBrowLAngle: -0.7,
      ParamBrowRAngle: -0.7,
      ParamBrowLY: -0.4,
      ParamBrowRY: -0.4,
      ParamMouthForm: -0.6,
      ParamEyeLOpen: 0.85,
      ParamEyeROpen: 0.85,
    },
    angry: {
      ...neutral,
      ParamBrowLAngle: 0.8,
      ParamBrowRAngle: 0.8,
      ParamBrowLY: -0.6,
      ParamBrowRY: -0.6,
      ParamEyeLOpen: 0.8,
      ParamEyeROpen: 0.8,
      ParamMouthForm: -0.5,
    },
    surprised: {
      ...neutral,
      ParamBrowLY: 0.8,
      ParamBrowRY: 0.8,
      ParamEyeLOpen: 1.2,
      ParamEyeROpen: 1.2,
      ParamEyeBallForm: 0.6,
      ParamMouthForm: -0.2,
    },
    relaxed: {
      ...neutral,
      ParamEyeLSmile: 0.5,
      ParamEyeRSmile: 0.5,
      ParamMouthForm: 0.4,
      ParamCheek: 0.2,
    },
  };

  for (const [emotion, expected] of Object.entries(recipes)) {
    it(`sets the ${emotion} face on exactly the eleven face parameters`, () => {
      const params = live2dEmotionParams(emotion);
      assert.deepEqual(Object.keys(params).sort(), [...ids].sort());
      assert.deepEqual(params, expected);
    });
  }

  it("keeps every value on its normalized scale, the same on both sides", () => {
    for (const emotion of Object.keys(recipes)) {
      const params = live2dEmotionParams(emotion);
      for (const side of ["L", "R"]) {
        const smile = params[`ParamEye${side}Smile`];
        assert.ok(smile >= 0 && smile <= 1, `${emotion} smile ${smile}`);
        assert.ok(Math.abs(params[`ParamBrow${side}Y`]) <= 1, emotion);
        assert.ok(Math.abs(params[`ParamBrow${side}Angle`]) <= 1, emotion);
        assert.ok(params[`ParamEye${side}Open`] > 0, `${emotion} keeps the eyes open`);
      }
      assert.equal(params.ParamEyeLSmile, params.ParamEyeRSmile);
      assert.equal(params.ParamBrowLY, params.ParamBrowRY);
      assert.equal(params.ParamBrowLAngle, params.ParamBrowRAngle);
      assert.equal(params.ParamEyeLOpen, params.ParamEyeROpen);
      assert.ok(params.ParamCheek >= 0 && params.ParamCheek <= 1, emotion);
      assert.ok(Math.abs(params.ParamMouthForm) <= 1, emotion);
      assert.ok(params.ParamEyeBallForm >= 0 && params.ParamEyeBallForm <= 1, emotion);
    }
  });

  it("falls back to neutral for an unknown emotion", () => {
    for (const emotion of ["bored", "Happy", "", "toString", "__proto__", undefined, null, 3]) {
      assert.deepEqual(live2dEmotionParams(emotion), neutral, String(emotion));
    }
  });

  it("returns a fresh object each time", () => {
    const first = live2dEmotionParams("happy");
    first.ParamCheek = 0;
    assert.equal(live2dEmotionParams("happy").ParamCheek, 0.6);
    const unknown = live2dEmotionParams("bored");
    unknown.ParamEyeLOpen = 0;
    assert.equal(live2dEmotionParams("neutral").ParamEyeLOpen, 1);
  });
});

describe("cursorLook", () => {
  const face = { x: 180, y: 200 };
  const size = 300;
  const straight = { angleX: 0, angleY: 0, bodyX: 0, eyeX: 0, eyeY: 0 };

  it("looks straight ahead when the cursor is on her face", () => {
    assert.deepEqual(cursorLook({ x: 180, y: 200 }, face, size), straight);
  });

  it("turns toward a cursor to her right or left", () => {
    const right = cursorLook({ x: 180 + size, y: 200 }, face, size);
    assertClose(right.angleX, Math.tanh(1));
    assertClose(right.angleY, 0);
    assertClose(right.bodyX, 0.35 * Math.tanh(1));
    assertClose(right.eyeX, 1.2 * Math.tanh(1));
    assertClose(right.eyeY, 0);
    const left = cursorLook({ x: 180 - size, y: 200 }, face, size);
    assertClose(left.angleX, -Math.tanh(1));
    assertClose(left.bodyX, -0.35 * Math.tanh(1));
    assertClose(left.eyeX, -1.2 * Math.tanh(1));
  });

  it("looks up at a cursor above her face and down at one below (screen y grows downward)", () => {
    const above = cursorLook({ x: 180, y: 50 }, face, size);
    assertClose(above.angleY, Math.tanh(0.5));
    assertClose(above.eyeY, 1.2 * Math.tanh(0.5));
    assertClose(above.angleX, 0);
    const below = cursorLook({ x: 180, y: 350 }, face, size);
    assertClose(below.angleY, -Math.tanh(0.5));
    assertClose(below.eyeY, -1.2 * Math.tanh(0.5));
  });

  it("follows a nearby cursor almost linearly, without a dead zone or a jump", () => {
    const near = cursorLook({ x: 183, y: 197 }, face, size);
    assertClose(near.angleX, 0.01, 1e-5);
    assertClose(near.angleY, 0.01, 1e-5);
  });

  it("turns further the further away the cursor is, saturating smoothly", () => {
    const angles = [0, 30, 100, 300, 1000].map((dx) => cursorLook({ x: 180 + dx, y: 200 }, face, size).angleX);
    for (let i = 1; i < angles.length; i += 1) assert.ok(angles[i] > angles[i - 1], `${angles}`);
    const far = cursorLook({ x: 180 + 1e6, y: 200 - 1e6 }, face, size);
    assert.ok(far.angleX > 0.999 && far.angleX <= 1, `got ${far.angleX}`);
    assert.ok(far.angleY > 0.999 && far.angleY <= 1, `got ${far.angleY}`);
    assert.equal(far.eyeX, 1);
    assert.equal(far.eyeY, 1);
    assertClose(far.bodyX, 0.35 * far.angleX);
  });

  it("moves the body less than the head and the eyes a little ahead of it", () => {
    const look = cursorLook({ x: 120, y: 260 }, face, size);
    assertClose(look.bodyX, 0.35 * look.angleX);
    assertClose(look.eyeX, 1.2 * look.angleX);
    assertClose(look.eyeY, 1.2 * look.angleY);
    assert.ok(Math.abs(look.bodyX) < Math.abs(look.angleX) && Math.abs(look.angleX) < Math.abs(look.eyeX));
  });

  it("keeps every output within -1..1 anywhere on or off the screen", () => {
    for (let x = -5000; x <= 5000; x += 250) {
      for (let y = -5000; y <= 5000; y += 250) {
        const look = cursorLook({ x, y }, face, size);
        for (const [name, value] of Object.entries(look)) {
          assert.ok(value >= -1 && value <= 1, `${name}=${value} at ${x},${y}`);
        }
      }
    }
  });

  it("turns less for the same cursor when her face is larger", () => {
    const cursor = { x: 330, y: 200 };
    assert.ok(cursorLook(cursor, face, 600).angleX < cursorLook(cursor, face, 300).angleX);
  });

  it("looks straight ahead for a missing or invalid cursor, face or size", () => {
    const cursor = { x: 330, y: 120 };
    const cases = [
      [undefined, face, size],
      [null, face, size],
      [{ x: Number.NaN, y: 0 }, face, size],
      [{ x: 10 }, face, size],
      [{ x: "10", y: 0 }, face, size],
      [{ x: Infinity, y: 0 }, face, size],
      [cursor, undefined, size],
      [cursor, { x: 180, y: Number.NaN }, size],
      [cursor, face, 0],
      [cursor, face, -300],
      [cursor, face, Number.NaN],
      [cursor, face, undefined],
      [cursor, face, "300"],
      [cursor, face, Infinity],
    ];
    for (const [c, f, s] of cases) {
      assert.deepEqual(cursorLook(c, f, s), straight, JSON.stringify([c, f, s]));
    }
  });
});

describe("live2dHeadBox and live2dRegionAt", () => {
  // A full-body model 1000 px tall (y downward) drawn at 1:1 on the canvas, laid out like Cyrene20251105 with
  // her wings: her head drawables span 0-15 %, her chest 18-24 %, and a ribbon that turns with her head
  // hangs to her feet, so the top 28 % of her bounds (the old rule) reached down over her chest.
  const rect = (left, top, right, bottom) => [left, top, right, top, right, bottom, left, bottom];
  const hair = { vertices: rect(400, 0, 600, 120), head: true, mouth: false };
  const face = { vertices: rect(440, 40, 560, 150), head: true, mouth: true };
  const mouth = { vertices: rect(485, 120, 515, 135), head: true, mouth: true };
  const hairLock = { vertices: rect(450, 100, 470, 210), head: true, mouth: false };
  const ribbon = { vertices: rect(150, 20, 850, 1000), head: true, mouth: false };
  const neck = { vertices: rect(480, 140, 520, 185), head: false, mouth: false };
  const chest = { vertices: rect(455, 180, 545, 240), head: false, mouth: false };
  const legs = { vertices: rect(430, 240, 570, 620), head: false, mouth: false };
  const model = [ribbon, legs, chest, neck, hair, hairLock, face, mouth];
  const body = { left: 150, top: 0, width: 700, height: 1000 };
  const opaque = () => true;

  function regionsOf(drawables) {
    return { body, head: live2dHeadBox(drawables) };
  }

  it("ends her head just above her chin, the lowest point of the head drawables her mouth moves", () => {
    const head = live2dHeadBox(model);
    assertClose(head.top, 0);
    assertClose(head.top + head.height, 150 - 0.05 * 150);
  });

  it("spans only the points of her head drawables above that line: what hangs lower never widens it", () => {
    const head = live2dHeadBox(model);
    assertClose(head.left, 150, 1e-9);
    assertClose(head.left + head.width, 850);
    const withoutRibbon = live2dHeadBox([legs, chest, neck, hair, hairLock, face, mouth]);
    assertClose(withoutRibbon.left, 400);
    assertClose(withoutRibbon.left + withoutRibbon.width, 600);
    const sweeping = { vertices: [500, 20, 500, 140, 40, 900, 960, 900], head: true, mouth: false };
    const swept = live2dHeadBox([sweeping, face]);
    assert.ok(swept.left > 440 - 1e-9 && swept.left + swept.width < 560 + 1e-9, JSON.stringify(swept));
  });

  it("makes a touch on her chest a body touch, never a head pat", () => {
    const regions = regionsOf(model);
    for (const y of [180, 186, 200, 220, 240]) {
      for (const x of [455, 480, 500, 520, 545]) {
        assert.equal(live2dRegionAt(x, y, regions, opaque), "body", `${x},${y}`);
      }
    }
  });

  it("makes the hair hanging in front of her chest body too", () => {
    assert.equal(live2dRegionAt(460, 200, regionsOf(model), opaque), "body");
  });

  it("pats her hair, forehead, face and mouth", () => {
    const regions = regionsOf(model);
    for (const [x, y] of [
      [500, 5],
      [420, 60],
      [500, 100],
      [500, 130],
    ]) {
      assert.equal(live2dRegionAt(x, y, regions, opaque), "head", `${x},${y}`);
    }
  });

  it("counts the head between strands of hair as head, so a rub never breaks off", () => {
    assert.equal(live2dRegionAt(500, 60, regionsOf(model), () => false), "head");
  });

  it("touches her body only where a pixel of her is drawn inside her bounds", () => {
    const regions = regionsOf(model);
    const drawn = (x) => x >= 430 && x <= 570;
    assert.equal(live2dRegionAt(500, 400, regions, drawn), "body");
    assert.equal(live2dRegionAt(200, 400, regions, drawn), null);
    assert.equal(live2dRegionAt(100, 400, regions, opaque), null, "outside her bounds");
    assert.equal(live2dRegionAt(500, 1001, regions, opaque), null, "below her bounds");
  });

  it("has no head without a head drawable that her mouth moves: she is all body then", () => {
    const noMouth = [ribbon, legs, chest, hair, { ...face, mouth: false }];
    const mouthOffHead = [ribbon, legs, chest, hair, { ...face, head: false }];
    for (const drawables of [noMouth, mouthOffHead, [], undefined, null, "face"]) {
      assert.equal(live2dHeadBox(drawables), null, JSON.stringify(drawables));
    }
    const regions = { body, head: live2dHeadBox(noMouth) };
    assert.equal(live2dRegionAt(500, 5, regions, opaque), "body");
    assert.equal(live2dRegionAt(500, 200, regions, opaque), "body");
  });

  it("skips drawables and points that are not finite numbers", () => {
    const broken = [
      null,
      { head: true, mouth: true },
      { vertices: [Number.NaN, 900, 500, Infinity], head: true, mouth: true },
      { vertices: "500,900", head: true, mouth: true },
      face,
      hair,
    ];
    const head = live2dHeadBox(broken);
    assertClose(head.top + head.height, 150 - 0.05 * 150);
    assertClose(head.left, 400);
  });

  it("touches nothing without her bounds", () => {
    assert.equal(live2dRegionAt(500, 100, { body: null, head: live2dHeadBox(model) }, opaque), null);
  });
});

describe("idlePhase", () => {
  it("stays active while the owner was around in the last three minutes", () => {
    for (const idleS of [0, 1, 60, 179.999]) assert.equal(idlePhase(idleS), "active", String(idleS));
  });

  it("gets bored from 180 s without activity on", () => {
    for (const idleS of [180, 180.001, 600, 86400, Infinity]) assert.equal(idlePhase(idleS), "bored", String(idleS));
  });

  it("stays active for a missing or invalid idle time", () => {
    for (const idleS of [undefined, null, Number.NaN, -5, -Infinity, "200"]) {
      assert.equal(idlePhase(idleS), "active", String(idleS));
    }
  });
});

describe("classifyPointer", () => {
  // The head is the box x 100..260, y 0..120 of the window; the rest of her is body.
  const inHead = (x, y) => x >= 100 && x <= 260 && y >= 0 && y <= 120;
  const HEAD = { x: 180, y: 60 };
  const BODY = { x: 180, y: 400 };
  const down = (t, { x, y }) => ({ t, x, y, type: "down" });
  const move = (t, { x, y }) => ({ t, x, y, type: "move" });
  const up = (t, { x, y }) => ({ t, x, y, type: "up" });
  const at = (point, dx, dy) => ({ x: point.x + dx, y: point.y + dy });

  /** A press at `start` moving through `offsets` ([dx, dy] from the press, 16 ms apart), then released there. */
  function press(t0, start, offsets, { release = true } = {}) {
    const samples = [down(t0, start)];
    let t = t0;
    let last = [0, 0];
    for (const [dx, dy] of offsets) {
      t += 16;
      last = [dx, dy];
      samples.push(move(t, at(start, dx, dy)));
    }
    if (release) samples.push(up(t + 16, at(start, ...last)));
    return samples;
  }

  function click(t0, point, holdMs = 80) {
    return [down(t0, point), up(t0 + holdMs, point)];
  }

  /** Offsets that go `length` px along `axis` and back, changing direction `turns` times. */
  function rubbing(turns, axis = [1, 0], length = 20) {
    const offsets = [];
    for (let leg = 0; leg <= turns; leg += 1) {
      const from = leg % 2 === 0 ? 0 : length;
      const to = length - from;
      const middle = (from + to) / 2;
      offsets.push([axis[0] * middle, axis[1] * middle], [axis[0] * to, axis[1] * to]);
    }
    return offsets;
  }

  it("is undecided without a press", () => {
    assert.equal(classifyPointer([], inHead), null);
    assert.equal(classifyPointer(undefined, inHead), null);
    assert.equal(classifyPointer("down", inHead), null);
    assert.equal(classifyPointer([move(0, BODY), move(16, at(BODY, 40, 0))], inHead), null);
  });

  it("ignores a release without a press", () => {
    assert.equal(classifyPointer([up(0, BODY)], inHead), null);
    assert.equal(classifyPointer([move(0, HEAD), up(16, HEAD)], inHead), null);
  });

  it("is undecided while the button is held still", () => {
    assert.equal(classifyPointer([down(0, BODY)], inHead), null);
    assert.equal(classifyPointer([down(0, HEAD), move(500, HEAD)], inHead), null);
  });

  it("reports a press and release in place as a click, on the head and on the body", () => {
    assert.equal(classifyPointer(click(0, HEAD), inHead), "click");
    assert.equal(classifyPointer(click(0, BODY), inHead), "click");
    assert.equal(classifyPointer(click(0, BODY, 2000), inHead), "click");
  });

  it("keeps jitter under 3 px a click", () => {
    const wobble = [
      [1, 0],
      [-1, 1],
      [2, -1],
      [0, 2],
      [-2, -2],
      [2, 2],
      [0, 0],
    ];
    assert.equal(classifyPointer(press(0, BODY, wobble), inHead), "click");
    assert.equal(classifyPointer(press(0, HEAD, wobble), inHead), "click");
    const shake = [];
    for (let i = 0; i < 12; i += 1) shake.push(i % 2 === 0 ? [2.9, 0] : [0, 0], i % 2 === 0 ? [0, 2.9] : [0, 0]);
    assert.equal(classifyPointer(press(0, HEAD, shake, { release: false }), inHead), null);
    assert.equal(classifyPointer(press(0, HEAD, shake), inHead), "click");
  });

  describe("from a press on the body", () => {
    it("is still a click after moving exactly 6 px", () => {
      assert.equal(classifyPointer(press(0, BODY, [[6, 0]]), inHead), "click");
      assert.equal(classifyPointer(press(0, BODY, [[0, -6]]), inHead), "click");
      assert.equal(classifyPointer(press(0, BODY, [[4, 4]]), inHead), "click");
    });

    it("drags once the pointer moves more than 6 px, while the button is still held", () => {
      assert.equal(classifyPointer(press(0, BODY, [[6.01, 0]], { release: false }), inHead), "drag");
      assert.equal(classifyPointer(press(0, BODY, [[5, 5]], { release: false }), inHead), "drag");
      assert.equal(classifyPointer(press(0, BODY, [[0, -40]]), inHead), "drag");
    });

    it("counts the release point as movement too", () => {
      assert.equal(classifyPointer([down(0, BODY), up(50, at(BODY, 10, 0))], inHead), "drag");
    });

    it("stays a drag when the pointer comes back to where it was pressed", () => {
      assert.equal(classifyPointer(press(0, BODY, [[30, 0], [0, 0]]), inHead), "drag");
    });

    it("drags rather than rubs when the pointer goes back and forth", () => {
      assert.equal(classifyPointer(press(0, BODY, rubbing(3)), inHead), "drag");
    });
  });

  describe("from a press on the head", () => {
    it("does not drag while the pointer stays on the head, however far it moves", () => {
      assert.equal(classifyPointer(press(0, HEAD, [[40, 0]], { release: false }), inHead), null);
      assert.equal(classifyPointer(press(0, HEAD, [[40, 0], [70, 50]]), inHead), "click");
    });

    it("does not drag after leaving the head 24 px or less from the press", () => {
      const edge = { x: 180, y: 110 };
      assert.equal(classifyPointer(press(0, edge, [[0, 24]], { release: false }), inHead), null);
      assert.equal(classifyPointer(press(0, edge, [[0, 24]]), inHead), "click");
    });

    it("drags once the pointer is off the head more than 24 px from the press", () => {
      const edge = { x: 180, y: 110 };
      assert.equal(classifyPointer(press(0, edge, [[0, 24.01]], { release: false }), inHead), "drag");
      assert.equal(classifyPointer(press(0, HEAD, [[0, 30], [0, 70]], { release: false }), inHead), "drag");
    });

    it("stays a drag when the pointer comes back onto the head", () => {
      assert.equal(classifyPointer(press(0, HEAD, [[0, 70], [0, 0]]), inHead), "drag");
    });

    it("recognises rubbing at the third change of direction, while the button is held", () => {
      assert.equal(classifyPointer(press(0, HEAD, rubbing(2), { release: false }), inHead), null);
      assert.equal(classifyPointer(press(0, HEAD, rubbing(3), { release: false }), inHead), "rub");
      assert.equal(classifyPointer(press(0, HEAD, rubbing(8), { release: false }), inHead), "rub");
    });

    it("stays a rub when released, and is a click after only two changes", () => {
      assert.equal(classifyPointer(press(0, HEAD, rubbing(3)), inHead), "rub");
      assert.equal(classifyPointer(press(0, HEAD, rubbing(2)), inHead), "click");
    });

    it("counts a stroke of exactly 6 px toward rubbing, and not one of less", () => {
      const strokes = (length) => [
        [length, 0],
        [0, 0],
        [length, 0],
        [0, 0],
      ];
      assert.equal(classifyPointer(press(0, HEAD, strokes(6), { release: false }), inHead), "rub");
      assert.equal(classifyPointer(press(0, HEAD, strokes(5.99), { release: false }), inHead), null);
    });

    it("recognises rubbing up and down too", () => {
      assert.equal(classifyPointer(press(0, HEAD, rubbing(3, [0, 1])), inHead), "rub");
    });

    it("counts a diagonal change of direction once, not once per axis", () => {
      assert.equal(classifyPointer(press(0, HEAD, rubbing(2, [1, 1], 14), { release: false }), inHead), null);
      assert.equal(classifyPointer(press(0, HEAD, rubbing(3, [1, 1], 14), { release: false }), inHead), "rub");
    });

    it("counts only the movement on the head toward rubbing", () => {
      const edge = { x: 180, y: 116 };
      const offHead = rubbing(5, [0, 1], 20);
      assert.equal(classifyPointer(press(0, edge, offHead, { release: false }), inHead), null);
      assert.equal(classifyPointer(press(0, edge, offHead), inHead), "click");
    });

    it("turns a rub into a drag when the pointer leaves the head far enough", () => {
      const offsets = [...rubbing(3), [0, 100]];
      assert.equal(classifyPointer(press(0, HEAD, offsets, { release: false }), inHead), "drag");
    });
  });

  describe("double click", () => {
    it("is two clicks whose presses are at most 500 ms and 12 px apart", () => {
      assert.equal(classifyPointer([...click(0, BODY), ...click(500, BODY)], inHead), "double_click");
      assert.equal(classifyPointer([...click(0, BODY), ...click(200, at(BODY, 12, 0))], inHead), "double_click");
      assert.equal(classifyPointer([...click(0, HEAD), ...click(150, HEAD)], inHead), "double_click");
    });

    it("accepts a double-click as slow as Windows' default double-click time", () => {
      assert.equal(classifyPointer([...click(0, HEAD), ...click(400, HEAD)], inHead), "double_click");
      assert.equal(classifyPointer([...click(0, BODY), ...click(450, BODY)], inHead), "double_click");
    });

    it("exports its 500 ms window, so a single click can wait for a possible second one", () => {
      assert.equal(DOUBLE_CLICK_MS, 500);
    });

    it("is two plain clicks when the second press comes more than 500 ms later", () => {
      assert.equal(classifyPointer([...click(0, BODY), ...click(501, BODY)], inHead), "click");
    });

    it("is two plain clicks when the presses are more than 12 px apart", () => {
      assert.equal(classifyPointer([...click(0, BODY), ...click(200, at(BODY, 12.01, 0))], inHead), "click");
      assert.equal(classifyPointer([...click(0, BODY), ...click(200, at(BODY, 9, 9))], inHead), "click");
    });

    it("measures the time from the first press to the second press", () => {
      const slowFirst = [down(0, BODY), up(200, BODY), down(340, BODY), up(420, BODY)];
      assert.equal(classifyPointer(slowFirst, inHead), "double_click");
      const lateSecond = [down(0, BODY), up(100, BODY), down(510, BODY), up(550, BODY)];
      assert.equal(classifyPointer(lateSecond, inHead), "click");
    });

    it("is undecided while the second press is still held", () => {
      assert.equal(classifyPointer([...click(0, BODY), down(200, BODY)], inHead), null);
    });

    it("counts a click with small jitter", () => {
      assert.equal(classifyPointer([...press(0, BODY, [[2, 1]]), ...click(200, BODY)], inHead), "double_click");
    });

    it("does not count a drag or a rub as the first click", () => {
      assert.equal(classifyPointer([...press(0, BODY, [[10, 0]]), ...click(150, BODY)], inHead), "click");
      assert.equal(classifyPointer([...press(0, HEAD, rubbing(3)), ...click(300, HEAD)], inHead), "click");
    });

    it("does not chain a third click onto a double click, but pairs a fourth with it", () => {
      const three = [...click(0, BODY), ...click(150, BODY), ...click(300, BODY)];
      assert.equal(classifyPointer(three, inHead), "click");
      assert.equal(classifyPointer([...three, ...click(450, BODY)], inHead), "double_click");
    });

    it("does not pair clicks around a press whose release was lost", () => {
      const samples = [...click(0, BODY), down(100, BODY), ...click(200, BODY)];
      assert.equal(classifyPointer(samples, inHead), "click");
    });
  });

  it("classifies only the latest press", () => {
    assert.equal(classifyPointer([...press(0, BODY, [[30, 0]]), ...click(1000, BODY)], inHead), "click");
    assert.equal(classifyPointer([...click(0, BODY), ...press(1000, BODY, [[30, 0]])], inHead), "drag");
    assert.equal(classifyPointer([...click(0, BODY), down(1000, BODY)], inHead), null);
  });

  it("ignores hovering after the release", () => {
    assert.equal(classifyPointer([...click(0, BODY), move(200, at(BODY, 300, 0))], inHead), "click");
  });

  it("skips samples with a missing or non-numeric time, position or type", () => {
    const samples = [
      down(0, BODY),
      { t: 10, x: Number.NaN, y: 0, type: "move" },
      { t: 20, x: 999, type: "move" },
      { t: Number.NaN, x: 999, y: 999, type: "move" },
      { t: 30, x: "999", y: 999, type: "move" },
      { t: 35, x: 999, y: 999, type: "hover" },
      { t: 38, x: 999, y: 999 },
      null,
      up(40, BODY),
    ];
    assert.equal(classifyPointer(samples, inHead), "click");
    assert.equal(classifyPointer([{ t: 0, x: Number.NaN, y: 0, type: "down" }, up(40, BODY)], inHead), null);
  });

  it("treats every press as a body press without a head test", () => {
    assert.equal(classifyPointer(press(0, HEAD, [[10, 0]]), inHead), "click");
    assert.equal(classifyPointer(press(0, HEAD, [[10, 0]])), "drag");
    assert.equal(classifyPointer(press(0, HEAD, [[10, 0]]), "head"), "drag");
  });
});

describe("live2dPlacement", () => {
  // Model space, y down. A narrow model (arms down) and one whose wings make it wider than the window's aspect, so
  // full is bound by its width.
  const NARROW = { x: -0.2, y: -0.6, width: 0.4, height: 1.2 };
  const WINGED = { x: -0.5, y: -0.6, width: 1.0, height: 1.2 };
  const WIDTH = 360;
  const HEIGHT = 640;
  const MARGIN = HEIGHT * 0.04;

  function onCanvas(placement, y) {
    return HEIGHT / 2 + (y - placement.pivot.y) * placement.scale;
  }

  it("fits the whole model inside the window for full", () => {
    for (const bounds of [NARROW, WINGED]) {
      const placement = live2dPlacement(bounds, "full", WIDTH, HEIGHT);
      assert.ok(bounds.width * placement.scale <= WIDTH - 2 * MARGIN + CLOSE);
      assert.ok(bounds.height * placement.scale <= HEIGHT - 2 * MARGIN + CLOSE);
      assert.ok(Math.abs(onCanvas(placement, bounds.y) - (HEIGHT - bounds.height * placement.scale) / 2) < 1e-6);
      assert.equal(placement.pivot.x, bounds.x + bounds.width / 2);
    }
    const winged = live2dPlacement(WINGED, "full", WIDTH, HEIGHT);
    assert.ok(Math.abs(WINGED.width * winged.scale - (WIDTH - 2 * MARGIN)) < 1e-6, "as wide as the window allows");
    const narrow = live2dPlacement(NARROW, "full", WIDTH, HEIGHT);
    assert.ok(Math.abs(NARROW.height * narrow.scale - (HEIGHT - 2 * MARGIN)) < 1e-6, "as tall as the window allows");
  });

  it("fills the window's height with the top half of her for upper, even when she is wider than the window", () => {
    for (const bounds of [NARROW, WINGED]) {
      const placement = live2dPlacement(bounds, "upper", WIDTH, HEIGHT);
      assert.ok(Math.abs(onCanvas(placement, bounds.y) - MARGIN) < 1e-6, "the top of her at the top margin");
      assert.ok(Math.abs(onCanvas(placement, bounds.y + bounds.height / 2) - (HEIGHT - MARGIN)) < 1e-6);
      assert.equal(placement.pivot.x, bounds.x + bounds.width / 2);
    }
  });

  it("shows her bigger for upper than for full", () => {
    for (const bounds of [NARROW, WINGED]) {
      const full = live2dPlacement(bounds, "full", WIDTH, HEIGHT);
      const upper = live2dPlacement(bounds, "upper", WIDTH, HEIGHT);
      assert.ok(upper.scale > full.scale * 1.5);
    }
  });

  it("treats an unknown framing as full", () => {
    const full = live2dPlacement(WINGED, "full", WIDTH, HEIGHT);
    assert.deepEqual(live2dPlacement(WINGED, "portrait", WIDTH, HEIGHT), full);
  });

  it("never scales below zero in a window narrower than its margins", () => {
    assert.equal(live2dPlacement(NARROW, "full", 10, HEIGHT).scale, 0);
  });
});

describe("adaptCubismCore", () => {
  // Shaped like live2dcubismcore.min.js: Core 6 keeps the render orders on the model (drawables first, then
  // offscreens) as a view of its heap, and each drawable's blend mode as color | alpha << 8 in a view twice as long
  // as the drawables; Core 5 keeps the render orders on the drawables.
  const NORMAL = 0;
  const ADD_COMPATIBLE = 1;
  const MULTIPLY_COMPATIBLE = 2;
  const OVERLAY = 12;
  const ATOP = 1 << 8;

  function core6({ offscreens = 0, blendModes = [NORMAL, NORMAL, NORMAL] } = {}) {
    class Model {
      constructor(moc) {
        this.ok = moc !== "broken";
        // Past the drawables the view runs on into other data, here a mode the engine cannot draw.
        this.drawables = { count: 3, blendModes: new Int32Array([...blendModes, OVERLAY, OVERLAY, OVERLAY]) };
        this.offscreens = { count: offscreens };
        this.renderOrders = new Int32Array([2, 0, 1, ...Array(offscreens).fill(3)]);
      }
      static fromMoc(moc) {
        const model = new Model(moc);
        return model.ok ? model : null;
      }
      getRenderOrders() {
        return this.renderOrders;
      }
    }
    return {
      Model,
      ColorBlendType_Normal: NORMAL,
      ColorBlendType_AddCompatible: ADD_COMPATIBLE,
      ColorBlendType_MultiplyCompatible: MULTIPLY_COMPATIBLE,
      AlphaBlendType_Over: 0,
    };
  }

  function core5() {
    class Model {
      constructor() {
        this.drawables = { count: 3, renderOrders: new Int32Array([1, 2, 0]) };
      }
      static fromMoc() {
        return new Model();
      }
    }
    return { Model };
  }

  it("gives a Core 6 model's drawables the render orders the engine reads", () => {
    const core = adaptCubismCore(core6());
    const model = core.Model.fromMoc("moc");
    assert.deepEqual([...model.drawables.renderOrders], [2, 0, 1]);
    model.renderOrders = new Int32Array([0, 1, 2]);
    assert.deepEqual([...model.drawables.renderOrders], [0, 1, 2]);
  });

  it("still returns null when the Core cannot make the model", () => {
    const core = adaptCubismCore(core6());
    assert.equal(core.Model.fromMoc("broken"), null);
  });

  it("wraps a Core only once", () => {
    const core = adaptCubismCore(core6());
    const fromMoc = core.Model.fromMoc;
    assert.equal(adaptCubismCore(core).Model.fromMoc, fromMoc);
  });

  it("leaves a Core 5 untouched", () => {
    const core = core5();
    const fromMoc = core.Model.fromMoc;
    adaptCubismCore(core);
    assert.equal(core.Model.fromMoc, fromMoc);
    assert.deepEqual([...core.Model.fromMoc().drawables.renderOrders], [1, 2, 0]);
  });

  it("refuses a model with offscreens, which the engine cannot draw", () => {
    const core = adaptCubismCore(core6({ offscreens: 1 }));
    assert.throws(() => core.Model.fromMoc("moc"), /offscreen/);
  });

  it("accepts the blend modes the engine draws: normal, and Cubism 5.2's add and multiply", () => {
    const core = adaptCubismCore(core6({ blendModes: [NORMAL, ADD_COMPATIBLE, MULTIPLY_COMPATIBLE] }));
    assert.deepEqual([...core.Model.fromMoc("moc").drawables.renderOrders], [2, 0, 1]);
  });

  it("refuses a model with Cubism 5.3 blend modes, which the engine would draw as normal", () => {
    for (const mode of [OVERLAY, NORMAL | ATOP]) {
      const core = adaptCubismCore(core6({ blendModes: [NORMAL, mode, NORMAL] }));
      assert.throws(() => core.Model.fromMoc("moc"), /blend modes/);
    }
  });
});

describe("maxFpsFor", () => {
  it("caps the mascot at 30 frames a second, the rate Live2D motions are made at", () => {
    assert.equal(maxFpsFor("mascot"), 30);
  });

  it("leaves the app window and the phone page unlimited", () => {
    for (const mode of ["app", undefined, null, "", "MASCOT"]) {
      assert.equal(maxFpsFor(mode), 0, String(mode));
    }
  });
});

describe("frameDue", () => {
  /** How many of the frames of one second on a `hz` display a render loop capped at `maxFps` draws. */
  function drawnInASecond(hz, maxFps) {
    let lastS = -Infinity;
    let drawn = 0;
    for (let frame = 0; frame < hz; frame++) {
      if (frameDue(lastS, frame / hz, maxFps)) {
        lastS = frame / hz;
        drawn += 1;
      }
    }
    return drawn;
  }

  it("draws every frame without a cap", () => {
    assert.equal(frameDue(1, 1.001, 0), true);
    assert.equal(drawnInASecond(144, 0), 144);
  });

  it("draws every other frame of a 60 Hz display at 30 fps, despite a little jitter", () => {
    assert.equal(drawnInASecond(60, 30), 30);
    assert.equal(frameDue(0, 0.0325, 30), true);
    assert.equal(frameDue(0, 0.0167, 30), false);
  });

  it("never draws more than 30 frames a second on a faster display", () => {
    for (const hz of [75, 120, 144, 165, 240]) {
      const drawn = drawnInASecond(hz, 30);
      assert.ok(drawn <= 30 && drawn >= 24, `${hz} Hz: ${drawn} frames`);
    }
  });

  it("draws the first frame", () => {
    assert.equal(frameDue(-Infinity, 0, 30), true);
  });
});
