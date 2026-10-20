/* ui/app/settings-ceo-bot.js — Settings > CEO Bot: "How much your CEO Bot does"
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Three levels (backend/ceo_bot.py LEVELS), each with its one sentence of what it will and won't do. Picking one shows
// that sentence again with a Confirm before anything changes. Only the owner and admins see the tab, and only where
// the CEO Bot switch is on; the server refuses everyone else, every bot included.
let CEO_BOT_PICK = null;
const settingsCeoBotShown = () => settingsIsAdmin() && ceoBotOn();
async function renderSettingsCeoBot() {
  const host = $('#set-ceo-bot');
  if (!host || formBusy(host)) return;
  const v = await v2Get('/v2/ceo-bot');
  if (!v) { host.innerHTML = '<div class="empty">Couldn\'t load the CEO Bot\'s settings. Try again in a moment.</div>'; return; }
  if (!v.on) { host.innerHTML = '<div class="empty">The CEO Bot is off on this install.</div>'; return; }
  const pick = v.levels.find(lv => lv.level === CEO_BOT_PICK && lv.level !== v.level);
  const last = v.level_changed_at ? `Level ${v.level} since ${esc(fmt(v.level_changed_at))}, chosen by ${esc(v.level_changed_by_name || v.level_changed_by)}.`
    : `Level ${v.level}, where every company starts.`;
  host.innerHTML = `<p>${last} Whatever the level, spending, publishing, people and changes to your goals or vision stay yours.</p>
    <div class="ceo-levels" role="radiogroup" aria-label="How much your CEO Bot does">${v.levels.map(lv => `
      <button type="button" class="ceo-level${lv.level === v.level ? ' cur' : ''}" role="radio" aria-checked="${lv.level === (pick?.level ?? v.level)}" data-ceo-level="${lv.level}">
        <strong>${lv.level} · ${esc(lv.name)}${lv.level === v.level ? ' <span class="pill">Now</span>' : ''}</strong>
        <span>${esc(lv.sentence)}</span></button>`).join('')}</div>
    ${pick ? `<div class="ceo-confirm" role="alertdialog" aria-label="Confirm the change">
      <p><strong>Change to level ${pick.level}, ${esc(pick.name)}?</strong> ${esc(pick.sentence)}</p>
      <div class="row"><button class="primary" type="button" data-ceo-confirm="${pick.level}">Confirm</button>
        <button class="ghost" type="button" data-ceo-cancel>Cancel</button></div></div>` : ''}
    <p class="settings-cell-note" data-ceo-status role="status"></p>
    ${v.level_history.length ? `<details><summary>Changes (${v.level_history.length})</summary>${v.level_history.map(h =>
      `<div class="settings-cell-note">${esc(fmt(h.at))} · ${esc(h.by_name || h.by)} · level ${h.was} → ${h.level}</div>`).join('')}</details>` : ''}`;
  host.onclick = async event => {
    const choose = event.target.closest('[data-ceo-level]'), confirm = event.target.closest('[data-ceo-confirm]');
    if (choose) { CEO_BOT_PICK = Number(choose.dataset.ceoLevel); void renderSettingsCeoBot(); return; }
    if (event.target.closest('[data-ceo-cancel]')) { CEO_BOT_PICK = null; void renderSettingsCeoBot(); return; }
    if (!confirm) return;
    confirm.disabled = true;
    try {
      await post('/v2/ceo-bot/level', {level: Number(confirm.dataset.ceoConfirm)});
      CEO_BOT_PICK = null;
      toast('Saved');
      void renderSettingsCeoBot();
    } catch (e) {
      confirm.disabled = false;
      const status = host.querySelector('[data-ceo-status]');
      if (status) status.textContent = e.message;
    }
  };
}
