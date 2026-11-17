/* ui/app/library-files.ts — Library › Files (#/library/files): every file the reader may see, with filters and search.
   The page is the library-files part (ui/parts.json); its shared names live in ui/app/pages-shell.ts.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- Library › Files
// A table across the page on a computer (name, kind, made by, team, updated) and two lines a row on a phone. The search
// and the filter button share the top row; the filters sit in a popover (the Tasks popover's classes) and show as chips
// under the row. Every filter rides in the address, so a view can be shared.
const LF_DATES = [['', 'Any time'], ['7', 'Past week'], ['30', 'Past month'], ['365', 'Past year']];
const LF_ROLES: [string, string][] = [['', 'Any role'], ['Spec', 'Spec'], ['QA report', 'QA report'], ['Release report', 'Release report'], ['Invoice', 'Invoice']];
const LF_FILTERS = ['q', 'kind', 'role', 'bot', 'writer', 'linked', 'team', 'task', 'days', 'pinned', 'archived'];
const LF_COUNTED = ['kind', 'role', 'bot', 'writer', 'linked', 'team', 'task', 'days', 'pinned', 'archived'];   // the filter button's count and the chips (not the search)
interface LfGroup { id: string; name: string }
// Every filter is always present: '' (or false) when off. lfQuery writes them to the address and back. role, linked (a
// target: task:, bot:, human: or goal:) and writer (bot: or human:) are the page filters the contract adds.
interface LfFilter { q: string; kind: string; role: string; bot: string; writer: string; linked: string; team: string; task: string; days: string; pinned: boolean; archived: boolean; [k: string]: string | boolean | undefined }
// What a page is written for (a link to a task, bot, person, goal or page) and who wrote it, as the page read sends them.
interface LfLink { target: string; role?: string; name?: string }
interface LfWriter { actor: string; name?: string }
// A file row of the Files list or a search hit, as the rows read it (`hit` marks a search hit).
type LfRow = { id: string; title: string; name?: string; library_kind?: string; bot?: string; bot_name?: string; locator?: string; hit?: boolean; url?: string | null; open?: {url?: string} | null; last_activity_at?: string | null; updated_at?: string; pins?: unknown[]; archived?: boolean; excerpt?: string; links?: LfLink[]; links_more?: number; writers?: LfWriter[]; };
interface LfState { root: HTMLElement; pop: HTMLElement; filter: LfFilter; rows: LfRow[]; cursor: string | null; seq: number; groups: LfGroup[] }
// The names the Linked to picker and its chip show: a target's name as it was picked or fetched, and the goal list.
const LF_NAMES = new Map<string, string>();
let LF_GOALS: {id: string; title: string}[] = [];
let LF_FIND = 0;   // the newest Linked to search: an answer to an older one is dropped
const lfTeamName = (id: string | undefined) => id ? (LF?.groups || []).find((g: LfGroup) => g.id === id)?.name || teamLabel(id) : '';
function lfQuery(filter: LfFilter) {
  const q = new URLSearchParams();
  for (const k of LF_FILTERS) if (filter[k]) q.set(k, filter[k] === true ? '1' : filter[k]);
  const s = q.toString();
  return s ? '?' + s : '';
}
function lfApiQuery(f: LfFilter, cursor?: string | null) {
  const q = new URLSearchParams({limit: '50'});
  for (const k of ['kind', 'role', 'bot', 'writer', 'linked', 'team', 'task']) if (f[k]) q.set(k, String(f[k]));
  if (f.days) q.set('since', new Date(Date.now() - Number(f.days) * 864e5).toISOString());
  if (f.pinned) q.set('pinned', 'true');
  if (f.archived) q.set('archived', 'true');
  if (cursor) q.set('cursor', cursor);
  return q.toString();
}
async function pageLibraryFiles() {
  const params = new URLSearchParams(S.route.split('?')[1] || '');
  const filter = Object.fromEntries(LF_FILTERS.map(k => [k, ['pinned', 'archived'].includes(k) ? params.get(k) === '1' : params.get(k) || ''])) as LfFilter;
  if (filter.bot) filter.bot = botByRef(filter.bot)?.id || filter.bot;     // an old link names the bot by slug
  const same = LF && document.body.contains(LF.root);
  if (!same) {
    // The one page header (ui/app/page-head.ts): the search and the shared filter icon are its tools, ⋯ last. The
    // filter popover sits outside the header, so it still opens when a phone folds the tools into ⋯.
    $('#main').innerHTML = `<div class="lf" id="lf-root">
      ${pageHead({title: 'Files', hook: 'lf-head',
        tools: `<div class="lf-search">${TL_ICON.search}<input class="lf-q" type="search" placeholder="Search" aria-label="Search files" autocomplete="off" spellcheck="false"></div>`
          + `<button type="button" class="tl-ib lf-fbtn" popovertarget="lf-filter-pop" aria-haspopup="true" aria-expanded="false" aria-label="Filters" title="Filters">${filterIcon(13)}<span class="ph-fold-word" aria-hidden="true">Filters</span><span class="tl-fbadge lf-fbn tnum" hidden></span></button>`})}
      <div class="tl-pop lf-pop" id="lf-filter-pop" popover></div>
      <div class="lf-bar" hidden></div>
      <div class="lf-list" aria-live="polite">${skelRows(6)}</div></div>`;
    LF = {root: $('#lf-root'), pop: $('#lf-filter-pop'), filter, rows: [], cursor: null, seq: 0, groups: []};
    lfWire();
    get('/v2/groups').then(g => { if (!LF) return; LF.groups = Array.isArray(g) ? g : g?.groups || []; lfControls(); }).catch(() => {});
    void lfGoalsLoad();
  } else if (LF) LF.filter = filter;
  lfControls();
  await lfLoad();
}
// The goals the Linked to picker names by their titles.
async function lfGoalsLoad() {
  try {
    const r = await get<import('../types/api').GoalList | null>('/v2/goals?all=1');
    LF_GOALS = (r?.goals || []).map(g => ({id: g.id, title: g.title || g.id}));
  } catch { /* the picker names a goal by its id */ }
}
// The count, the chips and the popover's ticks follow the filters; the search box keeps what is being typed.
function lfControls() {
  const state = LF;
  if (!state) return;
  const root = state.root, f = state.filter, q = root.querySelector<HTMLInputElement>('.lf-q'), btn = root.querySelector<HTMLElement>('.lf-fbtn'), badge = btn?.querySelector<HTMLElement>('.lf-fbn');
  if (!q || !btn || !badge) return;
  if (document.activeElement !== q) q.value = f.q;
  const n = LF_COUNTED.filter(k => f[k]).length;
  btn.classList.toggle('active', !!n);
  badge.hidden = !n; badge.textContent = n ? String(n) : '';
  btn.setAttribute('aria-label', n ? `Filters, ${n} active` : 'Filters');
  lfBarPaint();
  lfPopSync();
  if (f.linked.startsWith('task:') && !LF_NAMES.has(f.linked)) lfNameTask(f.linked);
}
function lfBarPaint() {
  const state = LF;
  if (!state) return;
  const f = state.filter, bar = state.root.querySelector<HTMLElement>('.lf-bar');
  if (!bar) return;
  // A chip's name opens the filter popover (its own popovertarget); its ✕ takes the filter off.
  const chip = (key: string, label: string, value: string) => `<span class="tl-chip"><button type="button" class="tl-chip-main" popovertarget="lf-filter-pop" aria-haspopup="true"`
    + ` aria-label="${esc(`${label ? label + ': ' : ''}${value}. Change`)}" title="${esc(label ? `${label}: ${value}` : value)}">`
    + `${label ? `<span class="k">${esc(label)}</span>` : ''}<span class="v">${esc(value)}</span></button>`
    + `<button type="button" class="tl-chip-x" data-lf-clear="${key}" aria-label="${esc(`Remove ${label || value} filter`)}">${TL_ICON.x}</button></span>`;
  const chips = [
    f.kind && chip('kind', 'Kind', LF_KINDS.find(([v]) => v === f.kind)?.[1] || f.kind),
    f.role && chip('role', 'Role', LF_ROLES.find(([v]) => v === f.role)?.[1] || f.role),
    f.bot && chip('bot', 'Bot', botDisplayName(f.bot)),
    f.writer && chip('writer', 'Written by', lfTargetName(f.writer)),
    f.linked && chip('linked', 'Linked to', lfTargetName(f.linked)),
    f.team && chip('team', 'Team', lfTeamName(f.team)),
    f.days && chip('days', 'Updated', LF_DATES.find(([v]) => v === f.days)?.[1] || f.days),
    f.task && chip('task', 'Task', 'One task'),
    f.pinned && chip('pinned', '', 'Pinned'),
    f.archived && chip('archived', '', 'Archived'),
  ].filter(Boolean);
  bar.hidden = !chips.length;
  bar.innerHTML = chips.length ? chips.join('') + '<button type="button" class="linkish lf-clear" data-lf-clear-all>Clear</button>' : '';
}
function lfWire() {
  const state = LF;
  if (!state) return;
  const root = state.root, pop = state.pop;
  let soon = 0;
  const search = root.querySelector<HTMLInputElement>('.lf-q');
  if (!search) return;
  search.oninput = (ev: Event) => { state.filter.q = (ev.target as HTMLInputElement).value.trim(); clearTimeout(soon); soon = setTimeout(lfChanged, 250); };
  root.onclick = ev => {
    if (eventElement(ev)?.closest<HTMLElement>('.lf-pop')) return;                // the popover's own handler (below) takes its clicks
    const clear = eventElement(ev)?.closest<HTMLElement>('[data-lf-clear]');
    if (clear) { state.filter[String(clear.dataset.lfClear)] = ''; lfChanged(); return; }
    if (eventElement(ev)?.closest<HTMLElement>('[data-lf-clear-all]')) { for (const k of LF_COUNTED) state.filter[k] = ''; lfChanged(); return; }
    if (eventElement(ev)?.closest<HTMLElement>('[data-lf-more]')) { void lfLoad(true); return; }
    // A row is one target (style guide, "Rows, pills and counts"): a click anywhere on it is a click on its name's link,
    // and ⌘ or Ctrl opens that link in a new tab, as the link itself would. A link in the meta line is a link of its own.
    const cell = eventElement(ev);
    const open = !cell?.closest('a, button') ? cell?.closest('tr.lf-row')?.querySelector<HTMLAnchorElement>('a.lf-open') : null;
    if (open) {
      if (ev.metaKey || ev.ctrlKey) window.open(open.href, '_blank', 'noopener'); else open.click();
      return;
    }
    const row = eventElement(ev)?.closest<HTMLElement>('a.lf-open[data-lf-open="tico"]');
    if (row && !ev.metaKey && !ev.ctrlKey) { ev.preventDefault(); void openFileLink(row.dataset.id, row.dataset.name); }
  };
  pop.onclick = (ev: MouseEvent) => {
    const pick = eventElement(ev)?.closest<HTMLElement>('[data-lf-set], [data-lf-toggle]');
    if (!pick) return;
    const key = pick.dataset.lfSet, value = pick.dataset.lfValue || '';
    if (key) {
      state.filter[key] = value;
      // A target picked in a menu keeps its name for the chip (a task's title is not in the address).
      if (value && pick.dataset.lfName && (key === 'linked' || key === 'writer')) LF_NAMES.set(value, pick.dataset.lfName);
    } else if (pick.dataset.lfToggle) state.filter[pick.dataset.lfToggle] = !state.filter[pick.dataset.lfToggle];
    lfChanged();
  };
  // A long bot list has a find box: it hides the items that do not match, within its own section. The Linked to find box
  // asks the bots, people, goals and tasks for its matches instead.
  pop.oninput = (ev: Event) => {
    const find = eventElement(ev)?.closest<HTMLInputElement>('.tl-menu-q'); if (!find) return;
    if (find.classList.contains('lf-find')) { void lfFindLinked(find); return; }
    const words = find.value.trim().toLowerCase();
    const section = find.closest<HTMLElement>('.lf-sec');
    if (!section) return;
    for (const b of section.querySelectorAll<HTMLElement>('.tl-mi')) b.hidden = !!words && !String(b.dataset.label).includes(words);
  };
  pop.onkeydown = tasksMenuKeys;
  // The popover is built as it opens (its teams and bots as they are now), placed under the button, and focused.
  pop.addEventListener('beforetoggle', (ev: Event) => { if ('newState' in ev && ev.newState === 'open') pop.innerHTML = lfPopHTML(); });
  pop.addEventListener('toggle', (ev: Event) => {
    const open = 'newState' in ev && ev.newState === 'open', btn = root.querySelector<HTMLElement>('.lf-fbtn');
    if (!btn) return;
    btn.setAttribute('aria-expanded', String(open));
    if (!open) return;
    lfMenuPlace(pop, btn);
    // The first pick in force, not the bot find box: a phone would open the keyboard over the sheet, and a
    // focus that scrolls the popover moves Kind out of sight.
    (pop.querySelector<HTMLElement>('[aria-checked="true"]') || pop.querySelector<HTMLElement>('.tl-mi, .lf-pill'))?.focus({preventScroll: true});
  });
}
function lfChanged() {
  const state = LF;
  if (!state) return;
  history.replaceState(null, '', RD_FILES + lfQuery(state.filter));
  S.route = LAST_ROUTE = location.hash;
  lfControls();
  void lfLoad();
}
// Under the filter button and inside the window; a phone's popover is the bottom sheet the stylesheet makes it.
function lfMenuPlace(pop: HTMLElement, anchor: Element) {
  if (matchMedia(PHONE).matches) { pop.style.top = pop.style.left = ''; return; }
  // A filter button the header folded into ⋯ is hidden once ⋯ closes: the menu goes under ⋯ instead.
  const at = anchor.getClientRects().length ? anchor : anchor.closest('header.ph')?.querySelector(':scope > .ph-more') || anchor;
  const r = at.getBoundingClientRect();
  pop.style.top = `${Math.max(8, Math.min(r.bottom + 6, innerHeight - pop.offsetHeight - 8))}px`;
  pop.style.left = `${Math.max(8, Math.min(r.right - pop.offsetWidth, innerWidth - pop.offsetWidth - 8))}px`;
}
// The pieces of a filter menu: a pill (one of a row), a pick (a radio item with its name for a chip), a check (a toggle).
const lfPill = (f: LfFilter, key: string, value: string, label: string) => `<button type="button" class="tl-mi lf-pill" role="menuitemradio" aria-checked="${f[key] === value}"`
  + ` data-lf-set="${key}" data-lf-value="${esc(value)}">${esc(label)}</button>`;
