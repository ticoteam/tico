/* ui/app/decide-about.ts — what an item of the decision view (ui/app/decide.ts) is about, where to do it, and a way to
   reply. The left side of every item on a task but an OK shows who asked and when, then the task's own text as
   Markdown (md(), ui/app/markdown.ts) with its links, files and pages. The text is read once per item, when it comes on
   screen (GET /v2/tasks/{id}, as Open task reads it), and the next item's is read ahead; Needs you itself carries
   nothing new. "Go to … ↗" opens the place the text names: an app link (#/strategy, or a link to this team's app), else
   a "Page → Tab" phrase that matches a sidebar page and one of its tabs (Finance → Access). No model is asked.
   The heading on the right is short: a person's own task is its title; a question its short title, else itself when it
   is short, else its first clause cut under 60 characters, with the whole question once under it.
   Reply (a person's own task) and Ask back (a question) post a comment on the task that @mentions whoever asked, which
   wakes that bot or notifies that person; the item stays in Needs you. A task Tico filed itself has no one to reply to:
   the box is a note on the task, which reaches the bots following it.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** A task as its own read gives it, the fields this screen reads. */
interface DecAboutTask {
  id: string;
  title?: string;
  body?: string | null;
  requester_id?: string | null;
  created_at?: string | null;
  attachments?: {id: string; name?: string | null}[] | null;
  links?: {url: string; title?: string | null; kind?: string | null}[] | null;
  tags?: {owner?: string | null}[] | null;
}
/** One task's read for this screen, by task id: loading (a skeleton), ready, or failed with its words. */
interface DecAbout {
  state: 'loading' | 'ready' | 'failed';
  task: DecAboutTask | null;
  error: string;
}
const DEC_ABOUT = new Map<string, DecAbout>();   // held while the screen is open (decideStop clears it)
let DEC_SENT = '';                               // the item whose reply was just sent: "Sent" shows under its box
/** A reply an org rule held (the comment's answer, `held`): shown under its box as the task page shows it, with the
    rule and the actions the sender may take. Nothing was written; an "Ask first" hold may still be sent by a reviewer. */
interface DecHeld { item: string; box: '#dc-reply' | '#dc-other'; hold: RuleHold; words: string; }
/** Kept per item while the screen is open, so a reply held after the person moved on still shows when they come back. */
const DEC_HELD = new Map<string, DecHeld>();
/** The items whose reply is on its way: a second Send waits for the first (the walk may move on meanwhile). */
const DEC_SENDING = new Set<string>();
let DEC_RESUME = '';                             // the item Go to left from: ▶ picks up there
const DEC_HEAD_LONG = 90, DEC_HEAD_MAX = 60;

// ---- reading
/** The task behind an item, read once while the screen is open; on arrival the item on screen draws again. */
function decideAboutLoad(taskId: string) {
  if (!taskId || DEC_ABOUT.has(taskId)) return;
  const v: DecAbout = {state: 'loading', task: null, error: ''};
  DEC_ABOUT.set(taskId, v);
  void (async () => {
    try {
      const r = await get<{task?: DecAboutTask | null}>(`/v2/tasks/${encodeURIComponent(taskId)}`);
      v.task = r?.task || null; v.state = v.task ? 'ready' : 'failed';
      if (!v.task) v.error = 'This task could not be read.';
    } catch (e) { v.state = 'failed'; v.error = errorMessage(e, 'This task could not be read.'); }
    if (DEC_ABOUT.get(taskId) !== v) return;
    const st = DEC, it = st ? decideCurrent(st) : null;
    if (st && it && decideTaskId(it) === taskId && decideKind(it) !== 'ok') { decidePaintBody(st); decidePaintSide(st); }
  })();
}
/** The item on screen, and ahead of it the next item that shows a task's text (an OK shows its own content). */
function decideAboutAhead(st: DecState) {
  const live = decideLive(st), i = live.indexOf(st.at || '');
  const shows = (k: string | undefined) => { const it = k ? st.items.get(k) : undefined; return it && decideKind(it) !== 'ok' && decideTaskId(it) ? it : null; };
  const now = shows(live[i]), next = live.slice(i + 1).map(shows).find(Boolean);
  for (const it of [now, next]) if (it) decideAboutLoad(decideTaskId(it));
}
const decideAboutOf = (it: NeedsEntry): DecAbout | undefined => DEC_ABOUT.get(decideTaskId(it));
/** The task's text without its attachments' footer (taskBody); none when it only repeats the title. */
function decideAboutText(t: DecAboutTask | null | undefined): string {
  if (!t) return '';
  const body = taskBody(t as unknown as HubTask).trim();
  return body === String(t.title || '').trim() ? '' : body;
}

