// Cyrene on the owner's phone (SPEC, "Cyrene on the phone (v1.14)"). The phone server serves this page at / to the
// owner's phone through Tailscale. The page boots from /config.json like the avatar page: a 401 means this phone is
// not paired yet, so it shows the pairing form; otherwise it shows the chat and loads her renderer (live2d.js or
// avatar.js, the interface described at the top of live2d.js) full screen with upper framing.
//
// The owner types, or holds the talk button: the microphone is captured with Web Audio (capture-worklet.js) while
// the button is held and sent to POST /talk as a 16 kHz mono 16-bit WAV (audio.js). Her reply, from /chat or /talk,
// shows as a bubble and plays through an AudioContext, which Chrome lets make sound only after a touch on the page
// (its autoplay policy), so the context is created and resumed on the owner's first touch; the renderer moves her
// mouth with the reply's visemes from the moment the audio starts.
//
// When the PC offers the camera (SPEC, "seeing through the phone camera (v1.15)"), the camera button opens a
// viewfinder over the lower part of the page. While it is open, each message, typed or said, says it was sent through
// the camera and takes one photo of that moment (camera.js), which goes with it to /chat or /talk and shows as a
// thumbnail in the owner's bubble. The camera turns off when the viewfinder closes, the page is hidden or left, or two
// minutes pass without a photo or a press of the talk button. A PC restarted with the camera off refuses what is sent
// through it; the page then reads /config.json again and hides the camera.
//
// When the PC has quick buttons (SPEC, "tombol cepat di HP (v1.35)"), the lamp button left of the text box opens a
// panel of them in the viewfinder's place: the owner's switches and routines, run on the PC without asking her. A tap
// sends the button's id to POST /action (actions.js) and the PC's answer shows as a toast; one runs at a time. The
// buttons are read again whenever the page shows again and the panel opens, since an alarm that rings on the PC (SPEC,
// "alarm bangun (v1.46)") adds its own, first: the panel then opens on them.
//
// The "Suara" button in the top bar (SPEC, "diagnosa suara (v1.47)") opens a sheet over the page that shows what her
// mic on the PC hears: the mic and its level against the VAD's gate, and her last utterances with what she did with
// each (voice.js). It reads GET /voice every VOICE_REFRESH_MS while it is open and the page visible, one read at a time,
// and the owner switches the mic or changes the voice settings there (POST /voice/set). The PC hides it in guest mode.
import { actionOutcome, actionRequest, isAlarmAction, parseActionGroups } from "./actions.js";
import {
  DEFAULT_OWNER_CALL,
  MAX_TALK_S,
  MIN_TALK_S,
  base64ToBytes,
  chatTextFrom,
  microphoneProblem,
  pairCodeFrom,
  parseChatReply,
  parsePhoneConfig,
  parseTalkReply,
  problemText,
  requestCyrene,
  speakEventFor,
  talkWav,
} from "./audio.js";
import {
  BACK_CAMERA,
  CAMERA_IDLE_MS,
  FRONT_CAMERA,
  cameraProblem,
  chatRequest,
  fitWithin,
  jpegWithin,
  openCamera,
  parseCameraConfig,
  talkRequest,
} from "./camera.js";
import {
  levelPercent,
  micRequest,
  outcomeClass,
  parseVoiceStatus,
  recordLines,
  resetRequest,
  tuneRequest,
  tuningFrom,
  voiceOutcome,
} from "./voice.js";

const CONFIG_URL = "/config.json";
const ACTIONS_URL = "/actions";
const ACTION_URL = "/action";
// While the panel lists a ringing alarm's buttons, the list is read again this often, so they go once it stops.
const ALARM_REFRESH_MS = 15_000;
const VOICE_URL = "/voice";
const VOICE_SET_URL = "/voice/set";
// While the Suara sheet is open and the page visible, the voice status is read this often, each read given this long.
const VOICE_REFRESH_MS = 1500;
const VOICE_TIMEOUT_MS = 4000;
// What the PC answers when it hides the sheet (guest mode) or has none: the sheet closes and its button goes.
const VOICE_GONE = new Set(["voice_hidden", "voice_missing"]);
const VOICE_FOLLOW_CONFIG = "Ikuti urutan di config";
const VOICE_NO_MIC = "Belum ada mic yang terbuka. Cyrene mencoba lagi tiap 5 detik.";
const VOICE_NO_LEVEL = "Levelnya belum terukur.";
const CHAT_URL = "/chat";
const TALK_URL = "/talk";
const PAIR_URL = "/pair";
const SERVICE_WORKER_URL = "/sw.js";
const CAPTURE_WORKLET_URL = "/phone/capture-worklet.js";
const CAPTURE_PROCESSOR = "cyrene-capture";

const MAX_BUBBLES = 6;
const NOTICE_MS = 6000;
const TEXT_MAX_HEIGHT_PX = 120;
const DEFAULT_EMOTION_HOLD_S = 4;
// How long a released recording waits for the worklet's last blocks.
const STOP_WAIT_MS = 300;
// How long a reply waits for a suspended AudioContext to start before only its text shows.
const RESUME_WAIT_MS = 500;

const MICROPHONE = {
  audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
};
// One input channel: the browser mixes a stereo microphone down to mono ("speakers": (L + R) / 2). No output: the
// worklet only hands the samples to the page, so nothing of the microphone reaches the speaker.
const CAPTURE_NODE = {
  numberOfInputs: 1,
  numberOfOutputs: 0,
  channelCount: 1,
  channelCountMode: "explicit",
  channelInterpretation: "speakers",
};

const STATE_LABELS = { idle: "Siap", listening: "Mendengarkan…", thinking: "Berpikir…", speaking: "Berbicara…" };
const TALK_LABEL = "Tahan untuk bicara";
const RELEASE_LABEL = "Lepaskan untuk mengirim";
const RENDERER_FAILED_TEXT = "Avatar tidak bisa dimuat dari PC, tapi chat tetap bisa dipakai.";
const CORE_MISSING_TEXT =
  "Cubism Core belum diatur di PC (avatar.live2d_core), jadi avatarnya tidak tampil. Chat tetap bisa dipakai.";
