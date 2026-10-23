/* First run, the team: who each bot reports to, the review, and the screen after Create (docs/onboarding.md).
   The org chart itself is built in ui/org-builder.js; this turns what is checked there into `selected` (the order to set
   the bots up in, each with its reports-to), says what is wrong before Create, and shows the rest. Nothing is created
   until "Create my team". It shares the wizard's state (`ONB`) and the catalog cards' state (`catalogState`) with
   ui/app/welcome.js and ui/app/catalog.js. */
'use strict';
const frOwner = () => (S.me?.id ? 'human:' + S.me.id : '');
const frHome = state => state.record.home || frOwner();
const frPeople = () => (SETTINGS_DATA.people?.length ? SETTINGS_DATA.people : (S.me?.id ? [{id: S.me.id, name: S.me.name}] : []));
const frPersonName = id => frPeople().find(p => p.id === id)?.name || id;
// The bot a card is created as: the assistant's card is named `coo` but the team's own bot is `assistantBot()`.
const frBotSlug = card => (card.template === 'assistant' ? assistantBot() : card.slug);

function frCollect(state, key) {
  if (state.catalog.cards.length) obCollect(state);
}

// ----------------------------------------------------------------- who reports to whom
// What a bot reports to: what the human chose, else its group head, else the owner.
const frRawReports = (state, slug) => state.catalog.edits[slug]?.reports_to ?? obDefaultReports(state, slug);
function frReports(state, slug) {
  const wanted = frRawReports(state, slug);
  // A parent that was removed leaves its team to the owner rather than a bot that will not exist.
  return frParentOptions(state, slug).some(option => option.value === wanted) ? wanted : frHome(state);
}
function frDescends(state, slug, of) {
  const seen = new Set();
  for (let at = slug; at && !seen.has(at); at = frRawReports(state, at)) { if (at === of) return true; seen.add(at); }
  return false;
}
function frParentOptions(state, slug) {
  const cat = state.catalog, out = [];
  const owner = frOwner();
  for (const person of frPeople()) out.push({value: 'human:' + person.id, label: (person.name || person.id) + (('human:' + person.id) === owner ? ' (you)' : ''), group: 'Humans'});
  // Built-in and message bots are not on the chart, so nobody reports to one.
  for (const card of cat.cards) {
    if (!card.required && card.kind !== 'helper' && cat.picked.has(card.slug) && card.slug !== slug && !frDescends(state, card.slug, slug))
      out.push({value: card.slug, label: catalogName(cat, card), group: 'Bots on your team'});
  }
  return out;
}
function frParentName(state, value) {
  const cat = state.catalog;
  if (String(value).startsWith('human:')) return frPersonName(value.slice(6));
  const card = cat.cards.find(row => frBotSlug(row) === value || row.slug === value);
  return card ? catalogName(cat, card) : value;
}
// The team in the order it is set up: the built-ins, then each department's bots with its head first.
function frSelection(state) {
  const cat = state.catalog, order = [];
  for (const id of state.org.chosen) for (const card of obPicked(state, id)) order.push(obCatalogCard(state, card.template)?.slug);
  const rank = card => { const i = order.indexOf(card.slug); return i < 0 ? 999 : i; };
  const cards = cat.cards.slice().sort((a, b) => (a.required === b.required ? 0 : a.required ? -1 : 1) || rank(a) - rank(b));
  const out = {};
  for (const card of cards) {
    if (!cat.picked.has(card.slug)) continue;
    out[card.slug] = {template: card.template, display_name: catalogName(cat, card), instructions: catalogInstructions(cat, card),
                      ...(card.required ? {} : {reports_to: frReports(state, card.slug)})};
  }
  return out;
}
// Two bots that report to each other would be refused at Create; say so on the screen instead.
function frProblem(state) {
  const cat = state.catalog;
  for (const card of cat.cards) {
    if (card.required || !cat.picked.has(card.slug)) continue;
    const seen = new Set([card.slug]);
    for (let at = frReports(state, card.slug); at && !at.startsWith('human:'); at = frReports(state, at)) {
      if (seen.has(at)) return `${catalogName(cat, card)} and ${catalogName(cat, catalogCard(cat, at) || card)} report to each other. Point one of them at a human.`;
      seen.add(at);
      if (!catalogCard(cat, at) || catalogCard(cat, at).required) break;
    }
  }
  const inbox = cat.cards.find(card => card.template === 'inbox');
  return catalogMissingMailbox(cat) ? `Choose whose mailbox ${catalogName(cat, inbox)} reads.` : '';
}

