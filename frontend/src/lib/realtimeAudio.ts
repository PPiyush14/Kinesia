export interface AudioStart {
  stream_id: string;
  sample_rate: number;
  speaker_id?: string | number;
  voice_start_ms?: number;
  sequence_start?: number;
}

interface AudioStream extends AudioStart {
  chunks: AudioBuffer[];
  lastSequence: number;
  ended: boolean;
}

/** Bounded PCM playback; context IDs own callbacks and cannot be replayed. */
export class PCMPlaybackQueue {
  private streams: AudioStream[] = [];
  private byId = new Map<string, AudioStream>();
  private seen = new Set<string>();
  private sources = new Map<AudioBufferSourceNode, { speaker?: string | number; end: number }>();
  private output: GainNode | null = null;
  private nextTime = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  muted = false;

  constructor(
    private context: AudioContext,
    private onPlaying: (playing: boolean) => void,
    private onBlocked: (blocked: boolean) => void,
    private onError: (message: string) => void
  ) {
    if (typeof context.createGain === 'function') {
      this.output = context.createGain();
      this.output.connect(context.destination);
    }
  }

  duck(speaking: boolean) {
    this.output?.gain.setTargetAtTime(speaking ? 0.45 : 1, this.context.currentTime, 0.025);
  }

  interruptExcept(speaker: string | number) {
    const keep = (value: string | number | undefined) => String(value) === String(speaker);
    this.streams = this.streams.filter((stream) => {
      if (keep(stream.speaker_id)) return true;
      this.byId.delete(stream.stream_id);
      return false;
    });
    for (const [node, metadata] of this.sources) {
      if (keep(metadata.speaker)) continue;
      node.onended = null;
      try { node.stop(); node.disconnect(); } catch { /* already finished */ }
      this.sources.delete(node);
    }
    this.nextTime = Math.max(this.context.currentTime, ...Array.from(this.sources.values(), (source) => source.end));
    this.onPlaying(this.sources.size > 0);
    this.pump();
  }

  start(info: AudioStart) {
    if (this.muted || this.seen.has(info.stream_id)) return;
    if (info.sample_rate < 8000 || info.sample_rate > 48000) return;
    this.seen.add(info.stream_id);
    if (this.seen.size > 1000) this.seen.delete(this.seen.values().next().value!);
    const initial = Number.isInteger(info.sequence_start) && (info.sequence_start ?? 0) >= 0 ? info.sequence_start! : 0;
    const stream = { ...info, chunks: [], lastSequence: initial - 1, ended: false };
    this.streams.push(stream);
    this.byId.set(info.stream_id, stream);
  }

  chunk(id: string, sequence: number, pcm: ArrayBuffer) {
    const stream = this.byId.get(id);
    if (!stream || this.muted || sequence <= stream.lastSequence || pcm.byteLength % 2) return;
    if (sequence !== stream.lastSequence + 1) {
      this.reset();
      this.onError('Audio was interrupted. Waiting for the next translated phrase.');
      return;
    }
    stream.lastSequence = sequence;
    const view = new DataView(pcm);
    const buffer = this.context.createBuffer(1, pcm.byteLength / 2, stream.sample_rate);
    const output = buffer.getChannelData(0);
    for (let i = 0; i < output.length; i++) output[i] = view.getInt16(i * 2, true) / 32768;
    stream.chunks.push(buffer);
    const buffered = this.streams.reduce(
      (sum, item) => sum + item.chunks.reduce((n, chunk) => n + chunk.duration, 0),
      0
    );
    if (buffered + Math.max(0, this.nextTime - this.context.currentTime) > 8) {
      this.reset();
      this.onError('Queued speech was skipped to stay live. The full text is in the transcript.');
      return;
    }
    this.pump();
  }

  end(id: string) {
    const stream = this.byId.get(id);
    if (stream) stream.ended = true;
    this.pump();
  }

  async unlock() {
    await this.context.resume();
    this.onBlocked(false);
    this.pump();
  }

  private pump() {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.muted) return;
    if (this.context.state !== 'running') {
      if (this.streams.length) this.onBlocked(true);
      return;
    }
    while (this.streams.length && this.nextTime < this.context.currentTime + 0.25) {
      const stream = this.streams[0];
      const chunk = stream.chunks.shift();
      if (!chunk) {
        if (stream.ended) {
          this.streams.shift();
          this.byId.delete(stream.stream_id);
          continue;
        }
        break;
      }
      const source = this.context.createBufferSource();
      source.buffer = chunk;
      source.connect(this.output || this.context.destination);
      source.onended = () => {
        this.sources.delete(source);
        source.disconnect();
        this.onPlaying(this.sources.size > 0);
        this.pump();
      };
      this.nextTime = Math.max(this.context.currentTime + 0.035, this.nextTime);
      this.sources.set(source, { speaker: stream.speaker_id, end: this.nextTime + chunk.duration });
      source.start(this.nextTime);
      this.nextTime += chunk.duration;
      this.onPlaying(true);
    }
    if (this.streams.length) this.timer = setTimeout(() => this.pump(), 80);
  }

  reset() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.streams = [];
    this.byId.clear();
    this.nextTime = 0;
    for (const source of this.sources.keys()) {
      source.onended = null;
      try {
        source.stop();
        source.disconnect();
      } catch {
        /* already ended */
      }
    }
    this.sources.clear();
    this.onPlaying(false);
  }
}

