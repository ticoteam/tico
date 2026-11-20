/** Where the conversation sits: Tico's sheet, the 320 second chat, an agent's page (stretching), a task's chat. */
export type Variant = 'tico' | 'side' | 'page' | 'task';

/** The page a message was sent from, as `refs.page` on the message: Tico reads what the person was looking at. */
export interface PageRef {
  path: string;
  title: string;
}

export interface Attachment {
  id: string;
  name?: string;
  title?: string;
  content_type?: string;
  /** Where its bytes are (`/api/v2/files/<id>…`). */
  url?: string;
  size?: number;
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
  /** Said in Slack: the channel, the message and its thread there (`thread_ts` is empty on a thread's first message),
      and the message's own link. */
  slack?: { channel?: string; channel_name?: string; ts?: string; thread_ts?: string | null; permalink?: string };
  [k: string]: unknown;
}

/** The message a reply answers, as the timeline quotes it (`reply_to`); `hidden` when the reader may not see it. */
export interface ReplyQuote {
  id: string;
  author?: string;
  author_name?: string;
  excerpt?: string;
  hidden?: boolean;
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
  /** The message this one answers; `reply_to` is its quote (null: none). The stream sends only `in_reply_to`. */
  in_reply_to?: string | null;
  reply_to?: ReplyQuote | null;
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
