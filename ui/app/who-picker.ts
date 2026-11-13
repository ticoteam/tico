/* ui/app/who-picker.ts — One search picker for "who": a person, a bot or a group, typed into one field.
   whoPickerHTML() draws the field and its list; whoPickerWire(root, {candidates, onPick}) binds them. The caller gives
   the candidates (each with its face, name and an optional muted sub-label) and what a pick does; the picker knows
   nothing of what is being shared, so any screen can use it (the Website tool's Who can use it is the first).
   The field is a combobox (role, aria-expanded, aria-controls, aria-activedescendant) over a listbox that floats in the
   top layer (popover="manual"), placed under the field by script and flipped above it when there is no room below,
   as wide as the field. It opens on a click in the field, on typing or on ↓, never on the focus a dialog gives the
   field when it opens; it closes on Esc (which keeps the typed text and does not close the dialog), on a click outside
   and when focus leaves the field. Groups come first, then people, then bots, each by name; typing filters by name
   (a person also by email), any case, anywhere in it. ↑/↓ move, Enter or a click picks: the field clears and keeps
   focus, and the list closes (it stays open only while the field has text).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

interface WhoCandidate {subject: string; kind: string; name: string; email?: string | undefined; face: string; sub?: string | undefined}
interface WhoPickerOptions {
  candidates: () => WhoCandidate[];
  onPick: (c: WhoCandidate) => unknown;
}
interface WhoPicker {refresh: () => void; close: () => void; input: HTMLInputElement}

const WHO_KIND_RANK: Record<string, number> = {group: 0, person: 1, bot: 2};
let whoPickerSeq = 0;

function whoPickerHTML(opts: {placeholder?: string; label?: string} = {}): string {
  const id = `who-pick-${++whoPickerSeq}`, placeholder = opts.placeholder || 'Add a person, bot or group';
  return `<div class="settings-combo who-pick" data-who-pick><input type="text" class="who-pick-q" role="combobox" aria-expanded="false"
    aria-controls="${id}" aria-autocomplete="list" aria-label="${esc(opts.label || placeholder)}" placeholder="${esc(placeholder)}"
    autocomplete="off" spellcheck="false" autocapitalize="off" data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other">
    <div class="settings-combo-list who-pick-list" id="${id}" role="listbox" popover="manual" aria-label="${esc(opts.label || placeholder)}"></div></div>`;
}

function whoPickerSort(list: WhoCandidate[]): WhoCandidate[] {
  return [...list].sort((a, b) => (WHO_KIND_RANK[a.kind] ?? 3) - (WHO_KIND_RANK[b.kind] ?? 3) || a.name.localeCompare(b.name));
}

function whoPickerWire(root: ParentNode, opts: WhoPickerOptions): WhoPicker | null {
  const box = root.querySelector<HTMLElement>('[data-who-pick]');
  const input = box?.querySelector<HTMLInputElement>('.who-pick-q');
  const list = box?.querySelector<HTMLElement>('.who-pick-list');
  if (!box || !input || !list) return null;
  const dialog = box.closest('dialog');
  let shown: WhoCandidate[] = [], active = -1, swallow = false;
  const isOpen = () => list.matches(':popover-open');
  const place = () => {
    if (!isOpen()) return;
    const r = input.getBoundingClientRect(), h = list.offsetHeight;
    const below = innerHeight - r.bottom - 8, above = r.top - 8;
    const top = below >= h + 4 || below >= above ? r.bottom + 4 : r.top - h - 4;
    list.style.width = `${r.width}px`;
    list.style.left = `${Math.max(8, Math.min(r.left, innerWidth - r.width - 8))}px`;
    list.style.top = `${Math.max(8, top)}px`;
  };
  const mark = () => {
    list.querySelectorAll<HTMLElement>('[role=option]').forEach((o, i) => { o.setAttribute('aria-selected', String(i === active)); });
    const on = active >= 0 ? list.querySelector<HTMLElement>(`#${list.id}-o${active}`) : null;
    if (on) { input.setAttribute('aria-activedescendant', on.id); on.scrollIntoView({block: 'nearest'}); }
    else input.removeAttribute('aria-activedescendant');
  };
  const paint = () => {
    const all = whoPickerSort(opts.candidates()), q = input.value.trim().toLowerCase();
    shown = q ? all.filter(c => c.name.toLowerCase().includes(q) || (c.kind === 'person' && !!c.email && c.email.toLowerCase().includes(q))) : all;
    active = shown.length ? Math.min(active, shown.length - 1) : -1;
    list.innerHTML = shown.length ? shown.map((c, i) => `<div class="settings-combo-opt" role="option" id="${list.id}-o${i}" data-who="${esc(c.subject)}" aria-selected="false">${c.face}
      <span class="settings-combo-n">${esc(c.name)}</span>${c.sub ? `<span class="settings-combo-sub">${esc(c.sub)}</span>` : ''}</div>`).join('')
      : `<p class="settings-combo-empty" data-who-empty>${all.length ? esc(`No one called "${input.value.trim()}"`) : 'Everyone already has it'}</p>`;
    mark();
    place();
  };
  const open = () => {
    if (!isOpen()) { list.showPopover(); input.setAttribute('aria-expanded', 'true'); active = -1; }
    paint();
  };
  const close = () => {
    if (isOpen()) list.hidePopover();
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    active = -1;
  };
  const pick = async (c: WhoCandidate | undefined) => {
    if (!c) return;
    // The field clears and keeps focus; the list stays open only while the field has text, so it closes here.
    input.value = '';
    input.focus();
    close();
    await opts.onPick(c);
  };
  input.addEventListener('click', () => { if (!isOpen()) open(); });
  input.addEventListener('input', () => { active = -1; if (input.value) open(); else close(); });
  input.addEventListener('keydown', ev => {
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      ev.preventDefault();
      if (!isOpen()) { open(); if (ev.key === 'ArrowDown' && shown.length) { active = 0; mark(); } return; }
      if (!shown.length) return;
      active = ev.key === 'ArrowDown' ? Math.min(shown.length - 1, active + 1) : Math.max(0, active - 1);
      mark();
    } else if (ev.key === 'Enter') {
      ev.preventDefault();      // the field never submits the form it sits in
      if (isOpen()) void pick(shown[active]);
    } else if (ev.key === 'Escape' && isOpen()) {
      // The list closes, the typed text and the dialog stay.
      ev.preventDefault(); ev.stopPropagation();
      swallow = true;
      close();
    }
  });
  // The dialog's cancel for that same Esc (it comes before the key's keyup) is held back; the next Esc closes the dialog.
  input.addEventListener('keyup', ev => { if (ev.key === 'Escape') swallow = false; });
  dialog?.addEventListener('cancel', ev => { if (swallow) ev.preventDefault(); swallow = false; });
  input.addEventListener('blur', () => { setTimeout(() => { if (document.activeElement !== input) close(); }); });
  // A press on an option keeps focus in the field, so the blur above does not close the list first.
  list.addEventListener('pointerdown', ev => ev.preventDefault());
  list.addEventListener('click', ev => {
    const el = ev.target instanceof Element ? ev.target.closest<HTMLElement>('[role=option]') : null;
    if (el) void pick(shown.find(c => c.subject === el.dataset.who));
  });
  const outside = (ev: Event) => {
    if (!box.isConnected) { document.removeEventListener('pointerdown', outside, true); return; }
    const t = ev.target;
    if (t instanceof Node && !box.contains(t)) close();
  };
  document.addEventListener('pointerdown', outside, true);
  const replace = () => place();
  addEventListener('resize', replace);
  dialog?.addEventListener('scroll', replace, true);
  dialog?.addEventListener('close', () => { close(); document.removeEventListener('pointerdown', outside, true); removeEventListener('resize', replace); });
  return {refresh: () => { if (isOpen()) paint(); }, close, input};
}
