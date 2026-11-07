/* ui/app/segment-tags.ts — Segments on the people's pages: a teammate's tags (the org chart's sheet and cards, the profile),
   the Segment filter a page keeps in its address (`?segment=<id>`), and the lookups the pages share. The segments are
   GET /api/v2/segments; the tags are GET /api/v2/segments/members, written with POST /api/v2/segments/members on the
   owner's own click (backend/segments.py `owner`), so nobody else is offered the menu. `all` stands alone.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   TypeScript: npm run build:ui compiles it to build/ui/app/segment-tags.js, which the page runs in this file's place. */
'use strict';

// One segment as GET /api/v2/segments lists it (backend/segments.py _segment_view).
interface SegFace { ref: string; name: string; kind: string; }
interface SegRow { id: string; name: string; who: string; color: string; rank: number; faces: SegFace[]; teammates: number; }
// One tagged teammate as GET /api/v2/segments/members lists it: `bot:<id>` or `person:<id>`, and the ids or `all`.
interface SegMember { member: string; segments: string[]; }
interface SegTags { list: SegRow[] | null; tags: Map<string, string[]>; loading: Promise<boolean> | null; listing: Promise<boolean> | null; error: string; }
// The one open menu or filter list, closed by a press outside it or Escape.
interface SegPop { el: HTMLElement; close: () => void; }
// A Segment filter's options: only these segment ids (a segment lead's own, from the P&L probe) or every segment with
// All segments; `unallocated` adds the Unallocated column (the whole P&L's viewers only).
interface SegFilterOpts { ids?: readonly string[]; unallocated?: boolean; }

// The eight colours a segment takes (backend/segments.py color_of). Each maps to an --au-* token (ui/styles/tokens.css).
const SEG_COLOURS = ['blue', 'green', 'amber', 'red', 'purple', 'teal', 'pink', 'gray'] as const;
const SEG_TICK = '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="M2.5 6.2l2.3 2.3 4.7-5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
let SEG_TAGS: SegTags = {list: null, tags: new Map(), loading: null, listing: null, error: ''};
let SEG_POP: SegPop | null = null;
// A route change takes an open menu or filter list away: the router keeps its own listeners, so this one hears the hash.
window.addEventListener('hashchange', () => segPopClose());

// The signed-in viewer as the state keeps it (ui/app/state.js). Only the owner tags, so only the owner is offered the menu.
function segTagMe(): {id?: string; role?: string} | null {
  return (S as unknown as {me: {id?: string; role?: string} | null}).me;
}
const segTagOwner = (): boolean => segTagMe()?.role === 'owner';
// A segment's colour name, one of the eight (anything else reads as gray).
const segColour = (colour: string): string => (SEG_COLOURS as readonly string[]).includes(colour) ? colour : 'gray';

// Reads the segments alone, for a page that names or lists segments and shows no one's tags (a Segment filter, a segment
// list). Resolves true when it answered; a failed read keeps what it had.
function segsListLoad(): Promise<boolean> {
  if (SEG_TAGS.listing) return SEG_TAGS.listing;
  const listing = get<{segments?: SegRow[]}>('/v2/segments', {fresh: true}).then(list => {
    SEG_TAGS.list = list.segments || [];
    SEG_TAGS.error = '';
    return true;
  }, (e: unknown) => { SEG_TAGS.error = (e as {message?: string} | null)?.message || ''; return false; }).finally(() => { SEG_TAGS.listing = null; });
  SEG_TAGS.listing = listing;
  return listing;
}

// Reads the segments and the teammates' tags at once. Resolves true when both answered; a failed read keeps what it had.
function segTagsLoad(): Promise<boolean> {
  if (SEG_TAGS.loading) return SEG_TAGS.loading;
  const load = Promise.all([
    get<{segments?: SegRow[]}>('/v2/segments', {fresh: true}),
    get<{members?: SegMember[]}>('/v2/segments/members', {fresh: true}),
  ]).then(([list, members]) => {
    SEG_TAGS.list = list.segments || [];
    SEG_TAGS.tags = new Map((members.members || []).map(row => [row.member, row.segments || []]));
    SEG_TAGS.error = '';
    return true;
  }, (e: unknown) => { SEG_TAGS.error = (e as {message?: string} | null)?.message || ''; return false; }).finally(() => { SEG_TAGS.loading = null; });
  SEG_TAGS.loading = load;
  return load;
}

