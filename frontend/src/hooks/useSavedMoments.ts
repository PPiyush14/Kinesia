import { useCallback, useEffect, useRef, useState } from 'react';
import { roomService } from '@/services/api';
import type { SavedMoment } from '@/types';

export function useSavedMoments(
  code: string | undefined,
  onError: (message: string) => void,
  enabled = true,
) {
  const [moments, setMoments] = useState<SavedMoment[]>([]);
  const [busy, setBusy] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const generation = useRef(0);
  const listRevision = useRef(0);
  const errorRef = useRef(onError);
  errorRef.current = onError;
  useEffect(() => {
    if (!enabled) return;
    const current = ++generation.current;
    const revision = ++listRevision.current;
    setLoading(true);
    setMoments([]);
    setBusy([]);
    roomService
      .getMoments(code)
      .then((data) => {
        if (current === generation.current && revision === listRevision.current)
          setMoments(Array.isArray(data) ? data : []);
      })
      .catch(() => {
        if (current === generation.current && revision === listRevision.current)
          errorRef.current('Saved moments could not be loaded.');
      })
      .finally(() => {
        if (current === generation.current && revision === listRevision.current)
          setLoading(false);
      });
    return () => {
      generation.current++;
    };
  }, [code, enabled, reload]);
  const toggle = useCallback(
    async (roomCode: string, turnId: string, saved: boolean) => {
      const current = generation.current;
      setBusy((previous) => [...previous, turnId]);
      try {
        if (saved) await roomService.removeMoment(roomCode, turnId);
        else await roomService.saveMoment(roomCode, turnId);
        const revision = ++listRevision.current;
        const data = await roomService.getMoments(code);
        if (
          current === generation.current &&
          revision === listRevision.current
        ) {
          setMoments(Array.isArray(data) ? data : []);
          setLoading(false);
        }
      } catch (error) {
        if (current === generation.current)
          errorRef.current(
            error instanceof Error
              ? error.message
              : 'Could not update this saved moment.',
          );
      } finally {
        if (current === generation.current)
          setBusy((previous) => previous.filter((id) => id !== turnId));
      }
    },
    [code],
  );
  const refresh = useCallback(() => setReload((value) => value + 1), []);
  return {
    moments,
    busy,
    loading,
    toggle,
    refresh,
    saved: new Set(moments.map((moment) => moment.turn_id)),
  };
}
