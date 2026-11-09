/* First run, the team: who each bot reports to, the review, and the screen after Create (docs/onboarding.md).
   The org chart itself is built in ui/org-builder.ts; this turns what is checked there into `selected` (the order to set
   the bots up in, each with its reports-to), says what is wrong before Create, and shows the rest. Nothing is created
   until "Create my team". It shares the wizard's state (`ONB`) and the catalog cards' state (`catalogState`) with
   ui/app/welcome.js and ui/app/catalog.ts. */
'use strict';

// The wizard's state (ui/app/welcome.js: ONB) as this screen reads it. The catalog is ui/app/catalog.ts's catalogState.
interface FrProviders { preset?: string; enabled?: string[]; providers?: {id: string; label?: string}[] }
interface FrRecord { home?: string; bots?: FrBot[]; machine?: {runners?: unknown[]} | null; completed?: boolean }
// The wizard's state (ONB, ui/app/welcome.js): the one org-builder.ts reads too.
type FrState = OBState;
/** A bot row as the setup screen reads it: the record's bots and the roster rows the chart uses. */
interface FrBot extends Employee {
  repository_present?: boolean;
  onboarding_state?: string | null;
  onboarding_ready?: {summary?: string} | null;
  can_complete_onboarding?: boolean;
  setup_task_id?: string | null;
  revision?: number;
  state?: string;
}
interface FrOption { value: string; label: string; group: string }
interface FrSelected { template: string | undefined; display_name: string; instructions: string; reports_to?: string }
interface FrGroup { id: string; name?: string | undefined; bots: FrBot[] }

const frOwner = (): string => (S.me?.id ? 'human:' + S.me.id : '');
const frHome = (state: FrState): string => state.record.home || frOwner();
const frPeople = () => (SETTINGS_DATA.people?.length ? SETTINGS_DATA.people : (S.me?.id ? [{id: S.me.id, name: S.me.name}] : []));
const frPersonName = (id: string | undefined) => frPeople().find(p => p.id === id)?.name || id;
// The bot a card is created as: the assistant's card is named `coo` but the team's own bot is `assistantBot()`.
const frBotSlug = (card: CatalogCard): string => (card.template === 'assistant' ? assistantBot() : card.slug);

function frCollect(state: FrState, key: string) {
  if (state.catalog.cards.length) obCollect(state);
}

// ----------------------------------------------------------------- who reports to whom
// What a bot reports to: what the human chose, else its group head, else the owner.
const frRawReports = (state: FrState, slug: string): string | undefined => state.catalog.edits[slug]?.reports_to ?? obDefaultReports(state, slug);
function frReports(state: FrState, slug: string): string {
  const wanted = frRawReports(state, slug);
  // A parent that was removed leaves its team to the owner rather than a bot that will not exist.
  // An option's value is a string, so a wanted that matches one is a string.
  return frParentOptions(state, slug).some(option => option.value === wanted) ? wanted! : frHome(state);
}
function frDescends(state: FrState, slug: string, of: string): boolean {
  const seen = new Set<string>();
  for (let at: string | undefined = slug; at && !seen.has(at); at = frRawReports(state, at)) { if (at === of) return true; seen.add(at); }
  return false;
}
function frParentOptions(state: FrState, slug: string): FrOption[] {
  const cat = state.catalog, out: FrOption[] = [];
  const owner = frOwner();
  for (const person of frPeople()) out.push({value: 'human:' + person.id, label: (person.name || person.id) + (('human:' + person.id) === owner ? ' (you)' : ''), group: 'Humans'});
  // Built-ins and explicit catalog helpers are off the chart, so nobody reports to one.
  for (const card of cat.cards) {
    if (!card.required && card.kind !== 'helper' && cat.picked.has(card.slug) && card.slug !== slug && !frDescends(state, card.slug, slug))
      out.push({value: card.slug, label: catalogName(cat, card), group: 'Bots on your team'});
  }
  return out;
}
function frParentName(state: FrState, value: string): string | undefined {
  const cat = state.catalog;
  if (String(value).startsWith('human:')) return frPersonName(value.slice(6));
  const card = cat.cards.find(row => frBotSlug(row) === value || row.slug === value);
  return card ? catalogName(cat, card) : value;
}
// The team in the order it is set up: the built-ins, then each department's bots with its head first.
function frSelection(state: FrState): Record<string, FrSelected> {
  const cat = state.catalog, order: (string | undefined)[] = [];
  for (const id of state.org.chosen) for (const card of obPicked(state, id)) order.push(obCatalogCard(state, card.template)?.slug);
  const rank = (card: CatalogCard): number => { const i = order.indexOf(card.slug); return i < 0 ? 999 : i; };
  const cards = cat.cards.slice().sort((a, b) => (a.required === b.required ? 0 : a.required ? -1 : 1) || rank(a) - rank(b));
  const out: Record<string, FrSelected> = {};
  for (const card of cards) {
    if (!cat.picked.has(card.slug)) continue;
    out[card.slug] = {template: card.template, display_name: catalogName(cat, card), instructions: catalogInstructions(cat, card),
                      ...(card.required ? {} : {reports_to: frReports(state, card.slug)})};
  }
  return out;
}
// Two bots that report to each other would be refused at Create; say so on the screen instead.
function frProblem(state: FrState): string {
  const cat = state.catalog;
  for (const card of cat.cards) {
    if (card.required || !cat.picked.has(card.slug)) continue;
    const seen = new Set([card.slug]);
    for (let at = frReports(state, card.slug); at && !at.startsWith('human:'); at = frReports(state, at)) {
      if (seen.has(at)) return `${catalogName(cat, card)} and ${catalogName(cat, catalogCard(cat, at) || card)} report to each other. Point one of them at a human.`;
      seen.add(at);
      const next = catalogCard(cat, at);
      if (!next || next.required) break;
    }
  }
  const inbox = cat.cards.find(card => card.template === 'inbox');
  // catalogMissingMailbox is true only when an inbox card exists, so inbox is set on that side.
  return catalogMissingMailbox(cat) ? `Choose whose mailbox ${catalogName(cat, inbox!)} reads.` : '';
}

