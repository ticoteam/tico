// Tests only: an EventSource that is fed the server's own wire text, and frames built the way backend/events.py
// builds them (`_frame`: `id: <seq>` when there is one, `event: <topic>`, `data: <json>` with sorted keys and no
// spaces, as store.encode writes it).

type Listener = (e: Event) => void;

export class FakeEventSource {
  static all: FakeEventSource[] = [];
  static get last(): FakeEventSource {
    const es = FakeEventSource.all.at(-1);
    if (!es) throw new Error('no EventSource was opened');
    return es;
  }

  readonly url: string;
  readyState = 0;
  lastEventId = '';
  onerror: ((e: Event) => void) | null = null;
  private listeners = new Map<string, Set<Listener>>();

  constructor(url: string | URL) {
    this.url = String(url);
    FakeEventSource.all.push(this);
  }

  get params(): URLSearchParams {
    return new URL(this.url, 'http://localhost').searchParams;
  }

  addEventListener(type: string, fn: Listener): void {
    let set = this.listeners.get(type);
    if (!set) this.listeners.set(type, (set = new Set()));
    set.add(fn);
  }

  removeEventListener(type: string, fn: Listener): void {
    this.listeners.get(type)?.delete(fn);
  }

  close(): void {
    this.readyState = 2;
  }

  /** Parse server-sent text as a browser does and dispatch each event. */
  feed(text: string): void {
    for (const block of text.split('\n\n')) {
      let type = 'message';
      const data: string[] = [];
      let dispatch = false;
      for (const line of block.split('\n')) {
        if (!line || line.startsWith(':')) continue;
        const at = line.indexOf(':');
        const field = at < 0 ? line : line.slice(0, at);
        const value = at < 0 ? '' : line.slice(at + 1).replace(/^ /, '');
        if (field === 'id') this.lastEventId = value;
        else if (field === 'event') type = value;
        else if (field === 'data') {
          data.push(value);
          dispatch = true;
        }
      }
      if (!dispatch) continue;
      this.readyState = 1;
      const e = new MessageEvent(type, { data: data.join('\n'), lastEventId: this.lastEventId });
      for (const fn of [...(this.listeners.get(type) ?? [])]) fn(e);
    }
  }

  /** The connection dropped (or the server ended the stream). */
  fail(): void {
    this.readyState = 2;
    this.onerror?.(new Event('error'));
  }
}

const sorted = (v: unknown): unknown =>
  Array.isArray(v)
    ? v.map(sorted)
    : v && typeof v === 'object'
      ? Object.fromEntries(
          Object.keys(v)
            .sort()
            .map((k) => [k, sorted((v as Record<string, unknown>)[k])]),
        )
      : v;

export const frame = (seq: number | null, topic: string, data: object): string =>
  (seq != null ? `id: ${seq}\n` : '') + `event: ${topic}\ndata: ${JSON.stringify(sorted(data))}\n\n`;

export const opening = (seq: number, after = seq, topics: string[] = ['bots', 'needs', 'threads', 'calendar', 'setup']) =>
  'retry: 1000\n\n' + frame(null, 'ready', { seq, after, topics, needs_version: 'v1' });

export const AT = '2026-11-19T10:00:00.000Z';

/** A task as GET /api/v2/tasks shows it (the fields the patcher reads, plus a title). */
export const task = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  title: `Task ${id}`,
  requester_id: 'human:p1',
  owner_id: 'bot:b1',
  status: 'open',
  created_at: AT,
  updated_at: AT,
  due_at: null,
  version: 1,
  pipeline_id: 'p1',
  stage_id: 's1',
  turn: null,
  late: null,
  lane: 'company',
  labels: [],
  ...over,
});

export const taskFrame = (seq: number, t: ReturnType<typeof task>, kind = 'update') =>
  frame(seq, 'tasks', { seq, actor: 'human:p1', at: AT, id: t.id, kind, kinds: [kind], task: t });

/** A message as a `messages` event carries it (docs/dev/threads-contract.md section 9). */
export const message = (id: string, thread: string, seq: number, over: Record<string, unknown> = {}) => ({
  id,
  conversation_id: thread,
  thread_id: thread,
  root_id: thread,
  seq,
  revision: 0,
  from_actor: 'human:p1',
  to_actor: null,
  kind: 'say',
  body: `Message ${id}`,
  refs: {},
  created: AT,
  edited_at: null,
  ...over,
});

export const messageFrame = (seq: number, m: ReturnType<typeof message>) =>
  frame(seq, 'messages', {
    seq,
    actor: m.from_actor,
    at: AT,
    id: m.id,
    conversation_id: m.conversation_id,
    thread_id: m.thread_id,
    root_id: m.root_id,
    message_seq: m.seq,
    revision: m.revision,
    message: m,
  });
