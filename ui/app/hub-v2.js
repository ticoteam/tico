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
// What a sender, "added by" or "by" slot says for the keeper: it happened automatically.
const KEEPER_LABEL = 'Automatic';
const actorLabel = a => {
  const slug = actorSlug(a);
  if (slug) return botDisplayName(slug);
  const pid = actorPerson(a);
  if (pid) return pid === S.me?.id ? 'You' : personDisplay(pid);
  // The keeper is the server itself, never the Assistant or the app: a row it wrote is labelled by its source (rowSource).
  return a === 'keeper' ? KEEPER_LABEL : String(a || '');
};
// Notices the hub writes for bots to read carry raw ids ("New task from human:sam"); people see names.
const plainActors = text => String(text ?? '').replace(/\b(human|bot):([A-Za-z0-9._-]*[A-Za-z0-9_])/g,
  (all, kind) => { const pid = actorPerson(all); return pid && pid === S.me?.id ? 'you' : actorLabel(all); });
const taskAddedBy = t => {
  const who = taskRequester(t);
  const pid = actorPerson(who);
  if (pid && pid === S.me?.id) return {kind: 'you', label: 'You'};
  if (pid) return {kind: 'human', label: personDisplay(pid)};
  if (actorSlug(who)) return {kind: 'bot', label: actorLabel(who)};
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
  return body.replace(/\n\n_Created by the keeper from schedule `([^`\n]+)`\._$/, '\n\n_Created from schedule `$1`._');
};

// ---- who or what wrote a row: `source` on message, task history and event rows (docs/threads.md, "Who wrote it")
// The server sends {kind, name, link}; kind is person | bot | assistant_for | task_update | builtin | automation | system.
// A row from a server without it gets the same answer here (backend/sources.py), from its sender and refs (a history
// row's `data`, an event's `detail`), so every surface draws alike.
// Only an `assistant_for` row ever shows the Assistant's name or face; the gray kinds have no avatar or sender row.
const SOURCE_BUILTINS = {'follow-up-timer': 'Follow-up timer', 'earlier-messages': 'Earlier messages', 'task-roundup': 'Task roundup', 'stuck-check': 'Stuck check',
  'rule-check': 'Rule check', 'check-health': 'Health check', maintenance: 'Maintenance'};
const SOURCE_LINES = new Set(['task_update', 'builtin', 'automation', 'system']);
// "Chris's Assistant": the person's first name and the install's name for the Assistant.
const assistantForName = person => {
  const first = String(person || '').trim().split(/\s+/)[0];
  return first ? `${first}'s ${assistantName()}` : assistantName();
};
function rowSource(m, by = m?.sender_id) {
  if (m?.source && typeof m.source === 'object' && m.source.kind) return m.source;
  const refs = m?.refs || m?.data || m?.detail || {}, who = String(by || ''), src = typeof refs.source === 'string' ? refs.source : '';
  if (actorPerson(who)) return {kind: 'person', name: actorLabel(who), link: null};
  // A bot (the Assistant) acting for a person, or a keeper line the Assistant wrote for one. The server writes the
  // person's words it passes on as the keeper with refs.carried, and names the person first ("Ana asked … in their words").
  const forName = () => actorPerson(refs.for) ? personDisplay(actorPerson(refs.for)) : (String(m?.body || '').match(/^(.+?) asked /) || [])[1];
  if (src === 'assistant-for' || actorSlug(who) && actorPerson(refs.for)) return {kind: 'assistant_for', name: assistantForName(forName()), link: null};
  if (actorSlug(who)) return {kind: 'bot', name: actorLabel(who), link: null};
  if (who !== 'keeper') return {kind: who ? 'bot' : 'system', name: who ? actorLabel(who) : '', link: null};
  if (src.startsWith('automation:')) return {kind: 'automation', name: 'Automation', link: AUTOMATION_HREF(src.slice(11))};
  if (SOURCE_BUILTINS[src]) return {kind: 'builtin', name: SOURCE_BUILTINS[src], link: null};
  if (src.startsWith('system:')) return {kind: 'system', name: '', link: null};
  if (refs.carried) return {kind: 'assistant_for', name: assistantForName(forName()), link: null};
  if (refs.task && m?.refs) return {kind: 'task_update', name: 'Task update', link: null};     // a message, never a history row
  return {kind: 'system', name: '', link: null};
}
const AUTOMATION_HREF = id => id ? '#/automations/' + encodeURIComponent(id) : '#/automations';
// Whether a message draws as a gray line. One that asks something of a person, or carries a card, keeps its own row
// whoever wrote it: its controls are never folded away.
const sourceCarriesCard = m => { const r = m?.refs || {};
  return m?.kind === 'ask' || !!(r.action || r.credential_request || r.approval || r.questions || r.chat_goal || r.read_back || r.update || r.card || r.reconnect); };
