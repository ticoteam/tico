/* ui/app/needs-you.js — Needs-you items: rendering, decide, reply
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ---- Needs you: hub.db items, rendered as the same request rows the Issues use ----
function needsV2Item(it, folded, showName = true) {
  const kind = String(it.kind || 'task');
  const from = taskRequester(it) || it.requested_by || '';
  const slug = actorSlug(from);
  const line = it.first_line || v2Firstline(it.body);
  const title = it.title || line || V2_KIND[kind] || 'Item';
  const acts = [];
  if (kind === 'approval') {
    acts.push(`<button class="primary" type="button" data-v2-approval="${esc(it.id)}" data-v2-decision="approved">Approve</button>`);
    acts.push(`<button class="ghost danger" type="button" data-v2-approval="${esc(it.id)}" data-v2-decision="declined">Decline</button>`);
  } else {
    if (it.status !== 'done') acts.push(`<button class="primary" type="button" data-v2-task="${esc(it.id)}" data-v2-status="done">Done</button>`);
    if (['done', 'declined'].includes(String(it.status))) acts.push(`<button class="ghost" type="button" data-v2-task="${esc(it.id)}" data-v2-status="open">Reopen</button>`);
    acts.push(`<button class="ghost danger" type="button" data-v2-task="${esc(it.id)}" data-v2-close="1">Close</button>`);
  }
  if (it.conversation_id) acts.push(`<button class="ghost" type="button" data-v2-reply="${esc(it.conversation_id)}">Reply</button>`);
  const payload = it.payload && typeof it.payload === 'object'
    ? `<div class="req-next"><span>Exactly this</span><div class="md"><pre>${esc(JSON.stringify(it.payload, null, 2))}</pre></div></div>` : '';
  return `<details class="req v2" data-v2-id="${esc(it.id)}" data-task-version="${esc(it.version || '')}"${folded ? ' hidden data-folded' : ''}>
    <summary>
      ${slug && S.emps.some(e => e.name === slug) ? avatar(slug, 24) : personCircle(actorLabel(from) || 'Tico', 24)}
      <span class="req-text">${showName ? `<span class="req-from">${esc(actorLabel(from) || 'Tico')}:</span> ` : ''}${esc(plainActors(title))}</span>
      <span class="req-meta"><span class="pill ${['approval', 'question', 'waiting'].includes(kind) ? 'needs' : kind === 'declined' ? 'blocked' : ''}">${esc(V2_KIND[kind] || kind)}</span>${esc(ago(it.created))}</span>
      <span class="req-arrow" aria-hidden="true">›</span>
    </summary>
    <div class="req-body">
      <div class="req-title">${esc(title)}</div>
      ${it.body ? `<div class="req-context"><div class="md">${safeMd(taskBody(it))}</div></div>`
                : line ? `<div class="req-context">${esc(line)}</div>` : ''}
      ${payload}
      ${it.note ? `<div class="req-next"><span>Note</span><div class="md">${safeMd(it.note)}</div></div>` : ''}
      <div class="issue-actions">${kind === "approval" ? "" : taskConversationButton(it.id)}${acts.join('')}</div>
      <div class="issue-compose" hidden></div>
    </div>
  </details>`;
}
async function v2Decide(button, id, decision) {
  const label = button.textContent;
  button.disabled = true; button.textContent = '…';
  try {
    await post(`/v2/approvals/${encodeURIComponent(id)}`, {decision});
    toast(decision === 'approved' ? 'Approved' : 'Declined');
    button.closest('.req')?.remove();
    await v2Refresh(); await refresh(true);
  } catch (e) { toast(e.message, true); button.disabled = false; button.textContent = label; }
}
async function v2TaskAct(button, id, body) {
  const label = button.textContent;
  button.disabled = true; button.textContent = '…';
  try {
    let task;
    if (body.status === 'done' || body.close) {
      ({task} = await get(`/v2/tasks/${encodeURIComponent(id)}`));
      const expected = body.version || Number(button.closest('[data-task-version]')?.dataset.taskVersion);
      if (expected && expected !== task.version) throw new Error('Task changed; refresh and review it before finishing.');
      const note = await taskOutcomeNote(task, body);
      if (note === null) { button.disabled = false; button.textContent = label; return false; }
      if (note) body = {...body, note};
    }
    if (S.me?.cloud) {
      const displayed = Number(button.closest('[data-task-version]')?.dataset.taskVersion);
      body = {...body, version: body.version || displayed || task?.version || (await get(`/v2/tasks/${encodeURIComponent(id)}`)).task.version};
    }
    const saved = await post(`/v2/tasks/${encodeURIComponent(id)}`, body);
    toast(body.close ? 'Closed' : body.status === 'open' ? 'Reopened' : `Marked ${body.status}`,
      false, body.status === 'done' ? {label: 'Undo', run: async () => {
        await post(`/v2/tasks/${encodeURIComponent(id)}`, {version: saved.task.version, status: task?.status || 'open'});
        await v2Refresh(); await refresh(true); personTasksReload();
        if (TASKS_ST) await tasksLoad(TASKS_ST);
        if (BOT) { if (isKeeper(BOT.slug)) void loadBotTasksV2(BOT.slug); void loadBotChatTasks(BOT.slug); }
      }} : null);
    if (body.close || body.status === 'done') button.closest('.req')?.remove();
    await v2Refresh();
    if (BOT) {
      if (isKeeper(BOT.slug)) void loadBotTasksV2(BOT.slug);
      void loadBotChatTasks(BOT.slug);
    }
    personTasksReload();
    await refresh(true);
    return true;
  } catch (e) { toast(e.message, true); button.disabled = false; button.textContent = label; return false; }
}
function taskOutcomeNote(task, action) {
  if (!task || !String(task.owner || '').startsWith('human:') ||
      !String(task.requester || '').startsWith('bot:') ||
      (action.close && ['done', 'declined'].includes(task.status))) return Promise.resolve('');
  return new Promise(resolve => {
    const dialog = document.createElement('dialog');
    dialog.className = 'tmodal task-outcome';
    const closing = !!action.close;
    dialog.innerHTML = `<form class="tmodal-body">
      <h2>${closing ? 'Close this request' : 'Finish this request'}</h2>
      ${task.title ? `<p class="task-outcome-title">${esc(task.title)}</p>` : ''}
      <p class="muted">${closing ? 'Why close it?' : 'What did you decide or do?'}</p>
      <textarea required maxlength="4000" aria-label="Result or decision" placeholder="State the result or decision explicitly."></textarea>
      <div class="row"><button class="ghost" type="button" data-cancel>Cancel</button>
        <button class="primary" type="submit">${closing ? 'Close task' : 'Mark done'}</button></div>
    </form>`;
    const finish = note => { dialog.close(); dialog.remove(); resolve(note); };
    dialog.querySelector('[data-cancel]').onclick = () => finish(null);
    dialog.addEventListener('cancel', ev => { ev.preventDefault(); finish(null); });
    dialog.querySelector('form').onsubmit = ev => {
      ev.preventDefault();
      const note = dialog.querySelector('textarea').value.trim();
      if (note) finish(note);
    };
    document.body.appendChild(dialog);
    dialog.showModal();
    dialog.querySelector('textarea').focus();
  });
}
function v2ReplyBox(button, conversation) {
  const host = button.closest('.req-body')?.querySelector('.issue-compose');
  if (!host) return;
  if (!host.hidden) { host.hidden = true; host.innerHTML = ''; return; }
  host.hidden = false;
  host.innerHTML = `<form>
    <textarea aria-label="Reply in this conversation" placeholder="Your reply…"></textarea>
    <div class="row"><button class="primary" type="submit">Send</button>
      <button class="ghost" type="button" data-v2-cancel>Cancel</button><span class="muted" data-v2-msg></span></div>
    </form>`;
  const form = $('form', host), box = $('textarea', host), msg = $('[data-v2-msg]', host);
  $('[data-v2-cancel]', host).onclick = () => { host.hidden = true; host.innerHTML = ''; };
  form.onsubmit = async ev => {
    ev.preventDefault();
    const text = box.value.trim();
    if (!text) { box.focus(); return; }
    [...form.elements].forEach(el => el.disabled = true); msg.textContent = 'Sending…';
    try {
      await post(`/v2/conversations/${encodeURIComponent(conversation)}/messages`, {text});
      host.hidden = true; host.innerHTML = '';
      toast('Sent');
      await v2Refresh();
    } catch (e) {
      msg.innerHTML = `<span class="err">${esc(e.message)}</span>`;
      [...form.elements].forEach(el => el.disabled = false);
    }
  };
  box.focus();
}
document.addEventListener('click', ev => {
  const ap = ev.target.closest('[data-v2-approval]');
  if (ap) { ev.preventDefault(); void v2Decide(ap, ap.dataset.v2Approval, ap.dataset.v2Decision); return; }
  const tk = ev.target.closest('[data-v2-task]');
  if (tk) {
    ev.preventDefault();
    void v2TaskAct(tk, tk.dataset.v2Task, tk.dataset.v2Close ? {close: true} : {status: tk.dataset.v2Status});
    return;
  }
  const rp = ev.target.closest('[data-v2-reply]');
  if (rp) { ev.preventDefault(); v2ReplyBox(rp, rp.dataset.v2Reply); }
});
