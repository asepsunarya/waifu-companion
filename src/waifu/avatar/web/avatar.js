// Renders Cyrene's VRM avatar and drives it from the Server-Sent Events that Cyrene publishes.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";

import {
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
} from "./logic.js";

const MODEL_URL = "/model.vrm";
const EVENTS_URL = "/events";
const RECONNECT_DELAY_MS = 2000;
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

const canvas = document.getElementById("stage");
const messageElement = document.getElementById("message");
const statusElement = document.getElementById("status");
const creditElement = document.getElementById("credit");

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
};

// --- Scene ---------------------------------------------------------------------------------------

// The canvas is always transparent: the mascot window shows the desktop through it, the app window
// shows the page's gradient.
function createRenderer() {
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setClearColor(0x000000, 0);
  return renderer;
}

let renderer;
try {
  renderer = createRenderer();
} catch (error) {
  console.error("WebGL renderer could not be created", error);
  showMessage("WebGL tidak tersedia di jendela ini, jadi avatar tidak bisa ditampilkan.");
}

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 20);
const lookTarget = new THREE.Object3D();
const cameraTarget = new THREE.Vector3();

scene.add(new THREE.AmbientLight(0xffffff, 1.2));
const keyLight = new THREE.DirectionalLight(0xffffff, 1.8);
keyLight.position.set(0.6, 1.8, 2.2);
scene.add(keyLight);

function resize() {
  const width = window.innerWidth;
  const height = window.innerHeight;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO));
  renderer.setSize(width, height, false);
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

