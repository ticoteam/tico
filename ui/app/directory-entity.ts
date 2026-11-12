/* ui/app/directory-entity.ts — one contact (#/library/contacts/<id>): a person, an organization, a place or
   any other type. Header, the rules that apply, values with their source and age (each age opens the field's history),
   links, and notes. A place adds what it is inside, the bodies that govern or regulate it, and whether it comes from the
   shared places tree. Reads: GET /api/v2/entities/card, /rules, /history, /types/<key> and /self; the one write is
   POST /api/v2/entities/notes. Every read goes through the read rule on the server (backend/entities/visible.py).
   No "mentioned in" list: the card does not check each text's readers, so no quote is drawn here. */
'use strict';

interface DirValue { value: unknown; source_type: string; source_id: string; confidence: number | null; updated_at: string }
interface DirLink {
  id: string; link_key: string; link_name: string; inverse_name: string | null;
  from_entity_id: string; to_entity_id: string; valid_from_on: string | null; valid_until_on: string | null;
  other_id: string; other_name: string; other_type: string;
  file?: DirFile | null;   // a document's file, when it has one the reader may open (documents are files, not contacts)
}
interface DirFile { id: string; title: string; page: boolean }
interface DirRef { system: string; external_type: string; external_id: string; url: string | null; synced_at: string | null }
interface DirNote { id: string; author_key: string; body: string; audience: string; created_at: string }
interface DirPlace { kind: string; governing_body: string; shared: boolean }
interface DirCard {
  id: string; type: string; name: string; status: string; self: boolean;
  primary_email: string | null; primary_domain: string | null; summary: string | null;
  links_to_us: string[]; organization?: DirOrgRef | null; owner?: string | null; sources?: string[];
  values: Record<string, DirValue | DirValue[]>;
  links: DirLink[]; refs: DirRef[]; notes: DirNote[]; place?: DirPlace;
}
interface DirRule {
  id?: string; text: string; cite?: string | null; source?: string | null; effect?: string | null;
  path?: string[] | null; reason?: string | null;
}
interface DirAttribute { key: string; name: string; value_type: string; multiple: number | boolean; sensitive: number | boolean; owner: string }
interface DirDescribe { type: DirType; attributes: DirAttribute[] }
interface DirSelf { id: string; name: string; operates_in: { id: string; name: string; link_id?: string }[] }
interface DirHistoryRow { id: string; attribute_key: string; value: unknown; source_type: string; source_id: string; confidence: number | null; active_from: string | null; active_until: string | null }

const DIR_WORKS_AT = ['works_at', 'member_of'];
const DIR_EFFECTS: Record<string, [string, string]> = {
  block: ['Block', 'fail'], escalate: ['Ask', 'waiting'], warn: ['Warn', 'waiting'], ok: ['Ok', 'ok'], skip: ['Skip', ''],
};

const dirOut = (l: DirLink, me: string): boolean => l.from_entity_id === me;
const dirLinkLabel = (l: DirLink, me: string): string => (dirOut(l, me) ? l.link_name : l.inverse_name || l.link_name);
const dirCap = (s: string): string => s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
function dirValueText(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'string' || typeof v === 'number') return String(v);
  return JSON.stringify(v);
}
// Where a value came from: the Contacts tools record the writer (source "hub", source id the actor).
const dirValueSource = (v: { source_type: string; source_id: string }): string =>
  v.source_type === 'hub' ? dirWho(v.source_id) : v.source_type ? dirSource(v.source_type) : '';
const dirShortAgo = (iso: string | null): string => iso ? ago(iso).replace(/ ago$/, '') : '';
const dirValues = (v: DirValue | DirValue[] | undefined): DirValue[] => v === undefined ? [] : Array.isArray(v) ? v : [v];

