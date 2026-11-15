/* ui/app/directory.ts — Library › Contacts (#/library/contacts; it was Library › Directory, and #/library/directory
   still lands here): the people, organizations and places the company knows, as the reader may see them. Documents are
   not contacts: their type never shows here (a document is a file, linked from its contact's page). View and find only;
   a note is the one thing written here.
   Backend: backend/entities/routes.py over backend/entities/store.py, which applies the read rule
   (backend/entities/visible.py) to every row, count, link and column. The page only draws what comes back.
   Addresses:
     #/library/contacts[?type=<key>&q=<prefix>&link=<link key>]   the list (People by default)
     #/library/contacts/<id>                                      one entity (ui/app/directory-entity.ts)
   Settings › Contacts is ui/app/directory-settings.ts. Classic script: globals are shared with ui/app/. */
'use strict';

const DIR_MAIN_TYPES = ['person', 'organization', 'place'];   // the hub leaves documents out (store.NOT_CONTACTS)
// How a link to us reads as a relationship (backend/entities/schema.py link types).
const DIR_RELATIONS: Record<string, string> = {
  customer_of: 'Customer', vendor_to: 'Vendor', partner_of: 'Partner', invests_in: 'Investor', advises: 'Advisor',
  represents: 'Represents', regulates: 'Regulator', sells_to: 'Reseller', competes_with: 'Competitor',
};
// Where a row is known from (entity_sources.source_type, entity_values.source_type).
const DIR_SOURCES: Record<string, string> = {
  mailbox: 'Mail', meeting: 'Meetings', finance_book: 'Finance', note_me: 'My note', market: 'Market', hub: 'Tico',
  system: 'Tico', counsel: 'Rules Owner', company_page: 'Company page', places: 'Shared',
};

interface DirOrgRef { id: string; name: string; links_to_us: string[] }
interface DirBrief {
  id: string; type: string; name: string; status: string;
  primary_email: string | null; primary_domain: string | null;
  links_to_us: string[]; last_activity_at: string | null;
  organization?: DirOrgRef | null; owner?: string | null; sources?: string[];
}
interface DirFind { rows: DirBrief[]; next: string | null; count: number; approximate: boolean }
interface DirType {
  key: string; name: string; plural_name: string; description: string; icon: string | null;
  built_in: number | boolean; mode: string | null; created_by_type: string | null; created_by_id: string | null;
}
interface DirCount { type: string; count: number; approximate: boolean }
// search counts the list's searches (a read from the top): only the newest one's rows or error draw.
interface DirListState { run: number; type: string; q: string; link: string; next: string | null; rows: DirBrief[]; search: number }

let DIRP: { run: number } | null = null;   // the page on screen: a late answer for a page left behind draws nothing
let DIRL: DirListState | null = null;      // the list's query and the rows shown
// The relationship filter's menu (task-list-menus.ts tasksMenu keeps its anchor here).
const DIR_MENU: TasksMenuHost = {};
// The list's columns seen so far for the type on screen: a column a search or a filter empties stays (no column jumps
// while typing); a new type, or leaving Contacts, starts again.
let DIR_SEEN: { type: string; cols: Set<string> } | null = null;

const dirHref = (id: string): string => DIR_ROUTE + '/' + encodeURIComponent(id);
const dirListHref = (type: string, link = ''): string => {
  const q = new URLSearchParams();
  if (type && type !== 'person') q.set('type', type);
  if (link) q.set('link', link);
  const s = q.toString();
  return DIR_ROUTE + (s ? '?' + s : '');
};
const dirLive = (run: number): boolean => DIRP !== null && DIRP.run === run;
const dirErr = (e: unknown): string => `<p class="err" data-dir-error>${esc(e instanceof Error ? e.message : String(e))}</p>`;
const dirN = (n: number, approximate = false): string => approximate ? `${Math.min(n, 1000).toLocaleString()}+` : n.toLocaleString();
const dirInitials = (name: string): string => name.split(/[\s&]+/).filter(Boolean).slice(0, 2).map(w => w[0] ?? '').join('').toUpperCase() || '?';
const dirRelation = (key: string): string => DIR_RELATIONS[key] ?? key.replace(/_/g, ' ');
const dirSource = (key: string): string => DIR_SOURCES[key] ?? key.replace(/_/g, ' ');
// A relationship is plain muted words, not a pill (style guide, Status: "a pill where plain muted text would do").
const dirRelationChip = (keys: readonly string[]): string => keys.length
  ? `<span class="muted dir-rel" data-dir-rel="${esc(keys.join(' '))}">${esc(keys.map(dirRelation).join(', '))}</span>` : '';
