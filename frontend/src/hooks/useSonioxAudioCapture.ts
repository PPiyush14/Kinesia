import { useCallback, useEffect, useRef, useState } from 'react';

interface UseSonioxAudioCaptureOptions {
  enabled: boolean;
  duckDuringPlayback?: boolean;
  onAudioUtterance: (base64Wav: string, mimeType: string) => Promise<void> | void;
  silenceThresholdMs?: number;
  energyThreshold?: number;
}

export function useSonioxAudioCapture({
  enabled,
  duckDuringPlayback = false,
  onAudioUtterance,
  silenceThresholdMs = 1100,
  energyThreshold = 0.015,
}: UseSonioxAudioCaptureOptions) {
  const [isRecording, setIsRecording] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [supported, setSupported] = useState(true);

  const audioContextRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceNodeRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const captureGenerationRef = useRef(0);

  // Speech buffering state
  const isSpeakingRef = useRef(false);
  const audioChunksRef = useRef<Float32Array[]>([]);
  const preRollBufferRef = useRef<Float32Array[]>([]);
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const speechStartTimeRef = useRef<number>(0);
  const isDuckedRef = useRef(duckDuringPlayback);
  const enabledRef = useRef(enabled);
  const onAudioUtteranceRef = useRef(onAudioUtterance);

  isDuckedRef.current = duckDuringPlayback;
  enabledRef.current = enabled;
  onAudioUtteranceRef.current = onAudioUtterance;

  const flushUtterance = useCallback(() => {
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }

    if (!isSpeakingRef.current && audioChunksRef.current.length === 0) {
      return;
    }

    isSpeakingRef.current = false;
    setIsSpeaking(false);

    const chunks = audioChunksRef.current;
    audioChunksRef.current = [];

    if (!chunks.length || !audioContextRef.current) return;

    // Concatenate chunks
    let totalLength = 0;
    for (const c of chunks) {
      totalLength += c.length;
    }
    const sampleRate = audioContextRef.current.sampleRate;
    const durationSec = totalLength / sampleRate;

    // Reject utterances shorter than 0.35s (e.g. mic pops or coughs)
    if (durationSec < 0.35) {
      return;
    }

    const merged = new Float32Array(totalLength);
    let offset = 0;
    for (const c of chunks) {
      merged.set(c, offset);
      offset += c.length;
    }

    // Downsample to 16,000 Hz for Soniox
    const downsampled = downsampleTo16k(merged, sampleRate);
    const wavBase64 = encodeWav(downsampled, 16000);

    if (wavBase64) {
      void onAudioUtteranceRef.current(wavBase64, 'audio/wav');
    }
  }, []);

  const stopCapture = useCallback(() => {
    // Invalidate getUserMedia calls that are still waiting for permission.
    captureGenerationRef.current += 1;
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
    if (processorRef.current) {
      processorRef.current.onaudioprocess = null;
      processorRef.current.disconnect();
      processorRef.current = null;
    }
    if (sourceNodeRef.current) {
      sourceNodeRef.current.disconnect();
      sourceNodeRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
      audioContextRef.current.close().catch(() => {});
      audioContextRef.current = null;
    }
    audioChunksRef.current = [];
    preRollBufferRef.current = [];
    isSpeakingRef.current = false;
    setIsSpeaking(false);
    setIsRecording(false);
  }, []);

  const startCapture = useCallback(async () => {
    if (typeof window === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      setSupported(false);
      return;
    }

    stopCapture();
    const generation = captureGenerationRef.current;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      if (generation !== captureGenerationRef.current || !enabledRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }

      streamRef.current = stream;
      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      const ctx = new AudioCtx();
      audioContextRef.current = ctx;

      const source = ctx.createMediaStreamSource(stream);
      sourceNodeRef.current = source;

      // ScriptProcessor with bufferSize = 2048 (~46ms per frame at 44.1kHz)
      const processor = ctx.createScriptProcessor(2048, 1, 1);
      processorRef.current = processor;

      const preRollMaxFrames = 8; // ~300ms pre-roll to prevent cutting off the first word

      processor.onaudioprocess = (e) => {
        if (!enabledRef.current || generation !== captureGenerationRef.current) return;

        // Acoustic echo ducking: ignore mic while peer speech/TTS is playing
        if (isDuckedRef.current) {
          if (isSpeakingRef.current) {
            flushUtterance();
          }
          preRollBufferRef.current = [];
          return;
        }

        const input = e.inputBuffer.getChannelData(0);
        const frameCopy = new Float32Array(input);

        // Compute RMS energy
        let sum = 0;
        for (let i = 0; i < frameCopy.length; i++) {
          sum += frameCopy[i] * frameCopy[i];
        }
        const rms = Math.sqrt(sum / frameCopy.length);

        if (!isSpeakingRef.current) {
          // Pre-roll storage
          preRollBufferRef.current.push(frameCopy);
          if (preRollBufferRef.current.length > preRollMaxFrames) {
            preRollBufferRef.current.shift();
          }

          if (rms >= energyThreshold) {
            // Speech started!
            isSpeakingRef.current = true;
            setIsSpeaking(true);
            speechStartTimeRef.current = Date.now();

            // Include pre-roll
            // The triggering frame is already the last pre-roll entry.
            audioChunksRef.current = [...preRollBufferRef.current];
            preRollBufferRef.current = [];
          }
        } else {
          // Speech ongoing
          audioChunksRef.current.push(frameCopy);

          if (rms >= energyThreshold) {
            // Reset silence timeout on continued vocal energy
            if (silenceTimerRef.current) {
              clearTimeout(silenceTimerRef.current);
              silenceTimerRef.current = null;
            }
          } else {
            // Vocal energy dropped below threshold; schedule end-of-turn utterance flush
            if (!silenceTimerRef.current) {
              silenceTimerRef.current = setTimeout(() => {
                flushUtterance();
              }, silenceThresholdMs);
            }
          }

          // Safety guard: auto-flush if utterance exceeds 15 seconds
          if (Date.now() - speechStartTimeRef.current > 15000) {
            flushUtterance();
          }
        }
      };

      source.connect(processor);
      // Dummy destination connection required for ScriptProcessor to fire in Chrome/Edge
      processor.connect(ctx.destination);

      setIsRecording(true);
    } catch (err) {
      if (generation !== captureGenerationRef.current) return;
      console.warn('Microphone access denied or error:', err);
      // A failed setup may already own a stream or an AudioContext.
      stopCapture();
    }
  }, [energyThreshold, flushUtterance, silenceThresholdMs, stopCapture]);

  useEffect(() => {
    if (enabled) {
      void startCapture();
    } else {
      stopCapture();
    }
    return () => {
      stopCapture();
    };
  }, [enabled, startCapture, stopCapture]);

  return {
    isRecording,
    isSpeaking,
    supported,
    stopCapture,
    startCapture,
  };
}

