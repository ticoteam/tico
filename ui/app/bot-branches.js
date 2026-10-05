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

async function assignmentBranchesLoad(source) {
  const host = $('#bot-assignment-branches'), bot = S.emps.find(e => e.name === source);
  if (!host || !bot || bot.shared_from || botLimited(bot)) return;
  try {
    const data = await get(`/v2/bots/${encodeURIComponent(source)}/assignment-branches`);
    if (BOT?.slug !== source || !host.isConnected) return;
    if (!data.enabled) {
      host.innerHTML = '<section class="assignment-branches"><strong>Temporary assignments need Allow branches.</strong><p class="muted">Enable it in this role’s Settings before allocating a task.</p></section>';
      return;
    }
    const manager = S.me?.role === 'owner' || !!bot.can_manage;
    const parentIsBot = String(bot.reports_to || '').startsWith('bot:');
    host.innerHTML = `<section class="assignment-branches" aria-label="Temporary assignments">
      <div class="row"><strong>Temporary assignments</strong><span class="muted">${data.active}/${data.capacity} active · same role, separate task branches</span><span class="spacer"></span>
      ${manager && parentIsBot ? `<label class="assignment-policy"><input type="checkbox" data-assignment-policy ${data.allocator_enabled ? 'checked' : ''}> Allow direct parent to allocate</label>` : ''}
      ${manager ? '<button class="ghost" type="button" data-assignment-create>Create assignment</button>' : ''}</div>
      ${(data.assignments || []).length ? `<div class="assignment-list">${data.assignments.map(a => `<article class="assignment-card">
        <a href="#/bot/${encodeURIComponent(a.bot)}"><strong>${esc(a.display_name)}</strong></a>
        <span class="assignment-phase">${esc(a.phase)}</span>
        ${a.task ? `<a href="#/task/${encodeURIComponent(a.task.id)}">${esc(a.task.title)}</a>` : '<span class="muted">Linked task is not visible</span>'}
        <small class="muted">Parent role: ${esc(botDisplayName(source))} · Generation ${a.generation} · ${esc(a.runner_id)} · checkpoint: ${esc(JSON.stringify(a.checkpoint || {}).slice(0, 240) || 'none')}</small>
        ${a.cleanup ? `<small class="muted">Local cleanup: ${esc(a.cleanup.state)}${a.cleanup.detail ? ` · ${esc(a.cleanup.detail)}` : ''}</small>` : ''}
        ${manager && !['archived','cancelled'].includes(a.phase) ? `<button class="ghost" type="button" data-assignment-manage="${esc(a.id)}">Checkpoint / lifecycle</button>
          <button class="ghost" type="button" data-assignment-learning="${esc(a.id)}">Review reusable lesson</button>` : ''}
        ${manager && ['archived','cancelled'].includes(a.phase) && a.cleanup?.state !== 'complete'
          ? `<button class="ghost" type="button" data-assignment-cleanup="${esc(a.id)}" ${a.cleanup?.state === 'requested' ? 'disabled' : ''}>${a.cleanup?.state === 'blocked' ? 'Retry guarded cleanup' : a.cleanup?.state === 'requested' ? 'Cleanup requested' : 'Request guarded cleanup'}</button>` : ''}
        <details><summary>History</summary><div data-assignment-history="${esc(a.id)}">Loading…</div></details>
      </article>`).join('')}</div>` : '<p class="muted">No temporary assignments yet.</p>'}
    </section>`;
    host.querySelector('[data-assignment-policy]')?.addEventListener('change', async event => {
      const checkbox = event.currentTarget; checkbox.disabled = true;
      try {
        const saved = await put(`/v2/bots/${encodeURIComponent(source)}/assignment-branches/policy`,
          {enabled: checkbox.checked, expected_revision: bot.revision});
        bot.revision = saved.revision;
        toast(saved.enabled ? 'Direct-parent allocation enabled' : 'Direct-parent allocation disabled');
      } catch (error) { checkbox.checked = !checkbox.checked; toast(error.message); }
      finally { checkbox.disabled = false; }
    });
    host.querySelector('[data-assignment-create]')?.addEventListener('click', () => assignmentBranchCreate(source, data.assignments || []));
    host.querySelectorAll('[data-assignment-manage]').forEach(button => button.addEventListener('click', () => {
      const row = (data.assignments || []).find(a => a.id === button.dataset.assignmentManage);
      if (row) void assignmentBranchManage(source, row);
    }));
    host.querySelectorAll('[data-assignment-learning]').forEach(button => button.addEventListener('click', () => {
      const row = (data.assignments || []).find(a => a.id === button.dataset.assignmentLearning);
      if (row) void assignmentBranchReviewLearning(source, row);
    }));
    host.querySelectorAll('[data-assignment-cleanup]').forEach(button => button.addEventListener('click', () => {
      const row = (data.assignments || []).find(a => a.id === button.dataset.assignmentCleanup);
      if (row) void assignmentBranchCleanup(source, row);
    }));
    for (const row of data.assignments || []) {
      const details = host.querySelector(`[data-assignment-history="${CSS.escape(row.id)}"]`);
      if (!details) continue;
      void get(`/v2/assignment-branches/${encodeURIComponent(row.id)}/events`).then(result => {
        if (!details.isConnected) return;
        details.innerHTML = (result.events || []).map(event => `<p><strong>${esc(event.action)}</strong> · ${esc(event.created)}<br><small>${esc(event.detail_json)}</small></p>`).join('') || '<p>No history.</p>';
      }).catch(error => { details.textContent = error.message; });
    }
  } catch (error) {
    if (BOT?.slug === source && host.isConnected) {
      host.innerHTML = `<section class="assignment-branches" role="status"><strong>Could not load temporary assignments.</strong><p class="muted">${esc(error.message)}</p><button class="ghost" type="button" data-assignment-retry>Refresh</button></section>`;
      host.querySelector('[data-assignment-retry]')?.addEventListener('click', () => void assignmentBranchesLoad(source));
    }
  }
}

