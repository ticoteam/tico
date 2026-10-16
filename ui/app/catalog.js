/* ui/app/catalog.js — Bot template cards (first run and Add from template)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- the bot templates, as cards
// One card component, shared by the first-run wizard (#/welcome) and by Settings → Bots →
// "Add from template". A card is a whole bot to read before choosing it: what it owns, what it
// will never do, what it runs on, and the AGENT.md it would be created with - editable here,
// because the words a bot is created with are the only thing that makes it this team's bot.
function catalogState(cards, options = {}) {
  return {cards: Array.isArray(cards) ? cards : [], picked: new Set(), edits: {}, touched: new Set(),
          decided: new Set(), lock: options.lock !== false};
}
const catalogCards = data => Array.isArray(data) ? data : (data?.cards || []);
const catalogCard = (state, slug) => state.cards.find(card => card.slug === slug);
const catalogName = (state, card) => state.edits[card.slug]?.display_name ?? card.name ?? card.slug;
const catalogText = (state, card) => state.edits[card.slug]?.instructions ?? card.instructions ?? '';
const catalogLocked = (state, card) => state.lock && !!card.required;
function catalogPeople() {
  const rows = (S.route === SETTINGS ? SETTINGS_DATA.people : S.people || []).filter(person => person.email && !person.hidden);
  if (rows.length) return rows;
  return S.me?.email ? [{id: S.me.id, name: S.me.name, email: S.me.email}] : [];
}
function catalogPerson(state, card) {
  const id = state.edits[card.slug]?.person;
  return catalogPeople().find(person => person.id === id) || null;
}
function catalogInstructions(state, card) {
  let text = catalogText(state, card);
  if (card.template !== 'inbox') return text;
  const person = catalogPerson(state, card);
  if (!person?.email) return text;
  const line = `Mailbox: ${person.email}`;
  if (/^Mailbox:\s+\S+/m.test(text)) return text.replace(/^Mailbox:\s+\S+/m, line);
  return `${text.replace(/\s*$/, '')}\n\n${line}\n`;
}
function catalogMissingMailbox(state) {
  return state.cards.some(card => card.template === 'inbox' && state.picked.has(card.slug)
    && !catalogPerson(state, card));
}
function catalogSelection(state) {
  const out = {};
  for (const card of state.cards) {
    if (!state.picked.has(card.slug)) continue;
    out[card.slug] = {template: card.template, display_name: catalogName(state, card),
                      instructions: catalogInstructions(state, card)};
  }
  return out;
}
function catalogCardHTML(state, card, note) {
  const slug = card.slug, picked = state.picked.has(slug), locked = catalogLocked(state, card);
  const meta = [card.runtime, card.model, card.reasoning_effort].filter(Boolean).join(' · ');
  const people = card.template === 'inbox' ? catalogPeople() : [];
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
function catalogGridHTML(state, note) {
  const rank = card => card.required ? 0 : card.default ? 1 : note && note(card) ? 2 : 3;
  const rows = state.cards.slice().sort((a, b) =>
    rank(a) - rank(b) || String(a.name || a.slug).localeCompare(String(b.name || b.slug)));
  if (!rows.length) return '<div class="empty">No templates yet.</div>';
  return rows.map(card => catalogCardHTML(state, card, note ? note(card) : '')).join('');
}
function catalogWire(host, state, changed) {
  if (!host) return;
  host.onchange = event => {
    const mailbox = event.target.closest('[data-cat-mailbox]');
    if (mailbox) {
      (state.edits[mailbox.dataset.catMailbox] ||= {}).person = mailbox.value;
      changed?.();
      return;
    }
    const toggle = event.target.closest('[data-cat-toggle]');
    if (!toggle) return;
    const slug = toggle.dataset.catToggle;
    if (toggle.checked) state.picked.add(slug); else state.picked.delete(slug);
    state.decided.add(slug);                 // a human's own choice outranks any later advice
    host.querySelector(`[data-cat-card="${cssSelectorValue(slug)}"]`)?.classList.toggle('on', toggle.checked);
    changed?.();
  };
  host.oninput = event => {
    const name = event.target.closest('[data-cat-name]'), text = event.target.closest('[data-cat-instructions]');
    if (name) { (state.edits[name.dataset.catName] ||= {}).display_name = name.value; state.touched.add(name.dataset.catName); }
    if (text) (state.edits[text.dataset.catInstructions] ||= {}).instructions = text.value;
    if (name || text) changed?.();
  };
}
const cssSelectorValue = value => window.CSS?.escape ? CSS.escape(value) : String(value);
