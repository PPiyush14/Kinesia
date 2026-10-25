// Exercise the actual TypeScript hooks with browser/React lifecycle fakes.
// Uses Node's runner and the project's existing TypeScript compiler; no new packages.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

function hooks(name, globals = {}, modulePath = name, modules = {}) {
  const slots = [];
  let cursor = 0;
  let effects = [];
  const timers = new Map();
  let timerId = 0;
  const equal = (a, b) => a && b && a.length === b.length && a.every((x, i) => Object.is(x, b[i]));
  const react = {
    useRef(value) { const i = cursor++; return slots[i] ??= { current: value }; },
    useState(value) {
      const i = cursor++;
      slots[i] ??= { value };
      return [slots[i].value, (next) => { slots[i].value = typeof next === 'function' ? next(slots[i].value) : next; }];
    },
    useCallback(fn, deps) {
      const i = cursor++;
      if (!slots[i] || !equal(slots[i].deps, deps)) slots[i] = { fn, deps };
      return slots[i].fn;
    },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!slots[i] || !equal(slots[i].deps, deps)) {
        effects.push(() => {
          slots[i]?.cleanup?.();
          slots[i] = { deps, cleanup: fn() };
        });
      }
    },
  };
  const module = { exports: {} };
  const filename = path.resolve(__dirname, `../src/hooks/${modulePath}.ts`);
  const source = fs.readFileSync(filename, 'utf8').replace('import.meta.env.VITE_API_BASE_URL', "''");
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const context = vm.createContext({
    module, exports: module.exports, require: (id) => { if (id in modules) return modules[id]; assert.equal(id, 'react'); return react; },
    console, URL, URLSearchParams, Float32Array, ArrayBuffer, DataView, Uint8Array,
    btoa: (binary) => Buffer.from(binary, 'binary').toString('base64'),
    setTimeout: (fn) => { timers.set(++timerId, fn); return timerId; },
    clearTimeout: (id) => timers.delete(id), ...globals,
  });
  vm.runInContext(compiled, context, { filename });
  return {
    exports: module.exports,
    timers,
    render(...args) {
      cursor = 0;
      const value = module.exports[name](...args);
      const pending = effects; effects = []; pending.forEach((fn) => fn());
      return value;
    },
    unmount() { slots.forEach((slot) => slot?.cleanup?.()); },
    fireTimers() { const pending = [...timers.values()]; timers.clear(); pending.forEach((fn) => fn()); },
  };
}

function captureSetup(getUserMedia) {
  const contexts = [];
  class AudioContext {
    sampleRate = 16000;
    state = 'running';
    destination = {};
    constructor() { contexts.push(this); }
    createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
    createScriptProcessor() { return this.processor = { connect() {}, disconnect() {} }; }
    close() { this.state = 'closed'; return Promise.resolve(); }
  }
  return { contexts, hook: hooks('useSonioxAudioCapture', { window: { AudioContext }, navigator: { mediaDevices: { getUserMedia } } }) };
}
const stream = () => {
  const track = { stopped: false, stop() { this.stopped = true; } };
  return { track, getTracks: () => [track] };
};

test('saved moments ignore an older list response after a newer bookmark change', async () => {
  const pending = [];
  const service = {
    getMoments() { const item = deferred(); pending.push(item); return item.promise; },
    async saveMoment() {}, async removeMoment() {},
  };
  const hook = hooks('useSavedMoments', {}, 'useSavedMoments', { '@/services/api': { roomService: service } });
  let state = hook.render('room', () => {});
  pending.shift().resolve([]);
  await settle();
  state = hook.render('room', () => {});
  const saveA = state.toggle('room', 'a', false);
  await settle();
  const oldList = pending.shift();
  const saveB = state.toggle('room', 'b', false);
  await settle();
  pending.shift().resolve([{ turn_id: 'a' }, { turn_id: 'b' }]);
  await saveB;
  oldList.resolve([{ turn_id: 'a' }]);
  await saveA;
  state = hook.render('room', () => {});
  assert.deepEqual(Array.from(state.saved).sort(), ['a', 'b']);
  hook.unmount();
});

