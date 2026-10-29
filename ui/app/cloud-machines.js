/* ui/app/cloud-machines.js — A bot's own cloud machine on Tico Cloud: where it runs, its size, Always on, its state
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// `cloud_machine` on a bot's row (/api/employees, backend/cloud_machines.py), null off Tico Cloud, so nothing here
// shows there. `mode` cloud runs the bot on its own machine, started when it has work and stopped when idle; any other
// computer is a machine transition, as before. A size applies at the machine's next start. The sizes are HQ's
// (GET /v2/cloud-machines), read once; before they come the four names stand alone.
const CLOUD_SIZE_NAMES = ['small', 'default', 'large', 'xlarge'];
const CLOUD_STATES = {none: 'Not started yet', starting: 'Starting', running: 'Running', stopping: 'Stopping',
  stopped: 'Stopped', deleted: 'Deleted', failed: 'Failed'};
let CLOUD_SIZES = null;
const cloudSizeName = name => name ? name[0].toUpperCase() + name.slice(1) : '';
const cloudMachineOwn = e => e?.cloud_machine?.mode === 'cloud';
async function cloudMachineSizes() {
  if (!CLOUD_SIZES) {
    try { CLOUD_SIZES = (await get('/v2/cloud-machines')).sizes || []; } catch { return []; }
  }
  return CLOUD_SIZES;
}
function cloudSizeLabel(size) {
  if (!size.cpu) return cloudSizeName(size.name);
  return `${cloudSizeName(size.name)} · ${size.cpu} CPU · ${size.memory_gb} GB · $${Number(size.usd_per_hour).toFixed(2)}/h`;
}
// "Running · Default · $0.42 this month · 3 starts", then a size waiting for the next start; a problem replaces the state.
function cloudMachineStatus(m) {
  const parts = [m.problem || CLOUD_STATES[m.state] || m.state, cloudSizeName(m.machine_size || m.size)];
  if (m.cost_month_usd) parts.push(`$${Number(m.cost_month_usd).toFixed(2)} this month`);
  if (m.starts_month) parts.push(`${m.starts_month} start${m.starts_month === 1 ? '' : 's'}`);
  if (m.machine_size && m.size !== m.machine_size) parts.push(`${cloudSizeName(m.size)} at next start`);
  if (m.always_on) parts.push('Always on');
  return parts.filter(Boolean).join(' · ');
}
// The Computer value on the bot's page and in its Info for a bot on its own machine; '' for any other.
function cloudMachineComputerHTML(e) {
  return cloudMachineOwn(e) ? `Own cloud machine <span class="muted">· ${esc(cloudMachineStatus(e.cloud_machine))}</span>` : '';
}
// Settings > Computers: whose machine a computer is.
function cloudMachineFacts(machine) {
  const m = machine.cloud_machine;
  return m ? `Cloud machine · ${empName(m.bot)} · ${cloudSizeName(m.size)}` : '';
}
// The bot editor's row under Computer: size and Always on, both held until Save bot (`stage`).
function cloudMachineRowHTML(e) {
  if (!cloudMachineOwn(e)) return '';
  const m = e.cloud_machine, may = settingsCanManageBot(e) ? '' : 'disabled';
  const names = CLOUD_SIZES?.length ? CLOUD_SIZES : CLOUD_SIZE_NAMES.map(name => ({name}));
  return `<div class="sb-row" data-bot-cloud-row><span>Size</span><div class="rel-line">
      <select class="settings-inline-select" data-bot-cloud-size="${esc(e.name)}" data-current="${esc(m.size)}" aria-label="Cloud machine size for ${esc(e.display_name)}" ${may}>
        ${names.map(size => `<option value="${esc(size.name)}" ${size.name === m.size ? 'selected' : ''}>${esc(cloudSizeLabel(size))}</option>`).join('')}
      </select>
      <label class="bot-editor-check"><input type="checkbox" data-bot-cloud-always="${esc(e.name)}" data-current="${m.always_on}" ${m.always_on ? 'checked' : ''} ${may}> Always on</label>
    </div><span></span></div>
    <div class="sb-sub" data-bot-cloud-status>${esc(cloudMachineStatus(m))}</div>`;
}
// HQ's sizes, with their prices, once they are read; the choice in the select stays as it is.
async function cloudMachineMount(rows, slug) {
  const select = rows?.querySelector(`[data-bot-cloud-size="${CSS.escape(slug)}"]`); if (!select) return;
  const sizes = await cloudMachineSizes();
  if (!sizes.length || !select.isConnected) return;
  const value = select.value;
  select.innerHTML = sizes.map(size => `<option value="${esc(size.name)}" ${size.name === value ? 'selected' : ''}>${esc(cloudSizeLabel(size))}</option>`).join('');
}
// One change to the bot's own machine; it moves no revision, so the next held row keeps the one it has.
function cloudMachineRun(slug, body) {
  return async rev => { await post(`/v2/bots/${encodeURIComponent(slug)}/cloud-machine`, body); return rev; };
}
