/* ui/app/settings-ceo-bot.js — Settings > CEO Bot: "Regular checks" and "How much your CEO Bot does"
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Regular checks on or off (the KPI pass and the automatic goal colours), then three levels (backend/ceo_bot.py LEVELS),
// each with its one sentence of what it will and won't do. Picking a level shows that sentence again with a Confirm
// before anything changes. Only the owner sees the tab; the server refuses everyone else, admins and every bot
// included. Its name is the bot's own Name field (Settings > Bots), the owner's too.
let CEO_BOT_PICK = null;
const settingsCeoBotShown = () => S.me?.role === 'owner';
async function renderSettingsCeoBot() {
  const host = $('#set-ceo-bot');
  if (!host || formBusy(host)) return;
  const v = await v2Get('/v2/ceo-bot');
  const name = esc(v?.name || ceoBotName());
  if (!v) { host.innerHTML = `<div class="empty">Couldn't load the ${name}'s settings. Try again in a moment.</div>`; return; }
  const pick = v.levels.find(lv => lv.level === CEO_BOT_PICK && lv.level !== v.level);
  const now = v.levels.find(lv => lv.level === v.level);
  const last = v.level_changed_at ? `${esc(now?.name || 'Level ' + v.level)} since ${esc(fmt(v.level_changed_at))}, chosen by ${esc(v.level_changed_by_name || v.level_changed_by)}.`
    : `${esc(now?.name || 'Level ' + v.level)}, where every company starts.`;
  host.innerHTML = `<h3>Regular checks</h3>
    <label class="row ceo-checks"><input type="checkbox" data-ceo-checks${v.regular_checks ? ' checked' : ''}>
      <span>KPI readings and the automatic goal colours. Off, neither runs until you turn them back on; you can still talk to the ${name}.</span></label>
    <h3>How much your ${name} does</h3>
    <p>${last} Whatever the level, spending, publishing, people and changes to your goals or vision stay yours.</p>
    <div class="ceo-levels" role="radiogroup" aria-label="How much your ${name} does">${v.levels.map(lv => `
      <button type="button" class="ceo-level${lv.level === v.level ? ' cur' : ''}" role="radio" aria-checked="${lv.level === (pick?.level ?? v.level)}" data-ceo-level="${lv.level}">
        <strong>${esc(lv.name)}${lv.level === v.level ? ' <span class="pill">Now</span>' : ''}</strong>
        <span>${esc(lv.sentence)}</span></button>`).join('')}</div>
    ${pick ? `<div class="ceo-confirm" role="alertdialog" aria-label="Confirm the change">
      <p><strong>Change to ${esc(pick.name)}?</strong> ${esc(pick.sentence)}</p>
      <div class="row"><button class="primary" type="button" data-ceo-confirm="${pick.level}">Confirm</button>
        <button class="ghost" type="button" data-ceo-cancel>Cancel</button></div></div>` : ''}
    <p class="settings-cell-note" data-ceo-status role="status"></p>
    ${v.level_history.length ? `<details><summary>Changes (${v.level_history.length})</summary>${v.level_history.map(h =>
      `<div class="settings-cell-note">${esc(fmt(h.at))} · ${esc(h.by_name || h.by)} · ${esc(ceoLevelName(v, h.was))} → ${esc(ceoLevelName(v, h.level))}</div>`).join('')}</details>` : ''}`;
  const checks = host.querySelector('[data-ceo-checks]');
  checks.onchange = async () => {
    checks.disabled = true;
    try {
      await post('/v2/ceo-bot/checks', {on: checks.checked});
      toast('Saved');
      void renderSettingsCeoBot();
    } catch (e) {
      checks.checked = !checks.checked;
      checks.disabled = false;
      const status = host.querySelector('[data-ceo-status]');
      if (status) status.textContent = e.message;
    }
  };
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
const ceoLevelName = (v, level) => v.levels.find(lv => lv.level === level)?.name || 'Level ' + level;

// The CEO Bot log on its page (backend/ceo_acts.py): each decision it made alone at level 3 and each thing it proposed
// because it stays the owner's, newest first, with the reason. Only the owner reads it (the page shows it to them
// alone) and only the owner sees Undo, which asks once before it puts the tasks back. What Undo couldn't restore stays
// on the entry in plain words. The entries loaded so far, every page of them, are kept on the host: asking to Undo an
// older entry, cancelling, or the Undo itself redraws them in place instead of going back to the first page.
let CEO_LOG_UNDO = null;
const CEO_LOG_STATE = {done: '', proposed: 'Proposed to you', undone: 'Undone'};
const ceoBotLogShown = () => S.me?.role === 'owner';
async function renderCeoBotLog(host, more) {
  if (!host) return;
  const page = await v2Get('/v2/ceo-bot/log' + (more ? '?before=' + encodeURIComponent(more) : ''));
  if (!page) { host.innerHTML = `<div class="empty">Couldn't load the ${esc(ceoBotName())} log. Try again in a moment.</div>`; return; }
  host._ceoLog = (more ? host._ceoLog || [] : []).concat(page.items);
  host._ceoLogNext = page.next_before || null;
  host._ceoCanUndo = !!page.can_undo;
  drawCeoBotLog(host);
}
function drawCeoBotLog(host) {
  const name = esc(ceoBotName()), items = host._ceoLog || [];
  if (!items.length) { host.innerHTML = `<div class="empty">Nothing yet. What the ${name} decides on its own, at "Acts on its own", shows here with an Undo.</div>`; return; }
  const lines = x => [...x.done.map(d => esc(d.text)), ...x.left.map(l => `${esc(l.text)} <span class="muted">· not done: ${esc(l.why)}</span>`)];
  host.innerHTML = `<ul class="ceo-log">${items.map(x => `<li class="ceo-log-entry" data-ceo-log="${esc(x.id)}">
      <div class="row"><strong>${esc(x.reason || (x.kind === 'decision' ? 'A decision' : 'Stays yours'))}</strong>
        ${CEO_LOG_STATE[x.state] ? `<span class="pill">${esc(CEO_LOG_STATE[x.state])}</span>` : ''}
        <span class="muted">${esc(fmt(x.created))}</span>
        ${host._ceoCanUndo && x.undoable ? `<button class="linkish" type="button" data-ceo-undo="${esc(x.id)}">Undo</button>` : ''}</div>
      <ul>${lines(x).map(l => `<li>${l}</li>`).join('')}</ul>
      ${x.note ? `<p class="settings-cell-note">${esc(x.note)}</p>` : ''}
      ${x.proposal_task ? `<p class="settings-cell-note"><a href="#/task/${esc(x.proposal_task)}">In your Needs you</a></p>` : ''}
      ${x.undo ? `<p class="settings-cell-note">${esc(x.undo.text)}</p>` : ''}
      ${CEO_LOG_UNDO === x.id ? `<div class="ceo-confirm" role="alertdialog" aria-label="Confirm the undo">
        <p><strong>Undo this decision?</strong> The tasks it changed go back as they were, unless someone changed them since; their bots are told.</p>
        <div class="row"><button class="primary" type="button" data-ceo-undo-confirm="${esc(x.id)}">Undo</button>
          <button class="ghost" type="button" data-ceo-undo-cancel>Cancel</button></div></div>` : ''}</li>`).join('')}</ul>
    ${host._ceoLogNext ? `<button class="linkish" type="button" data-ceo-log-more="${esc(host._ceoLogNext)}">Older</button>` : ''}
    <p class="settings-cell-note" data-ceo-log-status role="status"></p>`;
  host.onclick = async event => {
    const ask = event.target.closest('[data-ceo-undo]'), confirm = event.target.closest('[data-ceo-undo-confirm]');
    const older = event.target.closest('[data-ceo-log-more]');
    if (older) { void renderCeoBotLog(host, older.dataset.ceoLogMore); return; }
    if (ask) { CEO_LOG_UNDO = ask.dataset.ceoUndo; drawCeoBotLog(host); return; }
    if (event.target.closest('[data-ceo-undo-cancel]')) { CEO_LOG_UNDO = null; drawCeoBotLog(host); return; }
    if (!confirm) return;
    confirm.disabled = true;
    try {
      const done = await post(`/v2/ceo-bot/log/${encodeURIComponent(confirm.dataset.ceoUndoConfirm)}/undo`, {});
      CEO_LOG_UNDO = null;
      toast(done?.undo?.kept?.length ? 'Undone, except what changed since' : 'Undone');
      // The undone entry is replaced where it is, so an older page stays loaded.
      if (done?.id) host._ceoLog = host._ceoLog.map(x => x.id === done.id ? done : x);
      drawCeoBotLog(host);
    } catch (e) {
      confirm.disabled = false;
      const status = host.querySelector('[data-ceo-log-status]');
      if (status) status.textContent = e.message;
    }
  };
}
