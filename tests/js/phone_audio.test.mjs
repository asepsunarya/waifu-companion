import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  MAX_CHAT_CHARS,
  MAX_TALK_S,
  PROBLEM_KINDS,
  TALK_RATE,
  base64ToBytes,
  chatTextFrom,
  downsampleTo16k,
  encodeWav,
  floatTo16BitPCM,
  microphoneProblem,
  pairCodeFrom,
  parseChatReply,
  parsePhoneConfig,
  parseTalkReply,
  problemForStatus,
  problemText,
  requestCyrene,
  speakEventFor,
  talkWav,
} from "../../src/waifu/avatar/web/phone/audio.js";

const CLOSE = 1e-6;

function assertAllClose(actual, expected, tolerance = CLOSE) {
  assert.equal(actual.length, expected.length, "length");
  for (let i = 0; i < expected.length; i++) {
    assert.ok(Math.abs(actual[i] - expected[i]) <= tolerance, `at ${i}: expected ${expected[i]}, got ${actual[i]}`);
  }
}

function tone(frequency, rate, count, amplitude = 1, phase = 0) {
  return Float32Array.from(
    { length: count },
    (_, n) => amplitude * Math.sin((2 * Math.PI * frequency * n) / rate + phase),
  );
}

function ascii(bytes, start, length) {
  return String.fromCharCode(...bytes.subarray(start, start + length));
}

function reply(fields = {}) {
  return {
    text: "Lampu kamar sudah kunyalakan.",
    emotion: "happy",
    audio: "UklGRg==",
    fps: 60,
    visemes: [
      [0.1, 0, 0, 0, 0],
      [0.5, 0.2, 0, 0, 0.1],
    ],
    duration: 0.03,
    card: null,
    ...fields,
  };
}

describe("downsampleTo16k", () => {
  it("averages each group of three samples at 48 kHz", () => {
    const out = downsampleTo16k(Float32Array.of(1, 2, 3, 4, 5, 6, 7, 8, 9), 48000);

    assert.ok(out instanceof Float32Array);
    assertAllClose(out, [2, 5, 8]);
  });

  it("weighs the samples a window only partly covers at 24 kHz", () => {
    // Windows of 1.5 input samples: [0, 1.5) and [1.5, 3).
    const out = downsampleTo16k(Float32Array.of(3, 6, 9), 24000);

    assertAllClose(out, [(3 + 0.5 * 6) / 1.5, (0.5 * 6 + 9) / 1.5]);
  });

  it("keeps a constant level and the duration at 44.1 kHz", () => {
    const out = downsampleTo16k(new Float32Array(44100).fill(0.25), 44100);

    assert.equal(out.length, TALK_RATE);
    assertAllClose(out, new Array(TALK_RATE).fill(0.25));
  });

  it("keeps a 1 kHz tone, as the average of each window around its centre", () => {
    const omega = (2 * Math.PI * 1000) / 48000;
    const out = downsampleTo16k(tone(1000, 48000, 4800, 0.5), 48000);
    // The mean of sin at three neighbouring samples is sin at the middle one times (1 + 2 cos ω) / 3.
    const gain = (1 + 2 * Math.cos(omega)) / 3;
    const expected = Array.from({ length: 1600 }, (_, i) => 0.5 * Math.sin(omega * (3 * i + 1)) * gain);

    assertAllClose(out, expected);
  });

  it("cancels a 16 kHz tone instead of folding it into the speech band", () => {
    // Picking every third sample would turn this tone into a constant offset of sin(0.3).
    const out = downsampleTo16k(tone(16000, 48000, 4800, 1, 0.3), 48000);

    assert.equal(out.length, 1600);
    assert.ok(out.every((sample) => Math.abs(sample) < CLOSE));
  });

  it("returns a copy of audio that is already at 16 kHz", () => {
    const input = Float32Array.of(0.1, -0.2, 0.3);
    const out = downsampleTo16k(input, 16000);

    assert.notEqual(out, input);
    assertAllClose(out, input);
  });

  it("interpolates audio recorded below 16 kHz", () => {
    assertAllClose(downsampleTo16k(Float32Array.of(0, 1, 0.5), 8000), [0, 0.5, 1, 0.75, 0.5, 0.5]);
  });

  it("keeps an empty recording empty", () => {
    assert.equal(downsampleTo16k(new Float32Array(0), 48000).length, 0);
  });

  it("refuses a sample rate that is not a positive number", () => {
    for (const rate of [0, -48000, Number.NaN, Number.POSITIVE_INFINITY, "48000"]) {
      assert.throws(() => downsampleTo16k(Float32Array.of(1), rate), RangeError, String(rate));
    }
  });
});

