// The conversation's reads and writes: its timeline (paged backwards), sending, read marks, and the live reply.
// Messages themselves reach the timeline through live/patch.ts (the built-in `messages` patcher); this file adds
// what only a conversation knows: a pending send giving way to its echo, and a reply being written (`runs`).
import { infiniteQueryOptions, queryOptions, type InfiniteData, type QueryKey } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { Assistant, SlackMessage, SlackThread } from '@/api/schema';
import { keys, queryClient, quietRoot, write } from '@/data';
import { onTopic } from '@/live';
import { newId } from '@/lib/ids';
import type { FromPage } from '@/shell/pageOwner';
import type { ChatMessage, LiveRun, ReplyQuote, TimelinePage, Variant } from './types';

const PAGE = 50;
const enc = encodeURIComponent;

export const timelineKey = (id: string) => ['thread', id, 'timeline'] as const;
export const liveKey = (id: string) => ['thread', id, 'live'] as const;
export const proposalKey = (aid: string) => ['assistant', 'action', aid] as const;

export type TicoInfo = Pick<Assistant, 'available' | 'bot' | 'can_turn_on' | 'state'> & { room_id: string | null };

/**
 * GET /api/v2/assistant, kept to the few fields this app uses: whether Tico is on and its room. The answer also carries
 * the chat's recent messages (about 100 KB), which the chat reads from its timeline instead, so Tico's power and its
 * room share one read: asked for together (the first load, a `bots` change), they cost one.
 */
const ticoInfoQuery = queryOptions({
  queryKey: ['assistant', 'info'] as const,
  staleTime: 0,
  queryFn: async (): Promise<TicoInfo> => {
    const a = await api.get<Assistant>('/api/v2/assistant');
    return { available: a.available, bot: a.bot, can_turn_on: a.can_turn_on, state: a.state, room_id: a.room_id ?? null };
  },
});

/** Reads Tico's info, sharing a read already on its way (a read that ended is not reused: each ask is fresh). */
export const readTicoInfo = () => queryClient.fetchQuery(ticoInfoQuery);

/** Tico's chat with the signed-in person: the conversation id Tico's sheet shows. */
export const ticoRoomQuery = queryOptions({
  queryKey: ['assistant', 'room'] as const,
  queryFn: async () => (await readTicoInfo()).room_id,
});

export type Timeline = InfiniteData<TimelinePage, unknown>;

/**
 * The flat timeline. pages[0] is the newest page and each next page is older, so a refetch (a `reset`) reads the
 * newest first and walks back as far as the person had scrolled.
 */
