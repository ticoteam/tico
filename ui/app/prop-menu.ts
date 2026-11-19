/* ui/app/prop-menu.ts — The one small menu: a task's rows and its "…" menu (task-props.ts), and the ⋯ of a folded
   document tab row (task-docs.ts), in the task window and in Flow. In the task-docs part, which the task and Flow parts
   both load, so it is there for each. Its look is base.css (.tl-pop, .tl-menu, .tl-mi).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The one menu every row opens: its own element, with the anchor and the Enter handler it was given.
interface PropPop extends HTMLDivElement { anchor?: HTMLElement | null; onEntry?: ((v: string) => void) | null; }
interface PropMenuItem {
  value?: string;
  text: string;
  html?: string;
  checked?: boolean;
  danger?: boolean;
  actor?: string;
  /** A thin line above this item: the menu's last group (Delete, after a separator). */
  sep?: boolean;
}
interface PropMenuOptions<T extends PropMenuItem> {
  label: string;
  items?: T[];
  find?: string;
  entry?: string;
  none?: string;
  onPick?: (it: T) => unknown;
  onEntry?: (v: string) => unknown;
  /** The items for what is typed in the find box, from the server, in place of filtering the ones shown. */
  search?: (q: string) => Promise<T[]>;
}

// ---- one small menu for every row (and the "…" menu): arrow keys, Home/End, Enter, Esc; an optional find box
function propMenu<T extends PropMenuItem>(d: HTMLElement, anchor: HTMLElement, {label, items = [], find = '', entry = '', none = 'Nothing to pick', onPick, onEntry, search}: PropMenuOptions<T>): PropPop | undefined {
  let pop: PropPop | null = $('.prop-pop', d);
  if (pop?.matches(':popover-open')) { const same = pop.anchor === anchor; pop.hidePopover(); if (same) return; }
  if (pop?.anchor && pop.anchor !== anchor) pop.anchor.setAttribute('aria-expanded', 'false');   // only one menu, only one expanded
  if (!pop) {
    const el = document.createElement('div') as PropPop;
    pop = el;
    el.className = 'tl-pop prop-pop'; el.popover = 'auto';
    el.addEventListener('toggle', ev => {
      if (ev.newState !== 'closed') return;
      const a = el.anchor;
      if (a?.isConnected && (el.contains(document.activeElement) || document.activeElement === document.body)) a.focus();
      el.dispatchEvent(new Event('menuclosed'));
    });
    el.addEventListener('keydown', ev => {
      const list = [...el.querySelectorAll<HTMLElement>('.tl-mi')].filter(b => !b.hidden);
      const input = $('.tl-menu-q', el) as HTMLInputElement | null;
      const at = list.indexOf(document.activeElement as HTMLElement);
      const go = (i: number): void => { ev.preventDefault(); ev.stopPropagation(); list[Math.max(0, Math.min(list.length - 1, i))]?.focus(); };
      if (ev.key === 'ArrowDown') return go(at < 0 ? 0 : at + 1);
      if (ev.key === 'ArrowUp') { if (at <= 0 && input) { ev.preventDefault(); ev.stopPropagation(); input.focus(); return; } return go(at - 1); }
      if (ev.key === 'Home' && at >= 0) return go(0);
      if (ev.key === 'End' && at >= 0) return go(list.length - 1);
      if (ev.key === 'Enter' && input && document.activeElement === input) {
        ev.preventDefault(); ev.stopPropagation();
        if (el.onEntry && input.value.trim()) el.onEntry(input.value.trim());
        else list[0]?.click();
      }
      if (['j', 'k', 'x', 'c', '/'].includes(ev.key)) ev.stopPropagation();   // the list's keys wait until the menu closes
    });
    d.append(el);
  }
  pop.anchor = anchor;
  pop.onEntry = onEntry ? v => { pop.hidePopover(); onEntry(v); } : null;
  // A menu with a find box is a group of plain buttons (a role=menu holds only menu items); either way ↑/↓ move.
  const menu = !(find || entry);
  const state = (it: PropMenuItem): string => it.checked == null ? '' : menu ? ` aria-checked="${it.checked ? 'true' : 'false'}"` : it.checked ? ' aria-current="true"' : '';
  const list = (rows: T[]) => `<div class="tl-menu-list">${rows.map((it, i) => `${it.sep ? '<div class="tl-menu-sep" role="separator"></div>' : ''}<button type="button"${menu ? ` role="${it.checked != null ? 'menuitemradio' : 'menuitem'}"` : ''}${state(it)} class="tl-mi${it.danger ? ' danger' : ''}" data-pick="${i}"${it.value != null ? ` data-prop-pick="${esc(it.value)}"` : ''} data-label="${esc(String(it.text || '').toLowerCase())}">${it.html || esc(it.text)}${it.checked ? '<span class="tl-mi-on" aria-hidden="true">✓</span>' : ''}</button>`).join('')}</div>
    ${rows.length || !none ? '' : `<div class="tl-menu-none">${esc(none)}</div>`}`;
  pop.innerHTML = `<div class="tl-menu" role="${menu ? 'menu' : 'group'}" aria-label="${esc(label)}"><div class="tl-menu-h" aria-hidden="true">${esc(label)}</div>
    ${find || entry ? `<input type="${entry ? 'text' : 'search'}" class="tl-menu-q" placeholder="${esc(entry || find)}" aria-label="${esc(entry || find)}" autocomplete="off" spellcheck="false">` : ''}
    <div class="tl-menu-rows">${list(items)}</div></div>`;
  const input = $('.tl-menu-q', pop) as HTMLInputElement | null, first = items;
  let asked = 0, timer = 0;
  if (input) input.oninput = () => {
    const q = input.value.trim().toLowerCase();
    if (search) {
      // The server finds them (it holds more than the menu shows); the last thing typed wins.
      clearTimeout(timer);
      const n = ++asked, show = (rows: T[]) => { if (n !== asked || !pop.isConnected) return; items = rows; $('.tl-menu-rows', pop)!.innerHTML = list(rows); };
      if (!q) { show(first); return; }
      timer = window.setTimeout(() => void search(input.value.trim()).then(show, () => show([])), 200);
      return;
    }
    // Each word typed finds the item on its own ("back ice" finds "Backlog › On ice").
    const words = q.split(/\s+/).filter(Boolean);
    for (const b of pop.querySelectorAll<HTMLElement>('.tl-mi')) b.hidden = !words.every(w => b.dataset.label!.includes(w));
  };
  pop.onclick = ev => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-pick]'); if (!b) return;
    const it = items[Number(b.dataset.pick)]!;
    pop.hidePopover();
    onPick?.(it);
  };
  pop.showPopover();
  if (!matchMedia('(max-width:760px)').matches) {
    const a = anchor.getBoundingClientRect();
    const below = a.bottom + 4 + pop.offsetHeight <= innerHeight - 8;
    pop.style.top = `${Math.max(8, below ? a.bottom + 4 : a.top - pop.offsetHeight - 4)}px`;
    pop.style.left = `${Math.max(8, Math.min(a.left, innerWidth - pop.offsetWidth - 8))}px`;
  } else pop.style.top = pop.style.left = '';
  (input || pop.querySelector<HTMLElement>('.tl-mi[aria-checked="true"], .tl-mi[aria-current="true"]') || pop.querySelector<HTMLElement>('.tl-mi'))?.focus();
  anchor.setAttribute('aria-expanded', 'true');
  pop.addEventListener('menuclosed', () => anchor.setAttribute('aria-expanded', 'false'), {once: true});
  return pop;
}
