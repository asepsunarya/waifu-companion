// Renders Cyrene's VRM avatar (three.js + three-vrm) behind the renderer interface described in live2d.js:
// index.html imports this module for `engine: vrm`, awaits createRenderer(config), and feeds it the events. The module
// holds one renderer at a time: createRenderer may run again only after destroy().
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";

import {
  approach,
  blinkWeight,
  breathOffset,
  cursorLook,
  emotionEndAfterState,
  emotionTargets,
  frameDue,
  framingFor,
  idleSway,
  listeningEndS,
  nextBlinkDelay,
  sampleVisemes,
  speechAfterState,
  speechStartS,
} from "./logic.js";
import { WebGLUnavailableError } from "./recovery.js";

const MODEL_URL = "/model.vrm";
const MAX_PIXEL_RATIO = 2;
const MAX_FRAME_DT_S = 0.1;
const DEFAULT_HEAD_Y = 1.3;

// Viseme order in a speak event → VRM expression names (three-vrm maps the VRM 0.x a/i/u/e/o presets).
const VISEME_EXPRESSIONS = ["aa", "ih", "ou", "ee", "oh"];
const DEFAULT_EMOTION_HOLD_S = 4;
const SPEAK_EMOTION_TAIL_S = 1.5;
// Above this summed weight a non-neutral emotion shapes the eyes, so blinking would fight it.
const EYES_BUSY_WEIGHT = 0.3;

const EMOTION_SPEED = 6;
const MOUTH_SPEED = 30;
const POSE_SPEED = 4;
const LOOK_SPEED = 5;
const YAWN_SPEED = 4;

const DEG = Math.PI / 180;
// Pose angles in VRM 1.0 normalized-bone conventions (the model faces +Z); VRM 0.x bones get X/Z mirrored.
const UPPER_ARM_DOWN = 70 * DEG;
const UPPER_ARM_FORWARD = 4 * DEG;
const ELBOW_BEND = 15 * DEG;
const FOREARM_IN = 10 * DEG;
const BREATH_CHEST_PITCH = 1.2 * DEG;
const BREATH_SHOULDER_LIFT = 1 * DEG;
const LISTEN_LEAN = 5 * DEG;
const THINK_HEAD_TILT = 9 * DEG;
const THINK_HEAD_PITCH = 5 * DEG;
const LOOK_DRIFT_M = 0.05;
const THINK_LOOK_UP_M = 0.45;
const THINK_LOOK_SIDE_M = 0.2;

// Following the cursor (logic.cursorLook, -1..1 per axis): a cursor half the canvas height away from her face
// turns her about three quarters of the way. The neck takes part of the head's turn, the spine a little of
// the body's, and the eyes lead the head.
const LOOK_SPAN_OF_HEIGHT = 0.5;
const HEAD_MAX_YAW = 28 * DEG;
const HEAD_MAX_PITCH = 18 * DEG;
const NECK_SHARE = 0.4;
const SPINE_MAX_YAW = 15 * DEG;
const EYE_MAX_YAW = 35 * DEG;
const EYE_MAX_PITCH = 25 * DEG;
const NO_LOOK = cursorLook(null);

// Hit regions, in neck lengths (neck bone to head bone): her head is a circle from the top of her hair to her
// chin around a point above the head bone (measured on Tsukuyomi-chan); her torso a capsule from hips to neck
// whose radius is a share of the span between her shoulders.
const HEAD_CENTRE_RISE = 1.5;
const HEAD_RADIUS = 2.2;
const TORSO_RADIUS_OF_SHOULDERS = 0.55;
// A pixel at least this opaque (of 255) is her; fainter ones (soft edges) let clicks through.
const OPAQUE_ALPHA = 32;

// Bored: a slow yawn (mouth open, eyes half closed, head tilted back and aside) soon after she gets bored,
// then every 45-90 s while nothing happens.
const YAWN_S = 3.5;
const FIRST_YAWN_DELAY_S = 4;
const MIN_YAWN_GAP_S = 45;
const MAX_YAWN_GAP_S = 90;
const YAWN_MOUTH = 0.85;
const YAWN_EYES = 0.5;
const YAWN_HEAD_TILT = 8 * DEG;
const YAWN_HEAD_PITCH = 6 * DEG;

const messageElement = document.getElementById("message");

const nowS = () => performance.now() / 1000;

function showMessage(text) {
  messageElement.textContent = text;
  messageElement.hidden = false;
}

function hideMessage() {
  messageElement.hidden = true;
}