export const timelineQuery = (id: string) =>
  infiniteQueryOptions({
    queryKey: timelineKey(id),
    queryFn: ({ pageParam, signal }) =>
      api.get<TimelinePage>(
        `/api/v2/threads/${enc(id)}/timeline?flat=1&limit=${PAGE}${pageParam == null ? '' : `&cursor=${enc(String(pageParam))}`}`,
        { signal },
      ),
    initialPageParam: null as string | number | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

/** Oldest first, as the list draws it. */
export function rowsOf(data: Timeline | undefined): ChatMessage[] {
  if (!data) return [];
  const out: ChatMessage[] = [];
  for (let p = data.pages.length - 1; p >= 0; p--) out.push(...(data.pages[p]?.messages ?? []));
  return out;
}

/** The live replies of a conversation, kept in the cache so every view of it shows the same rows. */
export const liveQuery = (id: string) =>
  queryOptions({
    queryKey: liveKey(id),
    queryFn: () => queryClient.getQueryData<LiveRun[]>(liveKey(id)) ?? [],
    initialData: [] as LiveRun[],
  });

export interface Proposal {
  id: string;
  summary: string;
  description?: string;
  status: 'pending' | 'running' | 'done' | 'failed' | 'canceled' | 'expired';
  by_name?: string;
}

/** An assistant action proposal: the confirm card a message points to with `refs.action`. */
export const proposalQuery = (aid: string) =>
  queryOptions({
    queryKey: proposalKey(aid),
    queryFn: async ({ signal }) => (await api.get<{ action: Proposal }>(`/api/v2/assistant/actions/${enc(aid)}`, { signal })).action,
  });

export async function decideProposal(aid: string, yes: boolean): Promise<Proposal> {
  const { action } = await api.post<{ action: Proposal }>(`/api/v2/assistant/actions/${enc(aid)}/${yes ? 'confirm' : 'cancel'}`, {});
  queryClient.setQueryData(proposalKey(aid), action);
  return action;
}

/* ---------------------------------------------------------------- sending */

function dropRow(key: QueryKey, rowId: string): void {
  queryClient.setQueryData<Timeline>(key, (d) => {
    if (!d) return d;
    let changed = false;
    const pages = d.pages.map((p) => {
      if (!p.messages.some((m) => m.id === rowId)) return p;
      changed = true;
      return { ...p, messages: p.messages.filter((m) => m.id !== rowId) };
    });
    return changed ? { ...d, pages } : d;
  });
}

export interface Send {
  /** The conversation (a thread id; for Tico, the person's assistant room). */
  id: string;
  variant: Variant;
  text: string;
  files?: File[];
  /** The page it was written on (`from_page`, backend/page_owners.py check_from). */
  from?: FromPage | null;
  me: string;
  /** Retry: the failed row to send again in its place. */
  retry?: string;
  /** A reply: the message it answers (`in_reply_to`), and its quote shown on the pending row. Threads only: Tico's own
      chat takes no reply. */
  replyTo?: string;
  quote?: ReplyQuote | null;
}

type Sent = ChatMessage | { message?: ChatMessage } | { hold?: unknown };

function post(s: Send, idempotencyKey: string): Promise<Sent> {
  const files = s.files ?? [];
  const opts = { idempotencyKey };
  if (s.variant === 'tico') {
    if (!files.length) return api.post<Sent>('/api/v2/assistant/messages', { text: s.text, ...(s.from && { from_page: s.from }) }, opts);
    const f = new FormData();
    f.set('text', s.text);
    if (s.from) f.set('from_page', JSON.stringify(s.from));
    for (const file of files) f.append('files', file);
    return api.post<Sent>('/api/v2/uploads/assistant/messages', f, opts);
  }
  const reply = s.replyTo ? { in_reply_to: s.replyTo } : {};
  if (!files.length)
    return api.post<Sent>(`/api/v2/threads/${enc(s.id)}/messages`, { text: s.text, refs: s.from ? { from_page: s.from } : {}, ...reply }, opts);
  const f = new FormData();
  f.set('text', s.text);
  if (s.replyTo) f.set('in_reply_to', s.replyTo);
  if (s.from) f.set('from_page', JSON.stringify(s.from));
  for (const file of files) f.append('files', file);
  return api.post<Sent>(`/api/v2/uploads/threads/${enc(s.id)}/messages`, f, opts);
}

/** The posted message, or null when the server held it (a hold has no id). */
const answerOf = (r: Sent): ChatMessage | null => {
  const m = 'message' in r && r.message ? r.message : (r as ChatMessage);
  return m && typeof m.id === 'string' ? m : null;
};

const withRows = (d: Timeline | undefined, fn: (rows: ChatMessage[]) => ChatMessage[]): Timeline | undefined => {
  if (!d?.pages.length) return d;
  const first = d.pages[0] as TimelinePage;
  const messages = fn(first.messages);
  return messages === first.messages ? d : { ...d, pages: d.pages.with(0, { ...first, messages }) };
};

const markRow = (tmp: string, pending: ChatMessage['pending']) => (rows: ChatMessage[]) =>
  rows.some((m) => m.id === tmp) ? rows.map((m) => (m.id === tmp ? { ...m, pending } : m)) : rows;

/**
 * Show the message at once, send it, and swap in the server's row. The stream's echo may land first: the pending row
 * gives way to it (installConversationLive) and the answer then adds nothing, so it never shows twice. A failure keeps
 * the row, marked "Not sent", for Retry; a held message is marked "Held". Resolves with the real message, or null.
 */
/** What a message says of its page: what is open there (a doc, a mail) goes only to Tico, which acts with the person's
    own rights; any other chat's members may not read it, so they are told the page alone. Every send passes here. */
const pageFor = (s: Send): FromPage | null => (!s.from ? null : s.variant === 'tico' ? s.from : { page: s.from.page, label: s.from.label });

export async function sendMessage(given: Send): Promise<ChatMessage | null> {
  const s = { ...given, from: pageFor(given) };
  const tmp = s.retry ?? `tmp:${newId()}`;
  const row: ChatMessage = {
    id: tmp,
    thread_id: s.id,
    conversation_id: s.id,
    sender_id: s.me,
    body: s.text,
    created_at: new Date().toISOString(),
    refs: {
      ...(s.from && { from_page: s.from }),
      ...(s.files?.length && { attachments: s.files.map((f, i) => ({ id: `${tmp}:${i}`, name: f.name, content_type: f.type })) }),
    },
    pending: 'sending',
    ...(s.files?.length && { files: s.files }),
    ...(s.replyTo && { in_reply_to: s.replyTo, reply_to: s.quote ?? { id: s.replyTo } }),
  };
  try {
    const answer = await write<Sent>({
      key: timelineKey(s.id),
      optimistic: (d: Timeline | undefined) => withRows(d, (rows) => [...rows.filter((m) => m.id !== tmp), row]),
      send: (key) => post(s, key),
      settle: (d: Timeline | undefined, answer: Sent) => {
        const real = answerOf(answer);
        if (!real) return withRows(d, markRow(tmp, 'held'));
        if (!d) return d;
        const has = d.pages.some((p) => p.messages.some((m) => m.id === real.id));
        const out = withRows(d, (rows) => rows.filter((m) => m.id !== tmp));
        return has ? out : withRows(out, (rows) => [...rows, real]);
      },
      // A failed send is not taken back: it stays, marked, so the words are not lost.
      revert: (d: Timeline | undefined) => withRows(d, markRow(tmp, 'failed')),
      offline: 'hold',
    });
    return answerOf(answer);
  } catch {
    return null;
  }
}

/** Drop a failed row (the person took the words back into the box). */
export const discardPending = (id: string, tmp: string) => dropRow(timelineKey(id), tmp);

/* ---------------------------------------------------------------- read marks */

const marked = new Map<string, number>();

/** Read up to `seq`. The highest mark wins on the server too, so only a higher one is sent. */
export function markRead(id: string, seq: number): void {
  if ((marked.get(id) ?? 0) >= seq) return;
  marked.set(id, seq);
  api.post(`/api/v2/threads/${enc(id)}/read`, { seq }).catch(() => {
    if (marked.get(id) === seq) marked.delete(id);
  });
}

/* ---------------------------------------------------------------- live */

interface RunEvent {
  run_id?: string;
  conversation_id?: string;
  bot_id?: string;
  state?: string;
  output?: { kind?: string; compact?: boolean; label?: string; payload?: { text?: string; final?: boolean; delta_kind?: string } };
}
interface MessageEvent {
  id?: string;
  conversation_id?: string;
  thread_id?: string;
  message?: ChatMessage;
}

const END_WAIT_MS = 2000;
/** Runs whose output came before their start was seen: drawn from the middle they would start mid-sentence. */
const late = new Set<string>();

function setRuns(cid: string, fn: (runs: LiveRun[]) => LiveRun[]): void {
  const was = queryClient.getQueryData<LiveRun[]>(liveKey(cid)) ?? [];
  const next = fn(was);
  if (next !== was) queryClient.setQueryData(liveKey(cid), next);
}

/** One run's state change or piece of output, as the server's live reply joins them (backend/turns.py reply_parts). */
export function stepRun(runs: LiveRun[], d: RunEvent): LiveRun[] {
  const run = d.run_id;
  if (!run) return runs;
  const i = runs.findIndex((r) => r.run === run);
  const r = runs[i];
  const out = d.output;
  if (!out) {
    if (d.state === 'leased' || d.state === 'running') {
      if (r || !d.bot_id || late.has(run)) return runs;
      return [...runs, { run, bot: `bot:${d.bot_id}`, parts: [], typing: null, doing: 'think', ended: false }];
    }
    if (r && !r.ended && d.state && d.state !== 'input') return runs.with(i, { ...r, ended: true });
    return runs;
  }
  if (!r) {
    late.add(run);
    return runs;
  }
  const text = String(out.payload?.text ?? '');
  let next: LiveRun;
  if (out.kind === 'delta' && !out.compact && out.payload?.delta_kind !== 'thought') {
    next = { ...r, typing: (r.typing ?? '') + text, doing: 'type' };
  } else if (out.kind === 'message' && !out.compact) {
    let parts = r.parts;
    const last = parts[parts.length - 1];
    if (out.payload?.final) {
      if (text.trim()) parts = [text];
    } else if (r.typing !== null) parts = [...parts, text.trim() ? text : r.typing];
    else if (text.trim() && last?.trim() !== text.trim()) parts = [...parts, text];
    next = { ...r, parts, typing: null, doing: 'type' };
  } else if (out.kind === 'tool') {
    // a call starting carries its label ("Filing a task"); one ending keeps the last
    next = { ...r, parts: r.typing !== null ? [...r.parts, r.typing] : r.parts, typing: null, doing: 'work', label: out.label || r.label };
  } else return runs;
  return runs.with(i, next);
}

/** What the live row says: its pieces and what is being typed, a blank line between. */
export const liveText = (r: LiveRun) =>
  [...r.parts, r.typing ?? ''].map((p) => p.trim()).filter(Boolean).join('\n\n');

/** The signed-in person's actor id, from the cache. */
export const meId = () => queryClient.getQueryData<{ id?: string }>(keys.me())?.id;

let installed = false;

/** The conversation's own live patchers; once, from the first Conversation mounted. */
export function installConversationLive(): void {
  if (installed) return;
  installed = true;
  onTopic<RunEvent>('runs', (batch) => {
    for (const d of batch) {
      const cid = d.conversation_id;
      const run = d.run_id;
      if (!cid || !run) continue;
      const was = queryClient.getQueryData<LiveRun[]>(liveKey(cid))?.find((x) => x.run === run);
      setRuns(cid, (runs) => stepRun(runs, d));
      const now = queryClient.getQueryData<LiveRun[]>(liveKey(cid))?.find((x) => x.run === run);
      // Its reply, posted as the run ends, may come a moment later: the row waits for it, then goes.
      if (now?.ended && !was?.ended)
        setTimeout(() => setRuns(cid, (runs) => (runs.some((x) => x.run === run) ? runs.filter((x) => x.run !== run) : runs)), END_WAIT_MS);
    }
  });
  onTopic<MessageEvent>('messages', (batch) => {
    const me = meId();
    for (const e of batch) {
      const m = e.message;
      const cid = e.conversation_id ?? e.thread_id;
      if (!m || !cid) continue;
      // The posted reply takes its live row's place in the same frame.
      const turn = m.refs?.turn_id;
      if (turn) for (const id of new Set([cid, m.thread_id, m.conversation_id])) if (id) setRuns(id, (runs) => (runs.some((r) => r.run === turn) ? runs.filter((r) => r.run !== turn) : runs));
      // My own message echoed before its POST answered: the pending row gives way now, so it never shows twice.
      if (me && m.sender_id === me) {
        for (const id of new Set([cid, m.thread_id, m.conversation_id])) {
          if (!id) continue;
          queryClient.setQueryData<Timeline>(timelineKey(id), (d) => withRows(d, (rows) => {
            const i = rows.findIndex((x) => x.pending === 'sending' && (x.body ?? '') === (m.body ?? ''));
            return i < 0 ? rows : rows.toSpliced(i, 1);
          }));
        }
      }
    }
  });
  onTopic('reset', () => {
    for (const [key, runs] of queryClient.getQueriesData<LiveRun[]>({ queryKey: ['thread'] }))
      if (key[2] === 'live' && runs?.length) queryClient.setQueryData(key, []);
  });
}

/* ---------------------------------------------------------------- outline */

export interface OutlinePrompt {
  id: string;
  created_at: string;
  text: string;
  task?: { id: string; title: string } | null;
}

/** The people's prompts in a chat, oldest first, past the loaded pages (up to 500). */
export const outlineQuery = (id: string) =>
  queryOptions({
    queryKey: ['thread', id, 'outline'] as const,
    queryFn: async ({ signal }) =>
      (await api.get<{ prompts?: OutlinePrompt[] }>(`/api/v2/conversations/${enc(id)}/outline`, { signal })).prompts ?? [],
    staleTime: 0,
  });

/* ---------------------------------------------------------------- a Slack thread a message came from */

/** Where in Slack a message was said, and an agent that reads that channel (the server reads it as that agent). */
export interface SlackPlace {
  bot: string;
  channel: string;
  thread: string;
}

/** Whether a message came in from Slack. Every writer stores the channel and the message's time there (`refs.slack`:
    backend/slack_gateway.py refs_for adds a link, the agent it went to and more; backend/slack_threads.py, a linked
    channel, stores only `channel`, `ts` and `team`). */
export function fromSlack(m: Pick<ChatMessage, 'refs'>): boolean {
  const s = m.refs?.slack;
  return typeof s?.channel === 'string' && !!s.channel && typeof s.ts === 'string' && !!s.ts;
}

/** A message that came in from Slack (`refs.slack`, backend/slack_gateway.py refs_for): its thread, read as the agent
    it was routed to. Null when the message did not come from Slack or names no agent. */
export function slackPlaceOf(m: Pick<ChatMessage, 'refs'>): SlackPlace | null {
  const s = m.refs?.slack;
  const routing = m.refs?.routing as { recipients?: { bot?: unknown }[] } | undefined;
  const bot = routing?.recipients?.find((r) => typeof r.bot === 'string' && r.bot)?.bot as string | undefined;
  const thread = typeof s?.thread_ts === 'string' && s.thread_ts ? s.thread_ts : typeof s?.ts === 'string' ? s.ts : '';
  if (!bot || typeof s?.channel !== 'string' || !s.channel || !thread) return null;
  return { bot: bot.replace(/^bot:/, ''), channel: s.channel, thread };
}

/** One Slack message (backend/messaging.py _slack_message). */
export type SlackLine = SlackMessage;

quietRoot('slackThread');

/** The Slack thread, oldest first (up to 100). The server gives it to the owner only. Read when opened. */
export const slackThreadQuery = ({ bot, channel, thread }: SlackPlace) =>
  queryOptions({
    queryKey: ['slackThread', bot, channel, thread] as const,
    queryFn: async ({ signal }) =>
      (
        await api.get<SlackThread>(
          `/api/v2/messaging/bots/${enc(bot)}/slack-thread?${new URLSearchParams({ channel, thread_ts: thread }).toString()}`,
          { signal },
        )
      ).messages ?? [],
  });

/** A file's facts for its chip and preview: the same read and cache entry as Files' (`['files', 'info', id]`). */
export interface FileFacts {
  id: string;
  title: string;
  name: string | null;
  mime: string | null;
  library_kind?: string;
  size: number | null;
  version?: number;
}
export const fileFactsQuery = (id: string) =>
  queryOptions({
    queryKey: ['files', 'info', id],
    queryFn: () => api.get<{ file: FileFacts }>(`/api/v2/files/${enc(id)}/info`).then((r) => r.file),
    staleTime: 60_000,
    retry: false,
  });

/** A page's own words (HTML), for its small live preview in a message. Read once; a page is not edited under it. */
export const pageTextQuery = (url: `/api/${string}`) =>
  queryOptions({
    queryKey: ['files', 'page-text', url],
    queryFn: ({ signal }) => api.get<string>(url, { signal }).then((t) => (typeof t === 'string' ? t : '')),
    staleTime: Infinity,
    retry: false,
  });
