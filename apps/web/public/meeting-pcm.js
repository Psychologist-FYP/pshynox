/* Capture continuously on the audio rendering thread. Each message is a complete
   PCM slice; no MediaRecorder container fragments or stop/start gaps.

   currentFrame advances once per render quantum. If the audio thread skips quanta
   for this node (a glitch or a late-starting input), the frames in between were
   never delivered. Short skips (<= 0.5 s) are filled with silence so the slice
   stays aligned with the meeting clock and consecutive slices stay contiguous;
   longer skips (suspension) end the slice and the next one starts at the true time. */
class MeetingPCM extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.epoch = options.processorOptions.epoch;
    this.buffer = new Int16Array(Math.round(sampleRate * 60));
    this.maxPad = Math.round(sampleRate / 2);
    this.used = 0;
    this.start = null;
    this.next = null;      // frame expected at the start of the next render quantum
    this.padded = 0;       // silent frames inserted into the current slice
    this.stopped = false;
    this.port.onmessage = ({ data }) => {
      if (data === 'stop') {
        this.flush();
        this.stopped = true;
        this.port.postMessage({ done: true });
      }
    };
  }
  flush() {
    if (!this.used) return;
    const pcm = this.buffer.slice(0, this.used);
    this.port.postMessage({ pcm, offset: this.start - this.epoch, rate: sampleRate, padded: this.padded }, [pcm.buffer]);
    this.used = 0;
    this.start = null;
    this.padded = 0;
  }
  push(value, frame) {
    if (this.start === null) this.start = frame;
    this.buffer[this.used++] = value;
    if (this.used === this.buffer.length) this.flush();
  }
  process(inputs, outputs) {
    if (this.stopped) return false;
    const input = inputs[0]?.[0];
    const frames = outputs[0]?.[0]?.length || 128;
    if (this.next !== null && currentFrame > this.next) {
      const missing = currentFrame - this.next;
      if (missing <= this.maxPad) {
        for (let k = 0; k < missing; k++) { this.push(0, this.next + k); this.padded++; }
      } else {
        this.flush();
      }
    }
    for (let i = 0; i < frames; i++) {
      const value = Math.max(-1, Math.min(1, input?.[i] || 0));
      this.push(Math.round(value * (value < 0 ? 32768 : 32767)), currentFrame + i);
    }
    this.next = currentFrame + frames;
    return true;
  }
}
registerProcessor('meeting-pcm', MeetingPCM);
