// Pure motion, timing and pointer helpers for the avatar page (VRM and Live2D). No DOM, three.js or PixiJS
// here, so `node --test` covers them.

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

// How much each viseme [aa, ih, ou, ee, oh] opens a Live2D mouth (ParamMouthOpenY).
const LIVE2D_MOUTH_OPENING = [1, 0.5, 0.7, 0.6, 0.9];

// Live2D face parameters on a normalized scale where 0 is neutral (mapToRange turns it into the model's
// own range): eye smile and cheek 0..1, brow height and angle -1..1, mouth form -1 (round) .. 1 (wide),
// eyeball form 0..1. Eye open is a factor on the openness the blink leaves (neutral 1). Both sides match.
function live2dFace({
  eyeSmile = 0,
  mouthForm = 0,
  cheek = 0,
  browY = 0,
  browAngle = 0,
  eyeOpen = 1,
  eyeBallForm = 0,
}) {
  return {
    ParamEyeLSmile: eyeSmile,
    ParamEyeRSmile: eyeSmile,
    ParamMouthForm: mouthForm,
    ParamCheek: cheek,
    ParamBrowLY: browY,
    ParamBrowRY: browY,
    ParamBrowLAngle: browAngle,
    ParamBrowRAngle: browAngle,
    ParamEyeLOpen: eyeOpen,
    ParamEyeROpen: eyeOpen,
    ParamEyeBallForm: eyeBallForm,
  };
}

// Brow angle convention: positive pulls the inner ends down (angry), negative raises them (sad).
const LIVE2D_NEUTRAL_FACE = live2dFace({});
const LIVE2D_EMOTION_FACES = new Map([
  ["neutral", LIVE2D_NEUTRAL_FACE],
  ["happy", live2dFace({ eyeSmile: 1, mouthForm: 0.8, cheek: 0.6, browY: 0.3 })],
  ["sad", live2dFace({ browAngle: -0.7, browY: -0.4, mouthForm: -0.6, eyeOpen: 0.85 })],
  ["angry", live2dFace({ browAngle: 0.8, browY: -0.6, eyeOpen: 0.8, mouthForm: -0.5 })],
  // Eyes wider than neutral; mapToRange clamps the result to the parameter's maximum.
  ["surprised", live2dFace({ browY: 0.8, eyeOpen: 1.2, eyeBallForm: 0.6, mouthForm: -0.2 })],
  ["relaxed", live2dFace({ eyeSmile: 0.5, mouthForm: 0.4, cheek: 0.2 })],
]);

// Following the cursor: the body turns with a part of the head's angle, the eyes lead it a little.
const LOOK_BODY_SHARE = 0.35;
const LOOK_EYE_LEAD = 1.2;

// A Live2D head ends this share of its height above the chin, so her neckline never pokes into it while she
// breathes (on Cyrene20251105 her chest starts 0.2 % of her height below her chin at rest).
const CHIN_MARGIN = 0.05;

const BORED_AFTER_S = 180;

// Pointer gestures (px, ms). A press outside the head drags once it moves more than DRAG_SLOP_PX; one on
// the head only once the pointer is off the head more than HEAD_DRAG_PX from the press, so rubbing never
// drags. A rub needs RUB_DIRECTION_CHANGES turns on the head, each after a stroke of RUB_STROKE_PX, so
// jitter is not rubbing. A double click is two clicks whose presses are close in time and place: within
// Windows' default double-click time, so a double-click that Windows takes for one never becomes two pats.
const DRAG_SLOP_PX = 6;
const HEAD_DRAG_PX = 24;
const RUB_DIRECTION_CHANGES = 3;
const RUB_STROKE_PX = 6;
export const DOUBLE_CLICK_MS = 500;
const DOUBLE_CLICK_PX = 12;
const POINTER_TYPES = new Set(["down", "move", "up"]);

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

function clamp(value, low, high) {
  return Math.min(Math.max(value, low), high);
}

function isNumber(value) {
  return typeof value === "number" && !Number.isNaN(value);
}

/**
 * Live2D mouth for one viseme frame [aa, ih, ou, ee, oh]: `open` 0..1 from every vowel, `form` -1 (round:
 * ou, oh) .. 1 (wide: ee, ih). A missing frame or weight counts as a closed mouth.
 */
export function live2dMouth(visemes) {
  const weights = Array.from({ length: VISEME_COUNT }, (_, i) => {
    const weight = Array.isArray(visemes) ? visemes[i] : undefined;
    return isNumber(weight) ? clamp(weight, 0, 1) : 0;
  });
  const [aa, ih, ou, ee, oh] = weights;
  const opening = weights.reduce((total, weight, i) => total + weight * LIVE2D_MOUTH_OPENING[i], 0);
  return {
    open: clamp(opening, 0, 1),
    form: clamp(ee + ih - (ou + oh), -1, 1),
  };
}