async function loadModel() {
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  const gltf = await loader.loadAsync(MODEL_URL, (progress) => {
    if (progress.total > 0) showMessage(`Memuat avatar… ${Math.round((100 * progress.loaded) / progress.total)}%`);
  });
  const vrm = gltf.userData.vrm;
  if (!vrm) throw new Error(`${MODEL_URL} is a glTF file without VRM data`);

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

// --- Motion --------------------------------------------------------------------------------------

function setBone(name, x, y, z) {
  const bone = avatar.vrm.humanoid.getNormalizedBoneNode(name);
  if (bone) bone.rotation.set(x * avatar.mirror, y, z * avatar.mirror);
}

function applyPose(timeS) {
  const breath = breathOffset(timeS);
  const sway = idleSway(timeS);
  setBone("spine", sway.x + LISTEN_LEAN * avatar.listening, 0, sway.z);
  setBone("chest", -BREATH_CHEST_PITCH * breath, 0, 0);
  setBone("leftShoulder", 0, 0, BREATH_SHOULDER_LIFT * breath);
  setBone("rightShoulder", 0, 0, -BREATH_SHOULDER_LIFT * breath);
  setBone("leftUpperArm", UPPER_ARM_FORWARD, 0, -UPPER_ARM_DOWN);
  setBone("rightUpperArm", UPPER_ARM_FORWARD, 0, UPPER_ARM_DOWN);
  setBone("leftLowerArm", 0, -ELBOW_BEND, -FOREARM_IN);
  setBone("rightLowerArm", 0, ELBOW_BEND, FOREARM_IN);
  setBone("head", -THINK_HEAD_PITCH * avatar.thinking - sway.x * 0.5, 0, THINK_HEAD_TILT * avatar.thinking - sway.z);
}

function updateLookTarget(timeS) {
  const driftX = LOOK_DRIFT_M * (0.6 * Math.sin(timeS * 0.37) + 0.4 * Math.sin(timeS * 0.91));
  const driftY = LOOK_DRIFT_M * 0.5 * Math.sin(timeS * 0.53);
  lookTarget.position.set(
    camera.position.x + driftX + THINK_LOOK_SIDE_M * avatar.thinking,
    camera.position.y + driftY + THINK_LOOK_UP_M * avatar.thinking,
    camera.position.z,
  );
}

function currentState(timeS) {
  if (avatar.state === "listening" && timeS >= avatar.listeningUntilS) avatar.state = "idle";
  return avatar.state;
}

function updateExpressions(timeS, dt) {
  if (timeS >= avatar.emotionUntilS) setEmotion("neutral", Infinity);
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
  setExpression("blink", eyeWeight > EYES_BUSY_WEIGHT ? 0 : blinkWeight(timeS - avatar.blinkStartS));

  let mouthTargets = VISEME_EXPRESSIONS.map(() => 0);
  const speech = avatar.speech;
  if (speech) {
    const elapsedS = timeS - speech.startS;
    if (elapsedS >= speech.duration) avatar.speech = null;
    else mouthTargets = sampleVisemes(speech.visemes, speech.fps, elapsedS);
  }
  VISEME_EXPRESSIONS.forEach((name, i) => {
    avatar.mouth[i] = approach(avatar.mouth[i], mouthTargets[i], dt, MOUTH_SPEED);
    setExpression(name, avatar.mouth[i]);
  });
}

let lastFrameS = nowS();

function frame() {
  const timeS = nowS();
  const dt = Math.min(timeS - lastFrameS, MAX_FRAME_DT_S);
  lastFrameS = timeS;

  const vrm = avatar.vrm;
  if (vrm) {
    const state = currentState(timeS);
    avatar.thinking = approach(avatar.thinking, state === "thinking" ? 1 : 0, dt, POSE_SPEED);
    avatar.listening = approach(avatar.listening, state === "listening" ? 1 : 0, dt, POSE_SPEED);
    applyPose(timeS);
    updateLookTarget(timeS);
    updateExpressions(timeS, dt);
    vrm.update(dt);
  }
  renderer.render(scene, camera);
}

// --- Events --------------------------------------------------------------------------------------

function setEmotion(emotion, holdS) {
  avatar.emotion = emotion;
  avatar.emotionUntilS = nowS() + holdS;
}

function handleEvent(event) {
  switch (event.type) {
    case "hello":
      if (event.framing && event.framing !== avatar.framing) {
        avatar.framing = event.framing;
        applyFraming();
      }
      if (event.credit) creditElement.textContent = event.credit;
      if (event.name) document.title = avatarTitle(event.name);
      break;
    case "state":
      avatar.state = event.state;
      avatar.listeningUntilS = event.state === "listening" ? listeningEndS(event.followup_s, nowS()) : Infinity;
      break;
    case "speak": {
      // A viewer that connects mid-reply gets the track replayed with how much has already played.
      const timeS = nowS();
      const startS = speechStartS(event.elapsed, timeS);
      avatar.speech = {
        visemes: Array.isArray(event.visemes) ? event.visemes : [],
        fps: event.fps,
        duration: event.duration,
        startS,
      };
      setEmotion(event.emotion, startS + (event.duration ?? 0) - timeS + SPEAK_EMOTION_TAIL_S);
      break;
    }
    case "emotion":
      setEmotion(event.emotion, event.hold_s ?? DEFAULT_EMOTION_HOLD_S);
      break;
    default:
      break;
  }
}

function setConnected(connected) {
  statusElement.dataset.state = connected ? "connected" : "disconnected";
  statusElement.title = connected ? "Terhubung ke Cyrene" : "Terputus dari Cyrene";
}

function connectEvents() {
  const source = new EventSource(EVENTS_URL);
  source.onopen = () => setConnected(true);
  source.onmessage = (message) => {
    try {
      handleEvent(JSON.parse(message.data));
    } catch (error) {
      console.warn("Ignoring a malformed avatar event", message.data, error);
    }
  };
  source.onerror = () => {
    setConnected(false);
    // EventSource retries by itself unless the server answered with an error; then start over.
    if (source.readyState === EventSource.CLOSED) {
      source.close();
      setTimeout(connectEvents, RECONNECT_DELAY_MS);
    }
  };
}

// --- Start ---------------------------------------------------------------------------------------

if (renderer) {
  resize();
  applyFraming();
  window.addEventListener("resize", resize);
  renderer.setAnimationLoop(frame);
  // Subscribe once the model is on screen (or has failed): Cyrene waits for a viewer before
  // `say` and the login greeting, so by then the mouth can move with her first words.
  loadModel()
    .catch((error) => {
      console.error("Avatar model failed to load", error);
      showMessage(
        `Model avatar tidak bisa dimuat dari ${MODEL_URL}. Periksa avatar.model di config.yaml lalu jalankan ulang Cyrene.`,
      );
    })
    .finally(connectEvents);
}
