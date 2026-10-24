// The phone page's Suara sheet, its pure part (voice.js): the status GET /voice answers, where the level and the gate
// sit on the bar, how each utterance's record reads, and the bodies of POST /voice/set.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  levelPercent,
  micRequest,
  outcomeClass,
  outcomeLabel,
  parseVoiceStatus,
  recordLines,
  resetRequest,
  tuneRequest,
  tuningFrom,
  voiceOutcome,
} from "../../src/waifu/avatar/web/phone/voice.js";

const RECORD = {
  at: "19:30:05",
  length_s: 1.24,
  peak_db: -20.04,
  rms_db: -26.5,
  outcome: "accepted",
  transcript: "Cyrene, nyalakan lampu",
  wake: "wake_word",
  score: 0.71,
  threshold: 0.45,
  stt_s: 0.43,
  brain_s: 2.1,
  tts_s: 0.75,
};
const STATUS = {
  mic: { name: "Headset (Polaris-BT)", host_api: "MME", value: "Headset (Polaris-BT), MME" },
  windows_default: "Microphone (High Definition Aud",
  preferences: ["Headset (Polaris-BT), MME", null],
  override_mic: null,
  devices: [
    { value: "Headset (Polaris-BT), MME", name: "Headset (Polaris-BT)", host_api: "MME" },
    { value: "Microphone (High Definition Aud, MME", name: "Microphone (High Definition Aud", host_api: "MME" },
  ],
  level: { rms_db: -51.2, peak_db: -40.1, loudest_db: -45.5 },
  vad: {
    floor_db: -60.0,
    bar_db: -45.0,
    threshold_db: -45.0,
    margin_db: 12.0,
    default_threshold_db: -45.0,
    default_margin_db: 12.0,
    threshold_bounds: [-70.0, -25.0],
    margin_bounds: [3.0, 30.0],
  },
  speaker: { enabled: true, threshold: 0.45, default: 0.45, bounds: [0.3, 0.7] },
  switch: { pending: false, last: null },
  records: [RECORD],
};

describe("parseVoiceStatus", () => {
  it("keeps what the sheet shows", () => {
    const status = parseVoiceStatus(STATUS);

    assert.deepEqual(status.mic, STATUS.mic);
    assert.equal(status.windowsDefault, STATUS.windows_default);
    assert.equal(status.overrideMic, null);
    assert.deepEqual(status.devices, STATUS.devices);
    assert.deepEqual(status.level, { rmsDb: -51.2, peakDb: -40.1, loudestDb: -45.5 });
    assert.equal(status.vad.barDb, -45);
    assert.deepEqual(status.vad.thresholdBounds, [-70, -25]);
    assert.deepEqual(status.speaker, { enabled: true, threshold: 0.45, default: 0.45, bounds: [0.3, 0.7] });
    assert.deepEqual(status.switch, { pending: false, last: null });
    assert.equal(status.records.length, 1);
  });

  it("reads a mic that does not open yet, a level not measured yet, and speaker checking off", () => {
    const status = parseVoiceStatus({
      ...STATUS,
      mic: null,
      level: null,
      speaker: { enabled: false, threshold: null, default: 0.45, bounds: [0.3, 0.7] },
    });

    assert.equal(status.mic, null);
    assert.equal(status.level, null);
    assert.equal(status.speaker.enabled, false);
  });

  it("drops a device or a record that is not well formed", () => {
    const status = parseVoiceStatus({
      ...STATUS,
      devices: [...STATUS.devices, { value: 3, name: "Nomor" }, null],
      records: [RECORD, { at: "19:31:00" }, "rekaman"],
    });

    assert.equal(status.devices.length, 2);
    assert.equal(status.records.length, 1);
  });

  it("is null for an answer it cannot use", () => {
    for (const data of [null, [], "status", { ...STATUS, vad: null }, { ...STATUS, speaker: "aktif" }]) {
      assert.equal(parseVoiceStatus(data), null);
    }
  });
});

describe("levelPercent", () => {
  it("places a level from -80 dB (empty) to 0 dB (full)", () => {
    assert.equal(levelPercent(-80), 0);
    assert.equal(levelPercent(-40), 50);
    assert.equal(levelPercent(0), 100);
  });

  it("keeps a level out of range on the bar, and nothing measured at its start", () => {
    assert.equal(levelPercent(-200), 0);
    assert.equal(levelPercent(6), 100);
    assert.equal(levelPercent(null), 0);
    assert.equal(levelPercent(Number.NaN), 0);
  });
});

describe("outcomes", () => {
  it("name what she did with each utterance in Indonesian", () => {
    assert.deepEqual(
      ["accepted", "not-owner", "no-wake", "empty", "too-short"].map(outcomeLabel),
      ["dijawab", "bukan suaramu", "tanpa nama", "tak ada kata", "terlalu pendek"],
    );
  });

  it("colour each by its own class, an unknown one alike", () => {
    assert.equal(outcomeClass("not-owner"), "outcome-not-owner");
    assert.equal(outcomeClass("<script>"), "outcome-other");
  });
});

