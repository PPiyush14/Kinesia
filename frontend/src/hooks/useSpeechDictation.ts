import { useCallback, useEffect, useRef, useState } from 'react';

interface UseSpeechDictationOptions {
  lang: string;
  enabled: boolean;
  onResult: (text: string, confidence: number) => void;
  onInterim?: (text: string) => void;
}

/**
 * Continuous speech-to-text via Web Speech API.
 * Maps ISO language codes to BCP-47 tags where possible.
 */
export function useSpeechDictation({ lang, enabled, onResult, onInterim }: UseSpeechDictationOptions) {
  const [listening, setListening] = useState(false);
  const [supported, setSupported] = useState(true);
  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const onResultRef = useRef(onResult);
  const onInterimRef = useRef(onInterim);
  onResultRef.current = onResult;
  onInterimRef.current = onInterim;

  const langTag = mapLang(lang);

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

    recognition.onstart = () => setListening(true);
    recognition.onend = () => {
      setListening(false);
      // Auto-restart while enabled
      if (enabled && recognitionRef.current === recognition) {
        try {
          recognition.start();
        } catch {
          /* already started */
        }
      }
    };
    recognition.onerror = () => setListening(false);

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        const transcript = result[0]?.transcript ?? '';
        if (result.isFinal) {
          const confidence = result[0]?.confidence ?? 0.9;
          onResultRef.current(transcript.trim(), confidence);
        } else {
          interim += transcript;
        }
      }
      if (interim) onInterimRef.current?.(interim);
    };

    recognitionRef.current = recognition;

    return () => {
      recognitionRef.current = null;
      try {
        recognition.stop();
      } catch {
        /* ignore */
      }
    };
  }, [langTag]);

  useEffect(() => {
    const rec = recognitionRef.current;
    if (!rec) return;
    if (enabled) {
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
    es: 'es-ES',
    fr: 'fr-FR',
    de: 'de-DE',
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