// ----------------------------------------------------------------- review
function frSummaryTeamHTML(state: FrState): string {
  const cat = state.catalog, org = state.org;
  // In the order they are set up: each department's head first, then its team.
  const rows = Object.keys(frSelection(state)).map(slug => catalogCard(cat, slug)).filter((card): card is CatalogCard => !!card && !card.required && card.kind !== 'helper');
  // Built-ins and explicit catalog helpers stay outside the chart, in separate review rows.
  const helpers = cat.cards.filter(card => card.required)
    .map(card => esc(catalogName(cat, card))).join(', ');
  const messageBots = cat.cards.filter(card => !card.required && card.kind === 'helper' && cat.picked.has(card.slug))
    .map(card => esc(catalogName(cat, card))).join(', ');
  const departments = org.chosen.filter(id => obPicked(state, id).length).map(id => esc(obDept(org, id)?.name || id)).join(', ');
  return `<div><span class="k">Built-in</span><span data-review-helpers>${helpers || '—'}</span></div>
    <div><span class="k">Message bots</span><span data-review-message-bots>${messageBots || '—'}</span></div>
    ${org.loaded ? `<div><span class="k">Groups</span><span data-review-departments>${departments || '—'}</span></div>` : ''}
    <div><span class="k">Your team</span><span data-review-team>${rows.length ? rows.map(card =>
      `${esc(catalogName(cat, card))} <span class="muted">→ ${esc(frParentName(state, frReports(state, card.slug)))}</span>`).join('<br>') : 'Just the built-in bots'}</span></div>`;
}