describe("recordLines", () => {
  it("read a record with its time, outcome, transcript, level, score, and timings", () => {
    assert.deepEqual(recordLines(RECORD), {
      title: "19:30:05 · dijawab",
      transcript: "“Cyrene, nyalakan lampu”",
      detail: "1,2 dtk · puncak -20 dB · skor 0,71 (butuh 0,45) · Whisper 0,43 dtk · otak 2,10 dtk · suara 0,75 dtk",
    });
  });

  it("leave out what is not known", () => {
    const shortOne = { ...RECORD, outcome: "too-short", transcript: null, score: null, threshold: null };

    assert.deepEqual(recordLines({ ...shortOne, stt_s: null, brain_s: null, tts_s: null }), {
      title: "19:30:05 · terlalu pendek",
      transcript: null,
      detail: "1,2 dtk · puncak -20 dB",
    });
  });
});

describe("the requests", () => {
  it("switch to a listed mic, or follow the config again", () => {
    assert.deepEqual(micRequest("Headset (Polaris-BT), MME"), {
      body: JSON.stringify({ action: "mic", device: "Headset (Polaris-BT), MME" }),
      contentType: "application/json",
    });
    assert.equal(micRequest("").body, JSON.stringify({ action: "mic", device: null }));
  });

  it("change the settings given, and put the config's back", () => {
    assert.equal(
      tuneRequest({ speaker_threshold: 0.5, vad_threshold_db: -50, vad_margin_db: 10 }).body,
      JSON.stringify({ action: "tune", speaker_threshold: 0.5, vad_threshold_db: -50, vad_margin_db: 10 }),
    );
    assert.equal(tuneRequest({ vad_margin_db: 10 }).body, JSON.stringify({ action: "tune", vad_margin_db: 10 }));
    assert.equal(resetRequest().body, JSON.stringify({ action: "reset" }));
  });
});

describe("tuningFrom", () => {
  const status = parseVoiceStatus(STATUS);

  it("reads the boxes, a decimal comma too", () => {
    assert.deepEqual(tuningFrom({ speaker: "0,5", threshold: "-50", margin: "10.5" }, status), {
      tuning: { speaker_threshold: 0.5, vad_threshold_db: -50, vad_margin_db: 10.5 },
    });
  });

  it("leaves out the speaker threshold while speaker checking is off", () => {
    const off = { ...status, speaker: { ...status.speaker, enabled: false } };

    assert.deepEqual(tuningFrom({ speaker: "", threshold: "-50", margin: "10" }, off), {
      tuning: { vad_threshold_db: -50, vad_margin_db: 10 },
    });
  });

  it("sends only the settings changed from those in use, so the others keep following config.yaml", () => {
    assert.deepEqual(tuningFrom({ speaker: "0,45", threshold: "-45", margin: "10" }, status), {
      tuning: { vad_margin_db: 10 },
    });
  });

  it("leaves alone an untouched setting the config holds out of the page's bounds", () => {
    const strict = { ...status, speaker: { ...status.speaker, threshold: 0.75 } };

    assert.deepEqual(tuningFrom({ speaker: "0.75", threshold: "-50", margin: "12" }, strict), {
      tuning: { vad_threshold_db: -50 },
    });
  });

  it("refuses a save that changes nothing, without asking the PC", () => {
    assert.deepEqual(tuningFrom({ speaker: "0.45", threshold: "-45", margin: "12" }, status), {
      problem: "Tidak ada setelan yang diubah.",
    });
  });

  it("refuses a value out of its bounds or not a number, saying the bounds", () => {
    assert.deepEqual(tuningFrom({ speaker: "0.8", threshold: "-45", margin: "12" }, status), {
      problem: "Ambang kecocokan suara harus 0,30 sampai 0,70.",
    });
    assert.deepEqual(tuningFrom({ speaker: "0.45", threshold: "-20", margin: "12" }, status), {
      problem: "Ambang VAD harus -70 sampai -25 dB.",
    });
    assert.deepEqual(tuningFrom({ speaker: "0.45", threshold: "-45", margin: "dua belas" }, status), {
      problem: "Margin VAD harus 3 sampai 30 dB.",
    });
  });
});

describe("voiceOutcome", () => {
  it("reads POST /voice/set's answer", () => {
    assert.deepEqual(voiceOutcome({ ok: true, message: "Mic sedang diganti.", pending: true }), {
      ok: true,
      text: "Mic sedang diganti.",
      pending: true,
    });
  });

  it("is null otherwise", () => {
    for (const data of [null, { ok: "ya", message: "x", pending: false }, { ok: true, message: "", pending: false }]) {
      assert.equal(voiceOutcome(data), null);
    }
  });
});
