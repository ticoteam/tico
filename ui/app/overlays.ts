/* ui/app/overlays.ts — One of each overlay in the Sheets look: the dialog, the confirm, the menu, the side panel, the
   banner line and the toast that says itself. Styles: ui/styles/parts.css (s-modal, s-menu, s-panel, s-banner).
   Classic script in the page part sheets-kit (ui/parts.json), after parts-kit.ts, whose PK builds the markup here.
   A page part that lists sheets-kit in its deps calls these directly. Shell code reaches them through the part's
   `calls` (load, then call), so only sheetConfirm, which answers with a promise, can hand a value back to it.

   What each one does, so no caller writes it again:
   - Layers: a dialog and a panel are native modal <dialog>s, a menu is a native popover. The browser keeps them in
     order: Esc closes the top one only, Tab stays inside a dialog, and the page behind takes no press.
   - Focus: goes to the first field, or to the main button (Cancel when the action is destructive), and returns to
     the control that opened the overlay when it closes.
   - Enter in a one-line field presses the filled button (⌘/Ctrl+Enter from anywhere). A focused button does its own.
   - A press on the scrim closes, unless a field holds text that was typed and not saved.
   - A phone (760px and under): the dialog and the menu are sheets at the bottom edge, above the safe area; the panel
     is the full height.
   Motion is the m- classes and the app's own fades (styles/motion.css, base.css); nothing here animates or waits. */
'use strict';

// One button of a dialog. List Cancel first and the filled verb last.
interface SheetAction {
  label: string;             // a verb, two words at most
  fill?: boolean;            // the one filled button: Enter in a field presses it
  // Runs when pressed. Answer false (or a promise of false) to keep the dialog open; a throw is shown as the dialog's
  // error line. While a promise is pending the button is busy and holds its width. Without `run` the button closes.
  run?: (sheet: SheetHandle) => unknown;
  attrs?: string;            // markup, as PK's attrs: ' data-save'
}
interface SheetHandle {
  el: HTMLElement;
  // `why` reaches onClose: 'esc', 'scrim', 'close' (a panel's ✕, or this call without a reason) or a button's label.
  close(why?: string): void;
  // One amber line above the buttons; '' takes it away.
  error(text: string): void;
}
interface SheetDialogOptions {
  title: string;
  text?: string;             // at most one line under the title
  body?: string;             // markup: fields built with PK.field
  actions?: SheetAction[];
  steps?: {at: number; of: number};   // the wider, stepped dialog: "2 of 3" beside the buttons
  onClose?: (why: string) => void;
  alert?: boolean;           // an alertdialog: it interrupts to ask
  danger?: boolean;          // the focus starts on the first button (Cancel), not the filled one
}
// One row of a menu; null in the list is a rule between groups. A destructive row is last, under a rule.
interface SheetMenuItem {
  label: string;             // one or two words
  icon?: string;
  key?: string;              // its shortcut, shown at the right: '⌘K'
  on?: boolean;              // a choice that is on (a tick); false says it is a choice that is off
  disabled?: boolean;
  run?: () => unknown;
}

let SHEET_SEQ = 0;
const SHEET_PHONE = '(max-width:760px)';
const SHEET_FIELDS = 'input:not([type=hidden],[type=checkbox],[type=radio],[type=button],[type=submit]),textarea,select,[contenteditable=""],[contenteditable=true]';
// An open menu by its anchor: a second press on the anchor closes it.
const SHEET_MENUS = new WeakMap<HTMLElement, SheetHandle>();
// Typing in a menu jumps to the row that starts with it; a pause starts the word again.
const SHEET_TYPE_PAUSE = 600;