const MODEL_MISSING_TEXT =
  "Model avatar belum diatur di PC (avatar.model), jadi avatarnya tidak tampil. Chat tetap bisa dipakai.";
const PHOTO_ALT = "Foto dari kamera yang dikirim bersama pesan ini";
// What a message sent while the viewfinder is closed takes from the camera (takePhoto's answer).
const NOT_THROUGH_CAMERA = Object.freeze({ camera: false, photo: null, problem: null });
// How the PC refuses a message sent through the camera once `waifu run` restarted with the camera off: 400, or 413
// for a body over the limits it keeps without the camera.
const CAMERA_REFUSALS = new Set(["bad_request", "too_long_text", "too_long_audio"]);

const page = {
  name: document.getElementById("name"),
  status: document.getElementById("status"),
  credit: document.getElementById("credit"),
  message: document.getElementById("message"),
  retry: document.getElementById("retry"),
  pairing: document.getElementById("pairing"),
  pairForm: document.getElementById("pair-form"),
  pairCode: document.getElementById("pair-code"),
  pairButton: document.getElementById("pair-button"),
  pairNotice: document.getElementById("pair-notice"),
  chat: document.getElementById("chat"),
  bubbles: document.getElementById("bubbles"),
  notice: document.getElementById("notice"),
  composer: document.getElementById("composer"),
  text: document.getElementById("text"),
  send: document.getElementById("send"),
  talk: document.getElementById("talk"),
  camera: document.getElementById("camera"),
  viewfinder: document.getElementById("viewfinder"),
  cameraView: document.getElementById("camera-view"),
  cameraFlip: document.getElementById("camera-flip"),
  cameraClose: document.getElementById("camera-close"),
  quick: document.getElementById("quick"),
  quickToggle: document.getElementById("quick-toggle"),
  voiceToggle: document.getElementById("voice-toggle"),
  voice: document.getElementById("voice"),
  voiceClose: document.getElementById("voice-close"),
  voiceMic: document.getElementById("voice-mic"),
  voiceDefault: document.getElementById("voice-default"),
  voiceLevel: document.getElementById("voice-level"),
  voiceGate: document.getElementById("voice-gate"),
  voiceLevelText: document.getElementById("voice-level-text"),
  voiceDevice: document.getElementById("voice-device"),
  voiceSwitch: document.getElementById("voice-switch"),
  voiceMicNote: document.getElementById("voice-mic-note"),
  voiceSpeaker: document.getElementById("voice-speaker"),
  voiceThreshold: document.getElementById("voice-threshold"),
  voiceMargin: document.getElementById("voice-margin"),
  voiceSave: document.getElementById("voice-save"),
  voiceReset: document.getElementById("voice-reset"),
  voiceNote: document.getElementById("voice-note"),
  voiceRecords: document.getElementById("voice-records"),
};

let ownerCall = DEFAULT_OWNER_CALL;
let renderer = null;
let state = "idle";
// The typing dots at the end of the chat list while she thinks, else null.
let typing = null;
// A request to Cyrene is running (or a released recording is on its way); the server takes one at a time.
let busy = false;
const bubbles = [];
let noticeTimer = 0;
let audioContext = null;
let captureModule = null;
let playing = null;
// The held talk button: {recorder: Promise<recorder | null>, startedMs, timer}.
let talk = null;
// The PC's camera settings ({maxSide}) when it offers the camera, else null.
let cameraSettings = null;
// The open viewfinder: {facing, stream (null while the camera opens), timer (turns it off)}, else null.
let viewfinder = null;
// Every quick button in the panel, and whether one of them is running on the PC.
let quickButtons = [];
let quickRunning = false;
// The groups the panel shows, as JSON, so a new listing rebuilds it only when they changed.
let quickListed = "[]";
// Whether they hold a ringing alarm's buttons, and the timer that reads them again meanwhile (null when none runs).
let quickRinging = false;
let alarmPoll = null;
// The Suara sheet: the last status read, the timer reading it again (null when none runs), whether a read or a change
// runs, the mics its picker lists (as JSON, so it is rebuilt only when they changed), and whether its settings' boxes
// hold the PC's settings (filled once per opening, so a poll never overwrites what the owner types); whether the
// picker's note holds a failed read's problem (gone at the next good read), and whether a switch the PC answered
// "pending" is still to be shown there once it came out.
let voiceStatus = null;
let voicePoll = null;
let voiceReading = false;
let voiceChanging = false;
let voiceListed = "";
let voiceBoxesFilled = false;
let voiceReadProblem = false;
let voiceSwitchAwaited = false;

class CoreMissingError extends Error {}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// --- What the page shows ---------------------------------------------------------------------------

function setState(next) {
  state = next;
  document.body.dataset.state = next;
  page.status.textContent = STATE_LABELS[next];
  renderer?.setState(next);
  showTyping(next === "thinking");
}

/** Adds the typing dots at the end of the chat list and scrolls to them, or removes them; nothing else moves. */
function showTyping(on) {
  if (on === (typing !== null)) return;
  if (!on) {
    typing.remove();
    typing = null;
    return;
  }
  typing = document.createElement("li");
  typing.className = "bubble from-cyrene typing";
  typing.setAttribute("aria-label", STATE_LABELS.thinking);
  typing.append(...Array.from({ length: 3 }, () => document.createElement("span")));
  page.bubbles.append(typing);
  page.bubbles.scrollTop = page.bubbles.scrollHeight;
}

/** The thumbnail of a photo in its bubble; its size reserves the room before the image has loaded. */
function thumbnailImage({ url, width, height }) {
  const image = document.createElement("img");
  image.className = "photo";
  image.src = url;
  image.width = width;
  image.height = height;
  image.alt = PHOTO_ALT;
  return image;
}

