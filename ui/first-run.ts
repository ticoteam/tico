/* First run, the team chart's Change (docs/onboarding.md): who each bot reports to, and what is wrong before Create.
   The org chart itself is built in ui/org-builder.ts; this turns what is checked there into `selected` (the order to set
   the bots up in, each with its reports-to). Nothing is created until "Create team". It shares the setup's state
   (`ONB`) and the catalog cards' state (`catalogState`) with ui/app/welcome.ts and ui/app/catalog.ts. Below that, a
   bot's onboarding banner on its page. */
'use strict';

// The wizard's state (ONB, ui/app/welcome.ts): the one org-builder.ts reads too. The catalog is ui/app/catalog.ts's catalogState.
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

const frOwner = (): string => (S.me?.id ? 'human:' + S.me.id : '');
const frHome = (state: FrState): string => state.record.home || frOwner();
const frPeople = () => (SETTINGS_DATA.people?.length ? SETTINGS_DATA.people : (S.me?.id ? [{id: S.me.id, name: S.me.name}] : []));
const frPersonName = (id: string | undefined) => frPeople().find(p => p.id === id)?.name || id;
// The bot a card is created as: the assistant's card is named `assistant` but the team's own bot is `assistantBot()`.
const frBotSlug = (card: CatalogCard): string => (card.template === 'assistant' ? assistantBot() : card.slug);

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
  const inbox = cat.cards.find(card => card.template === 'inbox-manager');
  // catalogMissingMailbox is true only when an inbox card exists, so inbox is set on that side.
  return catalogMissingMailbox(cat) ? `Choose whose mailbox ${catalogName(cat, inbox!)} reads.` : '';
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
// The page follows setup completion by the bot or its manager.
function frBotRefresh(slug: string) {
  const e = botRow(slug), host = $('#bot-onboard-host');
  if (host) { host.innerHTML = frBotBannerHTML(e); frBotWire(slug); }
}
