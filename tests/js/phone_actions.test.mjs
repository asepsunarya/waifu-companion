// The phone page's quick buttons, their pure part (actions.js): the list GET /actions answers, the body of
// POST /action, and what its answer tells the owner.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  actionOutcome,
  actionRequest,
  isAlarmAction,
  isPassingAction,
  parseActionGroups,
} from "../../src/waifu/avatar/web/phone/actions.js";

const LAMP = {
  name: "Lampu kamar",
  actions: [
    { id: "dev.lampu_kamar.on", label: "Nyala" },
    { id: "dev.lampu_kamar.off", label: "Mati" },
  ],
};
const ROUTINES = { name: "Rutinitas", actions: [{ id: "rt.tidur", label: "Mode tidur" }] };

describe("parseActionGroups", () => {
  it("keeps the groups and their buttons in the PC's order", () => {
    assert.deepEqual(parseActionGroups({ groups: [LAMP, ROUTINES] }), [LAMP, ROUTINES]);
  });

  it("reads a PC without buttons as no groups", () => {
    assert.deepEqual(parseActionGroups({ groups: [] }), []);
  });

  it("drops a button that is not well formed, and keeps only the id and label of the rest", () => {
    const group = {
      name: "Lampu kamar",
      actions: [
        { id: "dev.lampu_kamar.on", label: "Nyala", device: "lampu_kamar" },
        { id: "", label: "Kosong" },
        { id: "dev.lampu_kamar.off" },
        { id: 7, label: "Tujuh" },
        { id: "dev.lampu_kamar.toggle", label: "  " },
        null,
        "dev.lampu_kamar.on",
      ],
    };

    assert.deepEqual(parseActionGroups({ groups: [group] }), [
      { name: "Lampu kamar", actions: [{ id: "dev.lampu_kamar.on", label: "Nyala" }] },
    ]);
  });

  it("drops a group that is not well formed or has no button left", () => {
    const groups = [
      { name: "", actions: LAMP.actions },
      { name: "Tanpa daftar", actions: "dev.x.on" },
      { name: "Kosong", actions: [] },
      { name: "Rusak", actions: [{ id: 1, label: 2 }] },
      [LAMP],
      ROUTINES,
    ];

    assert.deepEqual(parseActionGroups({ groups }), [ROUTINES]);
  });

  it("refuses an answer without a list of groups", () => {
    for (const data of [null, "groups", [], {}, { groups: "Lampu" }, { groups: null }]) {
      assert.equal(parseActionGroups(data), null, JSON.stringify(data));
    }
  });
});

describe("isAlarmAction", () => {
  it("tells a ringing alarm's buttons from the devices' and the routines'", () => {
    assert.equal(isAlarmAction("al.stop"), true);
    assert.equal(isAlarmAction("al.snooze"), true);
    for (const id of ["dev.lampu_kamar.on", "rt.tidur", "dev.alarm.on"]) assert.equal(isAlarmAction(id), false, id);
  });
});

describe("isPassingAction", () => {
  it("tells the buttons that show only for a while: a ringing alarm's and Dengar lagi", () => {
    for (const id of ["al.stop", "al.snooze", "mic.listen"]) assert.equal(isPassingAction(id), true, id);
    for (const id of ["dev.lampu_kamar.on", "rt.tidur", "dev.mic.on", "rt.mic"]) {
      assert.equal(isPassingAction(id), false, id);
    }
  });

  it("leaves Dengar lagi out of the alarm's buttons", () => {
    assert.equal(isAlarmAction("mic.listen"), false);
  });
});

describe("actionRequest", () => {
  it("sends only the button's id, as JSON", () => {
    assert.deepEqual(actionRequest("dev.lampu_kamar.on"), {
      body: '{"id":"dev.lampu_kamar.on"}',
      contentType: "application/json",
    });
  });
});

describe("actionOutcome", () => {
  it("tells what the PC did", () => {
    assert.deepEqual(actionOutcome({ ok: true, message: "Lampu kamar dinyalakan." }), {
      ok: true,
      text: "Lampu kamar dinyalakan.",
    });
  });

  it("tells a device's failure as the PC says it", () => {
    assert.deepEqual(actionOutcome({ ok: false, message: "Kipas tidak menjawab." }), {
      ok: false,
      text: "Kipas tidak menjawab.",
    });
  });

  it("cannot read anything else", () => {
    for (const data of [
      null,
      "ok",
      { ok: true },
      { message: "Lampu kamar dinyalakan." },
      { ok: "true", message: "Lampu kamar dinyalakan." },
      { ok: true, message: "" },
      { ok: true, message: 7 },
    ]) {
      assert.equal(actionOutcome(data), null, JSON.stringify(data));
    }
  });
});