/** Target Live2D face parameters for `emotion` (unknown → neutral), on the normalized scale of mapToRange. */
export function live2dEmotionParams(emotion) {
  return { ...(LIVE2D_EMOTION_FACES.get(emotion) ?? LIVE2D_NEUTRAL_FACE) };
}

/**
 * A normalized value (0 = neutral) on a Live2D parameter's [min, max]: for a range around 0, -1..1 → min..max
 * with 0 staying 0; for a range from 0 (or above), 0..1 → min..max; for a range up to 0 (or below),
 * -1..0 → min..max. Clamped to the range; a value that is not a number stays neutral; an invalid range gives 0.
 */
export function mapToRange(value, min, max) {
  if (!Number.isFinite(min) || !Number.isFinite(max) || min > max) return 0;
  const neutral = clamp(0, min, max);
  const normalized = isNumber(value) ? clamp(value, -1, 1) : 0;
  const end = normalized >= 0 ? max : min;
  return neutral + Math.abs(normalized) * (end - neutral);
}

function isPoint(point) {
  return point != null && Number.isFinite(point.x) && Number.isFinite(point.y);
}

/**
 * Where she looks for a `cursor` relative to her `faceCenter` (both {x, y} in screen pixels, y downward):
 * `angleX`/`angleY` (head, positive toward screen right and up), `bodyX`, `eyeX`/`eyeY`, each -1..1. The
 * offset is measured in units of `size` (px) and saturates smoothly (tanh), so a far cursor never snaps her
 * around. A missing or invalid input looks straight ahead.
 */
export function cursorLook(cursor, faceCenter, size) {
  if (!isPoint(cursor) || !isPoint(faceCenter) || !(Number.isFinite(size) && size > 0)) {
    return { angleX: 0, angleY: 0, bodyX: 0, eyeX: 0, eyeY: 0 };
  }
  const angleX = Math.tanh((cursor.x - faceCenter.x) / size);
  const angleY = Math.tanh((faceCenter.y - cursor.y) / size);
  return {
    angleX,
    angleY,
    bodyX: LOOK_BODY_SHARE * angleX,
    eyeX: clamp(LOOK_EYE_LEAD * angleX, -1, 1),
    eyeY: clamp(LOOK_EYE_LEAD * angleY, -1, 1),
  };
}

/** The finite [x, y] pairs of a flat vertex array [x0, y0, x1, y1, ...]; none for anything else. */
function vertexPoints(vertices) {
  const points = [];
  if (!Array.isArray(vertices) && !ArrayBuffer.isView(vertices)) return points;
  for (let i = 0; i + 1 < vertices.length; i += 2) {
    const [x, y] = [vertices[i], vertices[i + 1]];
    if (Number.isFinite(x) && Number.isFinite(y)) points.push([x, y]);
  }
  return points;
}

/**
 * Her head on a Live2D model, from its drawables at rest: `drawables` is [{vertices: [x0, y0, x1, y1, ...]
 * (y downward), head: whether it turns with her head (ParamAngleX/Y), mouth: whether it moves when her mouth
 * opens (ParamMouthOpenY)}]. Her chin is the lowest point of the head drawables her mouth moves (mouth and jaw);
 * the head is the box around the head drawables' points down to a line CHIN_MARGIN of the head's height above
 * it. Hair, ribbons and anything else that hangs from her head below that line stay her body, and so does
 * everything that does not turn with her head. Returns {left, top, width, height}, or null when no head
 * drawable moves with her mouth: she has no head region then, only a body. Non-finite points are skipped.
 */
export function live2dHeadBox(drawables) {
  if (!Array.isArray(drawables)) return null;
  // A model has some 100 000 vertices: plain loops, no spreading them into Math.min.
  let [chin, top] = [-Infinity, Infinity];
  const points = [];
  for (const drawable of drawables) {
    if (drawable?.head !== true) continue;
    for (const [x, y] of vertexPoints(drawable.vertices)) {
      points.push([x, y]);
      top = Math.min(top, y);
      if (drawable.mouth === true) chin = Math.max(chin, y);
    }
  }
  const bottom = chin - CHIN_MARGIN * (chin - top);
  if (!(bottom > top)) return null;
  let [left, right] = [Infinity, -Infinity];
  for (const [x, y] of points) {
    if (y > bottom) continue;
    left = Math.min(left, x);
    right = Math.max(right, x);
  }
  return { left, top, width: right - left, height: bottom - top };
}

