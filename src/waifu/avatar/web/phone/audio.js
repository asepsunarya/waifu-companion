// Pure helpers of Cyrene's phone page: the owner's voice as the 16 kHz mono 16-bit WAV that POST /talk takes, her
// reply's base64 WAV back to bytes, and the server's answers (/config.json, /chat, /talk, /pair, /action) read into
// what the page shows. No DOM or Web Audio here, so `node --test` covers them; `fetch` is passed in.

export const TALK_RATE = 16000;
// The server's limits (SPEC, Cyrene on the phone): 2000 characters of text, 60 s of audio. At 16 kHz mono 16-bit,
// 60 s is 1 920 044 bytes, under the 2 MB body limit.
export const MAX_TALK_S = 60;
export const MAX_CHAT_CHARS = 2000;
// A press shorter than this is a tap, not speech.
export const MIN_TALK_S = 0.3;
// A brain turn plus her voice can take a while; a PC that has not answered by then is not coming back.
export const REQUEST_TIMEOUT_MS = 120_000;
export const DEFAULT_OWNER_CALL = "Master";

const WAV_HEADER_BYTES = 44;
const PCM_FORMAT = 1;
const VISEME_COUNT = 5;
const EMOTIONS = new Set(["neutral", "happy", "sad", "angry", "surprised", "relaxed"]);
const ENGINES = new Set(["live2d", "vrm"]);
const PAIR_CODE = /^[0-9]{6}$/;
// Typed between the digits of a pairing code, the way it is read out or grouped.
const CODE_SEPARATORS = /[\s-]/g;

// What the page tells the owner. The texts in her voice address the owner by `{owner}` (assistant.owner_call), never
// at the start of a sentence, so it is used as written.
const PROBLEM_TEXTS = {
  offline: "Tidak bisa terhubung ke PC. Pastikan PC menyala, Cyrene berjalan, dan Tailscale di HP ini aktif.",
  timeout: "PC terlalu lama tidak menjawab. Coba lagi sebentar lagi.",
  server: "Ada masalah di PC Cyrene. Coba lagi nanti.",
  bad_reply: "Jawaban dari PC tidak bisa dibaca. Coba muat ulang halaman ini.",
  bad_request: "PC menolak permintaan ini. Coba muat ulang halaman ini.",
  forbidden: "PC menolak permintaan dari alamat ini. Buka Cyrene lewat alamat Tailscale PC.",
  unpaired: "HP ini belum dipasangkan dengan Cyrene.",
  busy: "Sebentar ya, {owner}, aku masih menjawab yang tadi.",
  too_long_text: `Pesannya terlalu panjang. Maksimal ${MAX_CHAT_CHARS} karakter.`,
  too_long_audio: `Rekamannya terlalu panjang. Maksimal ${MAX_TALK_S} detik.`,
  not_heard: "Maaf, {owner}, suaramu tidak tertangkap. Coba lagi ya.",
  too_short: "Tahan tombol mikrofon selama bicara, lalu lepaskan.",
  audio_locked: "Mikrofon baru siap. Tahan lagi tombol mikrofon sambil bicara.",
  sound_locked: "Ketuk layar sekali supaya suaranya bisa diputar.",
  audio_failed: "Suaranya tidak bisa diputar, jadi hanya teksnya yang tampil.",
  mic_denied: "Izin mikrofon ditolak. Izinkan mikrofon untuk halaman ini di pengaturan Chrome.",
  mic_unavailable: "Mikrofon tidak bisa dipakai di HP ini.",
  camera_denied:
    "Izin kamera ditolak. Izinkan kamera untuk halaman ini di Chrome: Setelan → Setelan situs → Kamera.",
  camera_missing: "Kamera tidak ditemukan di HP ini.",
  camera_busy: "Kamera sedang dipakai aplikasi lain. Tutup aplikasi itu, lalu coba lagi.",
  camera_unavailable: "Kamera tidak bisa dibuka. Coba lagi, atau muat ulang halaman ini.",
  photo_failed: "Fotonya tidak bisa diambil, jadi pesan ini terkirim tanpa foto.",
  photo_too_big: "Fotonya terlalu besar untuk dikirim, jadi pesan ini terkirim tanpa foto.",
  camera_off: "Kamera HP sudah dimatikan di PC, jadi pesan tadi tidak terkirim. Kirim lagi tanpa kamera.",
  pair_invalid: "Kodenya harus 6 angka.",
  pair_wrong:
    "Kodenya salah atau sudah kedaluwarsa. Cek lagi kode di PC, atau buat kode baru dengan waifu phone pair.",
  pair_locked: "Terlalu banyak percobaan. Buat kode baru di PC dengan waifu phone pair.",
  action_unknown: "Tombol itu sudah tidak ada di PC. Muat ulang halaman ini.",
  action_limit: "Tombolnya terlalu sering ditekan. Tunggu sebentar, lalu coba lagi.",
};

export const PROBLEM_KINDS = Object.freeze(Object.keys(PROBLEM_TEXTS));