async function pageDirectoryEntity(id: string): Promise<void> {
  const run = (DIRP?.run ?? 0) + 1;
  DIRP = { run };
  DIRL = null;
  const main = document.getElementById('main');
  if (!main) return;
  main.innerHTML = `<div class="tt dir dir-entity" id="dir-root" data-dir-page="entity">${skelRows(6, { card: true })}</div>`;
  const ref = encodeURIComponent(id);
  let card: DirCard, types: DirType[];
  try {
    [card, types] = await Promise.all([
      get<DirCard>(`/v2/entities/card?ref=${ref}`, { fresh: true }),
      get<{ types: DirType[] }>('/v2/entities/types', { fresh: true }).then(r => r.types),
    ]);
  } catch (e) {
    if (dirLive(run)) main.innerHTML = `<div class="tt dir"><p><a href="${DIR_ROUTE}">Contacts</a></p>${dirErr(e)}</div>`;
    return;
  }
  // Rules, the type's field names and our own row are drawn when they come; the page does not wait on them.
  const rules = get<{ rules: DirRule[] }>(`/v2/entities/rules?ref=${encodeURIComponent(card.id)}`, { fresh: true }).then(r => r.rules);
  const describe = get<DirDescribe>(`/v2/entities/types/${encodeURIComponent(card.type)}`, { fresh: true }).catch(() => null);
  const self = card.type === 'place' ? get<DirSelf>('/v2/entities/self', { fresh: true }).catch(() => null) : Promise.resolve(null);
  const [attributes, us] = await Promise.all([describe, self]);
  if (!dirLive(run)) return;
  const root = document.getElementById('dir-root');
  if (!root) return;
  root.dataset.dirType = card.type;
  root.innerHTML = card.type === 'place' ? dirPlaceHTML(card, types, us) : dirEntityHTML(card, types, attributes);
  dirBindEntity(root, card, attributes);
  try {
    const found = await rules;
    if (dirLive(run)) dirDrawRules(root, found);
  } catch (e) {
    if (dirLive(run)) { const box = root.querySelector('[data-dir-rules]'); if (box) box.innerHTML = dirErr(e); }
  }
}

// ----------------------------------------------------------------- header
function dirCrumb(card: DirCard, types: readonly DirType[], parent: DirLink | undefined): string {
  // A type the list does not show (a document) has no list to go back to.
  const listed = types.some(t => t.key === card.type);
  return `<nav class="dir-crumb" aria-label="Contacts"><a href="${DIR_ROUTE}">Contacts</a>
    ${listed ? `<span aria-hidden="true">›</span> <a href="${dirListHref(card.type)}" data-dir-crumb-type>${esc(dirTypeName(types, card.type, true))}</a>` : ''}
    ${parent ? `<span aria-hidden="true">›</span> <a href="${dirHref(parent.other_id)}">${esc(parent.other_name)}</a>` : ''}</nav>`;
}
function dirHero(card: DirCard, sub: string, chips: string): string {
  return `<header class="dir-hero"><span class="dir-av dir-av-lg" aria-hidden="true">${esc(dirInitials(card.name))}</span>
    <div class="dir-hero-text"><h1 data-dir-name>${esc(card.name)}</h1>${sub ? `<p class="muted dir-sub" data-dir-sub>${sub}</p>` : ''}</div>
    ${chips ? `<div class="dir-chips">${chips}</div>` : ''}</header>`;
}
// A document is a file, not a contact: it opens its file (a page in Tico, any other file as itself), and without one
// it is its name alone.
const dirFileHref = (f: DirFile): string => f.page ? pageHref(f.id) : `${API}/v2/files/${encodeURIComponent(f.id)}`;
const dirLinkTo = (l: DirLink): string => l.other_type !== 'document'
  ? `<a href="${dirHref(l.other_id)}">${esc(l.other_name)}</a>`
  : l.file ? `<a href="${esc(dirFileHref(l.file))}" data-dir-file="${esc(l.file.id)}"${l.file.page ? '' : ' target="_blank" rel="noopener"'}>${esc(l.other_name)}</a>`
    : `<span data-dir-document>${esc(l.other_name)}</span>`;

