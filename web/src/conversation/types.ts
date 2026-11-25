import type { FromPage } from '@/shell/pageOwner';

/** Where the conversation sits: Tico's sheet, the 320 second chat, an agent's page (stretching), a task's chat. */
export type Variant = 'tico' | 'side' | 'page' | 'task';


export interface Attachment {
  id: string;
  name?: string;
  title?: string;
  content_type?: string;
  /** Where its bytes are (`/api/v2/files/<id>…`). */
  url?: string;
  size?: number;
  /** A page's signed address to show it live from (backend/visuals.py), fresh on each read; null when it can't. */
  src?: string | null;
  file_id?: string;
  version?: number;
  /** A page app (backend/page_apps.py): framed sealed, with its bridge. */
  app?: boolean;
}

/** What a message's `refs` holds that this module reads; the server sends more. */
export interface Refs {
  /** The page it was written on (backend/page_owners.py check_from): its owner's turn reads it. */
  from_page?: FromPage;
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
  /** First page only: the bots the thread waits on. */
  bot_status?: BotWait[];
}

/** A reply being written, from the `runs` topic (compact output). */
export interface LiveRun {
  run: string;
  /** bot:<id> */
  bot: string;
  parts: string[];
  typing: string | null;
  /** The last thing it did: 'think' before any output, 'work' on a tool step, 'type' while text streams; 'wait': it
      got the message and its run has not started (replying.ts draws it from the thread's bot status). */
  doing: 'think' | 'work' | 'type' | 'wait';
  /** Its newest tool call in a few words ("Reading Finance"), from the `runs` topic's step `label`; while it waits, what
      it finishes first ("Busy with other work"). */
  label?: string;
  ended: boolean;
  /** The person's message it answers, as the thread's bot status names it: a run that ends without a reply says so
      only then. */
  message?: string;
  /** How it ended: the run's state ('completed', 'failed', 'interrupted', ...). */
  end?: string;
  /** It ended and no reply came: its row is the one line that says so ('none': it finished without one). */
  quiet?: 'none' | 'failed';
}

/** One bot the thread waits on, and why (GET /api/v2/threads/{id}/bot-status, backend/thread_bot_status.py): the
    timeline's first page brings them and the `threads` topic's `bot_status` keeps them current. */
export interface BotWait {
  actor: string;
  name: string;
  /** 'up_next': it got the message and finishes other work first; 'working': its run here is going; 'cant_reply':
      something stops it (`detail` says what). */
  state: 'working' | 'up_next' | 'cant_reply';
  detail: string;
  since: string;
  run_id: string | null;
  /** The newest message of a person's that woke it here, when the reader reads it. */
  message_id?: string | null;
}
