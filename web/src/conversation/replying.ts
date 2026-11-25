// Whether an agent got the person's message and is answering it, the same way in every chat: one row per agent, from
// the moment the message reaches it to its reply. The thread's bot status (the server's `bot_status`, which bots the
// thread waits on and why) says it got the message and what holds it; the `runs` topic streams its reply as it is
// written; and a run that ends with no reply leaves one line that says so.
import { useQuery } from '@tanstack/react-query';
import { liveQuery, liveText, waitsQuery } from './queries';
import type { BotWait, LiveRun } from './types';

/** What the chat draws for one agent answering: its reply being written (or that it got the message, `doing: 'wait'`),
    a line that something stops it, or a line that its run ended with no reply. */
export type Replying =
  | { k: 'live'; key: string; r: LiveRun; text: string }
  | { k: 'stuck'; key: string; bot: string; name: string; detail: string }
  | { k: 'quiet'; key: string; bot: string; why: 'none' | 'failed' };

/** Which agents' answers a view of the thread draws: `bot`, and `message`, the person's message it answers when known. */
export type ShownIn = (bot: string, message: string | null | undefined) => boolean;

/** The server's word for a wait that has nothing to add: it is about to start. */
const STARTING = 'Starting';

/** A wait drawn as the row its run will take over: the agent's face, with what it is doing as far as the server says. */
function waitRun(w: BotWait): LiveRun {
  const base = { run: w.run_id ?? `wait:${w.actor}`, bot: w.actor, parts: [], typing: null, ended: false, ...(w.message_id && { message: w.message_id }) };
  if (w.state === 'working') return { ...base, doing: w.detail === 'Thinking' || !w.detail ? 'think' : 'work', label: w.detail };
  return { ...base, doing: 'wait', ...(w.detail && w.detail !== STARTING && { label: w.detail }) };
}

/**
 * The rows a chat draws for its agents answering, oldest first: each run being written; then each agent the thread
 * waits on whose run is not here yet (it got the message, or is at work from before the chat opened), or that something
 * stops; then, for an agent with neither, the line that its last run ended with no reply. One row per agent.
 */
export function replyingRows(live: readonly LiveRun[], waits: readonly BotWait[], shown: ShownIn = () => true): Replying[] {
  const out: Replying[] = [];
  const busy = new Set<string>();
  for (const r of live) {
    if (r.quiet || !shown(r.bot, r.message)) continue;
    busy.add(r.bot);
    out.push({ k: 'live', key: `live:${r.run}`, r, text: liveText(r) });
  }
  for (const w of waits) {
    if (busy.has(w.actor) || !shown(w.actor, w.message_id)) continue;
    busy.add(w.actor);
    if (w.state === 'cant_reply') out.push({ k: 'stuck', key: `stuck:${w.actor}`, bot: w.actor, name: w.name, detail: w.detail });
    else {
      const r = waitRun(w);
      out.push({ k: 'live', key: `live:${r.run}`, r, text: '' });
    }
  }
  for (const r of live.toReversed()) {
    if (!r.quiet || busy.has(r.bot) || !shown(r.bot, r.message)) continue;
    busy.add(r.bot);
    out.push({ k: 'quiet', key: `quiet:${r.run}`, bot: r.bot, why: r.quiet });
  }
  return out;
}

/** A thread's agents answering, as `replyingRows` draws them, for every chat that shows the thread. */
export function useReplying(id: string, shown?: ShownIn): Replying[] {
  const { data: live = [] } = useQuery(liveQuery(id));
  const { data: waits = [] } = useQuery(waitsQuery(id));
  return replyingRows(live, waits, shown);
}
