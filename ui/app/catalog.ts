/* ui/app/catalog.ts — Bot template cards (first run and Add from template)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- the bot templates, as cards
// One card component, shared by the first-run wizard (#/welcome) and by Settings → Bots →
// "Add from template". A card is a whole bot to read before choosing it: what it owns, what it
// will never do, what it runs on, and the AGENTS.md it would be created with - editable here,
// because the words a bot is created with are the only thing that makes it this team's bot.

// A template card, as the template list gives it (the fields this file reads).
interface CatalogCard {
  slug: string;
  name?: string;
  template?: string;
  runtime?: string;
  model?: string;
  reasoning_effort?: string;
  summary?: string;
  when?: string;
  instructions?: string;
  required?: boolean;
  default?: boolean;
  owns?: string[];
  never?: string[];
  /** The other fields a card carries (the org builder and the first run read theirs). */
  [key: string]: unknown;
}
// A human who can be a template's mailbox (catalogPeople): the fields this file reads of a person.
interface CatalogPerson { id: string; name?: string | undefined; email?: string | undefined; hidden?: boolean | undefined }
// What a person has changed on one card: its name, its instructions and its mailbox.
interface CatalogEdit { display_name?: string; instructions?: string; person?: string; reports_to?: string; [key: string]: unknown }
// The cards on offer and what is chosen on them (catalogState); a page may read its cards as a narrower card type.
interface CatalogState<C extends CatalogCard = CatalogCard> {
  cards: C[];
  picked: Set<string | undefined>;
  edits: Record<string, CatalogEdit | undefined>;
  touched: Set<string>;
  decided: Set<string>;
  lock: boolean;
}
function catalogState(cards: unknown, options: {lock?: boolean} = {}): CatalogState {
  return {cards: Array.isArray(cards) ? cards : [], picked: new Set(), edits: {}, touched: new Set(),
          decided: new Set(), lock: options.lock !== false};
}
const catalogCards = (data: CatalogCard[] | {cards?: CatalogCard[]} | null | undefined): CatalogCard[] => Array.isArray(data) ? data : (data?.cards || []);
const catalogCard = (state: CatalogState, slug: string | undefined): CatalogCard | undefined => state.cards.find(card => card.slug === slug);
const catalogName = (state: CatalogState, card: CatalogCard): string => state.edits[card.slug]?.display_name ?? card.name ?? card.slug;
const catalogText = (state: CatalogState, card: CatalogCard): string => state.edits[card.slug]?.instructions ?? card.instructions ?? '';
const catalogLocked = (state: CatalogState, card: CatalogCard): boolean => state.lock && !!card.required;
function catalogPeople(): CatalogPerson[] {
  const rows = (S.route === SETTINGS ? SETTINGS_DATA.people : S.people || []).filter((person: CatalogPerson) => person.email && !person.hidden);
  if (rows.length) return rows;
  return S.me?.email ? [{id: S.me.id, name: S.me.name, email: S.me.email}] : [];
}
function catalogPerson(state: CatalogState, card: CatalogCard): CatalogPerson | null {
  const id = state.edits[card.slug]?.person;
  return catalogPeople().find(person => person.id === id) || null;
}
function catalogInstructions(state: CatalogState, card: CatalogCard): string {
  let text = catalogText(state, card);
  if (card.template !== 'inbox-manager') return text;
  const person = catalogPerson(state, card);
  if (!person?.email) return text;
  const line = `Mailbox: ${person.email}`;
  if (/^Mailbox:\s+\S+/m.test(text)) return text.replace(/^Mailbox:\s+\S+/m, line);
  return `${text.replace(/\s*$/, '')}\n\n${line}\n`;
}
function catalogMissingMailbox(state: CatalogState): boolean {
  return state.cards.some(card => card.template === 'inbox-manager' && state.picked.has(card.slug)
    && !catalogPerson(state, card));
}
function catalogSelection(state: CatalogState): Record<string, {template: string | undefined; display_name: string; instructions: string}> {
  const out: Record<string, {template: string | undefined; display_name: string; instructions: string}> = {};
  for (const card of state.cards) {
    if (!state.picked.has(card.slug)) continue;
    out[card.slug] = {template: card.template, display_name: catalogName(state, card),
                      instructions: catalogInstructions(state, card)};
  }
  return out;
}
function catalogCardHTML(state: CatalogState, card: CatalogCard, note?: string): string {
  const slug = card.slug, picked = state.picked.has(slug), locked = catalogLocked(state, card);
  const meta = [card.runtime, card.model, card.reasoning_effort].filter(Boolean).join(' · ');
  const people = card.template === 'inbox-manager' ? catalogPeople() : [];
  const chosen = state.edits[slug]?.person || '';
  const mailbox = people.length ? `<label class="cat-mailbox">Whose mailbox
      <select data-cat-mailbox="${esc(slug)}" aria-label="Human whose inbox this bot reads">
        <option value="">Choose a human…</option>
        ${people.map(person => `<option value="${esc(person.id)}" ${person.id === chosen ? 'selected' : ''}>${esc(person.name || person.id)}${person.email ? ` · ${esc(person.email)}` : ''}</option>`).join('')}
      </select></label>` : '';
  return `<article class="cat-card${picked ? ' on' : ''}" data-cat-card="${esc(slug)}">
    <div class="cat-pick"><input type="checkbox" data-cat-toggle="${esc(slug)}" ${picked ? 'checked' : ''} ${locked ? 'disabled' : ''}
        aria-label="Include ${esc(card.name || slug)}">
      <input type="text" class="cat-name" data-cat-name="${esc(slug)}" value="${esc(catalogName(state, card))}" maxlength="100"
        aria-label="Name for ${esc(card.name || slug)}">
      ${card.required ? '<span class="pill ok">required</span>' : ''}${botBetaPill(card)}</div>
    ${card.summary ? `<p class="cat-summary">${esc(card.summary)}</p>` : ''}
    ${card.when ? `<p class="cat-when" data-cat-when="${esc(slug)}">${esc(card.when)}</p>` : ''}
    ${note ? `<p class="cat-reason" data-cat-reason="${esc(slug)}">Recommended because ${esc(note)}.</p>` : ''}
    ${card.owns?.length ? `<p class="cat-list"><span class="k">Owns</span> ${esc(card.owns.join(', '))}</p>` : ''}
    ${card.never?.length ? `<p class="cat-list"><span class="k">Never</span> ${esc(card.never.join(', '))}</p>` : ''}
    ${mailbox}
    ${meta ? `<p class="cat-meta mono">${esc(meta)}</p>` : ''}
    <details class="cat-instructions"><summary>Instructions</summary>
      <textarea data-cat-instructions="${esc(slug)}" rows="10" aria-label="Instructions for ${esc(card.name || slug)}">${esc(catalogText(state, card))}</textarea></details>
  </article>`;
}
// Required first (BotOps), then what is ticked by default (the assistant), then whatever the
// answers recommend, then the rest of the menu.
function catalogGridHTML(state: CatalogState, note?: ((card: CatalogCard) => string) | null): string {
  const rank = (card: CatalogCard) => card.required ? 0 : card.default ? 1 : note && note(card) ? 2 : 3;
  const rows = state.cards.slice().sort((a: CatalogCard, b: CatalogCard) =>
    rank(a) - rank(b) || String(a.name || a.slug).localeCompare(String(b.name || b.slug)));
  if (!rows.length) return '<div class="empty">No templates yet.</div>';
  return rows.map(card => catalogCardHTML(state, card, note ? note(card) : '')).join('');
}
function catalogWire(host: HTMLElement | null, state: CatalogState, changed?: () => void): void {
  if (!host) return;
  host.onchange = event => {
    const mailbox = eventElement(event)?.closest<HTMLSelectElement>('[data-cat-mailbox]');
    if (mailbox) {
      (state.edits[String(mailbox.dataset.catMailbox)] ||= {}).person = mailbox.value;
      changed?.();
      return;
    }
    const toggle = eventElement(event)?.closest<HTMLInputElement>('[data-cat-toggle]');
    if (!toggle) return;
    const slug = String(toggle.dataset.catToggle);
    if (toggle.checked) state.picked.add(slug); else state.picked.delete(slug);
    state.decided.add(slug);                 // a human's own choice outranks any later advice
    host.querySelector(`[data-cat-card="${cssSelectorValue(slug)}"]`)?.classList.toggle('on', toggle.checked);
    changed?.();
  };
  host.oninput = event => {
    const target = eventElement(event);
    const name = target?.closest<HTMLInputElement>('[data-cat-name]'), text = target?.closest<HTMLTextAreaElement>('[data-cat-instructions]');
    if (name) { (state.edits[String(name.dataset.catName)] ||= {}).display_name = name.value; state.touched.add(String(name.dataset.catName)); }
    if (text) (state.edits[String(text.dataset.catInstructions)] ||= {}).instructions = text.value;
    if (name || text) changed?.();
  };
}
const cssSelectorValue = (value: string): string => window.CSS?.escape !== undefined ? CSS.escape(value) : String(value);