// ----------------------------------------------------------------- after Create
// One screen: the team is created, the one next step, each bot's setup, an admin to invite, an owner for each bot, and
// where tools are connected. What every bot waits on is said once, at the top: bots are placed when a computer appears
// and run once there is a provider.
const frWaiting = (state: FrState): string => ((state.record.machine?.runners || []).length ? (S.config?.providers_configured === false ? 'provider' : '') : 'computer');
// The next step, with the only primary button on the screen (with nothing to wait on, Open Tasks at the bottom), and the tour.
function frNextStepHTML(state: FrState): string {
  const waiting = frWaiting(state);
  const line = waiting === 'computer' ? 'Waiting for a computer. Your bots start once one is online.'
    : `${onbMachineStatus(state.record)}${waiting === 'provider' ? '. Waiting for an AI provider.' : ''}`;
  const button = waiting === 'computer' ? '<button class="primary" type="button" data-onb-go="devices">Add a computer</button>'
    : waiting === 'provider' ? '<button class="primary" type="button" data-onb-go="models">Add an AI provider</button>' : '';
  return `<p class="onb-machine" id="onb-next-status" role="status">${esc(line)}</p>${button}`;
}
// A row's one status: a word, or the one thing to do there (Set up, Activate, the setup task being worked).
function frBotStatusHTML(state: FrState, bot: FrBot): string {
  const card = state.catalog.cards.find(row => row.slug === bot.slug || row.template === bot.template);
  const bootstrap = bot.id === assistantBot() || !!card?.bootstrap;
  const ready = !!bot.repository_present, active = bot.status === 'active', parked = ['needs_setup', 'needs_onboarding'].includes(String(bot.onboarding_state));
  const name = esc(bot.display_name || bot.slug);
  if (frWaiting(state)) return '<span class="pill waiting">Waiting</span>';
  if (parked) return ready ? `<button class="ghost" type="button" data-fr-start="${esc(bot.id)}" aria-label="Set up ${name}">Set up</button>`
    : '<span class="pill waiting">Setting up</span>';
  if (active) return '<span class="pill ok">Active</span>';
  if (ready && !bootstrap) return `<button class="ghost" type="button" data-onb-activate="${esc(bot.id)}" aria-label="Activate ${name}">Activate</button>`;
  if (!bootstrap && bot.setup_task_id) return `<a href="#/task/${esc(bot.setup_task_id)}" data-onb-task="${esc(bot.id)}">Setting it up</a>`;
  return '<span class="pill waiting">Setting up</span>';
}
function frBotRowHTML(state: FrState, bot: FrBot): string {
  return `<div class="onb-bot" data-onb-bot="${esc(bot.id)}">${avatar(bot.id, 27)}
      <strong class="onb-bot-name">${esc(bot.display_name || bot.slug)}</strong>
      <div class="onb-bot-actions">${frBotStatusHTML(state, bot)}</div></div>`;
}
// The bots in the team chart's groups and order (ui/app/sidebar.ts): the bots outside any group first, as on the chart,
// then each group, nested ones after their parent, then the built-ins. A bot the chart does not list yet joins the first.
function frBotGroups(bots: FrBot[]): FrGroup[] {
  const byId = new Map<string, FrBot>(bots.map(bot => [bot.id, bot])), seen = new Set<string>();
  const loose: FrGroup = {id: '', name: '', bots: []}, groups: FrGroup[] = [loose];
  const tree = orgTreeWithHelpers(orgTreeByParent());
  const walk = (parent: string, group: FrGroup) => {
    for (const node of orgSorted(parent, tree[parent])) {
      if (node.kind === 'group') {
        const inner: FrGroup = {id: node.id, name: node.name, bots: []};
        groups.push(inner);
        walk('g:' + node.id, inner);
      } else if (node.kind === 'person') walk('p:' + node.id, group);
      else {
        if (byId.has(node.id) && !seen.has(node.id)) { seen.add(node.id); group.bots.push(byId.get(node.id)!); }
        walk('b:' + node.id, group);
      }
    }
  };
  walk('', loose);
  const rest = bots.filter(bot => !seen.has(bot.id));
  loose.bots.push(...rest.filter(bot => !isBuiltInBot(bot.id)));
  groups.push({id: 'built-in', name: 'Built-in', bots: rest.filter(bot => isBuiltInBot(bot.id))
    .sort((a, b) => helperRank(a) - helperRank(b))});
  return groups.filter(group => group.bots.length);
}
// The saved choice, with provider names for older installs that predate presets.
function frProvidersName(state: FrState): string {
  const view = state.providers;
  if (!view) return 'See provider settings';
  const preset = ({'subscriptions-gateway': 'subscriptions plus Tico Gateway', subscriptions: 'subscriptions only',
    gateway: 'Tico Gateway only'} as Record<string, string | undefined>)[String(view.preset)];
  if (preset) return preset;
  return (view.enabled || []).map(id => (view.providers || []).find(row => row.id === id)?.label || id).join(', ') || 'None chosen';
}
function frNextHTML(state: FrState): string {
  const bots = state.record.bots || [];
  const owners = bots.map(bot => `<div class="fr-owner" data-fr-owner-row="${esc(bot.id)}"><span>${esc(bot.display_name || bot.slug)}</span>
      <select data-fr-owner="${esc(bot.id)}" aria-label="Add an owner for ${esc(bot.display_name || bot.slug)}"><option value="">Add an owner…</option>${frPeople().map(person =>
        `<option value="${esc(person.id)}">${esc(person.name || person.id)}${('human:' + person.id) === frOwner() ? ' (you)' : ''}</option>`).join('')}</select>
      <span class="muted" data-fr-owned="${esc(bot.id)}">${esc(frPersonName(S.me?.id))} owns it</span></div>`).join('');
  // Getting started's "Publish your strategy" (backend/getting_started.py): setup saved a draft, and publishing is a
  // deliberate click on the Strategy page.
  const strategy = GS?.items?.find(item => item.id === 'strategy');
  // AI providers stays first: without one no bot runs.
  return `<p class="fr-providers" id="fr-providers"><strong>AI providers:</strong> ${esc(frProvidersName(state))}.
      <a href="#/settings" data-gs-tab="models">${S.config?.providers_configured === false ? 'Add an AI provider' : 'Change'}</a></p>
    ${strategy?.done ? '' : '<p class="fr-strategy" id="fr-strategy"><strong>Strategy:</strong> a draft. <a href="#/strategy">Publish your strategy</a></p>'}
    <details class="fr-owners" id="fr-owners"><summary><span>${bots.length ? `You own all ${bots.length} bots.` : 'No bots yet.'}</span> <span class="linkish">Change</span></summary>
      <div class="fr-owner-list">${owners}</div></details>
    <details class="fr-admin" id="fr-admin"><summary>Add an admin <span class="muted">(optional)</span></summary>
      <form id="fr-admin-form" class="fr-form"><label class="onb-field"><span class="k">Name</span><input name="name" autocomplete="off" placeholder="e.g. Alex Chen"></label>
        <label class="onb-field"><span class="k">Email</span><input name="email" type="email" autocomplete="off" required placeholder="alex@company.example"></label>
        <div class="onb-actions"><button class="ghost" type="submit">Add admin</button><span class="muted" id="fr-admin-status" role="status"></span></div></form>
      <small class="muted">Tico sends no email: tell them.${S.config?.local ? ' Only you can sign in until you add a domain and sign-in.' : ''}</small></details>
    <details class="fr-connections"><summary>Connect tools and credentials</summary>
      <p><a href="#/tools/keys" data-fr-link="credentials">Keys</a> · <a href="#/tools" data-fr-link="integrations">Tools</a></p>
      <p class="fr-secrets" data-fr-secrets><strong>Enter credentials in Tools › Keys, never in a chat with a bot.</strong> One pasted into a chat is leaked: rotate it.</p></details>`;
}

