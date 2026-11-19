import { hashKey } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { CallRinging, NeedsYou, NeedsYouItem } from '@/api/schema';
import { keys } from '@/data/keys';
import { queryClient } from '@/data/query-client';
import type { Directory } from '@/data/directory';
import { mayNotify, notify } from '@/desktop/notify';
import { onTopic } from './patch';

/* System notifications for what the old app notified (ui/app/needs-you.ts, ui/app/call-ring.ts): a new ask of the
   person (an OK or a question, never an @: docs/dev/pipelines-v3.md) when they turned notifications on, and a call
   ringing them. Only while the page is out of sight, never for the person's own doing, at most one per item. */

/** The Needs-you kinds that ask the person something. */
const ASKS = new Set<NeedsYouItem['kind']>(['ask', 'question']);
/** Item ids remembered per person, shared with the old app's tabs (same key), so a reload or a second tab is quiet. */
const MEMORY = 200;
const memoryKey = (who: string) => 'tico.notified.' + who.slice(who.indexOf(':') + 1);

function recall(who: string): string[] {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(memoryKey(who)) || '[]');
    return Array.isArray(v) ? v.map(String) : [];
  } catch {
    return [];
  }
}
function remember(who: string, ids: readonly string[]): void {
  const held = recall(who);
  const next = [...new Set([...held, ...ids])].slice(-MEMORY);
  if (next.length === held.length) return;
  try {
    localStorage.setItem(memoryKey(who), JSON.stringify(next));
  } catch {
    /* a private window: notify without memory */
  }
}

const myId = () => (queryClient.getQueryData(keys.me()) as { id: string } | undefined)?.id;
const nameOf = (actor: string) => (queryClient.getQueryData(keys.directory()) as Directory | undefined)?.byId[actor]?.name;

/** The person's own switch (POST /api/v2/humans/{id} notify_browser), read from the list Settings reads and writes,
    so turning it there counts here at once. */
const humans = {
  queryKey: ['settings', 'humans'],
  queryFn: async ({ signal }: { signal: AbortSignal }) =>
    (await api.get<{ people?: { id: string; notify_browser?: boolean }[] }>('/api/humans', { signal })).people ?? [],
};
const turnedOn = async (who: string) =>
  (await queryClient.ensureQueryData(humans)).find((p) => p.id === who.slice(who.indexOf(':') + 1))?.notify_browser === true;

/** Where a click on an ask goes: its task in Flow, else its thread. */
function askAddress(it: NeedsYouItem): string {
  if (it.task) return `/flow/${it.task.number ?? encodeURIComponent(it.task.id)}`;
  const room = it.root_id || it.conversation_id;
  return room ? `/threads/${encodeURIComponent(room)}` : '/flow';
}
const ownAsk = (it: NeedsYouItem, who: string) => it.ask?.asked_by === who || it.from_id === who;

// ---------------------------------------------------------------- asks (the `needs` topic, `needs=changes`)

let seeded = false;
let queue = Promise.resolve();

async function onNeeds(batch: { changed?: string[] | null }[]): Promise<void> {
  const who = myId();
  const changed = [...new Set(batch.flatMap((e) => e.changed ?? []))];
  if (!who || !changed.length) return;
  // In front of the person, or not allowed: what moved is seen, never notified later.
  if (!mayNotify() || !(await turnedOn(who))) return remember(who, changed);
  let items: NeedsYouItem[];
  if (!seeded) {
    // This tab's first: what already waited is remembered, so only what moved now can notify.
    const all = await api.get<NeedsYou>('/api/v2/needs-you?limit=200');
    const list = 'items' in all ? all.items : [];
    remember(who, list.filter((it) => !changed.includes(it.id)).map((it) => it.id));
    items = list.filter((it) => changed.includes(it.id));
    seeded = true;
  } else {
    const got = await api.get<NeedsYou>(`/api/v2/needs-you?ids=${changed.map(encodeURIComponent).join(',')}`);
    items = 'items' in got ? got.items : [];
  }
  const known = new Set(recall(who));
  remember(who, changed);
  for (const it of items) {
    if (!ASKS.has(it.kind) || known.has(it.id) || ownAsk(it, who)) continue;
    await notify({ title: 'Needs you', body: it.line, tag: `need-${it.id}`, url: askAddress(it) });
  }
}

// ---------------------------------------------------------------- calls ringing (the ringing list the ring card reads)

const RINGING = hashKey(['calls', 'ringing']);
/** Calls heard ringing, with the notice shown for each (null: none, it rang while the page was in front). */
const rung = new Map<string, Notification | null>();

function onRinging(data: CallRinging | undefined): void {
  const who = myId();
  const now = new Set<string>();
  for (const c of data?.calls ?? []) {
    if (!c.ring || c.state !== 'live' || c.connector === who) continue;
    now.add(c.id);
    if (rung.has(c.id)) continue;
    rung.set(c.id, null);
    void notify({ title: nameOf(c.connector) || 'Call', body: c.title, tag: `call-${c.id}`, url: `/call/${encodeURIComponent(c.id)}` }).then(
      (n) => (rung.has(c.id) ? rung.set(c.id, n) : n?.close()),
    );
  }
  // A ring that stopped (answered, declined, ended) takes its notice away.
  for (const [id, n] of rung)
    if (!now.has(id)) {
      n?.close();
      rung.delete(id);
    }
}

// ---------------------------------------------------------------- install

let installed = false;
/** Once, as the stream starts. */
export function installNotices(): void {
  if (installed) return;
  installed = true;
  onTopic<{ changed?: string[] | null }>('needs', (batch) => {
    queue = queue.then(() => onNeeds(batch)).catch(() => undefined);
  });
  queryClient.getQueryCache().subscribe((e) => {
    if (e.type === 'updated' && e.action.type === 'success' && e.query.queryHash === RINGING) onRinging(e.query.state.data as CallRinging | undefined);
  });
}

/** For tests: forget this tab's state. */
export function resetNotices(): void {
  seeded = false;
  queue = Promise.resolve();
  rung.clear();
}

/** For tests: wait for the asks being handled. */
export const noticesSettled = () => queue;
