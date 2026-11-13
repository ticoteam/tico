/* ui/app/file-mentions.ts — The files a bot names in its messages, as links to the review view (docs/files.md, Files
   named in a message). A bot writes `reports/2026-11-12-legal-review-pack.docx`, or a bare `notes-for-chris.docx`
   after the folder it sits in; each one that is the bot's own published file becomes a link that opens it as the
   bot's Files rail does (file-review.ts). What a message names is asked of the server together, once per draw
   (GET /api/v2/bots/{bot}/files/mentions), and kept; a name the server cannot pin to one file stays text.
   A bare name prefers the file in the folder named before it in the same paragraph or list item, else it must be the
   only file of that name. Classic script: its globals are shared with the other files under ui/app/. */
'use strict';

interface FmFile { id: string; title?: string | null; name?: string | null }
// One name in a message: where it is, what was written, and what to ask, preferred first.
interface FmFound { node: Text | HTMLElement; start: number; end: number; asks: string[] }
// The types a bot's computer publishes (clients/bot_files.py TYPES).
const FM_EXT = 'md|markdown|txt|csv|tsv|json|ya?ml|html?|pdf|rtf|docx?|xlsx?|pptx?|odt|ods|png|jpe?g|gif|webp|mp4|webm|mov|mp3|wav|m4a|ogg';
// A code span that is one path or name (a name may hold spaces there), or a folder (ending in /).
const FM_CODE = new RegExp(`^(?!.*://)[^\`<>"|*?\\n]{1,240}\\.(?:${FM_EXT})$`, 'i');
const FM_FOLDER = /^(?!.*:\/\/)[\w.@-][\w .@/-]{0,240}\/$/;
// A path or a name in running text: no spaces, not part of a longer word, address or path.
const FM_TEXT = new RegExp(`(?<![\\w./:@-])((?:[\\w.@-]+/)*[\\w@-][\\w.@-]*\\.(?:${FM_EXT}))(?![\\w/-])`, 'gi');
const FM_ANY = new RegExp(`\\.(?:${FM_EXT})\\b`, 'i');   // a quick no for a message that names no file
const FM_BLOCK = 'li, p, td, th, h1, h2, h3, h4, h5, h6, blockquote';
const FM_RETRY_MS = 30_000;   // a name nothing matched is asked again after this (its file publishes after the turn)
const FM_ASK_MAX = 60;        // names one request carries (backend/files.py MENTIONS_MAX)
const FM_KNOWN = new Map<string, {file: FmFile | null; at: number}>();   // bot \0 name -> what the server said
const FM_WANT = new Map<string, Set<string>>();                          // bot -> names this draw asks about
const FM_DRAWN = new Map<string, {gen: number; until: number; html: string}>();
let FM_GEN = 0;
/** What redraws the messages once an answer arrives with a file in it (thread-view.ts sets it). */
let FM_REDRAW: (() => void) | null = null;

const fmKey = (bot: string, said: string) => bot + '\0' + said;
// A name as the server reads it: no ./ in front, forward slashes.
const fmClean = (said: string) => said.trim().replace(/\\/g, '/').replace(/^\.\//, '');
const fmDir = (path: string) => path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';

// Every name in the drawn message, in reading order, each with its block's folder so far.
function fmFind(root: DocumentFragment): FmFound[] {
  const out: FmFound[] = [], folders = new Map<Element | null, string>();
  const take = (node: Text | HTMLElement, start: number, end: number, said: string) => {
    const block = node.parentElement?.closest(FM_BLOCK) ?? null, path = fmClean(said);
    if (!path || path.endsWith('/')) { if (path) folders.set(block, path.replace(/\/+$/, '')); return; }
    const dir = fmDir(path), folder = folders.get(block);
    if (dir) folders.set(block, dir);
    out.push({node, start, end, asks: !dir && folder ? [folder + '/' + path, path] : [path]});
  };
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (n instanceof HTMLElement) {
      if (n.tagName === 'CODE' && !n.closest('pre, a')) {
        const text = n.textContent ?? '';
        if (FM_CODE.test(text.trim()) || FM_FOLDER.test(text.trim())) take(n, 0, 0, text);
      }
      continue;
    }
    if (!(n instanceof Text) || n.parentElement?.closest('a, code, pre')) continue;
    for (const m of n.data.matchAll(FM_TEXT)) take(n, m.index ?? 0, (m.index ?? 0) + (m[1] ?? '').length, m[1] ?? '');
  }
  return out;
}

