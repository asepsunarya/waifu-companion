// The pure helpers of the phone page's camera (phone/camera.js): the PC's camera settings, the photo's size, its
// JPEG quality fallback, base64, the /chat and /talk requests with and without a photo, opening the camera, and what
// to tell the owner when it cannot open. No DOM, camera, or network here.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { PROBLEM_KINDS, base64ToBytes, problemText } from "../../src/waifu/avatar/web/phone/audio.js";
import {
  BACK_CAMERA,
  CAMERA_IDLE_MS,
  FRONT_CAMERA,
  JPEG_QUALITIES,
  MAX_PHOTO_BYTES,
  bytesToBase64,
  cameraProblem,
  chatRequest,
  fitWithin,
  jpegWithin,
  openCamera,
  parseCameraConfig,
  talkRequest,
} from "../../src/waifu/avatar/web/phone/camera.js";

// The Claude API's limit for one image's base64 data (SPEC, looking at the screen).
const API_IMAGE_BASE64_BYTES = 5 * 2 ** 20;

/** `length` bytes of a fixed pseudo-random sequence (every byte value occurs). */
function noise(length, seed = 7) {
  const bytes = new Uint8Array(length);
  let state = seed;
  for (let i = 0; i < length; i++) {
    state = (state * 1103515245 + 12345) >>> 0;
    bytes[i] = state >>> 24;
  }
  return bytes;
}

const fromBase64 = (text) => new Uint8Array(Buffer.from(text, "base64"));

function domError(name) {
  const error = new Error(name);
  error.name = name;
  return error;
}

describe("parseCameraConfig", () => {
  it("reads the longest side a photo may have when the PC offers the camera", () => {
    assert.deepEqual(parseCameraConfig({ engine: "live2d", camera: { enabled: true, max_side: 1280 } }), {
      maxSide: 1280,
    });
  });

  it("offers no camera when the PC turned it off or does not know it", () => {
    assert.equal(parseCameraConfig({ camera: { enabled: false, max_side: null } }), null);
    assert.equal(parseCameraConfig({ engine: "live2d" }), null, "a PC from before v1.15");
    assert.equal(parseCameraConfig(null), null);
  });

  it("offers no camera without a usable size", () => {
    for (const maxSide of [null, 0, -1280, 1280.5, "1280", undefined]) {
      assert.equal(parseCameraConfig({ camera: { enabled: true, max_side: maxSide } }), null, String(maxSide));
    }
    assert.equal(parseCameraConfig({ camera: { enabled: "yes", max_side: 1280 } }), null);
  });
});

describe("fitWithin", () => {
  it("scales the longer side down to the limit, keeping the aspect", () => {
    assert.deepEqual(fitWithin(1920, 1080, 1280), { width: 1280, height: 720 });
    assert.deepEqual(fitWithin(1080, 1920, 1280), { width: 720, height: 1280 });
    assert.deepEqual(fitWithin(4000, 3000, 1568), { width: 1568, height: 1176 });
  });

  it("rounds the shorter side to whole pixels, never below one", () => {
    assert.deepEqual(fitWithin(1000, 333, 500), { width: 500, height: 167 });
    assert.deepEqual(fitWithin(100000, 1, 1280), { width: 1280, height: 1 });
  });

  it("never upscales", () => {
    assert.deepEqual(fitWithin(640, 480, 1280), { width: 640, height: 480 });
    assert.deepEqual(fitWithin(1280, 1280, 1280), { width: 1280, height: 1280 });
  });

  it("refuses sizes that are not positive whole numbers", () => {
    for (const [width, height, maxSide] of [
      [0, 480, 1280],
      [640, -1, 1280],
      [640, 480, 0],
      [640.5, 480, 1280],
      [Number.NaN, 480, 1280],
      ["640", 480, 1280],
    ]) {
      assert.throws(() => fitWithin(width, height, maxSide), RangeError, `${width}x${height} within ${maxSide}`);
    }
  });
});