/** Downsamples an audio buffer from arbitrary rate to 16,000 Hz */
function downsampleTo16k(inputBuffer: Float32Array, inputSampleRate: number): Float32Array {
  if (inputSampleRate === 16000) return inputBuffer;
  const ratio = inputSampleRate / 16000;
  const newLength = Math.round(inputBuffer.length / ratio);
  const result = new Float32Array(newLength);
  let offsetResult = 0;
  let offsetBuffer = 0;
  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
    let accum = 0;
    let count = 0;
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < inputBuffer.length; i++) {
      accum += inputBuffer[i];
      count++;
    }
    result[offsetResult] = count > 0 ? accum / count : 0;
    offsetResult++;
    offsetBuffer = nextOffsetBuffer;
  }
  return result;
}

/** Encodes float32 PCM samples into a standard 16-bit 16kHz mono WAV file formatted as base64 */
function encodeWav(samples: Float32Array, sampleRate: number): string {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  function writeString(v: DataView, pos: number, str: string) {
    for (let i = 0; i < str.length; i++) {
      v.setUint8(pos + i, str.charCodeAt(i));
    }
  }

  /* RIFF chunk descriptor */
  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(view, 8, 'WAVE');

  /* "fmt " sub-chunk */
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true); // Subchunk1Size (16 for PCM)
  view.setUint16(20, 1, true); // AudioFormat (1 for PCM)
  view.setUint16(22, 1, true); // NumChannels (1 for mono)
  view.setUint32(24, sampleRate, true); // SampleRate (16000)
  view.setUint32(28, sampleRate * 2, true); // ByteRate (sampleRate * 1 * 2)
  view.setUint16(32, 2, true); // BlockAlign (1 * 2)
  view.setUint16(34, 16, true); // BitsPerSample (16 bits)

  /* "data" sub-chunk */
  writeString(view, 36, 'data');
  view.setUint32(40, samples.length * 2, true);

  // Write 16-bit PCM samples
  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }

  // Convert to Base64
  let binary = '';
  const bytes = new Uint8Array(buffer);
  const len = bytes.byteLength;
  const chunkSz = 8192;
  for (let i = 0; i < len; i += chunkSz) {
    const chunk = bytes.subarray(i, Math.min(i + chunkSz, len));
    binary += String.fromCharCode.apply(null, Array.from(chunk));
  }
  return btoa(binary);
}