test('local microphone speech preserves incoming translated audio during full-duplex conversation', async () => {
  const sockets = [], engines = [];
  class Socket {
    static OPEN = 1;
    readyState = 1;
    sent = [];
    constructor() { sockets.push(this); }
    send(data) { this.sent.push(data); }
    close() { this.readyState = 3; }
  }
  class Engine {
    resets = 0;
    player = { reset: () => this.resets++ };
    constructor() { engines.push(this); }
    startMicrophone(audio, speaking) { this.audio = audio; this.speaking = speaking; return Promise.resolve(); }
    stopMicrophone() {} dispose() {}
  }
  const hook = hooks('useRealtimeRoom', { WebSocket: Socket, window: { location: { origin: 'https://test.invalid' } } }, 'useRealtimeRoom', {
    '@/services/api': { API_BASE: '' }, '@/lib/realtimeAudio': { RealtimeAudioEngine: Engine },
  });
  const options = { speakingLang: 'en', listeningLang: 'hi', micMuted: false, audioMuted: false };
  hook.render('room', 'token', {}, options);
  sockets[0].onopen();
  sockets[0].onmessage({ data: JSON.stringify({ type: 'connected', data: {} }) });
  hook.render('room', 'token', {}, options);
  await settle();
  engines[0].speaking(true);
  assert.equal(engines[0].resets, 0, 'starting to speak discarded the incoming sentence');
  const pcm = new ArrayBuffer(3200);
  engines[0].audio(pcm);
  assert.equal(sockets[0].sent.at(-1), pcm);
  hook.render('room', 'token', {}, { ...options, audioMuted: true });
  assert.equal(engines[0].resets, 1, 'explicit output mute must still stop playback');
  hook.unmount();
});

test('speaker test plays one chime on the selected output and closes its context', async () => {
  const contexts = [];
  class AudioContext {
    currentTime = 0; destination = {}; oscillators = 0; closed = false;
    constructor() { contexts.push(this); }
    async setSinkId(id) { this.sink = id; }
    async resume() {}
    async close() { this.closed = true; }
    createOscillator() { this.oscillators++; return { frequency: { setValueAtTime() {} }, connect() {}, start() {}, stop() {} }; }
    createGain() { return { gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} }; }
  }
  const hook = hooks('useAudioDevices', { window: { AudioContext }, navigator: { mediaDevices: {} }, localStorage: { getItem: () => null } });
  const devices = hook.render();
  const result = devices.testSpeaker('headphones');
  await settle();
  assert.equal(contexts.length, 1);
  assert.equal(contexts[0].sink, 'headphones');
  assert.equal(contexts[0].oscillators, 3, 'one C-E-G chime must have three oscillators');
  hook.fireTimers();
  assert.equal(await result, true);
  assert.equal(contexts[0].closed, true);
  hook.unmount();
});

test('leaving during speaker-test setup cannot start audio after unmount', async () => {
  let contexts = 0;
  class AudioContext { constructor() { contexts++; } }
  const hook = hooks('useAudioDevices', { window: { AudioContext }, navigator: { mediaDevices: {} }, localStorage: { getItem: () => null } });
  const devices = hook.render();
  const pending = devices.testSpeaker();
  hook.unmount();
  assert.equal(await pending, false);
  assert.equal(contexts, 0);
});

test('capture includes the onset frame once and emits one WAV per utterance', async () => {
  const output = [];
  const { hook, contexts } = captureSetup(async () => stream());
  hook.render({ enabled: true, onAudioUtterance: (wav) => output.push(wav) });
  await settle();
  const frame = (energy) => contexts[0].processor.onaudioprocess({ inputBuffer: { getChannelData: () => new Float32Array(2048).fill(energy) } });
  frame(0); frame(0.1); frame(0.2); frame(0.3); frame(0);
  hook.fireTimers();
  hook.fireTimers();
  assert.equal(output.length, 1);
  const wav = Buffer.from(output[0], 'base64');
  assert.equal(wav.readUInt32LE(40), 5 * 2048 * 2);
  assert.equal(wav.readUInt32LE(24), 16000);
  assert.equal(wav.readInt16LE(44 + 2 * 2048 * 2), Math.floor(0.2 * 0x7fff));
  hook.unmount();
});

test('stale microphone permission cannot create a second processor or stop newer capture', async () => {
  const first = deferred(), second = deferred();
  const pending = [first, second];
  const { hook, contexts } = captureSetup(() => pending.shift().promise);
  const options = { enabled: true, onAudioUtterance() {} };
  const capture = hook.render(options);
  void capture.startCapture();
  const newer = stream(); second.resolve(newer); await settle();
  const stale = stream(); first.resolve(stale); await settle();
  assert.equal(stale.track.stopped, true);
  assert.equal(newer.track.stopped, false);
  assert.equal(contexts.length, 1);
  hook.unmount();
  assert.equal(newer.track.stopped, true);
});