async function assignmentBranchCleanup(source, row) {
  if (!confirm(`Request separate cleanup for ${row.display_name}? The original runner will remove its local trees only if registration is exact, both trees are clean, and every assignment commit is already preserved on its remote. Hub lifecycle history and receipts remain.`)) return;
  try {
    await post(`/v2/assignment-branches/${encodeURIComponent(row.id)}/cleanup`, {expected_revision: row.revision});
    toast('Guarded cleanup requested from the original computer');
    await assignmentBranchesLoad(source);
  } catch (error) { toast(error.message); }
}

async function assignmentBranchReviewLearning(source, row) {
  const lesson = prompt(`Reusable lesson from ${row.display_name}. Paste only a generalized engineering lesson from ${row.display_name}'s learning worktree; do not include task/customer details, transcripts, attachments, code or credentials.`);
  if (lesson === null || !lesson.trim()) return;
  if (!confirm('I reviewed this lesson and confirm it is generalized reusable knowledge with no task/customer details, private conversation content, or credentials. Send only this text to the persistent role for publication on its shared learning trunk?')) return;
  try {
    const result = await patch(`/v2/assignment-branches/${encodeURIComponent(row.id)}`, {
      expected_revision: row.revision, checkpoint: row.checkpoint || {},
      reviewed_learning_note: lesson.trim(), confirm_learning_review: true,
      note: 'Human reviewed a reusable learning proposal at this checkpoint.'
    });
    toast(result.learning_message_id ? `Reviewed lesson sent to ${botDisplayName(source)} for its learning trunk` : 'Lesson was already recorded for this assignment');
    await assignmentBranchesLoad(source);
  } catch (error) { toast(error.message); }
}

async function assignmentKeyForTask(taskId) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(taskId));
  return 'task-' + Array.from(new Uint8Array(digest).slice(0, 18), byte => byte.toString(16).padStart(2, '0')).join('');
}

