/* Bot details: summary, inline settings and instruction requests, and settings history. */
'use strict';

function botRoleHTML(role) {
  if (role.length <= 220) return esc(role);
  const end = role.lastIndexOf(' ', 220);
  return `<details class="bot-role"><summary>${esc(role.slice(0, end > 120 ? end : 220))}… <span class="muted bot-role-more">Show more</span><span class="muted bot-role-less">Show less</span></summary><div>${esc(role)}</div></details>`;
}
async function botSettingsMount(slug) {
  const host = $('#bot-editor');
  if (!host) return;
  await loadSettings();
  if (BOT?.slug !== slug || !host.isConnected) return;
  const bot = S.emps.find(e => e.name === slug);
  if (!bot || !settingsCanManageBot(bot)) {
    host.innerHTML = '<p class="muted">You have read-only access to this bot. Its owner or an admin can change its settings.</p>';
  } else settingsEditBot(slug, true);
  botScreenSettingsPaint(slug);
}
function botInstructionsMount(slug) {
  const host = $('#bot-instructions-editor'), bot = S.emps.find(e => e.name === slug);
  if (!host || !bot || bot.shared_from || !settingsCanManageBot(bot)) return;
  host.innerHTML = `<form class="bot-instructions-form">
    <label for="bot-instructions-changes">Request instruction changes</label>
    <textarea id="bot-instructions-changes" name="changes" required rows="6" aria-label="Instructions changes" placeholder="Describe what should change in this bot’s instructions"></textarea>
    <p class="muted">BotOps applies your changes to AGENT.md on the bot’s computer and verifies the file.</p>
    <div class="row"><button class="primary" type="submit">Send changes to BotOps</button><button class="ghost" type="reset">Cancel</button><span role="status" data-instructions-status></span></div>
  </form>`;
  const form = host.querySelector('form'), status = host.querySelector('[data-instructions-status]');
  form.onreset = () => { status.textContent = ''; };
  form.onsubmit = async event => {
    event.preventDefault();
    const changes = form.elements.changes.value.trim(); if (!changes) return;
    const button = form.querySelector('[type=submit]');
    button.disabled = true; status.textContent = 'Sending…';
    try {
      await post('/v2/chat/botops', {text: `Update the Instructions (AGENT.md) for ${bot.display_name} [${slug}]. Apply these changes and verify the file on its computer:\n\n${changes}`});
      form.reset(); status.textContent = 'Changes sent to BotOps.';
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  };
}
async function botSettingsHistoryLoad(slug) {
  const host = $('#bot-settings-history'); if (!host || S.me?.role !== 'owner') return;
  try {
    const history = await get('/v2/settings/history');
    if (BOT?.slug !== slug || !host.isConnected) return;
    renderSettingsHistory(host, {changes: (history.changes || []).filter(c => c.bot === slug),
      transitions: (history.transitions || []).filter(t => t.bot === slug)});
  } catch (error) { if (host.isConnected) host.textContent = error.message; }
}

function botInfoRefresh(slug) {
  const bot = S.emps.find(e => e.name === slug), host = $('#bot-setup-card');
  if (!bot || !host || botLimited(bot)) return;
  const title = $('#bot-top h1');
  if (title) title.innerHTML = `${shownName(bot)}${runtimeTag(bot)}${botBetaPill(bot)}`;
  const boss = S.emps.find(e => e.name === bot.reports_to);
  const values = {
    Role: botRoleHTML(String(bot.role || bot.description || '').trim()),
    Humans: userChips(bot) || '<span class="muted">Nobody assigned</span>',
    Computer: bot.machine ? `${esc(bot.machine.label || bot.machine.runner_id)} <span class="muted">· ${bot.online ? 'Online' : 'Offline'}</span>` : '<span class="muted">Not assigned</span>',
    Harness: esc(harnessWords(bot.harness || bot.runtime || bot.resolved_runtime) || 'Team default'),
    Model: !bot.agent ? esc(bot.model ? settingsChoiceLabel(bot.harness || bot.runtime, bot.model, bot.reasoning_effort || bot.effort) : settingsDefaultLabel(bot))
      + (bot.fallback ? `<span class="muted"> · fallback ${esc(settingsChoiceLabel(bot.fallback.harness, bot.fallback.model, bot.fallback.reasoning_effort || bot.fallback.effort))}</span>` : '') : null,
    'Reports to': boss ? `<a href="#/bot/${encodeURIComponent(boss.name)}">${esc(boss.display_name)}</a>` : bot.reports_to?.startsWith('human:') ? esc(botPersonName(bot.reports_to.slice(6))) : '<span class="muted">Top level</span>',
    Repository: botRepoHTML(bot).replace(/^Repository /, ''),
  };
  if (bot.reports_to && ![...host.querySelectorAll('dt')].some(label => label.textContent === 'Reports to'))
    host.querySelector('dl').insertAdjacentHTML('beforeend', `<dt>Reports to</dt><dd>${values['Reports to']}</dd>`);
  for (const label of host.querySelectorAll('dt')) {
    const html = values[label.textContent];
    if (html != null && label.nextElementSibling.innerHTML !== html) label.nextElementSibling.innerHTML = html;
  }
}