test('unmount during microphone permission closes the late stream', async () => {
  const pending = deferred();
  const { hook, contexts } = captureSetup(() => pending.promise);
  hook.render({ enabled: true, onAudioUtterance() { assert.fail('stale audio'); } });
  hook.unmount();
  const late = stream(); pending.resolve(late); await settle();
  assert.equal(late.track.stopped, true);
  assert.equal(contexts.length, 0);
});

function playbackSetup(outcomes = []) {
  const media = [], spoken = [], playing = [];
  let cancelled = 0;
  class Audio {
    constructor() { media.push(this); }
    play() { return outcomes.length ? outcomes.shift()() : Promise.resolve(); }
    pause() { this.paused = true; }
    removeAttribute() {} load() {}
  }
  class SpeechSynthesisUtterance { constructor(text) { this.text = text; } }
  const hook = hooks('useAudioPlaybackQueue', {
    Audio, SpeechSynthesisUtterance, localStorage: { getItem: () => null },
    window: { speechSynthesis: { speak: (utterance) => spoken.push(utterance), cancel: () => cancelled++ } },
  });
  const callback = (value) => playing.push(value);
  return { hook, media, spoken, playing, callback, cancelled: () => cancelled };
}

test('autoplay unlock starts the blocked head once and preserves FIFO', async () => {
  const policyError = Object.assign(new Error('gesture required'), { name: 'NotAllowedError' });
  const setup = playbackSetup([() => Promise.reject(policyError)]);
  const queue = setup.hook.render(false, setup.callback);
  queue.queueAudio({ url: 'first' });
  queue.queueAudio({ url: 'second' });
  await settle();
  assert.equal(setup.media.length, 1);
  queue.unlockAndResumeAudio(); await settle();
  assert.equal(setup.media.length, 2);
  assert.equal(setup.media[1].src, 'first');
  setup.media[1].onended(); await settle();
  assert.equal(setup.media.length, 3);
  assert.equal(setup.media[2].src, 'second');
  setup.hook.unmount();
});

test('provider audio and browser fallback speech share one FIFO', async () => {
  const setup = playbackSetup();
  const queue = setup.hook.render(false, setup.callback);
  queue.queueAudio({ url: 'first' });
  queue.queueAudio({ text: 'नमस्ते', lang: 'hi-IN' });
  queue.queueAudio({ url: 'third' });
  assert.equal(setup.spoken.length, 0);
  setup.media[0].onended();
  assert.equal(setup.spoken.length, 1);
  assert.equal(setup.spoken[0].text, 'नमस्ते');
  assert.equal(setup.media.length, 1);
  setup.spoken[0].onend();
  assert.equal(setup.media[1].src, 'third');
  setup.hook.unmount();
});

test('mute cancels queued audio; stale callbacks cannot consume a new turn', async () => {
  const late = deferred();
  const setup = playbackSetup([() => late.promise]);
  let queue = setup.hook.render(false, setup.callback);
  queue.queueAudio({ url: 'old' });
  const ended = setup.media[0].onended;
  setup.hook.render(true, setup.callback);
  queue = setup.hook.render(false, setup.callback);
  queue.queueAudio({ url: 'new' });
  late.reject(Object.assign(new Error(), { name: 'NotAllowedError' })); ended(); await settle();
  assert.equal(setup.media.length, 2);
  assert.equal(setup.media[0].paused, true);
  assert.equal(setup.hook.render(false, setup.callback).autoplayBlocked, false);
  setup.hook.unmount();
  assert.equal(setup.media[1].paused, true);
});

