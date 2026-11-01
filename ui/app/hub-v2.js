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
                 crashed: 'Crashed', paused: 'Paused', quarantined: 'Paused for review'};
// A bot the server has registered but not switched on yet. People read "Setting up"; the API value stays `planned`.
const statusWord = status => status === 'planned' ? 'Setting up' : String(status || '');
const V2_PILL = {running: 'in-progress', waiting_human: 'needs', waiting_bot: 'waiting', blocked: 'blocked',
                 limited: 'waiting', crashed: 'fail', quarantined: 'fail', idle: ''};
const V2_KIND = {decision: 'Decision', approval: 'Approval', review: 'Review', declined: 'Declined',
                 question: 'Question', task: 'Task', notice: 'Message', setup: 'Set up'};
const hostOf = slug => String(botRow(slug)?.host || 'dispatcher');
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
const taskRequester = t => t.requester_id === 'keeper' ? t.origin_actor || t.requester_id : t.requester_id;
// Hide the generated attachment trailer and rename the schedule footer; authored text stays intact.
const taskBody = t => {
  const raw = String(t.body || '');
  const body = (t.attachments || []).length ? raw.replace(/\n\nAttachments \(untrusted source material; authenticated downloads\):\n[^]*$/, '') : raw;
  if (t.requester_id !== 'keeper' || !actorSlug(t.owner_id) || !body.startsWith(`Run playbook ${t.title}.`)) return body;
  return body.replace(/\n\n_Created by the keeper from schedule `([^`\n]+)`\._$/, '\n\n_Created by ' + assistantName() + ' from schedule `$1`._');
};
const actorChip = a => {
  const slug = actorSlug(a);
  return slug && botRow(slug)
    ? `<a class="chip" href="#/bot/${encodeURIComponent(slug)}">${avatar(slug, 16)}<span>${empName(slug)}</span></a>`
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
async function v2Get(path, opts) { try { return await get(path, opts); } catch { return null; } }

async function v2Refresh() {
  const [st, needs] = await Promise.all([v2Get('/v2/status'), v2NeedsRead()]);
  S.v2.on = !!st;
  if (st) S.v2.status = Object.fromEntries((st.bots || []).map(b => [b.bot, b]));
  // when Tico is updating itself, say so at the top of the sidebar
  if (st) { const el = $('#side-update'); if (el) el.hidden = !st.updating; }
  if (needs && typeof OVERVIEW_PAGE !== 'undefined' && OVERVIEW_PAGE) overviewNeedsDraw();
  if (needs && !needs.unchanged && typeof BOT !== 'undefined' && BOT && isKeeper(BOT.slug)) botNeedsChanged(BOT.slug);
  if (needs && !needs.unchanged && typeof DEC !== 'undefined' && DEC) decideRefresh(DEC, S.v2.needs || []);   // the open Needs you card
  if (needs && !needs.unchanged && typeof askPeek === 'function') askPeek();   // a peek beside Ask's dot
}
// Needs you as the page holds it: the first items of the list, a page at a time (GET /v2/needs-you?limit=), in the
// server's order, with its version, its whole count, each bot's count over the whole list (`bots`) and where the next
// page starts (`needsNext`, null once it is all here). A read names the version held; an unchanged list comes back
// without its items. Each read is its own request: one already on its way may have been answered before the change
// this read was asked for (an answer sent, a live event), and sharing it would keep the old list. An older read that
// lands after a newer one is not kept. A server without pages sends the whole list, and there is no next page.
const NEEDS_PAGE = 50, NEEDS_PAGE_MAX = 200;
// Where the next page starts in the list at `version`: the server's cursor (backend/views.py needs_cursor).
const needsCursor = (version, n) => `${version}.${n}`;
const needsAll = () => !S.v2.needsNext;
// An item past the pages held, read alone (`ids=`) for a task's question box; forgotten whenever the list moves.
const NEEDS_ONE = new Map();     // id -> {read, it}
function v2NeedsOne(id) {
  const key = String(id);
  if (!NEEDS_ONE.has(key)) {
    const one = {it: null};
    one.read = v2Get(`/v2/needs-you?ids=${encodeURIComponent(key)}`).then(r => (one.it = (r?.items || [])[0] || null));
    NEEDS_ONE.set(key, one);
  }
  return NEEDS_ONE.get(key).read;
}
const needsOneHeld = id => NEEDS_ONE.get(String(id))?.it || null;
function needsTake(needs) {
  NEEDS_ONE.clear();
  S.v2.needs = needs.items || []; S.v2.needsVersion = needs.version || null;
  S.v2.needsCount = needs.count ?? S.v2.needs.length; S.v2.needsNext = needs.next || null; S.v2.needsBots = needs.bots || null;
}
let NEEDS_READ = 0, NEEDS_KEPT = 0;
async function v2NeedsRead() {
  const held = S.v2.needsVersion, seq = ++NEEDS_READ;
  // As many as are held, so a list that moved does not drop the pages already read.
  const limit = Math.min(NEEDS_PAGE_MAX, Math.max(NEEDS_PAGE, (S.v2.needs || []).length));
  const needs = await v2Get(`/v2/needs-you?limit=${limit}` + (held ? `&version=${encodeURIComponent(held)}` : ''), {fresh: true});
  if (!needs) return null;
  if (seq < NEEDS_KEPT) return needs;
  NEEDS_KEPT = seq;
  if (!needs.unchanged) needsTake(needs);
  S.v2.wentAway = needs.went_away || null;
  return needs;
}
// The next page, one read at a time. Resolves true when the list grew (or was read again because it moved).
let NEEDS_MORE = null;
function v2NeedsMore() {
  if (needsAll()) return Promise.resolve(false);
  return NEEDS_MORE ||= (async () => {
    try {
      const cursor = S.v2.needsNext;
      const r = await v2Get(`/v2/needs-you?limit=${NEEDS_PAGE}&cursor=${encodeURIComponent(cursor)}`, {fresh: true});
      if (!r || S.v2.needsNext !== cursor) return false;        // a read or a live event moved the list meanwhile
      if (r.restart || r.version !== S.v2.needsVersion) { await v2NeedsRead(); return true; }
      S.v2.needs = [...S.v2.needs, ...(r.items || [])];
      S.v2.needsCount = r.count ?? S.v2.needs.length; S.v2.needsNext = r.next || null; S.v2.needsBots = r.bots || null;
      return true;
    } finally { NEEDS_MORE = null; }
  })();
}
// A live `needs` event (live.js asks for changes, not the list): mend the held list from the ids that changed, or read
// it whole when the event was counted from another version than the one held. One at a time, in order. Resolves true
// when the list may have moved. A changed item whose place falls past the pages held stays unread: only the count
// moves, and the next page starts after what is held.
let NEEDS_MEND = Promise.resolve(false);
const v2NeedsChanged = d => (NEEDS_MEND = NEEDS_MEND.catch(() => false).then(() => v2NeedsMend(d)));
async function v2NeedsMend(d) {
  if (d.went_away) S.v2.wentAway = d.went_away;
  if (Array.isArray(d.items)) { needsTake({...d, version: null, next: null}); return true; }   // a server without changes
  const held = S.v2.needsVersion;
  if (!d.version || d.version === held) return false;
  const whole = async () => { await v2NeedsRead(); return true; };
  if (!held || d.base !== held || !Array.isArray(d.changed) || !Array.isArray(d.removed)) return whole();
  const all = needsAll(), out = new Set([...d.changed, ...d.removed].map(String));
  const items = (S.v2.needs || []).filter(it => !out.has(String(it.id)));
  if (d.changed.length) {
    const r = await v2Get(`/v2/needs-you?ids=${encodeURIComponent(d.changed.join(','))}`);
    if (!r || r.version !== d.version) return whole();
    // Unchanged items kept their order against each other, so each changed one goes back at its place, first place
    // first. Past the end of the pages held, the rest wait for their page.
    for (const it of (r.items || []).slice().sort((a, b) => r.positions[String(a.id)] - r.positions[String(b.id)])) {
      const at = r.positions[String(it.id)];
      if (!all && at > items.length) break;
      items.splice(at, 0, it);
    }
  }
  if (all ? items.length !== d.count : items.length > d.count) return whole();
  NEEDS_ONE.clear();
  S.v2.needs = items; S.v2.needsVersion = d.version; S.v2.needsCount = d.count;
  S.v2.needsNext = all || items.length >= d.count ? null : needsCursor(d.version, items.length);
  if (d.bots) S.v2.needsBots = d.bots;
  return true;
}
// One line above the composer when the bot cannot answer right now: its status says so, or
// its Mac has been offline for ten minutes. Messages still save and run when it is back; this
// only says why nothing is happening yet. A stopped run holds only its own request while other
// work continues. Redrawn from the 30 s refresh, never its own poll.
const PAUSED_WHY = {limited: 'usage limit'};
const clockTime = at => new Date(at).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
// Why a quarantined bot is paused, in plain words. An escape says what the safety check matched in the
// bot's own words, never that something was read or edited: the check reads only the text it refused.
function quarantineWhy(slug) {
  const q = v2StatusOf(slug)?.quarantine || {}, r = q.review || {};
  if (q.why) return q.why;
  if (q.auto) return `Quarantined for repeated refused writes (rule 8): resumes by itself ${q.resumes_at ? `at ${new Date(q.resumes_at).toISOString().slice(0, 16).replace('T', ' ')} UTC` : 'after its cooldown'}; a person who manages it can clear it sooner.`;
  const found = r.found === 'secrets/' ? 'a secrets/ path' : r.found ? `another bot's folder (${r.found})` : "a secrets path or another bot's folder";
  const what = r.what ? `${/^[aeiou]/i.test(r.what) ? 'an' : 'a'} ${r.what}` : 'repeated writes';
  return `Quarantined until a person clears it. The safety check stopped ${what} that named ${found}. It matched text only; nothing was sent or changed.`;
}
const QUARANTINE_RESUME_NOTE = 'Resume releases this pause; the checks stay on.';
async function quarantineResume(slug) {
  await post(`/v2/bots/${encodeURIComponent(slug)}/quarantine/clear`, {});
  await v2Refresh();
  toast(`${empName(slug)} resumed`);
}
// Try now: a person who renewed the plan clears the usage-limit cooldown for this bot and the others
// limited on the same runtime and computer (backend/execution.py `clear_limits`).
async function limitRetry(slug) {
  await post(`/v2/bots/${encodeURIComponent(slug)}/limit/retry`, {});
  await v2Refresh();
  toast(`${empName(slug)} will try again now`);
}
const limitRetryButton = (slug, cls = 'linkish') => v2StatusOf(slug)?.state === 'limited' && settingsCanManageBot(botRow(slug))
  ? ` <button type="button" class="${cls}" data-limit-retry>Try now</button>` : '';
