import { useCallback, useEffect, useRef, useState } from 'react';

interface UseSpeechDictationOptions {
  lang: string;
  enabled: boolean;
  onResult: (text: string, confidence: number) => void;
  onInterim?: (text: string) => void;
  /** Silence pause threshold in ms to wait before emitting a complete sentence (default: 750ms) */
  silenceThresholdMs?: number;
  /** Suppress microphone processing while peer's audio/TTS is playing to stop acoustic feedback echo */
  duckDuringPlayback?: boolean;
}

/**
 * Robust continuous speech-to-text via Web Speech API with Conversational Turn-Taking (VAD).
 * Buffers spoken fragments into complete, coherent sentences and eliminates duplicate/stuttered output.
 */
export function useSpeechDictation({
  lang,
  enabled,
  onResult,
  onInterim,
  silenceThresholdMs = 1250,
  duckDuringPlayback = false,
}: UseSpeechDictationOptions) {
  const [listening, setListening] = useState(false);
  const [supported, setSupported] = useState(true);
  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const onResultRef = useRef(onResult);
  const onInterimRef = useRef(onInterim);
  const enabledRef = useRef(enabled);
  const duckRef = useRef(duckDuringPlayback);
  const permissionDeniedRef = useRef(false);

  // Buffer state to assemble full sentences and prevent fragmented stutter
  const accumulatedBufferRef = useRef<string>('');
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const recentSentRef = useRef<{ text: string; time: number }[]>([]);

  onResultRef.current = onResult;
  onInterimRef.current = onInterim;
  enabledRef.current = enabled;
  duckRef.current = duckDuringPlayback;

  const langTag = mapLang(lang);

  const flushBuffer = useCallback(() => {
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }

    let raw = accumulatedBufferRef.current.trim();
    accumulatedBufferRef.current = '';

    if (!raw) return;

    // Remove immediately repeated duplicate words ("hello hello" -> "hello")
    const words = raw.split(/\s+/);
    const dedupedWords: string[] = [];
    for (let i = 0; i < words.length; i++) {
      if (i === 0 || words[i].toLowerCase() !== words[i - 1].toLowerCase()) {
        dedupedWords.push(words[i]);
      }
    }
    raw = dedupedWords.join(' ').trim();
    if (!raw) return;

    // Prune sentences older than 6 seconds from deduplication cache
    const now = Date.now();
    recentSentRef.current = recentSentRef.current.filter((item) => now - item.time < 6000);

    // Normalize for deduplication comparison
    const normalized = raw.toLowerCase().replace(/[.,!?;:।]/g, '').trim();

    // Check against recent utterances to prevent stutter and repeating prefixes
    for (const item of recentSentRef.current) {
      const itemNorm = item.text.toLowerCase().replace(/[.,!?;:।]/g, '').trim();
      // 1. Exact duplicate
      if (itemNorm === normalized) {
        return;
      }
      // 2. Already included inside an earlier sentence
      if (itemNorm.includes(normalized) && normalized.length > 2) {
        return;
      }
      // 3. New utterance extends an earlier sentence ("hello" -> "hello how are you")
      if (normalized.startsWith(itemNorm) && itemNorm.length >= 3) {
        const delta = raw.slice(item.text.length).replace(/^[.,!?;:।\s]+/, '').trim();
        if (!delta || delta.length < 2) {
          return;
        }
        raw = delta;
        break;
      }
    }

    recentSentRef.current.push({ text: raw, time: now });
    onResultRef.current(raw, 0.95);
    onInterimRef.current?.('');
  }, []);

  useEffect(() => {
    const win = typeof window !== 'undefined' ? (window as any) : undefined;
    const SR = win ? win.SpeechRecognition || win.webkitSpeechRecognition : undefined;

    if (!SR) {
      setSupported(false);
      return;
    }

    const recognition = new SR();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = langTag;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      setListening(true);
      permissionDeniedRef.current = false;
    };

    recognition.onend = () => {
      setListening(false);
      // Flush any lingering utterance before restarting
      if (accumulatedBufferRef.current.trim()) {
        flushBuffer();
      }

      // Auto-restart while enabled and permission was not permanently denied
      if (enabledRef.current && !permissionDeniedRef.current && recognitionRef.current === recognition) {
        try {
          recognition.start();
        } catch {
          /* already started */
        }
      }
    };

    recognition.onerror = (event: any) => {
      if (event && (event.error === 'not-allowed' || event.error === 'service-not-allowed')) {
        permissionDeniedRef.current = true;
      }
      setListening(false);
    };

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      // If computer speaker is currently playing peer audio, drop input to stop acoustic loop
      if (duckRef.current) {
        return;
      }

      let currentInterim = '';
      let newFinalPart = '';

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        const transcript = result[0]?.transcript ?? '';
        if (result.isFinal) {
          newFinalPart += ' ' + transcript;
        } else {
          currentInterim += transcript;
        }
      }

      if (newFinalPart.trim()) {
        const cleanedPart = newFinalPart.trim();
        // Append to current sentence buffer
        if (accumulatedBufferRef.current) {
          // Avoid appending if already contains this chunk at the end
          if (!accumulatedBufferRef.current.endsWith(cleanedPart)) {
            accumulatedBufferRef.current = `${accumulatedBufferRef.current} ${cleanedPart}`.trim();
          }
        } else {
          accumulatedBufferRef.current = cleanedPart;
        }
      }

      // Emit visual live feedback for the speaker HUD
      const livePreview = `${accumulatedBufferRef.current} ${currentInterim}`.trim();
      if (livePreview) {
        onInterimRef.current?.(livePreview);
      }

      // Reset turn-taking silence timer
      if (silenceTimerRef.current) {
        clearTimeout(silenceTimerRef.current);
      }

      // If the buffer ends with sentence-ending punctuation and has substance, flush faster
      const hasTerminalPunctuation = /[.!?।]\s*$/.test(accumulatedBufferRef.current);
      const wordCount = accumulatedBufferRef.current.split(/\s+/).filter(Boolean).length;
      const waitTime = hasTerminalPunctuation && wordCount >= 3 ? 750 : silenceThresholdMs;

      silenceTimerRef.current = setTimeout(() => {
        flushBuffer();
      }, waitTime);
    };

    recognitionRef.current = recognition;

    return () => {
      if (silenceTimerRef.current) {
        clearTimeout(silenceTimerRef.current);
      }
      recognitionRef.current = null;
      try {
        recognition.stop();
      } catch {
        /* ignore */
      }
    };
  }, [langTag, flushBuffer, silenceThresholdMs]);

  useEffect(() => {
    const rec = recognitionRef.current;
    if (!rec) return;
    if (enabled) {
      permissionDeniedRef.current = false;
      try {
        rec.lang = langTag;
        rec.start();
      } catch {
        /* already running */
      }
    } else {
      try {
        rec.stop();
      } catch {
        /* ignore */
      }
      setListening(false);
      accumulatedBufferRef.current = '';
      if (silenceTimerRef.current) {
        clearTimeout(silenceTimerRef.current);
      }
    }
  }, [enabled, langTag]);

  const stop = useCallback(() => {
    try {
      recognitionRef.current?.stop();
    } catch {
      /* ignore */
    }
  }, []);

  return { listening, supported, stop };
}