/** Puts the bubbles in the chat list, the typing dots (if any) after them; returns their items, oldest first. */
function renderBubbles() {
  const items = bubbles.map(({ from, text, thumbnail }) => {
    const item = document.createElement("li");
    item.className = `bubble from-${from}`;
    item.textContent = text;
    if (thumbnail) item.prepend(thumbnailImage(thumbnail));
    return item;
  });
  page.bubbles.replaceChildren(...items);
  if (typing) page.bubbles.append(typing);
  return items;
}

/** Lets go of the photos of bubbles that left the chat strip: a thumbnail is all the page keeps of a photo. */
function forgetBubbles(gone) {
  for (const { thumbnail } of gone) {
    if (thumbnail) URL.revokeObjectURL(thumbnail.url);
  }
}

/**
 * Adds a bubble, with the thumbnail of `photo` (a photo taken for it) when given, and scrolls to it: the owner's to
 * the end of the list, hers to her first line, below the list's faded top edge, so a reply taller than the list is
 * read from its start.
 */
function addBubble(from, text, photo = null) {
  const thumbnail = photo && { url: URL.createObjectURL(photo.blob), width: photo.width, height: photo.height };
  const bubble = { from, text, thumbnail };
  bubbles.push(bubble);
  forgetBubbles(bubbles.splice(0, bubbles.length - MAX_BUBBLES));
  const item = renderBubbles().at(-1);
  page.bubbles.scrollTop =
    from === "cyrene"
      ? item.offsetTop - Number.parseFloat(getComputedStyle(page.bubbles).paddingTop)
      : page.bubbles.scrollHeight;
  return bubble;
}

function removeBubble(bubble) {
  const index = bubbles.indexOf(bubble);
  if (index >= 0) forgetBubbles(bubbles.splice(index, 1));
  renderBubbles();
}

/** Shows `text` in the notice strip (its role="status" reads it out) for NOTICE_MS. */
function showToast(text) {
  clearTimeout(noticeTimer);
  page.notice.textContent = text;
  page.notice.hidden = false;
  noticeTimer = setTimeout(hideNotice, NOTICE_MS);
}

function showNotice(kind) {
  showToast(problemText(kind, ownerCall));
}

function hideNotice() {
  clearTimeout(noticeTimer);
  page.notice.hidden = true;
}

function showMessage(text) {
  page.message.textContent = text;
  page.message.hidden = false;
}

function setBusy(value) {
  busy = value;
  page.send.disabled = value;
  page.talk.disabled = value;
}

function fitTextBox() {
  page.text.style.height = "auto";
  page.text.style.height = `${Math.min(page.text.scrollHeight, TEXT_MAX_HEIGHT_PX)}px`;
}

// --- Boot and pairing -----------------------------------------------------------------------------

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker
    .register(SERVICE_WORKER_URL)
    .catch((error) => console.warn("The app shell could not be installed", error));
}

function showBootProblem(kind) {
  showMessage(problemText(kind, ownerCall));
  page.retry.hidden = false;
}

function showPairing() {
  cancelTalk();
  closeViewfinder();
  stopPlayback();
  setState("idle");
  page.message.hidden = true;
  page.chat.hidden = true;
  page.pairing.hidden = false;
  page.pairCode.focus();
}

function showPairProblem(kind) {
  page.pairNotice.textContent = problemText(kind, ownerCall);
  page.pairNotice.hidden = false;
}

async function pair(event) {
  event.preventDefault();
  const code = pairCodeFrom(page.pairCode.value);
  if (code === null) {
    showPairProblem("pair_invalid");
    return;
  }
  page.pairButton.disabled = true;
  const result = await requestCyrene(fetch, PAIR_URL, {
    body: JSON.stringify({ code }),
    contentType: "application/json",
    json: false,
  });
  page.pairButton.disabled = false;
  if (result.problem) {
    showPairProblem(result.problem);
    return;
  }
  // The answer set the device cookie: everything behind it loads now.
  location.reload();
}

function showChat(config, camera) {
  ownerCall = config.owner_call;
  cameraSettings = camera;
  page.name.textContent = config.name;
  page.credit.textContent = config.credit;
  page.text.placeholder = `Tulis pesan untuk ${config.name}…`;
  page.camera.hidden = camera === null;
  page.chat.hidden = false;
  // Chrome lets an AudioContext make sound only after the owner touched the page (pointerup for a finger,
  // pointerdown for a mouse, a key): every one of them creates or resumes it, so her first reply can play.
  for (const type of ["pointerdown", "pointerup", "keydown"]) {
    document.addEventListener(type, ensureAudio, { capture: true, passive: true });
  }
}

function loadClassicScript(url) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = url;
    script.addEventListener("load", resolve, { once: true });
    script.addEventListener("error", () => reject(new Error(`${url} could not be loaded`)), { once: true });
    document.head.append(script);
  });
}

async function loadLive2dRenderer(coreUrl) {
  try {
    // Her renderer drawn again after a lost WebGL context (keepDrawing) keeps the Core the engine already runs on.
    if (!window.Live2DCubismCore) await loadClassicScript(coreUrl);
  } catch (error) {
    throw new CoreMissingError("The Cubism Core could not be loaded", { cause: error });
  }
  if (!window.Live2DCubismCore) throw new CoreMissingError(`${coreUrl} did not define Live2DCubismCore`);
  return import("/live2d.js");
}

async function startRenderer(config) {
  let module;
  try {
    module = config.engine === "live2d" ? await loadLive2dRenderer(config.core) : await import("/avatar.js");
  } catch (error) {
    console.error("The avatar renderer could not be loaded", error);
    showMessage(error instanceof CoreMissingError ? CORE_MISSING_TEXT : RENDERER_FAILED_TEXT);
    return null;
  }
  try {
    return await module.createRenderer({ ...config, framing: "upper" });
  } catch (error) {
    // The renderer has already put the reason on the page.
    console.error("The avatar could not be shown", error);
    return null;
  }
}