// Release: Tico stopped the bot (backend/bot_stops.py: five fast failed runs, or over its run budget) and a person
// who manages it lets it run again (POST /v2/bots/{bot}/release, which clears both). One click, wherever it shows.
async function botRelease(slug) {
  await post(`/v2/bots/${encodeURIComponent(slug)}/release`, {});
  await v2Refresh();
  toast(`${empName(slug)} released`);
}
const botMayRelease = slug => settingsCanManageBot(botRow(slug));
const botReleaseButton = (slug, cls = 'linkish') => botMayRelease(slug)
  ? ` <button type="button" class="${cls}" data-bot-release="${esc(slug)}">Release</button>` : '';
// Every Release button (the chat line, the bot page, Health) goes through one listener; each page redraws its own.
document.addEventListener('click', async ev => {
  const button = ev.target.closest?.('[data-bot-release]');
  if (!button || button.disabled) return;
  button.disabled = true;
  try {
    await botRelease(button.dataset.botRelease);
    if (typeof pausedRender === 'function') pausedRender();
    if (typeof botAlertDraw === 'function' && $('#bot-alert')) botAlertDraw();
    if (button.closest('#hl-page')) window.hlRefresh?.();
  } catch (error) { button.disabled = false; toast(error.message, true); }
});
function pausedNotice(slug) {
  const s = v2StatusOf(slug) || {};
  // Tico's own words for the stop, as the server sends them: what happened and what to check.
  if (s.stopped) return `${esc(s.focus || `Tico stopped ${empName(slug)}`)}${botReleaseButton(slug)}`;
  if (s.backoff) return esc(s.backoff);
  const when = since => since ? ` since ${clockTime(since)}` : '';
  if (s.bot_state === 'quarantined') {
    // A refusal-count quarantine lifts itself after an hour; anything else waits for a person (backend/hubdb.py).
    const q = s.quarantine || {}, e = botRow(slug);
    const resume = e && settingsCanManageBot(e) ? ' <button type="button" class="linkish" data-quarantine-resume>Resume bot</button>' : '';
    const at = q.since || s.since, paused = (at ? ` paused at ${clockTime(at)}` : ' is paused') + (q.auto ? '' : ' for review');
    const who = q.auto || resume ? '' : ' A person who manages it can resume it.';
    return `${empName(slug)}${esc(paused)}. ${esc(quarantineWhy(slug) + who)} Your messages are saved and run when it's back.${resume}`;
  }
  let why = PAUSED_WHY[s.state], since = s.since;
  if (s.state === 'paused' && /^Paused: over /.test(s.focus || '')) why = s.focus.replace(/^Paused: /, '');     // a spend limit (Usage)
  if (s.state === 'crashed')
    return s.last_stop ? `${empName(slug)}: ${esc(s.last_stop.text)}`
      : `${empName(slug)} saved a stopped run for later${esc(when(since))} — only that request is paused; other work continues`;
  if (!why) {
    const off = (S.status?.health_issues || []).find(i => i.kind === 'machine' && (i.bots || [i.bot]).includes(slug) && i.since);
    if (off && Date.now() - Date.parse(off.since) > 10 * 60000) { why = off.title; since = off.since; }
  }
  if (!why) return '';
  return `${empName(slug)} is paused${esc(when(since))} — ${esc(why)}; your messages will run when it's back${limitRetryButton(slug)}`;
}
// A bot run by an external agent (a Hermes profile) is never dispatched to: the line says the
// message waits for it, and whether the agent has been reporting in.
// The 30 s refresh reloads the health issues, not the roster, so the two warnings follow the
// issue the server raises and only the quiet line reads the roster's last-seen time.
function agentNotice(slug) {
  const e = botRow(slug); if (!e?.agent) return '';
  const a = e.agent, name = agentKind(a);
  // A Grok Bot or Dots is synced in by the agent itself (docs/external-agent-sync.md): its history is copied
  // here, and what its person writes here goes to it with the next sync.
  if (a.synced) return `${empName(slug)} syncs from ${a.label || 'its platform'}${a.last_seen_at ? ` · last synced ${ago(a.last_seen_at)}` : ''} — your messages reach it at the next sync`;
  const issue = (S.status?.health_issues || []).find(i => i.kind === 'agent' && i.bot === slug);
  if (issue ? /no agent credential/.test(issue.title) : !a.credential)
    return `${empName(slug)} is a ${name} with no credential yet — messages wait until one is created in Settings and installed on its computer`;
  if (issue) return `${empName(slug)}'s ${name} has not reported in${issue.since ? ` since ${new Date(issue.since).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})}` : ''} — messages wait`;
  // No elapsed time here: the roster behind it is loaded once, and a fresh heartbeat would
  // not move it. Settings and More show last seen on a page that has just loaded it.
  return `${empName(slug)} is a ${name}; it reads its messages on its own schedule · reporting in`;
}
function agentQuiet(slug) {
  return !(S.status?.health_issues || []).some(i => i.kind === 'agent' && i.bot === slug) && !!botRow(slug)?.agent?.credential;
}
function pausedRender() {
  const el = $('#conv-paused'); if (!el || !V2C) return;
  const text = pausedNotice(V2C.slug);
  const agent = text ? '' : agentNotice(V2C.slug);
  patchLiveHTML(el, text || agent); el.hidden = !(text || agent);
  el.classList.toggle('agent', !text && !!agent && agentQuiet(V2C.slug));
  const resume = el.querySelector('[data-quarantine-resume]'), slug = V2C.slug;
  if (resume) resume.onclick = async () => {
    resume.disabled = true;
    try {
      await quarantineResume(slug);
      pausedRender();
      if (typeof botAlertDraw === 'function') botAlertDraw();
    } catch (error) { resume.disabled = false; toast(error.message, true); }
  };
  const retry = el.querySelector('[data-limit-retry]');
  if (retry) retry.onclick = async () => {
    retry.disabled = true;
    try {
      await limitRetry(slug);
      pausedRender();
      if (typeof botAlertDraw === 'function') botAlertDraw();
    } catch (error) { retry.disabled = false; toast(error.message, true); }
  };
}
// A bot over its spend limit (backend/usage_limits.py `over`): its status carries the limit while it holds new work.
const overLimit = slug => v2StatusOf(slug)?.limit || null;
// A hold by the team's or a group's limit names it: raising the bot's own limit would not lift it.
const overLimitWord = limit => limit.scope && limit.scope !== 'bot' ? `Waiting: ${limit.name} is over its ${limit.period} limit` : `Over its ${limit.period} limit`;
function v2StatePill(slug) {
  const s = v2StatusOf(slug);
  if (!s) return botStatePill(slug);
  // A back-off keeps the bot's state; its wait is the line beside it until the next try.
  const line = s.backoff || s.focus;
  return `<span class="pill ${V2_PILL[s.state] ?? ''}">${esc(s.stopped ? 'Stopped' : V2_WORD[s.state] || s.state || 'Unknown')}</span>${
    line ? ` <span class="muted">${esc(line)}</span>` : ''}`;
}