function isPositiveNumber(value) {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

// --- The owner's voice --------------------------------------------------------------------------

/**
 * `samples` (mono, `inputRate` Hz) at 16 kHz. Above 16 kHz each output sample is the mean of the input over its own
 * window of `inputRate / 16000` samples, a sample only partly inside it weighed by the part that is (so 44.1 kHz works
 * as well as 48 kHz); the averaging also damps what lies above the new 8 kHz limit instead of folding it into the
 * speech. Below 16 kHz the samples are interpolated linearly. Always a new Float32Array.
 */
export function downsampleTo16k(samples, inputRate) {
  if (!isPositiveNumber(inputRate)) throw new RangeError(`Invalid sample rate: ${inputRate}`);
  if (inputRate === TALK_RATE) return Float32Array.from(samples);
  const ratio = inputRate / TALK_RATE;
  const output = new Float32Array(Math.floor(samples.length / ratio));
  if (ratio < 1) {
    for (let i = 0; i < output.length; i++) {
      const position = i * ratio;
      const index = Math.floor(position);
      const from = samples[index];
      const to = index + 1 < samples.length ? samples[index + 1] : from;
      output[i] = from + (to - from) * (position - index);
    }
    return output;
  }
  for (let i = 0; i < output.length; i++) {
    const start = i * ratio;
    const end = Math.min((i + 1) * ratio, samples.length);
    let sum = 0;
    for (let k = Math.floor(start); k < end; k++) {
      sum += samples[k] * (Math.min(k + 1, end) - Math.max(k, start));
    }
    output[i] = sum / (end - start);
  }
  return output;
}

/** -1..1 samples as signed 16-bit PCM: clipped, rounded, and silent where a sample is not a number. */
export function floatTo16BitPCM(samples) {
  const pcm = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const sample = Number.isNaN(samples[i]) ? 0 : Math.min(Math.max(samples[i], -1), 1);
    pcm[i] = Math.round(sample < 0 ? sample * 0x8000 : sample * 0x7fff);
  }
  return pcm;
}

