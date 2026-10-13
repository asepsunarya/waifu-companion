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

const CONFIG_URL = "/config.json";
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

/** Puts the bubbles in the chat list, the typing dots (if any) after them; returns their items, oldest first. */
function renderBubbles() {
  const items = bubbles.map(({ from, text }) => {
    const item = document.createElement("li");
    item.className = `bubble from-${from}`;
    item.textContent = text;
    return item;
  });
  page.bubbles.replaceChildren(...items);
  if (typing) page.bubbles.append(typing);
  return items;
}

/**
 * Adds a bubble and scrolls to it: the owner's to the end of the list, hers to her first line, below the list's
 * faded top edge, so a reply taller than the list is read from its start.
 */
function addBubble(from, text) {
  const bubble = { from, text };
  bubbles.push(bubble);
  bubbles.splice(0, bubbles.length - MAX_BUBBLES);
  const item = renderBubbles().at(-1);
  page.bubbles.scrollTop =
    from === "cyrene"
      ? item.offsetTop - Number.parseFloat(getComputedStyle(page.bubbles).paddingTop)
      : page.bubbles.scrollHeight;
  return bubble;
}

function removeBubble(bubble) {
  const index = bubbles.indexOf(bubble);
  if (index >= 0) bubbles.splice(index, 1);
  renderBubbles();
}

function showNotice(kind) {
  clearTimeout(noticeTimer);
  page.notice.textContent = problemText(kind, ownerCall);
  page.notice.hidden = false;
  noticeTimer = setTimeout(hideNotice, NOTICE_MS);
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

function showChat(config) {
  ownerCall = config.owner_call;
  page.name.textContent = config.name;
  page.credit.textContent = config.credit;
  page.text.placeholder = `Tulis pesan untuk ${config.name}…`;
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
    await loadClassicScript(coreUrl);
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
  showChat(config);
  if (config.model === null) {
    showMessage(MODEL_MISSING_TEXT);
    return;
  }
  renderer = await startRenderer(config);
  renderer?.setState(state);
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

/** Sends one request while she thinks; its JSON answer, or null after showing the problem (or the pairing form). */
async function ask(route, body, contentType) {
  setBusy(true);
  hideNotice();
  stopPlayback();
  setState("thinking");
  const result = await requestCyrene(fetch, route, { body, contentType });
  setBusy(false);
  if (result.problem === "unpaired") {
    showPairing();
    return null;
  }
  if (result.problem) {
    setState("idle");
    showNotice(result.problem);
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
  page.text.value = "";
  fitTextBox();
  const sent = addBubble("owner", text);
  const result = await ask(CHAT_URL, JSON.stringify({ text }), "application/json");
  if (result === null) {
    // She did not answer it: the message goes back to the text box to send again.
    removeBubble(sent);
    if (!page.text.value) page.text.value = text;
    fitTextBox();
    return;
  }
  await answer(parseChatReply(result.data));
}

async function sendSpeech(wav) {
  const result = await ask(TALK_URL, wav, "audio/wav");
  if (result === null) return;
  const heard = parseTalkReply(result.data);
  if (heard === null) {
    await answer(null);
    return;
  }
  if (heard.heard) addBubble("owner", heard.heard);
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
  const recorder = await session.recorder;
  const recording = recorder && (await recorder.stop());
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
  await sendSpeech(wav);
}

function cancelTalk() {
  finishTalk(false);
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
  if (document.hidden) cancelTalk();
});

page.composer.addEventListener("submit", (event) => {
  event.preventDefault();
  sendText();
});
page.text.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  sendText();
});
page.text.addEventListener("input", fitTextBox);
page.pairForm.addEventListener("submit", pair);
page.retry.addEventListener("click", () => location.reload());

boot();
