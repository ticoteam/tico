/* ui/app/thread-view.js — One thread on screen: its timeline, flat and in time order (a reply quotes the message it
   answers in one line above its words), its composer, the bots' status at its end, and its live updates. Mounted by the
   Thread page (#/thread/<id>), a bot's Home and DM, a person's Home and a team's Home. Shapes: docs/dev/threads-contract.md §10.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const THV_VIEWS = new Set();
// One author's messages within this long of each other share one header.
const THV_GROUP_MS = 5 * 60 * 1000;
// The reaction picker: these, then what the thread already uses.
const THV_EMOJI = ['👍', '❤️', '😂', '🎉', '👀', '🙏'];
const THV_SVG = {
  react: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M8.5 14.5a4.5 4.5 0 0 0 7 0M9 9.5h.01M15 9.5h.01"/></svg>',
  reply: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 14L4 9l5-5M4 9h11a5 5 0 0 1 5 5v6"/></svg>',
  copy: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/></svg>',
  quote: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 14L4 9l5-5M4 9h11a5 5 0 0 1 5 5v6"/></svg>',
};
let THV_N = 0;
const thvPath = id => '/v2/threads/' + encodeURIComponent(id);
const thvHuman = a => String(a || '').startsWith('human:');
const thvMine = m => !!m && m.sender_id === myActor();
// Addressed to the viewer: written by a person, sent to them, or naming them. Other bot posts are activity.
const thvAddressed = m => thvHuman(m?.sender_id) || m?.recipient_id === myActor() || (m?.mentions || []).includes(myActor());

// Unsent words and files per thread, Home or DM, kept while the view is off screen (another tab, page or bot).
const THV_DRAFTS = new Map();
const thvDraftKey = view => view.opts.id ? 't:' + view.opts.id : view.opts.home ? 'h:' + view.opts.home : 'd:' + (view.opts.dm || '');
function thvDraftSave(view) {
  const C = view.composer, key = thvDraftKey(view);
  if (!C) return;
  const text = C.el.querySelector('.p-text')?.value || '';
  THV_DRAFTS.delete(key);
  if (text.trim() || C.files.length || C.lib.length) THV_DRAFTS.set(key, {text, files: C.files.slice(), lib: C.lib.slice()});
  if (THV_DRAFTS.size > 50) THV_DRAFTS.delete(THV_DRAFTS.keys().next().value);
}

// `opts`: {id} a thread, {home: actor} that teammate's Home, or {dm: actor} the viewer's DM with them (made on the
// first message). {card: true} keeps the list to a box inside a page; {header: false} leaves out the title line, and
// {header: 'compact'} keeps only its actions (call, Members, the thread menu), drawn into `actsHost` when given (a bar
// the page owns). {compactEmpty: true} draws a Home with no messages as its composer alone, never a blank timeline, and
// hides the card (`hideGone`) from a viewer who can't post either. {focus: message id} shows that message. {goals: slug}
// (a bot's Home or DM) brings back the old chat's bar (pill.js): the ways to send (Set as goal among them) and the
// pinned goal's bar (chat-goal.js), the bot's avatar that switches bots on a phone, Stop while it runs, and the "/" menu.
function threadMount(host, opts = {}) {
  const view = {key: ++THV_N, host, opts, id: '', thread: null, breadcrumb: [], messages: [], next: null, state: 'loading',
                readAt: 0, openSeq: 0, readSent: new Map(), marks: new Map(), pending: [], replyTo: null, botStatus: null, flash: '',
                follow: null, gone: false, error: '', run: {openSteps: new Set(), steps: {}, openFolds: new Set()}};
  THV_VIEWS.add(view);
  host.innerHTML = `<section class="thv${opts.card ? ' thv-card' : ''}" data-thv="${view.key}">
    ${opts.header === false ? '' : '<header class="thv-head"></header>'}
    ${opts.goals ? '<section class="chat-goal thv-goal" aria-label="Goal" hidden></section>' : ''}
    <div class="thv-scroll"><button type="button" class="ghost small thv-older" hidden>Earlier</button>
      <div class="thv-list" role="log" aria-live="polite"></div></div>
    <button type="button" class="stick-pill thv-unread" hidden>Unread</button>
    <div class="thv-composer"></div></section>`;
  view.el = host.querySelector('.thv');
  if (opts.goals) view.goals = chatGoalThread(view, opts.goals);
  thvWire(view);
  // A view that is not displayed (a hidden tab or pane) reads nothing; it marks read once it shows.
  if (window.ResizeObserver) { view.shown = new ResizeObserver(() => thvMarkRead(view)); view.shown.observe(view.el); }
  void thvLoad(view);
  return view;
}
function threadUnmount(view) {
  if (!view || view.gone) return;
  thvDraftSave(view);
  view.gone = true;
  view.follow?.();
  view.shown?.disconnect();
  THV_VIEWS.delete(view);
  window.askPeekDock?.();
}
// Views whose host left the page stop following.
function thvSweep() { for (const v of THV_VIEWS) if (!v.host.isConnected) threadUnmount(v); }

// ----------------------------------------------------------------- loading
// Flat: the root's whole readable subtree in time order, each reply quoting what it answers (an older server sends its
// tree, drawn flat in the order it comes, its quotes made here).
const thvTimeline = (id, cursor) => thvPath(id) + '/timeline?limit=50&flat=1' + (cursor != null ? '&cursor=' + encodeURIComponent(cursor) : '');
async function thvResolve(view) {
  const o = view.opts;
  if (o.id) return o.id;
  if (o.home) {
    const t = await get('/v2/threads/home/' + encodeURIComponent(o.home), {fresh: true});
    view.thread = t;
    return t?.id || '';
  }
  if (o.dm) {
    try {
      const r = await get('/v2/threads/dm?create=false&with=' + encodeURIComponent(o.dm), {fresh: true});
      const t = r?.thread !== undefined ? r.thread : r;
      if (t?.id) { view.thread = t; return t.id; }
    } catch (e) { if (e?.status !== 404) throw e; }
  }
  return '';
}
function thvSetId(view, id) {
  if (view.id === id) return;
  view.follow?.();
  view.id = id;
  view.follow = liveFollowThread(id);
  if (view.goals) { void chatGoalThreadId(view); void thvRunRead(view); }
}
async function thvLoad(view) {
  try {
    const id = await thvResolve(view);
    if (view.gone) return;
    if (!id) { view.state = 'empty'; thvDraw(view); if (view.goals) void chatGoalThreadId(view); return; }
    thvSetId(view, id);
    const r = await get(thvTimeline(id), {fresh: true});
    if (view.gone) return;
    view.thread = r.thread || view.thread;
    view.breadcrumb = r.breadcrumb || [];
    thvStatusTake(view, r);
    view.messages = thvMerge(view, r.messages || []);
    view.next = r.next_cursor || null;
    view.readAt = Number(view.thread?.me?.last_read_seq) || 0;
    view.openSeq = thvMaxSeq(view.messages);
    thvLearnMarks(view, view.messages);
    view.state = view.messages.length ? 'ready' : 'empty';
    thvDraw(view, {stick: !view.opts.focus});
    thvMarkRead(view);
    void callLoad(view);
    void thvHoldsLoad(view);
    void thvMembersLoad(view);
    if (view.opts.focus) void thvFocus(view, view.opts.focus);
  } catch (e) {
    if (view.gone) return;
    view.state = e?.status === 404 ? 'gone' : 'error';
    view.error = e?.message || '';
    // A Home the viewer may not read leaves no box behind on a person's or a team's page.
    if (view.state === 'gone' && view.opts.hideGone) { (view.opts.hideGone === true ? view.host : view.opts.hideGone).hidden = true; return; }
    thvDraw(view);
  }
}
// A message posted elsewhere (Start onboarding) into a DM on screen: that DM reads itself again, finding the thread
// if it was not there yet.
function threadsReloadDM(actor) {
  for (const v of THV_VIEWS) if (v.opts.dm === actor && !v.gone) { if (v.id) void thvRefresh(v); else void thvLoad(v); }
}
// The viewer's own held messages in this thread come back after a reload, still with Edit and Discard. A held edit (made
// through the API) is not shown here: this view does not edit messages.
async function thvHoldsLoad(view) {
  if (!view.id) return;
  try {
    const r = await get('/v2/rule-holds?thread=' + encodeURIComponent(view.id) + '&limit=20', {fresh: true});
    const mine = (r?.holds || []).filter(h => h.author === myActor() && h.state === 'held' && !view.pending.some(p => p.held?.id === h.id)).slice(0, 5);
    for (const s of mine) {
      const h = await get('/v2/rule-holds/' + encodeURIComponent(s.id), {fresh: true}).catch(() => null);
      if (!h || h.operation === 'threads_edit' || view.gone || view.pending.some(p => p.held?.id === h.id)) continue;
      view.pending.push({key: 'p' + (++THV_N), text: h.text || '', files: [], lib: [], state: 'held',
                         held: h, canSend: !!h.can_send, nonce: h.nonce || ''});
    }
    if (mine.length && !view.gone) thvDraw(view);
  } catch {}           // a server without rule holds has nothing to bring back
}
const thvMaxSeq = list => list.reduce((n, m) => Math.max(n, Number(m.seq) || 0), 0);
// Where the viewer had read each sub-thread an older server sends inline: what is past it shows as new, per thread.
function thvLearnMarks(view, rows) {
  for (const m of rows) if (m.sub_thread?.id && !view.marks.has(m.sub_thread.id)) view.marks.set(m.sub_thread.id, Number(m.sub_thread.last_read_seq) || 0);
}
// A page read from the server merged with what the stream already put on screen: the higher revision of each message
// wins, and newer messages the page does not have yet stay at the bottom (`seq` orders the whole install).
function thvMerge(view, fresh) {
  const had = new Map(view.messages.map(m => [m.id, m]));
  const out = fresh.map(f => {
    const e = had.get(f.id);
    // A row the server now hides or has deleted stays so; otherwise the later revision wins.
    if (!e || f.hidden || f.deleted || (Number(e.revision) || 0) <= (Number(f.revision) || 0) && !e.deleted) return f;
    return {...f, ...e, reply_to: f.reply_to ?? e.reply_to, routed: f.routed ?? e.routed};
  });
  const ids = new Set(out.map(m => m.id)), top = thvMaxSeq(out);
  for (const e of view.messages) if (!ids.has(e.id) && !e.hidden && e.seq && Number(e.seq) > top) { out.push(e); ids.add(e.id); }
  return out;
}
// The first page read again after a change the stream could not place; older pages already read stay above it.
async function thvRefresh(view) {
  if (view.gone || !view.id) return thvLoad(view);
  try {
    const r = await get(thvTimeline(view.id), {fresh: true});
    if (view.gone) return;
    const fresh = r.messages || [], ids = new Set(fresh.map(m => m.id));
    const first = fresh.find(m => !m.hidden && m.seq)?.seq ?? Infinity;
    // Everything above the page read again stays: whatever came before the page's first row.
    const start = view.messages.findIndex(m => !m.hidden && (Number(m.seq) || 0) >= first);
    const older = view.olderRead ? view.messages.slice(0, start < 0 ? view.messages.length : start).filter(m => !ids.has(m.id)) : [];
    view.thread = r.thread || view.thread;
    view.breadcrumb = r.breadcrumb || view.breadcrumb;
    thvStatusTake(view, r);
    thvLearnMarks(view, fresh);
    const olderIds = new Set(older.map(m => m.id));
    const merged = thvMerge({messages: view.messages.filter(m => !olderIds.has(m.id))}, fresh);
    view.messages = [...older, ...merged];
    if (!view.olderRead) view.next = r.next_cursor || null;
    view.state = view.messages.length ? 'ready' : 'empty';
    thvDraw(view, {stick: thvAtBottom(view)});
    thvMarkRead(view);
  } catch (e) {
    if (e?.status === 404) { view.state = 'gone'; thvDraw(view); }
  }
}
// One earlier page, above what is on screen.
async function thvPageOlder(view) {
  const r = await get(thvTimeline(view.id, view.next), {fresh: true});
  if (view.gone) return false;
  thvLearnMarks(view, r.messages || []);
  const have = new Set(view.messages.map(m => m.id));
  view.messages = [...(r.messages || []).filter(m => !have.has(m.id)), ...view.messages];
  view.next = r.next_cursor || null;
  view.olderRead = true;
  return true;
}
// A message to show (from search, or a quote): earlier pages are read until it is there, then it is scrolled to and
// marked. False when it is not in the pages this view can read.
async function thvFocus(view, mid, {flash = false} = {}) {
  for (let pages = 0; !view.messages.some(m => m.id === mid) && view.next && pages < 10 && !view.gone; pages++) {
    const ok = await thvPageOlder(view).catch(() => false);
    if (!ok) return false;
    thvDraw(view);
  }
  if (!view.messages.some(m => m.id === mid)) return false;
  if (flash) thvFlash(view, mid); else { view.focus = mid; thvDraw(view); }
  view.el.querySelector(`.th-msg[data-mid="${CSS.escape(mid)}"]`)?.scrollIntoView({block: 'center'});
  return true;
}
// A quoted message, brought into view and lit for a moment.
function thvFlash(view, mid) {
  view.flash = mid; thvDraw(view);
  clearTimeout(view.flashTimer);
  view.flashTimer = setTimeout(() => { if (view.flash === mid) { view.flash = ''; thvDraw(view); } }, 1600);
}
async function thvGoTo(view, mid) {
  const at = () => view.el.querySelector(`.th-msg[data-mid="${CSS.escape(mid)}"]`);
  // Found after the flash's redraw, which may put a new node in the old one's place.
  if (at()) { thvFlash(view, mid); at()?.scrollIntoView({block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'}); return; }
  if (await thvFocus(view, mid, {flash: true}) || view.gone) return;
  // Not in this view's pages (a sub-thread's message, or past what it reads): its thread opens at it.
  const root = view.thread?.root_id || view.id;
  if (root && root !== view.id) location.hash = threadHref(root) + '?focus=' + encodeURIComponent(mid);
}
async function thvOlder(view, button) {
  if (!view.next) return;
  button.disabled = true;
  const box = view.el.querySelector('.thv-scroll'), from = box.scrollHeight - box.scrollTop;
  try {
    if (await thvPageOlder(view)) { thvDraw(view); box.scrollTop = box.scrollHeight - from; }
  } catch (e) { toast(e.message, true); }
  button.disabled = false;
}
// ----------------------------------------------------------------- drawing
const thvCanPost = view => view.opts.canPost ?? (view.thread ? !!view.thread.me?.can_post : view.state === 'empty' && !!view.opts.dm);
function thvActsHTML(view) {
  const t = view.thread, list = view.memberList;
  const members = !t.parent_id && ['members', 'personal', 'company', 'team'].includes(t.audience);
  // On the Threads page the members show as faces and a count, which open the same list.
  const n = list ? list.length : 0, more = view.memberMore ? '+' : '';
  const people = members && list ? `<button type="button" class="thv-people" data-thv-members aria-label="Members, ${n}${more}" title="Members">${
      list.slice(0, 3).map(m => actorAvatar(m.actor, 22)).join('')}<span class="thv-people-n">${n}${more}</span></button>`
    : members ? '<button type="button" class="ghost small" data-thv-members>Members</button>' : '';
  return `${callHeadHTML(view)}
      ${people}
      <button type="button" class="ghost thv-menu-btn" data-thv-menu aria-haspopup="menu" aria-expanded="false" aria-label="Thread menu" title="More"><span class="nav-icon" aria-hidden="true">more_horiz</span></button>`;
}
// The viewer's own pin, on a thread the rail lists (a server with pins says `me.pinned`).
const thvPinnable = t => !!t?.me && 'pinned' in t.me && threadOnRail(t);
const thvPinHTML = t => thvPinnable(t) ? `<button type="button" class="thv-pin${t.me.pinned ? ' on' : ''}" data-thv-pin aria-pressed="${!!t.me.pinned}" aria-label="Pin" title="${t.me.pinned ? 'Unpin' : 'Pin'}">${TR_SVG.pin(!!t.me.pinned)}</button>` : '';
function thvHeadHTML(view) {
  const t = view.thread;
  if (!t) return '';
  if (view.opts.header === 'compact') return `<div class="thv-titleline thv-compact"><span class="spacer"></span>${thvActsHTML(view)}</div>`;
  const crumbs = (view.breadcrumb || []).filter(c => c.id !== t.id);
  // A thread the rail lists says what it is with its mark (# or a lock); a Home or a DM says it in words.
  const hash = threadOnRail(t);
  // A DM's face already says who it is with, so it carries no "Private"; the name keeps the room on a phone.
  const aud = hash || threadIsDm(t) ? '' : t.audience === 'company' ? 'Company' : t.audience === 'team' ? groupName(t.audience_group_id)
    : t.audience === 'home' ? 'Home' : threadPrivate(t) ? 'Private' : '';
  const back = crumbs.length ? `<a class="thv-back" href="${threadHref(crumbs[crumbs.length - 1].id)}" aria-label="Back" title="Back">‹</a>`
    : view.opts.back ? `<a class="thv-back thv-back-rail" href="${esc(view.opts.back)}" aria-label="Threads" title="Threads">‹</a>` : '';
  return `${crumbs.length ? `<nav class="thv-crumbs" aria-label="Thread path">${crumbs.map(c =>
      `<a href="${threadHref(c.id)}">${esc(c.title || 'Thread')}</a>`).join('<span aria-hidden="true">›</span>')}</nav>` : ''}
    <div class="thv-titleline">${back}
      ${hash ? '' : threadMarkHTML(t, 22)}<h1 class="thv-title">${hash ? threadHashHTML(t) : ''}${esc(threadTitle(t))}</h1>${thvPinHTML(t)}
      ${aud ? `<span class="thv-aud muted">${esc(aud)}</span>` : ''}<span class="spacer"></span>${thvActsHTML(view)}</div>`;
}
// Who is in it, for the faces in the header: a thread the rail lists, on the Threads page (`opts.members`). A DM keeps
// its Members button, so a phone has room for the name.
async function thvMembersLoad(view) {
  const t = view.thread;
  if (!view.opts.members || !threadOnRail(t)) return;
  try {
    const r = await get(thvPath(t.id) + '/members?limit=100', {fresh: true});
    if (view.gone) return;
    view.memberList = r?.members || []; view.memberMore = !!r?.next_cursor;
    thvDraw(view);
  } catch { /* the Members button stays */ }
}
// Pin or unpin for the viewer; the rail moves the row at once.
async function thvPinToggle(view, button) {
  const t = view.thread; if (!thvPinnable(t)) return;
  const on = !t.me.pinned;
  button.disabled = true;
  try {
    const r = on ? await put(thvPath(t.id) + '/pin', {}) : await writeRequest('DELETE', thvPath(t.id) + '/pin');
    view.thread = {...t, ...(r?.id ? r : {}), me: {...t.me, ...(r?.me || {}), pinned: r?.me ? !!r.me.pinned : on}};
    threadsPut(view.thread);
    thvDraw(view);
  } catch (e) { toast(e.message, true); button.disabled = false; }
}
// A message the viewer may not read is one quiet row, never its words. Earlier shared history folds into one "Earlier
// messages" row; two such rows meet where one page of it ends and the next begins.
function thvRows(view) {
  const rows = [];
  for (const m of view.messages) {
    if (m.hidden) {
      const fold = m.earlier ? 'earlier' : 'hidden', last = rows[rows.length - 1];
      if (last?.fold === fold) { last.ids.push(m.id); continue; }
      rows.push({fold, id: m.id, ids: [m.id]});
      continue;
    }
    // An older server's tree folds a long sub-thread's start: a quiet link to that sub-thread stands for it.
    if (m.collapsed) { rows.push({more: m, id: m.id}); continue; }
    if (chatGoalControl(m)) continue;          // a goal's /goal message: the goal bar shows its effect (chat-goal.js)
    rows.push({m});
  }
  return rows;
}
function thvIsNew(view, m) {
  if (!m.seq || thvMine(m)) return false;
  // Arriving at the bottom of the thread on screen is not news. An older server's inline sub-thread keeps its own read
  // mark, and a reply arriving in it is.
  const tid = m.thread_id || view.id;
  if (tid !== view.id && view.marks.has(tid)) return m.seq > view.marks.get(tid);
  return m.seq > view.readAt && m.seq <= view.openSeq;
}
const thvBot = a => String(a || '').startsWith('bot:');
function thvWhoHTML(a) {
  const slug = actorSlug(a), pid = actorPerson(a);
  if (slug && S.emps.some(e => e.id === slug)) return `<a class="th-who" href="#/bot/${esc(slug)}">${esc(actorName(a))}</a>`;
  if (pid) return `<a class="th-who" href="#/person/${esc(encodeURIComponent(pid))}">${esc(actorName(a))}</a>`;
  return `<span class="th-who">${esc(actorName(a) || 'Someone')}</span>`;
}
const THV_BOT_TAG = '<span class="th-bot">BOT</span>';
// An update is its own record with its own page: the Home shows a card that opens it, never the update as chat.
function thvUpdateHTML(m) {
  const u = m.refs?.update, id = typeof u === 'object' ? u?.id : u;
  const title = (typeof u === 'object' && u?.title) || v2Firstline(m.body) || 'Update';
  if (!id) return `<div class="th-body">${esc(title)}</div>`;
  const q = new URLSearchParams({...(u?.kind ? {kind: u.kind} : {}), ...(u?.day ? {day: u.day} : {}), update: id});
  return `<a class="th-update" href="${UPDATES}?${q}"><span class="th-update-k">Update</span>
    <strong>${esc(title)}</strong></a>`;
}
// Update cards are notices the server writes with refs.card 'update'; older rows may carry only refs.update.
const thvIsUpdateCard = m => m.refs?.card === 'update' || m.kind === 'update' || (!!m.refs?.update && m.kind === 'notice');
function thvFilesHTML(m) {
  const lib = (m.refs?.files || []).filter(f => f?.id);
  return (m.refs?.attachments?.length ? chatAttachmentsHTML(m.refs.attachments, m.id) : '')
    + (lib.length ? `<div class="chat-files">${lib.map(f => `<button type="button" class="pill th-libfile" data-th-file="${esc(f.id)}" data-name="${esc(f.title || f.name || 'file')}">${esc(f.title || f.name || 'file')}</button>`).join('')}</div>` : '');
}
// The server lists who reacted ({emoji, count, actors}); "mine" is whether the viewer is among them.
const thvReactedMine = r => (r?.actors || []).includes(myActor());
function thvReactionsHTML(m) {
  const rs = (m.reactions || []).filter(r => r.count > 0);
  if (!rs.length) return '';
  return `<div class="th-reacts">${rs.map(r => `<button type="button" class="th-react${thvReactedMine(r) ? ' me' : ''}" data-th-react="${esc(r.emoji)}" aria-pressed="${thvReactedMine(r) ? 'true' : 'false'}" title="${esc(r.emoji)}">${esc(r.emoji)} <span class="tnum">${r.count}</span></button>`).join('')}</div>`;
}