describe("floatTo16BitPCM", () => {
  it("scales -1..1 to the full 16-bit range", () => {
    const out = floatTo16BitPCM(Float32Array.of(0, 1, -1, 0.5, -0.5));

    assert.ok(out instanceof Int16Array);
    assert.deepEqual([...out], [0, 32767, -32768, 16384, -16384]);
  });

  it("clips samples outside -1..1 and silences ones that are not numbers", () => {
    assert.deepEqual([...floatTo16BitPCM(Float32Array.of(1.5, -2, Number.NaN))], [32767, -32768, 0]);
  });
});

describe("encodeWav", () => {
  it("writes a mono 16-bit PCM RIFF header and little-endian samples", () => {
    const wav = encodeWav(Int16Array.of(1, -2, 32767, -32768), 16000);
    const view = new DataView(wav.buffer, wav.byteOffset, wav.byteLength);

    assert.ok(wav instanceof Uint8Array);
    assert.equal(wav.length, 44 + 8);
    assert.equal(ascii(wav, 0, 4), "RIFF");
    assert.equal(view.getUint32(4, true), 36 + 8);
    assert.equal(ascii(wav, 8, 4), "WAVE");
    assert.equal(ascii(wav, 12, 4), "fmt ");
    assert.equal(view.getUint32(16, true), 16);
    assert.equal(view.getUint16(20, true), 1, "PCM");
    assert.equal(view.getUint16(22, true), 1, "mono");
    assert.equal(view.getUint32(24, true), 16000);
    assert.equal(view.getUint32(28, true), 32000, "byte rate");
    assert.equal(view.getUint16(32, true), 2, "block align");
    assert.equal(view.getUint16(34, true), 16, "bits per sample");
    assert.equal(ascii(wav, 36, 4), "data");
    assert.equal(view.getUint32(40, true), 8);
    assert.deepEqual([...wav.subarray(44)], [0x01, 0x00, 0xfe, 0xff, 0xff, 0x7f, 0x00, 0x80]);
  });

  it("writes a valid empty file", () => {
    const wav = encodeWav(new Int16Array(0), 16000);

    assert.equal(wav.length, 44);
    assert.equal(new DataView(wav.buffer).getUint32(4, true), 36);
  });

  it("refuses a sample rate that is not a positive whole number", () => {
    for (const rate of [0, 16000.5, -1, Number.NaN]) {
      assert.throws(() => encodeWav(Int16Array.of(0), rate), RangeError, String(rate));
    }
  });
});

describe("talkWav", () => {
  it("joins the captured blocks into one 16 kHz WAV", () => {
    const blocks = [new Float32Array(4800).fill(0.5), new Float32Array(4800).fill(0.5)];
    const { wav, seconds } = talkWav(blocks, 48000);
    const view = new DataView(wav.buffer, wav.byteOffset, wav.byteLength);

    assert.equal(seconds, 0.2);
    assert.equal(view.getUint32(24, true), TALK_RATE);
    assert.equal(view.getUint32(40, true), 3200 * 2);
    assert.equal(view.getInt16(44, true), 16384);
    assert.equal(view.getInt16(wav.length - 2, true), 16384);
  });

  it("keeps at most the longest recording the server accepts", () => {
    const { wav, seconds } = talkWav([new Float32Array((MAX_TALK_S + 1) * TALK_RATE)], TALK_RATE);

    assert.equal(MAX_TALK_S, 60);
    assert.equal(seconds, MAX_TALK_S);
    assert.equal(wav.length, 44 + MAX_TALK_S * TALK_RATE * 2);
  });

  it("gives an empty WAV for nothing captured", () => {
    const { wav, seconds } = talkWav([], 48000);

    assert.equal(seconds, 0);
    assert.equal(wav.length, 44);
  });
});

describe("base64ToBytes", () => {
  it("decodes into a buffer of exactly the decoded bytes", () => {
    const bytes = base64ToBytes("AAEC/w==");

    assert.ok(bytes instanceof Uint8Array);
    assert.deepEqual([...bytes], [0, 1, 2, 255]);
    assert.equal(bytes.buffer.byteLength, 4);
  });

  it("decodes the empty string to no bytes", () => {
    assert.equal(base64ToBytes("").length, 0);
  });

  it("refuses text that is not base64", () => {
    assert.throws(() => base64ToBytes("@@@@"));
  });
});

