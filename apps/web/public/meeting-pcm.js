/* Capture continuously on the audio rendering thread. Each message is a complete
   PCM slice; no MediaRecorder container fragments or stop/start gaps. */
class MeetingPCM extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.epoch = options.processorOptions.epoch;
    this.buffer = new Int16Array(Math.round(sampleRate * 60));
    this.used = 0;
    this.start = null;
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
    this.port.postMessage({ pcm, offset: this.start - this.epoch, rate: sampleRate }, [pcm.buffer]);
    this.used = 0;
    this.start = null;
  }
  process(inputs, outputs) {
    if (this.stopped) return false;
    const input = inputs[0]?.[0];
    const frames = outputs[0]?.[0]?.length || 128;
    for (let i = 0; i < frames; i++) {
      if (this.start === null) this.start = currentFrame + i;
      const value = Math.max(-1, Math.min(1, input?.[i] || 0));
      this.buffer[this.used++] = Math.round(value * (value < 0 ? 32768 : 32767));
      if (this.used === this.buffer.length) this.flush();
    }
    return true;
  }
}
registerProcessor('meeting-pcm', MeetingPCM);
