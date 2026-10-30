/* ui/app/cloud-machines.js — A bot's cloud machine on Tico Cloud, shared or its own: where it runs, size, Always on, state
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// `cloud_machine` on a bot's row (/api/employees, backend/cloud_machines.py), null off Tico Cloud, so nothing here
// shows there. `mode` cloud runs the bot on a cloud machine, started when there is work and stopped when idle;
// `placement` says which: the company's shared one or its own. The size, Always on and state are that machine's. Any
// other computer is a machine transition, as before. A size applies at the machine's next start; the shared machine's
// size and Always on are the company's, changed by owners and admins. The sizes are HQ's (GET /v2/cloud-machines),
// read once; before they come the four names stand alone.
const CLOUD_SIZE_NAMES = ['small', 'default', 'large', 'xlarge'];
const CLOUD_STATES = {none: 'Not started yet', starting: 'Starting', running: 'Running', stopping: 'Stopping',
  stopped: 'Stopped', deleted: 'Deleted', failed: 'Failed'};
// The Computer select's values for the two cloud machines; any other value is a computer's id.
const CLOUD_CHOICES = {'cloud-shared': 'shared', 'cloud-own': 'own'};
let CLOUD_SIZES = null;
const cloudSizeName = name => name ? name[0].toUpperCase() + name.slice(1) : '';
const cloudMachineOn = e => e?.cloud_machine?.mode === 'cloud';
const cloudMachineShared = e => cloudMachineOn(e) && e.cloud_machine.placement !== 'own';
const cloudMachinePick = value => CLOUD_CHOICES[value] || null;
const cloudMachineValue = e => cloudMachineOn(e) ? (cloudMachineShared(e) ? 'cloud-shared' : 'cloud-own') : '';
function cloudMachineOptionsHTML(current) {
  return Object.entries({'cloud-shared': 'Shared cloud machine', 'cloud-own': 'Own cloud machine'}).map(([value, label]) =>
    `<option value="${value}" ${value === current ? 'selected' : ''}>${label}</option>`).join('');
}
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
// The Computer value on the bot's page and in its Info for a bot on a cloud machine; '' for any other.
function cloudMachineComputerHTML(e) {
  if (!cloudMachineOn(e)) return '';
  return `${cloudMachineShared(e) ? 'Shared' : 'Own'} cloud machine <span class="muted">· ${esc(cloudMachineStatus(e.cloud_machine))}</span>`;
}
// Settings > Computers: which cloud machine a computer is.
function cloudMachineFacts(machine) {
  const m = machine.cloud_machine;
  if (!m) return '';
  return m.shared ? `Cloud machine · ${cloudSizeName(m.size)}` : `Cloud machine · ${empName(m.bot)} · ${cloudSizeName(m.size)}`;
}
// The bot editor's row under Computer: its machine's size and Always on, both held until Save bot (`stage`). The
// shared machine's are the company's: only owners and admins change them.
function cloudMachineRowHTML(e) {
  if (!cloudMachineOn(e)) return '';
  const m = e.cloud_machine, scope = cloudMachineShared(e) ? 'shared' : 'own';
  const may = (scope === 'shared' ? settingsIsAdmin() : settingsCanManageBot(e)) ? '' : 'disabled';
  const names = CLOUD_SIZES?.length ? CLOUD_SIZES : CLOUD_SIZE_NAMES.map(name => ({name}));
  const what = scope === 'shared' ? 'the shared cloud machine' : `${esc(e.display_name)}'s cloud machine`;
  return `<div class="sb-row" data-bot-cloud-row><span>Size</span><div class="rel-line">
      <select class="settings-inline-select" data-bot-cloud-size="${esc(e.name)}" data-cloud-scope="${scope}" data-current="${esc(m.size)}" aria-label="Size of ${what}" ${may}>
        ${names.map(size => `<option value="${esc(size.name)}" ${size.name === m.size ? 'selected' : ''}>${esc(cloudSizeLabel(size))}</option>`).join('')}
      </select>
      <label class="bot-editor-check"><input type="checkbox" data-bot-cloud-always="${esc(e.name)}" data-cloud-scope="${scope}" data-current="${m.always_on}" ${m.always_on ? 'checked' : ''} ${may}> Always on</label>
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
// One change to a cloud machine: the bot's (`/bots/<bot>/cloud-machine`), or the shared one's for a control whose
// scope is shared. It moves no revision, so the next held row keeps the one it has.
function cloudMachineRun(slug, body, control) {
  const path = control?.dataset.cloudScope === 'shared' ? '/v2/cloud-machines/shared' : `/v2/bots/${encodeURIComponent(slug)}/cloud-machine`;
  return async rev => { await post(path, body); return rev; };
}
