/* ui/app/core.ts — State-free basics: route constants, $, esc, md
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const API = '/api';
const GH = 'https://github.com/ticoteam/tico';
// Company (the place person:me, the reader's own) is where the app opens.
const HOME = '#/home';
const OVERVIEW = '#/overview';
const WELCOME = '#/welcome';          // first run: names, the team, the bot templates, a computer
const CHAT = '#/chat';                // retired with the Tico chat: old links land on Tasks
const NOTES = '#/notes';               // legacy route
const RECORDINGS = '#/recordings';    // legacy route
const MEETINGS = '#/meetings';        // imported meeting transcripts: the list, Import, and each meeting
const CALL_ROUTE = '#/call/';        // a Tico Meet call's meeting window: #/call/<call id> (ui/app/call-window.ts)
const UPDATES = '#/updates';          // the bots' daily and weekly updates, a feed (backend/updates.py)
const TASKS = '#/tasks';              // hub tasks and Issues in one page (List | Board)
const DECIDE = '#/decide';            // retired: old links land on Needs you, or open the item's card (ui/app/router.ts)
const NEEDS_YOU = '#/tasks?view=foryou';   // everything waiting on me; answerable cards walked by Flow (ui/app/decide.ts)
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
// ---- the keyed patch (motionSwap in ui/app/motion.ts draws with it and animates what it reports).
// patchHTML above matches top-level children by position. This matches elements by the key their markup carries
// (`attr`, data-key unless the caller names another), anywhere under `root`: one set of keys for the whole box, so a
// keyed row inside a keyed group is the same node when it moves to another group. An element whose markup is what it
// was last drawn from is not touched at all (its node, focus, hover, selection, scroll and running animations stay);
// one whose markup changed is patched in place, attribute by attribute and child by child; unkeyed children match by
// position and kind. Keys must be unique in the markup: a second element with a key already used is drawn as new.
/** A leaving element motion.ts keeps in the page while it fades: no longer the patch's to match, move or remove. */
const PATCH_GHOSTS = new WeakSet<Node>();
/** The markup each keyed element was last drawn from. */
const KEYED_SRC = new WeakMap<Element, string>();
/** A keyed build, read but not yet applied: how much of the box it changes is known before anything is written. */
interface KeyedPlan {
  root: Element; attr: string; fresh: DocumentFragment;
  /** Each new keyed element's markup, read before the build takes its children. */
  src: Map<Element, string>;
  /** The elements in the box now, by key. */
  old: Map<string, Element>;
  keys: Set<string>;
  /** Elements in the box whose key the new markup does not have. */
  gone: Element[];
  entered: number; changed: number;
}
/** What a keyed build did: the new keyed elements (the outermost of each new subtree), the keyed elements left in the
 * box for the caller to take out (the outermost too), and how many kept elements were patched or moved. */
