/** Where the conversation sits: Tico's sheet, the 320 second chat, an agent's page (stretching), a task's chat. */
export type Variant = 'tico' | 'side' | 'page' | 'task';

/** The page a message was sent from: the look chip, and `refs.page` on the message. */
export interface PageRef {
  path: string;
  title: string;
}

export interface Attachment {
  id: string;
  name?: string;
  title?: string;
  content_type?: string;
}

/** What a message's `refs` holds that this module reads; the server sends more. */
export interface Refs {
  page?: PageRef;
  /** An assistant action proposal (a confirm or approval card). */
  action?: string;
  /** The run that posted this reply: its live row gives way to it. */
  turn_id?: string;
  /** What Tico did on the page, one line each ("Opened Agents"). */
  did?: string | string[];
  /** Tico could not do something: shown as a fault line. */
  fault?: string;
  attachments?: Attachment[];
  /** Said in Slack: the channel and the message's own link there. */
  slack?: { channel?: string; channel_name?: string; permalink?: string };
  [k: string]: unknown;
}

/** A timeline row (GET /api/v2/threads/{id}/timeline?flat=1), plus the local fields of a pending send. */
export interface ChatMessage {
  id: string;
  seq?: number | null;
  revision?: number;
  conversation_id?: string;
  thread_id?: string;
  depth?: number;
  sender_id?: string;
  sender_name?: string;
  kind?: string;
  body?: string | null;
  created_at?: string;
  deleted?: boolean;
  hidden?: boolean;
  refs?: Refs;
  run?: { run_id?: string; steps?: number; took_s?: number } | null;
  /** Local only: a send that has not been answered, or that failed. */
  pending?: 'sending' | 'failed' | 'held';
  /** Local only: the files a pending send carries, kept for Retry. */
  files?: File[];
}

export interface TimelinePage {
  messages: ChatMessage[];
  next_cursor?: string | number | null;
  read_marks?: Record<string, number>;
}

/** A reply being written, from the `runs` topic (compact output). */
export interface LiveRun {
  run: string;
  /** bot:<id> */
  bot: string;
  parts: string[];
  typing: string | null;
  /** The last thing it did: 'think' before any output, 'work' on a tool step, 'type' while text streams. */
  doing: 'think' | 'work' | 'type';
  ended: boolean;
}