function frWireDone(state: FrState) {
  document.querySelectorAll<HTMLButtonElement>('[data-fr-start]').forEach(button => button.onclick = () => void frStartSetup(String(button.dataset.frStart), button));
  // Add a computer and Add an AI provider open their Settings tab.
  document.querySelectorAll<HTMLElement>('[data-onb-go]').forEach(button => button.onclick = () => {
    SETTINGS_TAB = String(button.dataset.onbGo);
    if (location.hash === '#/settings') route(); else location.hash = '#/settings';
  });
}
async function frStartSetup(slug: string, button: HTMLButtonElement | null) {
  if (button) button.disabled = true;
  try {
    await frSendSetup(slug);
    location.hash = '#/bot/' + encodeURIComponent(slug);
  } catch (error) { toast(errorMessage(error, ''), true); if (button) button.disabled = false; }
}
// Start setup: activate the bot when it is only planned (a starter runs nothing on its own), then say the one line that
// begins its setup conversation. Any first message from a human does the same.
async function frSendSetup(slug: string) {
  const bots: FrBot[] | null = await v2Get('/v2/bots?include_archived=1');
  const bot = (bots || []).find(row => row.id === slug);
  if (bot && bot.state === 'planned') await post(`/v2/bots/${encodeURIComponent(slug)}/definition`, {status: 'active', expected_revision: bot.revision});
  // Onboarding is team work: it lands in the bot's Home, where its manager and the team see it, not a private DM.
  const home = await get<{id: string}>('/v2/threads/home/' + encodeURIComponent('bot:' + slug), {fresh: true});
  await post(`/v2/threads/${encodeURIComponent(home.id)}/messages`, {text: "Let's set you up."});
  threadsReloadHome('bot:' + slug);
  await refresh(true);
}
function frWireNext(state: FrState) {
  const form = $('#fr-admin-form');
  if (form) form.onsubmit = async (event: Event) => {
    event.preventDefault();
    const status = $('#fr-admin-status'), data = Object.fromEntries(new FormData(form));
    form.querySelector('button').disabled = true; status.textContent = 'Adding…';
    try {
      const added = await post('/v2/access/humans', {name: data.name || '', email: data.email});
      // Adding is the roster and the sign-in list; the role is a second, owner-only step.
      await post('/v2/access/humans/' + encodeURIComponent(added.person), {role: 'admin'});
      status.textContent = `${added.name || data.email} is on the roster as an admin.${S.config?.local ? ' Only you can sign in until you add a domain and sign-in.' : ' Tico sends no email: tell them.'}`;
      form.reset();
      const roster = await get('/humans').catch(() => null);
      if (roster?.people) { SETTINGS_DATA.people = roster.people; setPeople(roster); }
    } catch (error) { status.innerHTML = `<span class="err">${esc(errorMessage(error, ''))}</span>`; }
    form.querySelector('button').disabled = false;
  };
  document.querySelectorAll<HTMLSelectElement>('[data-fr-owner]').forEach(select => select.onchange = async () => {
    const slug = String(select.dataset.frOwner), person = select.value;
    if (!person) return;
    const note = document.querySelector(`[data-fr-owned="${cssSelectorValue(slug)}"]`);
    select.disabled = true;
    try {
      const done: {bot_owners?: BriefPerson[]} = await post(`/v2/bots/${encodeURIComponent(slug)}/co-owners`, {add: [person], remove: []});
      if (note) note.textContent = (done.bot_owners || []).map(o => o.name || o.id).join(', ') + ' own it';
    } catch (error) { if (note) note.innerHTML = `<span class="err">${esc(errorMessage(error, ''))}</span>`; }
    select.value = ''; select.disabled = false;
  });
}

