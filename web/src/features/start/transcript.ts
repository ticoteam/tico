import { useSyncExternalStore } from 'react';
import type { ChatMessage } from '@/conversation';

/* What Tico said in the first run, kept for this page's life once the owner finishes: Tico's sheet then shows its real
   chat, with these lines above what follows (Conversation `extra`), so the setup reads as one chat. Never stored: the
   lines are the app's own words, not messages on the server. */

let lines: ChatMessage[] = [];
const listeners = new Set<() => void>();

export function keepTranscript(next: ChatMessage[]): void {
  lines = next;
  for (const fn of listeners) fn();
}

const subscribe = (fn: () => void) => {
  listeners.add(fn);
  return () => void listeners.delete(fn);
};

/** The first run's lines, once it finished on this page; none otherwise. */
export const useSetupTranscript = (): ChatMessage[] => useSyncExternalStore(subscribe, () => lines);