// ----------------------------------------------------------------- person, organization and the rest
function dirEntityHTML(card: DirCard, types: readonly DirType[], describe: DirDescribe | null): string {
  const me = card.id;
  const out = card.links.filter(l => dirOut(l, me));
  const where = out.find(l => l.link_key === 'located_in');
  const subParts: string[] = [];
  if (card.type === 'person') {
    const title = dirValues(card.values['title'])[0];
    const org = out.find(l => DIR_WORKS_AT.includes(l.link_key));
    const role = [title ? esc(dirValueText(title.value)) : '', org ? dirLinkTo(org) : ''].filter(Boolean).join(' at ');
    if (role) subParts.push(role);
  } else {
    if (card.primary_domain) subParts.push(esc(card.primary_domain));
    const kind = dirValues(card.values['kind'])[0];
    if (kind) subParts.push(esc(dirCap(dirValueText(kind.value))));
  }
  if (where) subParts.push(dirLinkTo(where));
  const rel = card.links_to_us.length ? card.links_to_us : card.organization?.links_to_us ?? [];
  const chips = dirRelationChip(rel) + (card.self ? ' <span class="pill ok">Us</span>' : '');
  return `${dirCrumb(card, types, undefined)}${dirHero(card, subParts.join(' · '), chips)}
    <div class="dir-cols">
      <div class="dir-main">
        ${dirRulesCard('Rules')}
        ${dirDetailsCard(card, describe)}
        ${dirLinksCard(card)}
        ${dirNotesCard(card)}
      </div>
    </div>`;
}
function dirDetailsCard(card: DirCard, describe: DirDescribe | null): string {
  const rows: string[] = [];
  if (card.primary_email) rows.push(`<tr><th scope="row">Email</th><td colspan="2"><a href="mailto:${esc(card.primary_email)}">${esc(card.primary_email)}</a></td></tr>`);
  if (card.primary_domain && card.type !== 'organization') rows.push(`<tr><th scope="row">Domain</th><td colspan="2">${esc(card.primary_domain)}</td></tr>`);
  const named = new Map((describe?.attributes ?? []).map(a => [a.key, a.name]));
  const order = [...(describe?.attributes ?? []).map(a => a.key), ...Object.keys(card.values)];
  const seen = new Set<string>();
  for (const key of order) {
    if (seen.has(key) || card.values[key] === undefined) continue;
    seen.add(key);
    const values = dirValues(card.values[key]);
    rows.push(`<tr data-dir-field="${esc(key)}"><th scope="row">${esc(named.get(key) ?? dirCap(key.replace(/_/g, ' ')))}</th>
      <td>${values.map(v => `<span class="dir-val">${esc(dirValueText(v.value))}</span>`).join(', ')}</td>
      <td class="dir-meta">${values.slice(0, 1).map(v => {
        const from = dirValueSource(v);
        return `${from ? `<span class="pill dir-src" data-dir-src>${esc(from)}</span>` : ''}<button type="button" class="dir-age" data-dir-history="${esc(key)}" aria-label="History of ${esc(named.get(key) ?? key)}" title="History">${esc(dirShortAgo(v.updated_at))}</button>`;
      }).join('')}</td></tr>`);
  }
  if (!rows.length) return '';
  return `<section class="card dir-card" data-dir-details><header><h2>Details</h2></header><table class="dir-kv">${rows.join('')}</table></section>`;
}
function dirLinksCard(card: DirCard): string {
  const me = card.id;
  const groups = new Map<string, DirLink[]>();
  for (const l of card.links) {
    // People who work here read as one group, "People".
    const label = !dirOut(l, me) && l.other_type === 'person' && DIR_WORKS_AT.includes(l.link_key) ? 'People' : dirCap(dirLinkLabel(l, me));
    groups.set(label, [...(groups.get(label) ?? []), l]);
  }
  const refs = card.refs.map(r => r.url
    ? `<a class="pill" href="${esc(r.url)}" target="_blank" rel="noopener">${esc(dirCap(r.system))}</a>`
    : `<span class="pill">${esc(dirCap(r.system))}</span>`).join(' ');
  if (!groups.size && !refs) return '';
  return `<section class="card dir-card" data-dir-links><header><h2>Links</h2></header><table class="dir-kv">
    ${[...groups].map(([label, links]) => `<tr data-dir-link-group="${esc(label)}"><th scope="row">${esc(label)}</th><td>${links.map(dirLinkTo).join(', ')}</td></tr>`).join('')}
    ${refs ? `<tr><th scope="row">Linked</th><td>${refs}</td></tr>` : ''}</table></section>`;
}

// ----------------------------------------------------------------- rules
const dirRulesCard = (title: string): string =>
  `<section class="card dir-card" data-dir-rules-card><header><h2>${esc(title)}</h2></header><div data-dir-rules>${skelRows(2, { dot: false })}</div></section>`;
