/* ui/app/decide-about.ts — what an item of the decision view (ui/app/decide.ts) is about, where to do it, and a way to
   reply. The left side of every item on a task but an OK shows who asked and when, then the task's own text as
   Markdown (md(), ui/app/markdown.ts) with its links, files and pages. The text is read once per item, when it comes on
   screen (GET /v2/tasks/{id}, as Open task reads it), and the next item's is read ahead; Needs you itself carries
   nothing new. "Go to … ↗" opens the place the text names: an app link (#/strategy, or a link to this team's app), else
   a "Page → Tab" phrase that matches a sidebar page and one of its tabs (Finance → Access). No model is asked.
   A question's heading is itself, once, in full; its task's title and text are the background under it (decideBgHTML),
   one line until it is opened (#3786).
   Ask back (a question) posts a comment on the task that @mentions whoever asked, which wakes that bot or notifies that
   person; the item stays in Needs you.
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
  conversation_id?: string | null;   // its thread: Flow's chat on an ask (decide.ts decideChatMount)
  documents?: TaskDoc[] | null;      // its documents: a phone's tabs on an ask (decide.ts decideTabsOf)
}
/** One task's read for this screen, by task id: loading (a skeleton), ready, or failed with its words. */
interface DecAbout {
  state: 'loading' | 'ready' | 'failed';
  task: DecAboutTask | null;
  error: string;
}
const DEC_ABOUT = new Map<string, DecAbout>();   // held while the screen is open (decideStop clears it)
let DEC_SENT = '';                               // the item whose Ask back was just sent: "Sent" shows under its box
/** A reply an org rule held (the comment's answer, `held`): shown under its box as the task page shows it, with the
    rule and the actions the sender may take. Nothing was written; an "Ask first" hold may still be sent by a reviewer. */
interface DecHeld { item: string; box: '#dc-other' | '#dc-other-note'; hold: RuleHold; words: string; }
/** Kept per item while the screen is open, so a reply held after the person moved on still shows when they come back. */
const DEC_HELD = new Map<string, DecHeld>();
/** The items whose reply is on its way: a second Send waits for the first (the walk may move on meanwhile). */
const DEC_SENDING = new Set<string>();
let DEC_RESUME = '';                             // the item Go to left from: ▶ picks up there

// ---- reading
/** The task behind an item, read once while the screen is open; on arrival the item on screen draws again. `fresh`
    reads it again and keeps the task read before until the new one arrives, so its tabs stay where they are. */
function decideAboutLoad(taskId: string, fresh = false) {
  const was = taskId ? DEC_ABOUT.get(taskId) : undefined;
  if (!taskId || (was && !fresh)) return;
  const v: DecAbout = was?.task ? {state: 'ready', task: was.task, error: ''} : {state: 'loading', task: null, error: ''};
  DEC_ABOUT.set(taskId, v);
  void (async () => {
    try {
      const r = await get<{task?: DecAboutTask | null}>(`/v2/tasks/${encodeURIComponent(taskId)}`);
      v.task = r?.task || null; v.state = v.task ? 'ready' : 'failed';
      if (!v.task) v.error = 'This task could not be read.';
    } catch (e) {
      // A read again that fails keeps the task read before.
      if (!v.task) { v.state = 'failed'; v.error = errorMessage(e, 'This task could not be read.'); }
    }
    if (DEC_ABOUT.get(taskId) !== v) return;
    const st = DEC, it = st ? decideCurrent(st) : null;
    if (!st || !it || decideTaskId(it) !== taskId) return;
    // An OK shows its own content; one with nothing attached shows the task's text (decidePaintBody), and its side stays.
    decidePaintBody(st);
    if (decideKind(it) !== 'ok' || decideChatOf(it)) decidePaintSideKeepGive(st, it);
  })();
}
/** The item on screen, and ahead of it the next item that shows a task's text. An OK shows its own content; one that
    shows nothing (decideOkBare), or whose ask names nothing to show and is not read yet, shows its task's text. */
