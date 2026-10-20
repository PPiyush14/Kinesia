import { useState, useRef } from 'react';
import { motion } from 'framer-motion';
import {
  ArrowLeft,
  Mic,
  MicOff,
  Volume2,
  Radio,
  ArrowRight,
  Copy,
  Check,
  ShieldCheck,
  Headphones,
  Sparkles,
} from 'lucide-react';
import type { Room } from '@/types';
import { SUPPORTED_LANGUAGES, langLabel } from '@/types';
import { useAudioMeter } from '@/hooks/useAudioMeter';
import { useAudioDevices } from '@/hooks/useAudioDevices';
import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Select';
import { Badge } from '@/components/ui/Badge';
import { Card, CardContent } from '@/components/ui/Card';

interface LobbyViewProps {
  room: Room;
  speakingLang: string;
  listeningLang: string;
  onUpdateSpeakingLang: (lang: string) => void;
  onUpdateListeningLang: (lang: string) => void;
  onEnterRoom: () => void;
  onBack: () => void;
}

export function LobbyView({
  room,
  speakingLang,
  listeningLang,
  onUpdateSpeakingLang,
  onUpdateListeningLang,
  onEnterRoom,
  onBack,
}: LobbyViewProps) {
  const [micEnabled, setMicEnabled] = useState(true);
  const [copiedCode, setCopiedCode] = useState(false);
  const [playingTestAudio, setPlayingTestAudio] = useState(false);
  const { level, ready } = useAudioMeter(micEnabled);
  const {
    inputs,
    outputs,
    selectedInput,
    selectedOutput,
    changeInput,
    changeOutput,
    testSpeaker,
  } = useAudioDevices();

  const langOptions = SUPPORTED_LANGUAGES.map((l) => ({ value: l.code, label: `${l.name} (${l.short})` }));

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(room.room_code);
      setCopiedCode(true);
      setTimeout(() => setCopiedCode(false), 2000);
    } catch {
      // fallback
    }
  };

  // Play a pleasant synthesizer test chime to test audio output
  const testAudioOutput = () => {
    try {
      setPlayingTestAudio(true);
      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      const ctx = new AudioCtx();
      const now = ctx.currentTime;

      // Play 3 harmonic chords (C-E-G)
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

      setTimeout(() => setPlayingTestAudio(false), 800);
    } catch {
      setPlayingTestAudio(false);
    }
  };

  return (
    <div className="mx-auto max-w-xl px-4 sm:px-6 py-10 sm:py-14 space-y-6">
      {/* Back button */}
      <button
        type="button"
        onClick={onBack}
        className="inline-flex items-center gap-1.5 text-xs text-ink-muted hover:text-ink-primary transition-colors"
      >
        <ArrowLeft size={16} /> Back to Workspace
      </button>

      {/* Main Card */}
      <Card className="border border-border bg-surface-1/90 backdrop-blur-xl shadow-elevation-2 overflow-hidden">
        {/* Top ambient banner */}
        <div className="h-2 bg-gradient-to-r from-accent via-accent-cyan to-accent" />

        <CardContent className="p-6 sm:p-8 space-y-8">
          {/* Header */}
          <div className="text-center space-y-2">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-surface-2 border border-border text-2xs text-ink-muted">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-accent" />
              </span>
              <span>Green Room Check</span>
            </div>

            <h1 className="font-display text-2xl font-bold text-ink-primary tracking-tight">
              {room.title}
            </h1>

            <div className="flex items-center justify-center gap-2 pt-1">
              <button
                type="button"
                onClick={copyCode}
                className="inline-flex items-center gap-1.5 font-mono text-xs px-2.5 py-1 rounded-lg bg-surface-2 border border-border text-ink-secondary hover:text-ink-primary hover:border-accent/40 transition-all"
                title="Click to copy room code"
              >
                <span>Room: <strong className="text-accent">{room.room_code}</strong></span>
                {copiedCode ? <Check size={12} className="text-accent" /> : <Copy size={12} />}
              </button>
            </div>
          </div>

          {/* Audio & Mic Hardware Settings */}
          <div className="rounded-2xl border border-border bg-surface-0/60 p-5 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <div className={`h-8 w-8 rounded-lg flex items-center justify-center ${micEnabled ? 'bg-accent/10 text-accent' : 'bg-surface-2 text-ink-muted'}`}>
                  {micEnabled ? <Mic size={16} /> : <MicOff size={16} />}
                </div>
                <div>
                  <h3 className="text-xs font-semibold text-ink-primary">Microphone Input</h3>
                  <p className="text-2xs text-ink-muted">
                    {micEnabled ? 'Speak to verify your audio levels' : 'Microphone is muted'}
                  </p>
                </div>
              </div>

              <Button
                variant={micEnabled ? 'secondary' : 'danger'}
                size="sm"
                onClick={() => setMicEnabled((v) => !v)}
              >
                {micEnabled ? 'Mute' : 'Unmute'}
              </Button>
            </div>

            {/* Dynamic Mic Selection if multiple devices found */}
            {inputs.length > 0 && (
              <div className="pt-1">
                <Select
                  label="Select Microphone Device"
                  value={selectedInput}
                  onChange={(e) => changeInput(e.target.value)}
                  options={[
                    { value: 'default', label: 'System Default Microphone' },
                    ...inputs.map((d) => ({ value: d.deviceId, label: d.label })),
                  ]}
                />
              </div>
            )}

            {/* Live Volume Meter Bar */}
            {micEnabled && (
              <div className="space-y-1.5 pt-1">
                <div className="h-2 w-full rounded-full bg-surface-2 overflow-hidden flex">
                  <motion.div
                    className="h-full bg-gradient-to-r from-accent to-accent-cyan rounded-full transition-all"
                    style={{ width: `${Math.min(100, Math.max(4, level * 120))}%` }}
                  />
                </div>
                <div className="flex justify-between text-2xs text-ink-faint">
                  <span>Input level</span>
                  <span className="font-mono">{ready ? `${Math.round(level * 100)}%` : 'Listening...'}</span>
                </div>
              </div>
            )}

            {/* Audio Output / Speaker Tester */}
            <div className="pt-3 border-t border-border-subtle space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-xs text-ink-primary font-medium">
                  <Headphones size={15} className="text-accent-cyan" />
                  <span>Audio & Speaker Output</span>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    testSpeaker(selectedOutput);
                    testAudioOutput();
                  }}
                  leftIcon={<Volume2 size={14} className={playingTestAudio ? 'text-accent animate-bounce' : ''} />}
                  className="text-xs text-ink-secondary"
                >
                  {playingTestAudio ? 'Playing chime...' : 'Test speaker'}
                </Button>
              </div>

              {outputs.length > 0 && (
                <Select
                  label="Select Speaker Device"
                  value={selectedOutput}
                  onChange={(e) => changeOutput(e.target.value)}
                  options={[
                    { value: 'default', label: 'System Default Speaker' },
                    ...outputs.map((d) => ({ value: d.deviceId, label: d.label })),
                  ]}
                />
              )}
            </div>
          </div>

          {/* Language dialect selection */}
          <div className="space-y-4">
            <h3 className="text-xs font-semibold text-ink-primary uppercase tracking-wider">
              Conversation Dialect Preferences
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Select
                label="I will speak in"
                value={speakingLang}
                onChange={(e) => onUpdateSpeakingLang(e.target.value)}
                options={langOptions}
              />
              <Select
                label="I want to hear"
                value={listeningLang}
                onChange={(e) => onUpdateListeningLang(e.target.value)}
                options={langOptions}
              />
            </div>
            <p className="text-2xs text-ink-muted text-center">
              You can still change your listening language anytime during the live call.
            </p>
          </div>

          {/* Enter Button */}
          <Button
            size="lg"
            onClick={onEnterRoom}
            rightIcon={<ArrowRight size={18} />}
            className="w-full py-4 text-base font-semibold shadow-lg shadow-accent/25"
          >
            Enter Conversation
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
