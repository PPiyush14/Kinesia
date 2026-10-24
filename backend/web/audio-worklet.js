/* Audio capture stays off the UI thread. One 100ms PCM16 frame per message. */
class KinesiaCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.phase = 0;
    this.sum = 0;
    this.count = 0;
    this.samples = new Int16Array(1600);
    this.index = 0;
    this.voiced = 0;
    this.quiet = 0;
    this.speaking = false;
    this.noise = 0.002;
  }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    let energy = 0;
    for (let i = 0; i < input.length; i++) {
      const sample = input[i];
      energy += sample * sample;
      this.sum += sample;
      this.count++;
      this.phase += 16000;
      if (this.phase >= sampleRate) {
        this.phase -= sampleRate;
        const value = Math.max(-1, Math.min(1, this.sum / this.count));
        this.samples[this.index++] = value < 0 ? value * 32768 : value * 32767;
        this.sum = 0;
        this.count = 0;
        if (this.index === this.samples.length) {
          const pcm = this.samples.buffer;
          this.port.postMessage({ type: 'pcm', data: pcm }, [pcm]);
          this.samples = new Int16Array(1600);
          this.index = 0;
        }
      }
    }
    const rms = Math.sqrt(energy / input.length);
    const threshold = Math.max(0.012, this.noise * 3.5);
    if (rms < threshold && !this.speaking) this.noise = this.noise * 0.995 + rms * 0.005;
    if (rms >= threshold) {
      this.voiced += input.length;
      this.quiet = 0;
    } else {
      this.quiet += input.length;
      this.voiced = 0;
    }
    const next = this.speaking ? this.quiet < sampleRate * 0.45 : this.voiced >= sampleRate * 0.06;
    if (next !== this.speaking) {
      this.speaking = next;
      this.port.postMessage({ type: 'speaking', active: next });
    }
    if (this.index < Math.ceil((input.length * 16000) / sampleRate)) {
      this.port.postMessage({ type: 'level', value: Math.min(1, rms * 8) });
    }
    return true;
  }
}
registerProcessor('kinesia-capture', KinesiaCapture);
