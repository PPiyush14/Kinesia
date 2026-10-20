import { useEffect, useState, useCallback } from 'react';

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
      if (inDevices.some((d) => d.label)) {
        setPermissionGranted(true);
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
      await refreshDevices();
    } catch (err) {
      console.warn('Microphone permission request failed or dismissed', err);
    }
  }, [refreshDevices]);

  useEffect(() => {
    void refreshDevices();
    navigator.mediaDevices?.addEventListener?.('devicechange', refreshDevices);
    return () => {
      navigator.mediaDevices?.removeEventListener?.('devicechange', refreshDevices);
    };
  }, [refreshDevices]);

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

  const testSpeaker = (deviceId?: string) => {
    try {
      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      if (!AudioCtx) return;
      const ctx = new AudioCtx();
      const now = ctx.currentTime;
      [523.25, 659.25, 783.99].forEach((freq, idx) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, now + idx * 0.12);
        gain.gain.setValueAtTime(0.12, now + idx * 0.12);
        gain.gain.exponentialRampToValueAtTime(0.001, now + idx * 0.12 + 0.4);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(now + idx * 0.12);
        osc.stop(now + idx * 0.12 + 0.45);
      });
    } catch (err) {
      console.warn('Speaker test error:', err);
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
    testSpeaker,
  };
}
