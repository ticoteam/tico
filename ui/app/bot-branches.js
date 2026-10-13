/* ui/app/bot-branches.js — Branch picker and personal branch creation. */
'use strict';

const branchBotLink = slug => `#/bot/${encodeURIComponent(slug)}`;
const branchPersonLabel = branch => `${botPersonName(branch.operator)}'s branch`;

function branchDialog(title) {
  const previous = $('#branch-editor');
  if (previous) { previous.close(); previous.remove(); }
  const dialog = document.createElement('dialog');
  dialog.id = 'branch-editor'; dialog.className = 'bot-editor';
  dialog.setAttribute('aria-label', title);
  dialog.addEventListener('close', () => dialog.remove(), {once: true});
  document.body.appendChild(dialog);
  return dialog;
}

function botBranchChatTarget(slug) {
  const original = S.emps.find(e => e.name === slug);
  if (!original?.shared) return slug;
  return S.emps.find(e => e.shared_from === slug && e.operator === S.me?.id && e.status === 'active')?.name || slug;
}

async function botBranchesLoad(slug) {
  const host = $('#bot-branches'), bot = S.emps.find(e => e.name === slug);
  if (!host || !bot || botLimited(bot) || (!bot.shared && !bot.shared_from && !S.emps.some(e => e.shared_from === slug))) return;
  try {
    const data = await get(`/v2/bots/${encodeURIComponent(slug)}/branches`);
    if (BOT?.slug !== slug || !host.isConnected) return;
    const original = S.emps.find(e => e.name === data.original);
    const branches = [...(data.branches || [])];
    if (!data.shared && !bot.shared_from && !branches.length) { host.innerHTML = ''; return; }
    if (bot.shared_from && !branches.some(b => b.slug === slug)) branches.push({...bot, slug});
    const mine = branches.find(b => b.operator === S.me?.id);
    const make = data.shared && original?.operator !== S.me?.id && !mine;
    // With no branches yet the picker has nothing to choose, so the button alone offers a branch.
    const pick = branches.length > 0;
    host.innerHTML = `${pick ? `<select aria-label="Branch" aria-description="Press Enter to open" data-branch-picker>
      <option value="${esc(data.original)}" ${slug === data.original ? 'selected' : ''}>Original</option>
      ${branches.map(b => `<option value="${esc(b.slug)}" ${slug === b.slug ? 'selected' : ''} ${b.status === 'archived' ? 'disabled' : ''}>${esc(branchPersonLabel(b))}${b.status === 'archived' ? ' · Archived' : ''}</option>`).join('')}
      </select>` : ''}
      ${bot.shared_from ? `<a href="${branchBotLink(data.original)}" aria-label="Open original ${esc(botDisplayName(data.original))}">Original</a>${data.shared ? '' : '<span class="muted">branches off</span>'}` : ''}
      ${make ? '<button type="button" class="ghost" data-branch-make>Make my branch</button>' : ''}`;
    host.querySelector('[data-branch-make]')?.addEventListener('click', () => void botBranchCreate(data.original));
    const picker = host.querySelector('[data-branch-picker]');
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

async function botBranchCreate(source) {
  try {
    const data = await get('/v2/computers');
    const owned = (data.computers || []).filter(c => !c.revoked_at && c.operator === S.me?.id);
    const family = new Set(S.emps.filter(e => e.name === source || e.shared_from === source).map(e => e.name));
    const occupied = new Set(S.emps.filter(e => family.has(e.name)).map(e => e.machine?.runner_id || e.assignment?.runner_id).filter(Boolean));
    const computers = owned.filter(c => !occupied.has(c.id) && !(c.bots || []).some(bot => family.has(typeof bot === 'string' ? bot : bot.slug || bot.name)));
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
    dialog.querySelector('[data-branch-close]').onclick = () => dialog.close();
    dialog.querySelector('form').onsubmit = async event => {
      event.preventDefault();
      const form = event.currentTarget, button = form.querySelector('[type=submit]'), status = form.querySelector('[data-branch-status]');
      button.disabled = true; status.textContent = 'Creating…';
      try {
        const made = await post(`/v2/bots/${encodeURIComponent(source)}/copies`, planned ? {} : {runner_id: form.elements.runner_id.value});
        dialog.close(); await refresh(true);
        if (!S.emps.some(e => e.name === made.slug)) S.emps.push({...made, name: made.slug,
          host: 'keeper', schedules: [], users: [], can_chat: true, can_manage: true,
          my_access: {see: true, read: true, write: true}});
        location.hash = branchBotLink(made.slug);
      } catch (error) {
        if (dialog.isConnected) { status.textContent = error.message; button.disabled = false; }
        else toast(error.message);
      }
    };
    dialog.showModal();
  } catch (error) { toast(error.message); }
}

function settingsEditBranch(e) {
  if (e.status === 'archived') return toast('This branch is archived. Restore it in Settings.');
  const dialog = branchDialog(`${botDisplayName(e.shared_from)} · ${branchPersonLabel(e)}`);
  let revision = e.revision;
  dialog.innerHTML = `<form><div class="tmodal-head"><h2>${esc(botDisplayName(e.shared_from))} · ${esc(branchPersonLabel(e))}</h2><span class="spacer"></span>
    <button class="ghost" type="button" data-branch-close aria-label="Close">✕</button></div>
    <div class="bot-editor-body"><p>Follows <a href="${branchBotLink(e.shared_from)}">${esc(botDisplayName(e.shared_from))}</a>.</p>
    <label>Status<select name="status">${['planned', 'active', 'paused'].map(value => `<option value="${value}" ${e.status === value ? 'selected' : ''}>${value === 'planned' ? 'Setting up' : value[0].toUpperCase() + value.slice(1)}</option>`).join('')}</select></label>
    <div class="row"><button class="primary" type="submit">Save</button><span role="status" data-branch-status></span></div></div></form>`;
  dialog.querySelector('[data-branch-close]').onclick = () => dialog.close();
  dialog.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    const form = event.currentTarget, button = form.querySelector('[type=submit]'), status = form.querySelector('[data-branch-status]');
    button.disabled = true;
    try {
      await post(`/v2/bots/${encodeURIComponent(e.name)}/definition`, {status: form.elements.status.value, expected_revision: revision});
      const here = location.hash, tab = BOT?.tab || 'more';
      dialog.close();
      if (here.startsWith('#/bot/')) {
        await refresh(true);
        if (location.hash === here && BOT?.slug === e.name) { BOT = null; pageBot(e.name, tab); }
      }
      else { await loadSettings(); settingsShow('bots'); }
    } catch (error) {
      if (error.body?.error?.code === 'version_conflict') {
        try { revision = (await get(`/v2/bots/${encodeURIComponent(e.name)}`)).revision; } catch {}
      }
      if (dialog.isConnected) { status.textContent = error.message; button.disabled = false; }
      else toast(error.message);
    }
  };
  dialog.showModal();
}
