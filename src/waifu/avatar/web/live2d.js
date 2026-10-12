// Renders Cyrene's Live2D model (PixiJS 8 + untitled-pixi-live2d-engine, Cubism 5) on the avatar page.
//
// Renderer interface, the same for avatar.js (VRM). Each renderer module exports `createRenderer(config)`, where
// `config` is the page's /config.json; it resolves to the renderer below once the model is on screen, and
// rejects when the model cannot be shown (after putting an Indonesian message on the page). index.html imports
// the module of `config.engine`, then calls `startInteractions({renderer, config})` from interaction.js (when
// that module loads), then opens /events: state, speak and emotion events go to the renderer, and every event
// also goes to the `handleEvent(event)` of the object startInteractions returned (speak events count as
// activity). Points are CSS pixels relative to the canvas's top-left corner (pointer clientX/clientY, or the
// mascot's cursor feed, which may lie outside the window).
//   setEmotion(name, holdS = 4)  one of the six emotions, back to neutral after holdS seconds
//   speak(event)                 a speak event: the mouth follows its visemes from when it arrived (minus its
//                                `elapsed`), and its emotion shows until the speech ends plus a short tail
//   setState(state, followupS)   idle | listening | thinking | speaking; listening ends after followupS seconds
//                                (logic.listeningEndS)
//   lookAt(point)                look toward the cursor {x, y}; null: straight ahead
//   setIdlePhase(phase)          "active" | "bored" (logic.idlePhase); bored yawns every 45-90 s
//   hitTest(x, y)                "head" | "body" | null
//   isOpaqueAt(x, y)             whether a visible pixel of her is there (the mascot lets clicks through elsewhere)
//   canvas                       the <canvas> she is drawn on
//
// The engine updates the model while rendering (CubismInternalModel.update): motions, then saveParameters(),
// then eye blink, focus, breath, physics, and the "beforeModelUpdate" event, then the Core's update and
// loadParameters(). Values set on "afterMotionUpdate" (before the save) are what blink, breath and physics build
// on, so the head angles drive the hair and clothes physics; the eyes are set on "beforeModelUpdate", after the
// blink, so an emotion scales the blink instead of fighting it. Both are written again every frame.
import { Application, extensions, UPDATE_PRIORITY } from "pixi.js";
import { configureCubismSDK, Live2DModel, Live2DPlugin } from "untitled-pixi-live2d-engine/cubism";

import {
  adaptCubismCore,
  approach,
  cursorLook,
  listeningEndS,
  live2dEmotionParams,
  live2dHeadBox,
  live2dMouth,
  live2dRegionAt,
  mapToRange,
  sampleVisemes,
  speechStartS,
} from "./logic.js";

const MAX_RESOLUTION = 2;
const MAX_FRAME_DT_S = 0.1;
// The Core's heap holds the moc and the model. The engine asks for Live2D's minimum, 16 MB; Cyrene's moc3 alone is
// 2 MB, so give it room rather than risk a model that stops updating.
const CUBISM_MEMORY_MB = 64;

const DEFAULT_EMOTION_HOLD_S = 4;
const SPEAK_EMOTION_TAIL_S = 1.5;

const EMOTION_SPEED = 6;
const MOUTH_SPEED = 30;
const LOOK_SPEED = 5;
const POSE_SPEED = 4;

// Framing: the part of her visible height (0 = top of the hair, 1 = feet) that fills the window.
const FRAMINGS = {
  full: { top: 0, bottom: 1 },
  upper: { top: 0, bottom: 0.5 },
};
const FRAME_MARGIN = 0.04;
const MIN_VISIBLE_OPACITY = 0.01;
const FRAMING_ATTEMPTS = 10;

// Hit regions (SPEC, logic.live2dHeadBox): her head is measured on the model at load, from the drawables that turn
// with it down to her chin; the rest of her bounds is her body where a pixel of her is drawn (a click on the
// empty space beside her legs pokes nobody). A full-body model's bounds include whatever hangs from her, so no
// fixed share of them tracks her head: on Cyrene20251105 the top 28 % reached down over her chest.
const HEAD_TURN_PARAMS = ["ParamAngleX", "ParamAngleY"];
const MOUTH_PARAMS = ["ParamMouthOpenY"];
// How far (in the model's canvas pixels) a vertex must move to count as moved by a parameter.
const MOVED_PX = 0.5;
// Where she looks from when the model gives no head to measure: about where a full-body model's face is.
const UNMEASURED_FACE_Y = 0.14;
// The cursor offset at which her head is turned about three quarters of the way, as a share of her height.
const LOOK_DISTANCE = 0.35;
// A pixel at least this opaque (0-255) is part of her; fainter ones, like soft hair edges, let clicks through.
const OPAQUE_ALPHA = 26;

