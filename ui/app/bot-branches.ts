/* ui/app/bot-branches.ts — Branch picker and personal branch creation. */
'use strict';

// A branch is a roster row that follows another bot (`shared_from`), with its own revision and profile (settings-bot-editor.ts).
interface BranchRow extends Employee {
  shared_from?: string | null;
  revision?: number;
  profile?: { name?: string; summary?: string } | null;
}

// GET /v2/bots/{slug}/branches: the original bot's id, whether it is shared, and the branches of it.
interface BranchesAnswer {
  original: string;
  shared?: boolean;
  branches?: Employee[];
}

// A computer the signed-in person owns, as GET /v2/computers lists it (the fields this file reads).
interface OwnedComputer {
  id: string;
  label: string;
  owner_id?: string | null;
  revoked_at?: string | null;
  bots?: (string | { id?: string; slug?: string })[];
}

const branchBotLink = (slug: string): string => `#/bot/${encodeURIComponent(slug)}`;
const branchPersonLabel = (branch: Employee): string => `${botPersonName(branch.owner_id)}'s branch`;

function branchDialog(title: string): HTMLDialogElement {
  const previous = $('#branch-editor');
  if (previous) { previous.close(); previous.remove(); }
  const dialog = document.createElement('dialog');
  dialog.id = 'branch-editor'; dialog.className = 'bot-editor';
  dialog.setAttribute('aria-label', title);
  dialog.addEventListener('close', () => dialog.remove(), {once: true});
  document.body.appendChild(dialog);
  return dialog;
}


async function botBranchesLoad(slug: string): Promise<void> {
  const host = $('#bot-branches') as HTMLElement | null, bot = botRow(slug);
  if (!host || !bot || botLimited(bot) || (!bot.shared && !bot.shared_from && !S.emps.some(e => e.shared_from === slug))) return;
  try {
    const data = await get<BranchesAnswer>(`/v2/bots/${encodeURIComponent(slug)}/branches`);
    if (BOT?.slug !== slug || !host.isConnected) return;
    const original = botRow(data.original);
    const branches = [...(data.branches || [])];
    if (!data.shared && !bot.shared_from && !branches.length) { host.innerHTML = ''; return; }
    if (bot.shared_from && !branches.some(b => b.id === slug)) branches.push(bot);
    const mine = branches.find(b => b.owner_id === S.me?.id);
    const make = data.shared && original?.owner_id !== S.me?.id && !mine;
    // With no branches yet the picker has nothing to choose, so the button alone offers a branch.
    const pick = branches.length > 0;
    host.innerHTML = `${pick ? `<select aria-label="Branch" aria-description="Press Enter to open" data-branch-picker>
      <option value="${esc(data.original)}" ${slug === data.original ? 'selected' : ''}>Original</option>
      ${branches.map(b => `<option value="${esc(b.id)}" ${slug === b.id ? 'selected' : ''} ${b.status === 'archived' ? 'disabled' : ''}>${esc(branchPersonLabel(b))}${b.status === 'archived' ? ' · Archived' : ''}</option>`).join('')}
      </select>` : ''}
      ${bot.shared_from ? `<a href="${branchBotLink(data.original)}" aria-label="Open original ${esc(botDisplayName(data.original))}">Original</a>${data.shared ? '' : '<span class="muted">branches off</span>'}` : ''}
      ${make ? '<button type="button" class="ghost" data-branch-make>Make my branch</button>' : ''}`;
    host.querySelector('[data-branch-make]')?.addEventListener('click', () => void botBranchCreate(data.original));
    const picker = host.querySelector<HTMLSelectElement>('[data-branch-picker]');
    if (!picker) return;
    let keyboard = false;
    picker.onpointerdown = () => { keyboard = false; };
    picker.onkeydown = event => {
      if (!['Enter', 'Tab'].includes(event.key)) keyboard = true;
      if (event.key === 'Enter') { event.preventDefault(); keyboard = false; choose(); }
    };
    const choose = () => {
      location.hash = branchBotLink(picker.value);
    };
    picker.onchange = () => { if (!keyboard) choose(); };
    picker.onblur = () => { picker.value = slug; keyboard = false; };
  } catch (error) {
    if (BOT?.slug === slug && host.isConnected) host.textContent = 'Branches unavailable';
  }
}