describe("parseChatReply", () => {
  it("reads the text, emotion, audio, and mouth track", () => {
    assert.deepEqual(parseChatReply(reply()), reply());
  });

  it("shows an unknown emotion as neutral", () => {
    assert.equal(parseChatReply(reply({ emotion: "smug" })).emotion, "neutral");
    assert.equal(parseChatReply(reply({ emotion: undefined })).emotion, "neutral");
  });

  it("keeps a reply without audio", () => {
    assert.equal(parseChatReply(reply({ audio: null })).audio, null);
    assert.equal(parseChatReply(reply({ audio: 42 })).audio, null);
  });

  it("repairs a malformed mouth track so the renderer never breaks", () => {
    const parsed = parseChatReply(reply({ visemes: [[2, -1, "a", null, 0.5], "frame", [0.3]] }));

    assert.deepEqual(parsed.visemes, [
      [1, 0, 0, 0, 0.5],
      [0, 0, 0, 0, 0],
      [0.3, 0, 0, 0, 0],
    ]);
  });

  it("drops the mouth track without a usable frame rate", () => {
    for (const fps of [0, -60, "60", null]) {
      const parsed = parseChatReply(reply({ fps }));
      assert.equal(parsed.fps, 0);
      assert.deepEqual(parsed.visemes, []);
    }
    assert.deepEqual(parseChatReply(reply({ visemes: "none" })).visemes, []);
  });

  it("reads a missing or negative duration as unknown (0)", () => {
    assert.equal(parseChatReply(reply({ duration: undefined })).duration, 0);
    assert.equal(parseChatReply(reply({ duration: -1 })).duration, 0);
  });

  it("keeps her text card", () => {
    const card = { title: "Cuaca 7 hari", body: "- Senin cerah\n- https://bmkg.go.id" };

    assert.deepEqual(parseChatReply(reply({ card })).card, card);
  });

  it("reads a missing or malformed card as none", () => {
    for (const card of [undefined, "kartu", { title: "Judul" }, { title: 1, body: "- satu" }, { title: "", body: " " }]) {
      assert.equal(parseChatReply(reply({ card })).card, null);
    }
  });

  it("refuses a reply without text", () => {
    assert.equal(parseChatReply(reply({ text: null })), null);
    assert.equal(parseChatReply(null), null);
    assert.equal(parseChatReply([reply()]), null);
  });
});

describe("parseTalkReply", () => {
  it("reads what was heard and the reply to it", () => {
    assert.deepEqual(parseTalkReply({ heard: "nyalakan lampu", ...reply() }), {
      heard: "nyalakan lampu",
      reply: reply(),
    });
  });

  it("reads an empty transcript as nothing heard and no reply", () => {
    assert.deepEqual(parseTalkReply({ heard: "", text: null }), { heard: "", reply: null });
  });

  it("refuses an answer without a transcript or with a malformed reply", () => {
    assert.equal(parseTalkReply(reply()), null);
    assert.equal(parseTalkReply({ heard: "halo", text: 3 }), null);
    assert.equal(parseTalkReply("halo"), null);
  });
});

describe("speakEventFor", () => {
  it("is a speak event for the renderer that starts now", () => {
    assert.deepEqual(speakEventFor(reply(), 0.5), {
      emotion: "happy",
      fps: 60,
      visemes: reply().visemes,
      duration: 0.03,
      elapsed: 0,
    });
  });

  it("lasts as long as the decoded audio when the reply gives no duration", () => {
    assert.equal(speakEventFor(reply({ duration: 0 }), 1.25).duration, 1.25);
  });
});

describe("parsePhoneConfig", () => {
  const live2d = {
    engine: "live2d",
    model: "/model/Cyrene.model3.json",
    core: "/live2d/live2dcubismcore.min.js",
    credit: "Cyrene20251105 by MomokaSono",
    owner_call: "sayang",
    name: "Cyrene",
  };

  it("reads the engine, model, core, credit, name, and how she calls the owner", () => {
    assert.deepEqual(parsePhoneConfig(live2d), live2d);
  });

  it("fills in the defaults of missing texts", () => {
    assert.deepEqual(parsePhoneConfig({ engine: "vrm", model: "/model.vrm", core: null }), {
      engine: "vrm",
      model: "/model.vrm",
      core: null,
      credit: "",
      owner_call: "Master",
      name: "Cyrene",
    });
  });

  it("reads settings without a model, so the chat works without the avatar", () => {
    // The PC sends model null while avatar.model is not set.
    assert.deepEqual(parsePhoneConfig({ ...live2d, model: null }), { ...live2d, model: null });
  });

  it("refuses settings no page can use", () => {
    assert.equal(parsePhoneConfig({ ...live2d, engine: "spine" }), null);
    assert.equal(parsePhoneConfig({ ...live2d, core: null }), null);
    assert.equal(parsePhoneConfig({ ...live2d, model: 7 }), null);
    assert.equal(parsePhoneConfig({ ...live2d, model: undefined }), null);
    assert.equal(parsePhoneConfig("live2d"), null);
  });
});

