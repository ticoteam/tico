/* ui/app/cloud-machines.ts — A bot's cloud machine on Tico Cloud, shared or its own: where it runs, size, Always on,
   Grow storage automatically, state
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// `cloud_machine` on a bot's row (/api/employees, backend/cloud_machines.py), null off Tico Cloud, so nothing here
// shows there. `mode` cloud runs the bot on a cloud machine, started when there is work and stopped when idle;
// `placement` says which: the company's shared one or its own. The size, Always on and state are that machine's. Any
// other computer is a machine transition, as before. A running machine restarts at a new size once its turns finish;
// the shared machine's size, Always on and Grow storage are the company's, changed by owners and admins (an own machine
// grows as the company's does until its bot chooses). The sizes are HQ's (GET /v2/cloud-machines), read once; before
// they come the four names stand alone.
// A bot's cloud machine as its row carries it (backend/cloud_machines.py row): the bot's own or the shared one.
interface CloudMachineRow {
  mode?: string;
  placement?: string;
  shared?: boolean;
  bot?: string;
  size?: string;
  machine_size?: string | null;
  state?: string;
  problem?: string | null;
  restarting?: boolean;
  runner_id?: string | null;
  cost_month_usd?: number;
  starts_month?: number;
  resizing?: boolean;
  always_on?: boolean;
  grow_note?: string;
  grow_storage?: boolean | null;
  pause_minutes?: number | null;
}
// A bot row from /api/employees, with its cloud machine.
interface CloudMachineBot extends Employee {
  cloud_machine?: CloudMachineRow | null;
}
// A size HQ offers (GET /v2/cloud-machines, `sizes`): its name, and its price and disk when HQ sends them.
// `available` false: the company's plan cannot start it, and `reason` says why ("Needs a higher plan").
interface CloudMachineSize {
  name: string;
  cpu?: number;
  memory_gb?: number;
  usd_per_hour?: number;
  disk_gb?: number;
  available?: boolean;
  reason?: string;
}
const CLOUD_SIZE_NAMES = ['small', 'default', 'large', 'xlarge'];
const CLOUD_STATES: Record<string, string> = {none: 'Not started yet', starting: 'Starting', running: 'Running', stopping: 'Stopping',
  stopped: 'Stopped', deleted: 'Deleted', failed: 'Failed'};
// The Computer select's values for the two cloud machines; any other value is a computer's id.
const CLOUD_CHOICES: Record<string, string> = {'cloud-shared': 'shared', 'cloud-own': 'own'};
// The names the Computer select shows for them; settings-bots.js search matches a bot's by the same words.
const CLOUD_CHOICE_LABELS: Record<string, string> = {'cloud-shared': 'Shared cloud machine', 'cloud-own': 'Own cloud machine'};
let CLOUD_SIZES: CloudMachineSize[] | null = null;
const cloudSizeName = (name: string | null | undefined) => name ? name.charAt(0).toUpperCase() + name.slice(1) : '';
const cloudMachineOn = (e: CloudMachineBot | null | undefined) => e?.cloud_machine?.mode === 'cloud';
const cloudMachineShared = (e: CloudMachineBot) => cloudMachineOn(e) && e.cloud_machine?.placement !== 'own';
const cloudMachinePick = (value: string) => CLOUD_CHOICES[value] || null;
const cloudMachineValue = (e: CloudMachineBot) => cloudMachineOn(e) ? (cloudMachineShared(e) ? 'cloud-shared' : 'cloud-own') : '';
// The name of a bot's cloud machine as the Computer select shows it; '' for a bot not on one.
const cloudMachineLabel = (e: CloudMachineBot) => CLOUD_CHOICE_LABELS[cloudMachineValue(e)] || '';
function cloudMachineOptionsHTML(current: string | null | undefined): string {
  return Object.entries(CLOUD_CHOICE_LABELS).map(([value, label]) =>
    `<option value="${value}" ${value === current ? 'selected' : ''}>${label}</option>`).join('');
}
async function cloudMachineSizes(): Promise<CloudMachineSize[]> {
  if (!CLOUD_SIZES) {
    try { CLOUD_SIZES = (await get('/v2/cloud-machines')).sizes || []; } catch { return []; }
  }
  return CLOUD_SIZES ?? [];
}
// A size the plan cannot start says why after it: "XLarge · 16 CPU · … — needs a higher plan".
function cloudSizeLabel(size: CloudMachineSize): string {
  const why = size.available === false ? ` — ${cloudSizeReason(size)}` : '';
  if (!size.cpu) return cloudSizeName(size.name) + why;
  const hourly = Number(size.usd_per_hour).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 6});
  const disk = size.disk_gb ? ` · ${size.disk_gb} GB disk` : '';
  return `${cloudSizeName(size.name)} · ${size.cpu} CPU · ${size.memory_gb} GB${disk} · $${hourly}/h${why}`;
}
function cloudSizeReason(size: CloudMachineSize): string {
  const why = size.reason || 'Needs a higher plan';
  return why.charAt(0).toLowerCase() + why.slice(1);
}
// "Running · Default · $0.42 this month · 3 starts", then a size waiting for the restart, and a grow note only while it
// needs someone ("Disk 96% full: XLarge is the largest size"); a problem replaces the state.
function cloudMachineStatus(m: CloudMachineRow): string {
  const parts: (string | null | undefined)[] = [m.problem || CLOUD_STATES[m.state ?? ''] || m.state, cloudSizeName(m.machine_size || m.size)];
  if (m.cost_month_usd) parts.push(`$${Number(m.cost_month_usd).toFixed(2)} this month`);
  if (m.starts_month) parts.push(`${m.starts_month} start${m.starts_month === 1 ? '' : 's'}`);
  if (m.machine_size && m.size !== m.machine_size)
    parts.push(`${cloudSizeName(m.size)} ${m.resizing ? 'once its turns finish' : 'at next start'}`);
  if (m.always_on) parts.push('Always on');
  if (m.grow_note) parts.push(m.grow_note);
  return parts.filter(Boolean).join(' · ');
}
// The Computer value on the bot's page and in its Info for a bot on a cloud machine; '' for any other.
function cloudMachineComputerHTML(e: CloudMachineBot): string {
  if (!cloudMachineOn(e)) return '';
  const m = e.cloud_machine;
  if (!m) return '';
  return `${cloudMachineShared(e) ? 'Shared' : 'Own'} cloud machine <span class="muted">· ${esc(cloudMachineStatus(m))}</span>`;
}
// Settings > Computers: which cloud machine a computer is.
function cloudMachineFacts(machine: CloudMachineBot): string {
  const m = machine.cloud_machine;
  if (!m) return '';
  return m.shared ? `Cloud machine · ${cloudSizeName(m.size)}` : `Cloud machine · ${empName(m.bot)} · ${cloudSizeName(m.size)}`;
}
// The bot editor's rows under Computer: its machine's size, Pause after, Always on and Grow storage, all
// held until Save bot (`stage`). The shared machine's are the company's: only owners and admins change them. Always on
// never pauses, so Pause after is off while it is on.
function cloudMachineRowHTML(e: CloudMachineBot): string {
  if (!cloudMachineOn(e)) return '';
  const m = e.cloud_machine;
  if (!m) return '';
  const scope = cloudMachineShared(e) ? 'shared' : 'own';
  const may = (scope === 'shared' ? settingsIsAdmin() : settingsCanManageBot(e)) ? '' : 'disabled';
  const names = CLOUD_SIZES?.length ? CLOUD_SIZES : CLOUD_SIZE_NAMES.map(name => ({name}));
  const what = scope === 'shared' ? 'the shared cloud machine' : `${esc(e.display_name)}'s cloud machine`;
  const pauseOff = (may || m.always_on) ? 'disabled' : '';
  return `<div class="sb-row" data-bot-cloud-row><span>Size</span><div class="rel-line">
      <select class="settings-inline-select" data-bot-cloud-size="${esc(e.id)}" data-cloud-scope="${scope}" data-current="${esc(m.size)}" data-machine-size="${esc(m.machine_size || '')}" aria-label="Size of ${what}" ${may}>
        ${cloudSizeOptions(names, m.size, m.machine_size)}
      </select>
      <label class="bot-editor-check"><input type="checkbox" data-bot-cloud-always="${esc(e.id)}" data-cloud-scope="${scope}" data-current="${m.always_on}" ${m.always_on ? 'checked' : ''} ${may}> Always on</label>
      <label class="bot-editor-check"><input type="checkbox" data-bot-cloud-grow="${esc(e.id)}" data-cloud-scope="${scope}" data-current="${m.grow_storage !== false}" ${m.grow_storage !== false ? 'checked' : ''} ${may}> Grow storage</label>
    </div><span></span></div>
    <div class="sb-row"><span>Pause after</span><div class="rel-line">
      <input type="number" min="5" max="1440" step="1" class="settings-inline-select" data-bot-cloud-pause="${esc(e.id)}" data-cloud-scope="${scope}" data-current="${m.pause_minutes}" value="${m.pause_minutes}" aria-label="Pause after, in minutes, for ${what}" ${pauseOff}>
      <span class="muted">min</span>
    </div><span></span></div>
    <div class="sb-sub" data-bot-cloud-status>${esc(cloudMachineStatus(m))}</div>
    ${scope === 'own' && settingsIsAdmin() && (m.runner_id || !['none', 'deleted'].includes(m.state ?? '') || (m.state === 'deleted' && m.problem)) ? `<div class="sb-row"><span></span><div>
      <button type="button" class="ghost" data-bot-cloud-replace="${esc(e.id)}">Replace machine</button>
    </div><span></span></div>` : ''}`;
}
// HQ's sizes, with their prices, once they are read; the choice in the select stays as it is.
async function cloudMachineMount(rows: ParentNode | null | undefined, slug: string): Promise<void> {
  const select = rows?.querySelector<HTMLSelectElement>(`[data-bot-cloud-size="${CSS.escape(slug)}"]`); if (!select) return;
  const sizes = await cloudMachineSizes();
  if (!sizes.length || !select.isConnected) return;
  select.innerHTML = cloudSizeOptions(sizes, select.value, select.dataset.machineSize);
}
// A size with less disk than the machine has now is off: a disk cannot shrink. So is one the company's plan cannot
// start (HQ's `available` false), shown with its reason rather than hidden, so people see larger sizes exist.
function cloudSizeOptions(sizes: CloudMachineSize[], value: string | undefined, machineSize: string | null | undefined): string {
  const floor = sizes.find(size => size.name === machineSize)?.disk_gb || 0;
  const off = (size: CloudMachineSize) => size.available === false || Boolean(size.disk_gb && size.disk_gb < floor);
  return sizes.map(size => `<option value="${esc(size.name)}" ${size.name === value ? 'selected' : ''} ${off(size) ? 'disabled' : ''}>${esc(cloudSizeLabel(size))}</option>`).join('');
}
// One change to a cloud machine: the bot's (`/bots/<bot>/cloud-machine`), or the shared one's for a control whose
// scope is shared. It moves no revision, so the next held row keeps the one it has.
function cloudMachineRun(slug: string, body: Record<string, unknown>, control?: HTMLElement | null): (revision: number) => Promise<number> {
  const path = control?.dataset.cloudScope === 'shared' ? '/v2/cloud-machines/shared' : `/v2/bots/${encodeURIComponent(slug)}/cloud-machine`;
  return async rev => { await post(path, body); return rev; };
}
async function cloudMachineReplace(slug: string, button: HTMLButtonElement): Promise<void> {
  if (!await settingsConfirm({title: 'Replace machine?', ok: 'Replace machine',
    text: 'Deletes this machine and its disk. The next task starts a fresh one.'})) return;
  settingsFieldClear(button);
  button.disabled = true;
  try {
    await post(`/v2/bots/${encodeURIComponent(slug)}/cloud-machine/replace`, {});
    await loadSettings();
    const status = button.closest('[data-bot-people]')?.querySelector<HTMLElement>('[data-bot-cloud-status]');
    const bot: CloudMachineBot | undefined = S.emps.find(e => e.id === slug);
    const machine = bot?.cloud_machine;
    if (status && machine) { status.textContent = cloudMachineStatus(machine); button.remove(); }
    toast('Machine replaced');
  } catch (error) { settingsFieldError(button, errorMessage(error, '')); }
  finally { button.disabled = false; }
}

// Tico support on cloud machines (GET /v2/cloud-machines/support): HQ's switch for the company and each machine's
// support helper, shared by the Settings panes until the next entry. A failed read shows no cached state.
const CLOUD_SUPPORT: {view: import('../types/api').CloudSupportView | null; read: Promise<import('../types/api').CloudSupportView | null> | null} = {view: null, read: null};
function cloudSupportRead({fresh = false}: {fresh?: boolean} = {}): Promise<import('../types/api').CloudSupportView | null> | null {
  if (fresh) CLOUD_SUPPORT.read = null;
  if (!CLOUD_SUPPORT.read) {
    const read: Promise<import('../types/api').CloudSupportView | null> = get('/v2/cloud-machines/support', {fresh: true}).then((view: import('../types/api').CloudSupportView | null) => view?.available ? view : null, () => null)
      .then((view: import('../types/api').CloudSupportView | null) => {
        // An older read may finish after a new entry or a write. Only the newest answer can replace the switch.
        if (CLOUD_SUPPORT.read !== read) return CLOUD_SUPPORT.read;
        CLOUD_SUPPORT.view = view;
        return view;
      });
    CLOUD_SUPPORT.read = read;
  }
  return CLOUD_SUPPORT.read;
}
// The answer to a change is the whole state again; it replaces what was read.
function cloudSupportSet(view: import('../types/api').CloudSupportView | null | undefined): void {
  CLOUD_SUPPORT.view = view?.available ? view : null;
  CLOUD_SUPPORT.read = Promise.resolve(CLOUD_SUPPORT.view);
}
// "Tico support: on", "off" or "offline" for a computer that is a cloud machine, matched by HQ's key (the shared
// machine's `_shared`, else its bot); '' for a helper that is not there, any other computer, or before the read.
function cloudSupportWords(machine: Pick<CloudMachineBot, 'cloud_machine'> | null | undefined): string {
  const m = machine?.cloud_machine;
  if (!m || !CLOUD_SUPPORT.view) return '';
  const key = m.shared ? '_shared' : m.bot;
  const helper = CLOUD_SUPPORT.view.machines.find(row => row.key === key)?.helper;
  return ['on', 'off', 'offline'].includes(helper ?? '') ? `Tico support: ${helper}` : '';
}