// ---- replies: one line quoting the message answered
// Plain words of a message's start, as a quote shows it.
const thvExcerpt = text => chatLine(String(text || '').replace(/!\[[^\]]*\]\([^)]*\)/g, '').replace(/\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/[*_`#>~|]/g, ''), 120);
const thvQuoteFrom = t => t.hidden ? {id: t.id, hidden: true} : {id: t.id, author: t.sender_id, author_name: actorName(t.sender_id), excerpt: thvExcerpt(t.deleted ? 'Deleted' : t.body)};
// What a message quotes: the server's `reply_to` (null: nothing), else, from a server without it or the stream, the
// message it names (`in_reply_to`) or, for one in a sub-thread, the message that sub-thread opened under.
function thvQuoteOf(view, m) {
  if (m.reply_to !== undefined) return m.reply_to || null;
  const target = m.in_reply_to ? view.messages.find(x => x.id === m.in_reply_to)
    : m.thread_id && m.thread_id !== view.id ? view.messages.find(x => x.sub_thread?.id === m.thread_id) : null;
  if (target) return thvQuoteFrom(target);
  return m.in_reply_to ? {id: m.in_reply_to, author: '', author_name: '', excerpt: ''} : null;
}
function thvQuoteHTML(q) {
  if (!q?.id) return '';
  if (q.hidden) return `<span class="th-quote th-quote-hidden">${THV_SVG.quote}<span class="th-quote-text">Message not shown</span></span>`;
  const who = q.author_name || (q.author ? actorName(q.author) : '');
  return `<button type="button" class="th-quote" data-th-quote="${esc(q.id)}" aria-label="${esc(who ? 'Reply to ' + who : 'Reply')}">${THV_SVG.quote}${
    who ? `<b class="th-quote-who">${esc(who)}</b>` : ''}<span class="th-quote-text">${esc(q.excerpt || '')}</span></button>`;
}
// Under a person's message that went through routing: which bots it asked, or that none was.
function thvRoutedHTML(m) {
  const r = m.routed;
  if (!r || !Array.isArray(r.to) || !thvHuman(m.sender_id)) return '';
  const names = r.to.map(x => x?.name || actorName(x?.actor)).filter(Boolean);
  return `<div class="th-routed">${names.length ? esc('Asked ' + names.join(', ')) : 'No bot asked'}</div>`;
}

// ---- one message
const thvRunOf = m => thvBot(m.sender_id) && !!(m.run?.steps || m.run?.did?.length || m.refs?.turn_id);
const thvClock = iso => { const d = new Date(iso || ''); return isNaN(d) ? '' : d.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}); };
// Whether `m` sits under `prev`'s header: the same author within five minutes, and `m` neither a reply nor a bot's run.
function thvJoins(view, prev, m) {
  if (!prev || prev.sender_id !== m.sender_id || prev.deleted || m.deleted || thvIsUpdateCard(m) || thvIsUpdateCard(prev)) return false;
  if (thvQuoteOf(view, m) || thvRunOf(m)) return false;
  const a = Date.parse(prev.created_at || ''), b = Date.parse(m.created_at || '');
  return a > 0 && b > 0 && Math.abs(b - a) <= THV_GROUP_MS;
}
function thvMsgActsHTML(view, m) {
  const can = thvCanPost(view);
  return `<div class="th-acts" role="toolbar" aria-label="Message">
    <button type="button" class="th-act" data-th-pick aria-label="React" title="React" aria-haspopup="menu" aria-expanded="false">${THV_SVG.react}</button>${
    can ? `<button type="button" class="th-act" data-th-reply aria-label="Reply" title="Reply">${THV_SVG.reply}</button>` : ''}${
    m.body ? `<button type="button" class="th-act" data-th-copy aria-label="Copy" title="Copy">${THV_SVG.copy}</button>` : ''}</div>`;
}
function thvMsgHTML(view, m, cont = false) {
  const bot = thvBot(m.sender_id);
  // A bot's own words take a row of Copy, 👍 and 👎 under them, apart from the emoji reactions (ui/app/feedback.js).
  const fb = !m.deleted && m.id && bot ? fbKey('message', m.id) : '';
  const fresh = thvIsNew(view, m), quiet = fresh && !thvAddressed(m);
  const cls = ['th-msg', cont && 'th-cont', view.focus === m.id && 'th-focus', view.flash === m.id && 'th-flash', m.refs?.follow_up && 'th-followup',
    thvMine(m) && 'th-mine', fresh && 'th-new', quiet && 'th-new-quiet', m.deleted && 'th-deleted'].filter(Boolean).join(' ');
  const head = cont ? '' : `<div class="th-head">${thvWhoHTML(m.sender_id)}${bot ? THV_BOT_TAG : ''}<time title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</time>${
    m.edited_at && !m.deleted ? '<span class="th-edited muted">edited</span>' : ''}</div>`;
  let body;
  if (m.deleted) body = '<div class="th-body muted">Deleted</div>';
  else if (thvIsUpdateCard(m)) body = thvUpdateHTML(m);
  // A met or stopped goal: the one line the chat showed (chat-goal.js).
  else if (m.refs?.chat_goal && m.refs?.goal_status) body = chatGoalNoticeHTML(m, view.goals?.goal);
  // A card a bot left for you fills in from its action or request (ui/assistant.js, ui/credential-card.js).
  else if (m.refs?.credential_request) body = `<div data-credential-host="${esc(m.refs.credential_request)}"><div class="asst-state">Loading the card…</div></div>`;
  else if (m.refs?.action) body = `<div data-action-host="${esc(m.refs.action)}"><div class="asst-state">Loading the card…</div></div>`;
  // The Assistant's read-back of what it will approve is shown exactly as written, never formatted.
  else if (m.refs?.read_back) body = `<div class="th-body read-back" style="white-space:pre-line">${esc(m.body || '')}</div>`;
  else body = `<div class="th-body md">${safeMd(m.body || '', {shortLinks: true})}</div>${
    // The notice a bot's manager gets about its Home's closed history (backend/thread_followups.py).
    m.refs?.earlier_messages?.thread_id ? `<button type="button" class="ghost small th-earlier-open" data-th-earlier="${esc(m.refs.earlier_messages.thread_id)}">Open earlier messages to Read</button>` : ''}`;
  // A bot's reply reads as Claude's does: one folded line of what its run did first (ui/app/chat.js), then the words,
  // the runner's warnings and the message's cards.
  const run = !m.deleted && bot ? v2RunHTML(m, view.run) : '';
  if (!m.deleted && bot)
    body += (m.refs?.warnings || []).map(w => `<div class="chat-warn">${esc(String(w).charAt(0).toUpperCase() + String(w).slice(1))}</div>`).join('') + v2MessageCards(m);
  const quote = m.deleted ? '' : thvQuoteHTML(thvQuoteOf(view, m));
  const av = cont ? `<time class="th-cont-time" title="${esc(fmt(m.created_at))}">${esc(thvClock(m.created_at))}</time>` : actorAvatar(m.sender_id, 32);
  return `<div class="${cls}" data-mid="${esc(m.id)}" data-message="${esc(m.id)}" data-tid="${esc(m.thread_id || view.id)}" data-seq="${esc(m.seq ?? '')}">
    <div class="th-av">${av}</div>
    <div class="th-main">${head}${quote}${run}${body}${m.deleted ? '' : thvFilesHTML(m) + thvReactionsHTML(m) + fbRowHTML(fb, m.body)}${thvRoutedHTML(m)}</div>
    ${m.deleted ? '' : thvMsgActsHTML(view, m)}
  </div>`;
}
function thvFoldHTML(view, row) {
  if (row.fold === 'hidden') return `<div class="th-fold th-notshown" data-mid="${esc(row.id)}"><span>Not shown</span></div>`;
  return `<div class="th-fold th-earlier" data-mid="${esc(row.id)}"><span>Earlier messages</span>${
    view.thread?.earlier ? `<button type="button" class="linkish" data-th-earlier="${esc(view.id)}">Open earlier messages to Read</button>` : ''}</div>`;
}
const thvMoreHTML = m => `<a class="th-fold th-more" href="${threadHref(m.thread_id)}" data-mid="${esc(m.id)}"><span>${esc(m.count)} earlier ${Number(m.count) === 1 ? 'reply' : 'replies'}</span></a>`;
function thvPendingHTML(view, p) {
  const text = `<div class="th-body">${esc(p.text)}${p.files?.length ? ` <span class="muted">· ${p.files.length} file${p.files.length === 1 ? '' : 's'}</span>` : ''}</div>`;
  const quote = thvQuoteHTML(p.quote);
  const av = `<div class="th-av">${actorAvatar(myActor(), 32)}</div>`;
  if (p.state === 'held') {
    const h = p.held || {}, rules = (h.rules || []).map(r => `<div class="th-held-rule">${ruTextHTML(r.text)}</div>`).join('');
    return `<div class="th-msg th-mine th-held" data-pending="${p.key}">${av}
      <div class="th-main"><div class="th-head"><span class="th-who">You</span><span class="th-state">${h.outcome === 'escalated' ? 'Waiting for review' : 'Held'}</span></div>${quote}${text}${rules}
        <div class="th-held-acts"><button type="button" class="ghost small" data-held-edit>Edit</button>${
          p.canSend ? '<button type="button" class="ghost small" data-held-send>Send anyway</button>' : ''}<button type="button" class="ghost small" data-held-drop>Discard</button></div></div></div>`;
  }
  return `<div class="th-msg th-mine th-pending${p.state === 'failed' ? ' th-failed' : ''}" data-pending="${p.key}">${av}
    <div class="th-main"><div class="th-head"><span class="th-who">You</span><span class="th-state" role="status">${p.state === 'failed' ? esc(p.error || 'Not sent') : 'Checking…'}</span></div>${quote}${text}
    ${p.state === 'failed' ? '<div class="th-held-acts"><button type="button" class="ghost small" data-held-edit>Edit</button><button type="button" class="ghost small" data-held-drop>Discard</button></div>' : ''}</div></div>`;
}

// ---- the bots' status, at the end of the timeline
// One row per bot a person is waiting on (`bot_status`, docs/dev/threads-contract.md): working on what, up next after
// what, or why it can't reply and where that is fixed. A server without it sends none and shows none.
const THV_STATUS_WORD = {working: 'working', up_next: 'up next', cant_reply: "can't reply"};
const thvMinSec = since => { const s = Math.max(0, Math.floor((Date.now() - (Date.parse(since || '') || Date.now())) / 1000)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
function thvStatusTake(view, r) {
  if (r && Array.isArray(r.bot_status)) view.botStatus = r.bot_status.slice(0, 5);
}
function thvStatusHTML(view, b) {
  if (!b?.actor || !THV_STATUS_WORD[b.state]) return '';
  const slug = actorSlug(b.actor), name = b.name || actorName(b.actor);
  const word = b.state === 'working' ? `working · <span class="tnum" data-th-since="${esc(b.since || '')}">${thvMinSec(b.since)}</span>` : THV_STATUS_WORD[b.state];
  const stop = b.state === 'working' && slug && settingsCanManageBot(botRow(slug))
    ? `<button type="button" class="ghost small th-status-act" data-th-stop="${esc(slug)}" data-run="${esc(b.run_id || '')}">Stop</button>` : '';
  const fix = b.state === 'cant_reply' && b.fix?.href ? `<a class="ghost small th-status-act" href="${esc(b.fix.href)}">${esc(b.fix.label || 'Fix')}</a>` : '';
  return `<div class="th-status th-status-${esc(b.state)}" data-th-status="${esc(b.actor)}" role="status">
    <div class="th-av">${actorAvatar(b.actor, 32)}</div>
    <div class="th-main"><div class="th-head"><span class="th-who">${esc(name)}</span>${THV_BOT_TAG}<span class="th-status-word">${word}</span></div>${
      b.detail ? `<div class="th-status-detail">${b.state === 'working' ? '<span class="th-status-dot" aria-hidden="true"></span>' : ''}${esc(b.detail)}</div>` : ''}</div>
    ${stop}${fix}</div>`;
}
// The working rows' clocks tick in place, once a second, while any is on screen.
let THV_TICK = 0;
function thvTick() {
  const els = document.querySelectorAll('[data-th-since]');
  if (!els.length) { clearInterval(THV_TICK); THV_TICK = 0; return; }
  for (const el of els) { const t = thvMinSec(el.dataset.thSince); if (el.textContent !== t) el.textContent = t; }
}
// A bot's reply landed: its row goes, before the server's next status says so.
function thvStatusReplied(view, m) {
  if (!view.botStatus?.length || !thvBot(m?.sender_id)) return;
  view.botStatus = view.botStatus.filter(b => b.actor !== m.sender_id);
}

function thvListHTML(view) {
  if (view.state === 'loading') return '<div class="th-empty muted">Loading…</div>';
  if (view.state === 'gone') return '<div class="th-empty muted">Not found</div>';
  if (view.state === 'error') return `<div class="th-empty err">${esc(view.error || 'Could not load')}</div>`;
  const rows = thvRows(view), out = [];
  let divided = false, prev = null;
  for (const row of rows) {
    // Updates have their own page (Updates); a Home does not repeat them as cards.
    if (view.opts.home && row.m && thvIsUpdateCard(row.m)) continue;
    // One "New" line above the first message the viewer had not read when the thread opened.
    if (!divided && row.m && thvIsNew(view, row.m)) { out.push('<div class="th-newline" role="separator" aria-label="New"><span>New</span></div>'); divided = true; prev = null; }
    if (row.m) { out.push(thvMsgHTML(view, row.m, thvJoins(view, prev, row.m))); prev = row.m; continue; }
    out.push(row.fold ? thvFoldHTML(view, row) : thvMoreHTML(row.more));
    prev = null;
  }
  for (const p of view.pending) out.push(thvPendingHTML(view, p));
  for (const b of view.botStatus || []) out.push(thvStatusHTML(view, b));
  if (!out.length) out.push(`<div class="th-empty muted">${esc(view.opts.empty || 'No messages yet')}</div>`);
  return out.join('');
}
const thvAtBottom = view => { const b = view.el?.querySelector('.thv-scroll'); return !b || b.scrollHeight - b.scrollTop - b.clientHeight < 60; };
function thvDraw(view, {stick = false} = {}) {
  if (view.gone) return;
  if (!view.host.isConnected) { threadUnmount(view); return; }
  const head = view.opts.actsHost?.isConnected ? view.opts.actsHost : view.el.querySelector('.thv-head');
  if (head) { const h = thvHeadHTML(view); patchHTML(head, h); head.hidden = !h; }
  if (head === view.opts.actsHost) view.el.querySelector('.thv-head')?.setAttribute('hidden', '');
  const list = view.el.querySelector('.thv-list'), box = view.el.querySelector('.thv-scroll');
  if (view.opts.compactEmpty) {
    const blank = view.state === 'empty' && !view.pending.length, card = view.opts.hideGone === true ? view.host : view.opts.hideGone;
    view.el.classList.toggle('thv-blank', blank);
    if (card && ['empty', 'ready'].includes(view.state)) card.hidden = blank && !thvCanPost(view);
  }
  const keep = stick || thvAtBottom(view);
  // Cards both trees share keep their node, as in a chat (ui/app/visual-cards.js).
  patchHTML(list, thvListHTML(view), visualSwap);
  const reload = () => thvRefresh(view);
  if (list.querySelector('[data-action-host]')) void window.assistantChat?.cards(list, {get, post, esc, toast, reload});
  if (list.querySelector('[data-credential-host]')) void window.credentialCards?.mount(list, {get, post, esc, toast, reload});
  view.el.querySelector('.thv-older').hidden = !view.next;
  chatThumbsLoad?.(list);
  const unread = list.querySelector('.th-new');
  view.el.querySelector('.thv-unread').hidden = !unread || thvVisible(box, unread);
  thvComposerDraw(view);
  fbSync();
  if (!THV_TICK && list.querySelector('[data-th-since]')) THV_TICK = setInterval(thvTick, 1000);
  if (keep) box.scrollTop = box.scrollHeight;
}
const thvVisible = (box, el) => { const a = box.getBoundingClientRect(), b = el.getBoundingClientRect(); return b.bottom > a.top && b.top < a.bottom; };

// ----------------------------------------------------------------- read state
// Up to the last message drawn, per thread drawn: the thread on screen and every sub-thread inline in it. A read mark is one
// number, so a sub-thread whose collapsed "N more" still holds something unread stays where it is until that is opened.
function thvMarkRead(view) {
  if (document.hidden || view.gone || !view.id || !['ready', 'empty'].includes(view.state) || !view.el.getClientRects().length) return;
  const max = new Map(), held = new Set(view.messages.filter(m => m.collapsed && m.unread).map(m => m.thread_id));
  for (const m of view.messages) {
    if (m.hidden || m.collapsed || !m.seq) continue;
    const tid = m.thread_id || view.id;
    if (held.has(tid)) continue;
    max.set(tid, Math.max(max.get(tid) || 0, Number(m.seq)));
  }
  for (const [tid, seq] of max) {
    const sent = view.readSent.get(tid) ?? (tid === view.id ? view.readAt : view.marks.get(tid) ?? 0);
    if (seq <= sent) continue;
    view.readSent.set(tid, seq);
    post(thvPath(tid) + '/read', {seq}).catch(() => {});
    threadsMarkedRead(tid, seq);
  }
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) for (const v of THV_VIEWS) thvMarkRead(v); });

// ----------------------------------------------------------------- live
// A message changed in a thread on screen: an edit or a delete in place, a new one at the bottom in time order (a reply
// in an older sub-thread too, as the flat timeline reads it); anything the view cannot place reads the first page again.
function thvApplyMessage(view, d) {
  const tid = d.thread_id || d.message?.thread_id || d.message?.conversation_id, inView = tid === view.id || view.messages.some(m => m.thread_id === tid || m.sub_thread?.id === tid);
  if (!inView && d.root_id !== (view.thread?.root_id || view.id)) return false;
  const at = view.messages.findIndex(m => m.id === d.id);
  if (d.deleted) {
    if (at < 0) return false;
    view.messages[at] = {...view.messages[at], deleted: true, body: '', revision: d.revision};
    return true;
  }
  const m = d.message;
  if (!m) { liveSoon('thv:' + view.key, () => thvRefresh(view), 150); return false; }
  if (at >= 0) {
    if ((Number(m.revision) || 0) < (Number(view.messages[at].revision) || 0)) return false;
    // The stream's view carries no `reply_to` or `routed`; the read's stay.
    const was = view.messages[at];
    view.messages[at] = {...was, ...m, reply_to: m.reply_to !== undefined ? m.reply_to : was.reply_to, routed: m.routed !== undefined ? m.routed : was.routed, sub_thread: was.sub_thread};
    return true;
  }
  view.pending = view.pending.filter(p => p.messageId !== m.id);
  const row = {...m, thread_id: tid};
  // Seq orders the whole install, so a late arrival still lands in its place.
  let i = view.messages.length;
  while (i > 0 && Number(view.messages[i - 1].seq) > Number(row.seq)) i--;
  view.messages.splice(i, 0, row);
  if (view.state === 'empty') view.state = 'ready';
  thvStatusReplied(view, row);
  return true;
}
function threadViewsLive(topic, d) {
  thvSweep();
  for (const view of THV_VIEWS) {
    if (topic === 'reset') { liveSoon('thv:' + view.key, () => thvRefresh(view), 150); continue; }
    if (topic === 'threads') {
      // The bots' status rows, patched as they are (a server with `bot_status`).
      if (d.thread_id === (view.thread?.root_id || view.id) && (d.kind === 'bot_status' || Array.isArray(d.bots))) {
        view.botStatus = (d.bots || []).slice(0, 5);
        thvDraw(view);
        continue;
      }
      // Who a message asked, known just after it lands.
      if (d.kind === 'routed' && d.message_id) {
        const m = view.messages.find(x => x.id === d.message_id);
        if (m) { m.routed = d.routed; thvDraw(view); }
        continue;
      }
      if (d.thread_id === view.id && d.thread) { view.thread = {...view.thread, ...d.thread}; thvDraw(view); }
      // A pin from another tab, or a manager's for everyone: the header's pin follows (`pinned` is this viewer's state).
      else if (d.thread_id === view.id && d.kind === 'pinned' && view.thread?.me) {
        view.thread = {...view.thread, pinned_for_everyone: d.pinned_for_everyone ?? view.thread.pinned_for_everyone, me: {...view.thread.me, pinned: !!d.pinned}};
        thvDraw(view);
      }
      if (d.thread_id === view.id && String(d.kind || '').startsWith('call.')) liveSoon('call:' + view.key, () => callLoad(view), 200);
      // Who may read changed: read again.
      if (d.thread_id === view.id && d.kind === 'members') liveSoon('thv-members:' + view.key, () => thvMembersLoad(view), 300);
      if (d.thread_id === view.id && ['audience', 'members'].includes(d.kind)) liveSoon('thv:' + view.key, () => thvRefresh(view), 150);
      continue;
    }
    if (topic === 'messages' && thvApplyMessage(view, d)) { thvDraw(view); thvMarkRead(view); }
  }
}
liveOn('messages', d => {
  if (d?.thread_id || d?.message?.thread_id) threadViewsLive('messages', d);
  // A goal set, paused, met or stopped in a bot's Home or DM on screen: read it as the goal route shows it.
  if (d?.goal_id) for (const view of THV_VIEWS) if (view.goals && view.id && view.id === d.conversation_id && !view.gone) void chatGoalThreadId(view);
});
// A run in a bot's Home or DM on screen started, ended or was stopped: Stop follows it. Its output steps change nothing.
liveOn('runs', d => {
  if (d?.output) return;
  for (const view of THV_VIEWS) if (view.goals && view.id && view.id === d?.conversation_id && !view.gone)
    liveSoon('thv-run:' + view.key, () => thvRunRead(view), 120);
});
// A message waiting behind the bot's other run shows Stop while that run goes on.
liveOn('bots', d => { for (const view of THV_VIEWS) if (view.composer?.slug === d?.bot && !view.gone) thvStopSync(view); });
liveOn('reset', () => { for (const view of THV_VIEWS) if (view.goals && !view.gone) void thvRunRead(view); });

// ----------------------------------------------------------------- the composer
function thvComposerDraw(view) {
  const host = view.el.querySelector('.thv-composer');
  const can = thvCanPost(view) && !['loading', 'gone', 'error'].includes(view.state);
  // Hidden, not removed, while posting is closed (archived, loading): unarchiving shows it again with its draft.
  if (!can) { host.hidden = true; return; }
  host.hidden = false;
  if (view.composer) { thvReplyingDraw(view); thvComposerLabel(view); return; }
  // One line, as the bot chat's pill was: (a bot's Home or DM: the bot, on a phone) the words, Attach, the ways to send
  // and Send, which Stop replaces while the bot runs. `pill-thread` keeps the task question card's frame (.ask,
  // ui/styles/task-files.css) off it. A bot's composer takes the old chat's "/" commands (slash-commands.js) through the
  // hooks below. `ways`: Send shows the person's kept way and every message names it; Set as goal is for one goal
  // (chat-goal.js reads it too).
  const bot = view.opts.goals || null;
  const C = view.composer = {files: [], lib: [], mention: null, slug: bot, thread: true, running: null, stopping: false, ways: !!bot,
    label: () => thvComposerLabel(view), buttons: () => thvComposerButtons(view), goalsOf: () => view.goals,
    clear: () => thvComposerClear(view), command: text => thvComposerCommand(view, text), task: text => thvComposerTask(view, text),
    newChat: view.opts.dm ? () => thvNewDm(view) : null};
  // Above the box: the Ask's question (ask-panel.js askPeekDock) and the message being answered, each its own row.
  host.innerHTML = `<div class="thv-peek-slot"></div><div class="thv-replying" hidden></div><div class="ask pill-thread thv-ask"><div class="p-pill">
      ${bot ? pillBotHTML(bot) : ''}
      ${bot ? '<span class="thv-goal-chip" hidden>Goal<button type="button" aria-label="Leave goal" title="Leave goal">×</button></span>' : ''}
      <textarea class="p-text" rows="1" autocorrect="on" autocapitalize="sentences" spellcheck="true" aria-label="Message" placeholder="Message"></textarea>
      <span class="thv-attach-wrap"><button class="p-attach p-icon" type="button" title="Attach" aria-label="Attach" aria-haspopup="menu" aria-expanded="false">${ICON_CLIP}</button>
        <div class="p-menu thv-attach-menu" role="menu" hidden><button type="button" role="menuitem" data-attach="upload">Upload</button><button type="button" role="menuitem" data-attach="library">Library file</button></div></span>
      <input type="file" multiple hidden class="p-file">
      ${bot ? `<button class="p-stop p-send-icon" type="button" aria-label="Stop" title="Stop" hidden>${ICON_STOP}</button>
      <span class="p-sendbox"><button class="p-way p-icon" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="Ways to send" title="Ways to send"><span class="nav-icon" aria-hidden="true">expand_more</span></button>` : ''}
      <button class="p-send p-send-icon" type="button" aria-label="Send" title="Send">${ICON_SEND}</button>${bot ? `
      <div class="p-menu p-ways" role="menu" aria-label="Ways to send" hidden></div></span>` : ''}</div>
    <div class="p-chips"></div>
    ${bot ? '<div class="p-cmd" hidden></div>' : ''}
    <div class="p-menu thv-mentions" role="listbox" aria-label="Mention" hidden></div>
    <div class="thv-lib" hidden></div></div>`;
  C.el = host.querySelector('.thv-ask');
  host.querySelector('.thv-replying').onclick = ev => { if (ev.target.closest('[data-reply-cancel]')) thvReplyStart(view, null); };
  const box = C.el.querySelector('.p-text'), file = C.el.querySelector('.p-file'), menu = C.el.querySelector('.thv-attach-menu');
  const grow = () => { box.style.height = 'auto'; box.style.height = Math.min(box.scrollHeight, 160) + 'px'; };
  box.addEventListener('input', () => { grow(); thvMentionFind(view); thvComposerButtons(view); });
  box.addEventListener('keydown', ev => {
    if (C.mention && thvMentionKey(view, ev)) return;
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing && !touchKeyboard()) { ev.preventDefault(); void thvComposerSend(view); }
  });
  box.addEventListener('blur', () => setTimeout(() => thvMentionClose(view), 150));
  C.el.querySelector('.p-send').onclick = () => void thvComposerSend(view);
  if (bot) {
    pillBotWire(C.el.querySelector('.p-bot'), bot);
    C.el.querySelector('.p-stop').onclick = () => void pillStop(C);
    slashAttach(C);
    thvWaysWire(view);
  }
  C.el.querySelector('.p-attach').onclick = ev => {
    const open = menu.hidden;
    menu.hidden = !open; ev.currentTarget.setAttribute('aria-expanded', String(open));
  };
  menu.onclick = ev => {
    const b = ev.target.closest('[data-attach]'); if (!b) return;
    menu.hidden = true; C.el.querySelector('.p-attach').setAttribute('aria-expanded', 'false');
    if (b.dataset.attach === 'upload') file.click(); else void thvLibOpen(view);
  };
  file.onchange = () => { C.files.push(...file.files); file.value = ''; thvChips(view); };
  C.el.querySelector('.p-chips').onclick = ev => {
    const rm = ev.target.closest('[data-rm]'); if (!rm) return;
    ev.preventDefault();
    const [kind, i] = rm.dataset.rm.split(':');
    (kind === 'lib' ? C.lib : C.files).splice(Number(i), 1);
    thvChips(view);
  };
  thvDropZone(view);
  const draft = THV_DRAFTS.get(thvDraftKey(view));
  if (draft) { box.value = draft.text; C.files.push(...draft.files); C.lib.push(...draft.lib); thvChips(view); requestAnimationFrame(grow); }
  if (view.goals) chatGoalRender(view.goals);
  thvComposerLabel(view);
  thvReplyingDraw(view);
  thvStopSync(view);
  window.askPeekDock?.();
}
// Send says what it will do: in a bot's Home or DM the way's icon (pill.js sendWayPaint), the target in goal mode,
// with the Goal chip and the empty box saying so too; elsewhere the arrow. An empty box only dims it.
function thvComposerLabel(view) {
  const C = view.composer, btn = C?.el.querySelector('.p-send'), box = C?.el.querySelector('.p-text');
  if (!btn) return;
  const goal = chatGoalThreadMode(C);
  if (C.ways) sendWayPaint(btn, thvWayMode(view));
  else if (!btn.disabled) { btn.setAttribute('aria-label', 'Send'); btn.title = 'Send'; }
  const chip = C.el.querySelector('.thv-goal-chip');
  if (chip) chip.hidden = !goal;
  box.placeholder = goal ? `Goal for ${botDisplayName(C.slug)}…` : thvPlaceholder(view);
  thvComposerButtons(view);
}
// Who the words go to: the bot in its Home or DM, the thread by its name, the people in a DM.
function thvPlaceholder(view) {
  const t = view.thread, slug = view.opts.goals;
  if (slug) return 'Message ' + botDisplayName(slug);
  if (t && threadOnRail(t)) return 'Message #' + threadTitle(t);
  const name = t ? threadTitle(t) : view.opts.dm ? actorName(view.opts.dm) : '';
  return name ? 'Message ' + name : 'Message';
}
// The message being answered, above the box: who wrote it, its start, and × to answer nothing.
function thvReplyingDraw(view) {
  const row = view.el.querySelector('.thv-replying'); if (!row) return;
  const m = view.replyTo && view.messages.find(x => x.id === view.replyTo);
  if (!m) { view.replyTo = null; row.hidden = true; row.textContent = ''; delete row.dataset.mid; return; }
  const q = thvQuoteFrom(m);
  const html = `<span class="thv-replying-q">${THV_SVG.quote}<b>${esc(q.author_name || 'Message')}</b><span class="th-quote-text">${esc(q.excerpt || '')}</span></span>
    <button type="button" class="ghost thv-replying-x" data-reply-cancel aria-label="Cancel reply" title="Cancel reply">×</button>`;
  if (row.dataset.mid !== m.id) { row.innerHTML = html; row.dataset.mid = m.id; }
  row.hidden = false;
}
function thvReplyStart(view, m) {
  view.replyTo = m?.id || null;
  thvReplyingDraw(view);
  if (m) view.composer?.el.querySelector('.p-text')?.focus();
}
function thvComposerButtons(view) {
  const C = view.composer, btn = C?.el.querySelector('.p-send'), box = C?.el.querySelector('.p-text');
  if (!btn) return;
  const empty = !box.value.trim() && !C.files.length && !C.lib.length;
  btn.classList.toggle('p-off', empty);
  // While the bot runs, Stop takes Send's place (and the caret's) until something is typed: a message typed meanwhile
  // still goes to it.
  const stop = C.el.querySelector('.p-stop');
  if (!stop) return;
  const show = !!C.running && empty;
  stop.hidden = !show; btn.hidden = show; stop.disabled = !!C.stopping;
  const caret = C.el.querySelector('.p-way');
  if (caret) { caret.hidden = show; if (show) thvWaysMenu(view, false); }
  const says = C.stopping ? 'Stopping…' : 'Stop';
  stop.title = says; stop.setAttribute('aria-label', says);
}
// ----------------------------------------------------------------- the ways to send (a bot's Home or DM)
// Steer now, Queue, Defer with its durations, then Set as goal (deliveryMenuHTML, pill.js). Steer, Queue and Defer are
// the person's kept way (sendWayPick); Set as goal enters goal mode (chat-goal.js) for one goal and is never kept.
// Someone who may not message the bot (Write) defers with no deadline, as on a task they are not on.
// Anyone in a DM with the bot may name a way to send; elsewhere it takes the right to direct the bot.
const thvDirects = view => threadIsDm(view.thread) && !!view.thread?.me?.can_post || botByRef(view.composer?.slug)?.my_access?.write !== false;
// The way a message goes (never goal: a goal is not a message), and what Send shows (goal in goal mode).
const thvSendMode = view => thvDirects(view) ? SEND_WAY : 'defer:never';
const thvWayMode = view => chatGoalThreadMode(view.composer) ? 'goal' : thvSendMode(view);
const thvGoalOffered = view => !!view.composer?.goalMode || !!view.goals?.goalSupported && !goalPinned(view.goals.goal);
function thvWaysMenu(view, open) {
  const C = view.composer, menu = C?.el.querySelector('.p-ways'), caret = C?.el.querySelector('.p-way');
  if (!menu || !caret) return;
  if (!open && menu.hidden) return;
  const directs = thvDirects(view), current = thvWayMode(view);
  menu.innerHTML = open ? deliveryMenuHTML({steer: directs, queue: directs, goal: thvGoalOffered(view), deadlines: directs,
    current, deferFor: deliveryWay(current) === 'defer' ? deliveryFor(current) : 'never'}) : '';
  menu.hidden = !open;
  caret.setAttribute('aria-expanded', String(!!open));
  // On a phone the keyboard stays up: focus stays in the box.
  if (open && !touchKeyboard()) (menu.querySelector('[aria-checked="true"]') || menu.querySelector('button'))?.focus();
}
function thvWayPick(view, mode) {
  const C = view.composer;
  thvWaysMenu(view, false);
  if (mode === 'goal') { if (!C.goalMode) chatGoalButton(view.goals); }
  else {
    if (C.goalMode) chatGoalButton(view.goals);      // a way to send leaves goal mode
    sendWayPick(mode);
  }
  thvComposerLabel(view);
  if (!touchKeyboard()) C.el.querySelector('.p-text')?.focus();
}
function thvWaysWire(view) {
  const C = view.composer, q = sel => C.el.querySelector(sel), menu = q('.p-ways'), caret = q('.p-way');
  // The caret, a right-click or a long press on Send open the menu.
  caret.onclick = () => thvWaysMenu(view, menu.hidden);
  sendWayHold(q('.p-send'), () => thvWaysMenu(view, true), () => !q('.p-send').disabled);
  menu.onclick = ev => { const b = ev.target.closest?.('[data-deliver]'); if (b) thvWayPick(view, b.dataset.deliver); };
  menu.addEventListener('keydown', ev => deliveryMenuKeys(menu, ev));
  // Escape closes the menu only, wherever focus is in the composer.
  C.el.addEventListener('keydown', ev => {
    if (ev.key !== 'Escape' || menu.hidden) return;
    ev.preventDefault(); ev.stopPropagation();
    thvWaysMenu(view, false);
    if (!touchKeyboard()) caret.focus();
  }, true);
  q('.thv-goal-chip button').onclick = () => { if (C.goalMode) chatGoalButton(view.goals); q('.p-text').focus(); };
}
// A press outside an open menu closes it; a way picked anywhere repaints every bot composer on screen.
document.addEventListener('pointerdown', ev => {
  for (const v of THV_VIEWS) {
    const box = v.composer?.el.querySelector('.p-sendbox');
    if (box && !box.querySelector('.p-ways').hidden && !box.contains(ev.target)) thvWaysMenu(v, false);
  }
});
SEND_WAY_PAINT.add(() => { for (const v of THV_VIEWS) if (v.composer?.ways && !v.gone) thvComposerLabel(v); });
// ----------------------------------------------------------------- Stop
// The newest run in a bot's Home or DM, as the old chat read it (its conversation snapshot's execution: a thread id is a
// conversation id, docs/dev/threads-contract.md); Stop shows while it runs (chat.js chatRunning).
async function thvRunRead(view) {
  if (!view.goals || !S.me?.cloud) return;
  const id = view.id;
  const d = id ? await v2Get(`/v2/conversations/${encodeURIComponent(id)}/snapshot?limit=1`, {fresh: true}) : null;
  if (view.gone || view.id !== id) return;
  view.execution = d?.execution || null;
  thvStopSync(view);
}
function thvStopSync(view) {
  const C = view.composer;
  if (!C?.slug) return;
  const running = chatRunning(view.execution, C.slug);
  if (!running === !C.running && running?.run_id === C.running?.run_id) return;
  C.running = running; C.stopping = false;
  thvComposerButtons(view);
}
// ----------------------------------------------------------------- "/" commands (slash-commands.js slashRun)
function thvComposerClear(view) {
  const box = view.composer.el.querySelector('.p-text');
  box.value = ''; box.style.height = '';
  THV_DRAFTS.delete(thvDraftKey(view));
  thvComposerButtons(view);
}
// An empty DM is made first, as its first message would make it.
async function thvDmMake(view) {
  if (view.id || !view.opts.dm) return !!view.id;
  const made = await post('/v2/threads/dm', {members: [view.opts.dm]});
  const t = made?.thread || made;
  if (t?.id && !view.gone) { view.thread = t; thvSetId(view, t.id); }
  return !!view.id;
}
// A harness's own command (/compact, a repository's skill) goes to the bot as the old chat sent it: a message marked
// as a command, which its harness runs as typed (backend/models.py MessageCreate.command).
async function thvComposerCommand(view, text) {
  const C = view.composer;
  try {
    if (!await thvDmMake(view)) return;
    const r = await post('/v2/messages', {to: 'bot:' + C.slug, text, conversation_id: view.id, command: true});
    if (C.el.querySelector('.p-text').value.trim() === text) thvComposerClear(view);
    if (r?.held) void thvHoldsLoad(view); else void thvRefresh(view);
  } catch (e) { toast(`Not sent: ${e.message}`, true); }
}
// /task: a task for this bot, with the files attached here (bot-tasks.js v2PillTask).
async function thvComposerTask(view, text) {
  const C = view.composer, slug = C.slug, files = C.files.slice();
  try {
    await cloudCompose('/v2/tasks', {title: text.split('\n')[0].trim().slice(0, 80), body: text, owner: slug}, files);
    C.files = C.files.filter(f => !files.includes(f));
    thvComposerClear(view); thvChips(view);
    toast(`Task for ${empName(slug)} created`);
    await v2Refresh();
    if (BOT?.slug === slug) void loadBotTasksV2(slug);
  } catch (e) { toast(e.message, true); }
}
// /new in a DM: the DM is archived and the next message starts a fresh one, as the old chat's New chat did.
async function thvNewDm(view) {
  if (!view.id) return;
  try { await patch(thvPath(view.id), {archived: true}); }
  catch (e) { toast(e.message, true); return; }
  if (view.gone) return;
  view.follow?.(); view.follow = null;
  Object.assign(view, {id: '', thread: null, messages: [], pending: [], next: null, execution: null, state: 'loading'});
  if (view.goals) { view.goals.conv = null; view.goals.goal = null; }
  thvStopSync(view);
  await thvLoad(view);
}
function thvDropZone(view) {
  const zone = view.el;
  zone.addEventListener('dragover', ev => { if (draggingFiles(ev) && view.composer) { ev.preventDefault(); ev.dataTransfer.dropEffect = 'copy'; } });
  zone.addEventListener('drop', ev => {
    if (!draggingFiles(ev) || !view.composer) return;
    ev.preventDefault();
    view.composer.files.push(...ev.dataTransfer.files);
    thvChips(view);
  });
}
function thvChips(view) {
  const C = view.composer; if (!C) return;
  C.el.querySelector('.p-chips').innerHTML = [
    ...C.files.map((f, i) => `<span>${esc(f.name)} <a href="#" data-rm="file:${i}" aria-label="Remove ${esc(f.name)}">×</a></span>`),
    ...C.lib.map((f, i) => `<span>${esc(f.title)} <a href="#" data-rm="lib:${i}" aria-label="Remove ${esc(f.title)}">×</a></span>`)].join('');
  thvComposerLabel(view);   // a file sends as a message, so goal mode's "Set goal" waits until it is gone
}
// Library files: the newest the viewer may read, filtered as they type.
async function thvLibOpen(view) {
  const C = view.composer, pane = C.el.querySelector('.thv-lib');
  pane.hidden = false;
  pane.innerHTML = '<input type="search" class="thv-lib-q" placeholder="Find a file" aria-label="Find a file"><div class="thv-lib-list"><div class="muted">Loading…</div></div><button type="button" class="ghost small" data-lib-close>Close</button>';
  const q = pane.querySelector('.thv-lib-q'), list = pane.querySelector('.thv-lib-list');
  pane.querySelector('[data-lib-close]').onclick = () => { pane.hidden = true; };
  let files = [];
  try { files = ((await get('/v2/files?limit=50', {fresh: true})).files || []).filter(f => f.locator !== 'remote_link' && !f.url && !f.archived); }
  catch (e) { list.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  const draw = () => {
    const words = q.value.trim().toLowerCase();
    const shown = files.filter(f => !words || String(f.title || f.name || '').toLowerCase().includes(words)).slice(0, 20);
    list.innerHTML = shown.length ? shown.map(f => `<button type="button" class="thv-lib-row" data-lib="${esc(f.id)}">${esc(f.title || f.name || f.id)}</button>`).join('')
      : '<div class="muted">No files</div>';
  };
  q.oninput = draw; draw(); q.focus();
  list.onclick = ev => {
    const b = ev.target.closest('[data-lib]'); if (!b) return;
    const f = files.find(x => x.id === b.dataset.lib);
    if (f && !C.lib.some(x => x.id === f.id)) C.lib.push({id: f.id, title: f.title || f.name || f.id});
    pane.hidden = true; thvChips(view);
    C.el.querySelector('.p-text').focus();
  };
}

// ----------------------------------------------------------------- mentions
// "@" then letters: the teammates whose name or handle starts so. A bot goes in as @slug, a person as their handle.
function thvMentionPeople() {
  const bots = shownEmps().filter(e => !e.hidden).map(e => ({key: 'bot:' + e.id, label: e.display_name || e.name, handle: e.name, kind: 'bot'}));
  const people = (S.people || []).filter(p => p.id && !p.hidden && p.id !== S.me?.id)
    .map(p => ({key: 'human:' + p.id, label: p.name || p.id, handle: personHandle(p.email || p.id), kind: 'person'}));
  return [...people, ...bots];
}
function thvMentionFind(view) {
  const C = view.composer, box = C.el.querySelector('.p-text');
  const before = box.value.slice(0, box.selectionStart || 0), hit = before.match(/(^|\s)@([\w.-]*)$/);
  if (!hit) { thvMentionClose(view); return; }
  const word = hit[2].toLowerCase();
  const matches = thvMentionPeople().filter(p => !word || p.handle.toLowerCase().startsWith(word)
    || p.label.toLowerCase().split(/\s+/).some(w => w.startsWith(word))).slice(0, 6);
  if (!matches.length) { thvMentionClose(view); return; }
  C.mention = {start: before.length - hit[2].length - 1, matches, at: 0};
  thvMentionDraw(view);
}
function thvMentionDraw(view) {
  const C = view.composer, list = C.el.querySelector('.thv-mentions');
  list.hidden = false;
  list.innerHTML = C.mention.matches.map((p, i) => `<button type="button" role="option" aria-selected="${i === C.mention.at}" data-mention="${i}">${actorAvatar(p.key, 18)}<span>${esc(p.label)}</span><span class="muted">@${esc(p.handle)}</span></button>`).join('');
  list.onmousedown = ev => ev.preventDefault();
  list.onclick = ev => { const b = ev.target.closest('[data-mention]'); if (b) thvMentionPick(view, Number(b.dataset.mention)); };
}
function thvMentionKey(view, ev) {
  const M = view.composer.mention;
  if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
    ev.preventDefault();
    M.at = (M.at + (ev.key === 'ArrowDown' ? 1 : -1) + M.matches.length) % M.matches.length;
    thvMentionDraw(view); return true;
  }
  if (ev.key === 'Enter' || ev.key === 'Tab') { ev.preventDefault(); thvMentionPick(view, M.at); return true; }
  if (ev.key === 'Escape') { ev.preventDefault(); thvMentionClose(view); return true; }
  return false;
}
function thvMentionPick(view, i) {
  const C = view.composer, box = C.el.querySelector('.p-text'), M = C.mention, p = M?.matches[i];
  if (!p) return;
  const end = box.selectionStart || 0, text = `@${p.handle} `;
  box.value = box.value.slice(0, M.start) + text + box.value.slice(end);
  const caret = M.start + text.length;
  box.setSelectionRange(caret, caret);
  thvMentionClose(view);
  box.focus();
}
function thvMentionClose(view) {
  const C = view.composer; if (!C) return;
  C.mention = null;
  const list = C.el.querySelector('.thv-mentions'); if (list) list.hidden = true;
}

// ----------------------------------------------------------------- sending
async function thvComposerSend(view) {
  const C = view.composer, box = C.el.querySelector('.p-text');
  const text = box.value.trim();
  // Goal mode: the words are the bot's goal for this thread (POST /v2/conversations/<thread>/goal), not a message.
  if (chatGoalThreadMode(C)) { if (await chatGoalSend(C, text)) THV_DRAFTS.delete(thvDraftKey(view)); return; }
  if (!text && !C.files.length && !C.lib.length) { box.focus(); return; }
  // A bot's "/" commands, as the old chat ran them: Tico's own here, a harness's to the bot, other words as a message.
  if (C.slug && text) {
    const slash = slashRun(C, text);
    if (slash === true) return;
    if (slash !== 'plain' && runCommand(text, C.el.querySelector('.p-cmd'), C.slug)) { sessionAcknowledge(box); thvComposerClear(view); return; }
  }
  // In a bot's Home or DM every message names the kept way (thread posts take delivery, backend/thread_api.py); in
  // goal mode with a file attached it goes as a message, that way too.
  const way = C.ways ? deliveryBody(thvSendMode(view)) : null;
  const files = C.files.splice(0), lib = C.lib.splice(0);
  const replyTo = view.messages.find(x => x.id === view.replyTo) || null;
  box.value = ''; box.style.height = ''; thvChips(view);
  THV_DRAFTS.delete(thvDraftKey(view));
  thvReplyStart(view, null);
  await thvSend(view, {text, files, lib, way, replyTo});
}
// One message out: a "Checking…" row while the rules check runs, then the message, or a held row the author can
// edit, discard or (when the server allows) send anyway. A reply goes to this thread too, naming what it answers.
async function thvSend(view, {text, files = [], lib = [], way = null, replyTo = null}) {
  const p = {key: 'p' + (++THV_N), text, files, lib, replyTo: replyTo?.id || '', quote: replyTo ? thvQuoteFrom(replyTo) : null, state: 'checking'};
  view.pending.push(p);
  thvDraw(view, {stick: true});
  const refs = lib.length ? {files: lib.map(f => ({id: f.id, title: f.title}))} : null;
  const body = {text, ...(refs ? {refs} : {}), ...(replyTo ? {in_reply_to: replyTo.id} : {}), ...(way || {})};
  try {
    let r;
    // An upload goes to a thread, so a first message with files makes the DM before it sends.
    if (!view.id && view.opts.dm && files.length) {
      const made = await post('/v2/threads/dm', {members: [view.opts.dm]});
      const t = made?.thread || made;
      if (t?.id) { view.thread = t; thvSetId(view, t.id); }
    }
    if (!view.id && view.opts.dm) {
      r = await post('/v2/threads/dm', {members: [view.opts.dm], body: text, ...(refs ? {refs} : {}), ...(way || {})});
      const t = r?.thread || r;
      if (t?.id) { view.thread = t; thvSetId(view, t.id); }
    } else {
      r = files.length ? await thvUpload(thvPath(view.id).replace('/v2/', '/v2/uploads/') + '/messages', body, files)
        : await post(thvPath(view.id) + '/messages', body);
    }
    if (r?.held) thvHeld(view, p, r.held);
    else {
      view.pending = view.pending.filter(x => x !== p);
      const m = r?.message || (r?.id && r?.seq !== undefined ? r : null);
      if (m) thvApplyMessage(view, {id: m.id, thread_id: m.thread_id, root_id: m.root_id, message: {...(replyTo && !m.in_reply_to ? {in_reply_to: replyTo.id} : {}), ...m}});
      else void thvRefresh(view);
      for (const w of r?.warnings || []) if (w.rule === 'mention_outside') toast(`${actorName(w.actor)} is not in this thread`, true);
      // Without the live stream, the bots' status is read once now; with it, `bot_status` events keep it.
      if (Array.isArray(view.botStatus) && !liveConnected()) void thvStatusRead(view);
    }
  } catch (e) {
    p.state = 'failed'; p.error = e.message;
  }
  thvDraw(view, {stick: true});
}
function thvHeld(view, p, held) {
  p.state = 'held'; p.held = held;
  if (held.id) get('/v2/rule-holds/' + encodeURIComponent(held.id), {fresh: true})
    .then(h => { if (h?.can_send) { p.canSend = true; p.nonce = h.nonce || ''; thvDraw(view); } }).catch(() => {});
}
async function thvStatusRead(view) {
  try {
    const r = await get(thvPath(view.id) + '/bot-status', {fresh: true});
    if (view.gone || !Array.isArray(r?.bots)) return;
    view.botStatus = r.bots.slice(0, 5); thvDraw(view);
  } catch { /* the rows stay as they were */ }
}
// Files go in one multipart request with the message (the server answers with the message, or a held verdict).
async function thvUpload(path, body, files) {
  const fd = new FormData();
  for (const [k, v] of Object.entries(body)) fd.append(k, typeof v === 'object' ? JSON.stringify(v) : v);
  files.forEach(f => fd.append('files', f, f.name));
  const response = await formFetch(API + path, fd, {});
  const result = await response.json().catch(() => ({}));
  if (!response.ok) { const err = apiFailure(result, response); err.message ||= 'The server did not take the files.'; throw err; }
  return result;
}

// ----------------------------------------------------------------- clicks
function thvWire(view) {
  const el = view.el;
  el.querySelector('.thv-older').onclick = ev => void thvOlder(view, ev.currentTarget);
  el.querySelector('.thv-unread').onclick = ev => {
    el.querySelector('.th-new')?.scrollIntoView({block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
    ev.currentTarget.hidden = true;
  };
  el.querySelector('.thv-scroll').addEventListener('scroll', () => {
    const pill = el.querySelector('.thv-unread'), first = el.querySelector('.th-new');
    if (!pill.hidden && first && thvVisible(el.querySelector('.thv-scroll'), first)) pill.hidden = true;
  }, {passive: true});
  el.addEventListener('click', ev => {
    // The long press's own click, just after the lift, is not a tap on what it lifted from.
    if (view.pressQuiet && Date.now() < view.pressQuiet) { ev.preventDefault(); ev.stopPropagation(); view.pressQuiet = 0; return; }
    void thvClick(view, ev);
  }, true);
  thvPressWire(view);
  // The bar outside the view is shared by the views that take turns in it: only the one on screen answers.
  if (view.opts.actsHost) view.opts.actsHost.onclick = ev => { if (!view.gone) void thvClick(view, ev); };
  // A turn's steps load when their fold opens; open folds stay open across redraws (ui/app/chat.js).
  el.addEventListener('toggle', ev => {
    if (ev.target.matches?.('details.run-steps')) void v2StepsOpen(view.run, ev.target, el);
    const fold = ev.target.dataset?.fold;
    if (fold) view.run.openFolds[ev.target.open ? 'add' : 'delete'](fold);
  }, true);
}
const thvMsgOf = (view, row) => view.messages.find(m => m.id === row?.dataset.mid);
async function thvClick(view, ev) {
  const t = ev.target, row = t.closest('.th-msg');
  const m = thvMsgOf(view, row);
  if (t.closest('[data-th-file]')) { const b = t.closest('[data-th-file]'); void openFileLink(b.dataset.thFile, b.dataset.name); return; }
  if (t.closest('[data-thv-members]')) { threadMembersOpen(view); return; }
  if (t.closest('[data-thv-pin]')) { void thvPinToggle(view, t.closest('[data-thv-pin]')); return; }
  if (t.closest('[data-call]')) { void callAction(view, t.closest('[data-call]').dataset.call); return; }
  if (t.closest('[data-thv-menu]')) { threadMenuOpen(view, t.closest('[data-thv-menu]')); return; }
  if (t.closest('[data-th-earlier]')) {
    const b = t.closest('[data-th-earlier]'); b.disabled = true;
    try { await threadEarlierOpen(view, b.dataset.thEarlier); }
    catch (e) { toast(e.message, true); b.disabled = false; }
    return;
  }
  if (t.closest('[data-th-quote]')) { void thvGoTo(view, t.closest('[data-th-quote]').dataset.thQuote); return; }
  if (t.closest('[data-th-stop]')) {
    const b = t.closest('[data-th-stop]'); b.disabled = true;
    try { await post(`/v2/bots/${encodeURIComponent(b.dataset.thStop)}/stop`, b.dataset.run ? {run_id: b.dataset.run} : {}); }
    catch (e) { toast(`Not stopped: ${e.message}`, true); b.disabled = false; }
    return;
  }
  if (t.closest('[data-th-react]') && m) { await thvReact(view, m, t.closest('[data-th-react]').dataset.thReact); return; }
  if (t.closest('[data-th-pick]') && m) { thvEmojiPick(view, m, t.closest('[data-th-pick]')); return; }
  if (t.closest('[data-th-reply]') && m) { thvReplyStart(view, m); return; }
  if (t.closest('[data-th-copy]') && m) { thvCopy(m); return; }
  const pend = t.closest('[data-pending]');
  if (pend) {
    const p = view.pending.find(x => x.key === pend.dataset.pending); if (!p) return;
    // Edit and Discard both take a held write back: nothing is sent, and the review it waited on closes.
    if (p.held?.id && t.closest('[data-held-drop], [data-held-edit]'))
      post('/v2/rule-holds/' + encodeURIComponent(p.held.id) + '/withdraw', {}).catch(() => {});
    if (t.closest('[data-held-drop]')) { view.pending = view.pending.filter(x => x !== p); thvDraw(view); return; }
    if (t.closest('[data-held-edit]')) {
      view.pending = view.pending.filter(x => x !== p);
      thvDraw(view);
      const C = view.composer, box = C?.el.querySelector('.p-text');
      if (p.replyTo && view.messages.some(x => x.id === p.replyTo)) thvReplyStart(view, view.messages.find(x => x.id === p.replyTo));
      if (box) { box.value = p.text; box.focus(); }
      if (C) { C.files.push(...(p.files || [])); C.lib.push(...(p.lib || [])); thvChips(view); }
      return;
    }
    if (t.closest('[data-held-send]')) {
      const b = t.closest('[data-held-send]'); b.disabled = true;
      try {
        await post('/v2/rule-holds/' + encodeURIComponent(p.held.id) + '/send', {nonce: p.nonce || ''});
        view.pending = view.pending.filter(x => x !== p);
        await thvRefresh(view);
      } catch (e) { toast(e.message, true); b.disabled = false; }
    }
  }
}
const thvCopy = m => void copyText(m.body || '').then(() => toast('Message copied'), () => toast('Could not copy the message', true));
async function thvReact(view, m, emoji) {
  const before = (m.reactions || []).map(r => ({...r, actors: [...(r.actors || [])]}));
  const me = myActor(), r = before.find(x => x.emoji === emoji), on = !thvReactedMine(r);
  // Shown at once; the server's answer (the message's reactions, as a list) settles it.
  m.reactions = r ? before.map(x => x.emoji !== emoji ? x : {...x, count: Math.max(0, x.count + (on ? 1 : -1)),
                                                                 actors: on ? [...x.actors, me] : x.actors.filter(a => a !== me)})
    : [...before, {emoji, count: 1, actors: [me]}];
  thvDraw(view);
  try {
    const path = '/v2/messages/' + encodeURIComponent(m.id) + '/reactions/' + encodeURIComponent(emoji);
    const back = on ? await put(path, {}) : await writeRequest('DELETE', path);
    const fresh = Array.isArray(back) ? back : back?.reactions || back?.message?.reactions;
    if (Array.isArray(fresh)) { m.reactions = fresh; thvDraw(view); }
  } catch (e) {
    m.reactions = before;
    thvDraw(view); toast(e.message, true);
  }
}
// The emoji to pick from: six common ones, then any already used in this thread.
function thvEmojiChoices(view) {
  const used = new Set(THV_EMOJI);
  for (const x of view.messages) for (const r of x.reactions || []) if (r?.emoji && r.count > 0) used.add(r.emoji);
  return [...used].slice(0, 18);
}
function thvEmojiPick(view, m, button) {
  thvMenuClose();
  thvMenuShow(button, thvEmojiChoices(view).map(e => [e, e]), emoji => thvReact(view, m, emoji), 'thv-emoji');
}

// ---- the long press, on touch: React, Reply and Copy as one sheet (and 👍 and 👎 on a bot's words)
const THV_PRESS_MS = 450;
function thvPressWire(view) {
  const el = view.el.querySelector('.thv-list');
  let press = null;
  const end = () => { if (press) { clearTimeout(press.timer); press = null; } };
  el.addEventListener('pointerdown', ev => {
    if (ev.pointerType !== 'touch' || !ev.isPrimary) return;
    const row = ev.target.closest('.th-msg[data-mid]');
    if (!row || ev.target.closest('a, button, input, textarea, select, summary, details, [contenteditable]')) return;
    end();
    press = {x: ev.clientX, y: ev.clientY, timer: setTimeout(() => {
      press = null;
      const m = thvMsgOf(view, row);
      if (!m || m.deleted) return;
      view.pressQuiet = Date.now() + 60000;   // until the finger lifts, however long it is held
      getSelection?.()?.removeAllRanges();
      navigator.vibrate?.(8);
      thvSheet(view, m);
    }, THV_PRESS_MS)};
  });
  el.addEventListener('pointermove', ev => { if (press && Math.hypot(ev.clientX - press.x, ev.clientY - press.y) > 10) end(); });
  for (const type of ['pointerup', 'pointercancel']) el.addEventListener(type, () => { end(); if (view.pressQuiet > Date.now() + 1000) view.pressQuiet = Date.now() + 400; });
  el.addEventListener('scroll', end, {passive: true});
  // The browser's own long-press menu would cancel the press before it is one.
  el.addEventListener('contextmenu', ev => { if ((press || view.pressQuiet > Date.now()) && ev.target.closest('.th-msg')) ev.preventDefault(); });
}
function thvSheet(view, m) {
  const d = fbDialog('fb-menu thv-sheet', 'Message actions');
  const key = thvBot(m.sender_id) ? fbKey('message', m.id) : '', mine = key ? fbMine(key)?.rating || '' : '';
  d.innerHTML = `<div class="thv-sheet-emoji" role="group" aria-label="React">${thvEmojiChoices(view).slice(0, 6).map(e =>
      `<button type="button" data-pick-emoji="${esc(e)}" aria-label="${esc(e)}">${esc(e)}</button>`).join('')}</div>
    <div class="fb-menu-row">${thvCanPost(view) ? `<button type="button" data-pick="reply" aria-label="Reply">${THV_SVG.reply}</button>` : ''}${
      m.body ? `<button type="button" data-pick="copy" aria-label="Copy">${THV_SVG.copy}</button>` : ''}${key ? `
      <button type="button" data-pick="up" aria-pressed="${mine === 'up'}" aria-label="Good">${FB_ICON.up}</button>
      <button type="button" data-pick="down" aria-pressed="${mine === 'down'}" aria-label="Not good">${FB_ICON.down}</button>` : ''}</div>`;
  // The click the lift makes lands on the backdrop: it is not a tap to close.
  d.addEventListener('click', ev => { if (view.pressQuiet > Date.now()) { ev.stopImmediatePropagation(); view.pressQuiet = 0; } }, true);
  d.showModal();
  d.onclick = ev => {
    const e = ev.target.closest('[data-pick-emoji]'), b = ev.target.closest('[data-pick]');
    if (!e && !b) return;
    d.close();
    if (e) { void thvReact(view, m, e.dataset.pickEmoji); return; }
    const k = b.dataset.pick;
    if (k === 'reply') thvReplyStart(view, m);
    else if (k === 'copy') thvCopy(m);
    else if (k === 'up') void fbUp(key);
    else if (mine === 'down') fbShow(key); else void fbDown(key);
  };
  d.querySelector('button')?.focus();
}
// One small menu at a time, under the button that opened it; Escape or a click elsewhere closes it.
let THV_MENU = null;
function thvMenuShow(button, items, pick, cls = '') {
  const menu = document.createElement('div');
  menu.className = 'p-menu thv-pop' + (cls ? ' ' + cls : ''); menu.setAttribute('role', 'menu');
  // A third value makes a choice among radios: true on the one in force.
  menu.innerHTML = items.map(([k, label, on]) => `<button type="button" role="${on === undefined ? 'menuitem' : 'menuitemradio'}"${on === undefined ? '' : ` aria-checked="${on}"`} data-k="${esc(k)}">${esc(label)}</button>`).join('');
  document.body.append(menu);
  const r = button.getBoundingClientRect();
  menu.style.top = Math.min(innerHeight - menu.offsetHeight - 8, r.bottom + 4) + 'px';
  menu.style.left = Math.max(8, Math.min(innerWidth - menu.offsetWidth - 8, r.right - menu.offsetWidth)) + 'px';
  button.setAttribute('aria-expanded', 'true');
  THV_MENU = {menu, button};
  menu.onclick = ev => { const b = ev.target.closest('[data-k]'); if (!b) return; thvMenuClose(); void pick(b.dataset.k); };
  menu.querySelector('button')?.focus();
}
function thvMenuClose() {
  if (!THV_MENU) return;
  THV_MENU.menu.remove(); THV_MENU.button.setAttribute('aria-expanded', 'false');
  THV_MENU = null;
}
document.addEventListener('click', ev => { if (THV_MENU && !THV_MENU.menu.contains(ev.target) && !THV_MENU.button.contains(ev.target)) thvMenuClose(); }, true);
document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && THV_MENU) { THV_MENU.button.focus(); thvMenuClose(); } });