// The segments the company plays in, in list order, as the last read had them.
const segTagRows = (): SegRow[] => SEG_TAGS.list || [];
// The server's text from the last failed list read ('' once it reads), so a page can show why it did not load.
const segTagsError = (): string => SEG_TAGS.error;
const segTagById = (id: string): SegRow | undefined => segTagRows().find(row => row.id === id);
// A segment's name; `all` reads as All segments, and an id the list no longer holds reads as the id.
// The P&L's column for lines that count toward no segment is Unallocated.
const segTagName = (id: string): string => id === 'all' ? 'All segments' : id === 'unallocated' ? 'Unallocated' : segTagById(id)?.name || id;
// True once the list is read and the company has no segments, with nothing picked (`current`): the pages then show no
// Segment control at all. Before the first read it is false, so a control shows and the page repaints once it is known.
const segNone = (current = ''): boolean => SEG_TAGS.list !== null && !SEG_TAGS.list.length && !current;
// A teammate's tags (`bot:<id>` or `person:<id>`), in list order; none when untagged.
const segTagsOf = (ref: string): string[] => SEG_TAGS.tags.get(ref) || [];

// A segment's dot, in its colour (a row, or just its colour name).
const segDot = (row: {color?: string}): string => `<span class="seg-dot c-${segColour(row.color || '')}" aria-hidden="true"></span>`;
// One segment in a line: its dot and name.
const segChip = (row: SegRow): string => `<span class="seg-chip">${segDot(row)}<span class="seg-chip-name">${esc(row.name)}</span></span>`;

// The tags as a page shows them in a line: a chip per segment, or All segments alone.
function segTagChips(segs: readonly string[]): string {
  if (!segs.length) return '';
  if (segs.includes('all')) return '<span class="seg-chip seg-all">All segments</span>';
  return segs.map(id => {
    const row = segTagById(id);
    return row ? segChip(row) : `<span class="seg-chip"><span class="seg-chip-name">${esc(segTagName(id))}</span></span>`;
  }).join('');
}
// The tags on a card: a dot per segment (its name in the tooltip), or a small All. Empty when untagged.
function segTagCard(segs: readonly string[]): string {
  if (!segs.length) return '';
  const all = segs.includes('all');
  const label = all ? 'All segments' : segs.map(segTagName).join(', ');
  const marks = all ? '<span class="seg-card-all">All</span>' : segs.map(id => segDot({color: segTagById(id)?.color || ''})).join('');
  return `<span class="seg-card" title="${esc(label)}" aria-label="${esc(label)}">${marks}</span>`;
}

// Replaces a teammate's segments. `[]` clears them; `all` goes alone. The saved list is what the server answered.
async function segTagsWrite(ref: string, segments: string[]): Promise<void> {
  const out = await post('/v2/segments/members', {member: ref, segments}) as SegMember | undefined;
  const saved = Array.isArray(out?.segments) ? out.segments : segments;
  if (saved.length) SEG_TAGS.tags.set(ref, saved);
  else SEG_TAGS.tags.delete(ref);
}

// One pick in the menu, applied to the tags a teammate has now. All stands alone; a segment drops All; a ticked
// segment unticks; `Clear` (the empty pick) gives none. The result keeps the list's order.
function segTagNext(current: readonly string[], pick: string): string[] {
  if (pick === '') return [];
  if (pick === 'all') return current.includes('all') ? [] : ['all'];
  const rest = current.filter(id => id !== 'all' && id !== pick);
  const next = current.includes(pick) ? rest : [...rest, pick];
  const rank = (id: string): number => segTagRows().findIndex(row => row.id === id);
  return next.sort((a, b) => rank(a) - rank(b));
}

// Closes the open menu or filter list, if any, and calls its close (which takes its listeners away).
function segPopClose(): void {
  const open = SEG_POP;
  if (!open) return;
  SEG_POP = null;
  open.el.remove();
  open.close();
}
// Puts a popover over `anchor`, inside the open dialog when there is one (a dialog is in the top layer, so a
// popover on the page would be under it), and wires the press-outside and Escape that close it.
function segPopMount(anchor: HTMLElement, el: HTMLElement): void {
  segPopClose();
  const host = anchor.closest<HTMLElement>('dialog[open]') || document.body;
  host.appendChild(el);
  const box = anchor.getBoundingClientRect(), height = el.offsetHeight;
  const below = box.bottom + 4 + height <= innerHeight;
  el.style.position = 'fixed';
  el.style.top = `${below ? box.bottom + 4 : Math.max(8, box.top - height - 4)}px`;
  el.style.left = `${Math.max(8, Math.min(box.left, innerWidth - el.offsetWidth - 8))}px`;
  // The anchor's own press (and anything inside it) is left to the anchor, which opens or closes its menu.
  const outside = (ev: Event): void => { if (!el.contains(ev.target as Node) && !anchor.contains(ev.target as Node)) segPopClose(); };
  const key = (ev: KeyboardEvent): void => {
    if (ev.key !== 'Escape') return;
    ev.preventDefault();
    ev.stopPropagation();
    segPopClose();
    anchor.focus({preventScroll: true});
  };
  document.addEventListener('pointerdown', outside, true);
  document.addEventListener('keydown', key, true);
  SEG_POP = {el, close: () => {
    document.removeEventListener('pointerdown', outside, true);
    document.removeEventListener('keydown', key, true);
  }};
}

