/* ui/app/core.ts — State-free basics: route constants, $, esc, md
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const API = '/api';
const GH = 'https://github.com/ticoteam/tico';
// My dashboard (Company › Mine) is where the app opens; Company › Dashboard is the company's.
const HOME = '#/home';
const OVERVIEW = '#/overview';
const WELCOME = '#/welcome';          // first run: names, the team, the bot templates, a computer
const CHAT = '#/chat';                // retired with the Tico chat: old links land on Tasks
const NOTES = '#/notes';               // legacy route
const RECORDINGS = '#/recordings';    // legacy route
const MEETINGS = '#/meetings';        // imported meeting transcripts: the list, Import, and each meeting
const UPDATES = '#/updates';          // the bots' daily and weekly updates, a feed (backend/updates.py)
const TASKS = '#/tasks';              // hub tasks and Issues in one page (List | Board)
const DECIDE = '#/decide';            // retired: old links land on Needs you, or open the item's card (ui/app/router.ts)
const NEEDS_YOU = '#/tasks?view=foryou';   // everything waiting on me; answerable cards walked by Catch up (ui/app/decide.ts)
const GOALS = '#/goals';              // the team chart with everyone's goals and a colour
const ASSISTANT = '#/assistant';      // your private chat with the assistant (ui/app/assistant-page.ts)
const LEARNED = '#/assistant/learned';   // what your chat bot has learned (ui/app/learned.ts)
const SETTINGS = '#/settings';
const HELP = '#/help';                // old Help links: Company with the Support pop-up open (ui/support.ts)
const CREDENTIALS = '#/credentials';   // the owner's connections: what is connected and whether it works
const SQL_PAGE = '#/sql';              // the owner's and admins' read-only query page over the hub database
const STRATEGY = '#/strategy';       // the team's strategy, with Market as its second tab (ui/strategy-page.ts)
const DOCS = '#/docs';              // team-wide documentation, separate from bot Docs tabs
const INTEGRATIONS = '#/integrations'; // integrations/*.md: how a bot uses each outside system, its queries, shared learnings
const MAIL = '#/mail';                // server-stored mail copies; #/inbox redirects here
const LEARNINGS = '#/learnings';     // the nightly learning run: what each night changed (ui/app/learnings.ts)
const MESSAGING = '#/messaging';       // selected Message bot: setup, schedules, and example messages
// Bot notices still flow through /api/v2/messages?unread=1. People read mail on #/mail, not live Gmail.

// `$()` returns an untyped element, as it always has. A typed `E | null` return is inferred from the annotation at each
// call (`const feedback: HTMLElement = $('#x')` would read `HTMLElement | null`), which broke the typed .ts callers on
// main. A TypeScript caller casts the result: `$('#x') as HTMLInputElement | null`.
// biome-ignore lint/suspicious/noExplicitAny: the untyped element is any by contract (see the comment above)
type UntypedElement = any;
const $ = (s: string, r: ParentNode = document): UntypedElement => r.querySelector(s);
// The entities `esc` writes for the five characters that matter in markup.
const ESC_ENTITY: Record<string, string> = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};
const esc = (s: unknown): string => String(s ?? '').replace(/[&<>"']/g, c => ESC_ENTITY[c] ?? c);
// The one filter icon: every button that opens or applies filters shows it, with no word (aria-label carries it).
const filterIcon = (size = 14): string => `<svg viewBox="0 0 14 14" width="${size}" height="${size}" aria-hidden="true" focusable="false"><path d="M2 3.5h10M4 7h6M5.8 10.5h2.4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`;
// A shaped placeholder for a list that is still loading (styles/skeleton.css): `n` rows of uneven width, so the page
// keeps its layout and does not jump when the rows arrive. `card` drops the dividers for a list inside a card.
const SKEL_W = [62, 44, 71, 53, 38, 66, 49, 57];
const skelRows = (n = 5, {card = false, dot = true} = {}): string => `<div class="skel${card ? ' skel-card' : ''}" aria-busy="true" aria-label="Loading">${
  Array.from({length: n}, (_, i) => `<div class="skel-row">${dot ? '<span class="skel-dot"></span>' : ''}<span class="skel-bar" style="width:${SKEL_W[i % SKEL_W.length]}%"></span><span class="skel-bar skel-end"></span></div>`).join('')}</div>`;
// A person is typing in `el`: a text field has focus or holds something other than its default. A poll or
// a same-route redraw must leave such a form alone (checkboxes and selects save when changed, so they do not count).
// `el` is an element, or a stand-in with the one method used here: computer-detail.js passes one (its fields, just read).
// The selector matches only input and textarea, so the filter keeps every field an element yields.
type FormScope = {querySelectorAll(selector: string): Iterable<Element>} | null | undefined;
const formBusy = (el: FormScope): boolean => !!el && [...el.querySelectorAll('textarea, input:not([type=checkbox], [type=radio], [type=hidden], [type=button], [type=submit])')]
  .filter((f): f is HTMLInputElement | HTMLTextAreaElement => f instanceof HTMLInputElement || f instanceof HTMLTextAreaElement)
  .some(field => field === document.activeElement || field.value !== field.defaultValue);
// Redraw `root` from `html`, touching only what changed. The same markup as last time leaves it alone; otherwise only
// the top-level children whose markup differs are replaced, so the unchanged rows keep their nodes (focus, the place
// someone reads, a playing clip, a loaded picture). Polls and live events redraw lists every few seconds, and redrawing
// a whole list for one changed row kept the desktop app's page busy. `full(root, html)` draws instead when a replaced
// row holds a visual card (chat keeps those through visualSwap), or when someone else changed root's children since.
// Returns whether anything changed.
/** What patchHTML last drew into a box: the markup it was given, each top-level child's markup, and the child nodes. */
interface CorePatchSource {
  html: string;
  src: (string | null)[];
  nodes: ChildNode[];
}
const PATCH_SRC = new WeakMap<Element, CorePatchSource>();
function patchHTML(root: Element, html: string, full: (el: Element, h: string) => void = (el, h) => { el.innerHTML = h; }): boolean {
  const was = patchDrawn(root);
  if (was && was.html === html) return false;
  const t = document.createElement('template');
  t.innerHTML = html;
  const fresh = [...t.content.childNodes];
  return patchNodes(root, was, html, fresh, fresh.map(patchSrcOf), full);
}
const patchSrcOf = (n: ChildNode): string | null => n instanceof Element ? n.outerHTML : n.nodeValue;
// What patchHTML last drew into `root`, while its children are still the ones it drew.
function patchDrawn(root: Element): CorePatchSource | null {
  const was = PATCH_SRC.get(root), kids = root.childNodes;
  return was && was.nodes.length === kids.length && was.nodes.every((n, i) => n === kids[i]) ? was : null;
}
function patchNodes(root: Element, was: CorePatchSource | null, html: string, fresh: ChildNode[], src: (string | null)[], full: (el: Element, h: string) => void): boolean {
  const draw = (): boolean => {
    full(root, html);
    PATCH_SRC.set(root, {html, src, nodes: [...root.childNodes].length === src.length ? [...root.childNodes] : []});
    return true;
  };
  if (!was) return draw();
  let a = 0, b = 0;
  while (a < was.src.length && a < src.length && was.src[a] === src[a]) a++;
  while (b < was.src.length - a && b < src.length - a && was.src[was.src.length - 1 - b] === src[src.length - 1 - b]) b++;
  const gone = was.nodes.slice(a, was.nodes.length - b), added = fresh.slice(a, fresh.length - b);
  if (gone.some(n => n instanceof Element && (n.matches('figure.vcard') || n.querySelector('figure.vcard')))) return draw();
  const after = was.nodes[was.nodes.length - b] || null;
  for (const n of gone) n.remove();
  for (const n of added) root.insertBefore(document.adoptNode(n), after);
  PATCH_SRC.set(root, {html, src, nodes: [...was.nodes.slice(0, a), ...added, ...was.nodes.slice(was.nodes.length - b)]});
  return true;
}
// patchHTML when only the end of the markup changed: `head` is what the box was last drawn from up to its last rows, and
// `tail` the new markup for those rows. Only the tail is read and compared, so a long list whose last rows change (a
// thread's status rows) is not built or parsed again. Same result as patchHTML(root, head + tail); null when the box was
// not drawn from `head` (the caller draws it whole). `head` must end between top-level elements.
function patchHTMLTail(root: Element, head: string, tail: string, full: (el: Element, h: string) => void = (el, h) => { el.innerHTML = h; }): boolean | null {
  const was = patchDrawn(root);
  if (!was || !was.html.startsWith(head)) return null;
  const html = head + tail;
  if (was.html === html) return false;
  const old = document.createElement('template'), t = document.createElement('template');
  old.innerHTML = was.html.slice(head.length);
  t.innerHTML = tail;
  const keep = was.src.length - old.content.childNodes.length;
  if (keep < 0) return null;
  const added = [...t.content.childNodes];
  return patchNodes(root, was, html, [...was.nodes.slice(0, keep), ...added], [...was.src.slice(0, keep), ...added.map(patchSrcOf)], full);
}
// patchHTML for a box every poll redraws. Unchanged markup is kept only while the controls still match it: a button a
// click left disabled ("Resuming…") or a box ticked by hand is drawn fresh, as before.
function patchLiveHTML(root: Element, html: string): boolean {
  if (root.querySelector('button:disabled') || [...root.querySelectorAll<HTMLInputElement>('input[type=checkbox]')].some(b => b.checked !== b.defaultChecked))
    PATCH_SRC.delete(root);
  return patchHTML(root, html);
}
// Bot text (final replies, status notes, playbooks) is untrusted: every render goes through safeMd.
const md = (s: string): string => safeMd(s);
// bucket objects are only reachable through the presign redirect; markdown may name them as s3:// URIs
async function copyText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(value);
  const area = document.createElement('textarea'); area.value = value; area.style.position = 'fixed'; area.style.opacity = '0';
  const focused = document.activeElement;
  document.body.appendChild(area);
  try {
    area.select();
    if (!document.execCommand('copy')) throw new Error('Clipboard unavailable');
  } finally {
    area.remove();
    // Only an HTML or SVG element has focus(); the original called it on any element, which threw for the rest.
    if (focused instanceof HTMLElement || focused instanceof SVGElement) focused.focus({preventScroll:true});
  }
}
// A dialog whose first control is the ✕ in its header opens with the focus on its heading (or itself) instead: the browser would
// otherwise put the focus, and a focus ring, on the ✕ before anyone has pressed a key. Tab still reaches the ✕ next.
// A dialog that focuses a field of its own after opening, or marks one autofocus, keeps that choice.
if (typeof HTMLDialogElement !== 'undefined') {
  const showModal = HTMLDialogElement.prototype.showModal;
  HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement, ...args: []) {
    const result = showModal.apply(this, args);
    const first = document.activeElement, head = first?.closest('.tmodal-head, header, .notes-modal-close, .msg-message-head, .doc-viewer-head');
    const heading = head && first && this.contains(head) && !first.hasAttribute('autofocus') && first.matches('button')
      ? head.querySelector<HTMLElement>('h1, h2, h3, strong') || this : null;
    if (heading) { if (!heading.hasAttribute('tabindex')) heading.tabIndex = -1; heading.focus({preventScroll: true}); }
    return result;
  };
}