// ----------------------------------------------------------------- review
function frSummaryTeamHTML(state) {
  const cat = state.catalog, org = state.org;
  // In the order they are set up: each department's head first, then its team.
  const rows = Object.keys(frSelection(state)).map(slug => catalogCard(cat, slug)).filter(card => card && !card.required && card.kind !== 'helper');
  // Built-in and message bots stay outside the chart, in separate review rows.
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
const frWaiting = state => ((state.record.machine?.runners || []).length ? (S.config?.providers_configured === false ? 'provider' : '') : 'computer');
// The next step, with the only primary button on the screen (with nothing to wait on, Open Tasks at the bottom), and the tour.
function frNextStepHTML(state) {
  const waiting = frWaiting(state);
  const line = waiting === 'computer' ? 'Waiting for a computer. Your bots start once one is online.'
    : `${onbMachineStatus(state.record)}${waiting === 'provider' ? '. Waiting for an AI provider.' : ''}`;
  const button = waiting === 'computer' ? '<button class="primary" type="button" data-onb-go="devices">Add a computer</button>'
    : waiting === 'provider' ? '<button class="primary" type="button" data-onb-go="models">Add an AI provider</button>' : '';
  return `<p class="onb-machine" id="onb-next-status" role="status">${esc(line)}</p>${button}`;
}
// A row's one status: a word, or the one thing to do there (Set up, Activate, the setup task BotOps is working).
function frBotStatusHTML(state, bot, botOps) {
  const card = state.catalog.cards.find(row => row.slug === bot.slug || row.template === bot.template);
  const bootstrap = bot.slug === assistantBot() || !!card?.bootstrap;
  const ready = !!bot.repository_present, active = bot.status === 'active', parked = ['needs_setup', 'needs_onboarding'].includes(bot.onboarding_state);
  const name = esc(bot.display_name || bot.slug);
  if (frWaiting(state)) return '<span class="pill waiting">Waiting</span>';
  if (parked) return ready ? `<button class="ghost" type="button" data-fr-start="${esc(bot.slug)}" aria-label="Set up ${name}">Set up</button>`
    : '<span class="pill waiting">Setting up</span>';
  if (active) return '<span class="pill ok">Active</span>';
  if (ready && !bootstrap) return `<button class="ghost" type="button" data-onb-activate="${esc(bot.slug)}" aria-label="Activate ${name}">Activate</button>`;
  if (!bootstrap && bot.setup_task_id) return `<a href="#/task/${esc(bot.setup_task_id)}" data-onb-task="${esc(bot.slug)}">${esc(botOps)} is setting it up</a>`;
  return '<span class="pill waiting">Setting up</span>';
}
function frBotRowHTML(state, bot, botOps) {
  return `<div class="onb-bot" data-onb-bot="${esc(bot.slug)}">${avatar(bot.slug, 27)}
      <strong class="onb-bot-name">${esc(bot.display_name || bot.slug)}</strong>
      <div class="onb-bot-actions">${frBotStatusHTML(state, bot, botOps)}</div></div>`;
}
// The bots in the team chart's groups and order (ui/app/sidebar.js): the bots outside any group first, as on the chart,
// then each group, nested ones after their parent, then the built-ins. A bot the chart does not list yet joins the first.
function frBotGroups(bots) {
  const bySlug = new Map(bots.map(bot => [bot.slug, bot])), seen = new Set();
  const loose = {id: '', name: '', bots: []}, groups = [loose];
  const tree = orgTreeWithHelpers(orgTreeByParent());
  const walk = (parent, group) => {
    for (const node of orgSorted(parent, tree[parent])) {
      if (node.kind === 'group') {
        const inner = {id: node.id, name: node.name, bots: []};
        groups.push(inner);
        walk('g:' + node.id, inner);
      } else if (node.kind === 'person') walk('p:' + node.id, group);
      else {
        if (bySlug.has(node.name) && !seen.has(node.name)) { seen.add(node.name); group.bots.push(bySlug.get(node.name)); }
        walk('b:' + node.name, group);
      }
    }
  };
  walk('', loose);
  const rest = bots.filter(bot => !seen.has(bot.slug));
  loose.bots.push(...rest.filter(bot => !isBuiltInBot(bot.slug)));
  groups.push({id: 'built-in', name: 'Built-in', bots: rest.filter(bot => isBuiltInBot(bot.slug))
    .sort((a, b) => helperRank({name: a.slug}) - helperRank({name: b.slug}))});
  return groups.filter(group => group.bots.length);
}
// The saved choice, with provider names for older installs that predate presets.
function frProvidersName(state) {
  const view = state.providers;
  if (!view) return 'See provider settings';
  const preset = {'subscriptions-gateway': 'subscriptions plus Tico Gateway', subscriptions: 'subscriptions only',
    gateway: 'Tico Gateway only'}[view.preset];
  if (preset) return preset;
  return (view.enabled || []).map(id => (view.providers || []).find(row => row.id === id)?.label || id).join(', ') || 'None chosen';
}
function frNextHTML(state) {
  const bots = state.record.bots || [];
  const owners = bots.map(bot => `<div class="fr-owner" data-fr-owner-row="${esc(bot.slug)}"><span>${esc(bot.display_name || bot.slug)}</span>
      <select data-fr-owner="${esc(bot.slug)}" aria-label="Add an owner for ${esc(bot.display_name || bot.slug)}"><option value="">Add an owner…</option>${frPeople().map(person =>
        `<option value="${esc(person.id)}">${esc(person.name || person.id)}${('human:' + person.id) === frOwner() ? ' (you)' : ''}</option>`).join('')}</select>
      <span class="muted" data-fr-owned="${esc(bot.slug)}">${esc(frPersonName(S.me?.id))} owns it</span></div>`).join('');
  return `<p class="fr-providers" id="fr-providers"><strong>AI providers:</strong> ${esc(frProvidersName(state))}.
      <a href="#/settings" data-gs-tab="models">${S.config?.providers_configured === false ? 'Add an AI provider' : 'Change'}</a></p>
    <details class="fr-owners" id="fr-owners"><summary><span>${bots.length ? `You own all ${bots.length} bots.` : 'No bots yet.'}</span> <span class="linkish">Change</span></summary>
      <div class="fr-owner-list">${owners}</div></details>
    <details class="fr-admin" id="fr-admin"><summary>Add an admin <span class="muted">(optional)</span></summary>
      <form id="fr-admin-form" class="fr-form"><label class="onb-field"><span class="k">Name</span><input name="name" autocomplete="off" placeholder="e.g. Alex Chen"></label>
        <label class="onb-field"><span class="k">Email</span><input name="email" type="email" autocomplete="off" required placeholder="alex@company.example"></label>
        <div class="onb-actions"><button class="ghost" type="submit">Add admin</button><span class="muted" id="fr-admin-status" role="status"></span></div></form>
      <small class="muted">Tico sends no email: tell them.${S.config?.local ? ' Only you can sign in until you add a domain and sign-in.' : ''}</small></details>
    <details class="fr-connections"><summary>Connect tools and credentials</summary>
      <p><a href="#/credentials" data-fr-link="credentials">Credentials</a> · <a href="#/integrations" data-fr-link="integrations">Tools</a></p>
      <p class="fr-secrets" data-fr-secrets><strong>Enter credentials on the Credentials page, never in a chat with a bot.</strong> One pasted into a chat is leaked: rotate it.</p></details>`;
}

function frWireDone(state) {
  document.querySelectorAll('[data-fr-start]').forEach(button => button.onclick = () => void frStartSetup(button.dataset.frStart, button));
  // Add a computer and Add an AI provider open their Settings tab.
  document.querySelectorAll('[data-onb-go]').forEach(button => button.onclick = () => {
    SETTINGS_TAB = button.dataset.onbGo;
    if (location.hash === '#/settings') route(); else location.hash = '#/settings';
  });
}
async function frStartSetup(slug, button) {
  if (button) button.disabled = true;
  try {
    await frSendSetup(slug);
    location.hash = '#/bot/' + encodeURIComponent(slug);
  } catch (error) { toast(error.message, true); if (button) button.disabled = false; }
}
// Start setup: activate the bot when it is only planned (a starter runs nothing on its own), then say the one line that
// begins its setup conversation. Any first message from a human does the same.
async function frSendSetup(slug) {
  const bots = await v2Get('/v2/bots?include_archived=1');
  const bot = (bots || []).find(row => row.slug === slug);
  if (bot && bot.state === 'planned') await post(`/v2/bots/${encodeURIComponent(slug)}/definition`, {status: 'active', expected_revision: bot.revision});
  v2ChatAdopt(slug, await post(`/v2/chat/${encodeURIComponent(slug)}`, {text: "Let's set you up."}));   // the Chat tab shows it
  await refresh(true);
}
function frWireNext(state) {
  const form = $('#fr-admin-form');
  if (form) form.onsubmit = async event => {
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
    } catch (error) { status.innerHTML = `<span class="err">${esc(error.message)}</span>`; }
    form.querySelector('button').disabled = false;
  };
  document.querySelectorAll('[data-fr-owner]').forEach(select => select.onchange = async () => {
    const slug = select.dataset.frOwner, person = select.value;
    if (!person) return;
    const note = document.querySelector(`[data-fr-owned="${cssSelectorValue(slug)}"]`);
    select.disabled = true;
    try {
      const done = await post(`/v2/bots/${encodeURIComponent(slug)}/co-owners`, {add: [person], remove: []});
      if (note) note.textContent = (done.bot_owners || []).map(o => o.name || o.id).join(', ') + ' own it';
    } catch (error) { if (note) note.innerHTML = `<span class="err">${esc(error.message)}</span>`; }
    select.value = ''; select.disabled = false;
  });
}

