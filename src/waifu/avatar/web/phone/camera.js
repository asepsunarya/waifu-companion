// Pure helpers of the phone page's camera (SPEC, "seeing through the phone camera (v1.15)"): while the owner has the
// viewfinder open, every message they type or say says it was sent through the camera and carries one JPEG photo of
// that moment, when one could be taken. No DOM here, so `node --test` covers them; the camera
// (`navigator.mediaDevices`) and the encoder are passed in.

export const BACK_CAMERA = "environment";
export const FRONT_CAMERA = "user";
// The camera turns itself off this long after the viewfinder opened, the last photo, or the last press of the talk
// button, whichever is later.
export const CAMERA_IDLE_MS = 2 * 60 * 1000;
// The server's limit for a photo. Its base64, what the Claude API counts, then stays under the API's 5 MB per image.
export const MAX_PHOTO_BYTES = 3.5 * 2 ** 20;
// A photo over MAX_PHOTO_BYTES is encoded again at the next quality, and dropped after the last one.
export const JPEG_QUALITIES = Object.freeze([0.85, 0.7, 0.5]);

const JSON_TYPE = "application/json";
const WAV_TYPE = "audio/wav";
const IDEAL_WIDTH = 1920;
const IDEAL_HEIGHT = 1080;
// Any camera at all, when the phone has none that comes close to the wishes above.
const ANY_CAMERA = { video: true, audio: false };
// Bytes per btoa call: a multiple of 3, so only the last chunk ends in padding, and small enough for one
// String.fromCharCode call.
const BASE64_CHUNK_BYTES = 3 * 8192;

const isPositiveInteger = (value) => Number.isInteger(value) && value > 0;

/**
 * The camera settings of the page's /config.json `{camera: {enabled, max_side}}`: {maxSide}, the longest side a photo
 * may have, when the PC offers the camera; else null (turned off, or a PC that predates the camera).
 */
export function parseCameraConfig(data) {
  const camera = data?.camera;
  if (camera?.enabled !== true || !isPositiveInteger(camera.max_side)) return null;
  return { maxSide: camera.max_side };
}

/** `width` x `height` scaled so the longer side is at most `maxSide`, aspect kept, never upscaled: {width, height}. */
export function fitWithin(width, height, maxSide) {
  if (![width, height, maxSide].every(isPositiveInteger)) {
    throw new RangeError(`Invalid size: ${width}x${height} within ${maxSide}`);
  }
  const scale = Math.min(1, maxSide / Math.max(width, height));
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

/** The base64 text of `bytes` (a Uint8Array), encoded in chunks so a photo of megabytes fits every call. */
export function bytesToBase64(bytes) {
  const chunks = [];
  for (let start = 0; start < bytes.length; start += BASE64_CHUNK_BYTES) {
    chunks.push(btoa(String.fromCharCode(...bytes.subarray(start, start + BASE64_CHUNK_BYTES))));
  }
  return chunks.join("");
}

/**
 * What a message sent with the viewfinder open adds to its JSON: that it came through the camera (so the PC never
 * takes a bare "lihat ini" for its own screen), and the JPEG bytes of `photo` as base64 when one was taken.
 */
function cameraFields(photo) {
  return photo === null ? { camera: true } : { camera: true, image: bytesToBase64(photo) };
}

/**
 * The body of POST /chat for `text`: {body, contentType}. With `camera` (the viewfinder was open as it was sent) the
 * message says so and carries the JPEG bytes of `photo`, if one was taken.
 */
export function chatRequest(text, { camera = false, photo = null } = {}) {
  const message = camera ? { text, ...cameraFields(photo) } : { text };
  return { body: JSON.stringify(message), contentType: JSON_TYPE };
}

/**
 * The body of POST /talk for the recording `wav`: {body, contentType}. The WAV itself, or with `camera` (the
 * viewfinder was open as it was sent) the recording as base64 in JSON that says so, with the JPEG bytes of `photo`,
 * if one was taken.
 */
export function talkRequest(wav, { camera = false, photo = null } = {}) {
  if (!camera) return { body: wav, contentType: WAV_TYPE };
  return { body: JSON.stringify({ audio: bytesToBase64(wav), ...cameraFields(photo) }), contentType: JSON_TYPE };
}

/**
 * The first of `encode(quality)` for JPEG_QUALITIES, in order, whose `size` is at most `maxBytes`; null when none
 * is. `encode` resolves to a Blob (or anything with a size); a failure rejects.
 */
export async function jpegWithin(encode, maxBytes = MAX_PHOTO_BYTES) {
  for (const quality of JPEG_QUALITIES) {
    const photo = await encode(quality);
    if (photo.size <= maxBytes) return photo;
  }
  return null;
}

/**
 * Opens the camera facing `facing` (BACK_CAMERA or FRONT_CAMERA) through `mediaDevices` (navigator.mediaDevices),
 * video only: the wished direction and size, or any camera when the phone has none that meets them. Resolves to the
 * MediaStream; rejects like getUserMedia (TypeError where the page has no mediaDevices).
 */
export async function openCamera(mediaDevices, facing) {
  const wished = {
    video: { facingMode: { ideal: facing }, width: { ideal: IDEAL_WIDTH }, height: { ideal: IDEAL_HEIGHT } },
    audio: false,
  };
  try {
    return await mediaDevices.getUserMedia(wished);
  } catch (error) {
    if (error?.name !== "OverconstrainedError") throw error;
    return mediaDevices.getUserMedia(ANY_CAMERA);
  }
}

/** Why the camera could not be opened, as a problem kind of the page (audio.js's PROBLEM_KINDS). */
export function cameraProblem(error) {
  switch (error?.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "camera_denied";
    case "NotFoundError":
    case "OverconstrainedError":
      return "camera_missing";
    case "NotReadableError":
      return "camera_busy";
    default:
      return "camera_unavailable";
  }
}