/** A mono 16-bit PCM WAV file (RIFF, 44-byte header, little-endian) of `pcm` at `sampleRate` Hz. */
export function encodeWav(pcm, sampleRate) {
  if (!Number.isInteger(sampleRate) || sampleRate <= 0) throw new RangeError(`Invalid sample rate: ${sampleRate}`);
  const dataBytes = pcm.length * 2;
  const wav = new Uint8Array(WAV_HEADER_BYTES + dataBytes);
  const view = new DataView(wav.buffer);
  const writeAscii = (offset, text) => {
    for (let i = 0; i < text.length; i++) wav[offset + i] = text.charCodeAt(i);
  };
  writeAscii(0, "RIFF");
  view.setUint32(4, WAV_HEADER_BYTES - 8 + dataBytes, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, PCM_FORMAT, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(36, "data");
  view.setUint32(40, dataBytes, true);
  for (let i = 0; i < pcm.length; i++) view.setInt16(WAV_HEADER_BYTES + i * 2, pcm[i], true);
  return wav;
}

/**
 * The captured microphone blocks (Float32Arrays at `inputRate` Hz, oldest first) as the body of POST /talk: a 16 kHz
 * mono 16-bit WAV of at most MAX_TALK_S seconds. Returns {wav, seconds}.
 */
export function talkWav(blocks, inputRate) {
  const joined = new Float32Array(blocks.reduce((total, block) => total + block.length, 0));
  let offset = 0;
  for (const block of blocks) {
    joined.set(block, offset);
    offset += block.length;
  }
  const speech = downsampleTo16k(joined, inputRate).subarray(0, MAX_TALK_S * TALK_RATE);
  return { wav: encodeWav(floatTo16BitPCM(speech), TALK_RATE), seconds: speech.length / TALK_RATE };
}

/** Why the microphone could not be opened: "mic_denied" when the permission was refused, else "mic_unavailable". */
export function microphoneProblem(error) {
  return error?.name === "NotAllowedError" || error?.name === "SecurityError" ? "mic_denied" : "mic_unavailable";
}

// --- Her reply ------------------------------------------------------------------------------------

/** The bytes of base64 `text`, in a buffer of exactly their size (decodeAudioData takes the whole buffer). */
export function base64ToBytes(text) {
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/** One viseme frame as five weights 0..1; anything that is not a number counts as closed. */
function visemeFrame(frame) {
  return Array.from({ length: VISEME_COUNT }, (_, i) => {
    const weight = Array.isArray(frame) ? frame[i] : undefined;
    return typeof weight === "number" && Number.isFinite(weight) ? Math.min(Math.max(weight, 0), 1) : 0;
  });
}

/**
 * A /chat answer `{text, emotion, audio, fps, visemes, duration}` made safe for the page and the renderer: an unknown
 * emotion is neutral, audio is a base64 string or null, the mouth track is dropped without a positive fps and its
 * frames are repaired, an unknown duration is 0. Null when there is no text to show.
 */
export function parseChatReply(data) {
  if (!isPlainObject(data) || typeof data.text !== "string") return null;
  const fps = isPositiveNumber(data.fps) ? data.fps : 0;
  return {
    text: data.text,
    emotion: EMOTIONS.has(data.emotion) ? data.emotion : "neutral",
    audio: typeof data.audio === "string" ? data.audio : null,
    fps,
    visemes: fps > 0 && Array.isArray(data.visemes) ? data.visemes.map(visemeFrame) : [],
    duration: isPositiveNumber(data.duration) ? data.duration : 0,
  };
}

/**
 * A /talk answer: `{heard, reply}`, where `reply` is the parsed chat reply, or null when nothing was heard (`text`
 * null). Null when the answer is malformed.
 */
export function parseTalkReply(data) {
  if (!isPlainObject(data) || typeof data.heard !== "string") return null;
  if (data.text === null) return { heard: data.heard, reply: null };
  const reply = parseChatReply(data);
  return reply && { heard: data.heard, reply };
}

/** The renderer's speak event for a reply whose audio starts now; `audioS` is the decoded audio's length. */
export function speakEventFor(reply, audioS) {
  return {
    emotion: reply.emotion,
    fps: reply.fps,
    visemes: reply.visemes,
    duration: reply.duration > 0 ? reply.duration : audioS,
    elapsed: 0,
  };
}

/**
 * The page's /config.json `{engine, model, core, credit, owner_call, name}`, with defaults for missing texts. `model`
 * is null while the PC has no avatar.model: the chat works, without the avatar. Null when the page cannot use it (an
 * unknown engine, a model that is neither a URL nor null, or Live2D without its Core).
 */
export function parsePhoneConfig(data) {
  if (!isPlainObject(data) || !ENGINES.has(data.engine)) return null;
  if (typeof data.model !== "string" && data.model !== null) return null;
  const core = typeof data.core === "string" ? data.core : null;
  if (data.engine === "live2d" && core === null) return null;
  const text = (value, fallback) => (typeof value === "string" && value.trim() ? value : fallback);
  return {
    engine: data.engine,
    model: data.model,
    core,
    credit: text(data.credit, ""),
    owner_call: text(data.owner_call, DEFAULT_OWNER_CALL),
    name: text(data.name, "Cyrene"),
  };
}

// --- Requests -------------------------------------------------------------------------------------

/**
 * The problem an error `status` of `route` means for the owner (a kind of PROBLEM_KINDS). A gateway error comes from
 * tailscale serve in front of the PC, which answers 502 while Cyrene is not running. A quick button (/action) the PC
 * no longer has is a 404, and one pressed too often a 429.
 */
export function problemForStatus(route, status) {
  const pairing = route === "/pair";
  if (route === "/action" && status === 404) return "action_unknown";
  if (route === "/action" && status === 429) return "action_limit";
  switch (status) {
    case 502:
    case 503:
    case 504:
      return "offline";
    case 401:
      return "unpaired";
    case 403:
      return pairing ? "pair_wrong" : "forbidden";
    case 413:
      return route === "/talk" ? "too_long_audio" : "too_long_text";
    case 429:
      return pairing ? "pair_locked" : "busy";
    case 400:
    case 415:
      return "bad_request";
    default:
      return "server";
  }
}

/** The text box's message to send, trimmed: {text}, or {problem} ("empty", "too_long_text"). */
export function chatTextFrom(value) {
  const text = value.trim();
  if (!text) return { problem: "empty" };
  // The server counts characters (code points), not UTF-16 units.
  if ([...text].length > MAX_CHAT_CHARS) return { problem: "too_long_text" };
  return { text };
}

/** The six digits of a typed pairing code (spaces, dashes, and full-width digits allowed), or null. */
export function pairCodeFrom(value) {
  const code = value.normalize("NFKC").replace(CODE_SEPARATORS, "");
  return PAIR_CODE.test(code) ? code : null;
}

/**
 * One request to Cyrene's phone server: a POST of `body` (with `contentType`) when given, else a GET, with the
 * page's device cookie and never from the HTTP cache. Resolves to {data} (the JSON answer; null with `json: false`)
 * or {problem} (problemForStatus, "offline", "timeout", or "bad_reply"); it never rejects.
 */
export async function requestCyrene(
  fetchFn,
  route,
  { body, contentType, json = true, timeoutMs = REQUEST_TIMEOUT_MS } = {},
) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const init = { method: "GET", credentials: "same-origin", cache: "no-store", signal: controller.signal };
  if (body !== undefined) Object.assign(init, { method: "POST", headers: { "Content-Type": contentType }, body });
  try {
    const response = await fetchFn(route, init);
    if (!response.ok) return { problem: problemForStatus(route, response.status) };
    return { data: json ? await response.json() : null };
  } catch (error) {
    if (controller.signal.aborted) return { problem: "timeout" };
    return { problem: error instanceof SyntaxError ? "bad_reply" : "offline" };
  } finally {
    clearTimeout(timer);
  }
}

/** The Indonesian text of problem `kind` (an unknown one reads as a problem on the PC), addressing `ownerCall`. */
export function problemText(kind, ownerCall = DEFAULT_OWNER_CALL) {
  const text = PROBLEM_TEXTS[kind] ?? PROBLEM_TEXTS.server;
  return text.replaceAll("{owner}", ownerCall);
}
