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
let DIR_MENU_WIRED = false;

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
const dirRelationChip = (keys: readonly string[]): string => keys.length
  ? keys.map(k => `<span class="pill dir-rel" data-dir-rel="${esc(k)}">${esc(dirRelation(k))}</span>`).join(' ') : '';
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
  const relations = Object.keys(DIR_RELATIONS);
  root.innerHTML = `<header class="tt-head"><h1>Contacts</h1><span class="spacer"></span>
      <button type="button" class="ghost small dir-icon" data-dir-search-toggle aria-label="Search" aria-expanded="${want.q ? 'true' : 'false'}"><span class="nav-icon" aria-hidden="true">search</span></button>
      <details class="dir-menu" data-dir-link-menu><summary class="ghost small dir-icon${want.link ? ' on' : ''}" aria-label="Relationship">${filterIcon(16)}</summary>
        <div class="dir-menu-list" role="menu">
          <a role="menuitemradio" aria-checked="${want.link ? 'false' : 'true'}" href="${dirListHref(type)}" data-dir-link="">All</a>
          ${relations.map(k => `<a role="menuitemradio" aria-checked="${k === want.link}" href="${dirListHref(type, k)}" data-dir-link="${esc(k)}">${esc(dirRelation(k))}</a>`).join('')}
        </div></details></header>
    <nav class="tt-tabs dir-tabs" aria-label="Contacts">${tabs.map(t => {
      const n = counted.get(t.key);
      return `<a href="${dirListHref(t.key)}" data-dir-tab="${esc(t.key)}"${t.key === type ? ' class="cur" aria-current="page"' : ''}>${esc(t.plural_name || t.name)}${n && n.count ? ` <span class="dir-tab-n tnum">${esc(dirN(n.count, n.approximate))}</span>` : ''}</a>`;
    }).join('')}</nav>
    <div class="dir-tools" data-dir-tools${want.q ? '' : ' hidden'}>
      <input type="search" class="dir-q" data-dir-q placeholder="Name starts with" aria-label="Name starts with" value="${esc(want.q)}" autocomplete="off">
    </div>
    ${want.link ? `<p class="dir-active"><span class="pill">${esc(dirRelation(want.link))}</span> <a href="${dirListHref(type)}" data-dir-clear-link aria-label="Clear"><span class="nav-icon" aria-hidden="true">close</span></a></p>` : ''}
    <div data-dir-body>${skelRows(6)}</div>`;
  dirBindList(root, types);
  await dirLoadRows(root, types, true);
}
function dirTabOrder(key: string): number {
  const i = DIR_MAIN_TYPES.indexOf(key);
  return i < 0 ? DIR_MAIN_TYPES.length : i;
}
function dirBindList(root: HTMLElement, types: DirType[]): void {
  const box = root.querySelector<HTMLElement>('[data-dir-tools]');
  const input = root.querySelector<HTMLInputElement>('[data-dir-q]');
  root.querySelector('[data-dir-search-toggle]')?.addEventListener('click', ev => {
    if (!box || !input) return;
    box.hidden = !box.hidden;
    const button = ev.currentTarget instanceof HTMLElement ? ev.currentTarget : null;
    button?.setAttribute('aria-expanded', String(!box.hidden));
    if (!box.hidden) input.focus();
  });
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
  if (!DIR_MENU_WIRED) {
    DIR_MENU_WIRED = true;
    // A click outside the relationship menu closes it (one listener for every visit to the page).
    document.addEventListener('click', ev => {
      const menu = document.querySelector<HTMLDetailsElement>('#dir-root [data-dir-link-menu]');
      if (menu?.open && !menu.contains(eventElement(ev))) menu.open = false;
    });
  }
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
  const plural = dirTypeName(types, state.type, true).toLowerCase();
  if (!state.rows.length) {
    body.innerHTML = `<div class="empty" data-dir-empty>${state.q || state.link ? 'No matches.' : `No ${esc(plural)} yet.`}</div>`;
    return;
  }
  body.innerHTML = `<p class="muted dir-count" data-dir-count>${esc(dirN(page.count, page.approximate))} ${esc(plural)}</p>
    ${dirTable(state.type, state.rows)}
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
function dirTable(type: string, rows: readonly DirBrief[]): string {
  const person = type === 'person', org = type === 'organization';
  const head = ['Name', person ? 'Organization' : org ? 'Domain' : '', person || org ? 'Relationship' : '', 'Owner', 'Last contact', 'From'];
  return `<table class="tt-list dir-list" data-dir-type="${esc(type)}"><thead><tr>${head.filter(Boolean).map(h => `<th>${h}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(r => {
      const rel = person ? (r.links_to_us.length ? r.links_to_us : r.organization?.links_to_us ?? []) : r.links_to_us;
      return `<tr data-dir-row="${esc(r.id)}">
        <td class="dir-name"><a href="${dirHref(r.id)}"><span class="dir-av" aria-hidden="true">${esc(dirInitials(r.name))}</span><strong>${esc(r.name)}</strong></a></td>
        ${person ? `<td class="dir-org" data-label="Organization">${r.organization ? `<a href="${dirHref(r.organization.id)}">${esc(r.organization.name)}</a>` : ''}</td>` : ''}
        ${org ? `<td class="dir-org muted" data-label="Domain">${esc(r.primary_domain ?? '')}</td>` : ''}
        ${person || org ? `<td class="dir-relcell" data-label="Relationship">${dirRelationChip(rel)}</td>` : ''}
        <td class="dir-owner muted" data-label="Owner">${esc(dirWho(r.owner))}</td>
        <td class="dir-last muted" data-label="Last contact">${r.last_activity_at ? agoSpan(r.last_activity_at) : ''}</td>
        <td class="dir-from muted" data-label="From">${esc((r.sources ?? []).map(dirSource).join(' · '))}</td></tr>`;
    }).join('')}</tbody></table>`;
}

// Leaving the page drops its state, so a late answer draws nothing.
function directoryStop(r: string): void {
  if (!rdUnder((r || '').split('?')[0] ?? '', DIR_ROUTE)) { DIRP = null; DIRL = null; }
}