async function boot() {
  registerServiceWorker();
  const result = await requestCyrene(fetch, CONFIG_URL);
  if (result.problem === "unpaired") {
    showPairing();
    return;
  }
  const config = result.problem ? null : parsePhoneConfig(result.data);
  if (config === null) {
    showBootProblem(result.problem ?? "bad_reply");
    return;
  }
  showChat(config, parseCameraConfig(result.data));
  loadQuick();
  checkVoice();
  if (config.model === null) {
    showMessage(MODEL_MISSING_TEXT);
    return;
  }
  renderer = await startRenderer(config);
  renderer?.setState(state);
  if (renderer !== null) keepDrawing(config);
}

/**
 * After a lost WebGL context (Android Chrome drops it for a page in the background, a GPU reset drops any), draws her
 * again on a fresh canvas instead of reloading the page, so the chat stays (recovery.keepRenderer). recovery.js comes
 * from the PC with the renderers: the service worker keeps only the page's own files, which never import it.
 */
async function keepDrawing(config) {
  let recovery;
  try {
    recovery = await import("/recovery.js");
  } catch (error) {
    console.warn("Her avatar cannot be drawn again after a lost WebGL context", error);
    return;
  }
  recovery.keepRenderer(renderer, {
    start: () => startRenderer(config),
    onRenderer(next) {
      renderer = next;
      renderer?.setState(state);
    },
  });
}

// --- Her voice --------------------------------------------------------------------------------------

function ensureAudio() {
  audioContext ??= new AudioContext();
  if (audioContext.state === "suspended") {
    audioContext.resume().catch((error) => console.warn("The audio could not start", error));
  }
  return audioContext;
}

/** Whether `context` can make sound now, giving a suspended one a moment to start. */
async function audioRunning(context) {
  if (context.state !== "running") {
    await Promise.race([context.resume().catch(() => {}), delay(RESUME_WAIT_MS)]);
  }
  return context.state === "running";
}

function stopPlayback() {
  const source = playing;
  playing = null;
  source?.stop();
}

/** Shows a reply's emotion without her voice (no audio, or audio that cannot play), and `problem` when given. */
function showSilently(reply, problem) {
  setState("idle");
  renderer?.setEmotion(reply.emotion, DEFAULT_EMOTION_HOLD_S);
  if (problem) showNotice(problem);
}

async function playReply(reply) {
  if (reply.audio === null) {
    showSilently(reply, null);
    return;
  }
  const context = ensureAudio();
  let buffer;
  try {
    buffer = await context.decodeAudioData(base64ToBytes(reply.audio).buffer);
  } catch (error) {
    console.warn("Her reply's audio could not be decoded", error);
    showSilently(reply, "audio_failed");
    return;
  }
  if (!(await audioRunning(context))) {
    showSilently(reply, "sound_locked");
    return;
  }
  stopPlayback();
  const source = context.createBufferSource();
  source.buffer = buffer;
  source.connect(context.destination);
  source.addEventListener("ended", () => {
    if (playing !== source) return;
    playing = null;
    setState("idle");
  });
  playing = source;
  source.start();
  setState("speaking");
  renderer?.speak(speakEventFor(reply, buffer.duration));
}

// --- Asking her ---------------------------------------------------------------------------------------

/**
 * Sends one request ({body, contentType}) of a message while she thinks; `shot` is what the camera gave the message
 * (takePhoto), whose problem, if any, shows meanwhile. Its JSON answer, or null after showing the problem (or the
 * pairing form).
 */
async function ask(route, request, shot) {
  setBusy(true);
  if (shot.problem) showNotice(shot.problem);
  else hideNotice();
  stopPlayback();
  setState("thinking");
  const result = await requestCyrene(fetch, route, request);
  const refusedCamera = shot.camera && CAMERA_REFUSALS.has(result.problem) && (await cameraTurnedOff());
  const problem = refusedCamera ? "camera_off" : result.problem;
  setBusy(false);
  if (problem === "unpaired") {
    showPairing();
    return null;
  }
  if (problem) {
    setState("idle");
    showNotice(problem);
    return null;
  }
  return result;
}

async function answer(reply) {
  if (reply === null) {
    setState("idle");
    showNotice("bad_reply");
    return;
  }
  if (reply.text) addBubble("cyrene", reply.text);
  await playReply(reply);
}

async function sendText() {
  if (busy || talk) return;
  const { text, problem } = chatTextFrom(page.text.value);
  if (problem === "empty") return;
  if (problem) {
    showNotice(problem);
    return;
  }
  // Busy already while the photo is taken, so the message cannot be sent twice.
  setBusy(true);
  page.text.value = "";
  fitTextBox();
  const shot = await takePhoto();
  const sent = addBubble("owner", text, shot.photo);
  const result = await ask(CHAT_URL, chatRequest(text, { camera: shot.camera, photo: shot.photo?.bytes }), shot);
  if (result === null) {
    // She did not answer it: the message goes back to the text box to send again.
    removeBubble(sent);
    if (!page.text.value) page.text.value = text;
    fitTextBox();
    return;
  }
  await answer(parseChatReply(result.data));
}

/** Sends a recording with what the camera gave it (`shot`, takePhoto's answer): through it or not, and its photo. */
async function sendSpeech(wav, shot) {
  const result = await ask(TALK_URL, talkRequest(wav, { camera: shot.camera, photo: shot.photo?.bytes }), shot);
  if (result === null) return;
  const heard = parseTalkReply(result.data);
  if (heard === null) {
    await answer(null);
    return;
  }
  if (heard.heard) addBubble("owner", heard.heard, shot.photo);
  if (heard.reply === null) {
    setState("idle");
    showNotice("not_heard");
    return;
  }
  await answer(heard.reply);
}