interface KeyedDone { entered: Element[]; left: Element[]; changed: number; moved: number }
function keyedPlan(root: Element, html: string, attr = 'data-key'): KeyedPlan {
  const t = document.createElement('template');
  t.innerHTML = html;
  const old = new Map<string, Element>(), src = new Map<Element, string>(), keys = new Set<string>(), gone: Element[] = [];
  for (const el of root.querySelectorAll(`[${attr}]`)) { const k = el.getAttribute(attr) ?? ''; if (old.has(k)) gone.push(el); else old.set(k, el); }
  let entered = 0, changed = 0;
  for (const el of t.content.querySelectorAll(`[${attr}]`)) {
    const k = el.getAttribute(attr) ?? '', was = keys.has(k) ? undefined : old.get(k), s = el.outerHTML;
    src.set(el, s);
    if (keys.has(k)) { entered++; continue; }
    keys.add(k);
    if (was && was.localName !== el.localName) { old.delete(k); gone.push(was); entered++; }
    else if (!was) entered++;
    else if (KEYED_SRC.get(was) !== s) changed++;
  }
  for (const [k, el] of old) if (!keys.has(k)) gone.push(el);
  return {root, attr, fresh: t.content, src, old, keys, gone, entered, changed};
}
// The positions (in `idx`, a list of old places, -1 for a node new to its parent) of the longest run already in order:
// those nodes stay where they are and the rest are moved around them, the fewest moves a new order needs.
function keyedKeep(idx: number[]): Set<number> {
  const tails: number[] = [], prev: number[] = idx.map(() => -1);
  idx.forEach((v, i) => {
    if (v < 0) return;
    let lo = 0, hi = tails.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if ((idx[tails[mid] ?? 0] ?? 0) < v) lo = mid + 1; else hi = mid; }
    prev[i] = lo ? tails[lo - 1] ?? -1 : -1;
    tails[lo] = i;
  });
  const keep = new Set<number>();
  for (let i = tails[tails.length - 1] ?? -1; i >= 0; i = prev[i] ?? -1) keep.add(i);
  return keep;
}
function keyedApply(plan: KeyedPlan): KeyedDone {
  const {root, attr, src, old} = plan, has = `[${attr}]`;
  const done: KeyedDone = {entered: [], left: [], changed: 0, moved: 0};
  const used = new Set<Element>(), drop: ChildNode[] = [], focused = document.activeElement;
  // moveBefore keeps a moved node's focus, hover and running animations; where the browser has none, insertBefore
  // moves it and the focus is put back below.
  const move = (parent: Element, node: ChildNode, ref: ChildNode | null): void => {
    const p = parent as Element & {moveBefore?: (node: Node, ref: Node | null) => void};
    if (p.moveBefore && node.isConnected && parent.isConnected) { try { p.moveBefore(node, ref); return; } catch { /* a node the browser will not move in place is inserted */ } }
    parent.insertBefore(node, ref);
  };
  const place = (parent: Element, want: ChildNode[]): void => {
    const at = new Map<ChildNode, number>();
    parent.childNodes.forEach((n, i) => { at.set(n, i); });
    const idx = want.map(n => n.parentNode === parent ? at.get(n) ?? -1 : -1);
    if (idx.every((v, i) => v >= 0 && v > (i ? idx[i - 1] ?? -1 : -1))) return;
    const keep = keyedKeep(idx);
    let ref: ChildNode | null = null;
    for (let w = want.length - 1; w >= 0; w--) {
      const n = want[w];
      if (!n) continue;
      if (!keep.has(w)) { move(parent, n, ref); if (n instanceof Element && used.has(n)) done.moved++; }
      ref = n;
    }
  };
  const attrs = (was: Element, fresh: Element): void => {
    // A row still arriving keeps its entrance: the class is motion.ts's, set on the element, never in the markup.
    const arriving = was.classList.contains('m-enter');
    for (const a of [...was.attributes]) if (!fresh.hasAttribute(a.name)) was.removeAttribute(a.name);
    for (const a of [...fresh.attributes]) if (was.getAttribute(a.name) !== a.value) was.setAttribute(a.name, a.value);
    if (arriving) was.classList.add('m-enter');
    // A field shows what the markup says, as a fresh one would, unless someone is typing in it.
    if (was instanceof HTMLInputElement && fresh instanceof HTMLInputElement) {
      if (was !== focused && was.value !== fresh.value) was.value = fresh.value;
      if (was.checked !== fresh.checked) was.checked = fresh.checked;
    }
  };
  const make = (fresh: ChildNode, outer: boolean): ChildNode => {
    if (!(fresh instanceof Element)) return document.adoptNode(fresh);
    const s = src.get(fresh);
    let el: Element;
    if (!fresh.querySelector(has)) el = document.adoptNode(fresh);
    // A new element around keyed ones: its shell is new, and its children are matched like any others, so a row
    // moving into a group that was not there keeps its node.
    else { el = document.adoptNode(fresh.cloneNode(false) as Element); kids(el, fresh, outer && s === undefined); }
    if (s !== undefined) { KEYED_SRC.set(el, s); if (outer) done.entered.push(el); }
    return el;
  };
  // A kept element's new keyed children are arrivals of their own (`outer`); a new element's arrive with it.
  const morph = (was: ChildNode, fresh: ChildNode): void => {
    if (!(was instanceof Element) || !(fresh instanceof Element)) { if (was.nodeValue !== fresh.nodeValue) was.nodeValue = fresh.nodeValue; return; }
    if (was.isEqualNode(fresh)) return;
    attrs(was, fresh);
    kids(was, fresh, true);
  };
  const kids = (parent: Element, from: ParentNode, outer: boolean): void => {
    const loose = [...parent.childNodes].filter(n => !PATCH_GHOSTS.has(n) && !(n instanceof Element && n.hasAttribute(attr)));
    const want: ChildNode[] = [];
    let u = 0;
    for (const fresh of [...from.childNodes]) {
      const s = fresh instanceof Element ? src.get(fresh) : undefined;
      if (fresh instanceof Element && s !== undefined) {
        const was = old.get(fresh.getAttribute(attr) ?? '');
        if (!was || used.has(was)) { want.push(make(fresh, outer)); continue; }
        used.add(was);
        if (KEYED_SRC.get(was) !== s) { done.changed++; morph(was, fresh); KEYED_SRC.set(was, s); }
        want.push(was);
        continue;
      }
      let j = u;
      while (j < loose.length && (loose[j]?.nodeType !== fresh.nodeType || loose[j]?.nodeName !== fresh.nodeName)) j++;
      const was = loose[j];
      if (!was) { want.push(make(fresh, outer)); continue; }
      for (; u < j; u++) { const skipped = loose[u]; if (skipped) drop.push(skipped); }
      u = j + 1;
      morph(was, fresh);
      want.push(was);
    }
    for (; u < loose.length; u++) { const rest = loose[u]; if (rest) drop.push(rest); }
    place(parent, want);
  };
  kids(root, plan.fresh, true);
  // Taken out last: a kept row has by now moved out of a group that is going.
  for (const n of drop) n.remove();
  const gone = new Set(plan.gone);
  for (const el of plan.gone) {
    if (!root.contains(el)) continue;
    let p = el.parentElement;
    while (p && p !== root && !gone.has(p)) p = p.parentElement;
    if (p === root) done.left.push(el);
  }
  if (focused instanceof HTMLElement && focused !== document.activeElement && root.contains(focused)) focused.focus({preventScroll: true});
  return done;
}
// A box drawn whole, with no patch: each keyed element's markup is remembered for the next build.
function keyedFill(root: Element, html: string, attr = 'data-key'): void {
  root.innerHTML = html;
  for (const el of root.querySelectorAll(`[${attr}]`)) KEYED_SRC.set(el, el.outerHTML);
}
// ---- the kept view: show what was read last at once, then patch. A page that has been read before draws that read
// in place of a skeleton while the fresh one is out, and draws the fresh one over it when it lands; drawn through
// motionSwap, the rows that did not change stay as they are. keptWrite keeps a page's read under the view it was read
// for (a filter, a tab); keptRead gives it back for that same view only, so one view's rows never stand in for another's.
// The copies live in S.lastRead (ui/app/state.ts) and go when the page reloads.
function keptWrite(page: string, view: string, value: unknown): void {
  S.lastRead['kept:' + page] = {view, value};
}
function keptRead<T>(page: string, view: string): T | null {
  const kept = S.lastRead['kept:' + page] as {view: string; value: T} | undefined;
  return kept && kept.view === view ? kept.value : null;
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
