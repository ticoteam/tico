/* Bot details: summary, inline settings and instruction requests, and settings history. */
'use strict';

/** A bot's fallback choice on its roster row (`fallback`): the harness and model it runs on when the primary is unavailable. */
interface BotDetailsFallback {
  harness?: string | null;
  model?: string | null;
  reasoning_effort?: string | null;
  effort?: string | null;
}

function botRoleHTML(role: string): string {
  if (role.length <= 220) return esc(role);
  const end = role.lastIndexOf(' ', 220);
  return `<details class="bot-role"><summary>${esc(role.slice(0, end > 120 ? end : 220))}… <span class="muted bot-role-more">Show more</span><span class="muted bot-role-less">Show less</span></summary><div>${esc(role)}</div></details>`;
}
async function botSettingsMount(slug: string): Promise<void> {
  const host = $('#bot-editor');
  if (!host) return;
  await loadSettings();
  if (BOT?.slug !== slug || !host.isConnected) return;
  const bot = botRow(slug);
  if (!bot || !settingsCanManageBot(bot)) {
    host.innerHTML = '<p class="muted">You have read-only access to this bot. Its owner or an admin can change its settings.</p>';
  } else settingsEditBot(slug, true);
  botScreenSettingsPaint(slug);
  void botBuiltinCardsMount(slug);
}
function botInstructionsMount(slug: string): void {
  const host = $('#bot-instructions-editor'), bot = botRow(slug);
  if (!host || !bot || bot.shared_from || !settingsCanManageBot(bot)) return;
  host.innerHTML = `<form class="bot-instructions-form">
    <label for="bot-instructions-changes">Request instruction changes</label>
    <textarea id="bot-instructions-changes" name="changes" required rows="6" aria-label="Instructions changes" placeholder="Describe what should change in this bot’s instructions"></textarea>
    <p class="muted">Sent through your Assistant: the changes are made to AGENT.md on the bot’s computer and the file is checked.</p>
    <div class="row"><button class="primary" type="submit">Send changes</button><button class="ghost" type="reset">Cancel</button><span role="status" data-instructions-status></span></div>
  </form>`;
  const form = host.querySelector('form'), status = host.querySelector('[data-instructions-status]');
  form.onreset = () => { status.textContent = ''; };
  form.onsubmit = async (event: Event) => {
    event.preventDefault();
    const changes = form.elements.changes.value.trim(); if (!changes) return;
    const button = form.querySelector('[type=submit]');
    button.disabled = true; status.textContent = 'Sending…';
    try {
      await post('/v2/assistant/messages', {text: `Update the Instructions (AGENT.md) for ${bot.display_name} [${botSlug(slug)}]. Apply these changes and verify the file on its computer:\n\n${changes}`});
      form.reset(); status.textContent = 'Changes sent.';
    } catch (error) { status.textContent = errorMessage(error, ''); }
    finally { button.disabled = false; }
  };
}
async function botSettingsHistoryLoad(slug: string): Promise<void> {
  // The server answers only the owner and the people who manage this bot; the card is drawn for those alone (bot-page.ts).
  const host = $('#bot-settings-history'); if (!host) return;
  try {
    // The server keeps the page to this bot, so the newest changes on other bots cannot push its own out of it.
    const history = await get(`/v2/settings/history?bot=${encodeURIComponent(slug)}`);
    if (BOT?.slug !== slug || !host.isConnected) return;
    renderSettingsHistory(host, history, before => settingsHistoryOlder(before, slug));
  } catch (error) { if (host.isConnected) host.textContent = errorMessage(error, ''); }
}

function botInfoRefresh(slug: string): void {
  const bot = botRow(slug), host = $('#bot-setup-card');
  if (!bot || !host || botLimited(bot)) return;
  const title = $('#bot-top h1');
  if (title) title.innerHTML = `${shownName(bot)}${botBetaPill(bot)}`;
  const boss = botRow(bot.reports_to);
  const fallback = bot.fallback as BotDetailsFallback | null | undefined;
  const values: Record<string, string | null> = {
    Role: botRoleHTML(String(bot.role || bot.description || '').trim()),
    Humans: userChips(bot) || '<span class="muted">Nobody assigned</span>',
    Computer: cloudMachineOn(bot) ? cloudMachineComputerHTML(bot) : bot.machine ? `${esc(bot.machine.label || bot.machine.runner_id)} <span class="muted">· ${bot.online ? 'Online' : 'Offline'}</span>` : '<span class="muted">Not assigned</span>',
    Harness: esc(harnessWords(bot.harness || bot.runtime || bot.resolved_runtime) || 'Team default'),
    Model: !bot.agent ? esc(bot.model ? settingsChoiceLabel(bot.harness || bot.runtime, bot.model, bot.reasoning_effort || bot.effort) : settingsDefaultLabel(bot))
      + (fallback ? `<span class="muted"> · fallback ${esc(settingsChoiceLabel(fallback.harness, fallback.model, fallback.reasoning_effort || fallback.effort))}</span>` : '') : null,
    'Reports to': boss ? `<a href="#/bot/${encodeURIComponent(boss.id)}">${esc(boss.display_name)}</a>` : bot.reports_to?.startsWith('human:') ? esc(botPersonName(bot.reports_to.slice(6))) : '<span class="muted">Top level</span>',
    Repository: botRepoHTML(bot).replace(/^Repository /, ''),
  };
  if (bot.reports_to && ![...host.querySelectorAll('dt')].some(label => label.textContent === 'Reports to'))
    host.querySelector('dl').insertAdjacentHTML('beforeend', `<dt>Reports to</dt><dd>${values['Reports to']}</dd>`);
  for (const label of host.querySelectorAll('dt') as NodeListOf<HTMLElement>) {
    const name = String(label.textContent);
    const html = values[name];
    if (html == null) continue;
    const value = label.nextElementSibling;
    if (!value) throw new Error(`The bot details row ${name} has no value beside it`);
    if (value.innerHTML !== html) value.innerHTML = html;
  }
}