// --- The talk button ------------------------------------------------------------------------------------

async function loadCaptureModule(context) {
  captureModule ??= context.audioWorklet.addModule(CAPTURE_WORKLET_URL).catch((error) => {
    captureModule = null;
    throw error;
  });
  return captureModule;
}

/**
 * Opens the microphone into a capture worklet on `context`. Resolves to a recorder whose `stop()` closes the
 * microphone and resolves to {blocks, rate}: every captured block (Float32Array, oldest first) at `rate` Hz.
 */
async function openRecorder(context) {
  const stream = await navigator.mediaDevices.getUserMedia(MICROPHONE);
  const closeMicrophone = () => stream.getTracks().forEach((track) => track.stop());
  try {
    await loadCaptureModule(context);
    const source = context.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(context, CAPTURE_PROCESSOR, CAPTURE_NODE);
    const blocks = [];
    const ended = new Promise((resolve) => {
      node.port.onmessage = ({ data }) => (data === null ? resolve() : blocks.push(data));
    });
    source.connect(node);
    return {
      async stop() {
        node.port.postMessage("stop");
        await Promise.race([ended, delay(STOP_WAIT_MS)]);
        source.disconnect();
        node.port.close();
        closeMicrophone();
        return { blocks, rate: context.sampleRate };
      },
    };
  } catch (error) {
    closeMicrophone();
    throw error;
  }
}

function setTalking(on) {
  page.talk.setAttribute("aria-pressed", String(on));
  page.talk.setAttribute("aria-label", on ? RELEASE_LABEL : TALK_LABEL);
  page.talk.title = on ? RELEASE_LABEL : TALK_LABEL;
}

function startTalk() {
  if (busy || talk) return;
  hideNotice();
  stopPlayback();
  // The owner is about to send through the camera: a long hold never loses it before the release takes its photo.
  if (viewfinder) keepCameraAwhile(viewfinder);
  const session = { startedMs: performance.now(), timer: 0, recorder: null };
  session.recorder = openRecorder(ensureAudio()).catch((error) => {
    console.warn("The microphone could not be opened", error);
    if (talk === session) endTalk("idle");
    showNotice(microphoneProblem(error));
    return null;
  });
  // The server takes at most MAX_TALK_S seconds: a longer hold is sent as it is then.
  session.timer = setTimeout(() => finishTalk(true), MAX_TALK_S * 1000);
  talk = session;
  setTalking(true);
  setState("listening");
}

function endTalk(nextState) {
  clearTimeout(talk.timer);
  talk = null;
  setTalking(false);
  setState(nextState);
}

/** Ends the held talk: sends what was said when `send`, else drops it. */
async function finishTalk(send) {
  const session = talk;
  if (session === null) return;
  const heldS = (performance.now() - session.startedMs) / 1000;
  endTalk(send ? "thinking" : "idle");
  setBusy(true);
  // The photo of the moment the button is let go, drawn before the recording stops; busy until it is encoded too,
  // so nothing else is sent meanwhile.
  const taking = send ? takePhoto() : null;
  const recorder = await session.recorder;
  const recording = recorder && (await recorder.stop());
  const shot = taking && (await taking);
  setBusy(false);
  if (!send || recording === null) {
    setState("idle");
    return;
  }
  const { wav, seconds } = talkWav(recording.blocks, recording.rate);
  if (seconds < MIN_TALK_S) {
    // A long hold with hardly any audio: the microphone or the audio only became ready on this press (the
    // permission prompt, or Chrome starting the audio on the release of the page's first touch).
    setState("idle");
    showNotice(heldS < MIN_TALK_S ? "too_short" : "audio_locked");
    return;
  }
  await sendSpeech(wav, shot);
}

function cancelTalk() {
  finishTalk(false);
}

// --- The camera ---------------------------------------------------------------------------------------

const stopTracks = (stream) => stream?.getTracks().forEach((track) => track.stop());

/** (Re)starts the two minutes after which the viewfinder of `session` turns the camera off. */
function keepCameraAwhile(session) {
  clearTimeout(session.timer);
  session.timer = setTimeout(closeViewfinder, CAMERA_IDLE_MS);
}

/** Opens the camera `session` faces into the viewfinder; a failure closes it and says why. */
async function startCamera(session) {
  page.cameraFlip.disabled = true;
  let stream;
  try {
    stream = await openCamera(navigator.mediaDevices, session.facing);
  } catch (error) {
    console.warn("The camera could not be opened", error);
    if (viewfinder !== session) return;
    closeViewfinder();
    showNotice(cameraProblem(error));
    return;
  }
  if (viewfinder !== session) {
    // Closed while the camera was opening (the permission prompt, a hidden page).
    stopTracks(stream);
    return;
  }
  session.stream = stream;
  page.cameraView.srcObject = stream;
  page.cameraFlip.disabled = false;
}

function openViewfinder() {
  setQuickOpen(false);
  const session = { facing: BACK_CAMERA, stream: null, timer: 0 };
  viewfinder = session;
  page.viewfinder.hidden = false;
  page.camera.setAttribute("aria-pressed", "true");
  keepCameraAwhile(session);
  startCamera(session);
}

/** Turns the camera off and hides the viewfinder; nothing is photographed until it opens again. */
function closeViewfinder() {
  const session = viewfinder;
  if (session === null) return;
  viewfinder = null;
  clearTimeout(session.timer);
  stopTracks(session.stream);
  page.cameraView.srcObject = null;
  page.viewfinder.hidden = true;
  page.camera.setAttribute("aria-pressed", "false");
}

function toggleViewfinder() {
  if (viewfinder) closeViewfinder();
  else openViewfinder();
}

