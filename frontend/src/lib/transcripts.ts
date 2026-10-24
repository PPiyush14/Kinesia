import type { TranscriptTurn } from '@/types';

/** A late history response must neither erase nor duplicate live SSE turns. */
export function mergeTranscriptHistory(
  history: TranscriptTurn[], live: TranscriptTurn[], language: string
): TranscriptTurn[] {
  const id = (turn: TranscriptTurn) => turn.turn_id ?? turn.id;
  const liveById = new Map(live.filter((turn) => id(turn) != null).map((turn) => [String(id(turn)), turn]));
  const historyIds = new Set(history.filter((turn) => id(turn) != null).map((turn) => String(id(turn))));
  return [
    ...history.map((turn) => liveById.get(String(id(turn))) ?? {
      ...turn, localized_text: turn.translations?.[language] || turn.localized_text || turn.original_text,
    }),
    ...live.filter((turn) => id(turn) == null || !historyIds.has(String(id(turn)))),
  ];
}
