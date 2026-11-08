/* ui/app/core.js — State-free basics: route constants, $, esc, md
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const API = '/api';
const GH = 'https://github.com/ticoteam/tico';
// My dashboard (Overview › Mine) is where the app opens; Company is the company's Dashboard.
const HOME = '#/home';
const OVERVIEW = '#/overview';
const WELCOME = '#/welcome';          // first run: names, the team, the bot templates, a computer
const CHAT = '#/chat';                // retired with the Tico chat: old links land on Tasks
const NOTES = '#/notes';               // legacy route
const RECORDINGS = '#/recordings';    // legacy route
const MEETINGS = '#/meetings';        // imported meeting transcripts: the list, Import, and each meeting
const UPDATES = '#/updates';          // the bots' daily and weekly updates, a feed (backend/updates.py)
const TASKS = '#/tasks';              // hub tasks and Issues in one page (List | Board)
const DECIDE = '#/decide';            // retired: old links land on Needs you, or open the item's card (ui/app/router.js)
const NEEDS_YOU = '#/tasks?view=foryou';   // everything waiting on me; a card at a time from Go through (ui/app/decide.js)
const GOALS = '#/goals';              // the team chart with everyone's goals and a colour
const ASSISTANT = '#/assistant';      // your private chat with the assistant (ui/app/assistant-page.js)
const LEARNED = '#/assistant/learned';   // what your chat bot has learned (ui/app/learned.js)
const BOARD = '#/board';              // the old links still work, each opening its view
const ISSUES = '#/issues';
const SETTINGS = '#/settings';
const HELP = '#/help';                // old Help links: Overview with the Support pop-up open (ui/support.js)
const CREDENTIALS = '#/credentials';   // the owner's connections: what is connected and whether it works
const SQL_PAGE = '#/sql';              // the owner's and admins' read-only query page over the hub database
const STRATEGY = '#/strategy';       // the team's strategy, with Market as its second tab (ui/strategy-page.js)
const DOCS = '#/docs';              // team-wide documentation, separate from bot Docs tabs
const INTEGRATIONS = '#/integrations'; // integrations/*.md: how a bot uses each outside system, its queries, shared learnings
const MAIL = '#/mail';                // server-stored mail copies; #/inbox redirects here
const LEARNINGS = '#/learnings';     // the nightly learning run: what each night changed (ui/app/learnings.js)
const MESSAGING = '#/messaging';       // selected Message bot: setup, schedules, and example messages
// Bot notices still flow through /api/v2/messages?unread=1. People read mail on #/mail, not live Gmail.
const $ = (s, r=document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// A shaped placeholder for a list that is still loading (styles/skeleton.css): `n` rows of uneven width, so the page
// keeps its layout and does not jump when the rows arrive. `card` drops the dividers for a list inside a card.
const SKEL_W = [62, 44, 71, 53, 38, 66, 49, 57];
const skelRows = (n = 5, {card = false, dot = true} = {}) => `<div class="skel${card ? ' skel-card' : ''}" aria-busy="true" aria-label="Loading">${
  Array.from({length: n}, (_, i) => `<div class="skel-row">${dot ? '<span class="skel-dot"></span>' : ''}<span class="skel-bar" style="width:${SKEL_W[i % SKEL_W.length]}%"></span><span class="skel-bar skel-end"></span></div>`).join('')}</div>`;
// A person is typing in `el`: a text field has focus or holds something other than its default. A poll or
// a same-route redraw must leave such a form alone (checkboxes and selects save when changed, so they do not count).
const formBusy = el => !!el && [...el.querySelectorAll('textarea, input:not([type=checkbox], [type=radio], [type=hidden], [type=button], [type=submit])')]
  .some(field => field === document.activeElement || field.value !== field.defaultValue);
// Redraw `root` from `html`, touching only what changed. The same markup as last time leaves it alone; otherwise only
// the top-level children whose markup differs are replaced, so the unchanged rows keep their nodes (focus, the place
// someone reads, a playing clip, a loaded picture). Polls and live events redraw lists every few seconds, and redrawing
// a whole list for one changed row kept the desktop app's page busy. `full(root, html)` draws instead when a replaced
// row holds a visual card (chat keeps those through visualSwap), or when someone else changed root's children since.
// Returns whether anything changed.
const PATCH_SRC = new WeakMap();
function patchHTML(root, html, full = (el, h) => { el.innerHTML = h; }) {
  const was = PATCH_SRC.get(root), kids = root.childNodes;
  const same = was && was.nodes.length === kids.length && was.nodes.every((n, i) => n === kids[i]);
  if (same && was.html === html) return false;
  const t = document.createElement('template');
  t.innerHTML = html;
  const fresh = [...t.content.childNodes], src = fresh.map(n => n.nodeType === 1 ? n.outerHTML : n.nodeValue);
  const draw = () => {
    full(root, html);
    PATCH_SRC.set(root, {html, src, nodes: [...root.childNodes].length === src.length ? [...root.childNodes] : []});
    return true;
  };
  if (!same) return draw();
  let a = 0, b = 0;
  while (a < was.src.length && a < src.length && was.src[a] === src[a]) a++;
  while (b < was.src.length - a && b < src.length - a && was.src[was.src.length - 1 - b] === src[src.length - 1 - b]) b++;
  const gone = was.nodes.slice(a, was.nodes.length - b), added = fresh.slice(a, fresh.length - b);
  if (gone.some(n => n.nodeType === 1 && (n.matches('figure.vcard') || n.querySelector('figure.vcard')))) return draw();
  const after = was.nodes[was.nodes.length - b] || null;
  for (const n of gone) n.remove();
  for (const n of added) root.insertBefore(document.adoptNode(n), after);
  PATCH_SRC.set(root, {html, src, nodes: [...was.nodes.slice(0, a), ...added, ...was.nodes.slice(was.nodes.length - b)]});
  return true;
}
// patchHTML for a box every poll redraws. Unchanged markup is kept only while the controls still match it: a button a
// click left disabled ("Resuming…") or a box ticked by hand is drawn fresh, as before.
function patchLiveHTML(root, html) {
  if (root.querySelector('button:disabled') || [...root.querySelectorAll('input[type=checkbox]')].some(b => b.checked !== b.defaultChecked))
    PATCH_SRC.delete(root);
  return patchHTML(root, html);
}
// Bot text (final replies, status notes, playbooks) is untrusted: every render goes through safeMd.
const md = s => safeMd(s);
// bucket objects are only reachable through the presign redirect; markdown may name them as s3:// URIs
async function copyText(value) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(value);
  const area = document.createElement('textarea'); area.value = value; area.style.position = 'fixed'; area.style.opacity = '0';
  const focused = document.activeElement;
  document.body.appendChild(area);
  try {
    area.select();
    if (!document.execCommand('copy')) throw new Error('Clipboard unavailable');
  } finally { area.remove(); focused?.focus({preventScroll:true}); }
}
// A dialog whose first control is the ✕ in its header opens with the focus on its heading (or itself) instead: the browser would
// otherwise put the focus, and a focus ring, on the ✕ before anyone has pressed a key. Tab still reaches the ✕ next.
// A dialog that focuses a field of its own after opening, or marks one autofocus, keeps that choice.
if (typeof HTMLDialogElement !== 'undefined') {
  const showModal = HTMLDialogElement.prototype.showModal;
  HTMLDialogElement.prototype.showModal = function (...args) {
    const result = showModal.apply(this, args);
    const first = document.activeElement, head = first?.closest?.('.tmodal-head, header, .notes-modal-close, .msg-message-head, .doc-viewer-head');
    const heading = head && this.contains(head) && !first.hasAttribute('autofocus') && first.matches('button')
      ? head.querySelector('h1, h2, h3, strong') || this : null;
    if (heading) { if (!heading.hasAttribute('tabindex')) heading.tabIndex = -1; heading.focus({preventScroll: true}); }
    return result;
  };
}
