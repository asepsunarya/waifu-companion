// Hands the owner's microphone to the phone page while the talk button is held: phone.js connects the microphone to
// an AudioWorkletNode of this processor with one input channel (the browser mixes a stereo microphone down to mono),
// receives a copy of every block at the AudioContext's rate, and posts "stop" when the button is released. The
// processor then posts null after its last block and lets the browser end it.
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.stopped = false;
    this.port.onmessage = () => {
      this.stopped = true;
      this.port.postMessage(null);
    };
  }

  process(inputs) {
    if (this.stopped) return false;
    const channel = inputs[0]?.[0];
    if (channel) {
      // The block's buffer belongs to the audio thread and is reused for the next block.
      const copy = channel.slice();
      this.port.postMessage(copy, [copy.buffer]);
    }
    return true;
  }
}

registerProcessor("cyrene-capture", CaptureProcessor);
