/* ui/app/hub-v2.js — Hub v2 helpers: statuses, actors, task text, paused notices
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- hub v2 (docs/history/hub-v2.md)
// For the bots the keeper hosts (`host: keeper` in the registry) the record is hub.db, not GitHub:
// chat is `messages`, work is `tasks`, approvals are one tap, and every bot has one status row with
// its history. The dispatcher's bots keep the Issue path; every surface below branches on `host`,
// and every fetch is guarded, so a hub with no hub.db yet looks exactly as it does today.
const V2_WORD = {idle: 'Idle', running: 'Running', waiting_human: 'Waiting on a human',
                 waiting_bot: 'Waiting on a bot', blocked: 'Blocked', limited: 'Rate limited',
                 crashed: 'Crashed', paused: 'Paused', quarantined: 'Quarantined'};
// A bot the server has registered but not switched on yet. People read "Setting up"; the API value stays `planned`.
const statusWord = status => status === 'planned' ? 'Setting up' : String(status || '');
const V2_PILL = {running: 'in-progress', waiting_human: 'needs', waiting_bot: 'waiting', blocked: 'blocked',
                 limited: 'waiting', crashed: 'fail', quarantined: 'fail', idle: ''};
const V2_KIND = {decision: 'Decision', approval: 'Approval', review: 'Review', declined: 'Declined',
                 question: 'Question', waiting: 'Waiting', task: 'Task', notice: 'Message'};
const hostOf = slug => String(S.emps.find(e => e.name === slug)?.host || 'dispatcher');
const isKeeper = slug => hostOf(slug) === 'keeper';
const v2StatusOf = slug => S.v2.status[slug] || null;
const actorSlug = a => String(a || '').startsWith('bot:') ? String(a).slice(4) : '';
const actorPerson = a => String(a || '').startsWith('human:') ? String(a).slice(6) : '';
const titleCase = s => String(s || '').replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
// Display names are independent of persisted actor and host identifiers.
const personDisplay = id => {
  const p = (S.people || []).find(x => x.id === id);
  return (p && p.name) || titleCase(id);
};
const actorLabel = a => {
  const slug = actorSlug(a);
  if (slug) return botDisplayName(slug);
  const pid = actorPerson(a);
  if (pid) return pid === S.me?.id ? 'You' : personDisplay(pid);
  return a === 'keeper' ? assistantName() : String(a || '');
};
// Notices the hub writes for bots to read carry raw ids ("New task from human:sam"); people see names.
const plainActors = text => String(text ?? '').replace(/\b(human|bot):([A-Za-z0-9._-]*[A-Za-z0-9_])/g,
  (all, kind) => { const pid = actorPerson(all); return pid && pid === S.me?.id ? 'you' : actorLabel(all); });
const taskAddedBy = t => {
  const who = taskRequester(t);
  const pid = actorPerson(who);
  if (pid && pid === S.me?.id) return {kind: 'you', label: 'You'};
  if (pid) return {kind: 'human', label: personDisplay(pid)};
  if (actorSlug(who) || who === 'keeper') return {kind: 'bot', label: actorLabel(who)};
  return {kind: 'other', label: actorLabel(who) || 'Unknown'};
};
const taskSourceLine = t => {
  const src = taskAddedBy(t);
  if (src.kind === 'you') return 'Added by you';
  if (src.kind === 'bot') return 'Added by ' + src.label + ' (bot)';
  return 'Added by ' + src.label;
};
const taskRequester = t => t.requester === 'keeper' ? t.origin_actor || t.requester : t.requester;
// Hide the generated attachment trailer and rename the schedule footer; authored text stays intact.
const taskBody = t => {
  const raw = String(t.body || '');
  const body = (t.attachments || []).length ? raw.replace(/\n\nAttachments \(untrusted source material; authenticated downloads\):\n[^]*$/, '') : raw;
  if (t.requester !== 'keeper' || !actorSlug(t.owner) || !body.startsWith(`Run playbook ${t.title}.`)) return body;
  return body.replace(/\n\n_Created by the keeper from schedule `([^`\n]+)`\._$/, '\n\n_Created by ' + assistantName() + ' from schedule `$1`._');
};
const actorChip = a => {
  const slug = actorSlug(a);
  return slug && S.emps.some(e => e.name === slug)
    ? `<a class="chip" href="#/bot/${esc(slug)}">${avatar(slug, 16)}<span>${empName(slug)}</span></a>`
    : `<span class="chip">${personCircle(actorLabel(a), 16)}<span>${esc(actorLabel(a))}</span></span>`;
};
const v2Firstline = body => String(body || '').split('\n').map(s => s.trim()).find(Boolean) || '';
const myActor = () => S.me?.id ? `human:${S.me.id}` : '';
// The person on a chat bubble is the local part of their address: jo@example.com is "jo".
// Takes an actor (human:jo), an email, a roster id, or the Issue's "Jo"; '' when unknown.
const personHandle = x => (actorPerson(x) || String(x || '')).split('@')[0].trim().toLowerCase();
const ownerHandle = () => S.me?.owner_id || 'owner';           // unsigned Issue text is the owner's
const myHandle = () => personHandle(S.me?.id || S.me?.email) || ownerHandle();
const unsigned = body => String(body || '').replace(/\n*_?— [^\s_]+@[^\s_]+ via the hub_?\s*$/, '');
// every v2 read is optional: a hub without hub.db answers 404 and the caller falls back
async function v2Get(path) { try { return await get(path); } catch { return null; } }

async function v2Refresh() {
  const [st, needs] = await Promise.all([v2Get('/v2/status'), v2Get('/v2/needs-you')]);
  S.v2.on = !!st;
  if (st) S.v2.status = Object.fromEntries((st.bots || []).map(b => [b.bot, b]));
  // when Tico is updating itself, say so at the top of the sidebar
  if (st) { const el = $('#side-update'); if (el) el.hidden = !st.updating; }
  if (needs) S.v2.needs = needs.items || [];
}
// One line above the composer when the bot cannot answer right now: its status says so, or
// its Mac has been offline for ten minutes. Messages still save and run when it is back; this
// only says why nothing is happening yet. A stopped run holds only its own request while other
// work continues. Redrawn from the 30 s refresh, never its own poll.
const PAUSED_WHY = {limited: 'usage limit', quarantined: 'paused after refused actions; Resume it under More'};
function pausedNotice(slug) {
  const s = v2StatusOf(slug) || {};
  const when = since => since ? ` since ${new Date(since).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})}` : '';
  let why = PAUSED_WHY[s.state], since = s.since;
  if (s.state === 'paused' && /^Paused: over /.test(s.focus || '')) why = s.focus.replace(/^Paused: /, '');     // a spend limit (Usage)
  if (s.state === 'crashed')
    return `${empName(slug)} saved a stopped run for later${esc(when(since))} — only that request is paused; other work continues`;
  if (!why) {
    const off = (S.status?.health_issues || []).find(i => i.kind === 'machine' && (i.bots || [i.bot]).includes(slug) && i.since);
    if (off && Date.now() - Date.parse(off.since) > 10 * 60000) { why = off.title; since = off.since; }
  }
  if (!why) return '';
  return `${empName(slug)} is paused${esc(when(since))} — ${esc(why)}; your messages are saved and will run when it's back`;
}
// A bot run by an external agent (a Hermes profile) is never dispatched to: the line says the
// message waits for it, and whether the agent has been reporting in.
// The 30 s refresh reloads the health issues, not the roster, so the two warnings follow the
// issue the server raises and only the quiet line reads the roster's last-seen time.
function agentNotice(slug) {
  const e = S.emps.find(x => x.name === slug); if (!e?.agent) return '';
  const a = e.agent, name = agentKind(a);
  // A Grok Bot is synced in by its person's own Grok routine (docs/grok-bot-sync.md): its
  // history is copied here, but nothing written here reaches it yet.
  if (a.synced) return `${empName(slug)} is a Grok Bot synced from Grok${a.last_seen ? ` · last synced ${ago(a.last_seen)}` : ''} — its Grok history is copied here; messages sent here wait for it`;
  const issue = (S.status?.health_issues || []).find(i => i.kind === 'agent' && i.bot === slug);
  if (issue ? /no agent credential/.test(issue.title) : !a.credential)
    return `${empName(slug)} is a ${name} with no credential yet — messages wait until one is created in Settings and installed on its computer`;
  if (issue) return `${empName(slug)}'s ${name} has not reported in${issue.since ? ` since ${new Date(issue.since).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})}` : ''} — messages wait`;
  // No elapsed time here: the roster behind it is loaded once, and a fresh heartbeat would
  // not move it. Settings and More show last seen on a page that has just loaded it.
  return `${empName(slug)} is a ${name}; it reads its messages on its own schedule · reporting in`;
}
function agentQuiet(slug) {
  return !(S.status?.health_issues || []).some(i => i.kind === 'agent' && i.bot === slug) && !!S.emps.find(x => x.name === slug)?.agent?.credential;
}
function pausedRender() {
  const el = $('#conv-paused'); if (!el || !V2C) return;
  const text = pausedNotice(V2C.slug);
  const agent = text ? '' : agentNotice(V2C.slug);
  el.innerHTML = text || agent; el.hidden = !(text || agent);
  el.classList.toggle('agent', !text && !!agent && agentQuiet(V2C.slug));
}
function v2StatePill(slug) {
  const s = v2StatusOf(slug);
  if (!s) return botStatePill(slug);
  return `<span class="pill ${V2_PILL[s.state] ?? ''}">${esc(V2_WORD[s.state] || s.state || 'Unknown')}</span>${
    s.focus ? ` <span class="muted">${esc(s.focus)}</span>` : ''}`;
}
