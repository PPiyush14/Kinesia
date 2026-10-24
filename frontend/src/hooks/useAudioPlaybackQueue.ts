import { useCallback, useEffect, useRef, useState } from 'react';

type Playback = { url: string } | { text: string; lang: string };

/** One FIFO for provider audio and browser speech, with cancellable callbacks. */
export function useAudioPlaybackQueue(muted: boolean, onPlaying: (playing: boolean) => void) {
  const queue = useRef<Playback[]>([]);
  const current = useRef<object | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const speech = useRef<SpeechSynthesisUtterance | null>(null);
  const blocked = useRef(false);
  const mutedRef = useRef(muted);
  const onPlayingRef = useRef(onPlaying);
  mutedRef.current = muted;
  onPlayingRef.current = onPlaying;
  const [autoplayBlocked, setAutoplayBlocked] = useState(false);

  const process = useCallback(function playNext() {
    if (mutedRef.current || blocked.current || current.current || !queue.current.length) return;
    const item = queue.current[0];
    const attempt = {};
    current.current = attempt;
    onPlayingRef.current(true);
    const finish = () => {
      if (current.current !== attempt) return;
      current.current = null;
      queue.current.shift();
      onPlayingRef.current(false);
      playNext();
    };

    if ('text' in item) {
      try {
        if (!('speechSynthesis' in window)) { finish(); return; }
        const utterance = new SpeechSynthesisUtterance(item.text);
        speech.current = utterance;
        utterance.lang = item.lang;
        utterance.onend = finish;
        utterance.onerror = finish;
        window.speechSynthesis.speak(utterance);
      } catch { finish(); }
      return;
    }

    try {
      // Each attempt owns its media element, so an old error cannot finish a new item.
      const element = new Audio();
      audio.current = element;
      const output = localStorage.getItem('kinesia_audio_output');
      if (output && output !== 'default' && typeof (element as any).setSinkId === 'function') {
        (element as any).setSinkId(output).catch(() => {});
      }
      element.src = item.url;
      element.onended = finish;
      element.onerror = finish;
      void element.play().then(() => {
        if (current.current === attempt) setAutoplayBlocked(false);
      }).catch((err) => {
        if (current.current !== attempt) return;
        if (err?.name !== 'NotAllowedError') { finish(); return; }
        current.current = null;
        blocked.current = true;
        onPlayingRef.current(false);
        setAutoplayBlocked(true);
      });
    } catch { finish(); }
  }, []);

  const cancelAudio = useCallback(() => {
    current.current = null;
    queue.current = [];
    blocked.current = false;
    if (audio.current) {
      audio.current.onended = null;
      audio.current.onerror = null;
      audio.current.pause();
      audio.current.removeAttribute('src');
      audio.current.load();
      audio.current = null;
    }
    if (speech.current) {
      speech.current.onend = null;
      speech.current.onerror = null;
      window.speechSynthesis?.cancel();
      speech.current = null;
    }
    onPlayingRef.current(false);
    setAutoplayBlocked(false);
  }, []);

  const queueAudio = useCallback((item: Playback) => {
    if (mutedRef.current) return;
    queue.current.push(item);
    process();
  }, [process]);

  const unlockAndResumeAudio = useCallback(() => {
    blocked.current = false;
    setAutoplayBlocked(false);
    process();
  }, [process]);

  useEffect(() => { if (muted) cancelAudio(); }, [muted, cancelAudio]);
  useEffect(() => cancelAudio, [cancelAudio]);

  return { queueAudio, cancelAudio, autoplayBlocked, unlockAndResumeAudio };
}