// Normalized poses (0 = the model's default) added on top of the face and the look.
const THINKING_POSE = { angleY: 0.15, angleZ: 0.3, eyeX: 0.4, eyeY: 0.7 };
const LISTENING_POSE = { angleZ: -0.12, browY: 0.25 };
// A face that already shapes the eyes this much (eye smile) is not blinked over.
const EYES_BUSY_SMILE = 0.3;

// Bored: a slow yawn (mouth wide and round, eyes half closed, head back and aside) soon after she gets bored,
// then every 45-90 s.
const YAWN_POSE = { mouthOpen: 0.9, mouthForm: -0.6, eyeOpen: 0.45, browY: 0.3, angleY: 0.25, angleZ: -0.2 };
const FIRST_YAWN_DELAY_S = 2;
const YAWN_MIN_GAP_S = 45;
const YAWN_MAX_GAP_S = 90;
const YAWN_OPEN_S = 1;
const YAWN_CLOSE_S = 1.2;
const YAWN_S = 3.4;
// Follows the yawn's own smooth curve closely, and eases it away when something interrupts it.
const YAWN_FOLLOW_SPEED = 12;

const EYE_OPEN_PARAMS = ["ParamEyeLOpen", "ParamEyeROpen"];
const NO_LOOK = { angleX: 0, angleY: 0, bodyX: 0, eyeX: 0, eyeY: 0 };

const messageElement = document.getElementById("message");

const nowS = () => performance.now() / 1000;
const clamp = (value, low, high) => Math.min(Math.max(value, low), high);

function showMessage(text) {
  messageElement.textContent = text;
  messageElement.hidden = false;
}

function hideMessage() {
  messageElement.hidden = true;
}

/** Yawn strength 0..1 at `phaseS` into a yawn: opens over YAWN_OPEN_S, holds, closes over YAWN_CLOSE_S. */
function yawnWeight(phaseS) {
  if (!(phaseS > 0) || phaseS >= YAWN_S) return 0;
  const t = Math.min(phaseS / YAWN_OPEN_S, (YAWN_S - phaseS) / YAWN_CLOSE_S, 1);
  return t * t * (3 - 2 * t);
}

// --- Parameters ----------------------------------------------------------------------------------

/** The model's parameters by id, with their index, range and default value from the Core. */
function parameterTable(coreModel) {
  const table = new Map();
  for (let index = 0; index < coreModel.getParameterCount(); index++) {
    table.set(coreModel.getParameterId(index).getString().s, {
      index,
      min: coreModel.getParameterMinimumValue(index),
      max: coreModel.getParameterMaximumValue(index),
      neutral: coreModel.getParameterDefaultValue(index),
    });
  }
  return table;
}

/** A signed normalized value (-1..1, 0 = the parameter's default) on the parameter's own range. */
function signedValue(param, value) {
  return param.neutral + mapToRange(value, param.min - param.neutral, param.max - param.neutral);
}

/**
 * An amount (0..1) on the parameter's own range, from its default toward the end farther away from it: an eye
 * smile runs 0..1, while Cubism's standard ParamEyeBallForm (pupils shrinking) runs -1..0.
 */
function amountValue(param, value) {
  const upward = param.max - param.neutral >= param.neutral - param.min;
  return signedValue(param, upward ? value : -value);
}

// --- Model ---------------------------------------------------------------------------------------

/** The drawables visible at rest: shown and not (nearly) transparent. */
function visibleDrawables(coreModel) {
  const indexes = [];
  for (let index = 0; index < coreModel.getDrawableCount(); index++) {
    if (!coreModel.getDrawableDynamicFlagIsVisible(index)) continue;
    if (coreModel.getDrawableOpacity(index) < MIN_VISIBLE_OPACITY) continue;
    indexes.push(index);
  }
  return indexes;
}