test('SSE retries once, uses current languages, ignores old streams and deduplicates IDs', () => {
  const sources = [], turns = [], speaking = [], concluded = [];
  class EventSource {
    listeners = {};
    constructor(url) { this.url = url; sources.push(this); }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    close() { this.closed = true; }
    emit(name, data) { this.listeners[name]?.({ data: JSON.stringify(data) }); }
  }
  const hook = hooks('useRoomSSE', { EventSource });
  const handlers = { onTurn: (turn) => turns.push(turn), onSpeaking: (id) => speaking.push(id), onSessionConcluded: (data) => concluded.push(data) };
  hook.render('room-a', 'token', handlers, { speakingLang: 'en', listeningLang: 'hi' });
  const turn = { turn_id: 'one', localized_text: 'नमस्ते' };
  sources[0].emit('turn', turn);
  sources[0].onmessage({ data: JSON.stringify(turn) });
  sources[0].emit('speaking', { speaker_id: 'speaker', speaker_name: 'Speaker' });
  assert.deepEqual(speaking, ['speaker']);
  sources[0].onerror({}); sources[0].onerror({});
  assert.equal(hook.timers.size, 1);
  hook.render('room-a', 'token', handlers, { speakingLang: 'mr', listeningLang: 'te' });
  hook.fireTimers();
  assert.match(sources[1].url, /speaking_lang=mr&listening_lang=te/);
  sources[0].emit('turn', { turn_id: 'stale' }); sources[0].onerror({});
  sources[1].emit('turn', turn);
  sources[1].emit('turn', { ...turn, turn_id: 'two' });
  assert.equal(turns.length, 2); // Identical words with distinct IDs are legitimate.
  assert.equal(hook.timers.size, 0);
  sources[1].emit('session_concluded', { summary: 'done' });
  sources[1].emit('session_ended', { summary: 'done' });
  assert.equal(concluded.length, 1);
  hook.unmount();
});

test('SSE unmount cancels pending reconnect and stale events', () => {
  const sources = [];
  class EventSource {
    constructor() { sources.push(this); }
    addEventListener() {} close() {}
  }
  const hook = hooks('useRoomSSE', { EventSource });
  hook.render('room', 'token', {});
  sources[0].onerror({});
  const lateRetry = [...hook.timers.values()][0];
  hook.unmount(); hook.fireTimers(); sources[0].onerror({});
  lateRetry(); // A retry already queued by the browser must also be harmless.
  assert.equal(sources.length, 1);
  assert.equal(hook.timers.size, 0);
});

test('late history merges live turns by stable ID and restores localized history', () => {
  const module = hooks('mergeTranscriptHistory', {}, '../lib/transcripts');
  const history = [
    { id: 'older', original_text: 'Hello', translations: { hi: 'नमस्ते' } },
    { id: 'overlap', original_text: 'Yes', translations: { hi: 'हाँ' } },
  ];
  const live = [
    { turn_id: 'overlap', original_text: 'Yes', localized_text: 'हाँ', audio_data_url: 'live-audio' },
    { turn_id: 'newer', original_text: 'Yes', localized_text: 'हाँ' },
  ];
  const result = module.render(history, live, 'hi');
  assert.equal(result.length, 3);
  assert.equal(result[0].localized_text, 'नमस्ते');
  assert.equal(result[1], live[0]);
  assert.equal(result[2], live[1]);
});

function pcmPlayer() {
  const nodes = [], playing = [], blocked = [], errors = [];
  const context = {
    state: 'running', currentTime: 0, destination: {},
    createBuffer(channels, length, rate) { const samples = new Float32Array(length); return { duration: length / rate, getChannelData: () => samples }; },
    createBufferSource() {
      const node = { connect() {}, disconnect() {}, start(time) { this.startTime = time; }, stop() { this.stopped = true; } };
      nodes.push(node); return node;
    },
    resume() { this.state = 'running'; return Promise.resolve(); },
  };
  const module = hooks('PCMPlaybackQueue', {}, '../lib/realtimeAudio');
  const player = new module.exports.PCMPlaybackQueue(context, (value) => playing.push(value), (value) => blocked.push(value), (value) => errors.push(value));
  return { player, context, nodes, playing, blocked, errors };
}

test('PCM playback schedules each context/sequence once and preserves speaker order', () => {
  const { player, nodes } = pcmPlayer();
  player.start({ stream_id: 'first', sample_rate: 24000 });
  player.chunk('first', 0, new ArrayBuffer(480));
  player.chunk('first', 0, new ArrayBuffer(480));
  player.start({ stream_id: 'second', sample_rate: 24000 });
  player.chunk('second', 0, new ArrayBuffer(480));
  assert.equal(nodes.length, 1);
  player.end('first');
  assert.equal(nodes.length, 2);
  assert.ok(nodes[1].startTime >= nodes[0].startTime + nodes[0].buffer.duration);
  player.reset();
});