function dirDrawRules(root: HTMLElement, rules: readonly DirRule[]): void {
  const box = root.querySelector('[data-dir-rules]');
  if (!box) return;
  if (!rules.length) { box.innerHTML = '<p class="muted dir-none" data-dir-rules-empty>None apply.</p>'; return; }
  box.innerHTML = `<ul class="dir-rules">${rules.map(r => {
    const [label, tone] = DIR_EFFECTS[r.effect ?? ''] ?? ['Rule', ''];
    const meta = [(r.path ?? []).join(' → '), r.cite ?? '', r.reason ?? ''].filter(Boolean).map(esc).join(' · ');
    return `<li data-dir-rule><span class="pill ${tone}">${esc(label)}</span><div><div>${esc(r.text)}</div>${meta ? `<div class="muted dir-rule-meta">${meta}</div>` : ''}</div></li>`;
  }).join('')}</ul>`;
}

// ----------------------------------------------------------------- notes
function dirNotesCard(card: DirCard): string {
  return `<section class="card dir-card" data-dir-notes><header><h2>Notes</h2></header>
    <ul class="dir-notes" data-dir-note-list>${card.notes.map(dirNoteHTML).join('')}</ul>
    <form class="dir-note-form" data-dir-note-form>
      <textarea name="body" rows="2" required maxlength="4000" aria-label="Note" placeholder="Add a note"></textarea>
      <div class="dir-note-row"><div class="theme-seg" role="radiogroup" aria-label="Who sees it">
        <label><input type="radio" name="audience" value="company" checked> Company</label>
        <label><input type="radio" name="audience" value="me"> Only me</label></div>
        <span class="spacer"></span><button type="submit" class="primary small">Add</button></div>
    </form></section>`;
}
const dirNoteHTML = (n: DirNote): string => `<li data-dir-note="${esc(n.id)}"><p class="dir-note-body">${esc(n.body)}</p>
  <p class="muted dir-note-meta">${esc(dirWho(n.author_key))} · ${agoSpan(n.created_at)}${n.audience === 'me' ? ' · <span class="pill">Only me</span>' : ''}</p></li>`;

function dirBindEntity(root: HTMLElement, card: DirCard, describe: DirDescribe | null): void {
  root.addEventListener('click', ev => {
    const button = eventElement(ev)?.closest<HTMLElement>('[data-dir-history]');
    const key = button?.dataset['dirHistory'];
    if (!key) return;
    const name = describe?.attributes.find(a => a.key === key)?.name ?? dirCap(key);
    void dirHistoryDrawer(card, key, name);
  });
  const form = root.querySelector<HTMLFormElement>('[data-dir-note-form]');
  form?.addEventListener('submit', async ev => {
    ev.preventDefault();
    const data = new FormData(form);
    const body = String(data.get('body') ?? '').trim();
    const audience = data.get('audience') === 'me' ? 'me' : 'company';
    if (!body) return;
    const button = form.querySelector<HTMLButtonElement>('button[type=submit]');
    if (button) button.disabled = true;
    try {
      const made: { id: string } = await post('/v2/entities/notes', { ref: card.id, body, audience });
      const note: DirNote = { id: made.id, author_key: S.me ? `human:${S.me.id}` : '', body, audience, created_at: new Date().toISOString() };
      root.querySelector('[data-dir-note-list]')?.insertAdjacentHTML('afterbegin', dirNoteHTML(note));
      form.reset();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), true);
    } finally {
      if (button) button.disabled = false;
    }
  });
}

