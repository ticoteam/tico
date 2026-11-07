/* ui/app/settings-hosted.ts — Settings > Computers: the company's hosted servers (its Tico server and bot computers)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// What GET /v2/hosted-servers answers: the company's servers when state is ok.
interface HostedServer { server_id: string; kind: string; power: string; }
interface HostedServersView { state: string; servers?: HostedServer[]; }
// What POST /v2/hosted-servers/:id/start answers: started, paused (with the page to add credit), or why not.
interface HostedStartResult { state: string; message?: string; credit_url?: string | null; }
// The sleep-when-idle setting of a hosted server, from GET /v2/sleep (backend/sleep_idle.py).
interface SleepState {
  hosted?: boolean; managed?: boolean; hours?: number | null; blocker?: string | null; idle_since?: string | null;
  wake_at?: string | null; runners?: boolean; last_error?: string | null;
}

// Owners and admins only (backend/company_billing.py asks Tico HQ with the install's gateway key). Shown only when HQ
// hosts this company's servers. Start is HQ's decision: while the company is paused for credit it refuses, and the row
// says so with a link to the account page where the company asks for more credit (paused-start, d8f8efaa).
const HOSTED_KINDS: Record<string, string> = {tico: 'Tico server', bot_computer: 'Bot computer'};
const HOSTED_START: Record<string, string> = {
  deactivated: 'Tico has shut this company down. Talk to us to start it again.',
  not_found: 'Tico HQ does not know this server any more. Reload to see your servers.',
  not_ready: 'This server is still being made or ended. Try again in a few minutes.',
  refused: 'Tico HQ did not accept this install’s gateway key.',
  no_gateway: 'This install is not connected to Tico AI Gateway.',
  unreachable: 'Tico HQ did not answer. Try again in a minute.',
};
// Settings reloads often: HQ's answer is kept a minute, a refusal on screen is never redrawn away, and a read that
// failed (no gateway key yet, a refused key, HQ down) is asked again at the next reload, so a fixed key shows it.
const HOSTED_KEEP_MS = 60_000;
async function renderSettingsHosted(again = false): Promise<void> {
  const card = document.querySelector<HTMLElement>('#settings-hosted'), host = document.querySelector<HTMLElement>('#set-hosted');
  if (!card || !host) return;
  const shown = host.querySelector('[data-hosted-note]:not([hidden])'), at = Number(host.dataset.readAt || 0);
  if (!again && (shown || Date.now() - at < HOSTED_KEEP_MS)) return;
  host.dataset.readAt = String(Date.now());
  let v: HostedServersView;
  try { v = await get('/v2/hosted-servers'); } catch { card.hidden = true; delete host.dataset.readAt; return; }
  if (!['ok', 'deactivated', 'none'].includes(v.state)) delete host.dataset.readAt;
  if (v.state === 'deactivated') {
    card.hidden = false;
    host.innerHTML = `<p class="muted">${esc(HOSTED_START.deactivated)}</p>`;
    return;
  }
  card.hidden = v.state !== 'ok';
  if (card.hidden) return;
  host.innerHTML = `<table class="hosted-table"><tbody>${v.servers!.map(s => `<tr data-hosted="${esc(s.server_id)}">
      <td>${esc(HOSTED_KINDS[s.kind] || s.kind)}</td>
      <td><span class="hosted-power" data-power="${esc(s.power)}">${s.power === 'on' ? 'On' : 'Stopped'}</span></td>
      <td class="hosted-actions">${s.power === 'on' ? '' : `<button class="ghost" type="button" data-hosted-start="${esc(s.server_id)}">Start</button>`}</td>
    </tr><tr class="hosted-note" data-hosted-note="${esc(s.server_id)}" hidden><td colspan="3"></td></tr>`).join('')}</tbody></table>`;
  host.querySelectorAll<HTMLButtonElement>('[data-hosted-start]').forEach(button => { button.onclick = () => void settingsHostedStart(button); });
}
async function settingsHostedStart(button: HTMLButtonElement): Promise<void> {
  const id = button.dataset.hostedStart ?? '', note = document.querySelector<HTMLElement>(`[data-hosted-note="${CSS.escape(id)}"]`), cell = note?.firstElementChild;
  button.disabled = true; button.textContent = 'Starting…';
  const say = (html: string): void => { if (!note) return; note.hidden = false; cell!.innerHTML = html; };
  try {
    const r: HostedStartResult = await post(`/v2/hosted-servers/${encodeURIComponent(id)}/start`);
    if (r.state === 'started') { await renderSettingsHosted(true); return; }
    if (r.state === 'paused') {
      say(`<span class="hosted-paused" role="alert">${esc(r.message)}</span>${r.credit_url
        ? ` <a class="link" href="${esc(r.credit_url)}" target="_blank" rel="noopener noreferrer" data-hosted-credit>Contact support for more credit</a>` : ''}`);
    } else say(`<span class="muted" role="alert">${esc(HOSTED_START[r.state] || HOSTED_START.unreachable)}</span>`);
  } catch (e) { say(`<span class="err" role="alert">${esc(errorMessage(e, ''))}</span>`); }
  button.disabled = false; button.textContent = 'Start';
}

// Sleep when idle (backend/sleep_idle.py): a server Tico hosts can stop itself after hours with nothing to do, and its
// bot computers with it; Tico starts it again for the next routine or a visit. Shown only on a hosted server, and not
// on a Tico Cloud cell (`managed`: Tico staff set its idle time). Anyone sees it; an owner or an admin changes it
// (the server says so too).
const SLEEP_BLOCKERS: Record<string, string> = {run_queued: 'a run is waiting', run_in_progress: 'a run is going', task_session: 'a task session is waiting',
  runner_job: 'a computer job is running', send_pending: 'a send is waiting', routine_due: 'a routine is due soon'};
async function renderSettingsSleep(): Promise<void> {
  const card = document.querySelector<HTMLElement>('#settings-sleep'), host = document.querySelector<HTMLElement>('#set-sleep');
  if (!card || !host || formBusy(host)) return;
  let v: SleepState;
  try { v = (await get('/v2/sleep')).sleep; } catch { card.hidden = true; return; }
  card.hidden = !v?.hosted || !!v.managed;
  if (card.hidden) return;
  const admin = settingsIsAdmin(), off = admin ? '' : ' disabled';
  const hours: [string, string][] = [['0', 'Off'], ...Array.from({length: 24}, (_, i): [string, string] => [String(i + 1), `After ${i + 1} h`])];
  const status = !v.hours ? '' : v.blocker ? `Awake: ${esc(SLEEP_BLOCKERS[v.blocker] || v.blocker)}`
    : v.idle_since ? `Quiet since ${esc(fmt(v.idle_since))}` : '';
  const line = [status, v.wake_at && `Next wake ${esc(fmt(v.wake_at))}`].filter(Boolean).join(' · ');
  host.innerHTML = `<div class="sleep-row">
      <select class="settings-inline-select" data-sleep-hours aria-label="Sleep when idle"${off}>${hours.map(([k, w]) => `<option value="${k}"${+k === v.hours ? ' selected' : ''}>${w}</option>`).join('')}</select>
      <label class="sysr-switch">${peopleSwitch('data-sleep-runners', v.runners, !admin, 'Stop bot computers too')}<span>Stop bot computers too</span></label>
    </div>
    ${line ? `<p class="settings-cell-note" data-sleep-status>${line}</p>` : ''}
    ${v.last_error ? `<p class="settings-cell-note err" data-sleep-error>Last stop failed: ${esc(v.last_error)}</p>` : ''}
    <p class="settings-cell-note err" role="status" data-sleep-note hidden></p>
    ${admin ? '' : settingsWhoCanHTML('this', {admins: true})}`;
  if (!admin) return;
  const save = async (body: Record<string, number | boolean>, input: HTMLSelectElement | HTMLInputElement): Promise<void> => {
    // The status line is drawn above in this function, so the lookup finds it.
    const note = host.querySelector<HTMLElement>('[data-sleep-note]')!;
    input.disabled = true; note.hidden = true;
    try { await post('/v2/sleep', body); toast('Saved'); await renderSettingsSleep(); }
    catch (e) { note.textContent = errorMessage(e, ''); note.hidden = false; input.disabled = false; }
  };
  const pick = host.querySelector<HTMLSelectElement>('[data-sleep-hours]')!, runners = host.querySelector<HTMLInputElement>('[data-sleep-runners]')!;
  pick.onchange = () => void save({hours: Number(pick.value)}, pick);
  runners.onchange = () => void save({runners: runners.checked}, runners);
}