const avatar = {
  vrm: null,
  expressionNames: new Map(),
  mirror: 1,
  framing: "full",
  state: "idle",
  listeningUntilS: Infinity,
  emotion: "neutral",
  emotionUntilS: Infinity,
  emotionWeights: emotionTargets("neutral"),
  speech: null,
  mouth: VISEME_EXPRESSIONS.map(() => 0),
  thinking: 0,
  listening: 0,
  blinkStartS: -Infinity,
  nextBlinkS: nowS() + nextBlinkDelay(Math.random),
  cursor: null,
  look: { ...NO_LOOK },
  bored: false,
  yawn: 0,
  yawnStartS: -Infinity,
  nextYawnS: Infinity,
};

// --- Scene ---------------------------------------------------------------------------------------

// The canvas is always transparent: the mascot window shows the desktop through it, the app window
// shows the page's gradient.
function createWebGLRenderer() {
  const webgl = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  webgl.outputColorSpace = THREE.SRGBColorSpace;
  webgl.setClearColor(0x000000, 0);
  return webgl;
}

// The page's #stage canvas, the renderer drawing on it, and the most frames a second it draws (0: no limit).
let canvas;
let webgl;
let maxFps = 0;

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 20);
const lookTarget = new THREE.Object3D();
const cameraTarget = new THREE.Vector3();
const pickTarget = new THREE.WebGLRenderTarget(1, 1);
const pickedPixel = new Uint8Array(4);

scene.add(new THREE.AmbientLight(0xffffff, 1.2));
const keyLight = new THREE.DirectionalLight(0xffffff, 1.8);
keyLight.position.set(0.6, 1.8, 2.2);
scene.add(keyLight);

function resize() {
  const width = window.innerWidth;
  const height = window.innerHeight;
  webgl.setPixelRatio(Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO));
  webgl.setSize(width, height, false);
  camera.aspect = width / Math.max(height, 1);
  camera.updateProjectionMatrix();
}

function headHeight(vrm) {
  const head = vrm.humanoid.getNormalizedBoneNode("head");
  if (!head) return DEFAULT_HEAD_Y;
  vrm.scene.updateMatrixWorld(true);
  return head.getWorldPosition(new THREE.Vector3()).y;
}

function applyFraming() {
  const headY = avatar.vrm ? headHeight(avatar.vrm) : DEFAULT_HEAD_Y;
  const framing = framingFor(avatar.framing, headY);
  camera.fov = framing.fov;
  camera.position.fromArray(framing.position);
  cameraTarget.fromArray(framing.target);
  camera.lookAt(cameraTarget);
  camera.updateProjectionMatrix();
}

// --- Model ---------------------------------------------------------------------------------------

function expressionNameMap(vrm) {
  const byLowerName = new Map();
  for (const expression of vrm.expressionManager?.expressions ?? []) {
    byLowerName.set(expression.expressionName.toLowerCase(), expression.expressionName);
  }
  return byLowerName;
}

function setExpression(name, weight) {
  const resolved = avatar.expressionNames.get(name.toLowerCase());
  if (resolved) avatar.vrm.expressionManager.setValue(resolved, weight);
}

async function loadModel(url) {
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  const gltf = await loader.loadAsync(url, (progress) => {
    if (progress.total > 0) showMessage(`Memuat avatar… ${Math.round((100 * progress.loaded) / progress.total)}%`);
  });
  const vrm = gltf.userData.vrm;
  if (!vrm) throw new Error(`${url} is a glTF file without VRM data`);

  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.combineSkeletons(gltf.scene);
  VRMUtils.combineMorphs(vrm);
  VRMUtils.rotateVRM0(vrm);
  vrm.scene.traverse((object) => {
    object.frustumCulled = false;
  });
  if (vrm.lookAt) vrm.lookAt.target = lookTarget;

  avatar.vrm = vrm;
  avatar.mirror = vrm.meta?.metaVersion === "0" ? -1 : 1;
  avatar.expressionNames = expressionNameMap(vrm);
  scene.add(vrm.scene);

  applyPose(nowS());
  vrm.humanoid.update();
  vrm.scene.updateMatrixWorld(true);
  vrm.springBoneManager?.reset();
  applyFraming();
  hideMessage();
}

// --- Where she is on the canvas --------------------------------------------------------------------

function boneWorldPosition(name) {
  const bone = avatar.vrm.humanoid.getNormalizedBoneNode(name);
  return bone ? bone.getWorldPosition(new THREE.Vector3()) : null;
}

