import { useEffect, useRef, useState } from 'react';

/**
 * Real microphone level meter via Web Audio API AnalyserNode.
 * Binds strictly to the chosen deviceId and returns normalized 0–1 level.
 */
export function useAudioMeter(active: boolean, deviceId?: string) {
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const streamRef = useRef<MediaStream | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (!active) {
      cleanup();
      setLevel(0);
      setReady(false);
      return;
    }

    let cancelled = false;

    async function start() {
      try {
        const audioConstraints: MediaTrackConstraints = {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        };
        if (deviceId && deviceId !== 'default') {
          audioConstraints.deviceId = { exact: deviceId };
        }

        const stream = await navigator.mediaDevices.getUserMedia({
          audio: audioConstraints,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        const ctx = new AudioContext();
        ctxRef.current = ctx;
        const source = ctx.createMediaStreamSource(stream);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        analyser.smoothingTimeConstant = 0.8;
        source.connect(analyser);

        const data = new Uint8Array(analyser.frequencyBinCount);
        setReady(true);
        setError(null);

        const tick = () => {
          analyser.getByteFrequencyData(data);
          let sum = 0;
          for (let i = 0; i < data.length; i++) sum += data[i];
          const avg = sum / data.length / 255;
          setLevel(Math.min(1, avg * 2.5));
          rafRef.current = requestAnimationFrame(tick);
        };
        tick();
      } catch (err) {
        const msg =
          err instanceof DOMException && err.name === 'NotAllowedError'
            ? 'Microphone permission denied by browser'
            : err instanceof Error
              ? err.message
              : 'Microphone hardware unavailable';
        setError(msg);
        setReady(false);
      }
    }

    start();

    return () => {
      cancelled = true;
      cleanup();
    };
  }, [active, deviceId]);

  function cleanup() {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (ctxRef.current && ctxRef.current.state !== 'closed') {
      void ctxRef.current.close();
    }
    ctxRef.current = null;
  }

  return { level, error, ready };
}
