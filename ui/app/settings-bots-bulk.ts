/* ui/app/settings-bots-bulk.ts — Settings > Bots: Change model for the selected bots (bot model, task model or both), one bot at a time
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// How one bot (or one of its two models) came out of a bulk change: the row's state and the words beside it.
interface SettingsBulkOutcome { state: 'changed' | 'skipped' | 'blocked' | 'failed'; partial?: boolean; note: string; }
// The running tally the dialog's summary line reads; failed bots are kept for Retry failed.
interface SettingsBulkTally { changed: number; partial: number; skipped: number; blocked: number; failed: SettingsBot[]; }

function settingsBulkModelDialog() {
  const bots = [...SETTINGS_BOTS_VIEW.selected].map(slug => S.emps.find(e => e.id === slug)).filter((e): e is SettingsBot => !!e);
  if (!bots.length) return;
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal settings-bulk-dialog';
  dialog.setAttribute('aria-labelledby', 'bulk-model-title');
  let choice: SettingsChoice | null = null, target = 'bot', running = false, ran = false;
  // The window every selected bot (or its tasks) compacts at; null keeps each bot's own (spec scope 5).
  let compact: number | null = null;
  // A part paint() just drew (each is in the dialog's markup).
  const part = <T extends Element>(selector: string): T => dialog.querySelector<T>(selector)!;
  const paint = () => {
    const label = choice ? settingsChoiceLabel(choice.harness, choice.model, choice.effort) : '';
    dialog.innerHTML = `<div class="tmodal-head"><h2 id="bulk-model-title">Change model · ${bots.length} bot${bots.length === 1 ? '' : 's'}</h2><span class="spacer"></span><button class="ghost" type="button" data-bulk-close aria-label="Close">✕</button></div>
      <div class="transition-body">
        <label>Apply to<select data-bulk-target aria-label="Apply to"><option value="bot">Bot model</option><option value="task">Task model</option><option value="both">Both models</option></select></label>
        <div class="settings-choice" data-bot-choice="" data-kind="model" data-current="${esc(choice ? settingsChoiceValue(choice.harness, choice.model, choice.effort) : '')}">
          ${settingsChoiceFields(choice ? settingsChoiceValue(choice.harness, choice.model, choice.effort) : '')}</div>
        <label>Compact<select data-bulk-compact aria-label="Compact">${compactOptions(compact, choice ? settingsModel(choice.model)?.context_tokens : null, "Keep each bot's")}</select></label>
        <p data-bulk-destination>${choice ? `Destination: ${esc(label)}` : ''}</p>
        <div class="transition-progress" data-bulk-explanation></div>
        <ul class="settings-bulk-list" data-bulk-list>${bots.map(e => `<li data-bulk-bot="${esc(e.id)}" data-state="pending"><span>${esc(e.display_name || e.slug)}</span><span class="settings-cell-note">${esc(settingsChoiceLabel(settingsBotHarness(e), e.model, settingsBotEffort(e)))}</span></li>`).join('')}</ul>
        <p role="status" data-bulk-summary></p>
        <div class="transition-actions"><button class="primary" type="button" data-bulk-apply ${choice || compact ? '' : 'disabled'}>Change ${bots.length} bot${bots.length === 1 ? '' : 's'}</button><button class="ghost" type="button" data-bulk-close>Cancel</button></div></div>`;
    dialog.querySelectorAll<HTMLButtonElement>('[data-bulk-close]').forEach(button => { button.onclick = () => dialog.close(); });
    const preview = () => {
      part<HTMLElement>('[data-bulk-explanation]').innerHTML = target === 'task'
        ? '<strong>Only task models change.</strong><p>New task sessions use this choice. If unavailable, tasks use the bot model and say so.</p>'
        : `<strong>${target === 'both' ? 'Bot and task models change independently.' : 'Only bot models change; explicit task models stay as set.'}</strong><p>Each changed bot starts a fresh provider session. History is kept. A bot mid-turn is skipped; retry it after.${target === 'both' ? ' Task settings still save if the bot model is blocked or fails.' : ''}</p>`;
      bots.forEach(e => { const why = target !== 'task' && choice && settingsRuntimeBlock(e, choice.harness);
        mark(e.id, why ? 'blocked' : 'pending', why ? `Can't run bot model: ${why.reason}${target === 'both' ? ' · Task model will still be saved' : ''}` : 'Ready to apply'); });
    };
    const targetPick = part<HTMLSelectElement>('[data-bulk-target]');
    targetPick.onchange = () => { target = targetPick.value; preview(); };
    const compactPick = part<HTMLSelectElement>('[data-bulk-compact]');
    compactPick.onchange = () => { compact = Number(compactPick.value) || null; part<HTMLButtonElement>('[data-bulk-apply]').disabled = !choice && !compact; };
    settingsWireChoiceFields(part<HTMLElement>('[data-choice-fields]'), (value, valid) => {
      choice = valid ? settingsChoiceFromValue(value) : null;
      // Only the sizes the chosen model holds; a size it cannot hold drops to the largest that fits.
      const window = choice ? settingsModel(choice.model)?.context_tokens || 0 : 0;
      if (compact && window && compact > window) compact = COMPACT_SIZES.filter(([size]) => size <= window).pop()?.[0] || null;
      compactPick.innerHTML = compactOptions(compact, window || null, "Keep each bot's");
      part<HTMLButtonElement>('[data-bulk-apply]').disabled = !choice && !compact;
      part<HTMLElement>('[data-bulk-destination]').textContent = choice
        ? `Destination: ${settingsChoiceLabel(choice.harness, choice.model, choice.effort)}` : '';
      preview();
    });
    preview();
    part<HTMLButtonElement>('[data-bulk-apply]').onclick = () => void run(bots);
  };
  const mark = (slug: string, state: string, note: string) => {
    const row = dialog.querySelector<HTMLElement>(`[data-bulk-bot="${CSS.escape(slug)}"]`); if (!row) return;
    row.dataset.state = state; row.querySelector('.settings-cell-note')!.textContent = note;
  };
  const run = async (targets: SettingsBot[]) => {
    if (running || (!choice && !compact)) return;
    running = ran = true;
    const apply = dialog.querySelector<HTMLButtonElement>('[data-bulk-apply]');
    if (apply) apply.disabled = true;
    const retry = dialog.querySelector<HTMLButtonElement>('[data-bulk-retry]');
    if (retry) retry.disabled = true;
    dialog.querySelectorAll<HTMLSelectElement | HTMLButtonElement>('[data-bot-choice] select,[data-choice-apply],[data-bulk-target]').forEach(control => { control.disabled = true; });
    const results: SettingsBulkTally = {changed: 0, partial: 0, skipped: 0, blocked: 0, failed: []};
    for (const e of targets) {
      mark(e.id, 'working', 'Changing…');
      const result = await settingsBulkApplyOne(e.id, choice, target, compact);
      mark(e.id, result.state, result.note);
      if (result.partial) results.partial++;
      if (result.state === 'failed') results.failed.push(e); else results[result.state]++;
    }
    running = false;
    const summary = [results.changed || !results.partial ? `${results.changed} changed` : '',
      results.partial ? `${results.partial} partly changed` : '', results.skipped ? `${results.skipped} already on it` : '',
      results.blocked ? `${results.blocked} can't run it` : '',
      results.failed.length ? `${results.failed.length} failed` : ''].filter(Boolean).join(' · ');
    part<HTMLElement>('[data-bulk-summary]').textContent = summary;
    const actions = part<HTMLElement>('.transition-actions');
    actions.innerHTML = `${results.failed.length ? '<button class="ghost" type="button" data-bulk-retry>Retry failed</button>' : ''}<button class="primary" type="button" data-bulk-close>Done</button>`;
    actions.querySelector<HTMLButtonElement>('[data-bulk-close]')!.onclick = () => dialog.close();
    const retryFailed = actions.querySelector<HTMLButtonElement>('[data-bulk-retry]');
    if (retryFailed) retryFailed.onclick = () => void run(results.failed);
    await loadSettings();
  };
  // Cancel keeps the selection; once a change has run, the next bulk action starts fresh.
  dialog.onclose = () => { dialog.remove(); if (ran) { SETTINGS_BOTS_VIEW.selected.clear(); void loadSettings(); } };
  document.body.appendChild(dialog); paint(); dialog.showModal();
}
// Each destination uses its existing single-bot route. In Both mode, reload after the bot transition
// so the task-model request uses its new revision. Retry failed preserves already-applied destinations.
async function settingsBulkApplyOne(slug: string, choice: SettingsChoice | null, target = 'bot', compact: number | null = null): Promise<SettingsBulkOutcome> {
  const notes: string[] = [], kinds = target === 'both' ? ['bot', 'task'] : [target];
  let changed = false, failed = false, blocked = false;
  for (const kind of kinds) {
    const result = choice ? await settingsBulkApplyKind(slug, choice, kind) : {state: 'skipped', note: 'Model kept'} as SettingsBulkOutcome;
    if (compact && result.state !== 'failed' && result.state !== 'blocked') {
      // The window saves on its own, without a fresh session (spec AC-15), after any model change.
      const window = await settingsBulkCompact(slug, compact, kind);
      result.note = `${result.note} · ${window.note}`;
      if (window.state === 'failed') result.state = 'failed';
      else if (window.state === 'changed') result.state = 'changed';
    }
    notes.push(`${kind === 'task' ? 'Task model' : 'Bot model'}: ${result.note}`);
    failed ||= result.state === 'failed';
    blocked ||= result.state === 'blocked';
    changed ||= result.state === 'changed';
    if (kind === 'bot' && target === 'both' && result.state === 'changed') {
      try { S.emps = namedRoster(await get('/employees')); }
      // The task route can refresh a stale revision itself if this read fails.
      catch {}
    }
  }
  return {state: failed ? 'failed' : blocked ? 'blocked' : changed ? 'changed' : 'skipped',
    partial: changed && (failed || blocked), note: notes.join(' · ')};
}
// One bot's window (kind 'bot': POST /compact-at) or its tasks' (kind 'task': the task context length), from a fresh read.
async function settingsBulkCompact(slug: string, compact_at: number, kind: string): Promise<SettingsBulkOutcome> {
  try {
    S.emps = namedRoster(await get('/employees'));
    const e: SettingsBot | undefined = S.emps.find(row => row.id === slug);
    if (!e) return {state: 'failed', note: 'This bot can no longer be changed here'};
    if (kind === 'task') await post(`/v2/bots/${encodeURIComponent(slug)}/task-model`, {task_model: settingsTaskBody(e, {compact_at}), expected_revision: e.revision});
    else await post(`/v2/bots/${encodeURIComponent(slug)}/compact-at`, {compact_at, expected_revision: e.revision});
    return {state: 'changed', note: `compacts at ${compactWords(compact_at)}`};
  } catch (error) {
    if (errorCode(error) === 'unchanged') return {state: 'skipped', note: `already compacts at ${compactWords(compact_at)}`};
    return {state: 'failed', note: errorMessage(error, 'The window was not saved')};
  }
}
async function settingsBulkApplyKind(slug: string, choice: SettingsChoice, kind: string): Promise<SettingsBulkOutcome> {
  for (let attempt = 0; attempt < 2; attempt++) {
    const e: SettingsBot | undefined = S.emps.find(row => row.id === slug);
    if (!e || !settingsBotSelectable(e)) return {state: 'failed', note: 'This bot can no longer be changed here'};
    // Task settings save even when unavailable; the server returns its fallback note.
    const why = kind === 'bot' && settingsRuntimeBlock(e, choice.harness);
    if (why) return {state: 'blocked', note: `Can't run: ${why.reason}${why.move ? ` · move to ${why.move.label}` : ''}`};
    const selected = kind === 'task' ? e.task_model : e;
    if (selected && settingsBotHarness(selected) === choice.harness && selected.model === choice.model && settingsBotEffort(selected) === choice.effort)
      return {state: 'skipped', note: 'Already on this model'};
    try {
      if (kind === 'task') {
        // The task model and its effort change; the bot's own task context length stays.
        const saved: SettingsTaskSaved = await post(`/v2/bots/${encodeURIComponent(slug)}/task-model`, {
          task_model: settingsTaskBody(e, {choice, effort: choice.effort}), expected_revision: e.revision});
        return {state: 'changed', note: [`Now ${settingsChoiceLabel(choice.harness, choice.model, choice.effort)}`,
          saved.readiness?.can_run === false ? `For now tasks use the bot model: ${saved.readiness.problem}` : '', saved.warning || ''].filter(Boolean).join('. ')};
      }
      const t: {state: string} = await post(`/v2/bots/${encodeURIComponent(slug)}/transitions`, {kind: 'model', model: choice.model,
        effort: choice.effort, harness: choice.harness, expected_generation: e.machine?.generation || 0,
        expected_revision: e.revision});
      return t.state === 'applied' ? {state: 'changed', note: `Now ${settingsChoiceLabel(choice.harness, choice.model, choice.effort)}`}
        : {state: 'failed', note: `Change ${t.state}; open the bot's Model control to finish it`};
    } catch (error) {
      const code = errorCode(error);
      if (code === 'unchanged') return {state: 'skipped', note: 'Already on this model'};
      if (code === 'version_conflict' && attempt === 0) {
        try { S.emps = namedRoster(await get('/employees')); } catch {}
        continue;
      }
      return {state: 'failed', note: errorMessage(error, 'The change failed')};
    }
  }
  return {state: 'failed', note: 'Bot settings kept changing; refresh and try again'};
}