function sheetNode<T extends HTMLElement>(html: string): T {
  const holder = document.createElement('template');
  holder.innerHTML = html;
  return holder.content.firstElementChild as T;
}
// Text someone typed and has not saved: a press on the scrim does not throw it away.
function sheetUnsaved(el: HTMLElement): boolean {
  return [...el.querySelectorAll<HTMLElement>(SHEET_FIELDS)].some(f =>
    f instanceof HTMLInputElement || f instanceof HTMLTextAreaElement ? f.value !== f.defaultValue
      : !(f instanceof HTMLSelectElement) && !!f.textContent?.trim());
}
// Shows a modal <dialog> and gives its handle: the opener gets the focus back, a press on the scrim closes it.
function sheetShow(dialog: HTMLDialogElement, onClose?: (why: string) => void): SheetHandle {
  const opener = document.activeElement;
  const outside = (ev: MouseEvent): boolean => {
    if (ev.target !== dialog) return false;
    const r = dialog.getBoundingClientRect();
    return ev.clientX < r.left || ev.clientX > r.right || ev.clientY < r.top || ev.clientY > r.bottom;
  };
  const sheet: SheetHandle = {
    el: dialog,
    close: (why = 'close') => { if (dialog.open) dialog.close(why); },
    error(text) {
      dialog.querySelector('.s-modal-err')?.remove();
      if (text) dialog.querySelector('.s-modal-row')?.insertAdjacentHTML('beforebegin', `<p class="s-f-err s-modal-err" role="alert">${PK.ico('error')}${esc(text)}</p>`);
    },
  };
  // A press that began inside (a selection dragged out of a field) is not a press on the scrim.
  let began = false;
  dialog.addEventListener('pointerdown', ev => { began = outside(ev); });
  dialog.addEventListener('click', ev => { if (began && outside(ev) && !sheetUnsaved(dialog)) sheet.close('scrim'); });
  dialog.addEventListener('cancel', () => { dialog.returnValue = 'esc'; });
  dialog.addEventListener('close', () => {
    dialog.remove();
    if (opener instanceof HTMLElement && opener.isConnected) opener.focus({preventScroll: true});
    onClose?.(dialog.returnValue || 'close');
  });
  // Inside the dialog that is open now, so it is above it and takes presses; else on the page.
  (opener instanceof Element && opener.closest('dialog[open]') || document.body).append(dialog);
  dialog.showModal();
  return sheet;
}

// The one dialog: 400 wide (440 with steps), a title, at most one line, fields, and its buttons with the filled one last.
function sheetDialog({title, text = '', body = '', actions = [], steps, onClose, alert = false, danger = false}: SheetDialogOptions): SheetHandle {
  const id = `sheet-${++SHEET_SEQ}`;
  const buttons = actions.map((a, i) => PK.btn({label: a.label, fill: a.fill === true, attrs: ` data-sheet-act="${i}"${a.attrs ?? ''}`})).join('');
  const dialog = sheetNode<HTMLDialogElement>(PK.dialog({id, title, text, body, buttons, tag: 'dialog', step: steps ? `${steps.at} of ${steps.of}` : '',
    attrs: `${alert ? ' role="alertdialog"' : ''}${text ? ` aria-describedby="${id}-text"` : ''}`}));
  dialog.querySelector('.s-modal-text')?.setAttribute('id', `${id}-text`);
  const sheet = sheetShow(dialog, onClose);
  const button = (i: number): HTMLElement | null => dialog.querySelector<HTMLElement>(`[data-sheet-act="${i}"]`);
  const main = actions.findIndex(a => a.fill);
  let busy = false;
  const press = async (i: number): Promise<void> => {
    const act = actions[i], el = button(i);
    if (!act || !el || busy) return;
    if (!act.run) { sheet.close(act.label); return; }
    busy = true;
    el.classList.add('is-busy');
    el.setAttribute('aria-busy', 'true');
    sheet.error('');
    try {
      if (await act.run(sheet) !== false) sheet.close(act.label);
    } catch (e) {
      sheet.error(errorMessage(e, 'Not saved'));
    } finally {
      busy = false;
      el.classList.remove('is-busy');
      el.removeAttribute('aria-busy');
    }
  };
  dialog.addEventListener('click', ev => {
    const el = (ev.target as Element).closest<HTMLElement>('[data-sheet-act]');
    if (el) void press(Number(el.dataset['sheetAct']));
  });
  dialog.addEventListener('keydown', ev => {
    if (ev.key !== 'Enter' || ev.isComposing || ev.defaultPrevented || main < 0) return;
    const on = ev.target as Element;
    if (!(ev.metaKey || ev.ctrlKey) && on.closest('textarea,button,a,select,summary,[contenteditable],[role=menu],[role=switch]')) return;
    ev.preventDefault();
    void press(main);
  });
  const first = dialog.querySelector<HTMLElement>(SHEET_FIELDS) || button(danger || main < 0 ? 0 : main);
  if (first) first.focus({preventScroll: true});
  else { dialog.tabIndex = -1; dialog.focus({preventScroll: true}); }
  return sheet;
}

