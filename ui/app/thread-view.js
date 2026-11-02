/* ui/app/thread-view.js — One thread on screen: its timeline in tree order (each message followed by its sub-thread,
   indented and quieter, the indent capped at three), its composer, and its live updates. Mounted by the Thread page
   (#/thread/<id>), a bot's Home and DM, a person's Home and a team's Home. Shapes: docs/dev/threads-contract.md §10.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const THV_VIEWS = new Set();
const THV_DEPTH_MAX = 3;
// The server's depth is the place in the whole tree (a sub-thread's own messages are 1); on screen it counts from the
// thread shown, capped.
const thvDepth = (view, d) => Math.max(0, Math.min((Number(d) || 0) - (view.base || 0), THV_DEPTH_MAX));
const THV_ACK = '✅';
let THV_N = 0;
const thvPath = id => '/v2/threads/' + encodeURIComponent(id);
const thvHuman = a => String(a || '').startsWith('human:');
const thvMine = m => !!m && m.from_actor === myActor();
// Addressed to the viewer: written by a person, sent to them, or naming them. Other bot posts are activity.
const thvAddressed = m => thvHuman(m?.from_actor) || m?.to_actor === myActor() || (m?.mentions || []).includes(myActor());

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
// hides the card (`hideGone`) from a viewer who can't post either. {focus: message id} shows that message.
function threadMount(host, opts = {}) {
  const view = {key: ++THV_N, host, opts, id: '', thread: null, breadcrumb: [], messages: [], next: null, state: 'loading',
                readAt: 0, openSeq: 0, readSent: new Map(), marks: new Map(), pending: [], replyTo: '', editing: '',
                follow: null, gone: false, error: '', run: {openSteps: new Set(), steps: {}, openFolds: new Set()}};
  THV_VIEWS.add(view);
  host.innerHTML = `<section class="thv${opts.card ? ' thv-card' : ''}" data-thv="${view.key}">
    ${opts.header === false ? '' : '<header class="thv-head"></header>'}
    <div class="thv-scroll"><button type="button" class="ghost small thv-older" hidden>Earlier</button>
      <div class="thv-list" role="log" aria-live="polite"></div></div>
    <button type="button" class="stick-pill thv-unread" hidden>Unread</button>
    <div class="thv-composer"></div></section>`;
  view.el = host.querySelector('.thv');
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
}
// Views whose host left the page stop following.
function thvSweep() { for (const v of THV_VIEWS) if (!v.host.isConnected) threadUnmount(v); }

// ----------------------------------------------------------------- loading
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
}
async function thvLoad(view) {
  try {
    const id = await thvResolve(view);
    if (view.gone) return;
    if (!id) { view.state = 'empty'; thvDraw(view); return; }
    thvSetId(view, id);
    const r = await get(thvPath(id) + '/timeline?limit=50', {fresh: true});
    if (view.gone) return;
    view.thread = r.thread || view.thread;
    view.breadcrumb = r.breadcrumb || [];
    view.messages = thvMerge(view, r.messages || [], id);
    view.next = r.next_cursor || null;
    view.readAt = Number(view.thread?.me?.last_read_seq) || 0;
    view.openSeq = thvMaxSeq(view.messages);
    thvLearnMarks(view, view.messages);
    view.state = view.messages.length ? 'ready' : 'empty';
    thvDraw(view, {stick: !view.opts.focus});
    thvMarkRead(view);
    void callLoad(view);
    void thvHoldsLoad(view);
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
// The viewer's own held writes in this thread come back after a reload, still with Edit and Discard.
async function thvHoldsLoad(view) {
  if (!view.id) return;
  try {
    const r = await get('/v2/rule-holds?thread=' + encodeURIComponent(view.id) + '&limit=20', {fresh: true});
    const mine = (r?.holds || []).filter(h => h.author === myActor() && h.state === 'held' && !view.pending.some(p => p.held?.id === h.id)).slice(0, 5);
    for (const s of mine) {
      const h = await get('/v2/rule-holds/' + encodeURIComponent(s.id), {fresh: true}).catch(() => null);
      if (!h || view.gone || view.pending.some(p => p.held?.id === h.id)) continue;
      // A held edit stays under the message it would change; the message keeps its words until the edit goes through.
      const of = h.operation === 'threads_edit' ? h.message_id || '' : '';
      view.pending.push({key: 'p' + (++THV_N), text: h.text || '', files: [], lib: [], parent: '', editOf: of,
                         depth: Number(view.messages.find(m => m.id === of)?.depth) || 0, state: 'held',
                         held: h, canSend: !!h.can_send, nonce: h.nonce || ''});
    }
    if (mine.length && !view.gone) thvDraw(view);
  } catch {}           // a server without rule holds has nothing to bring back
}
const thvMaxSeq = list => list.reduce((n, m) => Math.max(n, Number(m.seq) || 0), 0);
// Where the viewer had read each inline sub-thread when it first came on screen: what is past it shows as new, per thread.
function thvLearnMarks(view, rows) {
  for (const m of rows) if (m.sub_thread?.id && !view.marks.has(m.sub_thread.id)) view.marks.set(m.sub_thread.id, Number(m.sub_thread.last_read_seq) || 0);
}
// A page read from the server merged with what the stream already put on screen: the higher revision of each message
// wins, and newer messages the page does not have yet stay, at the bottom or under their parent.
function thvMerge(view, fresh, tid) {
  const had = new Map(view.messages.map(m => [m.id, m]));
  const out = fresh.map(f => {
    const e = had.get(f.id);
    // A row the server now hides or has deleted stays so; otherwise the later revision wins.
    if (!e || f.hidden || f.deleted || (Number(e.revision) || 0) <= (Number(f.revision) || 0) && !e.deleted) return f;
    return {...f, ...e, depth: f.depth, sub_thread: f.sub_thread || e.sub_thread};
  });
  const ids = new Set(out.map(m => m.id)), top = thvMaxSeq(out.filter(m => (m.thread_id || tid) === tid));
  for (const e of view.messages) {
    if (ids.has(e.id) || e.hidden || !e.seq) continue;
    const etid = e.thread_id || tid;
    if (etid === tid) { if (Number(e.seq) > top) { out.push(e); ids.add(e.id); } continue; }
    const pi = out.findIndex(x => x.sub_thread?.id === etid);
    if (pi < 0 || Number(e.seq) <= (Number(out[pi].sub_thread.last_seq) || 0)) continue;
    const d0 = Number(out[pi].depth) || 0;
    let end = pi;
    while (end + 1 < out.length && (Number(out[end + 1].depth) || 0) > d0) end++;
    out.splice(end + 1, 0, e); ids.add(e.id);
  }
  return out;
}
// The first page read again after a change the stream could not place; older pages already read stay above it.
async function thvRefresh(view) {
  if (view.gone || !view.id) return thvLoad(view);
  try {
    const r = await get(thvPath(view.id) + '/timeline?limit=50', {fresh: true});
    if (view.gone) return;
    const fresh = r.messages || [], ids = new Set(fresh.map(m => m.id));
    const first = fresh.find(m => (m.thread_id || view.id) === view.id)?.seq ?? Infinity;
    // Everything above the page read again stays, a branch with its message: whatever came before the page's first own row.
    const start = view.messages.findIndex(m => (m.thread_id || view.id) === view.id && !m.hidden && (Number(m.seq) || 0) >= first);
    const older = view.olderRead ? view.messages.slice(0, start < 0 ? view.messages.length : start).filter(m => !ids.has(m.id)) : [];
    view.thread = r.thread || view.thread;
    view.breadcrumb = r.breadcrumb || view.breadcrumb;
    thvLearnMarks(view, fresh);
    const olderIds = new Set(older.map(m => m.id));
    const merged = thvMerge({messages: view.messages.filter(m => !olderIds.has(m.id))}, fresh, view.id);
    view.messages = [...older, ...merged];
    if (!view.olderRead) view.next = r.next_cursor || null;
    view.state = view.messages.length ? 'ready' : 'empty';
    thvDraw(view, {stick: thvAtBottom(view)});
    thvMarkRead(view);
  } catch (e) {
    if (e?.status === 404) { view.state = 'gone'; thvDraw(view); }
  }
}
// A message to show (from search): earlier pages are read until it is there, then it is scrolled to and marked.
async function thvFocus(view, mid) {
  for (let pages = 0; !view.messages.some(m => m.id === mid) && view.next && pages < 10 && !view.gone; pages++) {
    const r = await get(thvPath(view.id) + '/timeline?limit=50&cursor=' + encodeURIComponent(view.next), {fresh: true}).catch(() => null);
    if (!r || view.gone) return;
    thvLearnMarks(view, r.messages || []);
    const have = new Set(view.messages.map(m => m.id));
    view.messages = [...(r.messages || []).filter(m => !have.has(m.id)), ...view.messages];
    view.next = r.next_cursor || null;
    view.olderRead = true;
    thvDraw(view);
  }
  view.focus = mid;
  thvDraw(view);
  const row = view.el.querySelector(`.th-msg[data-mid="${CSS.escape(mid)}"]`);
  row?.scrollIntoView({block: 'center'});
}
async function thvOlder(view, button) {
  if (!view.next) return;
  button.disabled = true;
  const box = view.el.querySelector('.thv-scroll'), from = box.scrollHeight - box.scrollTop;
  try {
    const r = await get(thvPath(view.id) + '/timeline?limit=50&cursor=' + encodeURIComponent(view.next), {fresh: true});
    thvLearnMarks(view, r.messages || []);
    const have = new Set(view.messages.map(m => m.id));
    view.messages = [...(r.messages || []).filter(m => !have.has(m.id)), ...view.messages];
    view.next = r.next_cursor || null;
    view.olderRead = true;
    thvDraw(view);
    box.scrollTop = box.scrollHeight - from;
  } catch (e) { toast(e.message, true); }
  button.disabled = false;
}
// "N more" in a long branch: the sub-thread's older messages take the row's place, and it stays while more are left.
async function thvExpand(view, button) {
  const row = view.messages.find(m => m.id === button.dataset.thExpand);
  if (!row) return;
  button.disabled = true;
  const box = view.el.querySelector('.thv-scroll'), top = box.scrollTop;
  try {
    const r = await get(thvPath(row.thread_id) + '/timeline?limit=50&cursor=' + encodeURIComponent(row.cursor), {fresh: true});
    const at = view.messages.indexOf(row);
    if (view.gone || at < 0) return;
    const have = new Set(view.messages.map(m => m.id));
    const rows = (r.messages || []).filter(m => !have.has(m.id));
    thvLearnMarks(view, rows);
    const count = row.count - rows.filter(m => m.thread_id === row.thread_id && !m.hidden && !m.deleted).length;
    view.messages.splice(at, 1, ...(r.next_cursor && count > 0 ? [{...row, count, cursor: r.next_cursor}] : []), ...rows);
    thvDraw(view);
    box.scrollTop = top;
    thvMarkRead(view);
  } catch (e) { toast(e.message, true); button.disabled = false; }
}

// ----------------------------------------------------------------- drawing
const thvCanPost = view => view.opts.canPost ?? (view.thread ? !!view.thread.me?.can_post : view.state === 'empty' && !!view.opts.dm);
function thvActsHTML(view) {
  const t = view.thread;
  return `${callHeadHTML(view)}
      ${!t.parent_id && ['members', 'personal', 'company', 'team'].includes(t.audience) ? '<button type="button" class="ghost small" data-thv-members>Members</button>' : ''}
      <button type="button" class="ghost thv-menu-btn" data-thv-menu aria-haspopup="menu" aria-expanded="false" aria-label="Thread menu" title="More"><span class="nav-icon" aria-hidden="true">more_horiz</span></button>`;
}
function thvHeadHTML(view) {
  const t = view.thread;
  if (!t) return '';
  if (view.opts.header === 'compact') return `<div class="thv-titleline thv-compact"><span class="spacer"></span>${thvActsHTML(view)}</div>`;
  const crumbs = (view.breadcrumb || []).filter(c => c.id !== t.id);
  const aud = t.audience === 'company' ? 'Company' : t.audience === 'team' ? groupName(t.audience_group_id)
    : t.audience === 'home' ? 'Home' : threadPrivate(t) ? 'Private' : '';
  return `${crumbs.length ? `<nav class="thv-crumbs" aria-label="Thread path">${crumbs.map(c =>
      `<a href="${threadHref(c.id)}">${esc(c.title || 'Thread')}</a>`).join('<span aria-hidden="true">›</span>')}</nav>` : ''}
    <div class="thv-titleline">${crumbs.length ? `<a class="thv-back" href="${threadHref(crumbs[crumbs.length - 1].id)}" aria-label="Back" title="Back">‹</a>` : ''}
      ${threadMarkHTML(t, 22)}<h1 class="thv-title">${esc(threadTitle(t))}</h1>
      ${aud ? `<span class="thv-aud muted">${esc(aud)}</span>` : ''}<span class="spacer"></span>${thvActsHTML(view)}</div>`;
}
// A message the viewer may not read is one quiet row, never its words. Earlier shared history folds into one "Earlier
// messages" row; two such rows meet where one page of it ends and the next begins.
function thvRows(view) {
  const rows = [];
  for (const m of view.messages) {
    if (m.hidden) {
      const fold = m.earlier ? 'earlier' : 'hidden', last = rows[rows.length - 1];
      if (last?.fold === fold && last.depth === (m.depth || 0)) { last.ids.push(m.id); continue; }
      rows.push({fold, depth: m.depth || 0, id: m.id, ids: [m.id]});
      continue;
    }
    if (m.collapsed) { rows.push({more: m, id: m.id}); continue; }
    rows.push({m});
  }
  return rows;
}
function thvIsNew(view, m) {
  if (!m.seq || thvMine(m)) return false;
  // Each sub-thread has its own read mark. Arriving at the bottom of the thread on screen is not news; a reply arriving
  // in an old sub-thread is.
  const tid = m.thread_id || view.id;
  if (tid !== view.id) return m.seq > (view.marks.has(tid) ? view.marks.get(tid) : view.readAt);
  return m.seq > view.readAt && m.seq <= view.openSeq;
}
function thvWhoHTML(a) {
  if (connectIsGuest(a)) return `<span class="th-who">${connectGuestHTML(a)}</span>`;   // another company's (ui/app/thread-connect.js)
  const slug = actorSlug(a), pid = actorPerson(a);
  if (slug && S.emps.some(e => e.name === slug)) return `<a class="th-who" href="#/bot/${esc(slug)}">${esc(actorName(a))}</a>`;
  if (pid) return `<a class="th-who" href="#/person/${esc(encodeURIComponent(pid))}">${esc(actorName(a))}</a>`;
  return `<span class="th-who">${esc(actorName(a) || 'Someone')}</span>`;
}
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
  return `<div class="th-reacts">${rs.map(r => `<button type="button" class="th-react${thvReactedMine(r) ? ' me' : ''}" data-th-react="${esc(r.emoji)}" aria-pressed="${thvReactedMine(r) ? 'true' : 'false'}" title="${r.emoji === THV_ACK ? 'Acknowledged' : esc(r.emoji)}">${esc(r.emoji)} <span class="tnum">${r.count}</span></button>`).join('')}</div>`;
}
function thvMsgHTML(view, m) {
  const depth = thvDepth(view, m.depth), sub = m.thread_id && m.thread_id !== view.id;
  const fresh = thvIsNew(view, m), quiet = fresh && !thvAddressed(m);
  const cls = ['th-msg', view.focus === m.id && 'th-focus', sub && 'th-sub', m.refs?.follow_up && 'th-followup', thvMine(m) && 'th-mine', fresh && 'th-new', quiet && 'th-new-quiet', m.deleted && 'th-deleted']
    .filter(Boolean).join(' ');
  const head = `<div class="th-head">${thvWhoHTML(connectAuthor(m))}<time title="${esc(fmt(m.created))}">${esc(ago(m.created))}</time>${
    m.edited_at && !m.deleted ? '<span class="th-edited muted">edited</span>' : ''}</div>`;
  let body;
  if (m.deleted) body = '<div class="th-body muted">Deleted</div>';
  else if (view.editing === m.id) body = `<div class="th-edit"><textarea class="th-edit-text" rows="2" aria-label="Edit message">${esc(m.body || '')}</textarea>
      <div class="th-edit-acts"><button type="button" class="ghost small" data-th-edit-cancel>Cancel</button><button type="button" class="primary small" data-th-edit-save>Save</button></div></div>`;
  else if (thvIsUpdateCard(m)) body = thvUpdateHTML(m);
  // A card a bot left for you fills in from its action or request (ui/assistant.js, ui/credential-card.js).
  else if (m.refs?.credential_request) body = `<div data-credential-host="${esc(m.refs.credential_request)}"><div class="asst-state">Loading the card…</div></div>`;
  else if (m.refs?.action) body = `<div data-action-host="${esc(m.refs.action)}"><div class="asst-state">Loading the card…</div></div>`;
  // The Assistant's read-back of what it will approve is shown exactly as written, never formatted.
  else if (m.refs?.read_back) body = `<div class="th-body read-back" style="white-space:pre-line">${esc(m.body || '')}</div>`;
  else body = `<div class="th-body md">${safeMd(m.body || '', {shortLinks: true})}</div>${
    // The notice a bot's manager gets about its Home's closed history (backend/thread_followups.py).
    m.refs?.earlier_messages?.thread_id && !m.deleted ? `<button type="button" class="ghost small th-earlier-open" data-th-earlier="${esc(m.refs.earlier_messages.thread_id)}">Open earlier messages to Read</button>` : ''}`;
  // What a bot's turn did, in the same muted rows a chat shows (ui/app/chat.js), and the runner's warnings.
  if (!m.deleted && view.editing !== m.id && String(m.from_actor || '').startsWith('bot:'))
    body += (m.refs?.warnings || []).map(w => `<div class="chat-warn">${esc(String(w).charAt(0).toUpperCase() + String(w).slice(1))}</div>`).join('')
      + v2RunHTML(m, view.run) + v2MessageCards(m);
  // A sub-thread opens with one quiet header row (its own title if it was given one, replies and what is new) that opens
  // it; the replies the timeline carries follow it, indented.
  const st = m.sub_thread, n = Number(st?.count) || 0;
  const more = st?.id && !m.deleted ? `<a class="th-subhead" href="${threadHref(st.id)}" style="--d:${Math.min(depth + 1, THV_DEPTH_MAX)}">
    ${st.title ? `<span class="th-subhead-title">${esc(st.title)}</span>` : ''}
    <span class="th-subhead-n">${n ? `${n} ${n === 1 ? 'reply' : 'replies'}` : 'No replies'}</span>${
    st.unread ? `<span aria-hidden="true">·</span><span class="th-more-new">${st.unread} new</span>` : ''}</a>` : '';
  return `<div class="${cls}" data-mid="${esc(m.id)}" data-message="${esc(m.id)}" data-tid="${esc(m.thread_id || view.id)}" data-seq="${esc(m.seq ?? '')}" style="--d:${depth}">
    <div class="th-av">${actorAvatar(connectAuthor(m), sub ? 20 : 28)}</div>
    <div class="th-main">${head}${body}${m.deleted ? '' : thvFilesHTML(m) + thvReactionsHTML(m)}</div>
    ${m.deleted || view.editing === m.id ? '' : `<div class="th-acts"><button type="button" class="th-act" data-th-ack aria-label="Acknowledge" title="Acknowledge">${THV_ACK}</button><button type="button" class="th-act" data-th-more aria-haspopup="menu" aria-expanded="false" aria-label="Message menu" title="More"><span class="nav-icon" aria-hidden="true">more_horiz</span></button></div>`}
  </div>${more}`;
}
function thvFoldHTML(view, row) {
  const depth = thvDepth(view, row.depth);
  if (row.fold === 'hidden') return `<div class="th-fold th-notshown" style="--d:${depth}" data-mid="${esc(row.id)}"><span>Not shown</span></div>`;
  return `<div class="th-fold th-earlier" style="--d:${depth}" data-mid="${esc(row.id)}"><span>Earlier messages</span>${
    view.thread?.earlier ? `<button type="button" class="linkish" data-th-earlier="${esc(view.id)}">Open earlier messages to Read</button>` : ''}</div>`;
}
function thvMoreHTML(view, m) {
  return `<button type="button" class="th-fold th-more" data-th-expand="${esc(m.id)}" data-mid="${esc(m.id)}" style="--d:${thvDepth(view, m.depth)}"><span>${esc(m.count)} more</span>${
    m.unread ? '<span aria-hidden="true">·</span><span class="th-more-new">new</span>' : ''}</button>`;
}
function thvPendingHTML(view, p) {
  const depth = thvDepth(view, p.depth);
  const text = `<div class="th-body">${esc(p.text)}${p.files?.length ? ` <span class="muted">· ${p.files.length} file${p.files.length === 1 ? '' : 's'}</span>` : ''}</div>`;
  if (p.state === 'held') {
    const h = p.held || {}, rules = (h.rules || []).map(r => `<div class="th-held-rule">${esc(r.text)}</div>`).join('');
    return `<div class="th-msg th-mine th-held" data-pending="${p.key}" style="--d:${depth}"><div class="th-av">${actorAvatar(myActor(), 28)}</div>
      <div class="th-main"><div class="th-head"><span class="th-who">You</span><span class="th-state">${p.editOf ? 'Edit ' : ''}${h.outcome === 'escalated' ? (p.editOf ? 'waiting for review' : 'Waiting for review') : (p.editOf ? 'held' : 'Held')}</span></div>${text}${rules}
        <div class="th-held-acts"><button type="button" class="ghost small" data-held-edit>Edit</button>${
          p.canSend ? '<button type="button" class="ghost small" data-held-send>Send anyway</button>' : ''}<button type="button" class="ghost small" data-held-drop>Discard</button></div></div></div>`;
  }
  return `<div class="th-msg th-mine th-pending${p.state === 'failed' ? ' th-failed' : ''}" data-pending="${p.key}" style="--d:${depth}"><div class="th-av">${actorAvatar(myActor(), 28)}</div>
    <div class="th-main"><div class="th-head"><span class="th-who">You</span><span class="th-state" role="status">${p.state === 'failed' ? esc(p.error || 'Not sent') : 'Checking…'}</span></div>${text}
    ${p.state === 'failed' ? '<div class="th-held-acts"><button type="button" class="ghost small" data-held-edit>Edit</button><button type="button" class="ghost small" data-held-drop>Discard</button></div>' : ''}</div></div>`;
}
function thvReplyHTML(view, m) {
  const depth = thvDepth(view, (Number(m.depth) || 0) + 1);
  return `<div class="th-reply" style="--d:${depth}" data-reply-to="${esc(m.id)}"><textarea class="th-reply-text" rows="1" aria-label="Reply in sub-thread" placeholder="Reply"></textarea>
    <button type="button" class="ghost small" data-th-reply-cancel>Cancel</button><button type="button" class="primary small" data-th-reply-send>Reply</button></div>`;
}
// The last row drawn in a message's branch: a pending reply or the reply box goes after it.
function thvBranchEnd(view, id) {
  const at = view.messages.findIndex(m => m.id === id), d = Number(view.messages[at]?.depth) || 0;
  let end = at;
  while (at >= 0 && end + 1 < view.messages.length && (Number(view.messages[end + 1].depth) || 0) > d) end++;
  return view.messages[end]?.id || '';
}
function thvListHTML(view) {
  if (view.state === 'loading') return '<div class="th-empty muted">Loading…</div>';
  if (view.state === 'gone') return '<div class="th-empty muted">Not found</div>';
  if (view.state === 'error') return `<div class="th-empty err">${esc(view.error || 'Could not load')}</div>`;
  const own = view.messages.filter(m => !m.hidden && !m.collapsed && (m.thread_id || view.id) === view.id).map(m => Number(m.depth) || 0);
  view.base = own.length ? Math.min(...own) : 0;
  const rows = thvRows(view), out = [];
  // Each pending and held message sits where it will land: after its parent's sub-thread, or at the bottom. A pending or
  // held edit sits under the message it would change, which keeps its words until the edit goes through.
  const afterParent = new Map(), placed = new Set();
  for (const p of view.pending) {
    const key = p.editOf ? (view.messages.some(m => m.id === p.editOf) ? p.editOf : '') : p.parent ? thvBranchEnd(view, p.parent) : '';
    if (key) { afterParent.set(key, [...(afterParent.get(key) || []), p]); placed.add(p); }
  }
  const replyAt = view.replyTo ? thvBranchEnd(view, view.replyTo) : '';
  for (const row of rows) {
    out.push(row.fold ? thvFoldHTML(view, row) : row.more ? thvMoreHTML(view, row.more) : thvMsgHTML(view, row.m));
    for (const id of row.ids || [row.m?.id || row.id]) {
      for (const p of afterParent.get(id) || []) out.push(thvPendingHTML(view, p));
      if (replyAt && id === replyAt) out.push(thvReplyHTML(view, view.messages.find(m => m.id === view.replyTo)));
    }
  }
  for (const p of view.pending) if (!placed.has(p)) out.push(thvPendingHTML(view, p));
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
// A message changed in a thread on screen: an edit or a delete in place, a new one at the bottom or under its parent's
// sub-thread; anything the view cannot place reads the first page again.
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
    view.messages[at] = {...view.messages[at], ...m, depth: view.messages[at].depth, sub_thread: view.messages[at].sub_thread};
    return true;
  }
  view.pending = view.pending.filter(p => p.messageId !== m.id);
  if (tid === view.id) { view.messages.push({...m, depth: m.depth ?? view.base ?? 0}); if (view.state === 'empty') view.state = 'ready'; return true; }
  const pi = view.messages.findIndex(x => x.sub_thread?.id === tid || (x.id === (d.parent || m.parent_message_id)));
  if (pi < 0) { liveSoon('thv:' + view.key, () => thvRefresh(view), 150); return false; }
  const parent = view.messages[pi], d0 = Number(parent.depth) || 0;
  let end = pi;
  while (end + 1 < view.messages.length && (Number(view.messages[end + 1].depth) || 0) > d0) end++;
  view.messages.splice(end + 1, 0, {...m, thread_id: tid, depth: d0 + 1});
  const was = parent.sub_thread || {id: tid, count: 0, unread: 0};
  parent.sub_thread = {...was, count: (was.count || 0) + 1, last_seq: Math.max(Number(was.last_seq) || 0, Number(m.seq) || 0),
                       unread: (was.unread || 0) + (!thvMine(m) && thvAddressed(m) ? 1 : 0)};
  return true;
}
function threadViewsLive(topic, d) {
  thvSweep();
  for (const view of THV_VIEWS) {
    if (topic === 'reset') { liveSoon('thv:' + view.key, () => thvRefresh(view), 150); continue; }
    if (topic === 'threads') {
      if (d.thread_id === view.id && d.thread) { view.thread = {...view.thread, ...d.thread}; thvDraw(view); }
      if (d.thread_id === view.id && String(d.kind || '').startsWith('call.')) liveSoon('call:' + view.key, () => callLoad(view), 200);
      // Who may read changed, or a sub-thread opened under a message on screen: read again.
      if ((d.thread_id === view.id && ['audience', 'members'].includes(d.kind)) || (d.kind === 'sub_thread' && view.messages.some(m => m.id === d.thread?.parent_message_id)))
        liveSoon('thv:' + view.key, () => thvRefresh(view), 150);
      continue;
    }
    if (topic === 'messages' && thvApplyMessage(view, d)) { thvDraw(view); thvMarkRead(view); }
  }
}
liveOn('messages', d => { if (d?.thread_id || d?.message?.thread_id) threadViewsLive('messages', d); });

// ----------------------------------------------------------------- the composer
function thvComposerDraw(view) {
  const host = view.el.querySelector('.thv-composer');
  const can = thvCanPost(view) && !['loading', 'gone', 'error'].includes(view.state);
  // Hidden, not removed, while posting is closed (archived, loading): unarchiving shows it again with its draft.
  if (!can) { host.hidden = true; return; }
  host.hidden = false;
  if (view.composer) return;
  const C = view.composer = {files: [], lib: [], mention: null};
  host.innerHTML = `<div class="ask thv-ask"><div class="p-pill">
      <textarea class="p-text" rows="1" autocorrect="on" autocapitalize="sentences" spellcheck="true" aria-label="Message" placeholder="Message"></textarea>
      <span class="thv-attach-wrap"><button class="p-attach p-icon" type="button" title="Attach" aria-label="Attach" aria-haspopup="menu" aria-expanded="false">${ICON_CLIP}</button>
        <div class="p-menu thv-attach-menu" role="menu" hidden><button type="button" role="menuitem" data-attach="upload">Upload</button><button type="button" role="menuitem" data-attach="library">Library file</button></div></span>
      <input type="file" multiple hidden class="p-file">
      <button class="p-send p-send-icon" type="button" aria-label="Send" title="Send">${ICON_SEND}</button></div>
    <div class="p-chips"></div>
    <div class="p-menu thv-mentions" role="listbox" aria-label="Mention" hidden></div>
    <div class="thv-lib" hidden></div></div>`;
  C.el = host.firstElementChild;
  const box = C.el.querySelector('.p-text'), file = C.el.querySelector('.p-file'), menu = C.el.querySelector('.thv-attach-menu');
  const grow = () => { box.style.height = 'auto'; box.style.height = Math.min(box.scrollHeight, 160) + 'px'; };
  box.addEventListener('input', () => { grow(); thvMentionFind(view); });
  box.addEventListener('keydown', ev => {
    if (C.mention && thvMentionKey(view, ev)) return;
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing && !touchKeyboard()) { ev.preventDefault(); void thvComposerSend(view); }
  });
  box.addEventListener('blur', () => setTimeout(() => thvMentionClose(view), 150));
  C.el.querySelector('.p-send').onclick = () => void thvComposerSend(view);
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
  const bots = shownEmps().filter(e => !e.hidden).map(e => ({key: 'bot:' + e.name, label: e.display_name || e.name, handle: e.name, kind: 'bot'}));
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
  if (!text && !C.files.length && !C.lib.length) return;
  const files = C.files.splice(0), lib = C.lib.splice(0);
  box.value = ''; box.style.height = ''; thvChips(view);
  THV_DRAFTS.delete(thvDraftKey(view));
  await thvSend(view, {text, files, lib});
}
// One message out: a "Checking…" row while the rules check runs, then the message, or a held row the author can
// edit, discard or (when the server allows) send anyway.
async function thvSend(view, {text, files = [], lib = [], parent = null}) {
  const p = {key: 'p' + (++THV_N), text, files, lib, parent: parent?.id || '', depth: parent ? (Number(parent.depth) || 0) + 1 : 0, state: 'checking'};
  view.pending.push(p);
  thvDraw(view, {stick: !parent});
  const refs = lib.length ? {files: lib.map(f => ({id: f.id, title: f.title}))} : null;
  const body = {text, ...(refs ? {refs} : {}), ...(parent ? {parent_message_id: parent.id} : {})};
  try {
    let r;
    // An upload goes to a thread, so a first message with files makes the DM before it sends.
    if (!view.id && view.opts.dm && files.length) {
      const made = await post('/v2/threads/dm', {members: [view.opts.dm]});
      const t = made?.thread || made;
      if (t?.id) { view.thread = t; thvSetId(view, t.id); }
    }
    if (!view.id && view.opts.dm) {
      r = await post('/v2/threads/dm', {members: [view.opts.dm], body: text, ...(refs ? {refs} : {})});
      const t = r?.thread || r;
      if (t?.id) { view.thread = t; thvSetId(view, t.id); }
    } else {
      const tid = parent?.thread_id || view.id;
      r = files.length ? await thvUpload(thvPath(tid).replace('/v2/', '/v2/uploads/') + '/messages', body, files)
        : await post(thvPath(tid) + '/messages', body);
    }
    if (r?.held) thvHeld(view, p, r.held);
    else {
      view.pending = view.pending.filter(x => x !== p);
      const m = r?.message || (r?.id && r?.seq !== undefined ? r : null);
      if (m) thvApplyMessage(view, {id: m.id, thread_id: m.thread_id, root_id: m.root_id, message: m, parent: parent?.id});
      else void thvRefresh(view);
      for (const w of r?.warnings || []) if (w.rule === 'mention_outside') toast(`${actorName(w.actor)} is not in this thread`, true);
    }
  } catch (e) {
    p.state = 'failed'; p.error = e.message;
  }
  thvDraw(view, {stick: !parent});
}
function thvHeld(view, p, held) {
  p.state = 'held'; p.held = held;
  if (held.id) get('/v2/rule-holds/' + encodeURIComponent(held.id), {fresh: true})
    .then(h => { if (h?.can_send) { p.canSend = true; p.nonce = h.nonce || ''; thvDraw(view); } }).catch(() => {});
}
// An edit out: "Checking…" under the message while the rules check runs, then the edit in place, or a held row with Edit,
// Discard and (when the server allows) Send anyway. The message keeps its words until the edit goes through.
async function thvEditSend(view, m, text) {
  const p = {key: 'p' + (++THV_N), text, files: [], lib: [], parent: '', editOf: m.id, depth: Number(m.depth) || 0, state: 'checking'};
  view.pending.push(p); view.editing = '';
  thvDraw(view);
  try {
    const r = await patch('/v2/messages/' + encodeURIComponent(m.id), {text});
    if (r?.held) thvHeld(view, p, r.held);
    else {
      view.pending = view.pending.filter(x => x !== p);
      const fresh = r?.message || r || {};
      Object.assign(m, fresh, {body: fresh.body ?? text});
    }
  } catch (e) { p.state = 'failed'; p.error = e.message; }
  thvDraw(view);
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
  el.addEventListener('click', ev => void thvClick(view, ev));
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
  if (t.closest('[data-th-expand]')) { void thvExpand(view, t.closest('[data-th-expand]')); return; }
  if (t.closest('[data-thv-members]')) { threadMembersOpen(view); return; }
  if (t.closest('[data-call-setup]')) { window.supportStart('Please set up calls on our Tico server.'); return; }
  if (t.closest('[data-call]')) { void callAction(view, t.closest('[data-call]').dataset.call); return; }
  if (t.closest('[data-thv-menu]')) { threadMenuOpen(view, t.closest('[data-thv-menu]')); return; }
  if (t.closest('[data-th-earlier]')) {
    const b = t.closest('[data-th-earlier]'); b.disabled = true;
    try { await threadEarlierOpen(view, b.dataset.thEarlier); }
    catch (e) { toast(e.message, true); b.disabled = false; }
    return;
  }
  if (t.closest('[data-th-ack]') && m) { await thvReact(view, m, THV_ACK); return; }
  if (t.closest('[data-th-react]') && m) { await thvReact(view, m, t.closest('[data-th-react]').dataset.thReact); return; }
  if (t.closest('[data-th-more]') && m) { thvMsgMenu(view, m, t.closest('[data-th-more]')); return; }
  if (t.closest('[data-th-edit-cancel]')) { view.editing = ''; thvDraw(view); return; }
  if (t.closest('[data-th-edit-save]') && m) {
    const text = row.querySelector('.th-edit-text').value.trim();
    if (!text) return;
    await thvEditSend(view, m, text);
    return;
  }
  if (t.closest('[data-th-reply-cancel]')) { view.replyTo = ''; thvDraw(view); return; }
  if (t.closest('[data-th-reply-send]')) {
    const box = view.el.querySelector('.th-reply-text'), text = box?.value.trim(), parent = view.messages.find(x => x.id === view.replyTo);
    if (!text || !parent) return;
    view.replyTo = '';
    await thvSend(view, {text, parent});
    return;
  }
  const pend = t.closest('[data-pending]');
  if (pend) {
    const p = view.pending.find(x => x.key === pend.dataset.pending); if (!p) return;
    // Edit and Discard both take a held write back: nothing is sent, and the review it waited on closes.
    if (p.held?.id && t.closest('[data-held-drop], [data-held-edit]'))
      post('/v2/rule-holds/' + encodeURIComponent(p.held.id) + '/withdraw', {}).catch(() => {});
    if (t.closest('[data-held-drop]')) { view.pending = view.pending.filter(x => x !== p); thvDraw(view); return; }
    if (t.closest('[data-held-edit]') && p.editOf) {
      view.pending = view.pending.filter(x => x !== p);
      view.editing = p.editOf;
      thvDraw(view);
      const box = view.el.querySelector('.th-edit-text');
      if (box) { box.value = p.text; box.focus(); }
      return;
    }
    if (t.closest('[data-held-edit]')) {
      view.pending = view.pending.filter(x => x !== p);
      if (p.parent) view.replyTo = p.parent;
      thvDraw(view);
      const box = p.parent ? view.el.querySelector('.th-reply-text') : view.composer?.el.querySelector('.p-text');
      if (box) { box.value = p.text; box.focus(); }
      if (!p.parent && view.composer) { view.composer.files.push(...(p.files || [])); view.composer.lib.push(...(p.lib || [])); thvChips(view); }
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
    return;
  }
  // A message in a sub-thread opens that sub-thread, with the path back above it.
  if (row && m && m.thread_id && m.thread_id !== view.id && !t.closest('a, button, textarea, input, .md img')) {
    location.hash = threadHref(m.thread_id);
  }
}
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
// The message menu: reply in its sub-thread, make a task of it, and edit or delete one's own.
function thvMsgMenu(view, m, button) {
  thvMenuClose();
  const can = thvCanPost(view), mine = thvMine(m), sayable = !m.kind || m.kind === 'say';
  const touch = matchMedia('(hover: none)').matches;   // a phone shows only ⋯, so Acknowledge is here
  const editing = view.pending.some(p => p.editOf === m.id);   // one edit at a time: a held one has its own Edit
  const items = [touch && ['ack', 'Acknowledge'], can && ['reply', 'Reply in sub-thread'], ['task', 'Make task'], mine && sayable && !editing && ['edit', 'Edit'], mine && ['delete', 'Delete']].filter(Boolean);
  thvMenuShow(button, items, async key => {
    if (key === 'ack') await thvReact(view, m, THV_ACK);
    else if (key === 'reply') {
      view.replyTo = m.id; thvDraw(view);
      view.el.querySelector('.th-reply-text')?.focus();
    } else if (key === 'task') threadMakeTask(m, view);
    else if (key === 'edit') { view.editing = m.id; thvDraw(view); view.el.querySelector('.th-edit-text')?.focus(); }
    else if (key === 'delete') {
      if (!confirm('Delete this message?')) return;
      try { await writeRequest('DELETE', '/v2/messages/' + encodeURIComponent(m.id)); Object.assign(m, {deleted: true, body: ''}); thvDraw(view); }
      catch (e) { toast(e.message, true); }
    }
  });
}
// One small menu at a time, under the button that opened it; Escape or a click elsewhere closes it.
let THV_MENU = null;
function thvMenuShow(button, items, pick) {
  const menu = document.createElement('div');
  menu.className = 'p-menu thv-pop'; menu.setAttribute('role', 'menu');
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
