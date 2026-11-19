/* The wire for Tico acting on the open app (architecture.md 5): the `screen` topic on GET /api/v2/events and the
   report the app posts back. The server is built from the same document; every shape it shares lives here. */

/** One `event: screen` frame's data. Only the person's own streams get these. */
export interface ScreenEvent {
  seq: number;
  /** The Assistant bot that asked. */
  actor: string;
  at: string;
  /** A uuid; the result is posted against it. */
  id: string;
  /** An action id from `contracts/screen-actions.json`. */
  action: string;
  input: unknown;
  /** The tab that sent the message this turn answers, or null for a background turn. */
  client: string | null;
  turn_id: string | null;
  message_id: string | null;
  /** Tico already made the change with its own tool: navigate and fly only, never write again. */
  done?: boolean;
}

/** POST /api/v2/screen-actions/{id}/result body. */
export interface ScreenResult {
  ok: boolean;
  error?: string;
  did?: string;
}

export const SCREEN_TOPIC = 'screen';

export const resultPath = (id: string): `/api/${string}` => `/api/v2/screen-actions/${encodeURIComponent(id)}/result`;

/** The tab id travels as `client` on POST /api/v2/assistant/messages and as `client=` on the events URL. */
export const TAB_PARAM = 'client';

export function isScreenEvent(v: unknown): v is ScreenEvent {
  if (typeof v !== 'object' || v === null) return false;
  const e = v as Record<string, unknown>;
  return (
    typeof e.id === 'string' &&
    typeof e.action === 'string' &&
    (e.client === null || e.client === undefined || typeof e.client === 'string') &&
    (e.done === undefined || typeof e.done === 'boolean')
  );
}
