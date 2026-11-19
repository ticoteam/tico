/* The wire for Tico acting on the open app (architecture.md 5): the `screen` topic on GET /api/v2/events and the
   report the app posts back. The server is built from the same document; every shape it shares lives here. */

/** One `event: screen` frame's data (backend/screen_actions.py, events.py): sent to every open stream of the person's
    own, and only while Tico still waits on the answer. */
export interface ScreenEvent {
  seq: number;
  /** Who asked: the person's Assistant (null when the viewer may not see the actor). */
  actor: string | null;
  at: string;
  /** A uuid; the result is posted against it. */
  id: string;
  /** An action id from `contracts/screen-actions.json`. */
  action: string;
  input: Record<string, unknown>;
  /** Tico already made the change with its own tool: navigate and fly only, never write again. */
  done: boolean;
}

/** POST /api/v2/screen-actions/{id}/result body. A second, late or foreign report is a 404. */
export interface ScreenResult {
  ok: boolean;
  error?: string;
  did?: string;
}

/** The server's cap on `did` and `error`. */
export const RESULT_TEXT_MAX = 500;

export const SCREEN_TOPIC = 'screen';

export const resultPath = (id: string): `/api/${string}` => `/api/v2/screen-actions/${encodeURIComponent(id)}/result`;

export function isScreenEvent(v: unknown): v is ScreenEvent {
  if (typeof v !== 'object' || v === null) return false;
  const e = v as Record<string, unknown>;
  return (
    typeof e.id === 'string' &&
    typeof e.action === 'string' &&
    typeof e.input === 'object' &&
    e.input !== null &&
    (e.done === undefined || typeof e.done === 'boolean')
  );
}