/** A world point in CSS pixels from the canvas's top-left corner. */
function toCanvas(point) {
  const ndc = point.clone().project(camera);
  return { x: ((ndc.x + 1) / 2) * canvas.clientWidth, y: ((1 - ndc.y) / 2) * canvas.clientHeight };
}

function headRegion() {
  const head = boneWorldPosition("head");
  const neckToHead = head.clone().sub(boneWorldPosition("neck") ?? boneWorldPosition("spine"));
  const centre = head.clone().addScaledVector(neckToHead, HEAD_CENTRE_RISE);
  const cameraRight = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
  const edge = centre.clone().addScaledVector(cameraRight, neckToHead.length() * HEAD_RADIUS);
  const centrePx = toCanvas(centre);
  const edgePx = toCanvas(edge);
  return { centre: centrePx, radius: Math.hypot(edgePx.x - centrePx.x, edgePx.y - centrePx.y) };
}

function torsoRegion() {
  const left = toCanvas(boneWorldPosition("leftUpperArm"));
  const right = toCanvas(boneWorldPosition("rightUpperArm"));
  return {
    from: toCanvas(boneWorldPosition("hips")),
    to: toCanvas(boneWorldPosition("neck") ?? boneWorldPosition("head")),
    radius: Math.hypot(left.x - right.x, left.y - right.y) * TORSO_RADIUS_OF_SHOULDERS,
  };
}

function distanceToSegment(point, from, to) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const lengthSq = dx * dx + dy * dy;
  const along = lengthSq > 0 ? ((point.x - from.x) * dx + (point.y - from.y) * dy) / lengthSq : 0;
  const t = Math.min(Math.max(along, 0), 1);
  return Math.hypot(point.x - (from.x + t * dx), point.y - (from.y + t * dy));
}

// Renders only the pixel under the point (a 1x1 view offset into a 1x1 target) and reads its alpha. A
// Raycaster through the skinned meshes cost 20-65 ms per call on the CPU (every vertex skinned again); this
// costs about a millisecond and also honours the alpha-cut edges of her hair.
function isOpaqueAt(x, y) {
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (!avatar.vrm || !(x >= 0 && y >= 0 && x < width && y < height)) return false;
  camera.setViewOffset(width, height, x, y, 1, 1);
  webgl.setRenderTarget(pickTarget);
  webgl.render(scene, camera);
  webgl.setRenderTarget(null);
  camera.clearViewOffset();
  webgl.readRenderTargetPixels(pickTarget, 0, 0, 1, 1, pickedPixel);
  return pickedPixel[3] >= OPAQUE_ALPHA;
}

function hitTest(x, y) {
  if (!avatar.vrm) return null;
  const head = headRegion();
  if (Math.hypot(x - head.centre.x, y - head.centre.y) <= head.radius) return "head";
  const torso = torsoRegion();
  // Arms, legs, skirt and hair outside the torso count as her body too.
  if (distanceToSegment({ x, y }, torso.from, torso.to) <= torso.radius || isOpaqueAt(x, y)) return "body";
  return null;
}

// --- Motion --------------------------------------------------------------------------------------

function setBone(name, x, y, z) {
  const bone = avatar.vrm.humanoid.getNormalizedBoneNode(name);
  if (bone) bone.rotation.set(x * avatar.mirror, y, z * avatar.mirror);
}

function applyPose(timeS) {
  const breath = breathOffset(timeS);
  const sway = idleSway(timeS);
  // Thinking looks up and aside by itself, so the cursor gives way to it.
  const follow = 1 - avatar.thinking;
  const { angleX, angleY, bodyX } = avatar.look;
  const lookYaw = HEAD_MAX_YAW * angleX * follow;
  const lookPitch = -HEAD_MAX_PITCH * angleY * follow;
  setBone("spine", sway.x + LISTEN_LEAN * avatar.listening, SPINE_MAX_YAW * bodyX * follow, sway.z);
  setBone("chest", -BREATH_CHEST_PITCH * breath, 0, 0);
  setBone("leftShoulder", 0, 0, BREATH_SHOULDER_LIFT * breath);
  setBone("rightShoulder", 0, 0, -BREATH_SHOULDER_LIFT * breath);
  setBone("leftUpperArm", UPPER_ARM_FORWARD, 0, -UPPER_ARM_DOWN);
  setBone("rightUpperArm", UPPER_ARM_FORWARD, 0, UPPER_ARM_DOWN);
  setBone("leftLowerArm", 0, -ELBOW_BEND, -FOREARM_IN);
  setBone("rightLowerArm", 0, ELBOW_BEND, FOREARM_IN);
  setBone("neck", NECK_SHARE * lookPitch, NECK_SHARE * lookYaw, 0);
  setBone(
    "head",
    -THINK_HEAD_PITCH * avatar.thinking - sway.x * 0.5 + (1 - NECK_SHARE) * lookPitch - YAWN_HEAD_PITCH * avatar.yawn,
    (1 - NECK_SHARE) * lookYaw,
    THINK_HEAD_TILT * avatar.thinking - sway.z + YAWN_HEAD_TILT * avatar.yawn,
  );
}