/** Switches between the back and the front camera; the first is closed before the other opens (Android has one). */
function flipCamera() {
  const session = viewfinder;
  if (session === null || session.stream === null) return;
  stopTracks(session.stream);
  session.stream = null;
  page.cameraView.srcObject = null;
  session.facing = session.facing === BACK_CAMERA ? FRONT_CAMERA : BACK_CAMERA;
  startCamera(session);
}

function canvasJpeg(canvas, quality) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("The photo could not be encoded"))),
      "image/jpeg",
      quality,
    );
  });
}

/** A message sent through the camera whose photo could not be taken, for the reason `problem` (a problem kind). */
const withoutPhoto = (problem) => ({ camera: true, photo: null, problem });

/**
 * What the camera gives the message being sent: {camera, photo, problem}. `camera` tells whether the viewfinder is
 * open; then `photo` is its frame of this moment, scaled to the PC's size, as a JPEG ({blob, bytes, width, height}),
 * or null with the `problem` kind that kept it from being taken. NOT_THROUGH_CAMERA while the viewfinder is closed.
 * Never rejects.
 */
async function takePhoto() {
  const session = viewfinder;
  if (session === null) return NOT_THROUGH_CAMERA;
  keepCameraAwhile(session);
  const video = page.cameraView;
  if (session.stream === null || video.videoWidth === 0) return withoutPhoto("photo_failed");
  try {
    // Drawn before the first await: the frame of the moment the message was sent.
    const { width, height } = fitWithin(video.videoWidth, video.videoHeight, cameraSettings.maxSide);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    canvas.getContext("2d").drawImage(video, 0, 0, width, height);
    const blob = await jpegWithin((quality) => canvasJpeg(canvas, quality));
    if (blob === null) return withoutPhoto("photo_too_big");
    const bytes = new Uint8Array(await blob.arrayBuffer());
    return { camera: true, photo: { blob, bytes, width, height }, problem: null };
  } catch (error) {
    console.warn("The photo could not be taken", error);
    return withoutPhoto("photo_failed");
  }
}

/**
 * Whether the PC no longer offers the camera. The page reads /config.json only as it loads, so after `waifu run`
 * restarted with the camera off, a message sent through it is refused; this reads it again, and when the camera is
 * off, closes the viewfinder and hides its button.
 */
async function cameraTurnedOff() {
  const result = await requestCyrene(fetch, CONFIG_URL);
  if (result.problem || parseCameraConfig(result.data) !== null) return false;
  closeViewfinder();
  cameraSettings = null;
  page.camera.hidden = true;
  return true;
}

// --- The quick buttons ------------------------------------------------------------------------------

/**
 * Reads the PC's quick buttons and shows them when they changed: their toggle when there are any, none when there are
 * none. A problem, or an answer it cannot read, keeps what shows, without a word; so does a press that runs meanwhile.
 */
async function loadQuick() {
  if (quickRunning) return;
  const result = await requestCyrene(fetch, ACTIONS_URL);
  const groups = result.problem ? null : parseActionGroups(result.data);
  if (groups === null || quickRunning) return;
  const listed = JSON.stringify(groups);
  if (listed === quickListed) return;
  quickListed = listed;
  showQuick(groups);
}

/**
 * Builds the panel of `groups` and shows or hides its toggle. An alarm's buttons (ids `al.`) open the panel, so a tap
 * on the alarm's push lands on "Matikan alarm", and are read again every ALARM_REFRESH_MS while they show; an open
 * panel puts the focus on its first button, which it replaced.
 */
function showQuick(groups) {
  quickButtons = [];
  page.quick.replaceChildren(...groups.map(quickGroup));
  page.quickToggle.hidden = groups.length === 0;
  quickRinging = groups.some(({ actions }) => actions.some(({ id }) => isAlarmAction(id)));
  pollWhileRinging();
  if (groups.length === 0) {
    setQuickOpen(false);
    return;
  }
  if (quickRinging && page.quick.hidden) setQuickOpen(true);
  if (!page.quick.hidden) quickButtons[0]?.focus();
}

/** Reads the buttons again every ALARM_REFRESH_MS while an alarm's show and the page is visible; else stops. */
function pollWhileRinging() {
  stopAlarmPoll();
  if (quickRinging && !document.hidden) alarmPoll = setInterval(loadQuick, ALARM_REFRESH_MS);
}

function stopAlarmPoll() {
  clearInterval(alarmPoll);
  alarmPoll = null;
}

/** One group of the panel: its heading, which names the group for a screen reader, over its buttons. */
function quickGroup({ name, actions }, index) {
  const group = document.createElement("div");
  group.className = "quick-group";
  group.setAttribute("role", "group");
  const heading = document.createElement("h2");
  heading.id = `quick-group-${index}`;
  heading.textContent = name;
  group.setAttribute("aria-labelledby", heading.id);
  const list = document.createElement("div");
  list.className = "quick-buttons";
  list.append(...actions.map(quickButton));
  group.append(heading, list);
  return group;
}

function quickButton({ id, label }) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "quick-action";
  button.textContent = label;
  button.addEventListener("click", () => runQuick(button, id));
  quickButtons.push(button);
  return button;
}

/** Shows or hides the panel; it opens in the viewfinder's place, so the camera turns off. */
function setQuickOpen(open) {
  if (open) closeViewfinder();
  page.quick.hidden = !open;
  page.quickToggle.setAttribute("aria-expanded", String(open));
}

/**
 * The toggle's tap: closes the panel, or opens it with the focus on its first button. The panel comes before its
 * toggle in the page, so Tab from the toggle would skip it.
 */
function toggleQuick() {
  const open = page.quick.hidden;
  setQuickOpen(open);
  if (!open) return;
  quickButtons[0]?.focus();
  loadQuick();
}

/** Escape in the open panel, or on its toggle, closes it and leaves the focus on the toggle. */
function closeQuickOnEscape(event) {
  if (event.key !== "Escape" || page.quick.hidden) return;
  setQuickOpen(false);
  page.quickToggle.focus();
}