// The one confirm: answers true when the verb is pressed, false for Cancel, Esc or the scrim. `verb` names what
// happens ("Delete task"), never "OK". A destructive one (`danger`) opens with the focus on Cancel.
function sheetConfirm({title, text = '', verb, danger = false}: {title: string; text?: string; verb: string; danger?: boolean}): Promise<boolean> {
  return new Promise<boolean>(resolve => {
    let yes = false;
    sheetDialog({title, text, alert: true, danger, onClose: () => resolve(yes),
      actions: [{label: 'Cancel', attrs: ' data-confirm-cancel'}, {label: verb, fill: true, attrs: ' data-confirm-ok', run: () => { yes = true; }}]});
  });
}

// What a screen reader is told: one polite line, or an urgent one. A toast is a picture of it.
function sheetSay(text: string, urgent = false): void {
  const id = urgent ? 'sheet-say-now' : 'sheet-say';
  let live = document.getElementById(id);
  if (!live) {
    live = sheetNode<HTMLElement>(`<div class="s-live" id="${id}" role="${urgent ? 'alert' : 'status'}" aria-live="${urgent ? 'assertive' : 'polite'}" aria-atomic="true"></div>`);
    document.body.append(live);
  }
  // In the open dialog when there is one: the page outside a modal is not read.
  const top = [...document.querySelectorAll<HTMLElement>('dialog[open]')].pop();
  if (top && live.parentElement !== top) top.append(live);
  else if (!top && live.parentElement !== document.body) document.body.append(live);
  live.textContent = '';
  live.textContent = text;
}
// The app's toast (ui/app/api.ts), said aloud. `failed` is the amber one, said at once.
function sheetToast(text: string, {failed = false, action = null}: {failed?: boolean; action?: {label: string; run: () => unknown} | null} = {}): void {
  const host = [...document.querySelectorAll<HTMLElement>('dialog[open]')].pop();
  toast(text, failed, action, host ? {host} : {});
  sheetSay(action ? `${text}. ${action.label}` : text, failed);
}
// "Task deleted · Undo": the thing is already done, and the toast takes it back. For anything a person can undo, in
// place of a confirm.
function sheetUndo(text: string, undo: () => unknown): void {
  sheetToast(text, {action: {label: 'Undo', run: undo}});
}

