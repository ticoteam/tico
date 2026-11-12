// A fake of the threads API (docs/dev/threads-contract.md) for scripts that open a bot's DM or Home on its page, where
// a bot's chat used to be. threadsFake({bot, messages, home}) returns answer(path, url, method, body): the JSON for a
// threads route, or undefined for any other path. `messages` and `home` are arrays or functions returning arrays, so a
// script can change them between reads; each message needs id and body (sender_id, created_at, kind, refs and reactions are
// optional); the helper fills in seq, depth, thread_id and created_at.
const iso = (): string => new Date().toISOString();

export interface ThreadsMessage {
  id: string;
  from_actor?: string;
  body: string;
  created?: string;
  [key: string]: unknown;
}
// What a spec passes as a message: the fields a fixture sets. The helper fills in the rest (seq, depth, thread_id).
export interface ThreadsReaction {
  emoji: string;
  count: number;
  actors: string[];
}
export interface ThreadsFixture {
  id: string;
  body: string;
  sender_id?: string;
  created_at?: string;
  kind?: string;
  refs?: Record<string, unknown>;
  reactions?: ThreadsReaction[];
}
export type ThreadsFixtureRows = ThreadsFixture[] | (() => ThreadsFixture[]);
// What a script posts to a threads route: the text of a message, or the fields of a new DM.
export interface ThreadsPost {
  text?: string;
  [key: string]: unknown;
}
export interface ThreadsFakeOptions {
  bot: string;
  me?: string;
  messages?: ThreadsFixtureRows;
  home?: ThreadsFixtureRows;
  sent?: ThreadsPost[];
}
// The JSON for a threads route, or undefined for any other path.
export type ThreadsAnswer = (path: string, url: string | URL, method?: string, body?: unknown) => unknown;

function threadsFake({bot, me = 'human:ana', messages = [], home = [], sent = []}: ThreadsFakeOptions = {} as ThreadsFakeOptions): ThreadsAnswer {
  const dmId = `t-dm-${bot}`, homeId = `t-home-${bot}`;
  const list = (v: ThreadsFixtureRows): ThreadsFixture[] => (typeof v === 'function' ? v() : v) || [];
  const seqd = (rows: ThreadsFixtureRows, tid: string): Record<string, unknown>[] => list(rows).map((m, i) => ({seq: i + 1, depth: 0, thread_id: tid, created_at: iso(), ...m}));
  const view = (id: string, rows: ThreadsFixtureRows, extra: Record<string, unknown>): Record<string, unknown> => ({id, title: '', last_seq: list(rows).length, last_unread_seq: 0, last_message_at: iso(),
    me: {member: true, following: true, notify: 'live', last_read_seq: list(rows).length, unread: 0, activity: false, can_post: true, can_manage: false},
    ...extra});
  const dm = (): Record<string, unknown> => view(dmId, messages, {audience: 'personal', members: [{actor: me}, {actor: 'bot:' + bot}]});
  const homeView = (): Record<string, unknown> => view(homeId, home, {audience: 'home', home: {actor: 'bot:' + bot}, members: []});
  return (p: string, url: string | URL, method = 'GET', body: unknown = null): unknown => {
    if (p === '/api/v2/threads' && method === 'GET') return {attention: [], threads: [], dms: [dm()], next_cursor: null};
    if (p === '/api/v2/threads/dm' && method === 'GET') return dm();
    if (p === '/api/v2/threads/dm' && method === 'POST') { sent.push(body as ThreadsPost); return dm(); }
    if (p === `/api/v2/threads/home/${encodeURIComponent('bot:' + bot)}` || p === `/api/v2/threads/home/bot:${bot}`) return homeView();
    if (p === `/api/v2/threads/${dmId}/timeline`) return {thread: dm(), breadcrumb: [], messages: seqd(messages, dmId), next_cursor: null};
    if (p === `/api/v2/threads/${homeId}/timeline`) return {thread: homeView(), breadcrumb: [], messages: seqd(home, homeId), next_cursor: null};
    if (/^\/api\/v2\/threads\/[^/]+\/calls$/.test(p)) return {calls: [], next_before: null};
    if (/^\/api\/v2\/threads\/[^/]+\/read$/.test(p)) return {ok: true};
    if (/^\/api\/v2\/threads\/[^/]+\/messages$/.test(p) && method === 'POST') {
      const post = body as ThreadsPost | null;
      sent.push(post as ThreadsPost);
      return {message: {id: 'sent-' + sent.length, seq: 1000 + sent.length, depth: 0, thread_id: p.split('/')[4],
        sender_id: me, body: post?.text || '', created_at: iso(), ...(post?.refs ? {refs: post.refs} : {})}};
    }
    if (p === '/api/v2/rule-holds' && method === 'GET') return {holds: [], next_cursor: null};
    return undefined;
  };
}
const api = {threadsFake};
export type Api = typeof api;
module.exports = api;