// An owner or author as "human:<id>" / "bot:<slug>" (store._owner, note author_key).
function dirWho(key: string | null | undefined): string {
  if (!key) return '';
  const [kind = '', id = ''] = key.split(':');
  if (kind === 'human' || kind === 'bot') return String(actorLabel(key));
  return id || kind;
}
function dirTypeName(types: readonly DirType[], key: string, plural = false): string {
  const t = types.find(x => x.key === key);
  if (!t) return key.charAt(0).toUpperCase() + key.slice(1);
  return plural ? t.plural_name || t.name : t.name;
}
function dirReadQuery(query: string): { type: string; q: string; link: string } {
  const p = new URLSearchParams(query);
  return { type: p.get('type') || 'person', q: p.get('q') || '', link: p.get('link') || '' };
}
// Keeps the address in step with the search box without a hashchange (the router would redraw and drop focus).
function dirQuietAddress(type: string, q: string, link: string): void {
  const p = new URLSearchParams();
  if (type !== 'person') p.set('type', type);
  if (q) p.set('q', q);
  if (link) p.set('link', link);
  const s = p.toString(), href = DIR_ROUTE + (s ? '?' + s : '');
  if (location.hash !== href) history.replaceState(history.state, '', href);
  S.route = href;
}

// ----------------------------------------------------------------- the list
async function pageDirectory(query: string): Promise<void> {
  const run = (DIRP?.run ?? 0) + 1;
  DIRP = { run };
  const want = dirReadQuery(query);
  const main = document.getElementById('main');
  if (!main) return;
  main.innerHTML = `<div class="tt dir" id="dir-root" data-dir-page="list">${skelRows(6)}</div>`;
  let types: DirType[], counts: DirCount[];
  try {
    const [t, n] = await Promise.all([
      get<{ types: DirType[] }>('/v2/entities/types', { fresh: true }),
      get<{ counts: DirCount[] }>('/v2/entities/counts', { fresh: true }),
    ]);
    types = t.types; counts = n.counts;
  } catch (e) {
    if (dirLive(run)) main.innerHTML = `<div class="tt dir">${dirErr(e)}</div>`;
    return;
  }
  if (!dirLive(run)) return;
  const counted = new Map(counts.map(c => [c.type, c]));
  // The four main tabs always; any other type once it holds a record (a type a bot added, say).
  const tabs = types.filter(t => t.mode !== 'archived'
    && (DIR_MAIN_TYPES.includes(t.key) || (counted.get(t.key)?.count ?? 0) > 0))
    .sort((a, b) => dirTabOrder(a.key) - dirTabOrder(b.key));
  const type = tabs.some(t => t.key === want.type) ? want.type : 'person';
  DIRL = { run, type, q: want.q, link: want.link, next: null, rows: [], search: 0 };
  const root = document.getElementById('dir-root');
  if (!root) return;
  // The one page header (ui/app/page-head.ts): the types are its tabs, each with its count; the search and the shared
  // filter icon (relationship) are its tools; ⋯ last. A relationship in force shows as a chip under the header, as the
  // Tasks list's and Files' filters do; the menu itself is the shared popover (#task-filter-pop), outside the header so
  // it opens even when a phone folds the tools into ⋯.
  const head = pageHead({title: 'Contacts', hook: 'dir-head', tabsAttrs: 'aria-label="Contacts"',
    tabs: tabs.map(t => {
      const n = counted.get(t.key);
      return {label: t.plural_name || t.name, href: dirListHref(t.key), cur: t.key === type, attrs: `data-dir-tab="${esc(t.key)}"`,
        count: n && n.count ? dirN(n.count, n.approximate) : undefined};
    }),
    tools: `<input type="search" class="dir-q" data-dir-q placeholder="Search" aria-label="Search by name" value="${esc(want.q)}" autocomplete="off" spellcheck="false">`
      + `<button type="button" class="tl-ib${want.link ? ' active' : ''}" data-dir-filter aria-haspopup="menu" aria-expanded="false" aria-label="${want.link ? 'Relationship, 1 active' : 'Relationship'}" title="Relationship">${filterIcon(13)}<span class="ph-fold-word" aria-hidden="true">Relationship</span><span class="tl-fbadge tnum" aria-hidden="true"${want.link ? '' : ' hidden'}>1</span></button>`});
  root.innerHTML = `${head}<div id="task-filter-pop" class="tl-pop" popover></div>
    ${want.link ? `<div class="tl-bar dir-active"><span class="tl-chip"><button type="button" class="tl-chip-main" data-dir-filter-chip aria-haspopup="menu" aria-label="${esc('Relationship: ' + dirRelation(want.link) + '. Change')}"><span class="k">Relationship</span><span class="v">${esc(dirRelation(want.link))}</span><span class="k tnum" data-dir-chip-n></span></button>`
      + `<a class="tl-chip-x" href="${dirListHref(type)}" data-dir-clear-link aria-label="Remove Relationship filter">${TL_ICON.x}</a></span></div>` : ''}
    <div data-dir-body>${skelRows(6)}</div>`;
  dirBindList(root, types);
  await dirLoadRows(root, types, true);
}
function dirTabOrder(key: string): number {
  const i = DIR_MAIN_TYPES.indexOf(key);
  return i < 0 ? DIR_MAIN_TYPES.length : i;
}
// The relationship menu: All, then each relationship; a pick is a link, so the address carries the filter.
function dirFilterMenu(button: HTMLElement): void {
  const state = DIRL;
  if (!state) return;
  // Opening the menu closes the header's ⋯; a filter button folded into it is hidden then, so the menu goes under ⋯.
  const anchor = button.closest('.ph-menu') ? button.closest('header.ph')?.querySelector<HTMLElement>(':scope > .ph-more') || button : button;
  tasksMenu(DIR_MENU, anchor, `<div class="tl-menu dir-filter-menu" role="menu" aria-label="Relationship">${[['', 'All'], ...Object.keys(DIR_RELATIONS).map(k => [k, dirRelation(k)])].map(([k = '', label = '']) =>
    `<a class="tl-mi" role="menuitemradio" aria-checked="${k === state.link}" href="${dirListHref(state.type, k)}" data-dir-link="${esc(k)}"><span class="lf-mi-name">${esc(label)}</span><span class="tl-mi-on" aria-hidden="true">${k === state.link ? '✓' : ''}</span></a>`).join('')}</div>`,
  pop => { pop.onclick = ev => { if (eventElement(ev)?.closest('a[data-dir-link]')) tasksMenuClose(); }; });
}
function dirBindList(root: HTMLElement, types: DirType[]): void {
  const input = root.querySelector<HTMLInputElement>('[data-dir-q]');
  const pop = root.querySelector<HTMLElement>('#task-filter-pop');
  root.querySelectorAll<HTMLElement>('[data-dir-filter], [data-dir-filter-chip]').forEach(b => { b.onclick = () => dirFilterMenu(b); });
  if (pop) {
    pop.onkeydown = tasksMenuKeys;
    pop.addEventListener('beforetoggle', ev => {
      const open = 'newState' in ev && ev.newState === 'open', anchor = DIR_MENU.menuAnchor;
      anchor?.setAttribute('aria-expanded', String(open));
      if (!open && anchor && pop.contains(document.activeElement)) requestAnimationFrame(() => { if (anchor.isConnected) anchor.focus(); });
    });
  }
  let timer = 0;
  input?.addEventListener('input', () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      if (!DIRL) return;
      DIRL.q = input.value.trim();
      dirQuietAddress(DIRL.type, DIRL.q, DIRL.link);
      void dirLoadRows(root, types, true);
    }, 220);
  });
}
async function dirLoadRows(root: HTMLElement, types: DirType[], reset: boolean): Promise<void> {
  const state = DIRL;
  if (!state) return;
  const body = root.querySelector<HTMLElement>('[data-dir-body]');
  if (!body) return;
  const p = new URLSearchParams({ detail: 'true' });
  if (state.q) p.set('name', state.q);
  if (state.link) p.set('link', state.link);
  if (!reset && state.next) p.set('after', state.next);
  // A new search starts a new count; Load more stays within the search it extends.
  const search = reset ? ++state.search : state.search;
  // A newer search has been typed since (or the page left): its own read draws, rows or error.
  const current = (): boolean => DIRL === state && dirLive(state.run) && state.search === search;
  let page: DirFind;
  try {
    page = await get<DirFind>(`/v2/entities/find/${encodeURIComponent(state.type)}?${p.toString()}`, { fresh: true });
  } catch (e) {
    if (current()) body.innerHTML = dirErr(e);
    return;
  }
  if (!current()) return;
  state.rows = reset ? page.rows : [...state.rows, ...page.rows];
  state.next = page.next;
  // The filter chip says how many match (the tab keeps the whole type's count).
  const matched = root.querySelector<HTMLElement>('[data-dir-chip-n]');
  if (matched && state.link) matched.textContent = dirN(page.count, page.approximate);
  const plural = dirTypeName(types, state.type, true).toLowerCase();
  if (!state.rows.length) {
    body.innerHTML = `<div class="empty" data-dir-empty>${state.q || state.link ? 'No matches.' : `No ${esc(plural)} yet.`}</div>`;
    return;
  }
  // No "8 people" line: the tab already counts them (style guide: no captions).
  body.innerHTML = `${dirTable(state.type, state.rows)}
    ${state.next ? '<div class="dir-more"><button type="button" class="ghost" data-dir-more>Load more</button></div>' : ''}`;
  body.querySelector('[data-dir-more]')?.addEventListener('click', ev => {
    const b = ev.currentTarget;
    if (b instanceof HTMLButtonElement) { b.disabled = true; b.textContent = 'Loading…'; }
    void dirLoadRows(root, types, false);
  });
  body.querySelector('tbody')?.addEventListener('click', ev => {
    const at = eventElement(ev);
    if (!at || at.closest('a, button')) return;
    const href = at.closest('tr')?.querySelector('a[href]')?.getAttribute('href');
    if (href) location.hash = href;
  });
}
// The list's columns: the name, then only the columns some row has a value for (style guide, "A table of things you
// manage": no column that says the same thing on every row, and an empty one says nothing at all).
function dirTable(type: string, rows: readonly DirBrief[]): string {
  const person = type === 'person', org = type === 'organization';
  const relOf = (r: DirBrief) => person ? (r.links_to_us.length ? r.links_to_us : r.organization?.links_to_us ?? []) : r.links_to_us;
  const cols: {head: string; cls: string; cell: (r: DirBrief) => string}[] = [
    ...(person ? [{head: 'Organization', cls: 'dir-org', cell: (r: DirBrief) => r.organization ? `<a href="${dirHref(r.organization.id)}">${esc(r.organization.name)}</a>` : ''}] : []),
    ...(org ? [{head: 'Domain', cls: 'dir-org muted', cell: (r: DirBrief) => esc(r.primary_domain ?? '')}] : []),
    ...(person || org ? [{head: 'Relationship', cls: 'dir-relcell', cell: (r: DirBrief) => dirRelationChip(relOf(r))}] : []),
    {head: 'Owner', cls: 'dir-owner muted', cell: (r: DirBrief) => esc(dirWho(r.owner))},
    {head: 'Last contact', cls: 'dir-last muted', cell: (r: DirBrief) => r.last_activity_at ? agoSpan(r.last_activity_at) : ''},
    {head: 'From', cls: 'dir-from muted', cell: (r: DirBrief) => esc((r.sources ?? []).map(dirSource).join(' · '))},
  ];
  if (DIR_SEEN?.type !== type) DIR_SEEN = { type, cols: new Set() };
  for (const c of cols) if (rows.some(r => c.cell(r) !== '')) DIR_SEEN.cols.add(c.head);
  const seen = DIR_SEEN.cols;
  const shown = cols.filter(c => seen.has(c.head));
  return `<table class="tt-list dir-list" data-dir-type="${esc(type)}"><thead><tr><th>Name</th>${shown.map(c => `<th>${c.head}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(r => `<tr data-dir-row="${esc(r.id)}">
        <td class="dir-name"><a href="${dirHref(r.id)}"><span class="dir-av" aria-hidden="true">${esc(dirInitials(r.name))}</span><strong>${esc(r.name)}</strong></a></td>
        ${shown.map(c => `<td class="${c.cls}" data-label="${c.head}">${c.cell(r)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

// Leaving the page drops its state, so a late answer draws nothing.
function directoryStop(r: string): void {
  if (!rdUnder((r || '').split('?')[0] ?? '', DIR_ROUTE)) { DIRP = null; DIRL = null; DIR_SEEN = null; }
}