describe("bytesToBase64", () => {
  it("encodes like the base64 the server decodes", () => {
    assert.equal(bytesToBase64(Uint8Array.of(0, 1, 2, 255)), "AAEC/w==");
    assert.equal(bytesToBase64(new Uint8Array(0)), "");
  });

  it("encodes megabytes of a photo exactly, whatever is left over after the last chunk", () => {
    // 2^20 leaves 1 byte over a multiple of 3, so the three lengths end on every remainder.
    for (const length of [2 ** 20, 2 ** 20 + 1, 2 ** 20 + 2, 3 * 2 ** 20]) {
      const bytes = noise(length);
      assert.equal(bytesToBase64(bytes), Buffer.from(bytes).toString("base64"), `${length} bytes`);
    }
  });

  it("encodes only the bytes of a view into a larger buffer", () => {
    const bytes = noise(1_500_000);
    const view = bytes.subarray(5, 1_200_005);

    assert.equal(bytesToBase64(view), Buffer.from(view).toString("base64"));
  });

  it("round-trips with the page's own decoder", () => {
    const bytes = noise(70_000, 3);

    assert.deepEqual(base64ToBytes(bytesToBase64(bytes)), bytes);
  });
});

describe("chatRequest", () => {
  it("is the v1.14 request while the viewfinder is closed", () => {
    assert.deepEqual(chatRequest("nyalakan lampu"), {
      body: '{"text":"nyalakan lampu"}',
      contentType: "application/json",
    });
    assert.deepEqual(chatRequest("halo", { camera: false }), chatRequest("halo"));
  });

  it("carries the photo as base64 next to the text, sent through the camera", () => {
    const photo = noise(2048);

    const request = chatRequest("ini apa?", { camera: true, photo });

    assert.equal(request.contentType, "application/json");
    const sent = JSON.parse(request.body);
    assert.deepEqual(Object.keys(sent), ["text", "camera", "image"]);
    assert.equal(sent.text, "ini apa?");
    assert.equal(sent.camera, true);
    assert.deepEqual(fromBase64(sent.image), photo);
  });

  it("says the message was sent through the camera also when its photo could not be taken", () => {
    for (const photo of [null, undefined]) {
      assert.deepEqual(chatRequest("coba lihat ini", { camera: true, photo }), {
        body: '{"text":"coba lihat ini","camera":true}',
        contentType: "application/json",
      });
    }
  });
});

describe("talkRequest", () => {
  const wav = Uint8Array.of(0x52, 0x49, 0x46, 0x46, 1, 2, 3, 4);

  it("is the v1.14 WAV body while the viewfinder is closed", () => {
    const request = talkRequest(wav);

    assert.equal(request.body, wav);
    assert.equal(request.contentType, "audio/wav");
    assert.deepEqual(talkRequest(wav, { camera: false }), request);
  });

  it("sends the recording and the photo together as JSON, sent through the camera", () => {
    const photo = noise(4096);

    const request = talkRequest(wav, { camera: true, photo });

    assert.equal(request.contentType, "application/json");
    const sent = JSON.parse(request.body);
    assert.deepEqual(Object.keys(sent), ["audio", "camera", "image"]);
    assert.deepEqual(fromBase64(sent.audio), wav);
    assert.equal(sent.camera, true);
    assert.deepEqual(fromBase64(sent.image), photo);
  });

  it("sends the recording as JSON sent through the camera also when its photo could not be taken", () => {
    const request = talkRequest(wav, { camera: true, photo: null });

    assert.equal(request.contentType, "application/json");
    const sent = JSON.parse(request.body);
    assert.deepEqual(Object.keys(sent), ["audio", "camera"]);
    assert.deepEqual(fromBase64(sent.audio), wav);
    assert.equal(sent.camera, true);
  });
});

describe("jpegWithin", () => {
  function encoder(sizes) {
    const qualities = [];
    const encode = async (quality) => {
      qualities.push(quality);
      return { quality, size: sizes[quality] };
    };
    return { encode, qualities };
  }

  it("tries quality 0.85, then 0.7, then 0.5", () => {
    assert.deepEqual(JPEG_QUALITIES, [0.85, 0.7, 0.5]);
  });

  it("keeps the first encoding that is small enough", async () => {
    const { encode, qualities } = encoder({ 0.85: 900 });

    const photo = await jpegWithin(encode, 1000);

    assert.equal(photo.quality, 0.85);
    assert.deepEqual(qualities, [0.85]);
  });

  it("encodes a photo that is too big again at lower qualities, in order", async () => {
    const { encode, qualities } = encoder({ 0.85: 1500, 0.7: 1001, 0.5: 1000 });

    const photo = await jpegWithin(encode, 1000);

    assert.equal(photo.quality, 0.5);
    assert.deepEqual(qualities, [0.85, 0.7, 0.5]);
  });

  it("gives up on a photo that stays too big", async () => {
    const { encode, qualities } = encoder({ 0.85: 3000, 0.7: 2000, 0.5: 1001 });

    assert.equal(await jpegWithin(encode, 1000), null);
    assert.deepEqual(qualities, [0.85, 0.7, 0.5]);
  });

  it("holds photos to 3.5 MB, whose base64 fits the API's 5 MB image limit", async () => {
    const { encode } = encoder({ 0.85: MAX_PHOTO_BYTES + 1, 0.7: MAX_PHOTO_BYTES });

    assert.equal(MAX_PHOTO_BYTES, 3.5 * 2 ** 20);
    assert.ok(Math.ceil(MAX_PHOTO_BYTES / 3) * 4 <= API_IMAGE_BASE64_BYTES);
    assert.equal((await jpegWithin(encode)).quality, 0.7);
  });

  it("passes on a failed encoding", async () => {
    const encode = async () => {
      throw new Error("no image");
    };

    await assert.rejects(jpegWithin(encode, 1000), /no image/);
  });
});