/** A {left, top, width, height} box in the model's canvas pixels (its vertices' space) in its local space. */
function toLocal(internalModel, { left, top, width, height }) {
  // The engine's layout transform only scales and moves (setupCubismLayoutMatrix).
  const topLeft = internalModel.localTransform.apply({ x: left, y: top });
  const bottomRight = internalModel.localTransform.apply({ x: left + width, y: top + height });
  return {
    x: Math.min(topLeft.x, bottomRight.x),
    y: Math.min(topLeft.y, bottomRight.y),
    width: Math.abs(bottomRight.x - topLeft.x),
    height: Math.abs(bottomRight.y - topLeft.y),
  };
}

/** Her bounds in the model's local space: the union of `drawables`. */
function visibleBounds(internalModel, drawables) {
  const box = { x: 0, y: 0, width: 0, height: 0 };
  let [minX, minY, maxX, maxY] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const index of drawables) {
    internalModel.getDrawableBounds(index, box);
    if (![box.x, box.y, box.width, box.height].every(Number.isFinite)) continue;
    minX = Math.min(minX, box.x);
    minY = Math.min(minY, box.y);
    maxX = Math.max(maxX, box.x + box.width);
    maxY = Math.max(maxY, box.y + box.height);
  }
  if (!(maxX > minX && maxY > minY)) return null;
  return toLocal(internalModel, { left: minX, top: minY, width: maxX - minX, height: maxY - minY });
}

function hasMoved(vertices, rest) {
  return vertices.some((value, i) => Math.abs(value - rest[i]) > MOVED_PX);
}

/**
 * Her head in the model's local space (logic.live2dHeadBox), or null when the model has none to measure. The
 * model is moved to find out which of `drawables` turn with her head (each of HEAD_TURN_PARAMS to both ends of
 * its range) and which move when her mouth opens (MOUTH_PARAMS), all against the same rest pose: the parameters
 * as the engine keeps them between frames (motions and the face, without blink, breath and physics). Every
 * parameter is put back afterwards, so the next frame draws her as before.
 */
function headBounds(internalModel, params, drawables) {
  const { coreModel } = internalModel;
  const saved = Array.from({ length: coreModel.getParameterCount() }, (_, index) =>
    coreModel.getParameterValueByIndex(index),
  );
  const restore = () => {
    saved.forEach((value, index) => coreModel.setParameterValueByIndex(index, value));
    coreModel.update();
  };
  const shapes = () => drawables.map((index) => internalModel.getDrawableVertices(index));
  restore();
  const rest = shapes();
  const movedBy = (ids) => {
    const moved = new Set();
    for (const param of ids.map((id) => params.get(id)).filter(Boolean)) {
      for (const end of [param.min, param.max]) {
        coreModel.setParameterValueByIndex(param.index, end);
        coreModel.update();
        shapes().forEach((vertices, i) => {
          if (hasMoved(vertices, rest[i])) moved.add(i);
        });
        restore();
      }
    }
    return moved;
  };
  const head = movedBy(HEAD_TURN_PARAMS);
  const mouth = movedBy(MOUTH_PARAMS);
  const box = live2dHeadBox(rest.map((vertices, i) => ({ vertices, head: head.has(i), mouth: mouth.has(i) })));
  return box && toLocal(internalModel, box);
}

/**
 * Scales and centres the model so the framed part of `bounds` fits the window. Returns `toCanvas(box)`, which
 * puts a box of the model's local space ({x, y, width, height}) on the canvas ({left, top, width, height}).
 */
function placeModel(model, bounds, framingName) {
  const framing = FRAMINGS[framingName] ?? FRAMINGS.full;
  const width = window.innerWidth;
  const height = window.innerHeight;
  const margin = height * FRAME_MARGIN;
  const spanTop = bounds.y + framing.top * bounds.height;
  const spanHeight = (framing.bottom - framing.top) * bounds.height;
  const scale = Math.max(Math.min((width - 2 * margin) / bounds.width, (height - 2 * margin) / spanHeight), 0);
  const pivot = { x: bounds.x + bounds.width / 2, y: spanTop + spanHeight / 2 };
  model.scale.set(scale);
  model.pivot.set(pivot.x, pivot.y);
  model.position.set(width / 2, height / 2);
  return (box) => ({
    left: width / 2 + (box.x - pivot.x) * scale,
    top: height / 2 + (box.y - pivot.y) * scale,
    width: box.width * scale,
    height: box.height * scale,
  });
}

