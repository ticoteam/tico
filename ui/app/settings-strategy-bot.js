/* ui/app/settings-strategy-bot.js — Settings > System > Strategy Bot: "Regular checks" and "How much your Strategy Bot does"
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Regular checks on or off (the KPI pass and the automatic goal colours), then three levels (backend/strategy_bot.py LEVELS),
// each with its one sentence of what it will and won't do. Picking a level shows that sentence again with a Confirm
// before anything changes. Where the level is only the default (`level_chosen` false), the owner can confirm it too,
// so a later default never moves them. It is a helper's settings, beside the Task Manager's: nobody chats with it, the
// Assistant hands it the work. Only the owner sees the card; the server refuses everyone else, admins and every bot
// included. Its name, pause and budget are its bot settings (Settings > Bots, its page's Info).
let STRATEGY_BOT_PICK = null;
const settingsStrategyBotShown = () => S.me?.role === 'owner';
async function renderSettingsStrategyBot() {
  const host = $('#set-strategy-bot');
  if (!host || formBusy(host)) return;
  const v = await v2Get('/v2/strategy-bot');
  const name = esc(v?.name || strategyBotName());
  if (!Array.isArray(v?.levels)) { host.innerHTML = `<div class="empty">Couldn't load the ${name}'s settings. Try again in a moment.</div>`; return; }
  const pick = v.levels.find(lv => lv.level === STRATEGY_BOT_PICK && (lv.level !== v.level || !v.level_chosen));
  const keep = pick?.level === v.level;
  const now = v.levels.find(lv => lv.level === v.level);
  const last = v.level_changed_at ? `${esc(now?.name || 'Level ' + v.level)} since ${esc(fmt(v.level_changed_at))}, chosen by ${esc(v.level_changed_by_name || v.level_changed_by)}.`
    : `${esc(now?.name || 'Level ' + v.level)}, where every company starts.`;
  host.innerHTML = `<h3>Regular checks</h3>
    <label class="row strategy-checks"><input type="checkbox" data-strategy-checks${v.regular_checks ? ' checked' : ''}>
      <span>KPI readings and the automatic goal colours. Off, neither runs until you turn them back on; your Assistant can still ask the ${name}.</span></label>
    <h3>How much your ${name} does</h3>
    <p>${last} Whatever the level, spending, publishing, people and changes to your goals or vision stay yours.</p>
    <div class="strategy-levels" role="radiogroup" aria-label="How much your ${name} does">${v.levels.map(lv => `
      <button type="button" class="strategy-level${lv.level === v.level ? ' cur' : ''}" role="radio" aria-checked="${lv.level === (pick?.level ?? v.level)}" data-strategy-level="${lv.level}">
        <strong>${esc(lv.name)}${lv.level === v.level ? ' <span class="pill">Now</span>' : ''}</strong>
        <span>${esc(lv.sentence)}</span></button>`).join('')}</div>
    ${pick ? `<div class="strategy-confirm" role="alertdialog" aria-label="Confirm the change">
      <p><strong>${keep ? `Keep ${esc(pick.name)} as your choice?` : `Change to ${esc(pick.name)}?`}</strong> ${keep
        ? `It stays at ${esc(pick.name)} even if the level every company starts at changes.` : esc(pick.sentence)}</p>
      <div class="row"><button class="primary" type="button" data-strategy-confirm="${pick.level}">Confirm</button>
        <button class="ghost" type="button" data-strategy-cancel>Cancel</button></div></div>` : ''}
    <p class="settings-cell-note" data-strategy-status role="status"></p>
    ${v.level_history.length ? `<details><summary>Changes (${v.level_history.length})</summary>${v.level_history.map(h =>
      `<div class="settings-cell-note">${esc(fmt(h.at))} · ${esc(h.by_name || h.by)} · ${esc(strategyLevelName(v, h.was))} → ${esc(strategyLevelName(v, h.level))}</div>`).join('')}</details>` : ''}`;
  const checks = host.querySelector('[data-strategy-checks]');
  checks.onchange = async () => {
    checks.disabled = true;
    try {
      await post('/v2/strategy-bot/checks', {on: checks.checked});
      toast('Saved');
      void renderSettingsStrategyBot();
    } catch (e) {
      checks.checked = !checks.checked;
      checks.disabled = false;
      const status = host.querySelector('[data-strategy-status]');
      if (status) status.textContent = e.message;
    }
  };
  host.onclick = async event => {
    const choose = event.target.closest('[data-strategy-level]'), confirm = event.target.closest('[data-strategy-confirm]');
    if (choose) { STRATEGY_BOT_PICK = Number(choose.dataset.strategyLevel); void renderSettingsStrategyBot(); return; }
    if (event.target.closest('[data-strategy-cancel]')) { STRATEGY_BOT_PICK = null; void renderSettingsStrategyBot(); return; }
    if (!confirm) return;
    confirm.disabled = true;
    try {
      await post('/v2/strategy-bot/level', {level: Number(confirm.dataset.strategyConfirm)});
      STRATEGY_BOT_PICK = null;
      toast('Saved');
      void renderSettingsStrategyBot();
    } catch (e) {
      confirm.disabled = false;
      const status = host.querySelector('[data-strategy-status]');
      if (status) status.textContent = e.message;
    }
  };
}
const strategyLevelName = (v, level) => v.levels.find(lv => lv.level === level)?.name || 'Level ' + level;

// The Strategy Bot log on its page (backend/strategy_acts.py): each decision it made alone at level 3 and each thing it proposed
// because it stays the owner's, newest first, with the reason. Only the owner reads it (the page shows it to them
// alone) and only the owner sees Undo, which asks once before it puts the tasks back. What Undo couldn't restore stays
// on the entry in plain words. The entries loaded so far, every page of them, are kept on the host: asking to Undo an
// older entry, cancelling, or the Undo itself redraws them in place instead of going back to the first page.
let STRATEGY_LOG_UNDO = null;
const STRATEGY_LOG_STATE = {done: '', proposed: 'Proposed to you', undone: 'Undone'};
const strategyBotLogShown = () => S.me?.role === 'owner';
async function renderStrategyBotLog(host, more) {
  if (!host) return;
  const page = await v2Get('/v2/strategy-bot/log' + (more ? '?before=' + encodeURIComponent(more) : ''));
  if (!page) { host.innerHTML = `<div class="empty">Couldn't load the ${esc(strategyBotName())} log. Try again in a moment.</div>`; return; }
  host._strategyLog = (more ? host._strategyLog || [] : []).concat(page.items);
  host._strategyLogNext = page.next_before || null;
  host._strategyCanUndo = !!page.can_undo;
  drawStrategyBotLog(host);
}
function drawStrategyBotLog(host) {
  const name = esc(strategyBotName()), items = host._strategyLog || [];
  if (!items.length) { host.innerHTML = `<div class="empty">Nothing yet. What the ${name} decides on its own, at "Acts on its own", shows here with an Undo.</div>`; return; }
  const lines = x => [...x.done.map(d => esc(d.text)), ...x.left.map(l => `${esc(l.text)} <span class="muted">· not done: ${esc(l.why)}</span>`)];
  host.innerHTML = `<ul class="strategy-log">${items.map(x => `<li class="strategy-log-entry" data-strategy-log="${esc(x.id)}">
      <div class="row"><strong>${esc(x.reason || (x.kind === 'decision' ? 'A decision' : 'Stays yours'))}</strong>
        ${STRATEGY_LOG_STATE[x.state] ? `<span class="pill">${esc(STRATEGY_LOG_STATE[x.state])}</span>` : ''}
        <span class="muted">${esc(fmt(x.created_at))}</span>
        ${host._strategyCanUndo && x.undoable ? `<button class="linkish" type="button" data-strategy-undo="${esc(x.id)}">Undo</button>` : ''}</div>
      <ul>${lines(x).map(l => `<li>${l}</li>`).join('')}</ul>
      ${x.note ? `<p class="settings-cell-note">${esc(x.note)}</p>` : ''}
      ${x.proposal_task ? `<p class="settings-cell-note"><a href="#/task/${esc(x.proposal_task)}">In your Needs you</a></p>` : ''}
      ${x.undo ? `<p class="settings-cell-note">${esc(x.undo.text)}</p>` : ''}
      ${STRATEGY_LOG_UNDO === x.id ? `<div class="strategy-confirm" role="alertdialog" aria-label="Confirm the undo">
        <p><strong>Undo this decision?</strong> The tasks it changed go back as they were, unless someone changed them since; their bots are told.</p>
        <div class="row"><button class="primary" type="button" data-strategy-undo-confirm="${esc(x.id)}">Undo</button>
          <button class="ghost" type="button" data-strategy-undo-cancel>Cancel</button></div></div>` : ''}</li>`).join('')}</ul>
    ${host._strategyLogNext ? `<button class="linkish" type="button" data-strategy-log-more="${esc(host._strategyLogNext)}">Older</button>` : ''}
    <p class="settings-cell-note" data-strategy-log-status role="status"></p>`;
  host.onclick = async event => {
    const ask = event.target.closest('[data-strategy-undo]'), confirm = event.target.closest('[data-strategy-undo-confirm]');
    const older = event.target.closest('[data-strategy-log-more]');
    if (older) { void renderStrategyBotLog(host, older.dataset.strategyLogMore); return; }
    if (ask) { STRATEGY_LOG_UNDO = ask.dataset.strategyUndo; drawStrategyBotLog(host); return; }
    if (event.target.closest('[data-strategy-undo-cancel]')) { STRATEGY_LOG_UNDO = null; drawStrategyBotLog(host); return; }
    if (!confirm) return;
    confirm.disabled = true;
    try {
      const done = await post(`/v2/strategy-bot/log/${encodeURIComponent(confirm.dataset.strategyUndoConfirm)}/undo`, {});
      STRATEGY_LOG_UNDO = null;
      toast(done?.undo?.kept?.length ? 'Undone, except what changed since' : 'Undone');
      // The undone entry is replaced where it is, so an older page stays loaded.
      if (done?.id) host._strategyLog = host._strategyLog.map(x => x.id === done.id ? done : x);
      drawStrategyBotLog(host);
    } catch (e) {
      confirm.disabled = false;
      const status = host.querySelector('[data-strategy-log-status]');
      if (status) status.textContent = e.message;
    }
  };
}