function updateLook(dt) {
  const target = avatar.cursor
    ? cursorLook(avatar.cursor, headRegion().centre, canvas.clientHeight * LOOK_SPAN_OF_HEIGHT)
    : NO_LOOK;
  for (const key of Object.keys(avatar.look)) {
    avatar.look[key] = approach(avatar.look[key], target[key], dt, LOOK_SPEED);
  }
}

// The eyes look at the camera (with small drifts), up and aside while thinking, and toward the cursor:
// three-vrm turns them toward this target relative to where the head already points.
function updateLookTarget(timeS) {
  const driftX = LOOK_DRIFT_M * (0.6 * Math.sin(timeS * 0.37) + 0.4 * Math.sin(timeS * 0.91));
  const driftY = LOOK_DRIFT_M * 0.5 * Math.sin(timeS * 0.53);
  const reach = camera.position.z * (1 - avatar.thinking);
  lookTarget.position.set(
    camera.position.x + driftX + THINK_LOOK_SIDE_M * avatar.thinking + reach * Math.tan(EYE_MAX_YAW * avatar.look.eyeX),
    camera.position.y + driftY + THINK_LOOK_UP_M * avatar.thinking + reach * Math.tan(EYE_MAX_PITCH * avatar.look.eyeY),
    camera.position.z,
  );
}

function currentState(timeS) {
  if (avatar.state === "listening" && timeS >= avatar.listeningUntilS) avatar.state = "idle";
  return avatar.state;
}

function yawnGapS(random) {
  return MIN_YAWN_GAP_S + (MAX_YAWN_GAP_S - MIN_YAWN_GAP_S) * random();
}

// Only a bored avatar with nothing else to do yawns; anything else ends a yawn at once (eased).
function updateYawn(timeS, state, dt) {
  let target = 0;
  if (avatar.bored && state === "idle" && avatar.speech === null) {
    if (timeS >= avatar.nextYawnS) {
      avatar.yawnStartS = timeS;
      avatar.nextYawnS = timeS + yawnGapS(Math.random);
    }
    const phase = (timeS - avatar.yawnStartS) / YAWN_S;
    if (phase >= 0 && phase < 1) target = Math.sin(Math.PI * phase) ** 2;
  } else {
    avatar.yawnStartS = -Infinity;
  }
  avatar.yawn = approach(avatar.yawn, target, dt, YAWN_SPEED);
}

function updateExpressions(timeS, dt) {
  if (timeS >= avatar.emotionUntilS) showEmotion("neutral", Infinity);
  const targets = emotionTargets(avatar.emotion);
  let eyeWeight = 0;
  for (const [name, target] of Object.entries(targets)) {
    const weight = approach(avatar.emotionWeights[name], target, dt, EMOTION_SPEED);
    avatar.emotionWeights[name] = weight;
    setExpression(name, weight);
    if (name !== "neutral") eyeWeight += weight;
  }

  if (timeS >= avatar.nextBlinkS) {
    avatar.blinkStartS = timeS;
    avatar.nextBlinkS = timeS + nextBlinkDelay(Math.random);
  }
  const blink = eyeWeight > EYES_BUSY_WEIGHT ? 0 : blinkWeight(timeS - avatar.blinkStartS);
  setExpression("blink", Math.max(blink, YAWN_EYES * avatar.yawn));

  let mouthTargets = VISEME_EXPRESSIONS.map(() => 0);
  const speech = avatar.speech;
  if (speech) {
    const elapsedS = timeS - speech.startS;
    if (elapsedS >= speech.duration) avatar.speech = null;
    else mouthTargets = sampleVisemes(speech.visemes, speech.fps, elapsedS);
  }
  mouthTargets[0] = Math.max(mouthTargets[0], YAWN_MOUTH * avatar.yawn);
  VISEME_EXPRESSIONS.forEach((name, i) => {
    avatar.mouth[i] = approach(avatar.mouth[i], mouthTargets[i], dt, MOUTH_SPEED);
    setExpression(name, avatar.mouth[i]);
  });
}