/** Resolves after the next frame has been drawn, when the model's drawables have their deformed vertices. */
function nextDrawnFrame(ticker) {
  return new Promise((resolve) => ticker.addOnce(resolve, null, UPDATE_PRIORITY.UTILITY));
}

async function createApplication(canvas) {
  // The plugin has to be registered before the renderer exists. The canvas stays transparent (the mascot shows
  // the desktop, the app window the page's gradient); the engine draws premultiplied alpha, the context's default.
  // The drawing buffer is kept so isOpaqueAt can read the last frame at any time.
  extensions.add(Live2DPlugin);
  const app = new Application();
  await app.init({
    canvas,
    preference: ["webgl"],
    backgroundAlpha: 0,
    premultipliedAlpha: true,
    preserveDrawingBuffer: true,
    antialias: true,
    resizeTo: window,
    autoDensity: true,
    resolution: Math.min(window.devicePixelRatio || 1, MAX_RESOLUTION),
  });
  return app;
}

// --- Renderer ------------------------------------------------------------------------------------

export async function createRenderer(config) {
  const canvas = document.getElementById("stage");

  let app;
  try {
    app = await createApplication(canvas);
  } catch (error) {
    showMessage("WebGL tidak tersedia di jendela ini, jadi avatar tidak bisa ditampilkan.");
    throw error;
  }

  let model;
  try {
    configureCubismSDK({ memorySizeMB: CUBISM_MEMORY_MB });
    adaptCubismCore(window.Live2DCubismCore);
    model = await Live2DModel.from(config.model, { ticker: app.ticker, autoHitTest: false, autoFocus: false });
  } catch (error) {
    showMessage(
      `Model Live2D tidak bisa dimuat dari ${config.model}. ` +
        "Periksa avatar.model di config.yaml lalu jalankan ulang Cyrene.",
    );
    throw error;
  }

  const internalModel = model.internalModel;
  const coreModel = internalModel.coreModel;
  const params = parameterTable(coreModel);
  const avatar = {
    state: "idle",
    listeningUntilS: Infinity,
    emotion: "neutral",
    emotionUntilS: Infinity,
    face: live2dEmotionParams("neutral"),
    speech: null,
    mouth: { open: 0, form: 0 },
    cursor: null,
    look: { angleX: 0, angleY: 0, angleZ: 0, bodyX: 0, eyeX: 0, eyeY: 0 },
    thinking: 0,
    listening: 0,
    bored: false,
    nextYawnS: Infinity,
    yawnStartS: -Infinity,
    yawn: 0,
    // Her bounds and her head (null when the model has none) on the canvas.
    box: null,
    headBox: null,
  };

  function setParam(param, value) {
    coreModel.setParameterValueByIndex(param.index, clamp(value, param.min, param.max));
  }

  /** Sets parameter `id` (when the model has it) to a normalized `value` through signedValue or amountValue. */
  function drive(id, value, toRange) {
    const param = params.get(id);
    if (param) setParam(param, toRange(param, value));
  }

  function setEmotion(name, holdS = DEFAULT_EMOTION_HOLD_S) {
    avatar.emotion = name;
    avatar.emotionUntilS = nowS() + holdS;
  }

  function currentState(time) {
    if (avatar.state === "listening" && time >= avatar.listeningUntilS) avatar.state = "idle";
    return avatar.state;
  }

  function faceCenter() {
    const box = avatar.headBox;
    if (box) return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
    return { x: avatar.box.left + avatar.box.width / 2, y: avatar.box.top + avatar.box.height * UNMEASURED_FACE_Y };
  }

  // --- Per frame: eased targets (before the render), then the parameters (inside the engine's update).

  // Only a bored avatar with nothing else to do yawns; anything else ends a yawn at once (eased).
  function updateYawn(time, dt, state) {
    const calm = avatar.bored && state === "idle" && !avatar.speech;
    if (!calm) {
      avatar.yawnStartS = -Infinity;
    } else if (time >= avatar.nextYawnS) {
      avatar.yawnStartS = time;
      avatar.nextYawnS = time + YAWN_MIN_GAP_S + (YAWN_MAX_GAP_S - YAWN_MIN_GAP_S) * Math.random();
    }
    avatar.yawn = approach(avatar.yawn, yawnWeight(time - avatar.yawnStartS), dt, YAWN_FOLLOW_SPEED);
  }

  function updateMouth(time, dt) {
    let target = { open: 0, form: 0 };
    const speech = avatar.speech;
    if (speech) {
      const elapsedS = time - speech.startS;
      if (elapsedS >= speech.duration) avatar.speech = null;
      else target = live2dMouth(sampleVisemes(speech.visemes, speech.fps, elapsedS));
    }
    avatar.mouth.open = approach(avatar.mouth.open, target.open, dt, MOUTH_SPEED);
    avatar.mouth.form = approach(avatar.mouth.form, target.form, dt, MOUTH_SPEED);
  }

  // The cursor sets where she looks; thinking takes over (head tilted, eyes up and aside), and listening and
  // yawning tilt the head on top.
  function updateLook(dt) {
    const { thinking, listening, yawn, box } = avatar;
    const cursor = avatar.cursor && box ? cursorLook(avatar.cursor, faceCenter(), box.height * LOOK_DISTANCE) : NO_LOOK;
    const follow = 1 - thinking;
    const target = {
      angleX: cursor.angleX * follow,
      angleY: cursor.angleY * follow + THINKING_POSE.angleY * thinking + YAWN_POSE.angleY * yawn,
      angleZ: THINKING_POSE.angleZ * thinking + LISTENING_POSE.angleZ * listening + YAWN_POSE.angleZ * yawn,
      bodyX: cursor.bodyX * follow,
      eyeX: cursor.eyeX * follow + THINKING_POSE.eyeX * thinking,
      eyeY: cursor.eyeY * follow + THINKING_POSE.eyeY * thinking,
    };
    for (const [name, value] of Object.entries(target)) {
      avatar.look[name] = approach(avatar.look[name], value, dt, LOOK_SPEED);
    }
  }

  function update(ticker) {
    const time = nowS();
    const dt = Math.min(ticker.deltaMS / 1000, MAX_FRAME_DT_S);
    const state = currentState(time);
    avatar.thinking = approach(avatar.thinking, state === "thinking" ? 1 : 0, dt, POSE_SPEED);
    avatar.listening = approach(avatar.listening, state === "listening" ? 1 : 0, dt, POSE_SPEED);
    if (time >= avatar.emotionUntilS) setEmotion("neutral", Infinity);
    const targets = live2dEmotionParams(avatar.emotion);
    for (const [id, target] of Object.entries(targets)) {
      avatar.face[id] = approach(avatar.face[id], target, dt, EMOTION_SPEED);
    }
    updateYawn(time, dt, state);
    updateMouth(time, dt);
    updateLook(dt);
  }

  // Before the parameters are saved: blink, breath and physics build on these.
  function applyPose() {
    const { look, face, mouth, yawn } = avatar;
    drive("ParamAngleX", look.angleX, signedValue);
    drive("ParamAngleY", look.angleY, signedValue);
    drive("ParamAngleZ", look.angleZ, signedValue);
    drive("ParamBodyAngleX", look.bodyX, signedValue);
    drive("ParamEyeBallX", look.eyeX, signedValue);
    drive("ParamEyeBallY", look.eyeY, signedValue);

    const browLift = LISTENING_POSE.browY * avatar.listening + YAWN_POSE.browY * yawn;
    for (const side of ["L", "R"]) {
      drive(`ParamEye${side}Smile`, face[`ParamEye${side}Smile`], amountValue);
      drive(`ParamBrow${side}Y`, face[`ParamBrow${side}Y`] + browLift, signedValue);
      drive(`ParamBrow${side}Angle`, face[`ParamBrow${side}Angle`], signedValue);
    }
    drive("ParamCheek", face.ParamCheek, amountValue);
    drive("ParamEyeBallForm", face.ParamEyeBallForm, amountValue);

    // While the mouth is open the vowel shapes it; the emotion's mouth shows when it is closed.
    const spokenForm = face.ParamMouthForm + (mouth.form - face.ParamMouthForm) * mouth.open;
    drive("ParamMouthForm", spokenForm + (YAWN_POSE.mouthForm - spokenForm) * yawn, signedValue);
    drive("ParamMouthOpenY", Math.max(mouth.open, YAWN_POSE.mouthOpen * yawn), amountValue);
  }

  // After the blink: the emotion and the yawn scale how open the blink leaves the eyes (the face's eye-open values
  // are factors, neutral 1, clamped to the parameter's range), and a smiling face is not blinked over.
  function applyEyes() {
    const smiling = Math.max(avatar.face.ParamEyeLSmile, avatar.face.ParamEyeRSmile) > EYES_BUSY_SMILE;
    const yawnFactor = 1 - (1 - YAWN_POSE.eyeOpen) * avatar.yawn;
    for (const id of EYE_OPEN_PARAMS) {
      const param = params.get(id);
      if (!param) continue;
      const blink = smiling ? 1 : coreModel.getParameterValueByIndex(param.index);
      setParam(param, blink * avatar.face[id] * yawnFactor);
    }
  }

  internalModel.on("afterMotionUpdate", applyPose);
  internalModel.on("beforeModelUpdate", applyEyes);
  app.ticker.add(update);

  // Frame her and measure her head once a drawn frame has deformed the drawables (the Core places their vertices
  // in its update, which the engine skips on a frame without elapsed time), and only then show the canvas.
  canvas.style.visibility = "hidden";
  app.stage.addChild(model);
  let bounds = null;
  let drawables = [];
  for (let attempt = 0; attempt < FRAMING_ATTEMPTS && !bounds; attempt++) {
    await nextDrawnFrame(app.ticker);
    drawables = visibleDrawables(coreModel);
    bounds = visibleBounds(internalModel, drawables);
  }
  if (!bounds) {
    showMessage(`Model Live2D dari ${config.model} tidak punya bagian yang terlihat.`);
    throw new Error(`${config.model} has no visible drawables`);
  }
  const head = headBounds(internalModel, params, drawables);
  if (!head) {
    console.warn(
      `${config.model} has no head to measure (no drawable both turns with ParamAngleX/Y and moves with ` +
        "ParamMouthOpenY): every touch counts as her body",
    );
  }
  const frame = () => {
    const toCanvas = placeModel(model, bounds, config.framing);
    avatar.box = toCanvas(bounds);
    avatar.headBox = head && toCanvas(head);
  };
  frame();
  window.addEventListener("resize", frame);
  canvas.style.visibility = "";
  hideMessage();

  const pixel = new Uint8Array(4);

  function isOpaqueAt(x, y) {
    const gl = app.renderer.gl;
    const resolution = app.renderer.resolution;
    const px = Math.floor(x * resolution);
    const py = gl.drawingBufferHeight - 1 - Math.floor(y * resolution);
    if (!(px >= 0 && py >= 0 && px < gl.drawingBufferWidth && py < gl.drawingBufferHeight)) return false;
    // Read the canvas itself, restoring whatever framebuffer PixiJS has bound for reading.
    const target = gl.READ_FRAMEBUFFER ?? gl.FRAMEBUFFER;
    const previous = gl.getParameter(gl.READ_FRAMEBUFFER_BINDING ?? gl.FRAMEBUFFER_BINDING);
    gl.bindFramebuffer(target, null);
    gl.readPixels(px, py, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
    gl.bindFramebuffer(target, previous);
    return pixel[3] >= OPAQUE_ALPHA;
  }

  function hitTest(x, y) {
    return live2dRegionAt(x, y, { body: avatar.box, head: avatar.headBox }, isOpaqueAt);
  }

  return {
    canvas,
    setEmotion,
    speak(event) {
      const time = nowS();
      const startS = speechStartS(event.elapsed, time);
      avatar.speech = {
        visemes: Array.isArray(event.visemes) ? event.visemes : [],
        fps: event.fps,
        duration: event.duration ?? 0,
        startS,
      };
      setEmotion(event.emotion, startS + avatar.speech.duration - time + SPEAK_EMOTION_TAIL_S);
    },
    setState(state, followupS) {
      avatar.state = state;
      avatar.listeningUntilS = state === "listening" ? listeningEndS(followupS, nowS()) : Infinity;
    },
    lookAt(point) {
      const valid = point != null && Number.isFinite(point.x) && Number.isFinite(point.y);
      avatar.cursor = valid ? { x: point.x, y: point.y } : null;
    },
    setIdlePhase(phase) {
      const bored = phase === "bored";
      if (bored && !avatar.bored) avatar.nextYawnS = nowS() + FIRST_YAWN_DELAY_S;
      if (!bored) avatar.nextYawnS = Infinity;
      avatar.bored = bored;
    },
    hitTest,
    isOpaqueAt,
  };
}