const isSourceLine = (m, by) => SOURCE_LINES.has(rowSource(m, by).kind) && !sourceCarriesCard(m);
// Plain words for a gray line: names for actor ids, markdown marks and link targets dropped.
const sourcePlain = text => plainActors(String(text ?? '').replace(/^New task from keeper: /, 'New task: ')).replace(/!\[[^\]]*\]\([^)]*\)/g, '').replace(/\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/[*_`#>~|]/g, '').trim();
// The name a gray line leads with: bold for a built-in, a link for an automation, nothing for a task update or system.
function sourceLeadHTML(src) {
  if (!src.name || !['builtin', 'automation'].includes(src.kind)) return '';
  return src.kind === 'automation' && src.link
    ? `<a class="src-name" href="${esc(src.link)}">${esc(src.name)}</a> ` : `<b class="src-name">${esc(src.name)}</b> `;
}
// One gray line for a row no person or bot wrote. `cls` and `attrs` let each surface keep its own row hooks; the
// hover says what made it and when, and the whole text when the line may be cut.
function sourceLineHTML(m, {src = rowSource(m), at = m.created_at, text = m.body, cls = '', attrs = '', tail = ''} = {}) {
  const words = sourcePlain(text || ''), first = words.split('\n').map(s => s.trim()).find(Boolean) || '';
  const tip = [src.name, at ? fmt(at) : ''].filter(Boolean).join(' · ') + (words.length > first.length || first.length > 60 ? '\n\n' + words.slice(0, 600) : '');
  const when = at ? `<time class="src-time" data-ago="${esc(at)}">${esc(ago(at))}</time>` : '';
  return `<div class="src-line src-${esc(src.kind)}${cls ? ' ' + cls : ''}"${m.id ? ` data-message="${esc(m.id)}"` : ''}${attrs} title="${esc(tip)}">${
    sourceLeadHTML(src)}<span class="src-text">${esc(first)}</span>${when}${tail}</div>`;
}
// Consecutive task updates (two or more) are one gray "N task updates" row, shut by default; open shows a line each.
// Every other row is drawn by `draw`. `open(run)` says whether a group is open, so a redraw keeps it: the surface keeps
// the ids of the rows in groups opened (sourceGroupToggle), nothing is stored, and a group stays open when updates join
// it at either end, live or from an older page.
function sourceRunsHTML(items, {line, draw, open = () => false, isUpdate = it => rowSource(it).kind === 'task_update'}) {
  const out = [];
  for (let i = 0; i < items.length;) {
    if (!isUpdate(items[i])) { out.push(draw(items[i], i)); i++; continue; }
    let end = i;
    while (end < items.length && isUpdate(items[end])) end++;
    const run = items.slice(i, end);
    if (run.length === 1) out.push(line(run[0]));
    else {
      const key = String(run[0].id || run[0].message?.id || ''), last = run.at(-1), at = last.created_at || last.ts || last.message?.created_at;
      out.push(`<details class="src-group" data-src-group="${esc(key)}"${open(run) ? ' open' : ''}><summary class="src-line src-task_update">${
        run.length} task updates<span class="src-chev" aria-hidden="true">▸</span>${at ? `<time class="src-time" data-ago="${esc(at)}" title="${esc(fmt(at))}">${esc(ago(at))}</time>` : ''}</summary>${
        run.map(line).join('')}</details>`);
    }
    i = end;
  }
  return out.join('');
}
// A group opened or shut (its `toggle`): the ids of its rows join or leave `set`.
function sourceGroupToggle(set, el) {
  if (!set || !el?.matches?.('details.src-group')) return;
  for (const row of el.querySelectorAll('[data-message]')) set[el.open ? 'add' : 'delete'](row.dataset.message);
}
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
  if (needs && !needs.unchanged && typeof notifyNewThreadNeeds === 'function') notifyNewThreadNeeds(S.v2.needs || []);
}
// Needs you as the page holds it: a bounded page at a time (GET /v2/needs-you?limit=), in the server's order, with
// its version, whole attention count and plate count, each bot's count over the whole list (`bots`) and where the next
// page starts (`needsNext`, null once it is all here), plus counts by kind (`by_kind`) and ids of tasks folded into
// other cards (`folded_task_ids`) over the whole list.
// A read names the version held; an unchanged list comes back
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
// A question to the person is delivered once their Needs you holds it, and read once they open its card or its task
// (POST /v2/needs-you/seen, /v2/tasks/{id}/seen): a question withdrawn later tells its bot whether they had seen it.
// Delivery is sent once per question per page load; one that fails is sent again with the next list.
const ASKS_SENT = {seen: new Set(), read: new Set()};
const askToMe = a => a?.id && actorPerson(a.recipient_id || '') === S.me?.id;
function needsSeen(items) {
  const ids = (items || []).map(it => it.ask).filter(a => askToMe(a) && !a.delivered_at && !ASKS_SENT.seen.has(a.id))
    .map(a => a.id).slice(0, 200);
  if (!ids.length) return;
  ids.forEach(id => ASKS_SENT.seen.add(id));
  post('/v2/needs-you/seen', {asks: ids}).catch(() => ids.forEach(id => ASKS_SENT.seen.delete(id)));
}
// Reads a task's open questions to the person (the server marks each open one). A card that shows one question sends
// once per page load for that question (askId), so repaints stay quiet; opening the full task always sends, because a
// question asked after an earlier open is read only by a request made then.
function taskAsksRead(id, askId) {
  const key = String(id);
  if (!key) return;
  if (askId) {
    if (ASKS_SENT.read.has('ask:' + askId)) return;
    ASKS_SENT.read.add('ask:' + askId);
  }
  post(`/v2/tasks/${encodeURIComponent(key)}/seen`, {}).catch(() => { if (askId) ASKS_SENT.read.delete('ask:' + askId); });
}
// The first list held: a page drawn before it (a task opened from a link) fills its question box then. A read that
// fails holds no list, so the wait goes on to the next one that answers.
let needsFirstDone = null;
const NEEDS_FIRST = new Promise(done => { needsFirstDone = done; });
const needsFirstIn = () => !needsFirstDone;
function needsTake(needs) {
  NEEDS_ONE.clear();
  S.v2.needs = needs.items || []; S.v2.needsVersion = needs.version || null;
  for (let at = 0; at < S.v2.needs.length; at += 200) needsSeen(S.v2.needs.slice(at, at + 200));
  S.v2.needsCount = needs.count ?? S.v2.needs.length; S.v2.needsNext = needs.next || null; S.v2.needsBots = needs.bots || null;
  S.v2.needsPlateCount = needs.plate_count ?? null;
  S.v2.needsByKind = needs.by_kind || null;
  S.v2.needsFoldedIds = needs.folded_task_ids || null;
  needsListsRedraw();
  if (needsFirstDone) { const done = needsFirstDone; needsFirstDone = null; done(); }
}
// Reads are ordered by their request sequence; only paging and live mends invalidate reads already on their way.
let NEEDS_READ = 0, NEEDS_KEPT = 0, NEEDS_REVISION = 0;
async function v2NeedsRead() {
  const held = S.v2.needsVersion, seq = ++NEEDS_READ, revision = NEEDS_REVISION;
  // As many as are held, so a list that moved does not drop the pages already read.
  const limit = Math.min(NEEDS_PAGE_MAX, Math.max(NEEDS_PAGE, (S.v2.needs || []).length));
  const needs = await v2Get(`/v2/needs-you?limit=${limit}` + (held ? `&version=${encodeURIComponent(held)}` : ''), {fresh: true});
  if (!needs) return null;
  if (seq < NEEDS_KEPT || revision !== NEEDS_REVISION) return null;
  NEEDS_KEPT = seq;
  if (!needs.unchanged) needsTake(needs);
  else if (needs.plate_count != null) { S.v2.needsPlateCount = needs.plate_count; needsListsRedraw(); }
  S.v2.wentAway = needs.went_away || null;
  return needs;
}
// The next page, one read at a time. Resolves true when the list grew (or was read again because it moved).
let NEEDS_MORE = null;
function v2NeedsMore() {
  if (needsAll()) return Promise.resolve(false);
  return NEEDS_MORE ||= (async () => {
    try {
      const cursor = S.v2.needsNext, read = NEEDS_READ;
      const r = await v2Get(`/v2/needs-you?limit=${NEEDS_PAGE}&cursor=${encodeURIComponent(cursor)}`, {fresh: true});
      // A newer read owns the next snapshot even before it lands; this older page must not invalidate it.
      if (!r || read !== NEEDS_READ || S.v2.needsNext !== cursor) return false;
      if (r.restart || r.version !== S.v2.needsVersion) { await v2NeedsRead(); return true; }
      S.v2.needs = [...S.v2.needs, ...(r.items || [])];
      needsSeen(r.items);
      S.v2.needsCount = r.count ?? S.v2.needs.length; S.v2.needsNext = r.next || null; S.v2.needsBots = r.bots || null;
      S.v2.needsPlateCount = r.plate_count ?? null;
      S.v2.needsByKind = r.by_kind || null;
      S.v2.needsFoldedIds = r.folded_task_ids || null;
      NEEDS_REVISION++;
      needsListsRedraw();
      return true;
    } finally { NEEDS_MORE = null; }
  })();
}
// A live `needs` event (live.js asks for changes, not the list): mend the held list from the ids that changed, or read
// it whole when the event was counted from another version than the one held. One at a time, in order. Resolves true
// when the list may have moved. A changed item whose place falls past the pages held stays unread: only the count
// moves, and the next page starts after what is held. Missing folded ids keep the held set; null reads it whole.
let NEEDS_MEND = Promise.resolve(false);
const v2NeedsChanged = d => (NEEDS_MEND = NEEDS_MEND.catch(() => false).then(() => v2NeedsMend(d)));
async function v2NeedsMend(d) {
  if (d.went_away) S.v2.wentAway = d.went_away;
  if (Array.isArray(d.items)) {       // a server without changes
    NEEDS_REVISION++;
    needsTake({...d, version: null, next: null});
    return true;
  }
  const held = S.v2.needsVersion;
  if (!d.version || d.version === held) return false;
  const whole = async () => { await v2NeedsRead(); return true; };
  if (!held || d.base !== held || !Array.isArray(d.changed) || !Array.isArray(d.removed) || d.folded_task_ids === null) return whole();
  const all = needsAll(), out = new Set([...d.changed, ...d.removed].map(String));
  const items = (S.v2.needs || []).filter(it => !out.has(String(it.id)));
  let plateCount = d.plate_count;
  if (d.changed.length) {
    const r = await v2Get(`/v2/needs-you?ids=${encodeURIComponent(d.changed.join(','))}`);
    if (!r || r.version !== d.version) return whole();
    plateCount = r.plate_count ?? plateCount;
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
  S.v2.needsPlateCount = plateCount ?? null;
  needsSeen(items);
  S.v2.needsNext = all || items.length >= d.count ? null : needsCursor(d.version, items.length);
  if (d.bots) S.v2.needsBots = d.bots;
  S.v2.needsByKind = d.by_kind || null;
  if (Array.isArray(d.folded_task_ids)) S.v2.needsFoldedIds = d.folded_task_ids;
  NEEDS_REVISION++;
  needsListsRedraw();
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
    const at = q.since || s.changed_at, paused = (at ? ` paused at ${clockTime(at)}` : ' is paused') + (q.auto ? '' : ' for review');
    const who = q.auto || resume ? '' : ' A person who manages it can resume it.';
    return `${empName(slug)}${esc(paused)}. ${esc(quarantineWhy(slug) + who)} Your messages are saved and run when it's back.${resume}`;
  }
  let why = PAUSED_WHY[s.state], since = s.changed_at;
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
  // The Assistant's chat (V2C), or a bot page, whose Home and DM are threads (ui/app/bot-page.js).
  const el = $('#conv-paused'), slug = V2C?.slug || (typeof BOT !== 'undefined' && BOT?.slug); if (!el || !slug) return;
  const text = pausedNotice(slug);
  const agent = text ? '' : agentNotice(slug);
  patchLiveHTML(el, text || agent); el.hidden = !(text || agent);
  el.classList.toggle('agent', !text && !!agent && agentQuiet(slug));
  const resume = el.querySelector('[data-quarantine-resume]');
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