function inBox(x, y, box) {
  return box != null && x >= box.left && x <= box.left + box.width && y >= box.top && y <= box.top + box.height;
}

/**
 * What a canvas point touches on a Live2D model: "head" inside her `head` box (also between strands of hair, so
 * a rub never breaks off), "body" where `isOpaqueAt(x, y)` finds a pixel of her elsewhere inside her `body` box
 * (her bounds), else null. Both boxes are {left, top, width, height} on the canvas; without a head box (see
 * live2dHeadBox) she is all body.
 */
export function live2dRegionAt(x, y, { body, head }, isOpaqueAt) {
  if (!inBox(x, y, body)) return null;
  if (inBox(x, y, head)) return "head";
  return isOpaqueAt(x, y) ? "body" : null;
}

function isPointerSample(sample) {
  return isPoint(sample) && Number.isFinite(sample.t) && POINTER_TYPES.has(sample.type);
}

/** Back-and-forth along one axis: a turn counts once the pointer is RUB_STROKE_PX back from its furthest point. */
class StrokeCounter {
  constructor(start) {
    this.direction = 0;
    this.extreme = start;
    this.turns = 0;
  }

  feed(position) {
    const travel = position - this.extreme;
    if (this.direction === 0) {
      if (Math.abs(travel) >= RUB_STROKE_PX) {
        this.direction = Math.sign(travel);
        this.extreme = position;
      }
    } else if (travel * this.direction > 0) {
      this.extreme = position;
    } else if (-travel * this.direction >= RUB_STROKE_PX) {
      this.turns += 1;
      this.direction = -this.direction;
      this.extreme = position;
    }
  }
}

/** One press of the button: `kind` becomes "drag" (for good) or "rub" as the samples show it. */
class Press {
  constructor(sample, onHead) {
    this.start = sample;
    this.onHead = onHead;
    this.fromHead = onHead(sample.x, sample.y);
    this.kind = null;
    this.strokes = [new StrokeCounter(sample.x), new StrokeCounter(sample.y)];
  }

  follow(sample) {
    if (this.kind === "drag") return;
    const distance = Math.hypot(sample.x - this.start.x, sample.y - this.start.y);
    const inside = this.onHead(sample.x, sample.y);
    if (this.fromHead ? distance > HEAD_DRAG_PX && !inside : distance > DRAG_SLOP_PX) {
      this.kind = "drag";
    } else if (this.fromHead && inside) {
      this.strokes[0].feed(sample.x);
      this.strokes[1].feed(sample.y);
      if (Math.max(this.strokes[0].turns, this.strokes[1].turns) >= RUB_DIRECTION_CHANGES) this.kind = "rub";
    }
  }
}

function isDoubleClick(first, second) {
  const gap = second.t - first.t;
  const distance = Math.hypot(second.x - first.x, second.y - first.y);
  return gap >= 0 && gap <= DOUBLE_CLICK_MS && distance <= DOUBLE_CLICK_PX;
}

/**
 * The gesture of the latest press in `samples` ([{t (ms), x, y (px), type: "down" | "move" | "up"}], oldest
 * first): "drag" or "rub" as soon as the movement shows it (also while the button is held), "click" or
 * "double_click" on release, null while a press is still undecided or there is none. `inHead(x, y)` tells
 * whether a point is on her head (without it every press is a body press). Invalid samples are skipped.
 */
export function classifyPointer(samples, inHead) {
  if (!Array.isArray(samples)) return null;
  const onHead = typeof inHead === "function" ? (x, y) => Boolean(inHead(x, y)) : () => false;
  let press = null;
  let firstClick = null;
  let gesture = null;
  for (const sample of samples.filter(isPointerSample)) {
    if (sample.type === "down") {
      if (press !== null) firstClick = null;
      press = new Press(sample, onHead);
      gesture = null;
    } else if (press !== null) {
      press.follow(sample);
      gesture = press.kind;
      if (sample.type === "up") {
        if (gesture === null) {
          gesture = firstClick !== null && isDoubleClick(firstClick, press.start) ? "double_click" : "click";
        }
        firstClick = gesture === "click" ? press.start : null;
        press = null;
      }
    }
  }
  return gesture;
}

/** "bored" once nobody has moved the mouse, clicked, or talked for BORED_AFTER_S seconds, else "active". */
export function idlePhase(idleS) {
  return isNumber(idleS) && idleS >= BORED_AFTER_S ? "bored" : "active";
}