// The one menu: rows with an icon and one or two words, a shortcut at the right, rules between groups. Opens from its
// anchor and stays inside the window; on a phone it is a sheet at the bottom edge. ↑ ↓ Home End move, Enter picks, Esc
// closes, typing jumps. A second call for an anchor whose menu is open closes it, so the anchor's click is a toggle.
function sheetMenu(anchor: HTMLElement, items: (SheetMenuItem | null)[], {label = '', onClose}: {label?: string; onClose?: () => void} = {}): SheetHandle {
  const had = SHEET_MENUS.get(anchor);
  if (had) { had.close(); return had; }
  const menu = sheetNode<HTMLElement>(PK.menu(items.map((item, i) => item && {label: item.label, attrs: ` data-sheet-item="${i}"`,
    ...(item.icon ? {icon: item.icon} : {}), ...(item.key ? {key: item.key} : {}), ...(item.on ? {on: true} : {}), ...(item.disabled ? {disabled: true} : {})})));
  items.forEach((item, i) => {
    if (item?.on === undefined) return;
    const row = menu.querySelector(`[data-sheet-item="${i}"]`);
    row?.setAttribute('role', 'menuitemcheckbox');
    row?.setAttribute('aria-checked', String(item.on));
  });
  menu.setAttribute('popover', 'auto');
  menu.setAttribute('aria-label', label || anchor.getAttribute('aria-label') || anchor.textContent?.trim() || 'Menu');
  const phone = matchMedia(SHEET_PHONE).matches;
  if (phone) menu.classList.add('is-sheet');
  const rows = (): HTMLElement[] => [...menu.querySelectorAll<HTMLElement>('.s-menu-item:not(:disabled)')];
  const sheet: SheetHandle = {
    el: menu,
    close: () => { if (menu.matches(':popover-open')) menu.hidePopover(); },
    error: () => undefined,
  };
  let typed = '', typedAt = 0;
  menu.addEventListener('keydown', ev => {
    const all = rows(), at = all.indexOf(document.activeElement as HTMLElement);
    let to = -1;
    if (ev.key === 'ArrowDown') to = (at + 1) % all.length;
    else if (ev.key === 'ArrowUp') to = (at - 1 + all.length) % all.length;
    else if (ev.key === 'Home') to = 0;
    else if (ev.key === 'End') to = all.length - 1;
    else if (ev.key === 'Tab') { ev.preventDefault(); sheet.close(); return; }
    else if (ev.key.length === 1 && ev.key !== ' ' && !ev.metaKey && !ev.ctrlKey && !ev.altKey) {
      typed = (ev.timeStamp - typedAt > SHEET_TYPE_PAUSE ? '' : typed) + ev.key.toLowerCase();
      typedAt = ev.timeStamp;
      // One letter again steps to the next row that starts with it; a word finds its row.
      const from = typed.length === 1 ? at + 1 : Math.max(at, 0);
      const order = [...all.slice(from), ...all.slice(0, from)];
      const hit = order.find(r => r.textContent?.trim().toLowerCase().startsWith(typed));
      to = hit ? all.indexOf(hit) : -1;
      if (to < 0) return;
    } else return;
    ev.preventDefault();
    all[to]?.focus({preventScroll: true});
  });
  menu.addEventListener('click', ev => {
    const row = (ev.target as Element).closest<HTMLElement>('[data-sheet-item]');
    const item = row ? items[Number(row.dataset['sheetItem'])] : null;
    if (!item || item.disabled) return;
    // Closed first, with the focus back on the anchor, so what the row opens returns there too.
    sheet.close();
    anchor.focus({preventScroll: true});
    void item.run?.();
  });
  // `toggle` comes a moment after the menu closes: a press on the anchor that closed it (the browser's own outside
  // press) still finds it in SHEET_MENUS, so that press does not open it again.
  menu.addEventListener('toggle', ev => {
    if ((ev as ToggleEvent).newState !== 'closed') return;
    menu.remove();
    SHEET_MENUS.delete(anchor);
    anchor.setAttribute('aria-expanded', 'false');
    onClose?.();
  });
  // Inside the open dialog its anchor is in (the page outside a modal takes no press), else on the page.
  (anchor.closest('dialog[open]') || document.body).append(menu);
  SHEET_MENUS.set(anchor, sheet);
  anchor.setAttribute('aria-haspopup', 'menu');
  anchor.setAttribute('aria-expanded', 'true');
  anchor.focus({preventScroll: true});   // where Esc sends the focus back
  menu.showPopover();
  if (!phone) sheetPlace(menu, anchor);
  (menu.querySelector<HTMLElement>('.s-menu-item.is-on:not(:disabled)') || rows()[0])?.focus({preventScroll: true});
  return sheet;
}
// Under the anchor (over it when there is no room), its right edge on the anchor's in the right half of the window and
// its left edge otherwise, never past the window. Measured once, as it opens.
function sheetPlace(menu: HTMLElement, anchor: HTMLElement): void {
  const GAP = 4, EDGE = 8;
  const r = anchor.getBoundingClientRect(), w = menu.offsetWidth, h = menu.offsetHeight;
  const left = r.left + r.width / 2 > innerWidth / 2 ? r.right - w : r.left;
  const under = r.bottom + GAP, over = r.top - GAP - h;
  const top = under + h <= innerHeight - EDGE || over < EDGE ? under : over;
  menu.style.left = `${Math.max(EDGE, Math.min(left, innerWidth - w - EDGE))}px`;
  menu.style.top = `${Math.max(EDGE, Math.min(top, innerHeight - h - EDGE))}px`;
}
// An old menu's buttons and links as the one menu: each row presses the control it stands for, so the page's own
// handlers and links work unchanged. A rule falls where a new group begins (a different parent). A count or badge in
// a control is shown at its right. Answers null, and opens nothing, when the old menu holds a control that is not a
// button or a link (a field, a select): the caller then opens the old menu.
function sheetMenuFrom(anchor: HTMLElement, source: HTMLElement): SheetHandle | null {
  const shown = (el: Element): boolean => !el.closest('[hidden]');
  if ([...source.querySelectorAll('input,select,textarea')].some(shown)) return null;
  const items: (SheetMenuItem | null)[] = [];
  let group: Element | null = null;
  for (const el of source.querySelectorAll<HTMLElement>('button,a[href]')) {
    if (!shown(el)) continue;
    if (group && el.parentElement !== group) items.push(null);
    group = el.parentElement;
    const copy = el.cloneNode(true) as HTMLElement;
    const icon = copy.querySelector('.nav-icon,.s-ico')?.textContent?.trim() || '';
    const count = copy.querySelector('.ph-n,[class*="badge"],[class*="count"]')?.textContent?.trim() || '';
    copy.querySelectorAll('.nav-icon,.s-ico,svg,[hidden],.ph-n,[class*="badge"],[class*="count"]').forEach(part => part.remove());
    const on = el.matches('.cur,[aria-current=page],[aria-pressed=true],[aria-checked=true],[aria-selected=true]');
    items.push({label: copy.textContent?.trim() || el.getAttribute('aria-label') || el.title, run: () => el.click(),
      ...(icon ? {icon} : {}), ...(count ? {key: count} : {}), ...(on ? {on: true} : {}),
      ...(el instanceof HTMLButtonElement && el.disabled ? {disabled: true} : {})});
  }
  return items.length ? sheetMenu(anchor, items) : null;
}
// The page top bar's ⋯ (ui/app/page-head.ts) under Sheets: its list of tabs past the seventh, folded tools and the
// page's own items opens as the one menu. ui/parts.json lists the button in this part's `events`, so the first press
// loads this file and is sent again. The old look, and a ⋯ that holds a field, keep the old popover.
document.addEventListener('click', ev => {
  if (!document.documentElement.classList.contains('sheets')) return;
  const more = (ev.target as Element | null)?.closest?.<HTMLElement>('header.ph > .ph-more');
  const list = more?.parentElement?.querySelector<HTMLElement>(':scope > .ph-menu');
  if (!more || !list) return;
  const had = SHEET_MENUS.get(more);
  if (had) { ev.preventDefault(); had.close(); return; }
  if (sheetMenuFrom(more, list)) ev.preventDefault();
}, true);