// ----------------------------------------------------------------- the bot page and the org chart
const frNeedsSetup = (e: FrBot | null | undefined): e is FrBot => !!e && ['needs_setup', 'needs_onboarding'].includes(String(e.onboarding_state));
// The org chart's mark: small, beside the name, a gear so the name keeps the row; it says the bot page's words on hover.
const frTreeMark = (e: FrBot | null | undefined): string => (frNeedsSetup(e) ? '<span class="tree-setup" role="img" aria-label="Onboarding" title="Onboarding: it learns in chat and takes no work until someone above it marks onboarding complete"><span class="nav-icon" aria-hidden="true">settings</span></span>' : '');
// Onboarding (backend/lifecycle.py): the bot learns in chat; a person above it on the org chart marks it complete.
function frBotBannerHTML(e: FrBot | null | undefined): string {
  if (!frNeedsSetup(e)) return '';
  const manager = typeof settingsCanManageBot === 'function' && settingsCanManageBot(e);
  const planned = e.status === 'planned';
  const can = e.can_chat && (!planned || manager);
  const ready = e.onboarding_ready?.summary;
  return `<section class="bot-onboard" id="bot-onboard" role="status" aria-label="Onboarding">
      <span class="pill needs">Onboarding</span>
      ${can ? '<button class="primary" type="button" id="bot-start-setup">Start onboarding</button>'
        : `<span class="muted">${planned ? 'Getting ready.' : 'It learns in chat with its manager before it takes any work.'}</span>`}
      ${e.can_complete_onboarding ? '<button type="button" id="bot-finish-setup">Mark onboarding complete</button>' : ''}
      <button class="ghost" type="button" data-setup-help="bot">Want help onboarding it?</button>
      ${ready ? `<p class="muted bot-ready" id="bot-ready">It says it is ready: ${esc(ready)}</p>` : ''}
    </section>`;
}
function frBotWire(slug: string) {
  const button = $('#bot-start-setup');
  if (button) button.onclick = async () => {
    button.disabled = true; button.textContent = 'Starting…';
    try { await frSendSetup(slug); button.textContent = 'Onboarding started'; showBotTab('home'); }
    catch (error) { toast(errorMessage(error, ''), true); button.disabled = false; button.textContent = 'Start onboarding'; }
  };
  const finish = $('#bot-finish-setup');
  if (finish) finish.onclick = async () => {
    finish.disabled = true; finish.textContent = 'Saving…';
    try {
      const result = await post(`/v2/bots/${encodeURIComponent(slug)}/onboarded`, {});
      const bot = botRow(slug);
      if (bot) Object.assign(bot, {onboarding_state: result.onboarding_state, stage: result.stage});
      if (BOT?.slug === slug) frBotRefresh(slug);
      renderTree();
      toast('Onboarding marked complete');
    } catch (error) {
      toast(errorMessage(error, ''), true);
      finish.disabled = false; finish.textContent = 'Mark onboarding complete';
    }
  };
}
// The page follows setup completion by the bot or its manager.
function frBotRefresh(slug: string) {
  const e = botRow(slug), host = $('#bot-onboard-host');
  if (host) { host.innerHTML = frBotBannerHTML(e); frBotWire(slug); }
}
