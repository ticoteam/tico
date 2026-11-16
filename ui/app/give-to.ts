/* ui/app/give-to.ts — Give to…: the one picker every place that hands work on opens (Needs you's Give to…, Reassign
   and 👎 give to a person; New task; a task's Owner and People; Ask someone; Subtask; a meeting's Add task).
   Type to search the name, the slug and the role; before typing, Suggested (who asked for the task, the bots it names,
   the owner of its area tag, your last 5 picks) and then Everyone. Recent picks are kept on this device, per person.
   Opening and filtering read only what the app already holds: no network call.
   This file is the picker, a page part (ui/parts.json): it loads the first time a picker opens, and the field that
   opens it is in ui/app/give-to-field.ts with Home. */
'use strict';

/** The words of a name: "BotOps" reads as "bot ops", "finance-po" as "finance po". */
function giveToWords(text: string | null | undefined): string[] {
  return String(text || '').replace(/([\p{Ll}\p{N}])(\p{Lu})/gu, '$1 $2').replace(/(\p{Lu}+)(\p{Lu}\p{Ll})/gu, '$1 $2')
    .toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
}
const giveToSquash = (text: string | null | undefined) => String(text || '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');

/** What was typed, against everyone: name matches (each typed word starts a word of the name or the slug, or the
    letters run together inside the name), then role matches (each typed word starts a word of the role). Words that
    start a name word rank above letters found inside one; A–Z within each. */
function giveToMatch(q: string, entries: readonly GiveToEntry[]): {name: GiveToEntry[]; role: GiveToEntry[]} {
  const typed = String(q || '').toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  if (!typed.length) return {name: entries.slice(), role: []};
  const starts = (words: string[]) => typed.every(t => words.some(w => w.startsWith(t)));
  const squashed = typed.join('');
  const words: GiveToEntry[] = [], inside: GiveToEntry[] = [], role: GiveToEntry[] = [];
  for (const e of entries) {
    const own = [...giveToWords(e.name), ...giveToWords(e.slug)];
    if (starts(own)) words.push(e);
    else if (giveToSquash(e.name).includes(squashed) || giveToSquash(e.slug).includes(squashed)) inside.push(e);
    else if (e.role && starts([...own, ...giveToWords(e.role)])) role.push(e);
  }
  const byName = (a: GiveToEntry, b: GiveToEntry) => a.name.localeCompare(b.name, undefined, {sensitivity: 'base'});
  return {name: [...words.sort(byName), ...inside.sort(byName)], role: role.sort(byName)};
}

/** Where a bot's display name or slug first stands as a whole word in the text (a slug's - and _ join words, so
    finance-po does not name finance), or -1. */
function giveToNamedAt(text: string, e: GiveToEntry): number {
  let at = -1;
  for (const word of new Set([e.name, e.slug].filter(w => w && w.length > 1))) {
    const m = new RegExp(`(?<![\\p{L}\\p{N}_-])${word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![\\p{L}\\p{N}_-])`, 'iu').exec(text);
    if (m && (at < 0 || m.index < at)) at = m.index;
  }
  return at;
}

/** Suggested, in order: who asked for the task, the bots it names (title, then body), the owner of its area tag, then
    the last picks on this device. No one twice, at most 8; only people and bots that may be picked. */
function giveToSuggest(entries: readonly GiveToEntry[], task: GiveToTask | null | undefined, recent: readonly string[]): GiveToSuggestion[] {
  const out: GiveToSuggestion[] = [], seen = new Set<string>();
  const add = (actor: string | null | undefined, why: string) => {
    const want = giveToActor(actor), e = entries.find(x => x.actor === want);
    if (!e || seen.has(e.actor) || out.length >= GIVE_TO_SUGGEST_MAX) return;
    seen.add(e.actor); out.push({entry: e, why});
  };
  if (task) {
    add(task.requester_id, 'Asked for it');
    const text = `${task.title || ''}\n${task.body || ''}`;
    const named = entries.filter(e => e.kind === 'bot').map(e => ({e, at: giveToNamedAt(text, e)})).filter(x => x.at >= 0);
    for (const x of named.sort((a, b) => a.at - b.at)) add(x.e.actor, 'Named in the task');
    for (const tag of task.tags || []) add(tag?.owner, 'Owns its area');
  }
  for (const actor of recent) add(actor, 'Recent');
  return out;
}