// The one side panel: a sheet at the right edge on a computer, the full height on a phone. A title, ✕, and a body that
// scrolls. For reading and for more than three fields; three or fewer is the dialog.
function sheetPanel({title, body = '', onClose}: {title: string; body?: string; onClose?: (why: string) => void}): SheetHandle {
  const id = `sheet-${++SHEET_SEQ}`;
  const dialog = sheetNode<HTMLDialogElement>(`<dialog class="s-panel m-enter" aria-labelledby="${id}-title"><header class="s-panel-head">`
    + `<h2 class="s-panel-title" id="${id}-title" tabindex="-1">${esc(title)}</h2>${PK.ib('close', 'Close', {attrs: ' data-sheet-close'})}</header>`
    + `<div class="s-panel-body">${body}</div></dialog>`);
  const sheet = sheetShow(dialog, onClose);
  dialog.addEventListener('click', ev => { if ((ev.target as Element).closest('[data-sheet-close]')) sheet.close(); });
  (dialog.querySelector<HTMLElement>(`.s-panel-body :is(${SHEET_FIELDS})`) || dialog.querySelector<HTMLElement>('.s-panel-title'))?.focus({preventScroll: true});
  return sheet;
}

// The one banner line: at the top of `host` (the page), one per host, one line with at most one action. `warn` is for
// something wrong (an amber mark, said at once); otherwise it is said politely. close() takes it away.
function sheetBanner({text, action, kind = 'plain', host}: {text: string; action?: {label: string; run: () => unknown}; kind?: 'plain' | 'warn'; host?: HTMLElement}): SheetHandle {
  const into = host || document.getElementById('main') || document.body;
  into.querySelector(':scope > .s-banner')?.remove();
  const warn = kind === 'warn';
  const banner = sheetNode<HTMLElement>(`<div class="s-banner${warn ? ' is-warn' : ''}" role="${warn ? 'alert' : 'status'}">${warn ? PK.ico('warning') : ''}`
    + `<span>${esc(text)}</span>${action ? PK.btn({label: action.label, cls: 's-btn-quiet'}) : ''}</div>`);
  const sheet: SheetHandle = {el: banner, close: () => banner.remove(), error: () => undefined};
  banner.querySelector('button')?.addEventListener('click', () => { void action?.run(); });
  into.prepend(banner);
  return sheet;
}

