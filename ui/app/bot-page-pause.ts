/* ui/app/bot-page-pause.ts — The bot page's pause row on its Settings tab and the pause state behind the Paused badges (split out of ui/app/bot-page.ts).
   Classic script, loaded right after bot-page.ts: its globals are shared with the other files under ui/app/. */
'use strict';

// Pause is one row in the bot's Settings, not a strip on the page; the header's Paused badge says it at a glance.
// The control API remains the sole authority; failed writes never change the shown state.
function botPauseHTML(slug: string, state: BotPauseState | null | undefined, error = '') {
  const retry = '<button type="button" class="ghost small" data-bot-pause-retry>Retry</button>';
  if (!state) return error ? `<p class="err" role="alert">${esc(error)} ${retry}</p>` : '<p class="muted" role="status">Loading…</p>';
  const name = botDisplayName(slug);
  const paused = state.state === 'paused', record = state.paused_by;
  const stamp = record && Number.isFinite(Date.parse(record.at))
    ? `${new Date(record.at).toISOString().slice(0, 16).replace('T', ' ')} UTC` : '';
  const can = state.can_control && !state.company_paused && ['active', 'paused'].includes(state.state);
  const tasks = paused && state.open_tasks ? ` <a href="#/bot/${encodeURIComponent(slug)}/tasks">${state.open_tasks} open task${state.open_tasks === 1 ? ' is' : 's are'} waiting on it</a>` : '';
  return `<div class="bot-pause-row">
    <div class="bot-pause-copy"><div class="bot-pause-title${paused ? ' bot-pause-record' : ''}" role="status" aria-live="polite" aria-atomic="true">${paused ? (stamp ? `Paused by ${esc(record!.name)} on ${esc(stamp)}` : 'Paused') : 'Pause this bot'}</div>
      <p class="muted">${paused ? `${esc(name)} is paused: no tasks, messages or routines until it is unpaused.${tasks}${!state.can_control ? ` Ask ${esc(state.owner_id || 'its operator')} to unpause.` : ''}`
        : 'Stops new tasks, messages and routines; a turn in progress is interrupted.'}</p>
      ${state.company_paused ? '<p class="muted">Company is paused for billing. <a href="#/settings/billing">Add credit</a></p>' : ''}
      ${error ? `<p class="err" role="alert">${esc(error)} ${retry}</p>` : ''}</div>
    ${can ? `<button type="button" class="ghost" data-bot-pause-action="${paused ? 'resume' : 'pause'}">${paused ? 'Unpause' : 'Pause'}</button>` : ''}
  </div>`;
}
function botPauseDraw() {
  const host = $('#bot-control-host'); if (!host || !BOT) return;
  const focus = host.contains(document.activeElement);
  patchLiveHTML(host, botPauseHTML(BOT.slug, BOT.pauseState, BOT.pauseError));
  const button = host.querySelector('[data-bot-pause-action]');
  if (button) { button.disabled = !!BOT.pauseBusy; button.onclick = () => botPauseChange(BOT!, button.dataset.botPauseAction); }
  const retry = host.querySelector('[data-bot-pause-retry]');
  if (retry) { retry.disabled = !!BOT.pauseBusy; retry.onclick = async () => { const view = BOT; if (!view) return; const action = view.pauseRetry; await botPauseLoad(); if (action && BOT === view) await botPauseChange(view, action); }; }
  if (focus && !host.contains(document.activeElement)) (button || retry)?.focus();
}
// The header's and the tree's Paused badges read the roster, which only catches up at its next poll. The pause state
// (a fresh read or a finished control) is the authority, so the roster entry follows it as soon as it is known.
function botPauseFollow(view: BotPageView) {
  const emp = botRow(view.slug), paused = view.pauseState?.state === 'paused', badge = $('#bot-paused');
  if (emp && paused !== (emp.status === 'paused')) { emp.status = paused ? 'paused' : 'active'; renderTree(); }
  if (badge) patchLiveHTML(badge, botPausedBadge(view.slug));
}
async function botPauseLoad() {
  const view = BOT;
  if (!view || view.pauseLoading || view.pauseBusy) return;
  view.pauseLoading = true;
  const generation = view.pauseGeneration || 0;
  try {
    const value = await get<BotPauseState>(`/v2/bots/${encodeURIComponent(view.slug)}/pause-state`, {fresh: true});
    if (BOT !== view || view.pauseBusy || generation !== (view.pauseGeneration || 0)) return;
    view.pauseState = value;
    view.pauseLoadedAt = Date.now();
    if (!view.pauseRetry) view.pauseError = '';
    botPauseFollow(view);
  } catch (error) {
    if (BOT === view && generation === (view.pauseGeneration || 0) && !view.pauseState) view.pauseError = `Could not load bot state: ${errorMessage(error, '')}`;
  } finally {
    view.pauseLoading = false;
    if (BOT === view) {
      botPauseDraw();
      // The control may have finished while this older read waited on billing. Re-read its committed state.
      if (generation !== (view.pauseGeneration || 0) && !view.pauseBusy && !view.pauseRetry) void botPauseLoad();
    }
  }
}
async function botPauseChange(view: BotPageView, action: string) {
  if (BOT !== view || view.pauseBusy || !view.pauseState?.can_control || view.pauseState.company_paused) return;
  // Invalidate any refresh already in flight, even if it arrives after the control finishes.
  view.pauseGeneration = (view.pauseGeneration || 0) + 1;
  view.pauseBusy = true; view.pauseError = ''; view.pauseRetry = null; botPauseDraw();
  try {
    const result = await post(`/v2/bots/${encodeURIComponent(view.slug)}/control`, {action, expected_revision: view.pauseState.revision});
    if (BOT !== view) return;
    view.pauseState = {...view.pauseState, state: action === 'pause' ? 'paused' : 'active', revision: result.revision,
      paused_by: null};
    botPauseFollow(view);
    const name = botDisplayName(view.slug);
    toast(`${name} is ${action === 'pause' ? 'paused.' : 'unpaused'}`, false,
      action === 'pause' ? {label: 'Unpause', run: () => botPauseChange(view, 'resume')} : null);
  } catch (error) {
    if (BOT === view) { view.pauseError = `Could not ${action === 'pause' ? 'pause' : 'unpause'}: ${errorMessage(error, '')}`; view.pauseRetry = action; }
  } finally {
    view.pauseBusy = false;
    if (BOT === view) { botPauseDraw(); if (!view.pauseRetry) void botPauseLoad(); }
  }
}
function botPauseStale(view: BotPageView) {
  if (!view.pauseState || view.pauseLoadedAt == null) return true;
  if ((stateOf(view.slug) === 'paused') !== (view.pauseState.state === 'paused')) return true;
  return Date.now() - view.pauseLoadedAt >= PAUSE_STALE_MS;
}