function lfItem(f: LfFilter, key: string, value: string, label: string, face: string, find: string) {
  return `<button type="button" class="tl-mi" role="menuitemradio" aria-checked="${f[key] === value}"`
    + ` data-lf-set="${key}" data-lf-value="${esc(value)}" data-lf-name="${esc(label)}" data-label="${esc(find.toLowerCase())}">${face ? `<span class="tl-opt-face">${face}</span>` : ''}`
    + `<span class="lf-mi-name">${esc(label)}</span><span class="tl-mi-on" aria-hidden="true">✓</span></button>`;
}
const lfCheck = (f: LfFilter, key: string, label: string) => `<button type="button" class="tl-mi" role="menuitemcheckbox" aria-checked="${!!f[key]}" data-lf-toggle="${key}">`
  + `<span class="lf-mi-name">${label}</span><span class="tl-mi-on" aria-hidden="true">✓</span></button>`;
function lfPopHTML() {
  const state = LF;
  if (!state) return '';
  const f = state.filter;
  const bots = (S.emps || []).filter(e => e.status !== 'archived').map(e => ({id: e.id, name: botDisplayName(e.id)}))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, {numeric: true}));
  const people = (S.people || []).filter(p => !p.left && !p.hidden);
  const groups = state.groups || [];
  return `<div class="tl-menu lf-menu" role="group" aria-label="Filters">
    <div class="lf-sec" role="group" aria-label="Kind"><div class="tl-menu-h" aria-hidden="true">Kind</div>
      <div class="lf-pills">${LF_KINDS.map(([v, l]) => lfPill(f, 'kind', String(v), String(l))).join('')}</div>
      <div class="tl-menu-h" aria-hidden="true">Role</div>
      <div class="lf-pills">${LF_ROLES.map(([v, l]) => lfPill(f, 'role', v, l)).join('')}</div></div>
    <div class="lf-sec" role="group" aria-label="Updated"><div class="tl-menu-h" aria-hidden="true">Updated</div>
      <div class="lf-pills">${LF_DATES.map(([v, l]) => lfPill(f, 'days', String(v), String(l))).join('')}</div></div>
    <div class="lf-sec" role="group" aria-label="Bot"><div class="tl-menu-h" aria-hidden="true">Bot</div>
      ${bots.length > 7 ? '<input type="search" class="tl-menu-q" placeholder="Find a bot" aria-label="Find a bot" autocomplete="off" spellcheck="false">' : ''}
      <div class="tl-menu-list">${lfItem(f, 'bot', '', 'All bots', '', 'all bots')}${bots.map(b => lfItem(f, 'bot', b.id, b.name, avatar(b.id, 16), `${b.name} ${b.id}`)).join('')}</div></div>
    ${groups.length ? `<div class="lf-sec" role="group" aria-label="Team"><div class="tl-menu-h" aria-hidden="true">Team</div>
      <div class="tl-menu-list">${lfItem(f, 'team', '', 'All teams', '', 'all teams')}${groups.map((g: LfGroup) => lfItem(f, 'team', g.id, g.name, '', g.name)).join('')}</div></div>` : ''}
    <div class="lf-sec" role="group" aria-label="Linked to" data-lf-sec="linked"><div class="tl-menu-h" aria-hidden="true">Linked to</div>
      <input type="search" class="tl-menu-q lf-find" placeholder="Find a task, bot, person or goal" aria-label="Find a task, bot, person or goal" autocomplete="off" spellcheck="false">
      <div class="tl-menu-list">${lfItem(f, 'linked', '', 'Any link', '', 'any')}${f.linked ? lfItem(f, 'linked', f.linked, lfTargetName(f.linked), '', lfTargetName(f.linked)) : ''}</div></div>
    <div class="lf-sec" role="group" aria-label="Written by"><div class="tl-menu-h" aria-hidden="true">Written by</div>
      <input type="search" class="tl-menu-q" placeholder="Find a person or bot" aria-label="Find a person or bot" autocomplete="off" spellcheck="false">
      <div class="tl-menu-list">${lfItem(f, 'writer', '', 'Anyone', '', 'anyone')}${bots.map(b => lfItem(f, 'writer', `bot:${b.id}`, b.name, avatar(b.id, 16), `${b.name} ${b.id}`)).join('')}${people.map(p => lfItem(f, 'writer', `human:${p.id}`, p.name || p.id, personAvatar(p, 16), String(p.name || p.id))).join('')}</div></div>
    <div class="tl-menu-sep" role="separator"></div>
    <div class="lf-sec" role="group" aria-label="Show">${lfCheck(f, 'pinned', 'Pinned')}${lfCheck(f, 'archived', 'Archived')}</div>
  </div>`;
}
// The ticks follow a change while the popover stays open (its find boxes keep what was typed).
function lfPopSync() {
  const state = LF;
  if (!state?.pop?.matches(':popover-open')) return;
  for (const el of state.pop.querySelectorAll<HTMLElement>('[data-lf-set]')) el.setAttribute('aria-checked', String(state.filter[String(el.dataset.lfSet)] === el.dataset.lfValue));
  for (const el of state.pop.querySelectorAll<HTMLElement>('[data-lf-toggle]')) el.setAttribute('aria-checked', String(!!state.filter[String(el.dataset.lfToggle)]));
}
// The name a target shows: the one picked or fetched (LF_NAMES), else a bot's or person's name, a goal's title, or the id.
function lfTargetName(t: string): string {
  if (LF_NAMES.has(t)) return LF_NAMES.get(t) || t;
  const i = t.indexOf(':'), kind = t.slice(0, i), id = t.slice(i + 1);
  if (kind === 'bot') return botDisplayName(id);
  if (kind === 'human') return (S.people || []).find(p => p.id === id)?.name || id;
  if (kind === 'goal') return LF_GOALS.find(g => g.id === id)?.title || id;
  return id;
}
// A link target's route: a task, bot, person, goal or page (the contract's target forms).
function lfTargetHref(t: string): string {
  const i = t.indexOf(':'), kind = t.slice(0, i), raw = t.slice(i + 1), id = encodeURIComponent(raw);
  if (kind === 'task') return '#/task/' + id;
  if (kind === 'bot') return '#/bot/' + id;
  if (kind === 'human') return '#/person/' + id;
  if (kind === 'goal') return '#/goals/' + id;
  return kind === 'page' ? pageHref(raw) : '#';
}
// A task a link names (not picked here) gets its title once, for the chip; until it comes the chip shows its id.
function lfNameTask(target: string) {
  const id = target.slice(target.indexOf(':') + 1);
  LF_NAMES.set(target, id);
  taskBriefsPage({ids: [id], limit: 1}).then(r => {
    const t = r.tasks[0];
    if (t) LF_NAMES.set(target, t.title || id);
    if (LF) lfBarPaint();
  }).catch(() => {});
}
// The Linked to find box: the bots, people and goals whose names match, then the tasks the Tasks search finds (two
// letters or more). Its Any link item stays first; a late task answer for an older search is dropped.
async function lfFindLinked(find: HTMLInputElement) {
  const state = LF, list = find.closest<HTMLElement>('.lf-sec')?.querySelector<HTMLElement>('.tl-menu-list');
  if (!state || !list) return;
  const words = find.value.trim().toLowerCase(), seq = ++LF_FIND, f = state.filter;
  const hit = (s: string) => s.toLowerCase().includes(words);
  const bots = (S.emps || []).filter(e => e.status !== 'archived' && hit(`${botDisplayName(e.id)} ${e.id}`)).slice(0, 6)
    .map(e => lfItem(f, 'linked', `bot:${e.id}`, botDisplayName(e.id), avatar(e.id, 16), `${botDisplayName(e.id)} ${e.id}`));
  const people = (S.people || []).filter(p => !p.left && !p.hidden && hit(String(p.name || p.id))).slice(0, 6)
    .map(p => lfItem(f, 'linked', `human:${p.id}`, p.name || p.id, personAvatar(p, 16), String(p.name || p.id)));
  const goals = LF_GOALS.filter(g => hit(g.title)).slice(0, 6).map(g => lfItem(f, 'linked', `goal:${g.id}`, g.title, '', g.title));
  list.innerHTML = lfItem(f, 'linked', '', 'Any link', '', 'any') + (words ? [...bots, ...people, ...goals].join('') : '');
  if (words.length < 2) return;
  try {
    const r = await taskBriefsPage({search: words, limit: 8});
    if (seq !== LF_FIND || !list.isConnected) return;
    list.insertAdjacentHTML('beforeend', r.tasks.map(t => lfItem(f, 'linked', `task:${t.id}`, t.title || t.id, '', t.title || t.id)).join(''));
  } catch { /* the bots, people and goals above still show */ }
}
// Search (the box) asks GET /api/v2/files/search with the role, linked and writer filters and narrows its hits by kind and
// bot here; the list asks GET /api/v2/files with every filter, a page at a time.
async function lfLoad(more = false) {
  const state = LF;
  if (!state) return;
  const seq = ++state.seq, f = state.filter, list = state.root.querySelector<HTMLElement>('.lf-list');
  if (!list) return;
  if (!more) list.innerHTML = skelRows(6);
  try {
    if (f.q) {
      const sq = new URLSearchParams({q: f.q, limit: '50'});
      for (const k of ['role', 'linked', 'writer']) if (f[k]) sq.set(k, String(f[k]));
      const r = await get<import('../types/api').FileSearch>(`/v2/files/search?${sq}`, {fresh: true});
      if (LF !== state || seq !== state.seq) return;
      state.rows = (r.results || []).filter(h => (!f.kind || h.library_kind === f.kind) && (!f.bot || h.bot === f.bot))
        .map(h => ({...h, last_activity_at: h.updated_at, hit: true}));
      state.cursor = null;
    } else {
      const r = await get('/v2/files?' + lfApiQuery(f, more ? state.cursor : ''), {fresh: true});
      if (LF !== state || seq !== state.seq) return;
      state.rows = more ? state.rows.concat(r.files || []) : r.files || [];
      state.cursor = r.has_more ? r.next_cursor : null;
    }
  } catch (e) {
    if (LF === state && seq === state.seq) list.innerHTML = `<p class="err lf-pad">${esc(errorMessage(e, ''))}</p>`;
    return;
  }
  lfDraw();
}
// The name a row shows (style guide, Files: "the name carries its ending", never a path): a page's and a link's title as
// written; a stored file's own name when its title is that file's path (guides/holiday-setup.md reads
// holiday-setup.md), or the last part of a title that is a path (a search hit has no name). A title a bot gave with a
// slash in its words ("Q3 plan / draft") is kept whole. The full title stays the name's tooltip.
function lfName(f: LfRow): string {
  const title = String(f.title || f.name || '');
  if (f.library_kind === 'page' || f.locator === 'remote_link' || (f.hit && f.url) || !title.includes('/')) return title;
  // A stored file knows its own name: a title with a slash in it shows that name ("Sales 2024/25.xlsx" stays whole).
  if (f.name) return f.name;
  // A search hit has no name: only a title that reads as a path (no spaces in any part, an ending) loses its folders.
  const parts = title.split('/');
  const pathLike = parts.every(p => p && !/\s/.test(p)) && /\.[a-z0-9]{1,8}$/i.test(parts.at(-1) || '');
  return pathLike ? parts.at(-1) || title : title;
}
// A page's links for its meta line: "Spec of D-18463, D-19385 +2", each target a link to its route. The contract sends five
// links and a count of the rest (links_more). A link with no role is named alone.
function lfPageLinks(f: LfRow): string {
  const groups: [string, LfLink[]][] = [];
  for (const l of f.links || []) {
    const role = l.role || '', g = groups.find(([r]) => r === role);
    if (g) g[1].push(l); else groups.push([role, [l]]);
  }
  const names = groups.map(([role, ls]) => (role && role !== 'related' ? `${esc(role)} of ` : '')
    + ls.map(l => `<a href="${esc(lfTargetHref(l.target))}">${esc(l.name || l.target)}</a>`).join(', ')).join(' · ');
  return names ? names + (f.links_more ? ` +${Number(f.links_more)}` : '') : '';
}
// One row of the shared table (.tt-list, ui/styles/tools.css, as Contacts and Skills draw theirs). The name cell holds the
// row's link (its icon and title) and, beside it, the meta line and the search excerpt: the two-line form a phone shows
// (the name, then the meta, then the excerpt); a computer shows the excerpt on the name's line and the columns beside it.
// A click elsewhere on the row is a click on that link (lfWire). A page's meta names its links and its writers (or its
// bot when none is known).
function lfRowHTML(f: LfRow) {
  const kind = f.library_kind || 'other';
  const page = kind === 'page', link = f.locator === 'remote_link' || (f.hit && f.url);
  const url = page ? pageHref(f.id) : link ? (f.open?.url || f.url) : f.open?.url || `${API}/v2/files/${encodeURIComponent(f.id)}`;
  // A Google file opens in the review view, in Google's own preview (file-review.ts); any other link at its site.
  const google = link && !f.hit && !!googlePreviewUrl(String(url || ''));
  const attrs = page ? '' : link ? ` target="_blank" rel="noopener noreferrer"${google ? ` data-lf-open="tico" data-id="${esc(f.id)}" data-name="${esc(f.title)}"` : ''}`
    : ` data-lf-open="tico" data-id="${esc(f.id)}" data-name="${esc(f.name || f.title)}"`;
  const kindName = (LF_KINDS.find(([v]) => v === kind)?.[1] || 'Other').replace(/s$/, '');
  const bot = pageBotName(f), when = ago(f.last_activity_at), team = lfTeamName(botRow(f.bot)?.team) || '';
  const who = page && f.writers?.length ? f.writers.map(w => w.name || w.actor.replace(/^[a-z]+:/, '')).join(', ') : bot;
  const first = page ? lfPageLinks(f) || esc(kindName) : esc(kindName);
  // Pinned and archived are marks, not pills (style guide: a pill only when something is wrong); the mark says where
  // it is pinned on hover and to a screen reader.
  const pinned = f.pins || [];
  const pinWords = pinned.length > 1 ? `Pinned in ${pinned.length} places` : 'Pinned';
  const pins = pinned.length ? `<span class="lf-mark lf-pin" role="img" aria-label="${esc(pinWords)}" title="${esc('Pinned: ' + pinned.join(', '))}">${TL_ICON.pin}</span>` : '';
  const archived = f.archived ? '<span class="lf-mark lf-arch nav-icon" role="img" aria-label="Archived" title="Archived">archive</span>' : '';
  const excerpt = f.excerpt ? `<span class="lf-excerpt">${esc(f.excerpt)}</span>` : '';
  const name = lfName(f);
  return `<tr class="lf-row"><td class="lf-name"><div class="lf-cell"><a class="lf-open" href="${esc(url)}"${attrs}>
      <span class="nav-icon lf-ico" aria-hidden="true">${link ? 'link' : LF_ICON[kind] || 'inventory_2'}</span>
      <span class="lf-text"><span class="lf-line"><span class="lf-title" title="${esc(f.title)}">${esc(name)}</span>${pins}${archived}</span></span></a>
      <span class="lf-meta">${first} · ${esc(who)} · ${esc(when)}</span>${excerpt}</div></td>
    <td class="lf-kind">${esc(kindName)}</td>
    <td class="lf-by"><span class="lf-by-in">${page && f.writers?.length ? '' : f.bot ? avatar(f.bot, 16) : ''}<span class="lf-by-n">${esc(who)}</span></span></td>
    <td class="lf-team">${esc(team)}</td>
    <td class="lf-when" title="${esc(fmt(f.last_activity_at))}">${esc(when)}</td></tr>`;
}
function lfDraw() {
  const state = LF;
  if (!state) return;
  const root = state.root, list = root.querySelector<HTMLElement>('.lf-list'), rows = state.rows;
  if (!list) return;
  const any = LF_FILTERS.some(k => state.filter[k]);
  list.innerHTML = rows.length ? `<table class="tt-list lf-tbl"><thead><tr><th class="lf-name">Name</th><th class="lf-kind">Kind</th>`
      + `<th class="lf-by">Made by</th><th class="lf-team">Team</th><th class="lf-when">Updated</th></tr></thead><tbody>${rows.map(lfRowHTML).join('')}</tbody></table>`
      + (state.cursor ? '<button type="button" class="ghost lf-more" data-lf-more>More</button>' : '')
    : `<p class="muted lf-pad">${any ? 'Nothing matches.' : 'No files yet.'}</p>`;
}