function decideAboutAhead(st: DecState) {
  const live = decideLive(st), i = live.indexOf(st.at || '');
  const text = (it: NeedsEntry) => {
    if (decideKind(it) !== 'ok') return true;
    const v = st.ok.get(decideOkKey(it));
    return v ? decideOkBare(v) : !decideShowRefs(it).length && !it.ask?.page;
  };
  const shows = (k: string | undefined) => { const it = k ? st.items.get(k) : undefined; return it && decideTaskId(it) && text(it) ? it : null; };
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
/** "From Strategy Owner · 2h ago" (a task, a mention). A question says who asked on its own meta line. */
function decideAboutWho(it: NeedsEntry, kind: AskKind, t: DecAboutTask | null): string {
  const title = t?.title || it.task?.title || '';
  // A bare OK carries the title as its heading already.
  const on = kind !== 'ok' && title ? ` · on <b>${esc(title)}</b>` : '';
  const from = kind === 'mention' ? it.from_id || '' : t?.requester_id || it.task?.requester_id || '';
  const name = !from || from === 'keeper' || !/^(bot|human):/.test(from) ? 'Tico' : from === myActor() ? 'you' : actorLabel(from);
  const when = kind === 'mention' ? it.since : t?.created_at;
  return `<p class="dc-who">From ${esc(name)}${when ? ` · ${esc(ago(when))}` : ''}${on}</p>`;
}

// ---- the left side
/** The left side of an item on a task: who asked and when, then the task's text, its links, files and pages. `head`:
    a heading over it (a bare OK's task title), and then no "No details." when the task has no text. */
function decideAboutHTML(it: NeedsEntry, kind: AskKind, head = ''): string {
  const v = decideAboutOf(it), t = v?.task || null;
  const quote = kind === 'mention' && it.line ? `<blockquote class="dc-quote">${esc(it.line)}</blockquote>` : '';
  if (!v || v.state === 'loading') return `<div class="dc-doc">${head}${decideAboutWho(it, kind, null)}${quote}${skelRows(4)}</div>`;
  if (v.state === 'failed') return `<div class="dc-doc">${head}${decideAboutWho(it, kind, null)}${quote}<p class="muted">${esc(v.error)}</p></div>`;
  const text = decideAboutText(t);
  const body = text ? `<div class="md dc-md">${md(text)}</div>` : head ? '' : '<p class="muted">No details.</p>';
  // Nothing to show (no heading, text, links or files): marked, so a phone opens on the question instead (decide.css).
  const chips = decideAboutChips(t!, text), bare = !head && !text && !chips && !quote ? ' dc-bare' : '';
  return `<div class="dc-doc${bare}">${head}${decideAboutWho(it, kind, t)}${quote}${body}${chips}</div>`;
}
/** A question's task: its title, then its text, links and files, open (the left pane; a phone's Task tab). */
function decideBgHTML(it: NeedsEntry): string {
  const v = decideAboutOf(it), t = v?.task || null, title = t?.title || it.task?.title || '';
  const text = decideAboutText(t), chips = t ? decideAboutChips(t, text) : '';
  const inner = !v || v.state === 'loading' ? skelRows(3) : v.state === 'failed' ? `<p class="muted">${esc(v.error)}</p>`
    : (text ? `<div class="md dc-md">${md(text)}</div>` : '') + chips;
  // The task open, never behind a chevron: its title, then its text, links and files.
  return `<div class="dc-bg"><h3 class="dc-bg-t">${esc(title)}</h3>${inner ? `<div class="dc-bg-body">${inner}</div>` : ''}</div>`;
}
/** A question as its asker wrote it: a stage's typed question, else the ask's own sentence. */
const decideQuestionOf = (it: NeedsEntry): string => String(it.ask?.typed?.question || it.ask?.line || it.line || '').trim();
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

// ---- Ask back
/** Ask back: the words (and the files added with 📎) as a comment on the task that @mentions whoever asked, or, given
    `ask` (a bot's question), that names the question, which the server writes to the bot that asked. The item stays; the box clears and says Sent. A comment an org rule holds writes nothing: the words
    stay. */
async function decideReplySend(st: DecState, it: NeedsEntry, box: '#dc-other' | '#dc-other-note', ask = '') {
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
function decideHeldHTML(it: NeedsEntry, box: '#dc-other' | '#dc-other-note'): string {
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
