// Whether an agent got the person's message and is answering it, the same way in every chat: one row per agent, from
// the moment the message reaches it to its reply. The thread's bot status (the server's `bot_status`,
// backend/thread_bot_status.py) says what became of each message that woke an agent, and the chat draws exactly that:
// the `runs` topic only writes the reply into the row whose run the status names.
import { useQuery } from '@tanstack/react-query';
import { liveQuery, liveText, waitsQuery } from './queries';
import type { BotWait, LiveRun } from './types';

/** What the chat draws for one agent answering: its reply being written (or that it got the message, `doing: 'wait'`),
    a line that something stops it, or a line that its run ended with no reply. */
export type Answering =
  | { k: 'live'; key: string; r: LiveRun; text: string }
  | { k: 'stuck'; key: string; bot: string; name: string; detail: string }
  | { k: 'quiet'; key: string; bot: string; why: 'none' | 'failed' };

/** Which agents' answers a view of the thread draws: `bot`, and `message`, the person's message it answers. */
export type ShownIn = (bot: string, message: string) => boolean;

/** The server's word for a wait that has nothing to add: it is about to start. */
const STARTING = 'Starting';

/** A row drawn before its run's reply streams: the agent's face, with what it is doing as far as the server says. */
function waitRun(w: BotWait): LiveRun {
  const base = { run: w.run_id ?? `wait:${w.actor}`, bot: w.actor, parts: [], typing: null, ended: false };
  if (w.state === 'working') return { ...base, doing: w.detail === 'Thinking' || !w.detail ? 'think' : 'work', label: w.detail };
  return { ...base, doing: 'wait', ...(w.detail && w.detail !== STARTING && { label: w.detail }) };
}

/**
 * The rows a chat draws for its agents answering: the server's rows (`waits`) as they are, the first this view shows per
 * agent. A `replied` row draws nothing (its reply is in the chat); a `working` one draws its run's reply as written so
 * far when the stream has that run.
 */
export function replyingRows(live: readonly LiveRun[], waits: readonly BotWait[], shown: ShownIn = () => true): Answering[] {
  const out: Answering[] = [];
  const done = new Set<string>();
  for (const w of waits) {
    if (done.has(w.actor) || !shown(w.actor, w.message_id)) continue;
    done.add(w.actor);
    if (w.state === 'replied') continue;
    if (w.state === 'cant_reply') out.push({ k: 'stuck', key: `stuck:${w.actor}`, bot: w.actor, name: w.name, detail: w.detail });
    else if (w.state === 'no_reply' || w.state === 'stopped')
      out.push({ k: 'quiet', key: `quiet:${w.actor}:${w.message_id}`, bot: w.actor, why: w.state === 'no_reply' ? 'none' : 'failed' });
    else {
      const r = (w.state === 'working' && w.run_id && live.find((x) => x.run === w.run_id)) || waitRun(w);
      out.push({ k: 'live', key: `live:${r.run}`, r, text: liveText(r) });
    }
  }
  return out;
}

/** A thread's agents answering, as `replyingRows` draws them, for every chat. */
export function useReplying(id: string, shown?: ShownIn): Answering[] {
  const { data: live = [] } = useQuery(liveQuery(id));
  const { data: waits } = useQuery(waitsQuery(id));
  return replyingRows(live, waits?.bots ?? [], shown);
}
