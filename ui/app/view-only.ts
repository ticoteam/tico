/* ui/app/view-only.ts — a bot's read-only view of the app (backend/app_view.py): the page offers nothing that writes.
   The server refuses every write from a view, but a screenshot on a proof page that shows New task, Follow or a message
   box suggests the bot could act. So, in a view: the controls that only write are not shown (New task, Follow, the page
   +, answer buttons), every message box is a grey "Read-only view" line, and every other control that is not a read
   (moving between places, opening, searching, filtering, closing, showing more) stays in sight, greyed out, and does
   nothing when clicked. A person's page is never touched: viewOnlyStart returns at once without a view.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** The controls a view hides outright (styles/components.css, html.view-only; the task window's own in task-code.css). */
const VIEW_HIDDEN = [
  '#task-mi-new', '#task-mi-edit', '#task-play', '.add-ctl', '[data-task-follow]', '.p-follow', '.tm-page-add', 'header.ph .ph-add',
  '[data-page-add]', '[data-page-link]', '[data-dash-mode]', '[data-dash-add]',
  // 👍 👎 on a bot's or a person's work (feedback.ts): the task window's head and their messages.
  '[data-fb-rate]',
  // The task window: its page +, Ask someone, Follow, Answer, Done or Close, and Stop on a run.
  '[data-tp-add]', '[data-req-ask]', '[data-prop-follow]', '[data-prop-unfollow]', '[data-task-answer]',
  '[data-task-finish]', '[data-task-stop]',
  // ...and (task-code.css) Reopen, Pages' upload line, every + and every × that takes something off, Edit on the
  // description and the roadmap's "I want this"; a section left with only its heading goes with them.
  '#task-modal [data-task-reopen]', '#task-modal [data-tp-drop]', '#task-modal .prop-add', '#task-modal .prop-x',
  '#task-modal [data-drop-link]', '#task-modal [data-drop-rel]', '#task-modal [data-req-withdraw]', '#task-modal [data-tp-unlink]',
  '#task-modal [data-depends-add]', '#task-modal [data-requester-self]',
  // A bot's ⋯: the private chat's Archive (and its line) is a write.
  '#bot-dm-arch-host',
];
/** The controls that only read. Anything else that takes a click or input is greyed out and inert in a view.
    ui/tests/app-view-read-only.cts keeps its own list on purpose: the test checks this one rather than reading it, so
    a write added here by mistake fails the test. Do not make one list from the other. */
const VIEW_READS = [
  '#side a', '#side button', '#mobile-nav button', '#org-fan a', '#org-fan button', '#account', '#account-menu button', '#task-scope', '#task-q', '#task-filter',
  '#task-menu', '#task-mi-archive', '#task-mi-layout button', '#task-archive-x', '#task-archives button', '.tl-mi',
  '.tl-chip button', '#task-more button', '.tk-row', '[data-task]',
  '[data-need]', '[data-reload]', '[data-dash-retry]', '[data-fin-retry]', '[data-gr-retry]', '.dash-tabs a', '.dash-tabs button', '[role="tab"]',
  '[aria-label="Close"]', '[data-close]', '[data-modal-close]', '[data-task-earlier]', '[data-task-tab]', '[data-show-more]', '[data-more]', 'a[href]', 'input[type="search"]',
  // The shared page header's ⋯ (page-head.ts) opens a menu; its views held there and Copy link only move or read.
  'header.ph > .ph-more', '.ph-menu > .ph-mtabs > .ph-mtab', '.ph-menu .ph-copy',
  // A bot's page: its ⋯ (the detail views), the lock and Incognito in ⋯ (they only move between its chats), and the
  // rail's ⋯ (the task lists that did not fit). The private chat's Archive in that ⋯ is a write: hidden (VIEW_HIDDEN).
  '#bot-more-btn', '#bot-incognito', '#bot-more-menu [data-bot-incognito]', '.bot-tab-more',
  // The task window: opening a subtask or a related task (and unfolding the subtasks), opening a file, copying a session's
  // resume command, and the head's ⋯, which in a view holds only reads (Copy link,
  // Copy session ID, Watch the session; task-props.ts leaves the rest out).
  '[data-open-task]', '[data-sub-open]', '[data-sub-toggle]', '[data-tf-file]', '[data-copy-session]', '[data-task-more]',
  // Replay, Changelog, Usage, Roadmap and What it has learned (the pages a view opens; Usage and Learnings answer a bot
  // with an error): Retry after a failed load, the shared filter icon, and in the shared popover a filter's choices and
  // its search.
  '[data-use-retry]', '[data-rp-retry]', '[data-learned-retry]', '[data-roadmap-retry]', '[data-retry-changes]',
  '.use-filter', '#rp-filter', '[data-cl-filter]', '#task-filter-pop .tl-opt input', '#task-filter-pop .tl-menu-q',
];
const VIEW_CONTROLS = 'button, input, textarea, select, [contenteditable="true"], [role="button"], [role="checkbox"], '
  + '[role="switch"], [role="menuitem"], [role="menuitemradio"]';
