/* ui/app/needs-you.js — Needs you: its groups, its rows outside any task, approvals' exact payloads, stale tickets
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ---- Needs you: hub.db items, rendered as the same request rows the Issues use ----
// What a card takes, in the order Needs you lists the groups (the server sets each card's `need`, backend/views.py
// NEED_GROUPS): all the yes/no and pick cards can be cleared in one sitting.
const NEED_GROUPS = [['decide', 'Answer'], ['pay', 'Pay'], ['connect', 'Sign in or connect'], ['review', 'Review and send'],
  ['hands', 'Hands needed'], ['do', 'Do']];
const NEED_RANK = Object.fromEntries(NEED_GROUPS.map(([k], i) => [k, i]));
const needLabel = need => (NEED_GROUPS.find(([k]) => k === need) || NEED_GROUPS.at(-1))[1];
let NEEDS_MEMO = null;
function needsIndex() {
  const list = S.v2?.needs || [];
  if (NEEDS_MEMO?.list !== list) {
    const byId = new Map(list.map((it, i) => [String(it.id), {it, i}]));
    // A bot's task folded into the card that asks the person the same thing is not listed again, nor a task the
    // Strategy Bot merged into an item of its own (backend/strategy_merge.py): the server leaves both out of Needs you.
    const folded = new Set(list.flatMap(it => [...(it.folded || []).map(f => String(f.id)), ...(it.merged || []).map(String)]));
    NEEDS_MEMO = {list, byId, folded};
  }
  return NEEDS_MEMO;
}
const needsItem = id => needsIndex().byId.get(String(id))?.it || null;
const needsFolded = id => needsIndex().folded.has(String(id));
// The next page of Needs you (hub-v2.js v2NeedsMore), then everything drawn from it. A list that holds only some pages
// ends in Show more; on the Tasks page it also loads as it scrolls into view.
const needsMoreButton = () => needsAll() ? '' : '<button class="ghost tl-more" type="button" data-needs-page>Show more</button>';
async function needsMoreShow() {
  if (!await v2NeedsMore()) return;
  liveEmit('needs-list', S.v2.needs || []);
  renderTree();
  if (OVERVIEW_PAGE) overviewNeedsDraw();
  if (TASKS_ST && isTasksRoute(S.route)) tasksRender(TASKS_ST);
  if (BOT && isKeeper(BOT.slug)) botNeedsChanged(BOT.slug);
}
const NEEDS_IO = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
  if (entries.some(e => e.isIntersecting)) void needsMoreShow();
}, {rootMargin: '400px'}) : null;
function needsMoreWatch(root) {
  NEEDS_IO?.disconnect();
  const button = root?.querySelector('[data-needs-page]');
  if (button) NEEDS_IO?.observe(button);
}
document.addEventListener('click', ev => {
  const button = ev.target.closest('[data-needs-page]');
  if (!button) return;
  button.disabled = true;
  void needsMoreShow().finally(() => { button.disabled = false; });
});
// "Do what you recommend": one tap answers the bot's ask with what it said it would do, and the card goes. It sends
// the version of the task the card showed, so a task that changed meanwhile is refused, not answered unseen.
const recommendButton = (rec, cls = 'primary', version = null) => rec?.ask
  ? `<button type="button" class="${cls}" data-recommend="${esc(rec.ask)}"${version ? ` data-version="${esc(String(version))}"` : ''} title="${esc(rec.do)}">Do what you recommend</button>` : '';
const recommendLine = (rec, from) => rec?.do
  ? `<div class="req-context need-rec"><span>${esc(actorLabel(from) || 'The bot')} recommends</span> ${esc(plainActors(rec.do))}</div>` : '';
// The cards folded into this one: each bot's task and what it asks, so nothing it asks is hidden.
const foldedHTML = it => (it.folded || []).length ? `<div class="req-next need-folded"><span>Also waiting on this</span><ul>${it.folded.map(f =>
  `<li><a href="#/task/${encodeURIComponent(f.id)}">${esc(plainActors(f.title || ''))}</a>${f.person_ask || f.note ? ` <span class="muted">${esc(plainActors(f.person_ask || f.note))}</span>` : ''}</li>`).join('')}</ul></div>` : '';
async function needsRecommend(button, ask, version) {
  const label = button.textContent;
  button.disabled = true; button.textContent = '…';
  try {
    const done = await post('/v2/needs-you/recommended', {ask, ...(version ? {version} : {})});
    toast(`Done: ${done.did}`);
    button.closest('.req, .tl-row')?.remove();
    await v2Refresh();
    if (TASKS_ST && isTasksRoute(S.route)) { await tasksLoad(TASKS_ST); }
    personTasksReload();
  } catch (e) { toast(e.message, true); button.disabled = false; button.textContent = label; }
}
// One line in Needs you for an item outside any task (an approval, a question, the Stale tickets card): who asks, what,
// and its kind. A click opens its card (decide.js); nothing unfolds in the list. Its key is the card's. It keeps the
// task rows' columns (task-list.js taskListRowHTML), with nothing to select and no status.
// The thread items (backend/thread_attention.py): each names its kind, and opens its thread rather than a decide card.
const THREAD_NEED_KINDS = {thread_ask: 'Question', thread_mention: 'Mention', thread_dm: 'Direct message',
                           thread_held: 'Held', thread_capped: 'Paused'};
function needRowHTML(it, ctx = {}) {
  const key = decideItemKey(it), stale = it.kind === 'stale', thread = THREAD_NEED_KINDS[it.kind];
  const from = stale ? '' : taskRequester(it) || it.requester || '';
  const name = stale ? 'Tico' : actorLabel(from) || 'Tico';
  const title = stale ? it.title || 'Stale tickets' : approvalTitle(it) || it.first_line || it.title || v2Firstline(it.body) || V2_KIND[it.kind] || 'Item';
  const kind = it.kind === 'approval' ? 'Approval' : stale ? 'Stale check' : it.kind === 'memory_tidy' ? 'Nightly memory tidy' : thread || 'Question';
  const face = stale ? personCircle('Tico', 18) : actorFace(from, 18);
  const open = thread && it.conversation_id
    ? `<a class="tl-open" href="#/thread/${encodeURIComponent(it.conversation_id)}" tabindex="-1"><span class="tl-title"><b class="req-from">${esc(name)}:</b> ${esc(plainActors(title))}</span></a>`
    : it.kind === 'thread_held'
      ? `<button type="button" class="tl-open" data-hold-review="${esc(it.id)}" tabindex="-1"><span class="tl-title"><b class="req-from">${esc(name)}:</b> ${esc(plainActors(title))}</span></button>`
    : `<button type="button" class="tl-open" data-open-task="n${esc(key)}" tabindex="-1" aria-haspopup="dialog"><span class="tl-title"><b class="req-from">${esc(name)}:</b> ${esc(plainActors(title))}</span></button>`;
  return tasksSigned(`<div class="tl-row tl-need" data-task-key="n${esc(key)}" data-need-card="${esc(key)}" role="listitem" aria-level="1" style="--depth:0">
    <span class="tl-check-pad${ctx.select ? ' tl-check-room' : ''}"></span><span class="tl-chev-pad"></span>
    ${open}
    <span class="tl-fill"></span>
    <span class="tl-chips"><span class="pill needs">${esc(kind)}</span>${stale && (it.rows || []).length ? `<span class="tl-folded tnum">${(it.rows || []).length}</span>` : ''}</span>
    ${ctx.by === 'status' ? '' : '<span class="tl-status-pad"></span>'}
    <span class="tl-people"><span class="tl-face" title="${esc(name)}">${face}</span></span>
    <time class="tl-age tnum" datetime="${esc(it.ready_since || it.created || '')}" title="${esc(fmt(it.ready_since || it.created))}">${esc(ageShort(it.ready_since || it.created))}</time>
  </div>`);
}
// The items Needs you lists outside any task, without the ones just answered on a card (decide.js decideGone).
const needsExtras = () => (S.v2?.needs || []).filter(it => ['approval', 'stale', 'ask', 'memory_tidy', ...Object.keys(THREAD_NEED_KINDS)].includes(it.kind) && !decideGone(decideItemKey(it)));
// How long an approval's exact payload has been ready to decide, in seconds (until its decision once decided), and whose
// decision it is: "Ready 2 h 10 min, waiting on Chris" (backend/hubdb.py approval_wait). Counted here from `ready_since`
// so a page left open stays right.
function waitWords(s) {
  const m = Math.floor(Math.max(0, +s || 0) / 60), d = Math.floor(m / 1440), h = Math.floor(m % 1440 / 60), r = m % 60;
  if (d) return h ? `${d} d ${h} h` : `${d} d`;
  if (h) return r ? `${h} h ${r} min` : `${h} h`;
  return `${r} min`;
}
function approvalReadyAge(it) {
  const at = Date.parse(it?.ready_since || '');
  if (!Number.isFinite(at)) return null;
  const end = it.decided_at ? Date.parse(it.decided_at) : Date.now();
  return Math.max(0, ((Number.isFinite(end) ? end : Date.now()) - at) / 1000);
}
// The age as its own element, marked with what it counts from: a card the list keeps across redraws (its patch key)
// keeps this node too, so approvalAgesTick updates its text where it stands.
function approvalAgeNode(it) {
  const age = approvalReadyAge(it);
  if (age == null) return null;
  const span = document.createElement('span');
  span.className = 'approval-age';
  span.dataset.readySince = it.ready_since;
  if (it.decided_at) span.dataset.decidedAt = it.decided_at;
  span.textContent = waitWords(age);
  return span;
}
function approvalReadyLine(it) {
  const age = approvalAgeNode(it);
  if (!age) return null;
  const owner = it.decision_owner || '';
  const who = !owner ? '' : actorPerson(owner) && actorPerson(owner) === S.me?.id ? 'you'
    : it.decision_owner_name || actorLabel(owner);
  const line = document.createElement('p');
  line.className = 'approval-ready';
  if (it.decision) line.append('Decided ', age, ' after it was ready');
  else line.append('Ready ', age, who ? `, waiting on ${who}` : '');
  return line;
}
// Every age on screen (Needs you's rows and cards, an idle Decide card) moves on with the clock, without redrawing
// the card: open details, a draft and focus stay as they are.
function approvalAgesTick() {
  for (const node of document.querySelectorAll('.approval-age[data-ready-since]')) {
    const age = approvalReadyAge({ready_since: node.dataset.readySince, decided_at: node.dataset.decidedAt || ''});
    const text = age == null ? '' : waitWords(age);
    if (node.textContent !== text) node.textContent = text;
  }
}
setInterval(() => { if (!document.hidden) approvalAgesTick(); }, 30000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) approvalAgesTick(); });
// What an approval asks for, built as elements with every payload value set as text; the markup the list
// redraws from is this element's serialization, so a value is never parsed as HTML. A send shows its
// recipients, subject, draft and files readably; a publish where it goes and its full text; a spend or merge
// its fields. Each keeps the exact payload folded underneath; any other kind shows the raw payload, wrapped.
const APPROVAL_FILE_ID = /^[A-Za-z0-9-]{8,80}$/;   // the ids the file viewer opens (fileLinkId)
function approvalFileIcon() {
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg'), path = document.createElementNS(ns, 'path');
  for (const [k, v] of Object.entries({viewBox: '0 0 14 14', width: '13', height: '13', class: 'approval-file-icon', 'aria-hidden': 'true', focusable: 'false'})) svg.setAttribute(k, v);
  for (const [k, v] of Object.entries({d: 'M3.5 1.5h4.5l2.5 2.5v8.5h-7z M8 1.5V4h2.5', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.3', 'stroke-linejoin': 'round'})) path.setAttribute(k, v);
  svg.append(path);
  return svg;
}
// Where a publish goes: a Support ticket by its number, a web address as a link, anything else as text.
function approvalWhere(url) {
  const u = String(url ?? '').trim(), ticket = /^hq-ticket:(TK-[A-Za-z0-9]+)$/.exec(u);
  if (ticket) return 'Support ticket ' + ticket[1];
  if (/^https?:\/\//i.test(u)) {
    try {
      const href = new URL(u);
      if (href.protocol === 'http:' || href.protocol === 'https:') {
        const a = document.createElement('a');
        a.href = href.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.textContent = u;
        return a;
      }
    } catch { /* not a web address: shown as text */ }
  }
  return u;
}
// The readable rows for a publish, spend or merge: the fields APPROVAL_FIELDS requires by name, then one row per other
// key (labelled with the key; the content hash stays in the exact payload). A string or number shows as is, anything
// else as its JSON. A row is [label, value, class, keep]; a named row with no value is left out, a `keep` row never is.
// A support report (a bot's Tico bug report) shows where it goes (the host the server names in `it.support.to`), what
// goes with it, and its whole message. Null for any other kind.
// The redesign's approvals (CONTRACT §5) name teammates by id; a card names them as people read them.
const approvalWho = ref => {
  const r = String(ref || '');
  return r.startsWith('person:') ? personDisplay(r.slice(7)) : r.includes(':') ? actorLabel(r) : r ? botDisplayName(r) : '';
};
const approvalUsd = v => v == null || v === '' ? '' : '$' + Number(v).toFixed(2).replace(/\.00$/, '');
function approvalTitle(it) {
  const p = it?.payload && typeof it.payload === 'object' ? it.payload : {};
  if (it?.kind !== 'approval') return it?.title || '';
  if (it.what === 'task_cap' && p.sender) return `Task cap: ${approvalWho(p.sender)} → ${approvalWho(p.receiver)}`;
  if (it.what === 'spend_limit') return `Spend limit: ${p.pool ? String(p.pool) : approvalWho(p.bot)}`;
  return it.title || '';
}
function approvalFacts(what, p, it = {}) {
  if (!p || typeof p !== 'object' || Array.isArray(p)) return null;
  const shown = v => v == null ? '' : typeof v === 'string' || typeof v === 'number' ? String(v) : JSON.stringify(v);
  const plain = v => typeof v === 'string' || typeof v === 'number';
  let rows, named;
  if (what === 'publish') {
    const text = p.text == null || (typeof p.text === 'string' && !p.text.trim()) ? null : p.text;
    rows = [['Where', typeof p.url === 'string' ? approvalWhere(p.url) : shown(p.url)],
      text == null ? ['Text', 'not included; only a hash of it is in the payload', 'muted', true]
                   : ['Text', shown(text), 'approval-text']];
    named = ['url', 'text', 'content_sha256'];
  } else if (what === 'spend') {
    rows = [['Amount', shown(p.amount)], ['Account', shown(p.account)], ['For', shown(p.what)]];
    named = ['amount', 'account', 'what'];
  } else if (what === 'support') {
    const host = String(it?.support?.to ?? '').trim();
    rows = [['To', host ? `Tico support (${host})` : 'Tico support'],
      ['With it', p.include_ids === false ? 'Nothing else: only the message' : "The Tico version and this install's ID"],
      ['Message', shown(p.message), 'approval-text', true]];
    named = ['message', 'include_ids'];
  } else if (what === 'task_cap') {
    rows = [['From', approvalWho(p.sender)], ['To', approvalWho(p.receiver)], ['Per day', p.per_day == null ? 'No cap' : shown(p.per_day)]];
    named = ['sender', 'receiver', 'per_day'];
  } else if (what === 'spend_limit') {
    rows = [['For', p.pool ? shown(p.pool) : approvalWho(p.bot)], ['30 min', approvalUsd(p.per_30m_usd)], ['Day', approvalUsd(p.daily_usd)],
      ['Week', approvalUsd(p.weekly_usd)], ['Month', approvalUsd(p.monthly_usd)]];
    named = ['bot', 'pool', 'per_30m_usd', 'daily_usd', 'weekly_usd', 'monthly_usd'];
  } else if (what === 'finance') {
    // A Finance action waiting on this person (backend/finance/actions.py): its summary, and for a budget change that
    // needs room above it, which budget is raised and to what (backend/finance/handlers.py _raise_note).
    const text = String(p.what ?? '').replace(/\s*\(Finance action [^)]*\)\s*$/, '');
    const m = /^(.*?) and raise (.+?)(?: to (-?[\d.,]+ [A-Z]{3}))? from (\d{4}-\d{2}-\d{2})$/.exec(text);
    rows = [['Change', m ? m[1] : text, 'approval-text']];
    if (m) rows.push(['Raises', `${m[2]}${m[3] ? ' to ' + m[3] : ''} from ${m[4]}`, 'approval-text']);
    named = ['what', 'source', 'action_id', 'kind', 'payload'];
  } else if (what === 'merge') {
    rows = [['Repository', shown(p.repo)], ['Pull request', plain(p.pr) && p.pr !== '' ? '#' + String(p.pr).replace(/^#/, '') : shown(p.pr)]];
    named = ['repo', 'pr'];
  } else return null;
  for (const key of Object.keys(p)) if (!named.includes(key) && key !== 'content_sha256') rows.push([key, plain(p[key]) ? String(p[key]) : JSON.stringify(p[key]), '', true]);
  return rows;
}
function approvalPayloadNode(it) {
  const p = it.payload && typeof it.payload === 'object' ? it.payload : {}, el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = String(text);
    return node;
  };
  const box = el('div', 'req-next approval-payload'), main = el('div', 'md');
  box.append(el('span', '', 'Exactly this'), main);
  const ready = approvalReadyLine(it);
  if (ready) main.append(ready);
  const raw = el('pre', '', JSON.stringify(p, null, 2));
  if (it.what !== 'send') {
    const rows = approvalFacts(it.what, p, it);
    if (!rows) { main.append(raw); return box; }
    const facts = el('dl', 'approval-facts');
    for (const [label, value, cls, keep] of rows) {
      if (!keep && (value == null || value === '')) continue;
      const dd = el('dd', cls || '');
      if (value instanceof Node) dd.append(value); else dd.textContent = String(value);
      facts.append(el('dt', '', label), dd);
    }
    const details = el('details', 'approval-raw');
    details.append(el('summary', '', 'Exact payload'), raw);
    main.append(facts, details);
    return box;
  }
  const list = v => (Array.isArray(v) ? v : v == null || v === '' ? [] : [v]).map(String).join(', ');
  const facts = el('dl', 'approval-facts');
  for (const [label, value] of [['From', p.mailbox], ['To', list(p.to)], ['Cc', list(p.cc)],
                                ['Subject', p.subject], ['Draft', p.draft]]) {
    if (value == null || value === '') continue;
    facts.append(el('dt', '', label), el('dd', label === 'Draft' ? 'mono' : '', value));
  }
  main.append(facts);
  const files = Array.isArray(p.attachments) ? p.attachments.filter(f => f && typeof f === 'object') : [];
  if (files.length) {
    const ul = el('ul', 'approval-files');
    ul.setAttribute('aria-label', 'Attached files');
    const verified = Array.isArray(it.attachment_files) ? it.attachment_files : [];
    files.forEach((f, i) => {
      const li = el('li'), name = String(f.name ?? 'file'), size = Number(f.size), hash = String(f.sha256 ?? '');
      li.append(approvalFileIcon());
      // A link only to the version the server found with the approved sha256 and size; the payload's own
      // file_id is the requester's word and never becomes a link.
      const ok = verified[i], id = String(ok?.file_id ?? ''), version = Number(ok?.version);
      if (APPROVAL_FILE_ID.test(id) && Number.isInteger(version) && version > 0) {
        const a = el('a', 'approval-file-name', name);
        a.href = `/api/v2/files/${encodeURIComponent(id)}?v=${version}`;
        a.dataset.name = name;
        li.append(a);
      } else li.append(el('span', 'approval-file-name', name));
      if (Number.isFinite(size) && size >= 0) li.append(el('span', 'approval-file-size', bytes(size)));
      if (hash) {
        const code = el('code', 'approval-file-hash', hash.slice(0, 12));
        code.title = 'sha256 ' + hash;
        li.append(code);
      }
      if (f.file_id != null && f.file_id !== '' && !li.querySelector('a'))
        li.append(el('span', 'approval-file-warn', "Not openable: the file on the task doesn't match the approved file"));
      ul.append(li);
    });
    main.append(ul);
  }
  const details = el('details', 'approval-raw');
  details.append(el('summary', '', 'Details'), raw);
  main.append(details);
  return box;
}
// The person's one Stale tickets card (backend/stale_review.py): a row per task the hourly check found stale, each with
// its reason and suggestion, Accept or Skip, and Accept all, answered on its card (decide.js decideStaleHTML). Accept
// is the person's own change, refused as by hand, and names the task version the row showed; Accept all names the rows
// on screen, so a row added since is not applied.
const staleCards = () => (S.v2?.needs || []).filter(it => it.kind === 'stale');
// Each row keeps its key while the card's rows change, so a redraw takes an answered row out and a new one in.
const staleSig = v => esc(JSON.stringify(v));
function staleCardRow(r) {
  const accept = r.action ? `<button type="button" class="ghost" data-stale="${esc(r.id)}" data-stale-decision="accept">Accept</button>` : '';
  return `<li class="stale-row" data-patch-key="stale-row:${esc(r.id)}" data-patch-sig="${staleSig([r.task, r.title, r.reason, r.suggest, r.action, r.version])}" data-stale-row="${esc(r.id)}" data-stale-version="${esc(String(r.version ?? ''))}"${r.action ? ' data-stale-action' : ''}>
    <div class="stale-what"><a href="#/task/${encodeURIComponent(r.task)}">${esc(plainActors(r.title || ''))}</a>
      <div class="muted">${esc(r.reason)}. Suggest: ${esc(r.suggest)}.</div></div>
    <div class="row">${accept}<button type="button" class="ghost" data-stale="${esc(r.id)}" data-stale-decision="skip">Skip</button></div>
  </li>`;
}
// After the answer the card is drawn again from the new list (the next card once no row is left). A button still on
// screen (a refused row) is ready again, and when the answered row took the focus with it, focus moves to the row now
// in its place.
async function staleDecide(button, path, body) {
  const host = button.closest('[data-stale-card]') || document.querySelector(`[data-stale-card="${CSS.escape(button.dataset.staleAll || '')}"]`);
  const card = host?.dataset.staleCard || '', label = button.textContent;
  const at = [...(host?.querySelectorAll('[data-stale-row]') || [])].indexOf(button.closest('[data-stale-row]'));
  button.disabled = true; button.textContent = '…';
  try {
    const out = await post(path, body);
    const refused = out.refused || [];
    if (refused.length) needsSay(`${refused.length} not changed: ${refused[0].reason}`, true);
    else needsSay(out.accepted ? `Accepted ${out.accepted.length}` : out.outcome === 'skipped' ? 'Skipped' : 'Accepted');
    await v2Refresh(); await refresh(true);      // the open card follows (hub-v2.js v2Refresh)
    needsListsRedraw();
  } catch (e) { needsSay(e.message, true); }
  finally {
    if (button.isConnected) { button.disabled = false; button.textContent = label; }
    else if (card && (!document.activeElement || document.activeElement === document.body || !document.activeElement.isConnected)) {
      const again = document.querySelector(`[data-stale-card="${CSS.escape(card)}"]`);
      const rows = again?.querySelectorAll('[data-stale-row]') || [];
      (rows[Math.min(Math.max(at, 0), rows.length - 1)]?.querySelector('[data-stale]') || $('#need-card [data-dc="skip"]'))?.focus({preventScroll: true});
    }
  }
}
// Filtered for you: what the ask filter kept off your plate today (backend/ask_ingest.py), folded at the foot of Needs
// you. In Rehearsal it lists what On would have done and changes nothing; with the filter On, Let it through puts an
// item on your plate in one click and tells the bot that asked.
const ASK_FILTER_WHY = {
  status: 'A progress note: nothing to decide', covered: 'Same as one you already have open',
  unclear: 'Doesn\u2019t say what to decide', own_work: 'Work a bot can do itself',
  chores_full: 'Your hands-only chores were full', criteria: 'Over your budget and meets none of your lines',
  recommends: 'Over your budget, without a recommendation or a reason it can\u2019t wait',
  urgent_only: 'Your plate is full and it isn\u2019t urgent',
};
const ASK_FILTER_OUTCOME = {sent_back: 'Sent back', fyi: 'FYI', merged: 'Merged'};
async function askFilteredLoad(state) {
  const r = await v2Get('/v2/ask-ingest/filtered?hours=24');
  if (TASKS_ST !== state) return;
  const items = r?.items || [];
  if (JSON.stringify(items) === JSON.stringify(state.filtered || [])) return;
  state.filtered = items;
  tasksRender(state);
}
function askFilteredRow(it) {
  const rehearsal = it.mode !== 'on', done = !!it.let_through_at;
  const what = ASK_FILTER_OUTCOME[it.would] || 'Filtered';
  const title = it.task ? `<a href="#/task/${encodeURIComponent(it.task)}">${esc(it.title || 'Untitled')}</a>` : esc(it.title || 'A question');
  const act = done ? '<span class="muted">Let through</span>'
    : rehearsal ? `<span class="muted">Would have: ${esc(what.toLowerCase())}</span>`
    : `<button type="button" class="ghost" data-let-through="${esc(it.id)}">Let it through</button>`;
  return `<div class="tl-filtered-row" role="listitem">
    <span class="tl-filtered-text"><span class="req-from">${esc(it.asker_name || actorLabel(it.asker) || 'A bot')}:</span> ${title}
      <span class="muted">${esc(rehearsal ? '' : what + ' · ')}${esc(ASK_FILTER_WHY[it.reason] || '')} · ${esc(ago(it.created))}</span></span>
    <span class="tl-filtered-act">${act}</span></div>`;
}
function askFilteredHTML(state) {
  const items = state?.filtered || [];
  if (!items.length) return '';
  const rehearsal = items.every(it => it.mode !== 'on');
  const label = rehearsal ? 'Filtered for you (rehearsal)' : 'Filtered for you';
  return `<details class="tl-group tl-filtered" data-group="filtered"><summary class="tl-ghead"><span class="tl-gname">${esc(label)}</span>
      <span class="tl-gcount tnum">${items.length}</span><span class="spacer"></span>
      <a class="tl-gchat" href="#/settings/tasks" data-ask-filter-settings>What bots may put on you</a></summary>
    <div class="tl-rows" role="list" aria-label="${esc(label)}">${items.map(askFilteredRow).join('')}</div></details>`;
}
document.addEventListener('click', async ev => {
  const button = ev.target.closest?.('[data-let-through]');
  if (!button) return;
  button.disabled = true;
  try {
    await post(`/v2/ask-ingest/items/${encodeURIComponent(button.dataset.letThrough)}/let-through`);
    toast('On your plate; the bot that asked is told');
    await v2Refresh();
    if (TASKS_ST && isTasksRoute(S.route)) { await tasksLoad(TASKS_ST); }
  } catch (e) {
    toast(e.message || 'Not let through', true);
    button.disabled = false;
  }
});
// Finishing a bot's request is one tap: no prompt. What the person typed in the box they are looking at (the task
// window's comment box, or the card's reply box) goes with Done as the outcome; otherwise nothing extra is sent.
// Closing never takes one.
function taskOutcomeNote(task, action, typed) {
  if (!task || action.close || !String(task.owner || '').startsWith('human:') ||
      !String(task.requester || '').startsWith('bot:')) return '';
  return String(typed || '').trim();
}
// The task window's typed outcome went with Done: it is not left behind to be sent again as a comment.
function taskOutcomeTaken(task) {
  TASK_DRAFTS.delete(String(task.id));
  const el = document.querySelector(`dialog[data-task="${CSS.escape(String(task.id))}"] .task-comments + form textarea`);
  if (el) { el.value = ''; el.style.height = ''; }
}
// Capturing, so a recommendation button inside a task row answers instead of opening the task.
document.addEventListener('click', ev => {
  const rec = ev.target.closest?.('[data-recommend]');
  if (!rec) return;
  ev.preventDefault(); ev.stopPropagation();
  void needsRecommend(rec, rec.dataset.recommend, Number(rec.dataset.version) || null);
}, true);
document.addEventListener('click', ev => {
  const st = ev.target.closest('[data-stale]');
  if (st) {
    ev.preventDefault();
    const decision = st.dataset.staleDecision, version = Number(st.closest('[data-stale-row]')?.dataset.staleVersion);
    void staleDecide(st, `/v2/stale-review/${encodeURIComponent(st.dataset.stale)}`, decision === 'accept' ? {decision, version} : {decision});
    return;
  }
  const sa = ev.target.closest('[data-stale-all]');
  if (sa) {
    ev.preventDefault();
    const host = document.querySelector(`[data-stale-card="${CSS.escape(sa.dataset.staleAll)}"]`);
    const rows = [...(host?.querySelectorAll('[data-stale-row][data-stale-action]') || [])]
      .map(li => ({id: li.dataset.staleRow, version: Number(li.dataset.staleVersion)}));
    if (rows.length) void staleDecide(sa, '/v2/stale-review/accept-all', {rows});
  }
});

// Browser notifications for new thread items, while the page is in the background, when the person turned them on
// (their Notifications card, person-page.js). The ids already notified are kept per person (their id, from /api/me) in
// localStorage, so a reload does not repeat them; the first read after a fresh start records what is listed without
// notifying. Both reads of the list call this (hub-v2.js after a refresh, refresh.js after a live change), and the calls
// run one after another, so they share one memory and an item is notified once.
let notifyQueue = Promise.resolve();
function notifyNewThreadNeeds(items) {
  notifyQueue = notifyQueue.then(() => notifyThreadNeedsOnce(items)).catch(() => {});
  return notifyQueue;
}
async function notifyThreadNeedsOnce(items) {
  const id = S.me?.id;
  if (!id) return;                  // the identity has not loaded: there is no person to keep a memory for yet
  if (S.notifyBrowser === undefined) {
    try { S.notifyBrowser = (await v2Get('/v2/humans/' + encodeURIComponent(id)))?.notify_browser === true; }
    catch { S.notifyBrowser = false; }
  }
  const key = 'tico.notified.' + id;
  const thread = items.filter(it => THREAD_NEED_KINDS[it.kind]);
  let seen = null;
  try { seen = JSON.parse(localStorage.getItem(key) || 'null'); } catch { seen = null; }
  const first = !Array.isArray(seen);
  const known = new Set(first ? [] : seen);
  const fresh = thread.filter(it => !known.has(String(it.id)));
  if (fresh.length) {
    const ids = [...known, ...fresh.map(it => String(it.id))].slice(-200);
    try { localStorage.setItem(key, JSON.stringify(ids)); } catch { /* private window: notify without memory */ }
  }
  if (first || !fresh.length || !S.notifyBrowser || !document.hidden || !('Notification' in window)
      || Notification.permission !== 'granted') return;
  for (const it of fresh) {
    try { new Notification(`${THREAD_NEED_KINDS[it.kind]} in Tico`, {body: it.title || '', tag: 'need-' + it.id}); }
    catch { /* a browser that refuses a notification shows nothing; the list still has it */ }
  }
}
// A write held for review (org_rule_holds) has no thread to open: its hold card opens under the row, the card the task
// page shows, with Send anyway and Keep blocked for its reviewer. The card is read from the hold, so its text reaches
// only the reviewer, who is the one who may read it.
document.addEventListener('click', async ev => {
  const review = ev.target.closest('[data-hold-review]');
  if (!review) return;
  const holdId = review.dataset.holdReview, row = review.closest('[data-need-card]');
  if (!row) return;
  const open = row.parentElement?.querySelector(`[data-hold-slot="${CSS.escape(holdId)}"]`);
  if (open) { open.remove(); return; }
  const slot = document.createElement('div');
  slot.className = 'needs-hold';
  slot.dataset.holdSlot = holdId;
  slot.dataset.task = holdId;       // ruleHoldRedraw checks a card's slot by this id, as a task page does
  row.after(slot);
  try {
    const hold = await get('/v2/rule-holds/' + encodeURIComponent(holdId), {fresh: true});
    slot.innerHTML = ruleHoldHTML(hold);
    ruleHoldsWire(slot, holdId);
  } catch (e) {
    slot.textContent = e.message;
  }
});