export function mapLang(code: string): string {
  const map: Record<string, string> = {
    en: 'en-US',
    hi: 'hi-IN',
    mr: 'mr-IN',
    te: 'te-IN',
    bn: 'bn-IN',
    ta: 'ta-IN',
    gu: 'gu-IN',
    kn: 'kn-IN',
    ml: 'ml-IN',
    pa: 'pa-IN',
    es: 'es-ES',
    fr: 'fr-FR',
    de: 'de-DE',
    it: 'it-IT',
    pt: 'pt-BR',
    ru: 'ru-RU',
    ja: 'ja-JP',
    ko: 'ko-KR',
    zh: 'zh-CN',
    ar: 'ar-SA',
  };
  return map[code] ?? code;
}

// Minimal SpeechRecognition typings for environments without DOM lib extras
interface SpeechRecognition extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  onstart: ((this: SpeechRecognition, ev: Event) => void) | null;
  onend: ((this: SpeechRecognition, ev: Event) => void) | null;
  onerror: ((this: SpeechRecognition, ev: Event) => void) | null;
  onresult: ((this: SpeechRecognition, ev: SpeechRecognitionEvent) => void) | null;
}

interface SpeechRecognitionEvent extends Event {
  resultIndex: number;
  results: SpeechRecognitionResultList;
}

declare global {
  interface Window {
    SpeechRecognition?: new () => SpeechRecognition;
    webkitSpeechRecognition?: new () => SpeechRecognition;
  }
}