/** The message boxes: the task window's (task-modal.ts) and a thread's (thread-view.ts). */
const VIEW_COMPOSERS = 'form.task-pill, .thv-composer';

const viewIsRead = (el: Element): boolean => VIEW_READS.some(sel => el.matches(sel));

/** One control: a read is left alone; anything else is disabled (a field or button) or made inert (a role). */
function viewOff(el: Element) {
  if (el.hasAttribute('data-view-off') || viewIsRead(el)) return;
  el.setAttribute('data-view-off', '');
  if (el instanceof HTMLButtonElement || el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement
    || el instanceof HTMLSelectElement) el.disabled = true;
  if (el.getAttribute('contenteditable') === 'true') el.setAttribute('contenteditable', 'false');
  el.setAttribute('aria-disabled', 'true');
  el.setAttribute('tabindex', '-1');
}

/** A message box gives way to one grey line. */
function viewComposer(form: Element) {
  if (form.hasAttribute('data-view-box')) return;
  form.setAttribute('data-view-box', '');
  const line = document.createElement('p');
  line.className = 'view-only-line';
  line.textContent = 'Read-only view';
  form.after(line);
}

function viewSweep(root: ParentNode) {
  if (root instanceof Element) {
    if (root.matches(VIEW_COMPOSERS)) viewComposer(root);
    if (root.matches(VIEW_CONTROLS)) viewOff(root);
  }
  for (const form of root.querySelectorAll(VIEW_COMPOSERS)) viewComposer(form);
  for (const el of root.querySelectorAll(VIEW_CONTROLS)) viewOff(el);
}

/** Called once the signed-in reader is known (boot.ts). Nothing happens for a person. */
function viewOnlyStart() {
  if (!S.me?.read_only_view || document.documentElement.classList.contains('view-only')) return;
  document.documentElement.classList.add('view-only');
  viewSweep(document);
  // Each page and window draws itself again often. What a redraw put in is swept in the same turn it was drawn (a
  // mutation observer runs before the next task), so no screenshot or click ever finds a write still enabled.
  new MutationObserver(records => {
    for (const r of records) for (const node of r.addedNodes) if (node instanceof Element && node.isConnected) viewSweep(node);
  }).observe(document.body, {childList: true, subtree: true});
  // A control drawn and clicked inside one frame is still refused before its own handler runs.
  const refuse = (ev: Event) => {
    const el = ev.target instanceof Element ? ev.target.closest(VIEW_CONTROLS) : null;
    if (el && !viewIsRead(el)) { ev.preventDefault(); ev.stopImmediatePropagation(); viewOff(el); }
  };
  for (const type of ['click', 'submit', 'change', 'input', 'keydown']) {
    document.addEventListener(type, ev => {
      if (type === 'keydown' && !(ev instanceof KeyboardEvent && ev.target instanceof Element && ev.target.closest('form, [contenteditable]'))) return;
      refuse(ev);
    }, {capture: true});
  }
}