async function botBranchCreate(source: string): Promise<void> {
  try {
    const data = await get<{ computers?: OwnedComputer[] }>('/v2/computers');
    const owned = (data.computers || []).filter(c => !c.revoked_at && c.owner_id === S.me?.id);
    const family = new Set<string | undefined>(S.emps.filter(e => e.id === source || e.shared_from === source).map(e => e.id));
    const occupied = new Set(S.emps.filter(e => family.has(e.id)).map(e => e.machine?.runner_id || e.assignment?.runner_id).filter(Boolean));
    const computers = owned.filter(c => !occupied.has(c.id) && !(c.bots || []).some(bot => family.has(typeof bot === 'string' ? bot : bot.id || bot.slug)));
    const planned = !computers.length;
    const dialog = branchDialog('Make my branch');
    dialog.innerHTML = `<form><div class="tmodal-head"><h2>Make my branch</h2><span class="spacer"></span>
      <button class="ghost" type="button" data-branch-close aria-label="Close">✕</button></div>
      <div class="bot-editor-body">${planned
        ? `<p>${owned.length ? 'Your computers already run the original or a branch of it.' : 'You have no computer yet.'} Your branch needs another computer.</p>
          <p>Create a planned branch now. It waits until you add a computer in Settings → Computers.</p>`
        : `<label>Computer<select name="runner_id" required>
          ${computers.map(c => `<option value="${esc(c.id)}">${esc(c.label)}</option>`).join('')}</select></label>`}
      <div class="row"><button class="primary" type="submit">${planned ? 'Create planned branch' : 'Make my branch'}</button>
      <span role="status" data-branch-status></span></div></div></form>`;
    const close = dialog.querySelector<HTMLButtonElement>('[data-branch-close]');
    if (!close) throw new Error('Branch dialog has no close button');
    close.onclick = () => dialog.close();
    const form = dialog.querySelector<HTMLFormElement>('form');
    if (!form) throw new Error('Branch dialog has no form');
    form.onsubmit = async event => {
      event.preventDefault();
      const button = form.querySelector<HTMLButtonElement>('[type=submit]'), status = form.querySelector<HTMLElement>('[data-branch-status]');
      if (!button || !status) throw new Error('Branch dialog has no submit button or status line');
      button.disabled = true; status.textContent = 'Creating…';
      try {
        const made = await post(`/v2/bots/${encodeURIComponent(source)}/copies`, planned ? {} : {runner_id: (form.elements as HTMLFormControlsCollection & {runner_id: HTMLSelectElement}).runner_id.value});
        dialog.close(); await refresh(true);
        if (!botRow(made.id)) S.emps.push({...made, name: made.slug,
          host: 'keeper', schedules: [], users: [], can_chat: true, can_manage: true,
          my_access: {see: true, read: true, write: true}});
        location.hash = branchBotLink(made.id);
      } catch (error) {
        if (dialog.isConnected) { status.textContent = errorMessage(error, ''); button.disabled = false; }
        else toast(errorMessage(error, ''));
      }
    };
    dialog.showModal();
  } catch (error) { toast(errorMessage(error, '')); }
}

function settingsEditBranch(e: BranchRow, host: HTMLElement | null) {
  if (!host) { location.hash = `#/bot/${encodeURIComponent(e.id)}/settings`; return; }
  if (e.status === 'archived') return toast('This branch is archived. Restore it in Settings.');
  // settings-bot-editor.ts opens this only for a branch, so shared_from is set.
  const source = e.shared_from;
  if (!source) return;
  const dialog = host;
  let revision = e.revision;
  const profile = e.profile?.name || 'doer';
  dialog.innerHTML = `<form><div class="tmodal-head"><h2>${esc(botDisplayName(source))} · ${esc(branchPersonLabel(e))}</h2><span class="spacer"></span>
    </div>
    <div class="bot-editor-body"><p>Follows <a href="${branchBotLink(source)}">${esc(botDisplayName(source))}</a>.</p>
    <label>Status<select name="status">${['planned', 'active', 'paused'].map(value => `<option value="${value}" ${e.status === value ? 'selected' : ''}>${value === 'planned' ? 'Setting up' : value[0]!.toUpperCase() + value.slice(1)}</option>`).join('')}</select></label>
    <label>Profile<select name="profile">${(PROFILE_LIST || Object.entries(PROFILE_LABELS).map(([name, label]) => ({name, label}))).map((p: {name: string; label: string}) => `<option value="${esc(p.name)}" ${p.name === profile ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select></label>
    ${e.profile?.summary ? `<p class="muted">${esc(e.profile.summary)}</p>` : ''}
    <div class="row"><button class="primary" type="submit">Save</button><span role="status" data-branch-status></span></div></div></form>`;
  const form = dialog.querySelector<HTMLFormElement>('form');
  if (!form) throw new Error('Branch dialog has no form');
  form.onsubmit = async event => {
    event.preventDefault();
    const button = form.querySelector<HTMLButtonElement>('[type=submit]'), status = form.querySelector<HTMLElement>('[data-branch-status]');
    if (!button || !status) throw new Error('Branch dialog has no submit button or status line');
    const controls = form.elements as HTMLFormControlsCollection & { status: HTMLSelectElement; profile: HTMLSelectElement };
    button.disabled = true;
    try {
      // A branch's profile is its own (backend/profiles.py); each save moves the revision. Leaving Custom asks first.
      if (controls.profile.value !== profile) {
        const out = await profileSave(e.id, {profile: controls.profile.value}, revision);
        if (!out) { button.disabled = false; return; }
        revision = out.revision;
      }
      await post(`/v2/bots/${encodeURIComponent(e.id)}/definition`, {status: controls.status.value, expected_revision: revision});
      const here = location.hash, tab = BOT?.tab || 'more';
      if (here.startsWith('#/bot/')) {
        await refresh(true);
        if (location.hash === here && BOT?.slug === e.id) { BOT = null; pageBot(e.id, tab); }
      }
      else { await loadSettings(); settingsShow('bots'); }
    } catch (error) {
      if (errorCode(error) === 'version_conflict') {
        try { revision = (await get<{ revision?: number }>(`/v2/bots/${encodeURIComponent(e.id)}`)).revision; } catch {}
      }
      if (dialog.isConnected) { status.textContent = errorMessage(error, ''); button.disabled = false; }
      else toast(errorMessage(error, ''));
    }
  };

}