// ---- who asked
/** Whom a reply reaches: the asker of a question, the requester of a task. '' for a task Tico filed itself, or one the
    reader asked of themselves: then the box is a note on the task. */
function decideReplyTo(it: NeedsEntry): string {
  const who = decideKind(it) === 'question' ? String(it.ask?.asked_by || '')
    : String(decideAboutOf(it)?.task?.requester_id || it.task?.requester_id || '');
  return /^(bot|human):./.test(who) && who !== myActor() ? who : '';
}
/** How a comment @mentions someone, as a thread's composer writes it: a bot by its slug, a person by their handle. */
function decideHandle(actor: string): string {
  const slug = actorSlug(actor);
  if (slug) return String((S.emps || []).find(e => e.id === slug)?.name || slug);
  const p = (S.people || []).find(x => x.id === actorPerson(actor));
  return personHandle(p?.email || actorPerson(actor));
}
/** "From Strategy Owner · 2h ago" (a task) or "Asked by Performance Owner · 5m ago · on Perf I31" (a question). */
function decideAboutWho(it: NeedsEntry, kind: AskKind, t: DecAboutTask | null): string {
  const title = t?.title || it.task?.title || '';
  const on = kind !== 'yours' && title ? ` · on <b>${esc(title)}</b>` : '';
  if (kind === 'question') {
    const by = it.ask?.asked_by || '';
    return `<p class="dc-who">Asked by ${esc(by ? actorLabel(by) : 'Tico')}${it.since ? ` · ${esc(ago(it.since))}` : ''}${on}</p>`;
  }
  const from = kind === 'mention' ? it.from_id || '' : t?.requester_id || it.task?.requester_id || '';
  const name = !from || from === 'keeper' || !/^(bot|human):/.test(from) ? 'Tico' : from === myActor() ? 'you' : actorLabel(from);
  const when = kind === 'mention' ? it.since : t?.created_at;
  return `<p class="dc-who">From ${esc(name)}${when ? ` · ${esc(ago(when))}` : ''}${on}</p>`;
}

// ---- the left side
/** The left side of an item on a task: who asked and when, then the task's text, its links, files and pages. */
function decideAboutHTML(it: NeedsEntry, kind: AskKind): string {
  const v = decideAboutOf(it), t = v?.task || null;
  const quote = kind === 'mention' && it.line ? `<blockquote class="dc-quote">${esc(it.line)}</blockquote>` : '';
  if (!v || v.state === 'loading') return `<div class="dc-doc">${decideAboutWho(it, kind, null)}${quote}${skelRows(4)}</div>`;
  if (v.state === 'failed') return `<div class="dc-doc">${decideAboutWho(it, kind, null)}${quote}<p class="muted">${esc(v.error)}</p></div>`;
  const text = decideAboutText(t);
  const body = text ? `<div class="md dc-md">${md(text)}</div>` : '<p class="muted">No details.</p>';
  return `<div class="dc-doc">${decideAboutWho(it, kind, t)}${quote}${body}${decideAboutChips(t!, text)}</div>`;
}
/** The task's links, files and the pages its text names (`page:<id>@<n>`), as chips: a file opens in the viewer, a page
    at its version. */
function decideAboutChips(t: DecAboutTask, text: string): string {
  const chips: string[] = [];
  // What a person can open: a task's PR, worktree and session links are the builder's, as on the task page.
  for (const l of t.links || []) if (l.url && !['pr', 'worktree', 'session'].includes(String(l.kind || ''))) chips.push(`<a class="dc-ref-chip" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title || l.url)}</a>`);
  for (const f of t.attachments || []) if (f.id) chips.push(`<a class="dc-ref-chip" href="${API}/v2/files/${encodeURIComponent(f.id)}">${esc(f.name || 'File')}</a>`);
  const pages = new Set<string>();
  for (const m of text.matchAll(/\bpage:([A-Za-z0-9_-]{6,80})(?:@(\d+))?/g)) {
    if (pages.has(m[0])) continue;
    pages.add(m[0]);
    const href = PAGE_ROUTE + encodeURIComponent(m[1]!) + (m[2] ? `?v=${m[2]}` : '');
    chips.push(`<a class="dc-ref-chip" href="${esc(href)}">Page${m[2] ? ` v${esc(m[2])}` : ''}</a>`);
  }
  return chips.length ? `<div class="dc-refs">${chips.join('')}</div>` : '';
}

