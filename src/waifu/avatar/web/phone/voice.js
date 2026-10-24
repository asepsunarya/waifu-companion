// Pure helpers of the phone page's Suara sheet (SPEC, "diagnosa suara (v1.47)"): what Cyrene's mic on the PC hears,
// what she did with each utterance, and the mic and voice settings the owner may change from the phone. The PC
// answers GET /voice with its status and takes POST /voice/set; the page only ever names a mic the PC listed. No DOM
// here, so `node --test` covers them.

const JSON_TYPE = "application/json";
// The bar: empty at BAR_FLOOR_DB, full at 0 dB (full scale), as `waifu mic` draws it.
const BAR_FLOOR_DB = -80;
const OUTCOME_LABELS = Object.freeze({
  accepted: "dijawab",
  "not-owner": "bukan suaramu",
  "no-wake": "tanpa nama",
  empty: "tak ada kata",
  "too-short": "terlalu pendek",
});

const isPlainObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const isText = (value) => typeof value === "string" && value.trim() !== "";
const isNumber = (value) => typeof value === "number" && Number.isFinite(value);
const numberOrNull = (value) => (isNumber(value) ? value : null);
const textOrNull = (value) => (isText(value) ? value : null);
const isBounds = (value) => Array.isArray(value) && value.length === 2 && value.every(isNumber);

/** `value` with `places` decimals and a decimal comma, as Indonesian writes it. */
const decimal = (value, places) => value.toFixed(places).replace(".", ",");

function parseDevice(device) {
  if (!isPlainObject(device) || !isText(device.value) || !isText(device.name)) return null;
  return { value: device.value, name: device.name, host_api: textOrNull(device.host_api) };
}

function parseRecord(record) {
  if (!isPlainObject(record) || !isText(record.at) || !isText(record.outcome)) return null;
  if (!isNumber(record.length_s) || !isNumber(record.peak_db)) return null;
  return {
    at: record.at,
    length_s: record.length_s,
    peak_db: record.peak_db,
    outcome: record.outcome,
    transcript: textOrNull(record.transcript),
    score: numberOrNull(record.score),
    threshold: numberOrNull(record.threshold),
    stt_s: numberOrNull(record.stt_s),
    brain_s: numberOrNull(record.brain_s),
    tts_s: numberOrNull(record.tts_s),
  };
}

function parseVad(vad) {
  if (!isPlainObject(vad) || !isNumber(vad.threshold_db) || !isNumber(vad.margin_db)) return null;
  if (!isBounds(vad.threshold_bounds) || !isBounds(vad.margin_bounds)) return null;
  return {
    floorDb: numberOrNull(vad.floor_db),
    barDb: numberOrNull(vad.bar_db),
    thresholdDb: vad.threshold_db,
    marginDb: vad.margin_db,
    thresholdBounds: vad.threshold_bounds,
    marginBounds: vad.margin_bounds,
  };
}

function parseSpeaker(speaker) {
  if (!isPlainObject(speaker) || typeof speaker.enabled !== "boolean" || !isBounds(speaker.bounds)) return null;
  return {
    enabled: speaker.enabled,
    threshold: numberOrNull(speaker.threshold),
    default: numberOrNull(speaker.default),
    bounds: speaker.bounds,
  };
}

/**
 * GET /voice's answer as the sheet shows it: the mic (null while none opens), the Windows default, the mic picked on
 * the phone, the mics the PC lists, the level (null before a second was measured), the VAD's gate, the speaker
 * threshold, the last switch, and the records, newest first. A device or a record that is not well formed is
 * dropped; null when the answer has no gate or speaker settings.
 */
export function parseVoiceStatus(data) {
  if (!isPlainObject(data)) return null;
  const vad = parseVad(data.vad);
  const speaker = parseSpeaker(data.speaker);
  if (vad === null || speaker === null) return null;
  const mic = isPlainObject(data.mic) && isText(data.mic.name) ? data.mic : null;
  const level =
    isPlainObject(data.level) && ["rms_db", "peak_db", "loudest_db"].every((key) => isNumber(data.level[key]))
      ? { rmsDb: data.level.rms_db, peakDb: data.level.peak_db, loudestDb: data.level.loudest_db }
      : null;
  const last = data.switch?.last;
  return {
    mic,
    windowsDefault: textOrNull(data.windows_default),
    overrideMic: textOrNull(data.override_mic),
    devices: (Array.isArray(data.devices) ? data.devices : []).map(parseDevice).filter(Boolean),
    level,
    vad,
    speaker,
    switch: {
      pending: data.switch?.pending === true,
      last: isPlainObject(last) && typeof last.ok === "boolean" && isText(last.message) ? last : null,
    },
    records: (Array.isArray(data.records) ? data.records : []).map(parseRecord).filter(Boolean),
  };
}

