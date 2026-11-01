// A fake of the threads API (docs/dev/threads-contract.md) for scripts that open a bot's DM or Home on its page, where
// a bot's chat used to be. threadsFake({bot, messages, home}) returns answer(path, url, method, body): the JSON for a
// threads route, or undefined for any other path. `messages` and `home` are arrays or functions returning arrays, so a
// script can change them between reads; each message needs id, from_actor, body and created; the rest is filled in.
const iso = () => new Date().toISOString();

exports.threadsFake = ({bot, me = 'human:ana', messages = [], home = [], sent = []} = {}) => {
  const dmId = `t-dm-${bot}`, homeId = `t-home-${bot}`;
  const list = v => (typeof v === 'function' ? v() : v) || [];
  const seqd = (rows, tid) => list(rows).map((m, i) => ({seq: i + 1, depth: 0, thread_id: tid, created: iso(), ...m}));
  const view = (id, rows, extra) => ({id, title: '', last_seq: list(rows).length, last_unread_seq: 0, last_message_at: iso(),
    me: {member: true, following: true, notify: 'live', last_read_seq: list(rows).length, unread: 0, activity: false, can_post: true, can_manage: false},
    ...extra});
  const dm = () => view(dmId, messages, {audience: 'personal', members: [{actor: me}, {actor: 'bot:' + bot}]});
  const homeView = () => view(homeId, home, {audience: 'home', home: {actor: 'bot:' + bot}, members: []});
  return (p, url, method = 'GET', body = null) => {
    if (p === '/api/v2/threads' && method === 'GET') return {attention: [], threads: [], dms: [dm()], next_cursor: null};
    if (p === '/api/v2/threads/dm' && method === 'GET') return dm();
    if (p === '/api/v2/threads/dm' && method === 'POST') { sent.push(body); return dm(); }
    if (p === `/api/v2/threads/home/${encodeURIComponent('bot:' + bot)}` || p === `/api/v2/threads/home/bot:${bot}`) return homeView();
    if (p === `/api/v2/threads/${dmId}/timeline`) return {thread: dm(), breadcrumb: [], messages: seqd(messages, dmId), next_cursor: null};
    if (p === `/api/v2/threads/${homeId}/timeline`) return {thread: homeView(), breadcrumb: [], messages: seqd(home, homeId), next_cursor: null};
    if (/^\/api\/v2\/threads\/[^/]+\/calls$/.test(p)) return {calls: [], next_before: null};
    if (/^\/api\/v2\/threads\/[^/]+\/read$/.test(p)) return {ok: true};
    if (/^\/api\/v2\/threads\/[^/]+\/messages$/.test(p) && method === 'POST') {
      sent.push(body);
      return {message: {id: 'sent-' + sent.length, seq: 1000 + sent.length, depth: 0, thread_id: p.split('/')[4], from_actor: me,
        body: body?.text || '', created: iso()}};
    }
    if (p === '/api/v2/rule-holds' && method === 'GET') return {holds: [], next_cursor: null};
    return undefined;
  };
};