let lastFrameS = nowS();

function frame() {
  const timeS = nowS();
  if (!frameDue(lastFrameS, timeS, maxFps)) return;
  const dt = Math.min(timeS - lastFrameS, MAX_FRAME_DT_S);
  lastFrameS = timeS;

  const vrm = avatar.vrm;
  if (vrm) {
    const state = currentState(timeS);
    avatar.thinking = approach(avatar.thinking, state === "thinking" ? 1 : 0, dt, POSE_SPEED);
    avatar.listening = approach(avatar.listening, state === "listening" ? 1 : 0, dt, POSE_SPEED);
    updateYawn(timeS, state, dt);
    updateLook(dt);
    applyPose(timeS);
    updateLookTarget(timeS);
    updateExpressions(timeS, dt);
    vrm.update(dt);
  }
  webgl.render(scene, camera);
}

// --- Renderer interface ----------------------------------------------------------------------------

function showEmotion(emotion, holdS) {
  avatar.emotion = emotion;
  avatar.emotionUntilS = nowS() + holdS;
}

const vrmRenderer = {
  get canvas() {
    return canvas;
  },
  setEmotion(name, holdS = DEFAULT_EMOTION_HOLD_S) {
    showEmotion(name, holdS ?? DEFAULT_EMOTION_HOLD_S);
  },
  speak(event) {
    // A viewer that connects mid-reply gets the track replayed with how much has already played.
    const timeS = nowS();
    const startS = speechStartS(event.elapsed, timeS);
    showEmotion(event.emotion, startS + (event.duration ?? 0) - timeS + SPEAK_EMOTION_TAIL_S);
    avatar.speech = {
      visemes: Array.isArray(event.visemes) ? event.visemes : [],
      fps: event.fps,
      duration: event.duration,
      startS,
      emotionUntilS: avatar.emotionUntilS, // tells its emotion from one set later
    };
  },
  setState(state, followupS) {
    const timeS = nowS();
    avatar.state = state;
    avatar.listeningUntilS = state === "listening" ? listeningEndS(followupS, timeS) : Infinity;
    avatar.emotionUntilS = emotionEndAfterState(
      avatar.emotionUntilS,
      avatar.speech,
      state,
      timeS,
      SPEAK_EMOTION_TAIL_S,
    );
    avatar.speech = speechAfterState(avatar.speech, state);
  },
  lookAt(point) {
    const valid = point != null && Number.isFinite(point.x) && Number.isFinite(point.y);
    avatar.cursor = valid ? { x: point.x, y: point.y } : null;
  },
  setIdlePhase(phase) {
    const bored = phase === "bored";
    if (bored === avatar.bored) return;
    avatar.bored = bored;
    avatar.nextYawnS = bored ? nowS() + FIRST_YAWN_DELAY_S : Infinity;
  },
  hitTest,
  isOpaqueAt,
  destroy() {
    webgl.setAnimationLoop(null);
    window.removeEventListener("resize", resize);
    if (avatar.vrm) {
      scene.remove(avatar.vrm.scene);
      VRMUtils.deepDispose(avatar.vrm.scene);
      avatar.vrm = null;
    }
    pickTarget.dispose();
    webgl.dispose();
  },
};

/**
 * Starts rendering on the page's canvas and resolves to the renderer once `config.model` (a VRM) is on
 * screen; rejects after showing an Indonesian message when WebGL (WebGLUnavailableError) or the model is not
 * available.
 */
export async function createRenderer(config) {
  canvas = document.getElementById("stage");
  maxFps = config.maxFps > 0 ? config.maxFps : 0;
  try {
    webgl = createWebGLRenderer();
  } catch (error) {
    console.error("WebGL renderer could not be created", error);
    showMessage("WebGL tidak tersedia di jendela ini, jadi avatar tidak bisa ditampilkan.");
    throw new WebGLUnavailableError("three.js could not get a WebGL context", { cause: error });
  }
  avatar.framing = config.framing ?? avatar.framing;
  resize();
  applyFraming();
  window.addEventListener("resize", resize);
  webgl.setAnimationLoop(frame);

  const modelUrl = config.model ?? MODEL_URL;
  try {
    await loadModel(modelUrl);
  } catch (error) {
    console.error("Avatar model failed to load", error);
    showMessage(
      `Model avatar tidak bisa dimuat dari ${modelUrl}. Periksa avatar.model di config.yaml lalu jalankan ulang Cyrene.`,
    );
    throw error;
  }
  return vrmRenderer;
}