// The file a name is, once the server has said: the first ask that is a file, none while a preferred one is unknown.
// Every ask not yet answered goes in the same request.
function fmPick(bot: string, asks: string[], now: number, missing: Set<string>): {file: FmFile | null; until: number} {
  let until = Infinity, waiting = false;
  for (const said of asks) {
    const known = FM_KNOWN.get(fmKey(bot, said));
    if (!known || (!known.file && now - known.at > FM_RETRY_MS)) { missing.add(said); waiting = true; continue; }
    if (known.file) { if (!waiting) return {file: known.file, until}; continue; }
    until = Math.min(until, known.at + FM_RETRY_MS);
  }
  return {file: null, until};
}

function fmAsk(bot: string, names: Set<string>): void {
  const want = FM_WANT.get(bot) ?? new Set<string>();
  const first = !FM_WANT.has(bot);
  for (const n of names) if (!FM_KNOWN.has(fmKey(bot, n)) || !FM_KNOWN.get(fmKey(bot, n))?.file) want.add(n);
  FM_WANT.set(bot, want);
  // Every message drawn in this pass asks in one request (a long thread: a few, FM_ASK_MAX names each).
  if (first) queueMicrotask(() => { void fmSend(bot); });
}
async function fmSend(bot: string): Promise<void> {
  const names = [...(FM_WANT.get(bot) ?? [])];
  FM_WANT.delete(bot);
  const now = Date.now();
  // Asked: none of these is asked again by the next draw while this answer is on its way.
  for (const n of names) if (!FM_KNOWN.get(fmKey(bot, n))?.file) FM_KNOWN.set(fmKey(bot, n), {file: null, at: now});
  let found = false;
  for (let i = 0; i < names.length; i += FM_ASK_MAX) {
    const part = names.slice(i, i + FM_ASK_MAX), q = new URLSearchParams();
    for (const n of part) q.append('m', n);
    try {
      const r = await get<{files?: Record<string, FmFile>}>(`/v2/bots/${encodeURIComponent(bot)}/files/mentions?${q}`);
      for (const [said, file] of Object.entries(r.files || {})) {
        if (!file?.id || !part.includes(said)) continue;
        FM_KNOWN.set(fmKey(bot, said), {file, at: Date.now()});
        found = true;
      }
    } catch { /* not readable or offline: the names stay text until they are asked again */ }
  }
  FM_GEN++;
  if (found) FM_REDRAW?.();
}

/** A bot's drawn message (safeMd's HTML) with the files it names as links; the same input gives the same markup until
    an answer changes what is known. `bot` is the bot's id; '' leaves the message as it is. */
function fileMentionLinks(html: string, bot: string): string {
  if (!bot || !FM_ANY.test(html)) return html;
  const key = fmKey(bot, html), now = Date.now(), kept = FM_DRAWN.get(key);
  if (kept && kept.gen === FM_GEN && now < kept.until) return kept.html;
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  const found = fmFind(tpl.content), missing = new Set<string>();
  let until = Infinity;
  const picks = found.map(f => { const pick = fmPick(bot, f.asks, now, missing); until = Math.min(until, pick.until); return pick.file; });
  if (missing.size) fmAsk(bot, missing);
  // Last first, so an earlier name's offsets in a shared text node still hold.
  for (let i = found.length - 1; i >= 0; i--) {
    const f = found[i]!, file = picks[i];
    if (!file) continue;
    const a = document.createElement('a');
    a.setAttribute('href', fileReviewHref(file.id, {bot}));
    a.className = 'md-file-ref';
    a.dataset.fileRef = file.id;
    a.dataset.bot = bot;
    a.dataset.mi = '1';
    a.title = file.name || file.title || '';
    if (f.node instanceof HTMLElement) { f.node.replaceWith(a); a.append(f.node); continue; }
    const text = f.node, tail = text.splitText(f.start);
    tail.splitText(f.end - f.start);
    tail.replaceWith(a);
    a.append(tail);
  }
  const out = found.length ? tpl.innerHTML : html;
  FM_DRAWN.set(key, {gen: FM_GEN, until: missing.size ? now : until, html: out});
  if (FM_DRAWN.size > 400) { const oldest = FM_DRAWN.keys().next().value; if (oldest !== undefined) FM_DRAWN.delete(oldest); }
  return out;
}

// A named file opens in the review view over the page, with the bot's chat beside it, as its Files rail opens it.
document.addEventListener('click', ev => {
  if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
  const a = eventElement(ev)?.closest<HTMLElement>('a[data-file-ref]');
  if (!a) return;
  ev.preventDefault();
  fileReviewOpen(a.dataset.fileRef ?? '', {bot: a.dataset.bot, name: a.title || undefined});
});