// ---- Go to
/** The sidebar's pages, by name: their own words (no icon, no count) and their address. */
function decideSidebarPages(): {name: string; href: string}[] {
  return [...document.querySelectorAll<HTMLAnchorElement>('a.nav-link[data-nav][href^="#/"]')].map(a => {
    const copy = a.cloneNode(true) as HTMLElement;
    for (const x of copy.querySelectorAll('.nav-icon, .nav-badge, .pill')) x.remove();
    return {name: (copy.textContent || '').trim(), href: a.getAttribute('href') || ''};
  }).filter(p => p.name && p.href);
}
/** The pages whose tabs a "Page → Tab" phrase may name, from each page's own tab list. */
function decideTabPages(): {page: string; tabs: [string, string][]; href: (id: string) => string}[] {
  const out: {page: string; tabs: [string, string][]; href: (id: string) => string}[] = [];
  if (typeof FIN_TABS !== 'undefined') out.push({page: 'Finance', tabs: FIN_TABS, href: id => `${FIN_ROUTE}/${id}`});
  if (typeof RU_TABS !== 'undefined') out.push({page: 'Rules', tabs: RU_TABS, href: id => `${RULES_ROUTE}/${id}`});
  if (typeof SETTINGS_TABS !== 'undefined') out.push({page: 'Settings', tabs: SETTINGS_TABS.map(id => [id, titleCase(id)] as [string, string]), href: id => `#/settings/${id}`});
  return out;
}
const decideReEsc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
/** The address of an app link (`#/…`, or a full link to this team's app), '' for anything else. A task's own link is
    left to Open task. */
