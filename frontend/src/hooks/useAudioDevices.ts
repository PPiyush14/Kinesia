import { useEffect, useState, useCallback, useRef } from 'react';

export interface AudioDeviceInfo {
  deviceId: string;
  label: string;
  kind: 'audioinput' | 'audiooutput';
}

export function useAudioDevices() {
  const [inputs, setInputs] = useState<AudioDeviceInfo[]>([]);
  const [outputs, setOutputs] = useState<AudioDeviceInfo[]>([]);
  const [selectedInput, setSelectedInput] = useState<string>(
    () => (typeof localStorage !== 'undefined' && localStorage.getItem('kinesia_audio_input')) || 'default'
  );
  const [selectedOutput, setSelectedOutput] = useState<string>(
    () => (typeof localStorage !== 'undefined' && localStorage.getItem('kinesia_audio_output')) || 'default'
  );
  const [permissionGranted, setPermissionGranted] = useState(false);
  const [permissionDenied, setPermissionDenied] = useState(false);
  const [permissionError, setPermissionError] = useState<string | null>(null);
  const [speakerError, setSpeakerError] = useState<string | null>(null);
  const chime = useRef<AudioContext | null>(null);
  const chimeGeneration = useRef(0);

  const refreshDevices = useCallback(async () => {
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.enumerateDevices) return;
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const inDevices = devices
        .filter((d) => d.kind === 'audioinput')
        .map((d, i) => ({
          deviceId: d.deviceId,
          label: d.label || `Microphone ${i + 1}`,
          kind: 'audioinput' as const,
        }));
      const outDevices = devices
        .filter((d) => d.kind === 'audiooutput')
        .map((d, i) => ({
          deviceId: d.deviceId,
          label: d.label || `Speaker / Headphone ${i + 1}`,
          kind: 'audiooutput' as const,
        }));
      setInputs(inDevices);
      setOutputs(outDevices);
      if (devices.some((d) => d.kind === 'audioinput' && Boolean(d.label))) {
        setPermissionGranted(true);
        setPermissionDenied(false);
        setPermissionError(null);
      }
    } catch (err) {
      console.warn('Failed to enumerate audio devices', err);
    }
  }, []);

  const requestPermission = useCallback(async () => {
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((t) => t.stop());
      setPermissionGranted(true);
      setPermissionDenied(false);
      setPermissionError(null);
      await refreshDevices();
    } catch (err) {
      if (err instanceof DOMException && err.name === 'NotAllowedError') {
        setPermissionDenied(true);
        setPermissionError('Microphone permission is blocked in browser settings.');
      } else {
        setPermissionError(err instanceof Error ? err.message : 'Microphone unavailable');
      }
      setPermissionGranted(false);
    }
  }, [refreshDevices]);

  useEffect(() => {
    void requestPermission();
    navigator.mediaDevices?.addEventListener?.('devicechange', refreshDevices);
    return () => {
      navigator.mediaDevices?.removeEventListener?.('devicechange', refreshDevices);
      chimeGeneration.current++;
      void chime.current?.close().catch(() => {});
      chime.current = null;
    };
  }, [requestPermission, refreshDevices]);

  const changeInput = (deviceId: string) => {
    setSelectedInput(deviceId);
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem('kinesia_audio_input', deviceId);
    }
  };

  const changeOutput = (deviceId: string) => {
    setSelectedOutput(deviceId);
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem('kinesia_audio_output', deviceId);
    }
  };

  const testSpeaker = async (deviceId = selectedOutput) => {
    const generation = ++chimeGeneration.current;
    let ctx: AudioContext | null = null;
    setSpeakerError(null);
    try {
      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      if (!AudioCtx) throw new Error('Audio playback is unavailable in this browser.');
      await chime.current?.close().catch(() => {});
      if (generation !== chimeGeneration.current) return false;
      ctx = new AudioCtx();
      chime.current = ctx;
      const context = ctx!;
      if (deviceId && deviceId !== 'default') {
        const routed = context as AudioContext & { setSinkId?: (id: string) => Promise<void> };
        if (!routed.setSinkId) throw new Error('This browser cannot select an output device. Choose the system default instead.');
        await routed.setSinkId(deviceId);
      }
      if (generation !== chimeGeneration.current) return false;
      await context.resume();
      if (chime.current !== context) return false;
      const now = ctx.currentTime;
      [523.25, 659.25, 783.99].forEach((freq, idx) => {
        const osc = context.createOscillator();
        const gain = context.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, now + idx * 0.12);
        gain.gain.setValueAtTime(0.12, now + idx * 0.12);
        gain.gain.exponentialRampToValueAtTime(0.001, now + idx * 0.12 + 0.4);
        osc.connect(gain);
        gain.connect(context.destination);
        osc.start(now + idx * 0.12);
        osc.stop(now + idx * 0.12 + 0.45);
      });
      await new Promise<void>((resolve) => setTimeout(resolve, 800));
      return true;
    } catch (err) {
      if (generation === chimeGeneration.current)
        setSpeakerError(err instanceof Error ? err.message : 'Speaker test failed. Check the selected output device.');
      return false;
    } finally {
      if (ctx) await ctx.close().catch(() => {});
      if (chime.current === ctx) chime.current = null;
    }
  };

  return {
    inputs,
    outputs,
    selectedInput,
    selectedOutput,
    changeInput,
    changeOutput,
    refreshDevices,
    requestPermission,
    permissionGranted,
    permissionDenied,
    permissionError,
    speakerError,
    testSpeaker,
  };
}