describe("problemForStatus", () => {
  it("sends an unpaired phone to the pairing form from every route", () => {
    for (const route of ["/config.json", "/chat", "/talk"]) assert.equal(problemForStatus(route, 401), "unpaired");
  });

  it("tells a long text from a long recording", () => {
    assert.equal(problemForStatus("/chat", 413), "too_long_text");
    assert.equal(problemForStatus("/talk", 413), "too_long_audio");
  });

  it("reads 429 as busy, and as too many tries when pairing", () => {
    assert.equal(problemForStatus("/chat", 429), "busy");
    assert.equal(problemForStatus("/talk", 429), "busy");
    assert.equal(problemForStatus("/pair", 429), "pair_locked");
  });

  it("reads 403 as a wrong code when pairing and as a refused address elsewhere", () => {
    assert.equal(problemForStatus("/pair", 403), "pair_wrong");
    assert.equal(problemForStatus("/chat", 403), "forbidden");
  });

  it("reads a quick button the PC no longer has, and one pressed too often", () => {
    assert.equal(problemForStatus("/action", 404), "action_unknown");
    assert.equal(problemForStatus("/action", 429), "action_limit");
    assert.equal(problemForStatus("/action", 401), "unpaired");
    assert.equal(problemForStatus("/action", 403), "forbidden");
    assert.equal(problemForStatus("/action", 500), "server");
  });

  it("tells a quick button whose last press still runs from one pressed too often", () => {
    assert.equal(problemForStatus("/action", 409), "action_busy");
    assert.equal(problemText("action_busy"), "Perintah tadi masih jalan. Tunggu sebentar.");
    assert.notEqual(problemText("action_busy"), problemText("action_limit"));
    assert.equal(problemForStatus("/chat", 409), "server");
  });

  it("reads the Suara sheet hidden in guest mode, missing, changed too often, or still changing", () => {
    for (const route of ["/voice", "/voice/set"]) {
      assert.equal(problemForStatus(route, 403), "voice_hidden");
      assert.equal(problemForStatus(route, 404), "voice_missing");
      assert.equal(problemForStatus(route, 401), "unpaired");
    }
    assert.equal(problemForStatus("/voice/set", 429), "voice_limit");
    assert.equal(problemForStatus("/voice/set", 409), "voice_busy");
    assert.equal(problemText("voice_hidden"), "Diagnosa suara disembunyikan selama mode tamu.");
  });

  it("reads a gateway error as a PC that cannot be reached", () => {
    // tailscale serve answers 502 for the PC while Cyrene (waifu run) is not running.
    for (const route of ["/config.json", "/chat", "/talk", "/pair"]) {
      for (const status of [502, 503, 504]) assert.equal(problemForStatus(route, status), "offline", `${route} ${status}`);
    }
  });

  it("reads other statuses as a bad request or a problem on the PC", () => {
    assert.equal(problemForStatus("/chat", 400), "bad_request");
    assert.equal(problemForStatus("/talk", 415), "bad_request");
    for (const status of [404, 500, 501]) assert.equal(problemForStatus("/chat", status), "server");
  });
});

describe("chatTextFrom", () => {
  it("trims the message", () => {
    assert.deepEqual(chatTextFrom("  nyalakan lampu kamar \n"), { text: "nyalakan lampu kamar" });
  });

  it("sends nothing for an empty message", () => {
    assert.deepEqual(chatTextFrom(" \n\t "), { problem: "empty" });
  });

  it("counts characters as the server does, at most MAX_CHAT_CHARS", () => {
    assert.equal(MAX_CHAT_CHARS, 2000);
    assert.deepEqual(chatTextFrom("😀".repeat(MAX_CHAT_CHARS)), { text: "😀".repeat(MAX_CHAT_CHARS) });
    assert.deepEqual(chatTextFrom("a".repeat(MAX_CHAT_CHARS + 1)), { problem: "too_long_text" });
  });
});

describe("pairCodeFrom", () => {
  it("reads six digits, also with spaces, dashes, or full-width digits", () => {
    for (const typed of ["123456", " 123 456 ", "123-456", "１２３４５６"]) {
      assert.equal(pairCodeFrom(typed), "123456", typed);
    }
  });

  it("refuses anything else", () => {
    // Arabic-Indic digits are digits, but not the ones on the PC's screen.
    for (const typed of ["", "12345", "1234567", "12a456", "١٢٣٤٥٦"]) {
      assert.equal(pairCodeFrom(typed), null, typed);
    }
  });
});

