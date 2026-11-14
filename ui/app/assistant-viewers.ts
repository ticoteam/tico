/* ui/app/assistant-viewers.ts — "Bots that can view this chat": the bots you let read your Assistant chat (docs/assistant.md)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** One bot on your list, as GET /v2/assistant/viewers sends it (backend/assistant_share.py viewers). */
interface AssistantViewer { bot: string; slug: string; name: string; added_at: string; last_read_at: string | null }
/** GET /v2/assistant/viewers: the bots you chose, and whether they also read what it has learned about you. */
interface AssistantViewers { bots: AssistantViewer[]; include_learned: boolean }
/** A bot the search can offer, from GET /v2/bots. */
interface ViewerCandidate { slug: string; display_name?: string | null; state?: string }

// One quiet line on your own Assistant settings (#/assistant/learned) until you open it. Nobody is on it at first; you
// search for a bot and add it, and it can read your chat but never write in it. Remove takes it off, and its next read is
// refused. The bot list is fetched only when you search, and only the matches are shown, never every bot.
const VIEWERS_SHOWN = 6;
let VIEWERS: AssistantViewers | null = null, VIEWER_CANDIDATES: ViewerCandidate[] | null = null;

const viewerRead = (v: AssistantViewer) => v.last_read_at ? 'read ' + ago(v.last_read_at) : 'not read yet';

function viewersHTML(d: AssistantViewers, name: string): string {
  const rows = d.bots.map(v => `<li class="viewers-item" data-viewer="${esc(v.bot)}"><span class="viewers-name">${esc(v.name)}</span>
      <span class="muted" title="${esc(v.last_read_at ? fmt(v.last_read_at) : '')}">${esc(viewerRead(v))}</span>
      <button type="button" class="ghost" data-viewer-remove>Remove</button></li>`).join('');
  return `<div class="viewers-line"><span class="viewers-label">Bots that can view this chat</span>
      ${d.bots.length ? '' : '<span class="muted">None</span> ·'}
      <button type="button" class="ghost viewers-add" data-viewer-open aria-expanded="false">Add a bot</button></div>
    ${d.bots.length ? `<ul class="viewers-list">${rows}</ul>` : ''}
    <div class="viewers-search" hidden>
      <label class="sr-only" for="viewers-q">Search for a bot</label>
      <input id="viewers-q" class="viewers-q" type="search" autocomplete="off" placeholder="Search for a bot">
      <ul class="viewers-matches" role="listbox" aria-label="Bots"></ul>
      <p class="muted viewers-note">Can read this chat. Can't write in it.</p></div>
    ${d.bots.length ? `<label class="viewers-learned"><input type="checkbox" data-viewer-learned ${d.include_learned ? 'checked' : ''}>
      Include what ${esc(name)} has learned about me</label>` : ''}`;
}

async function viewersMount(el: HTMLElement, name: string) {
  try { VIEWERS = await get<AssistantViewers>('/v2/assistant/viewers', {fresh: true}); }
  catch (e) { el.innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`; return; }
  viewersDraw(el, name);
}

function viewersDraw(el: HTMLElement, name: string) {
  if (!VIEWERS || !el.isConnected) return;
  const d = VIEWERS;
  el.innerHTML = viewersHTML(d, name);
  el.onclick = ev => {
    const target = eventElement(ev);
    if (target?.closest('[data-viewer-open]')) void viewersOpen(el);
    const pick = target?.closest<HTMLElement>('[data-viewer-pick]');
    if (pick) void viewersChange(el, name, () => post('/v2/assistant/viewers', {bot: pick.dataset.viewerPick ?? ''}), 'Added');
    const row = target?.closest<HTMLElement>('[data-viewer]');
    if (row && target?.closest('[data-viewer-remove]')) {
      void viewersChange(el, name, () => writeRequest('DELETE', `/v2/assistant/viewers/${encodeURIComponent(row.dataset.viewer ?? '')}`), 'Removed');
    }
  };
  el.onchange = ev => {
    const box = eventElement(ev)?.closest<HTMLInputElement>('[data-viewer-learned]');
    if (box) void viewersChange(el, name, () => put('/v2/assistant/viewers/learned', {include_learned: box.checked}), 'Saved');
  };
}

async function viewersChange(el: HTMLElement, name: string, write: () => Promise<UntypedJson>, said: string) {
  try {
    VIEWERS = await write() as unknown as AssistantViewers;
    viewersDraw(el, name);
    toast(said);
  } catch (e) { toast(errorMessage(e, ''), true); viewersDraw(el, name); }
}

async function viewersOpen(el: HTMLElement) {
  const box = el.querySelector<HTMLElement>('.viewers-search'), q = el.querySelector<HTMLInputElement>('.viewers-q');
  const opener = el.querySelector<HTMLElement>('[data-viewer-open]');
  if (!box || !q) return;
  box.hidden = false;
  opener?.setAttribute('aria-expanded', 'true');
  q.focus();
  if (!VIEWER_CANDIDATES) {
    try { VIEWER_CANDIDATES = await get<ViewerCandidate[]>('/v2/bots'); }
    catch (e) { toast(errorMessage(e, ''), true); return; }
  }
  const draw = () => viewersMatches(el, q.value);
  q.oninput = draw;
  q.onkeydown = ev => { if (ev.key === 'Escape') { box.hidden = true; opener?.setAttribute('aria-expanded', 'false'); opener?.focus(); } };
  draw();
}

function viewersMatches(el: HTMLElement, query: string) {
  const list = el.querySelector<HTMLElement>('.viewers-matches');
  if (!list) return;
  const term = query.trim().toLowerCase(), have = new Set((VIEWERS?.bots || []).map(v => v.slug));
  const found = term ? (VIEWER_CANDIDATES || []).filter(b => b.state !== 'archived' && b.slug !== myChatBot() && !have.has(b.slug)
    && `${b.display_name || ''} ${b.slug}`.toLowerCase().includes(term)).slice(0, VIEWERS_SHOWN) : [];
  list.innerHTML = found.map(b => `<li><button type="button" class="ghost" role="option" data-viewer-pick="${esc(b.slug)}">${esc(b.display_name || b.slug)}</button></li>`).join('')
    || (term ? '<li class="muted">No bot by that name</li>' : '');
}
