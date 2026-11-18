/* ui/app/decide-about.ts — what an item of the decision view (ui/app/decide.ts) is about, and where to do it. The
   left side of every item on a task but an OK shows who asked and when, then the task's own text as Markdown (md(),
   ui/app/markdown.ts) with its links, files and pages. The text is read once per item, when it comes on screen
   (GET /v2/tasks/{id}, as Open task reads it), and the next item's is read ahead; Needs you itself carries nothing new.
   A link in that text to a place in the app (#/strategy, or a link to this team's app) opens there; the item stays.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** A task as its own read gives it, the fields this screen reads. */
interface DecAboutTask {
  id: string;
  title?: string;
  request?: {id?: string; from?: string | null; text?: string | null; at?: string | null} | null;
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
let DEC_RESUME = '';                             // the item a link in the text left from: ▶ picks up there

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
/** "From Strategy Owner · 2h ago" (a task, a mention). */
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
  // A message in its chat on the right (a bot's ❓, an @ of the person) is there already: the left is its task alone.
  const quote = kind === 'mention' && !decideChatOf(it) && it.line ? `<blockquote class="dc-quote">${esc(it.line)}</blockquote>` : '';
  if (!v || v.state === 'loading') return `<div class="dc-doc">${head}${decideAboutWho(it, kind, null)}${quote}${skelRows(4)}</div>`;
  if (v.state === 'failed') return `<div class="dc-doc">${head}${decideAboutWho(it, kind, null)}${quote}<p class="muted">${esc(v.error)}</p></div>`;
  const text = decideAboutText(t);
  const body = text ? `<div class="md dc-md">${md(text)}</div>` : head ? '' : '<p class="muted">No details.</p>';
  // Nothing to show (no heading, text, links or files): marked, so a phone opens on the question instead (decide.css).
  const chips = decideAboutChips(t!), bare = !head && !text && !chips && !quote ? ' dc-bare' : '';
  return `<div class="dc-doc${bare}">${head}${decideAboutWho(it, kind, t)}${quote}${body}${chips}</div>`;
}
/** The task's links and files, as chips: a file opens in the viewer. A document its text names (`page:<id>@<n>`) is a
    chip in the text itself, with its title (markdown.ts docRefs). */
function decideAboutChips(t: DecAboutTask): string {
  const chips: string[] = [];
  // What a person can open: a task's PR, worktree and session links are the builder's, as on the task page.
  for (const l of t.links || []) if (l.url && !['pr', 'worktree', 'session'].includes(String(l.kind || ''))) chips.push(`<a class="dc-ref-chip" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title || l.url)}</a>`);
  for (const f of t.attachments || []) if (f.id) chips.push(`<a class="dc-ref-chip" href="${API}/v2/files/${encodeURIComponent(f.id)}">${esc(f.name || 'File')}</a>`);
  // The same link or file named twice is one chip.
  const one = [...new Set(chips)];
  return one.length ? `<div class="dc-refs">${one.join('')}</div>` : '';
}

// ---- a place in the app
/** The address of an app link (`#/…`, or a full link to this team's app), '' for anything else. A task's own link is
    left to Open task. */
function decideAppHref(raw: string): string {
  let url: URL;
  try { url = new URL(raw, location.href); } catch { return ''; }
  if (!hubPageUrl(url) || !url.hash.startsWith('#/') || /^#\/tasks?\//.test(url.hash)) return '';
  return url.hash.replace(/[.,;:!?]+$/, '');
}
/** A place in the app: the decision view closes and the page opens; the item stays in Needs you and ▶ picks up at it. */
function decideGoThere(st: DecState, href: string) {
  DEC_RESUME = st.at || '';
  decideStop();
  location.hash = href;
}