// A tab row that keeps to seven: the rest wait under More, the chosen one always on the row. Draws PK.tabs into `host`
// and keeps its line and edge fades right (PK.tabsSync). `onPick` gets the tab's place in `tabs`; a tab with an href
// is a link and needs none. Call it again to draw the row after the choice changes.
function sheetTabs(host: HTMLElement, tabs: PkTab[], {label, max = 7, onPick}: {label: string; max?: number; onPick?: (index: number) => void}): void {
  const order = tabs.map((_, i) => i);
  const on = tabs.findIndex(t => t.on);
  const shown = order.slice(0, max);
  if (on >= max) shown[max - 1] = on;
  const rest = order.filter(i => !shown.includes(i));
  const row = shown.map(i => ({...tabs[i]!, attrs: `${tabs[i]!.attrs ?? ''} data-sheet-tab="${i}"`}));
  host.innerHTML = PK.tabs(rest.length ? [...row, {name: 'More', attrs: ' data-sheet-more aria-haspopup="menu"', cls: 's-tab-more'}] : row, label);
  const nav = host.firstElementChild as HTMLElement;
  // More is a button that opens a menu, among links or tabs: it is not one of them.
  const more = nav.querySelector<HTMLElement>('[data-sheet-more]');
  if (more) {
    more.removeAttribute('aria-selected');
    more.removeAttribute('href');
    more.setAttribute('role', 'button');
    more.tabIndex = 0;
    more.insertAdjacentHTML('beforeend', PK.ico('expand_more'));
  }
  PK.tabsSync(nav);
  const pick = (i: number): void => {
    const href = tabs[i]?.href;
    if (onPick) onPick(i); else if (href != null) location.assign(href);
  };
  nav.addEventListener('click', ev => {
    const el = (ev.target as Element).closest<HTMLElement>('[data-sheet-more],[data-sheet-tab]');
    if (!el) return;
    if (el === more) { ev.preventDefault(); sheetMenu(more, rest.map(i => ({label: tabs[i]!.name, run: () => pick(i)})), {label: 'More'}); return; }
    if (onPick) { ev.preventDefault(); onPick(Number(el.dataset['sheetTab'])); }
  });
  more?.addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); more.click(); } });
}