export function decodeAudioPacket(packet: ArrayBuffer) {
  if (packet.byteLength < 26) return null;
  const bytes = new Uint8Array(packet);
  if (String.fromCharCode(...bytes.subarray(0, 4)) !== 'KNS1') return null;
  const hex = Array.from(bytes.subarray(4, 20), (value) => value.toString(16).padStart(2, '0')).join('');
  const id = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  return { id, sequence: new DataView(packet).getUint32(20, false), pcm: packet.slice(24) };
}

export class RealtimeAudioEngine {
  context: AudioContext | null = null;
  player: PCMPlaybackQueue | null = null;
  private microphone: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private processor: AudioWorkletNode | null = null;
  private worklet: Promise<void> | null = null;
  private generation = 0;
  private disposed = false;

  constructor(
    private onPlaying: (value: boolean) => void,
    private onBlocked: (value: boolean) => void,
    private onError: (message: string) => void
  ) {}

  ensure() {
    if (this.disposed) throw new Error('Audio session has closed.');
    if (!this.context) {
      this.context = new AudioContext({ latencyHint: 'interactive' });
      this.player = new PCMPlaybackQueue(this.context, this.onPlaying, this.onBlocked, this.onError);
      const output = localStorage.getItem('kinesia_audio_output');
      if (output && output !== 'default' && typeof (this.context as any).setSinkId === 'function') {
        void (this.context as any).setSinkId(output).catch(() => {});
      }
    }
    return this.context;
  }

  async startMicrophone(
    onPCM: (data: ArrayBuffer) => void,
    onSpeaking: (value: boolean) => void,
    onLevel: (value: number) => void
  ) {
    this.stopMicrophone();
    const generation = this.generation;
    const context = this.ensure();
    if (!context.audioWorklet) throw new Error('Use a modern browser over HTTPS to enable streaming audio.');
    const input = localStorage.getItem('kinesia_audio_input');
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        ...(input && input !== 'default' ? { deviceId: { exact: input } } : {}),
      },
    });
    if (generation !== this.generation || this.disposed) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    this.microphone = stream;
    try {
      this.worklet ??= context.audioWorklet.addModule('/audio-worklet.js');
      await this.worklet;
      if (generation !== this.generation || this.disposed) return;
      this.source = context.createMediaStreamSource(stream);
      this.processor = new AudioWorkletNode(context, 'kinesia-capture');
      this.processor.port.onmessage = ({ data }) => {
        if (generation !== this.generation || this.disposed) return;
        if (data.type === 'pcm') onPCM(data.data);
        else if (data.type === 'speaking') onSpeaking(data.active);
        else if (data.type === 'level') onLevel(data.value);
      };
      this.source.connect(this.processor);
      this.processor.connect(context.destination);
      await context.resume();
      this.onBlocked(context.state !== 'running');
    } catch (error) {
      if (generation === this.generation) this.stopMicrophone();
      throw error;
    }
  }

  stopMicrophone() {
    this.generation++;
    if (this.processor) {
      this.processor.port.onmessage = null;
      this.processor.disconnect();
    }
    this.processor = null;
    this.source?.disconnect();
    this.source = null;
    this.microphone?.getTracks().forEach((track) => track.stop());
    this.microphone = null;
  }

  receive(packet: ArrayBuffer) {
    const data = decodeAudioPacket(packet);
    if (data) this.player?.chunk(data.id, data.sequence, data.pcm);
  }

  async encoded(url: string) {
    const generation = this.generation;
    const context = this.ensure();
    const audio = await context.decodeAudioData(await (await fetch(url)).arrayBuffer());
    if (this.disposed || generation !== this.generation) return;
    const id = crypto.randomUUID();
    this.player!.start({ stream_id: id, sample_rate: audio.sampleRate });
    const samples = audio.getChannelData(0);
    const pcm = new ArrayBuffer(samples.length * 2),
      view = new DataView(pcm);
    samples.forEach((sample, i) =>
      view.setInt16(i * 2, Math.max(-32768, Math.min(32767, sample * 32767)), true)
    );
    this.player!.chunk(id, 0, pcm);
    this.player!.end(id);
  }

  dispose() {
    this.disposed = true;
    this.stopMicrophone();
    this.player?.reset();
    void this.context?.close().catch(() => {});
    this.context = null;
  }
}