// ----------------------------------------------------------------- the bot page and the org chart
const frNeedsSetup = e => !!e && ['needs_setup', 'needs_onboarding'].includes(e.onboarding_state);
// The org chart's mark: small, beside the name, a gear so the name keeps the row; it says the bot page's words on hover.
const frTreeMark = e => (frNeedsSetup(e) ? '<span class="tree-setup" role="img" aria-label="Needs setup" title="Needs setup: set it up together before it does anything on its own"><span class="nav-icon" aria-hidden="true">settings</span></span>' : '');
function frBotBannerHTML(e) {
  if (!frNeedsSetup(e)) return '';
  const manager = typeof settingsCanManageBot === 'function' && settingsCanManageBot(e);
  const planned = e.status === 'planned';
  const can = e.can_chat && (!planned || manager);
  return `<section class="bot-onboard" id="bot-onboard" role="status" aria-label="Needs setup">
      <span class="pill needs">Needs setup</span>
      ${can ? '<button class="primary" type="button" id="bot-start-setup">Set up</button>'
        : `<span class="muted">${planned ? 'Setting up.' : 'Ask someone who can write to it to start.'}</span>`}
      ${manager ? '<button type="button" id="bot-finish-setup">Mark setup done</button>' : ''}
      <button class="ghost" type="button" data-setup-help="bot">Want help setting up?</button>
    </section>`;
}
function frBotWire(slug) {
  const button = $('#bot-start-setup');
  if (button) button.onclick = async () => {
    button.disabled = true; button.textContent = 'Starting…';
    try { await frSendSetup(slug); button.textContent = 'Setup started'; showBotTab('chat'); }
    catch (error) { toast(error.message, true); button.disabled = false; button.textContent = 'Set up'; }
  };
  const finish = $('#bot-finish-setup');
  if (finish) finish.onclick = async () => {
    finish.disabled = true; finish.textContent = 'Saving…';
    try {
      const result = await post(`/v2/bots/${encodeURIComponent(slug)}/onboarded`, {});
      const bot = S.emps.find(e => e.name === slug);
      if (bot) bot.onboarding_state = result.onboarding_state;
      if (BOT?.slug === slug) frBotRefresh(slug);
      renderTree();
      toast('Setup marked done');
    } catch (error) {
      toast(error.message, true);
      finish.disabled = false; finish.textContent = 'Mark setup done';
    }
  };
}
// The page follows setup completion by the bot or its manager.
function frBotRefresh(slug) {
  const e = S.emps.find(x => x.name === slug), host = $('#bot-onboard-host');
  if (host) { host.innerHTML = frBotBannerHTML(e); frBotWire(slug); }
}
