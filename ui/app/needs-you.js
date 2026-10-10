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
  const payload = it.payload && typeof it.payload === 'object' ? approvalPayloadNode(it).outerHTML : '';
  const keep = kind === 'approval' ? ` data-patch-key="approval:${esc(it.id)}"` : '';
  return `<details class="req v2" data-v2-id="${esc(it.id)}" data-task-version="${esc(it.version || '')}"${keep}${folded ? ' hidden data-folded' : ''}>
    <summary>
      ${slug && S.emps.some(e => e.name === slug) ? avatar(slug, 24) : personCircle(actorLabel(from) || 'Tico', 24)}
      <span class="req-text">${showName ? `<span class="req-from">${esc(actorLabel(from) || 'Tico')}:</span> ` : ''}${esc(plainActors(title))}</span>
      <span class="req-meta"><span class="pill ${kind === 'approval' || kind === 'question' ? 'needs' : kind === 'declined' ? 'blocked' : ''}">${esc(V2_KIND[kind] || kind)}</span>${esc(ago(it.created))}</span>
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
// What an approval asks for, built as elements with every payload value set as text; the markup the list
// redraws from is this element's serialization, so a value is never parsed as HTML. A send shows its
// recipients, subject, draft and files readably; every kind keeps the exact payload under Details.
const APPROVAL_FILE_ID = /^[A-Za-z0-9-]{8,80}$/;   // the ids the file viewer opens (fileLinkId)
function approvalFileIcon() {
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg'), path = document.createElementNS(ns, 'path');
  for (const [k, v] of Object.entries({viewBox: '0 0 14 14', width: '13', height: '13', class: 'approval-file-icon', 'aria-hidden': 'true', focusable: 'false'})) svg.setAttribute(k, v);
  for (const [k, v] of Object.entries({d: 'M3.5 1.5h4.5l2.5 2.5v8.5h-7z M8 1.5V4h2.5', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.3', 'stroke-linejoin': 'round'})) path.setAttribute(k, v);
  svg.append(path);
  return svg;
}
function approvalPayloadNode(it) {
  const p = it.payload, el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = String(text);
    return node;
  };
  const box = el('div', 'req-next approval-payload'), main = el('div', 'md');
  box.append(el('span', '', 'Exactly this'), main);
  const raw = el('pre', '', JSON.stringify(p, null, 2));
  if (it.what !== 'send') { main.append(raw); return box; }
  const list = v => (Array.isArray(v) ? v : v == null || v === '' ? [] : [v]).map(String).join(', ');
  const facts = el('dl', 'approval-facts');
  for (const [label, value] of [['From', p.mailbox], ['To', list(p.to)], ['Cc', list(p.cc)],
                                ['Subject', p.subject], ['Draft', p.draft]]) {
    if (value == null || value === '') continue;
    facts.append(el('dt', '', label), el('dd', label === 'Draft' ? 'mono' : '', value));
  }
  main.append(facts);
  const files = Array.isArray(p.attachments) ? p.attachments.filter(f => f && typeof f === 'object') : [];
  if (files.length) {
    const ul = el('ul', 'approval-files');
    ul.setAttribute('aria-label', 'Attached files');
    for (const f of files) {
      const li = el('li'), name = String(f.name ?? 'file'), size = Number(f.size), hash = String(f.sha256 ?? '');
      li.append(approvalFileIcon());
      const id = String(f.file_id ?? '');
      if (APPROVAL_FILE_ID.test(id)) {
        const a = el('a', 'approval-file-name', name);
        a.href = '/api/v2/files/' + encodeURIComponent(id);
        a.dataset.name = name;
        li.append(a);
      } else li.append(el('span', 'approval-file-name', name));
      if (Number.isFinite(size) && size >= 0) li.append(el('span', 'approval-file-size', bytes(size)));
      if (hash) {
        const code = el('code', 'approval-file-hash', hash.slice(0, 12));
        code.title = 'sha256 ' + hash;
        li.append(code);
      }
      ul.append(li);
    }
    main.append(ul);
  }
  const details = el('details', 'approval-raw');
  details.append(el('summary', '', 'Details'), raw);
  main.append(details);
  return box;
}
// Approvals waiting on the person lead Needs you, decided in place with Approve or Decline.
const pendingApprovals = () => (S.v2?.needs || []).filter(it => it.kind === 'approval');
function approvalsHTML() {
  const items = pendingApprovals();
  if (!items.length) return '';
  return `<section class="tl-group" data-group="approvals"><header class="tl-ghead"><span class="tl-gname">Approvals</span><span class="tl-gcount tnum">${items.length}</span></header>
    <div class="tl-rows" role="list" aria-label="Approvals">${items.map(it => needsV2Item(it, false)).join('')}</div></section>`;
}
// A question a bot asked the person outside any task: no task row carries it, so Needs you lists it on its own,
// answered in place. `data-patch-key` keeps an opened row (and a half-written answer) through the list's redraws.
const looseAsks = () => (S.v2?.needs || []).filter(it => it.kind === 'ask');
function looseAskRow(it) {
  const from = it.requester || '', slug = actorSlug(from);
  return `<details class="req v2" data-patch-key="ask:${esc(it.id)}" data-ask="${esc(it.id)}">
    <summary>
      ${slug && S.emps.some(e => e.name === slug) ? avatar(slug, 24) : personCircle(actorLabel(from) || 'Tico', 24)}
      <span class="req-text"><span class="req-from">${esc(actorLabel(from) || 'Tico')}:</span> ${esc(plainActors(it.first_line || it.title))}</span>
      <span class="req-meta"><span class="pill needs">Question</span>${esc(ago(it.created))}</span>
      <span class="req-arrow" aria-hidden="true">›</span>
    </summary>
    <div class="req-body">
      <div class="req-context"><div class="md">${safeMd(plainActors(it.body || ''))}</div></div>
      <form class="issue-compose" data-ask-answer="${esc(it.id)}">
        <textarea aria-label="Your answer" placeholder="Your answer…" required></textarea>
        <div class="row"><button class="primary" type="submit">Answer</button><span class="muted" data-ask-msg></span></div>
      </form>
    </div>
  </details>`;
}
function looseAsksHTML() {
  const asks = looseAsks();
  if (!asks.length) return '';
  return `<section class="tl-group" data-group="asks"><header class="tl-ghead"><span class="tl-gname">Questions</span><span class="tl-gcount tnum">${asks.length}</span></header>
    <div class="tl-rows" role="list" aria-label="Questions">${asks.map(looseAskRow).join('')}</div></section>`;
}
document.addEventListener('submit', async ev => {
  const form = ev.target.closest?.('[data-ask-answer]');
  if (!form) return;
  ev.preventDefault();
  const box = $('textarea', form), msg = $('[data-ask-msg]', form), text = box.value.trim();
  if (!text) { box.focus(); return; }
  [...form.elements].forEach(el => el.disabled = true); msg.textContent = 'Sending…';
  try {
    await post(`/v2/messages/${encodeURIComponent(form.dataset.askAnswer)}/answer`, {text});
    toast('Answered');
    form.closest('details')?.remove();
    await v2Refresh();
    if (TASKS_ST && isTasksRoute(S.route)) tasksRender(TASKS_ST);
  } catch (e) {
    msg.innerHTML = `<span class="err">${esc(e.message)}</span>`;
    [...form.elements].forEach(el => el.disabled = false);
  }
});
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
// Finishing a bot's request records what was decided; closing never asks for a reason.
function taskOutcomeNote(task, action) {
  if (!task || action.close || !String(task.owner || '').startsWith('human:') ||
      !String(task.requester || '').startsWith('bot:')) return Promise.resolve('');
  return new Promise(resolve => {
    const dialog = document.createElement('dialog');
    dialog.className = 'tmodal task-outcome';
    dialog.innerHTML = `<form class="tmodal-body">
      <h2>Finish this request</h2>
      ${task.title ? `<p class="task-outcome-title">${esc(task.title)}</p>` : ''}
      <p class="muted">What did you decide or do?</p>
      <textarea required maxlength="4000" aria-label="Result or decision" placeholder="State the result or decision explicitly."></textarea>
      <div class="row"><button class="ghost" type="button" data-cancel>Cancel</button>
        <button class="primary" type="submit">Mark done</button></div>
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