describe("microphoneProblem", () => {
  it("tells a refused permission from a missing microphone", () => {
    assert.equal(microphoneProblem({ name: "NotAllowedError" }), "mic_denied");
    assert.equal(microphoneProblem({ name: "SecurityError" }), "mic_denied");
    assert.equal(microphoneProblem({ name: "NotFoundError" }), "mic_unavailable");
    assert.equal(microphoneProblem(new TypeError("navigator.mediaDevices is undefined")), "mic_unavailable");
  });
});

describe("problemText", () => {
  it("speaks to the owner the way they asked her to", () => {
    assert.equal(problemText("busy", "sayang"), "Sebentar ya, sayang, aku masih menjawab yang tadi.");
    assert.equal(problemText("not_heard", "sayang"), "Maaf, sayang, suaramu tidak tertangkap. Coba lagi ya.");
  });

  it("calls the owner Master by default", () => {
    assert.equal(problemText("busy"), "Sebentar ya, Master, aku masih menjawab yang tadi.");
  });

  it("has an Indonesian text for every problem, with nothing left to fill", () => {
    for (const kind of PROBLEM_KINDS) {
      const text = problemText(kind, "Master");
      assert.ok(text.length > 0, kind);
      assert.ok(!/[{}]/.test(text), kind);
    }
  });

  it("names the limits", () => {
    assert.match(problemText("too_long_text"), /2000/);
    assert.match(problemText("too_long_audio"), /60 detik/);
  });

  it("reads an unknown problem as a problem on the PC", () => {
    assert.equal(problemText("gremlins"), problemText("server"));
  });
});

describe("requestCyrene", () => {
  function fakeFetch(answer) {
    const calls = [];
    const fetch = async (url, init) => {
      calls.push({ url, init });
      return typeof answer === "function" ? answer(init) : answer;
    };
    return { fetch, calls };
  }

  function response(status, body) {
    return {
      status,
      ok: status >= 200 && status < 300,
      async json() {
        if (body instanceof Error) throw body;
        return body;
      },
    };
  }

  it("posts the body with its type and the page's own cookie, never from the cache", async () => {
    const { fetch, calls } = fakeFetch(response(200, reply()));

    const result = await requestCyrene(fetch, "/chat", { body: '{"text":"halo"}', contentType: "application/json" });

    assert.deepEqual(result, { data: reply() });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "/chat");
    assert.equal(calls[0].init.method, "POST");
    assert.deepEqual(calls[0].init.headers, { "Content-Type": "application/json" });
    assert.equal(calls[0].init.body, '{"text":"halo"}');
    assert.equal(calls[0].init.credentials, "same-origin");
    assert.equal(calls[0].init.cache, "no-store");
  });

  it("gets without a body", async () => {
    const { fetch, calls } = fakeFetch(response(200, { engine: "vrm" }));

    assert.deepEqual(await requestCyrene(fetch, "/config.json"), { data: { engine: "vrm" } });
    assert.equal(calls[0].init.method, "GET");
    assert.equal(calls[0].init.body, undefined);
  });

  it("turns an error status into the problem to show", async () => {
    assert.deepEqual(await requestCyrene(fakeFetch(response(429, null)).fetch, "/chat", { body: "{}" }), {
      problem: "busy",
    });
    assert.deepEqual(await requestCyrene(fakeFetch(response(401, null)).fetch, "/config.json"), {
      problem: "unpaired",
    });
  });

  it("reads a failed connection as offline", async () => {
    const fetch = async () => {
      throw new TypeError("Failed to fetch");
    };

    assert.deepEqual(await requestCyrene(fetch, "/chat", { body: "{}" }), { problem: "offline" });
  });

  it("reads an answer that is not JSON as unreadable", async () => {
    const { fetch } = fakeFetch(response(200, new SyntaxError("Unexpected token <")));

    assert.deepEqual(await requestCyrene(fetch, "/chat", { body: "{}" }), { problem: "bad_reply" });
  });

  it("does not read the body of an answer that has none", async () => {
    const { fetch } = fakeFetch(response(204, new SyntaxError("no body")));

    assert.deepEqual(await requestCyrene(fetch, "/pair", { body: "{}", json: false }), { data: null });
  });

  it("gives up on a PC that does not answer in time", async () => {
    const fetch = (url, init) =>
      new Promise((resolve, reject) => {
        init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });

    assert.deepEqual(await requestCyrene(fetch, "/chat", { body: "{}", timeoutMs: 5 }), { problem: "timeout" });
  });
});