/**
 * While the PC runs a button: that one says it is busy and every one that it is unavailable. aria-disabled, not
 * disabled, so the focus stays on the pressed button; taps meanwhile are ignored.
 */
function setQuickRunning(pressed, running) {
  quickRunning = running;
  pressed.setAttribute("aria-busy", String(running));
  for (const button of quickButtons) button.setAttribute("aria-disabled", String(running));
}

/**
 * Runs the quick button `id`, pressed as `button`, on the PC and toasts what it did or why it could not. An alarm's
 * button reads the buttons again, as the press may have taken the alarm's away.
 */
async function runQuick(button, id) {
  if (quickRunning) return;
  setQuickRunning(button, true);
  const result = await requestCyrene(fetch, ACTION_URL, actionRequest(id));
  setQuickRunning(button, false);
  if (result.problem === "unpaired") {
    showPairing();
    return;
  }
  if (isAlarmAction(id)) loadQuick();
  if (result.problem) {
    showNotice(result.problem);
    return;
  }
  const outcome = actionOutcome(result.data);
  if (outcome === null) showNotice("bad_reply");
  else showToast(outcome.text);
}

// --- The Suara sheet ------------------------------------------------------------------------------

/** Shows the Suara button once the PC answers its voice status; without one, or in guest mode, none. */
async function checkVoice() {
  const result = await requestCyrene(fetch, VOICE_URL, { timeoutMs: VOICE_TIMEOUT_MS });
  page.voiceToggle.hidden = result.problem !== undefined || parseVoiceStatus(result.data) === null;
}

/** Opens the sheet over the page, its settings' boxes filled afresh, and reads the status on a timer. */
function openVoice() {
  cancelTalk();
  closeViewfinder();
  voiceBoxesFilled = false;
  page.voice.hidden = false;
  page.voiceToggle.setAttribute("aria-expanded", "true");
  page.voiceClose.focus();
  readVoice();
  pollVoice();
}

function closeVoice() {
  page.voice.hidden = true;
  page.voiceToggle.setAttribute("aria-expanded", "false");
  stopVoicePoll();
}

/** Escape in the open sheet closes it and leaves the focus on its button. */
function closeVoiceOnEscape(event) {
  if (event.key !== "Escape" || page.voice.hidden) return;
  closeVoice();
  page.voiceToggle.focus();
}

/** Reads the status every VOICE_REFRESH_MS while the sheet is open and the page visible; else stops. */
function pollVoice() {
  stopVoicePoll();
  if (!page.voice.hidden && !document.hidden) voicePoll = setInterval(readVoice, VOICE_REFRESH_MS);
}

function stopVoicePoll() {
  clearInterval(voicePoll);
  voicePoll = null;
}

/** Reads the status and shows it, one read at a time; a PC that hides the sheet closes it and takes its button. */
async function readVoice() {
  if (voiceReading) return;
  voiceReading = true;
  const result = await requestCyrene(fetch, VOICE_URL, { timeoutMs: VOICE_TIMEOUT_MS });
  voiceReading = false;
  if (!voiceAnswered(result, page.voiceMicNote)) {
    voiceReadProblem = true;
    return;
  }
  const status = parseVoiceStatus(result.data);
  if (status !== null) {
    showVoice(status);
    return;
  }
  showVoiceNote(page.voiceMicNote, problemText("bad_reply", ownerCall), true);
  voiceReadProblem = true;
}

/** Whether `result` of a voice route is an answer to show; else it is dealt with here, a problem shown in `note`. */
function voiceAnswered(result, note) {
  if (result.problem === "unpaired") {
    closeVoice();
    showPairing();
    return false;
  }
  if (VOICE_GONE.has(result.problem)) {
    closeVoice();
    page.voiceToggle.hidden = true;
    return false;
  }
  if (result.problem) {
    showVoiceNote(note, problemText(result.problem, ownerCall), true);
    return false;
  }
  return true;
}

/** Writes `text` in one of the sheet's notes, marked when it tells a problem. */
function showVoiceNote(note, text, problem = false) {
  note.textContent = text;
  note.className = problem ? "problem" : "";
  if (note === page.voiceMicNote) voiceReadProblem = false;
}

function showVoice(status) {
  voiceStatus = status;
  if (voiceReadProblem) showVoiceNote(page.voiceMicNote, "");
  showSwitchOutcome(status.switch);
  const { mic, level, vad } = status;
  page.voiceMic.textContent = mic === null ? VOICE_NO_MIC : [mic.name, mic.host_api].filter(Boolean).join(" · ");
  page.voiceDefault.textContent = `Default Windows (saat Cyrene mulai): ${status.windowsDefault ?? "tidak ada"}`;
  const bar = vad.barDb ?? vad.thresholdDb;
  page.voiceLevel.style.width = `${levelPercent(level?.loudestDb).toFixed(1)}%`;
  page.voiceLevel.className = level !== null && level.loudestDb >= bar ? "meter-level above" : "meter-level";
  page.voiceGate.style.left = `${levelPercent(bar).toFixed(1)}%`;
  page.voiceLevelText.textContent =
    level === null
      ? VOICE_NO_LEVEL
      : `Paling keras ${Math.round(level.loudestDb)} dB, batas suara ${Math.round(bar)} dB · ` +
        `RMS ${Math.round(level.rmsDb)} dB`;
  showVoiceDevices(status);
  if (!voiceBoxesFilled) fillVoiceBoxes(status);
  page.voiceRecords.replaceChildren(...status.records.map(voiceRecord));
}

/** How a switch the PC answered "pending" came out (it waited for her to finish thinking or speaking), once it has. */
function showSwitchOutcome({ pending, last }) {
  if (!voiceSwitchAwaited || pending || last === null) return;
  voiceSwitchAwaited = false;
  showVoiceNote(page.voiceMicNote, last.message, !last.ok);
}