function decideAppHref(raw: string): string {
  let url: URL;
  try { url = new URL(raw, location.href); } catch { return ''; }
  if (!hubPageUrl(url) || !url.hash.startsWith('#/') || /^#\/tasks?\//.test(url.hash)) return '';
  return url.hash.replace(/[.,;:!?]+$/, '');
}
/** Where the text says the work is done: [label, address], or null. An app link first, else "Page → Tab". */
function decideGoOf(text: string): [string, string] | null {
  if (!text) return null;
  const pages = decideSidebarPages();
  for (const m of text.matchAll(/(?:https?:\/\/[^\s<>()"'\]]+|(?<![\w/])#\/[^\s<>()"'\]]+)/g)) {
    const href = decideAppHref(m[0]);
    if (!href) continue;
    const route = href.split(/[/?]/)[1] || '';
    const page = pages.find(p => (p.href.split(/[/?]/)[1] || '') === route);
    return [page ? `Go to ${page.name} ↗` : 'Go to page ↗', href];
  }
  for (const {page, tabs, href} of decideTabPages()) {
    const side = pages.find(p => p.name.toLowerCase() === page.toLowerCase());
    if (!side) continue;
    for (const [id, label] of tabs) {
      const re = new RegExp(`(?<![\\w])${decideReEsc(side.name)}\\s*(?:→|->|›|>)\\s*${decideReEsc(label)}(?![\\w])`, 'i');
      if (re.test(text)) return [`Go to ${side.name} → ${label} ↗`, href(id)];
    }
  }
  return null;
}
/** Go to: the decision view closes and the page opens; the item stays in Needs you and ▶ picks up at it. */
function decideGoThere(st: DecState, href: string) {
  DEC_RESUME = st.at || '';
  decideStop();
  location.hash = href;
}

// ---- the heading
/** A long question's short heading: its first clause, cut at a word under 60 characters, with "…". */
function decideShortHead(q: string): string {
  const clause = (/^(.+?)(?:[.?!;:](?:\s|$)|\s[—–-]\s|,\s)/.exec(q)?.[1] || q).trim();
  if (clause.length < DEC_HEAD_MAX) return clause.replace(/[\s,;:.—–-]+$/, '') + '…';
  const cut = clause.slice(0, DEC_HEAD_MAX - 1), at = cut.lastIndexOf(' ');
  return (at > 20 ? cut.slice(0, at) : cut).replace(/[\s,;:.—–-]+$/, '') + '…';
}
/** The heading on the right and the full question under it ('' when the heading is all of it), or null for an item
    whose heading decide.ts draws (an OK, a stuck task, a mention). */
function decideHeadOf(it: NeedsEntry, kind: AskKind): {head: string; full: string} | null {
  if (kind === 'yours') return {head: it.task?.title || decideAboutOf(it)?.task?.title || it.line, full: ''};
  if (kind !== 'question') return null;
  const q = (it.ask?.typed?.question || it.line || '').trim();
  const short = String((it.ask as {title?: string | null} | undefined)?.title || '').trim();
  if (short) return {head: short, full: short === q ? '' : q};
  return q.length > DEC_HEAD_LONG ? {head: decideShortHead(q), full: q} : {head: q, full: ''};
}

// ---- the reply
/** The reply box under a person's own task: "Reply to <who asked>", or "Note on the task" when Tico filed it. */
function decideReplyHTML(st: DecState, it: NeedsEntry): string {
  const to = decideReplyTo(it), label = to ? `Reply to ${actorLabel(to)}` : 'Note on the task';
  const off = st.busy ? ' disabled' : '';
  return `<div class="dc-reply"><label class="dc-notes"><span>${esc(label)}</span><textarea id="dc-reply" data-dc-grow rows="3" maxlength="4000"></textarea></label>
    ${decideFilesHTML(st)}<div class="dc-reply-row">${decideBtn('reply', 'Send', false, ` data-dc-needs="#dc-reply" disabled${off}`)}`
    + `${DEC_SENT === it.id ? '<span class="dc-sent" role="status">Sent</span>' : ''}</div>${decideHeldHTML(it, '#dc-reply')}</div>`;
}
/** Reply, Ask back and Write to <bot>: the words (and the files added with 📎) as a comment on the task that
    @mentions whoever asked, or, given `ask` (a bot's question), that names the question, which the server writes to the
    bot that asked. The item stays; the box clears and says Sent. A comment an org rule holds writes nothing: the words
    stay. */
async function decideReplySend(st: DecState, it: NeedsEntry, box: '#dc-reply' | '#dc-other', ask = '') {
  const words = decideNote(box), taskId = decideTaskId(it);
  if (!words || !taskId || st.busy || DEC_SENDING.has(it.id)) return;
  const files = await decideFilesReady(st, it.id);
  if (!files) { if (DEC === st) decidePaintSide(st); return; }
  if (DEC !== st || st.at !== it.id) return;
  const to = decideReplyTo(it), handle = to ? decideHandle(to) : '';
  const named = !!ask || handle && new RegExp(`(?<![\\w@])@${decideReEsc(handle)}(?![\\w-])`, 'i').test(words);
  const text = decideWithFiles(handle && !named ? `@${handle} ${words}` : words, files);
  st.busy = true; st.error = ''; DEC_SENT = ''; DEC_HELD.delete(it.id); DEC_SENDING.add(it.id);
  decidePaintSide(st);
  try {
    const r = await post(`/v2/tasks/${encodeURIComponent(taskId)}/comments`, ask ? {text, ask} : {text}) as {held?: RuleHold | null};
    if (DEC !== st) return;
    // A hold is kept by item: the card shows on this item now, or when the walk comes back to it.
    if (r?.held) DEC_HELD.set(it.id, {item: it.id, box, hold: r.held, words});
    // The walk moved on while this was sent: the item on screen is another one, and its box and files are its own.
    if (st.at !== it.id) return;
    st.busy = false;
    const el = $(`.decide ${box}`) as HTMLTextAreaElement | null;
    if (r?.held) {
      // An "Ask first" hold waits for a reviewer who may still send it: the words leave the box, so a second Send
      // cannot post them twice. A "Stop" hold keeps them, to change and send again.
      if (r.held.outcome === 'escalated' && el) { el.value = ''; st.files = []; }
    } else {
      st.files = []; DEC_SENT = it.id;
      if (el) el.value = '';
    }
  } catch (e) {
    if (DEC !== st || st.at !== it.id) return;
    st.busy = false; st.error = taskRefusedOf(e)?.detail || errorMessage(e, 'Not sent.');
  } finally { DEC_SENDING.delete(it.id); }
  decidePaintSide(st);
}
/** The held reply under its box: what happened in one line, then the hold's card (rule, and Edit and resend). */
function decideHeldHTML(it: NeedsEntry, box: '#dc-reply' | '#dc-other'): string {
  const held = DEC_HELD.get(it.id), h = held?.box === box ? held.hold : null;
  if (!h) return '';
  const line = h.outcome === 'escalated' ? 'Held for a reviewer: a rule asks first. It is posted if they send it anyway.'
    : 'Not sent: a rule stops it.';
  return `<div class="dc-held" data-dc-held><p class="dc-held-line" role="status">${esc(line)}</p>${ruleHoldHTML(h)}</div>`;
}
/** After the right side draws: the held card's actions. Withdrawing a held reply puts its words back in the box. */
function decideHeldWire(st: DecState, host: HTMLElement) {
  const it = decideCurrent(st), slot = host.querySelector<HTMLElement>('[data-dc-held]'), held = it ? DEC_HELD.get(it.id) : undefined;
  if (!slot || !held || !it) return;
  ruleHoldsWire(slot, decideTaskId(it), (_card, _hid, path) => {
    if (DEC_HELD.get(held.item) !== held) return;
    DEC_HELD.delete(held.item);
    if (DEC !== st || st.at !== held.item) return;
    decidePaintSide(st);
    // The words typed, not the comment as sent: Send adds the @mention and the files list again (files still on the
    // note stay; after an "Ask first" hold they left with it).
    const el = $(`.decide ${held.box}`) as HTMLTextAreaElement | null;
    if (path === 'withdraw' && el) { el.value = held.words; el.focus(); decideNeedsText($('.decide')!); }
  });
}
