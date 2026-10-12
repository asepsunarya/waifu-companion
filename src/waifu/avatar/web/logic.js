// Pure motion and timing helpers for the avatar page. No DOM or three.js here, so `node --test` covers them.

export const BLINK_DURATION_S = 0.15;
const BLINK_CLOSE_S = 0.05;

export const BREATH_PERIOD_S = 4;

// The sway mixes 6, 8 and 12 s waves, which line up again every 24 s.
export const SWAY_PERIOD_S = 24;
const SWAY_AMPLITUDE_RAD = (1.5 * Math.PI) / 180;

const VISEME_COUNT = 5;
const MIN_BLINK_DELAY_S = 2;
const MAX_BLINK_DELAY_S = 6;

// Cyrene's emotion names → VRM expression names. three-vrm maps the VRM 0.x presets
// joy/sorrow/fun to happy/sad/relaxed; Surprised is a custom expression of the Tsukuyomi-chan model.
const EMOTION_EXPRESSIONS = {
  neutral: "neutral",
  happy: "happy",
  sad: "sad",
  angry: "angry",
  surprised: "Surprised",
  relaxed: "relaxed",
};
const EMOTION_WEIGHT = 1;

const FRAMING_FOV_DEG = 30;
// Fractions of the head bone height: the top of the hair, the lowest point in view, and the
// margin around the framed span.
const FRAMINGS = {
  full: { top: 1.2, bottom: -0.03, margin: 1.06 },
  upper: { top: 1.2, bottom: 0.55, margin: 1.1 },
};

/** Viseme weights [aa, ih, ou, ee, oh] at `elapsedS` into a track of frames `1/fps` apart. */
export function sampleVisemes(track, fps, elapsedS) {
  const silent = new Array(VISEME_COUNT).fill(0);
  if (!(fps > 0) || track.length === 0) return silent;
  const position = elapsedS * fps;
  if (!(position >= 0) || position >= track.length) return silent;
  const index = Math.floor(position);
  const fraction = position - index;
  const from = track[index];
  const to = track[index + 1] ?? silent;
  return from.map((weight, i) => weight + (to[i] - weight) * fraction);
}

/** Seconds until the next blink, 2–6 s, from a `random()` source returning [0, 1). */
export function nextBlinkDelay(random) {
  return MIN_BLINK_DELAY_S + (MAX_BLINK_DELAY_S - MIN_BLINK_DELAY_S) * random();
}

/** Eyelid closure 0..1 at `phaseS` into a blink: closes quickly, opens a little slower. */
export function blinkWeight(phaseS) {
  if (phaseS <= 0 || phaseS >= BLINK_DURATION_S) return 0;
  if (phaseS <= BLINK_CLOSE_S) return phaseS / BLINK_CLOSE_S;
  return 1 - (phaseS - BLINK_CLOSE_S) / (BLINK_DURATION_S - BLINK_CLOSE_S);
}

/** Frame-rate independent exponential smoothing of `current` toward `target`. */
export function approach(current, target, dt, speed) {
  if (dt <= 0) return current;
  return current + (target - current) * (1 - Math.exp(-speed * dt));
}

/** Target weight of every emotion expression for `emotion` (unknown → neutral). */
export function emotionTargets(emotion) {
  const active = EMOTION_EXPRESSIONS[emotion] ?? EMOTION_EXPRESSIONS.neutral;
  const targets = {};
  for (const expression of Object.values(EMOTION_EXPRESSIONS)) {
    targets[expression] = expression === active ? EMOTION_WEIGHT : 0;
  }
  return targets;
}

/** Breathing phase 0 (exhaled) .. 1 (inhaled), one breath per BREATH_PERIOD_S. */
export function breathOffset(timeS) {
  return (1 - Math.cos((2 * Math.PI * timeS) / BREATH_PERIOD_S)) / 2;
}

/** Slow idle sway of the upper body in radians: `x` leans forward/back, `z` side to side. */
export function idleSway(timeS) {
  const wave = (periodS, phase = 0) => Math.sin((2 * Math.PI * timeS) / periodS + phase);
  return {
    x: SWAY_AMPLITUDE_RAD * (0.5 * wave(6, 0.7) + 0.5 * wave(12)),
    z: SWAY_AMPLITUDE_RAD * (0.6 * wave(8) + 0.4 * wave(12, 1.3)),
  };
}

/**
 * When a `listening` state that arrived at `nowS` ends: after its follow-up window, at once
 * for a window of 0 or less, and only with the next state when it has no window.
 */
export function listeningEndS(followupS, nowS) {
  if (typeof followupS !== "number" || Number.isNaN(followupS)) return Infinity;
  return nowS + Math.max(followupS, 0);
}

/** When a speak track received at `nowS` started: `elapsedS` earlier for a replayed one. */
export function speechStartS(elapsedS, nowS) {
  return Number.isFinite(elapsedS) && elapsedS > 0 ? nowS - elapsedS : nowS;
}

/** The avatar window's title: never just her name, which is the title of Cyrene's console. */
export function avatarTitle(name) {
  return `Avatar ${name}`;
}

/** Camera `fov` (degrees), `position` and `target` ([x, y, z]) for "full" or "upper" framing. */
export function framingFor(name, headY) {
  const framing = FRAMINGS[name] ?? FRAMINGS.full;
  const top = framing.top * headY;
  const bottom = framing.bottom * headY;
  const centreY = (top + bottom) / 2;
  const halfHeight = ((top - bottom) / 2) * framing.margin;
  const distance = halfHeight / Math.tan(((FRAMING_FOV_DEG / 2) * Math.PI) / 180);
  return {
    fov: FRAMING_FOV_DEG,
    position: [0, centreY, distance],
    target: [0, centreY, 0],
  };
}
