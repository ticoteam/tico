/* ui/app/settings-cards.js — Settings: every card's first line and its Ask BotOps link
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// One row per card: where it is, its name as people say it (the Ask BotOps draft names it), and its first line, which
// says in plain words what the card controls. A card its own module draws (People, Repositories, Email & Phone...)
// gets its line once the module has drawn it: settingsCardsWatch redraws the line whenever a card loses it. A card
// whose line is in its own markup (Automation's) has '' here and keeps that line. Task settings is the Tasks Owner's
// card: it gets the link but no line from here.
const SETTINGS_CARDS = [
  ['#settings-theme-card', 'Theme', 'How Tico looks for you: dark, light, or the same as your device.'],
  ['#settings-assistant-pane', 'my Assistant', 'Your own Assistant: open its chat and, where the team allows it, choose whether it may decide your approvals for you.'],
  ['#settings-inbox', 'my Google account', 'Connect your own Google account, read only, so the computer that syncs your mail can read it.'],
  ['#set-people .people-join', 'adding people', 'How people join this team: add them by email, or keep the list in step with your company directory.'],
  ['#set-people .people-list-card:not(#setup-people)', 'People', 'Everyone on the team, the role each has, and whether they can sign in.'],
  ['#setup-people', 'the people from setup', 'People named while this team was set up who are not on the team yet. Add the ones who should be.'],
  ['#settings-ceo > .card', 'the CEO bot', 'How much the CEO bot does on its own, and what it has done lately, with Undo.'],
  ['#settings-bots-card', 'Bots', 'Every bot on the team: who owns it, what it may reach, its model and its spend limit. Add, edit or archive bots here.'],
  ['#settings-models', 'the company models', 'The AI providers and models bots use, the company default model, and what bots fall back to.'],
  ['#set-gateway-page > .card:nth-of-type(1)', 'Tico AI Gateway', 'Whether this team is connected to Tico AI Gateway, and how much it decides for you.'],
  ['#set-gateway-page > .card:nth-of-type(2)', 'Gateway picks', 'The Tico AI Gateway models bots and jobs use now.'],
  ['#set-gateway-page > .card:nth-of-type(3)', 'Gateway services', 'The services Tico AI Gateway offers, with their prices and the tools each brings.'],
  ['#settings-computers', 'Computers', 'The computers bots run on: add one, see whether it is online, and sign its AI tools in.'],
  ['#settings-hosted', 'Hosted servers', 'The servers Tico hosts for this company, and whether each is running.'],
  ['#settings-sleep', 'Sleep when idle', 'A server Tico hosts can stop itself, and its bot computers, when it has nothing to do. Tico starts it again for the next routine or a visit.'],
  ['#settings-spend-limits', 'a bot\'s spend limit', 'When a bot\'s estimated spend reaches its hourly, daily, weekly or monthly limit, it starts no new work until the period turns over (UTC) or the limit is raised. A run already going finishes. Its operator gets a notice at 80% and again at 100%.'],
  ['#settings-turn-budgets', 'the run budget', 'When a bot starts more runs in an hour or a day than this, Tico holds it as a likely runaway loop. Its queued work waits until someone releases it or raises the budget. A person\'s own message still goes through.'],
  ['#settings-turn-levels', 'the company turn levels', 'Both levels count bot turns running at the same time, per signed-in person. At the warning level Health shows it; at the stop level no new bot turn starts, and new work waits until running turns finish.'],
  ['#settings-billing > .card', 'Billing', 'Your Tico balance and credits. Tico models stop when the balance reaches $0.00.'],
  ['#set-routines-card', 'Bot routines', ''],
  ['#set-rules-card', 'Rules', ''],
  ['#set-system-card', 'What Tico runs on its own', ''],
  ['#set-release-card', 'Releases', ''],
  ['#set-task-manager-card', 'the Task Manager', ''],
  ['#settings-listening > .card', 'Listening', ''],
  ['#set-ask-filter-card', 'What bots may put on me', 'What bots may put on your plate, how many open items you take before that tightens, and your chores budget.'],
  ['#set-task-settings-card', 'Task settings', ''],
  ['#settings-types > .card', 'Task types', 'The kinds of task this team uses and the steps each one moves through.'],
  ['#settings-roadmaps', 'the public roadmap', 'Which tags show on the public roadmap, the websites that show it, and who gets visitors\' suggestions.'],
  ['#settings-tags-card', 'Task tags', 'The tags on tasks: rename, recolour or remove them.'],
  ['#set-repos', 'Repositories', 'The code repositories bots may work in, and what each bot may do there.'],
  ['#set-inbound', 'Email & Phone', 'The email addresses and phone numbers this company receives messages on, and which bot reads them.'],
  ['#settings-webhooks > .card', 'Webhooks', 'Addresses other services call to start work here (Incoming), and the events Tico sends out (Outgoing).'],
  ['#settings-tokens', 'API tokens', 'Tokens that let a script or the hub command line act as you. Each is shown once, when it is made.'],
  ['#settings-tools-link', 'Tools & Credentials', ''],
  ['#settings-team', 'the team', 'This team\'s icon, and how it shows pictures, video and audio.'],
  ['#settings-policies', 'the team policies', 'What bots may do without asking, who may see and store what, and whether Tico support may be let in.'],
  ['#settings-company', 'the Tico company link', 'Links this self-run server to its company on app.tico.dev.'],
  ['#settings-storage', 'Storage', 'Where Tico keeps files, pictures and recordings: on this server, or in a bucket of your own.'],
  ['#settings-privacy', 'Privacy', 'Whether this server helps Tico count active installs, and the random install ID it sends.'],
  ['#hl-page', 'Health', 'What needs attention now: stuck work, computers, bots waiting and recent failures.'],
  ['#hl-setup-check', 'the setup check', ''],
  ['#settings-services-card', 'Services', 'The services this server depends on, and whether each one answers.'],
  ['#settings-history > .card', 'History', 'What bots ran, and for the owner every change to settings, newest first.'],
];

// The Ask BotOps draft for a card: it names the card's setting and where it is, and is left in the box unsent.
function settingsAskDraft(card) {
  const row = SETTINGS_CARDS.find(([sel]) => card.matches(sel));
  const group = SETTINGS_GROUPS.find(g => g.key === card.closest('[data-settings-group]')?.dataset.settingsGroup);
  const name = row?.[1] || card.querySelector(':scope > header h2')?.textContent.trim() || 'a setting';
  return `Help me change ${name}${group ? ` in Settings > ${group.label}` : ''}: `;
}

function settingsCardsDecorate(root = $('.settings-panes')) {
  if (!root) return;
  for (const [sel, , line] of SETTINGS_CARDS) for (const card of root.querySelectorAll(sel)) {
    const head = card.querySelector(':scope > header');
    let first = card.querySelector(':scope > .settings-card-line');
    if (!first && line) {
      first = document.createElement('p');
      first.className = 'settings-card-line';
      first.textContent = line;
      if (head) head.after(first); else card.prepend(first);
    }
    card.dataset.settingsCard = '';
    if (card.querySelector(':scope > header > [data-settings-ask], :scope > .settings-card-line > [data-settings-ask]')) continue;
    const ask = document.createElement('button');
    ask.type = 'button';
    ask.className = 'linkish settings-ask';
    ask.dataset.settingsAsk = '';
    ask.textContent = 'Ask BotOps';
    if (head) head.append(ask);
    else if (first) first.append(' ', ask);
  }
}

// The cards redraw themselves as their data loads, so the lines and links are put back whenever they go missing.
let SETTINGS_CARDS_WATCH = null;
function settingsCardsWatch() {
  SETTINGS_CARDS_WATCH?.disconnect();
  const root = $('.settings-panes'); if (!root) return;
  settingsCardsDecorate(root);
  SETTINGS_CARDS_WATCH = new MutationObserver(() => settingsCardsDecorate(root));
  SETTINGS_CARDS_WATCH.observe(root, {childList: true, subtree: true});
  root.addEventListener('click', event => {
    const button = event.target.closest('[data-settings-ask]');
    if (!button) return;
    event.preventDefault();
    botopsAsk(settingsAskDraft(button.closest('[data-settings-card]')));
  });
}

// Opens the person's BotOps chat with `text` in the box, sent only when they press send. A draft they already started
// stays, with this one below it; `keep` leaves a started draft alone instead.
function botopsAsk(text, {keep = false} = {}) {
  const bot = (S.emps || []).find(row => row.name === 'botops' && row.can_chat && !botLimited(row));
  if (!bot) { toast('BotOps chat is unavailable. Ask your team owner for access.', true); return; }
  const pill = botPill('botops'), box = pq(pill, '.p-text'), started = box.value.trim();
  if (!started || !keep) {
    box.value = started ? `${box.value.replace(/\s+$/, '')}\n\n${text}` : text;
    box.dispatchEvent(new Event('input', {bubbles: true}));
  }
  pill.focusOnOpen = true;
  if (location.hash === '#/bot/botops/chat') showBotTab('chat'); else location.hash = '#/bot/botops/chat';
}