// ----------------------------------------------------------------- a field's history
async function dirHistoryDrawer(card: DirCard, key: string, name: string): Promise<void> {
  const dialog = document.createElement('dialog');
  dialog.className = 'dir-drawer';
  dialog.setAttribute('aria-label', name);
  dialog.innerHTML = `<header class="dir-drawer-head"><h2>${esc(name)}</h2><span class="spacer"></span>
    <button type="button" class="ghost small dir-icon" data-dir-close aria-label="Close"><span class="nav-icon" aria-hidden="true">close</span></button></header>
    <div data-dir-history-body>${skelRows(3, { dot: false })}</div>`;
  dialog.addEventListener('close', () => dialog.remove());
  dialog.addEventListener('click', ev => {
    // A click on the backdrop (the dialog itself, outside its content) closes it.
    if (ev.target === dialog || eventElement(ev)?.closest('[data-dir-close]')) dialog.close();
  });
  document.body.appendChild(dialog);
  dialog.showModal();
  const body = dialog.querySelector('[data-dir-history-body]');
  if (!body) return;
  const current = dirValues(card.values[key]).map(v => ({ value: v.value, source_type: v.source_type, source_id: v.source_id, confidence: v.confidence, from: v.updated_at, until: null as string | null }));
  try {
    const page = await get<{ rows: DirHistoryRow[] }>(`/v2/entities/history?ref=${encodeURIComponent(card.id)}&attribute=${encodeURIComponent(key)}`, { fresh: true });
    const past = page.rows.map(h => ({ value: h.value, source_type: h.source_type, source_id: h.source_id, confidence: h.confidence, from: h.active_from, until: h.active_until }));
    body.innerHTML = `<ol class="dir-history">${[...current, ...past].map((h, i) => {
      const from = dirValueSource(h);
      const conf = h.confidence !== null && h.confidence < 1 ? `${Math.round(h.confidence * 100)}%` : '';
      const when = h.until ? `${h.from ? dirDay(h.from) + ' – ' : ''}${dirDay(h.until)}` : h.from ? dirDay(h.from) : '';
      return `<li data-dir-history-row${i < current.length ? ' class="cur"' : ''}><strong>${esc(dirValueText(h.value))}</strong>
        <span class="muted">${[from, conf, when].filter(Boolean).map(esc).join(' · ')}${i < current.length ? ' · Current' : ''}</span></li>`;
    }).join('')}</ol>`;
  } catch (e) {
    body.innerHTML = dirErr(e);
  }
}
const dirDay = (iso: string): string => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
};

// ----------------------------------------------------------------- a place
function dirPlaceHTML(card: DirCard, types: readonly DirType[], us: DirSelf | null): string {
  const me = card.id;
  const parent = card.links.find(l => dirOut(l, me) && l.link_key === 'located_in' && l.other_type === 'place');
  const bodies = card.links.filter(l => !dirOut(l, me) && (l.link_key === 'governs' || l.link_key === 'regulates'));
  const inside = card.links.filter(l => !dirOut(l, me) && l.link_key === 'located_in');
  const places = inside.filter(l => l.other_type === 'place'), here = inside.filter(l => l.other_type !== 'place');
  const place = card.place ?? { kind: '', governing_body: '', shared: false };
  const operate = us?.operates_in.some(p => p.id === me) ?? false;
  const governor = place.governing_body || bodies.find(l => l.link_key === 'governs')?.other_name || '';
  const sub = [place.kind ? esc(dirCap(place.kind.replace(/_/g, ' '))) : '', governor ? `governed by ${esc(governor)}` : ''].filter(Boolean).join(' · ');
  const chips = (operate ? '<span class="pill ok" data-dir-operate>We operate here</span> ' : '')
    + (place.shared ? '<span class="pill" data-dir-shared>Shared</span>' : '');
  const list = (rows: readonly DirLink[], label: (l: DirLink) => string): string =>
    `<ul class="dir-plain">${rows.map(l => `<li>${dirLinkTo(l)}${label(l) ? ` <span class="muted">${esc(label(l))}</span>` : ''}</li>`).join('')}</ul>`;
  return `${dirCrumb(card, types, parent)}${dirHero(card, sub, chips)}
    <div class="dir-cols">
      <div class="dir-main">
        ${dirRulesCard('Rules that bind us here')}
        ${bodies.length ? `<section class="card dir-card" data-dir-bodies><header><h2>Bodies</h2></header>${list(bodies, l => dirLinkLabel(l, me) === l.link_name ? l.link_key : dirLinkLabel(l, me))}</section>` : ''}
        ${places.length ? `<section class="card dir-card" data-dir-inside><header><h2>Inside</h2></header>${list(places, () => '')}</section>` : ''}
        ${here.length ? `<section class="card dir-card" data-dir-here><header><h2>Located here</h2></header>${list(here, () => '')}</section>` : ''}
        ${operate && us ? `<section class="card dir-card" data-dir-why><header><h2>Why it applies</h2></header>
          <p><a href="${dirHref(us.id)}">${esc(us.name)}</a> <span class="muted">→ operates in →</span> ${esc(card.name)}</p></section>` : ''}
        ${dirNotesCard(card)}
      </div>
    </div>`;
}
