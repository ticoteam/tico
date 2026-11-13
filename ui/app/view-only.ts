/* ui/app/view-only.ts — a bot's read-only view of the app (backend/app_view.py): the page offers nothing that writes.
   The server refuses every write from a view, but a screenshot on a proof page that shows New task, Follow or a message
   box suggests the bot could act. So, in a view: the controls that only write are not shown (New task, Follow, the page
   +, answer buttons), every message box is a grey "Read-only view" line, and every other control that is not a read
   (moving between places, opening, searching, filtering, closing, showing more) stays in sight, greyed out, and does
   nothing when clicked. A person's page is never touched: viewOnlyStart returns at once without a view.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** The controls a view hides outright (styles/components.css, html.view-only). */
const VIEW_HIDDEN = [
  '#task-mi-new', '#task-mi-edit', '#task-play', '.add-ctl', '[data-need-answer]', '[data-task-follow]', '.p-follow', '.tm-page-add',
  '[data-page-add]', '[data-page-link]', '[data-answer]', '[data-dash-mode]', '[data-dash-add]',
];
/** The controls that only read. Anything else that takes a click or input is greyed out and inert in a view.
    ui/tests/app-view-read-only.cts keeps its own list on purpose: the test checks this one rather than reading it, so
    a write added here by mistake fails the test. Do not make one list from the other. */
const VIEW_READS = [
  '#side a', '#side button', '#account', '#account-menu button', '#task-scope', '#task-q', '#task-filter',
  '#task-menu', '#task-mi-archive', '#task-mi-layout button', '#task-archive-x', '#task-archives button', '.tl-mi',
  '.tl-chip button', '#task-more button', '.tk-row', '[data-task]',
  '[data-need]', '[data-reload]', '[data-dash-retry]', '.dash-tabs a', '.dash-tabs button', '[role="tab"]',
  '[aria-label="Close"]', '[data-close]', '[data-show-more]', '[data-more]', 'a[href]', 'input[type="search"]',
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
  for (const form of root.querySelectorAll(VIEW_COMPOSERS)) viewComposer(form);
  for (const el of root.querySelectorAll(VIEW_CONTROLS)) viewOff(el);
}

/** Called once the signed-in reader is known (boot.ts). Nothing happens for a person. */
function viewOnlyStart() {
  if (!S.me?.read_only_view || document.documentElement.classList.contains('view-only')) return;
  document.documentElement.classList.add('view-only');
  viewSweep(document);
  // Each page and window draws itself again often; a frame's sweep covers whatever a redraw put in.
  let queued = false;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; viewSweep(document); });
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