// The Segments menu on a teammate: All, each segment with a tick when the teammate is in it, and Clear. Each pick
// saves at once and the menu stays open, so several can be ticked; `done` hears each saved change so the page can draw it.
function segTagMenuOpen(anchor: HTMLElement, ref: string, name: string, done: () => void): void {
  if (!segTagOwner()) return;
  const el = document.createElement('div');
  el.className = 'seg-menu';
  el.setAttribute('role', 'menu');
  el.setAttribute('aria-label', `Segments for ${name}`);
  let busy = false;
  const draw = (): void => {
    const current = segTagsOf(ref);
    const item = (pick: string, label: string, colour: string): string => {
      const on = pick === 'all' ? current.includes('all') : !current.includes('all') && current.includes(pick);
      return `<button type="button" role="menuitemcheckbox" aria-checked="${on}" class="seg-mi" data-pick="${esc(pick)}"${busy ? ' disabled' : ''}>
        <span class="seg-tick" aria-hidden="true">${on ? SEG_TICK : ''}</span>${colour ? segDot({color: colour}) : ''}<span>${esc(label)}</span></button>`;
    };
    el.innerHTML = `${item('all', 'All segments', '')}
      ${segTagRows().length ? '<hr class="seg-sep">' : ''}
      ${segTagRows().map(row => item(row.id, row.name, row.color)).join('')}
      <hr class="seg-sep"><button type="button" role="menuitem" class="seg-mi seg-clear" data-pick=""${busy ? ' disabled' : ''}><span class="seg-tick" aria-hidden="true"></span><span>Clear</span></button>`;
  };
  draw();
  el.addEventListener('click', ev => {
    const button = (ev.target as Element).closest<HTMLButtonElement>('[data-pick]');
    if (!button || busy) return;
    const pick = button.dataset.pick || '';
    busy = true;
    draw();
    segTagsWrite(ref, segTagNext(segTagsOf(ref), pick))
      .then(() => { busy = false; draw(); done(); })
      .catch((e: unknown) => {
        busy = false;
        draw();
        toast(e instanceof Error ? e.message : 'Could not save the segments', true, null, {host: anchor.closest<HTMLElement>('dialog[open]')});
      });
  });
  segPopMount(anchor, el);
  el.querySelector<HTMLElement>('[role="menuitemcheckbox"]')?.focus({preventScroll: true});
}

// The segment a page is filtered to: the `segment` of its address (`#/goals?segment=segment/x`), '' for all segments.
function segQuery(): string {
  return new URLSearchParams(String(location.hash).split('?')[1] || '').get('segment') || '';
}
// Sets (or, for '', removes) the page's segment in the address, without a history entry or a route change: the page
// draws the filter itself.
function segQuerySet(id: string): void {
  const [path, query = ''] = String(location.hash).split('?');
  const params = new URLSearchParams(query);
  if (id) params.set('segment', id);
  else params.delete('segment');
  const tail = params.toString();
  history.replaceState(history.state, '', `${location.pathname}${location.search}${path}${tail ? '?' + tail : ''}`);
  S.route = LAST_ROUTE = location.hash;
}