test('interruption cancels obsolete speech while preserving the current speaker and blocking late chunks', () => {
  const { player, nodes } = pcmPlayer();
  player.start({ stream_id: 'old', sample_rate: 24000, speaker_id: 'A' });
  player.chunk('old', 0, new ArrayBuffer(480));
  player.start({ stream_id: 'current', sample_rate: 24000, speaker_id: 'B' });
  player.chunk('current', 0, new ArrayBuffer(480));
  player.interruptExcept('B');
  assert.equal(nodes[0].stopped, true);
  assert.equal(nodes.length, 2);
  assert.notEqual(nodes[1].stopped, true);
  player.chunk('old', 1, new ArrayBuffer(480));
  player.start({ stream_id: 'old', sample_rate: 24000, speaker_id: 'A' });
  assert.equal(nodes.length, 2);
  player.end('current');
  player.start({ stream_id: 'new-acknowledgement', sample_rate: 24000, speaker_id: 'A' });
  player.chunk('new-acknowledgement', 0, new ArrayBuffer(480));
  assert.equal(nodes.length, 3, 'later acknowledged words must not be blacklisted with obsolete audio');
  player.reset();
});

test('a listener joining an existing PCM clause can start at its actual sequence number', () => {
  const { player, nodes, errors } = pcmPlayer();
  player.start({ stream_id: 'mid-stream', sample_rate: 24000, sequence_start: 7 });
  player.chunk('mid-stream', 7, new ArrayBuffer(480));
  player.chunk('mid-stream', 8, new ArrayBuffer(480));
  assert.equal(nodes.length, 2);
  assert.deepEqual(errors, []);
  player.reset();
});

test('PCM cancellation ignores late chunks and mute cannot build a stale queue', () => {
  const { player, nodes } = pcmPlayer();
  player.start({ stream_id: 'old', sample_rate: 24000 });
  player.chunk('old', 0, new ArrayBuffer(480));
  player.reset();
  assert.equal(nodes[0].stopped, true);
  player.chunk('old', 1, new ArrayBuffer(480));
  player.muted = true;
  player.start({ stream_id: 'muted', sample_rate: 24000 });
  player.chunk('muted', 0, new ArrayBuffer(480));
  assert.equal(nodes.length, 1);
  player.muted = false;
  player.start({ stream_id: 'new', sample_rate: 24000 });
  player.chunk('new', 0, new ArrayBuffer(480));
  assert.equal(nodes.length, 2);
  player.reset();
});

test('PCM autoplay recovery resumes buffered audio without double scheduling', async () => {
  const { player, context, nodes, blocked } = pcmPlayer();
  context.state = 'suspended';
  player.start({ stream_id: 'blocked', sample_rate: 24000 });
  player.chunk('blocked', 0, new ArrayBuffer(480));
  assert.equal(nodes.length, 0);
  assert.equal(blocked.at(-1), true);
  await player.unlock();
  await player.unlock();
  assert.equal(nodes.length, 1);
  player.reset();
});

test('PCM decoding uses the shared binary header and preserves little-endian audio', () => {
  const module = hooks('decodeAudioPacket', {}, '../lib/realtimeAudio');
  const packet = new ArrayBuffer(28), bytes = new Uint8Array(packet), view = new DataView(packet);
  bytes.set(Buffer.from('KNS1')); bytes.set(Buffer.from('00112233445566778899aabbccddeeff', 'hex'), 4);
  view.setUint32(20, 7, false); view.setInt16(24, -1234, true); view.setInt16(26, 2345, true);
  const decoded = module.exports.decodeAudioPacket(packet);
  assert.equal(decoded.id, '00112233-4455-6677-8899-aabbccddeeff');
  assert.equal(decoded.sequence, 7);
  assert.equal(new DataView(decoded.pcm).getInt16(0, true), -1234);
  assert.equal(module.exports.decodeAudioPacket(new ArrayBuffer(10)), null);
});

test('AudioWorklet delivers 40ms frames without losing or duplicating 44.1k and 48k input samples', () => {
  for (const rate of [44100, 48000]) {
    const messages = [];
    let Processor;
    class Base { constructor() { this.port = { postMessage: (message) => messages.push(message) }; } }
    const context = vm.createContext({ AudioWorkletProcessor: Base, sampleRate: rate, registerProcessor: (name, type) => { Processor = type; } });
    vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../public/audio-worklet.js'), 'utf8'), context);
    const processor = new Processor();
    for (let offset = 0; offset < rate; offset += 128) processor.process([[new Float32Array(Math.min(128, rate - offset)).fill(0.1)]]);
    const frames = messages.filter((message) => message.type === 'pcm');
    assert.equal(frames.length, 25);
    assert.ok(frames.every((frame) => frame.data.byteLength === 1280));
    assert.equal(frames.reduce((sum, message) => sum + message.data.byteLength / 2, 0), 16000);
    assert.equal(new DataView(frames[0].data).getInt16(0, true), Math.floor(0.1 * 32767));
  }
});