/** The picker's mics, rebuilt only when the PC lists others, so a pick under way is kept. */
function showVoiceDevices(status) {
  const listed = JSON.stringify(status.devices);
  if (listed === voiceListed) return;
  voiceListed = listed;
  const follow = document.createElement("option");
  follow.value = "";
  follow.textContent = VOICE_FOLLOW_CONFIG;
  const options = status.devices.map(({ value, name, host_api }) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = [name, host_api].filter(Boolean).join(" · ");
    return option;
  });
  page.voiceDevice.replaceChildren(follow, ...options);
  page.voiceDevice.value = status.overrideMic ?? "";
}

function fillVoiceBoxes(status) {
  voiceBoxesFilled = true;
  const { speaker, vad } = status;
  page.voiceSpeaker.value = speaker.threshold === null ? "" : String(speaker.threshold);
  page.voiceSpeaker.disabled = !speaker.enabled;
  page.voiceThreshold.value = String(vad.thresholdDb);
  page.voiceMargin.value = String(vad.marginDb);
}

/** One utterance: its time and outcome, what was heard, and its level, score, and timings. */
function voiceRecord(record) {
  const { title, transcript, detail } = recordLines(record);
  const item = document.createElement("li");
  item.className = `voice-record ${outcomeClass(record.outcome)}`;
  const lines = [
    ["voice-title", title],
    ["", transcript],
    ["voice-soft", detail],
  ].filter(([, text]) => text !== null);
  item.append(
    ...lines.map(([className, text]) => {
      const line = document.createElement("p");
      line.className = className;
      line.textContent = text;
      return line;
    }),
  );
  return item;
}

/**
 * Sends a change to POST /voice/set, one at a time, shows the PC's answer in `note`, and reads the status again. A mic
 * switch (`switching`) the PC answered "pending" is shown there again once the status tells how it came out.
 */
async function changeVoice(request, note, { switching = false } = {}) {
  if (voiceChanging) return;
  voiceChanging = true;
  const result = await requestCyrene(fetch, VOICE_SET_URL, request);
  voiceChanging = false;
  if (!voiceAnswered(result, note)) return;
  const outcome = voiceOutcome(result.data);
  if (outcome === null) showVoiceNote(note, problemText("bad_reply", ownerCall), true);
  else showVoiceNote(note, outcome.text, !outcome.ok);
  if (switching) voiceSwitchAwaited = outcome?.pending === true;
  readVoice();
}

function saveVoice() {
  if (voiceStatus === null) return;
  const values = {
    speaker: page.voiceSpeaker.value,
    threshold: page.voiceThreshold.value,
    margin: page.voiceMargin.value,
  };
  const checked = tuningFrom(values, voiceStatus);
  if (checked.problem) {
    page.voiceNote.textContent = checked.problem;
    return;
  }
  changeVoice(tuneRequest(checked.tuning), page.voiceNote);
}

function resetVoice() {
  voiceBoxesFilled = false; // the next status fills them with the config's settings
  changeVoice(resetRequest(), page.voiceNote);
}

const isTalkKey = (event) => event.key === " " || event.key === "Enter";

page.talk.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  // Keeps the press from focusing the button or selecting text, and its release on the button even when the finger
  // slides off it.
  event.preventDefault();
  page.talk.setPointerCapture(event.pointerId);
  startTalk();
});
page.talk.addEventListener("pointerup", () => finishTalk(true));
page.talk.addEventListener("pointercancel", cancelTalk);
page.talk.addEventListener("contextmenu", (event) => event.preventDefault());
page.talk.addEventListener("keydown", (event) => {
  if (!isTalkKey(event)) return;
  event.preventDefault();
  if (!event.repeat) startTalk();
});
page.talk.addEventListener("keyup", (event) => {
  if (!isTalkKey(event)) return;
  event.preventDefault();
  finishTalk(true);
});
page.talk.addEventListener("blur", cancelTalk);
document.addEventListener("visibilitychange", () => {
  pollWhileRinging();
  pollVoice();
  if (!document.hidden) {
    loadQuick();
    if (page.voice.hidden) checkVoice(); // guest mode may have ended or begun meanwhile
    else readVoice();
    return;
  }
  // The alarm may stop and ring again unseen, with the same buttons: the next listing rebuilds the panel, opening it.
  if (quickRinging) quickListed = "";
  cancelTalk();
  closeViewfinder();
});
// Leaving the page fires pagehide; a mobile browser may skip either event, so both turn the camera off.
window.addEventListener("pagehide", () => {
  closeViewfinder();
  stopAlarmPoll();
  stopVoicePoll();
});

page.camera.addEventListener("click", toggleViewfinder);
page.cameraFlip.addEventListener("click", flipCamera);
page.cameraClose.addEventListener("click", closeViewfinder);

page.composer.addEventListener("submit", (event) => {
  event.preventDefault();
  sendText();
});
page.text.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  sendText();
});
// The text box is wider while the owner types in it (phone.css hides the quick toggle then): its lines change.
for (const type of ["input", "focus", "blur"]) page.text.addEventListener(type, fitTextBox);
page.pairForm.addEventListener("submit", pair);
page.retry.addEventListener("click", () => location.reload());

page.quickToggle.addEventListener("click", toggleQuick);
page.quickToggle.addEventListener("keydown", closeQuickOnEscape);
page.quick.addEventListener("keydown", closeQuickOnEscape);

page.voiceToggle.addEventListener("click", () => (page.voice.hidden ? openVoice() : closeVoice()));
page.voiceClose.addEventListener("click", () => {
  closeVoice();
  page.voiceToggle.focus();
});
page.voice.addEventListener("keydown", closeVoiceOnEscape);
page.voiceSwitch.addEventListener("click", () =>
  changeVoice(micRequest(page.voiceDevice.value), page.voiceMicNote, { switching: true }),
);
page.voiceSave.addEventListener("click", saveVoice);
page.voiceReset.addEventListener("click", resetVoice);

boot();