// The filter button every page with a Segment filter shows: the shared filter icon, with the segment's name beside it
// while one is picked (no word of its own). `data-seg-filter` marks it for segFilterOpen.
function segFilterButton(current: string): string {
  if (segNone(current)) return '';
  const name = current ? segTagName(current) : '';
  return `<button type="button" class="ghost seg-filter-btn${current ? ' active' : ''}" data-seg-filter aria-haspopup="menu" aria-label="${esc(name ? `Segment filter: ${name}` : 'Segment filter')}" title="${esc(name ? `Segment: ${name}` : 'Segment')}">${filterIcon(14)}${name ? `<span class="seg-filter-on">${esc(name)}</span>` : ''}</button>`;
}
// The Segment section of a page's filter menu: All segments, then each segment, one pick at a time. `pick` hears the
// segment's id, or '' for all segments; the page sets the address and redraws.
function segFilterOpen(anchor: HTMLElement, current: string, pick: (id: string) => void, opts: SegFilterOpts = {}): void {
  const el = document.createElement('div');
  el.className = 'seg-menu seg-filter';
  el.setAttribute('role', 'menu');
  el.setAttribute('aria-label', 'Segment');
  // A lead's list is their own segments only (no All segments, which would reach past them).
  const options: [string, string, string][] = opts.ids
    ? opts.ids.map((id): [string, string, string] => [id, segTagName(id), segTagById(id)?.color || ''])
    : [['', 'All segments', ''], ...segTagRows().map((row): [string, string, string] => [row.id, row.name, row.color])];
  if (opts.unallocated) options.push(['unallocated', 'Unallocated', '']);
  el.innerHTML = `<div class="seg-pop-h" aria-hidden="true">Segment</div>${options.map(([id, label, colour]) => `<button type="button" role="menuitemradio" aria-checked="${id === current}" class="seg-mi" data-value="${esc(id)}">
      <span class="seg-tick" aria-hidden="true">${id === current ? SEG_TICK : ''}</span>${colour ? segDot({color: colour}) : ''}<span>${esc(label)}</span></button>`).join('')}`;
  el.addEventListener('click', ev => {
    const button = (ev.target as Element).closest<HTMLElement>('[data-value]');
    if (!button) return;
    segPopClose();
    pick(button.dataset.value || '');
  });
  segPopMount(anchor, el);
  el.querySelector<HTMLElement>('[aria-checked="true"]')?.focus({preventScroll: true});
}

// A Segment select for a dialog: None first (an optional segment), then the segments in list order. Used beside
// Bet on a goal, in a task and on a budget. `cls` is the label's class (a form's field class), `disabled` a read-only one.
function segSelectHtml(current: string, {label = 'Segment', name = 'segment', cls = 'bd-field', disabled = false}: {label?: string; name?: string; cls?: string; disabled?: boolean} = {}): string {
  if (segNone(current)) return '';
  const rows = segTagRows();
  const known = !current || rows.some(row => row.id === current) ? '' : `<option value="${esc(current)}" selected>${esc(segTagName(current))}</option>`;
  return `<label class="${esc(cls)}"><span>${esc(label)}</span><select name="${esc(name)}"${disabled ? ' disabled' : ''}>
    <option value="">None</option>${known}${rows.map(row => `<option value="${esc(row.id)}"${row.id === current ? ' selected' : ''}>${esc(row.name)}</option>`).join('')}</select></label>`;
}

// A teammate's Segments row: its tags as chips, and the Segments button for the owner only. Nothing when the viewer is
// not the owner and the teammate has no tag (a non-owner sees the tags a teammate has, never the menu).
// Nothing when the company has no segments, or the teammate has none and the viewer may not tag. The Edit link is the
// owner's; its menu is segTagMenuOpen.
function segTagRowHtml(ref: string): string {
  const segs = segTagsOf(ref);
  if (!segs.length && (!segTagOwner() || segNone(''))) return '';
  return `<div class="seg-tags-row" data-seg-row><span class="seg-tags-chips" data-seg-chips>${segTagChips(segs) || '<span class="muted">None</span>'}</span>${
    segTagOwner() ? '<button type="button" class="linkish" data-seg-tags aria-haspopup="menu">Edit</button>' : ''}</div>`;
}
// Wires the Segments row drawn by segTagRowHtml under `root`: each saved pick redraws its chips, then `onSaved`
// (the page's own redraw, e.g. the org chart's cards).
function segTagRowWire(root: ParentNode, ref: string, name: string, onSaved?: () => void): void {
  const row = root.querySelector<HTMLElement>('[data-seg-row]'), button = root.querySelector<HTMLButtonElement>('[data-seg-tags]');
  if (!row || !button) return;
  button.onclick = () => segTagMenuOpen(button, ref, name, () => {
    const chips = row.querySelector<HTMLElement>('[data-seg-chips]');
    if (chips) chips.innerHTML = segTagChips(segTagsOf(ref)) || '<span class="muted">None</span>';
    onSaved?.();
  });
}