// Recent picks: this device, this person; the newest first, at most 5.
const giveToRecentKey = () => 'tico.giveTo.recent:' + (myActor() || 'anyone');
function giveToRecent(): string[] {
  try {
    const list: unknown = JSON.parse(localStorage.getItem(giveToRecentKey()) || '[]');
    return Array.isArray(list) ? list.filter((a): a is string => typeof a === 'string').slice(0, GIVE_TO_RECENT_MAX) : [];
  } catch { return []; }
}
function giveToRemember(actor: string): void {
  try { localStorage.setItem(giveToRecentKey(), JSON.stringify([actor, ...giveToRecent().filter(a => a !== actor)].slice(0, GIVE_TO_RECENT_MAX))); } catch { /* private mode: no recent */ }
}

const GIVE_TO_OPEN = new Map<HTMLElement, () => void>();
/** Opens the picker under `anchor` (a full-height sheet on a phone). Focus starts in the search box; ↑ ↓ move, Enter
    picks, Esc closes with nothing changed and focus back on `anchor`. */
function giveToOpen(anchor: HTMLElement, opts: GiveToOptions): HTMLElement {
  for (const [old, end] of GIVE_TO_OPEN) { end(); GIVE_TO_OPEN.delete(old); if (old.matches(':popover-open')) old.hidePopover(); old.remove(); }
  for (const old of document.querySelectorAll<HTMLElement>('.gv-pop')) old.remove();
  const label = opts.label || 'Give to', n = ++GIVE_TO_SEQ, listId = `gv-list-${n}`;
  const skip = new Set(opts.exclude || []);
  const all = giveToEntries(opts.selected || '').filter(e => !skip.has(e.actor));
  const pop = document.createElement('div');
  pop.className = 'gv-pop'; pop.popover = 'auto';
  pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-label', label);
  pop.innerHTML = `<div class="gv-head"><button type="button" class="gv-x" data-gv-close aria-label="Close">✕</button><span class="gv-title">${esc(label)}</span></div>
    <input type="text" class="gv-q" role="combobox" aria-expanded="true" aria-autocomplete="list" aria-controls="${listId}" aria-label="${esc(label)}: find a person or bot"
      placeholder="Find a person or bot" autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="done">
    <div class="gv-list" id="${listId}" role="listbox" aria-label="${esc(label)}"></div>
    <div class="gv-status" role="status" aria-live="polite"></div>`;
  (anchor.closest('dialog[open]') || document.body).append(pop);
  const input = pop.querySelector<HTMLInputElement>('.gv-q')!, list = pop.querySelector<HTMLElement>('.gv-list')!;
  const status = pop.querySelector<HTMLElement>('.gv-status')!;
  let rows: GiveToEntry[] = [], at = -1;
  const row = (e: GiveToEntry, why: string, byRole: boolean): string => {
    const i = rows.push(e) - 1, on = giveToIs(e, opts.selected) || giveToIs(e, opts.current);
    return `<div role="option" id="${listId}-${i}" class="gv-row${byRole ? ' by-role' : ''}" data-i="${i}" data-give-pick="${esc(e.value)}" aria-selected="false"${on ? ' aria-checked="true"' : ''}${e.role ? ` title="${esc(e.role)}"` : ''}>`
      + `<span class="gv-face" aria-hidden="true">${actorFace(e.actor, 20)}</span><span class="gv-name">${esc(e.name)}</span>`
      + `${e.role ? `<span class="gv-role">${esc(e.role)}</span>` : ''}${why ? `<span class="gv-why">${esc(why)}</span>` : ''}${on ? '<span class="gv-on" aria-hidden="true">✓</span>' : ''}</div>`;
  };
  const group = (title: string, inner: string) => !inner ? '' : `<div role="group" aria-labelledby="${listId}-${title}"><div class="gv-h" id="${listId}-${title}" role="presentation">${esc(title === 'suggested' ? 'Suggested' : 'Everyone')}</div>${inner}</div>`;
  const light = (i: number) => {
    at = rows.length ? Math.max(0, Math.min(rows.length - 1, i)) : -1;
    for (const el of list.querySelectorAll<HTMLElement>('.gv-row')) {
      const on = Number(el.dataset.i) === at;
      el.classList.toggle('on', on); el.setAttribute('aria-selected', String(on));
      if (on) el.scrollIntoView({block: 'nearest'});
    }
    if (at >= 0) input.setAttribute('aria-activedescendant', `${listId}-${at}`); else input.removeAttribute('aria-activedescendant');
  };
  const paint = () => {
    const q = input.value.trim();
    rows = [];
    if (!q) {
      const sug = giveToSuggest(all, opts.task, giveToRecent());
      list.innerHTML = group('suggested', sug.map(s => row(s.entry, s.why, false)).join('')) + group('everyone', all.map(e => row(e, '', false)).join(''));
      status.textContent = all.length ? '' : opts.none || 'Nobody to pick';
    } else {
      const found = giveToMatch(q, all);
      list.innerHTML = found.name.map(e => row(e, '', false)).join('') + found.role.map(e => row(e, '', true)).join('');
      status.textContent = rows.length ? `${rows.length} found` : `No one matches ‘${q}’`;
    }
    status.classList.toggle('gv-none', !rows.length);
    at = -1; light(q ? 0 : -1);
  };
  const pick = (e: GiveToEntry | undefined) => {
    if (!e) return;
    giveToRemember(e.actor);
    pop.hidePopover();
    opts.onPick(e);
  };
  input.oninput = paint;
  pop.addEventListener('keydown', ev => {
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') { ev.preventDefault(); light(at < 0 ? 0 : at + (ev.key === 'ArrowDown' ? 1 : -1)); }
    else if (ev.key === 'Enter' && ev.target === input) { ev.preventDefault(); pick(rows[at]); }
    // popover="auto" closes on Esc by itself; closing here as well keeps it closing even if the stopPropagation below,
    // which keeps Esc from also cancelling Needs you's dialog, is ever moved.
    else if (ev.key === 'Escape') { ev.preventDefault(); pop.hidePopover(); }
    ev.stopPropagation();   // the screen's own keys (← →, j k, Esc on the dialog) wait until the picker closes
  });
  pop.addEventListener('click', ev => {
    const el = ev.target instanceof Element ? ev.target : null;
    if (el?.closest('[data-gv-close]')) { pop.hidePopover(); return; }
    const r = el?.closest<HTMLElement>('[data-give-pick]');
    if (r) pick(rows[Number(r.dataset.i)]);
  });
  pop.addEventListener('mousedown', ev => { if (ev.target instanceof Element && ev.target.closest('.gv-row')) ev.preventDefault(); });   // the search box keeps focus
  function place(): void {
    if (!pop.isConnected) { done(); return; }   // removed without being hidden (its dialog drew again): no toggle comes
    pop.style.maxHeight = '';
    if (matchMedia('(max-width:760px)').matches) { pop.style.top = pop.style.left = ''; return; }
    const a = anchor.getBoundingClientRect();
    // Below the button when it fits, else above; when neither fits (a short window, the button mid-way), the roomier
    // side, shortened to it, so the picker never covers its own button.
    const roomBelow = innerHeight - 8 - (a.bottom + 4), roomAbove = a.top - 4 - 8, h = pop.offsetHeight;
    const below = h <= roomBelow || (h > roomAbove && roomBelow >= roomAbove);
    if (h > (below ? roomBelow : roomAbove)) pop.style.maxHeight = `${Math.max(120, below ? roomBelow : roomAbove)}px`;
    pop.style.top = `${Math.max(8, below ? a.bottom + 4 : a.top - pop.offsetHeight - 4)}px`;
    pop.style.left = `${Math.max(8, Math.min(a.left, innerWidth - pop.offsetWidth - 8))}px`;
  }
  // A press on the button while the picker is open closes it (light dismiss); the click that follows must not open it
  // again. Esc and a pick do not count.
  const press = () => { if (pop.matches(':popover-open')) anchor.dataset.gtClosed = String(Date.now()); };
  // Its listeners go with it: on close, on the next picker opening, or when a resize finds it removed.
  const off = new AbortController();
  const done = () => { off.abort(); GIVE_TO_OPEN.delete(pop); anchor.setAttribute('aria-expanded', 'false'); };
  GIVE_TO_OPEN.set(pop, done);
  anchor.addEventListener('pointerdown', press, {capture: true, signal: off.signal});
  pop.addEventListener('toggle', ev => {
    if ((ev as ToggleEvent).newState !== 'closed') return;
    done();
    if (anchor.isConnected && (pop.contains(document.activeElement) || document.activeElement === document.body)) anchor.focus();
    pop.remove();
  });
  paint();
  pop.showPopover();
  place();
  addEventListener('resize', place, {signal: off.signal});   // under its button again when the window changes size (a phone width is the sheet)
  anchor.setAttribute('aria-expanded', 'true');
  input.focus();
  return pop;
}