function assignmentBranchCreate(source, assignments = []) {
  const dialog = branchDialog('Create temporary task assignment');
  dialog.innerHTML = `<form><div class="tmodal-head"><h2>Temporary assignment</h2><span class="spacer"></span><button class="ghost" type="button" data-assignment-close aria-label="Close">✕</button></div>
    <div class="bot-editor-body"><p>This creates a separate actor and local Git branch for one existing delivery task. The role and its learning trunk stay in place.</p>
    <label>Task ID<input name="task_id" required autocomplete="off" placeholder="Paste the task ID"></label>
    <label>Assignment name<input name="display_name" required maxlength="100" placeholder="Pro Workflow Engineer"></label>
    <div class="row"><button class="primary" type="submit">Allocate task</button><span role="status" data-assignment-status></span></div></div></form>`;
  dialog.querySelector('[data-assignment-close]').onclick = () => dialog.close();
  dialog.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    const form = event.currentTarget, button = form.querySelector('[type=submit]'), status = form.querySelector('[data-assignment-status]');
    button.disabled = true; status.textContent = 'Checking authority and runner…';
    try {
      const taskId = form.elements.task_id.value.trim();
      const prior = assignments.filter(a => a.task_id === taskId).sort((a, b) => String(b.created).localeCompare(String(a.created)))[0];
      const assignmentKey = prior?.assignment_key || await assignmentKeyForTask(taskId);
      const generation = prior ? prior.generation + (['archived', 'cancelled'].includes(prior.phase) ? 1 : 0) : 1;
      const made = await post(`/v2/bots/${encodeURIComponent(source)}/assignment-branches`, {
        assignment_key: assignmentKey, generation, task_id: taskId,
        display_name: form.elements.display_name.value.trim()
      });
      dialog.close(); await refresh(true); location.hash = `#/bot/${encodeURIComponent(made.bot)}`;
    } catch (error) { status.textContent = error.message; button.disabled = false; }
  };
  dialog.showModal();
}

async function assignmentBranchManage(source, row) {
  const options = [];
  if (row.phase === 'working' && row.task?.status === 'review') options.push(['waiting_review', 'Wait for review']);
  if (row.phase === 'waiting_review' && ['open', 'doing'].includes(row.task?.status)) options.push(['working', 'Resume work']);
  if (row.phase === 'waiting_review' && ['ready', 'done'].includes(row.task?.status)) options.push(['waiting_release', 'Wait for release']);
  if (row.phase === 'waiting_release') options.push(['verifying', 'Start verification']);
  if (['working', 'preparing'].includes(row.phase)) options.push(['paused', 'Pause']);
  if (row.phase === 'paused') options.push(['working', 'Resume']);
  if (row.phase === 'verifying' && ['done', 'closed'].includes(row.task?.status)) options.push(['archived', 'Archive with receipts']);
  options.push(['cancelled', 'Cancel and return task']);
  const picked = prompt(`Assignment: ${row.display_name}\nCurrent phase: ${row.phase}\nChoose: ${options.map(([phase, label]) => `${phase} = ${label}`).join('\n')}\n\nEnter a phase (or rename to change its label):`);
  if (!picked) return;
  const byLabel = options.find(([, label]) => label.toLowerCase() === picked.trim().toLowerCase());
  const phase = byLabel?.[0] || (options.some(([key]) => key === picked.trim()) ? picked.trim() : null);
  const rename = !phase ? picked.trim() : null;
  let note = '', deployed_version = '', acceptance_receipt = '', learning_receipt = '', evidence_receipt = '', handoff_task_id = '', checkpoint = row.checkpoint || {};
  if (phase === 'cancelled') {
    note = prompt('Reason for cancellation; the task will return to the source role:') || ''; if (!note.trim()) return;
    handoff_task_id = prompt('Optional existing source-role follow-up task ID to record as handoff:') || '';
  }
  if (['working', 'paused', 'interrupted', 'waiting_review', 'waiting_release'].includes(phase)) {
    const saved = prompt(phase === 'working' ? 'Checkpoint to resume from:' : 'Record the safe assignment checkpoint as JSON:', JSON.stringify(checkpoint));
    if (saved === null) return;
    try { checkpoint = JSON.parse(saved || '{}'); } catch { return toast('Checkpoint must be valid JSON'); }
  }
  if (phase === 'archived') {
    deployed_version = prompt('Deployed version:') || ''; acceptance_receipt = prompt('Acceptance receipt reference:') || '';
    learning_receipt = prompt('Reviewed learning receipt reference:') || ''; evidence_receipt = prompt('Preserved evidence reference:') || '';
  }
  if (rename !== null) { const name = prompt('New display name:', row.display_name); if (!name) return; row.display_name = name; }
  try {
    await patch(`/v2/assignment-branches/${encodeURIComponent(row.id)}`, {
      expected_revision: row.revision, ...(phase ? {phase} : {}), ...(rename !== null ? {display_name: row.display_name} : {}),
      note, checkpoint, deployed_version, acceptance_receipt, learning_receipt, evidence_receipt, handoff_task_id
    });
    await assignmentBranchesLoad(source);
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