/** Where `db` sits on the bar, in percent from its empty end; 0 for nothing measured. */
export function levelPercent(db) {
  if (!isNumber(db)) return 0;
  return Math.min(100, Math.max(0, ((db - BAR_FLOOR_DB) / -BAR_FLOOR_DB) * 100));
}

/** What she did with an utterance, in Indonesian (an unknown outcome as the PC named it). */
export function outcomeLabel(outcome) {
  return OUTCOME_LABELS[outcome] ?? outcome;
}

/** The class that colours a record by its outcome. */
export function outcomeClass(outcome) {
  return Object.hasOwn(OUTCOME_LABELS, outcome) ? `outcome-${outcome}` : "outcome-other";
}

/** A record as the sheet's lines: {title, transcript (null without one), detail}. */
export function recordLines(record) {
  const detail = [`${decimal(record.length_s, 1)} dtk`, `puncak ${Math.round(record.peak_db)} dB`];
  if (record.score !== null && record.threshold !== null) {
    detail.push(`skor ${decimal(record.score, 2)} (butuh ${decimal(record.threshold, 2)})`);
  }
  for (const [label, seconds] of [
    ["Whisper", record.stt_s],
    ["otak", record.brain_s],
    ["suara", record.tts_s],
  ]) {
    if (seconds !== null) detail.push(`${label} ${decimal(seconds, 2)} dtk`);
  }
  return {
    title: `${record.at} · ${outcomeLabel(record.outcome)}`,
    transcript: record.transcript === null ? null : `“${record.transcript}”`,
    detail: detail.join(" · "),
  };
}

const request = (body) => ({ body: JSON.stringify(body), contentType: JSON_TYPE });

/** POST /voice/set for the mic `value` the PC listed; an empty value follows the config again. */
export function micRequest(value) {
  return request({ action: "mic", device: value === "" ? null : value });
}

/** POST /voice/set for the settings in `tuning` (speaker_threshold, vad_threshold_db, vad_margin_db). */
export function tuneRequest(tuning) {
  return request({ action: "tune", ...tuning });
}

/** POST /voice/set putting the config's settings back. */
export function resetRequest() {
  return request({ action: "reset" });
}

/** A typed number, a decimal comma allowed; NaN for anything else. */
function typedNumber(text) {
  const written = String(text).trim().replace(",", ".");
  return /^-?\d+(\.\d+)?$/.test(written) ? Number(written) : Number.NaN;
}

function within(text, [low, high], problem) {
  const value = typedNumber(text);
  return value >= low && value <= high ? { value } : { problem };
}

/**
 * The boxes {speaker, threshold, margin} as POST /voice/set's settings ({tuning}), each checked against the bounds of
 * `status`, or {problem} (Indonesian) for the first out of them. The speaker threshold only while speaker checking is
 * on.
 */
export function tuningFrom(values, status) {
  const [speakerLow, speakerHigh] = status.speaker.bounds;
  const [thresholdLow, thresholdHigh] = status.vad.thresholdBounds;
  const [marginLow, marginHigh] = status.vad.marginBounds;
  const checks = [
    [
      "speaker_threshold",
      status.speaker.enabled,
      values.speaker,
      status.speaker.bounds,
      `Ambang kecocokan suara harus ${decimal(speakerLow, 2)} sampai ${decimal(speakerHigh, 2)}.`,
    ],
    [
      "vad_threshold_db",
      true,
      values.threshold,
      status.vad.thresholdBounds,
      `Ambang VAD harus ${thresholdLow} sampai ${thresholdHigh} dB.`,
    ],
    [
      "vad_margin_db",
      true,
      values.margin,
      status.vad.marginBounds,
      `Margin VAD harus ${marginLow} sampai ${marginHigh} dB.`,
    ],
  ];
  const tuning = {};
  for (const [name, used, text, bounds, problem] of checks) {
    if (!used) continue;
    const checked = within(text, bounds, problem);
    if (checked.problem) return { problem: checked.problem };
    tuning[name] = checked.value;
  }
  return { tuning };
}

/** POST /voice/set's answer {ok, message, pending} as {ok, text, pending}; null otherwise. */
export function voiceOutcome(data) {
  if (!isPlainObject(data) || typeof data.ok !== "boolean" || !isText(data.message)) return null;
  return { ok: data.ok, text: data.message, pending: data.pending === true };
}
