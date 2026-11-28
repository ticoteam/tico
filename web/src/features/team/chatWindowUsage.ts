import { api } from '@/api/client';
import { desktop } from '@/desktop';
import { newId } from '@/lib';

/* The usage events an agent's chat window sends for the feature's measure (backend/flight.py WEB_EVENTS): the share of
   opened windows a person sends a message from. Each window has its own id, kept in its sessionStorage so a reload is
   the same window: `opened` goes once, when the window first loads (bringing an open one forward loads nothing, so it
   sends nothing), and `sent` once, with the first message written there. The main window never holds the mark: a
   window the browser copies sessionStorage into (window.open) starts without it. */

const KEY = 'tico-next:chat-window';
type Mark = { agent: string; id: string; sent?: boolean };
let memory: Mark | null = null;

/* Memory first: `keep` writes it on every change, so it is never behind sessionStorage, and it is the only copy when
   the browser refuses the write (a full quota). sessionStorage is what a reload starts from. */
function read(agent: string): Mark | null {
  if (memory?.agent === agent) return memory;
  try {
    const v = JSON.parse(sessionStorage.getItem(KEY) ?? 'null') as Mark | null;
    return v && v.agent === agent && typeof v.id === 'string' ? v : null;
  } catch {
    return null;
  }
}

function keep(mark: Mark): void {
  memory = mark;
  try {
    sessionStorage.setItem(KEY, JSON.stringify(mark));
  } catch {
    /* kept in memory alone */
  }
}

function post(event: 'chat_window.opened' | 'chat_window.sent', mark: Mark): void {
  // never holds anything up, never fails a press
  void api.post('/api/v2/web-events', { event, surface: desktop() ? 'desktop' : 'web', agent: mark.agent, window: mark.id }).catch(() => undefined);
}

/** This chat window has loaded: `opened`, unless it is the same window loading again. */
export function chatWindowOpened(agent: string): void {
  if (read(agent)) return;
  const mark = { agent, id: newId() };
  keep(mark);
  post('chat_window.opened', mark);
}

/** A message was sent from this chat window: `sent`, with the first one only. */
export function chatWindowSent(agent: string): void {
  const mark = read(agent);
  if (!mark || mark.sent) return;
  keep({ ...mark, sent: true });
  post('chat_window.sent', mark);
}

/** Tests only: forget the window. */
export function forgetChatWindow(): void {
  memory = null;
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* none */
  }
}