describe("openCamera", () => {
  function mediaDevices(...answers) {
    const asked = [];
    return {
      asked,
      async getUserMedia(constraints) {
        asked.push(constraints);
        const answer = answers.shift();
        if (answer instanceof Error) throw answer;
        return answer;
      },
    };
  }

  it("asks for the back camera, without sound, at up to 1920 x 1080", async () => {
    const stream = { id: "back" };
    const devices = mediaDevices(stream);

    assert.equal(await openCamera(devices, BACK_CAMERA), stream);
    assert.deepEqual(devices.asked, [
      {
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      },
    ]);
  });

  it("asks for the front camera when flipped", async () => {
    const devices = mediaDevices({ id: "front" });

    await openCamera(devices, FRONT_CAMERA);

    assert.deepEqual(devices.asked[0].video.facingMode, { ideal: "user" });
  });

  it("takes any camera when the phone cannot meet the wishes", async () => {
    const any = { id: "any" };
    const devices = mediaDevices(domError("OverconstrainedError"), any);

    assert.equal(await openCamera(devices, BACK_CAMERA), any);
    assert.deepEqual(devices.asked[1], { video: true, audio: false });
  });

  it("passes on every other refusal without asking again", async () => {
    const devices = mediaDevices(domError("NotAllowedError"));

    await assert.rejects(openCamera(devices, BACK_CAMERA), { name: "NotAllowedError" });
    assert.equal(devices.asked.length, 1);
  });

  it("fails where the page has no camera access at all", async () => {
    // navigator.mediaDevices is undefined outside a secure context.
    await assert.rejects(openCamera(undefined, BACK_CAMERA), TypeError);
  });
});

describe("cameraProblem", () => {
  it("tells a refused permission, a missing camera, and a busy camera apart", () => {
    assert.equal(cameraProblem(domError("NotAllowedError")), "camera_denied");
    assert.equal(cameraProblem(domError("SecurityError")), "camera_denied");
    assert.equal(cameraProblem(domError("NotFoundError")), "camera_missing");
    assert.equal(cameraProblem(domError("OverconstrainedError")), "camera_missing");
    assert.equal(cameraProblem(domError("NotReadableError")), "camera_busy");
  });

  it("reads anything else as a camera that cannot be opened", () => {
    assert.equal(cameraProblem(domError("AbortError")), "camera_unavailable");
    assert.equal(cameraProblem(new TypeError("navigator.mediaDevices is undefined")), "camera_unavailable");
    assert.equal(cameraProblem(undefined), "camera_unavailable");
  });

  it("has an Indonesian text for every camera and photo problem", () => {
    const kinds = [
      "camera_denied",
      "camera_missing",
      "camera_busy",
      "camera_unavailable",
      "photo_failed",
      "photo_too_big",
      "camera_off",
    ];

    for (const kind of kinds) assert.ok(PROBLEM_KINDS.includes(kind), kind);
    assert.match(problemText("camera_denied"), /Setelan situs/, "how to allow it in Chrome's site settings");
    assert.match(problemText("camera_busy"), /aplikasi lain/);
    assert.match(problemText("photo_too_big"), /tanpa foto/);
    assert.match(problemText("photo_failed"), /tanpa foto/);
    assert.match(problemText("camera_off"), /dimatikan di PC/);
    assert.match(problemText("camera_off"), /tidak terkirim/);
  });
});

describe("camera timing", () => {
  it("turns the camera off after two minutes without a photo", () => {
    assert.equal(CAMERA_IDLE_MS, 2 * 60 * 1000);
  });
});
