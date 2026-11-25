// Whether an agent got the person's message and is answering it, the same way in every chat: one row per agent, from
// the moment the message reaches it to its reply. The thread's bot status (the server's `bot_status`: which bots the
// thread waits on, why, and the run each one's row is) decides which rows there are; the `runs` topic only writes the
// reply into the row whose run it names; and a run that took a person's message and ended with no reply leaves one line
// that says so. A reply is any message of the agent's after the one it answers, however it posted it.
import { useQuery } from '@tanstack/react-query';
import { liveQuery, liveText, waitsQuery } from './queries';
import type { BotWait, ChatMessage, LiveRun } from './types';

/** What the chat draws for one agent answering: its reply being written (or that it got the message, `doing: 'wait'`),
    a line that something stops it, or a line that its run ended with no reply. */
export type Answering =
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

/** Whether `bot` answered: a message of its after `message` in this view, or the reply its run `run` posted. */
function answered(rows: readonly ChatMessage[], bot: string, message: string | null | undefined, run?: string | null): boolean {
  const from = message ? rows.findIndex((m) => m.id === message) : -1;
  return rows.some((m, i) => (from >= 0 && i > from && m.sender_id === bot) || (!!run && m.refs?.turn_id === run));
}

/**
 * The rows a chat draws for its agents answering, one per agent: each row the server's bot status lists (`waits`),
 * with its run's reply as written so far when the stream has it; then, for an agent with none, its run that just ended
 * while its reply comes, or the line that it ended with no reply to a person's message here. A run the bot status does
 * not name is never drawn as under way: it ended unseen, or is not this view's.
 */
export function replyingRows(live: readonly LiveRun[], waits: readonly BotWait[], rows: readonly ChatMessage[] = [], shown: ShownIn = () => true): Answering[] {
  const out: Answering[] = [];
  const busy = new Set<string>();
  for (const w of waits) {
    if (busy.has(w.actor) || !shown(w.actor, w.message_id) || answered(rows, w.actor, w.message_id, w.run_id)) continue;
    busy.add(w.actor);
    if (w.state === 'cant_reply') {
      out.push({ k: 'stuck', key: `stuck:${w.actor}`, bot: w.actor, name: w.name, detail: w.detail });
      continue;
    }
    const r = (w.run_id && live.find((x) => x.run === w.run_id && !x.quiet)) || waitRun(w);
    out.push({ k: 'live', key: `live:${r.run}`, r, text: liveText(r) });
  }
  for (const r of live.toReversed()) {
    if (!r.ended || busy.has(r.bot) || !shown(r.bot, r.message) || answered(rows, r.bot, r.message, r.run)) continue;
    const text = liveText(r);
    if (!r.quiet && text) {
      busy.add(r.bot);
      out.push({ k: 'live', key: `live:${r.run}`, r, text });
    } else if (r.quiet && rows.some((m) => m.id === r.message && m.sender_id?.startsWith('human:'))) {
      busy.add(r.bot);
      out.push({ k: 'quiet', key: `quiet:${r.run}`, bot: r.bot, why: r.quiet });
    }
  }
  return out;
}

/** A thread's agents answering, as `replyingRows` draws them from `rows` (the view's messages), for every chat. */
export function useReplying(id: string, rows: readonly ChatMessage[], shown?: ShownIn): Answering[] {
  const { data: live = [] } = useQuery(liveQuery(id));
  const { data: waits = [] } = useQuery(waitsQuery(id));
  return replyingRows(live, waits, rows, shown);
}
