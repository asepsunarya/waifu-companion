// The phone page's microphone worklet, run against a fake AudioWorklet scope.
import assert from "node:assert/strict";
import { before, describe, it } from "node:test";

const processors = new Map();

class FakeAudioWorkletProcessor {
  constructor() {
    this.port = {
      sent: [],
      onmessage: null,
      postMessage(message) {
        this.sent.push(message);
      },
    };
  }
}

before(async () => {
  globalThis.AudioWorkletProcessor = FakeAudioWorkletProcessor;
  globalThis.registerProcessor = (name, processor) => processors.set(name, processor);
  await import("../../src/waifu/avatar/web/phone/capture-worklet.js");
});

function createProcessor() {
  const Processor = processors.get("cyrene-capture");
  assert.ok(Processor, "registered as cyrene-capture");
  return new Processor();
}

describe("capture worklet", () => {
  it("hands each block of the microphone to the page as a copy", () => {
    const processor = createProcessor();
    const block = Float32Array.of(0.1, -0.2, 0.3);

    const alive = processor.process([[block]]);
    block.fill(0);

    assert.equal(alive, true);
    assert.equal(processor.port.sent.length, 1);
    assert.ok(processor.port.sent[0] instanceof Float32Array);
    assert.deepEqual([...processor.port.sent[0]], [...Float32Array.of(0.1, -0.2, 0.3)]);
  });

  it("waits while no microphone is connected", () => {
    const processor = createProcessor();

    assert.equal(processor.process([[]]), true);
    assert.deepEqual(processor.port.sent, []);
  });

  it("ends with a marker after every block once the page says stop, then lets itself go", () => {
    const processor = createProcessor();
    processor.process([[Float32Array.of(0.5)]]);

    processor.port.onmessage({ data: "stop" });
    const alive = processor.process([[Float32Array.of(0.7)]]);

    assert.equal(alive, false);
    assert.equal(processor.port.sent.length, 2);
    assert.equal(processor.port.sent[1], null);
  });
});
