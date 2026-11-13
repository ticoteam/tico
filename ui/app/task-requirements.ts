/* ui/app/task-requirements.ts — "To leave <stage>" in a task's window (docs/pipelines-v3.md S4): what the task needs
   before it leaves its stage, each with ✓ or ○, the line and whose it is (a stage's answer, once given, its value); × takes back an ask you may withdraw, and +
   asks someone for an answer or an OK (POST /v2/tasks/{id}/asks). A task with nothing to leave by shows only +.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** The task fields the section reads (GET /v2/tasks/{id}). */
/** What the section reads; who asked, the title, body and tags feed Ask someone's suggestions when the task has them. */
type TaskReqTask = Pick<TaskV3, 'id' | 'status' | 'version' | 'to_leave' | 'stage'> & Partial<Pick<TaskV3, 'requester_id' | 'title' | 'body' | 'tags'>>;

const taskStageOf = (t: Pick<TaskV3, 'stage'> | null | undefined): TaskStageV3 | null =>
  t?.stage && typeof t.stage === 'object' && 'name' in t.stage ? t.stage as unknown as TaskStageV3 : null;

function taskReqRowHTML(req: TaskReq): string {
  // A stage's answer, once given, shows its value where whose it is would be.
  const value = req.typed && req.met ? req.answer?.value ?? req.standing : '';
  const who = value ? `<span class="task-req-val">${esc(value)}</span>`
    : req.who ? `${actorFace(req.who, 16)}<span class="task-req-who">${esc(actorLabel(req.who))}</span>` : '';
  const drop = req.may.includes('withdraw') && !req.met
    ? `<button type="button" class="x" data-req-withdraw="${esc(req.requirement_id)}" aria-label="Take back: ${esc(req.line)}" title="Take back">×</button>` : '';
  return `<div class="task-req" data-req-id="${esc(req.requirement_id)}" data-state="${req.met ? 'met' : 'missing'}">
    <span class="task-req-dot" aria-label="${req.met ? 'Met' : 'Not yet'}">${req.met ? '✓' : '○'}</span>
    <span class="task-req-name">${esc(req.line)}</span>
    <span class="task-req-state">${who}</span>
    <span class="task-req-end">${drop}</span>
  </div>`;
}

/** The section, or '' for a finished task. */
function taskRequirementsHTML(t: TaskReqTask): string {
  if (t.status !== 'open') return '';
  const stage = taskStageOf(t), reqs = t.to_leave || [];
  const head = stage ? `To leave ${stage.name}` : 'To finish';
  return `<section class="tsec task-requirements${reqs.length ? '' : ' tsec-empty'}" data-task-reqs="${esc(t.id)}" aria-label="${esc(head)}">
    <h3 class="rail-h">${esc(head)}<button type="button" class="prop-add" data-req-ask aria-label="Ask someone" title="Ask someone">${TL_ICON.plus}</button></h3>
    ${reqs.map(taskReqRowHTML).join('')}
    <form class="task-req-ask" data-req-ask-form hidden>
      ${giveToFieldHTML({name: 'to', label: 'Ask', placeholder: 'Who', task: t})}
      <input name="line" maxlength="500" autocomplete="off" placeholder="What to ask" aria-label="What to ask" required>
      <label class="task-req-ok"><input type="checkbox" name="ok"> OK</label>
      <button type="submit" class="primary">Ask</button>
      <span class="task-req-err" role="status"></span>
    </form></section>`;
}

/** Wires the section of a drawn task; `redraw` draws the task the server answered. */
function taskRequirementsBind(root: ParentNode, t: TaskReqTask, redraw: (task: TaskV3) => void): void {
  const section = root.querySelector<HTMLElement>('[data-task-reqs]');
  if (!section) return;
  const form = section.querySelector<HTMLFormElement>('[data-req-ask-form]');
  const err = section.querySelector<HTMLElement>('.task-req-err');
  const fail = (e: unknown) => { if (err) err.textContent = errorMessage(e, 'Not sent'); else toast(errorMessage(e, ''), true); };
  section.onclick = ev => {
    const el = eventElement(ev);
    if (el?.closest('[data-req-ask]') && form) {
      form.hidden = !form.hidden;
      if (!form.hidden) form.querySelector<HTMLButtonElement>('button[data-give-to]')?.focus();
      return;
    }
    const drop = el?.closest<HTMLButtonElement>('[data-req-withdraw]');
    if (drop) {
      drop.disabled = true;
      taskWithdrawAsk(String(t.id), drop.dataset.reqWithdraw ?? '').then(r => redraw(r.task), e => { drop.disabled = false; fail(e); });
    }
  };
  if (form) form.onsubmit = ev => {
    ev.preventDefault();
    const data = new FormData(form), to = String(data.get('to') || ''), line = String(data.get('line') || '').trim();
    if (!to && line) { giveToFieldOpen(form.querySelector<HTMLButtonElement>('button[data-give-to]')!); return; }
    if (!to || !line) return;
    const actor = to.includes(':') ? to : 'bot:' + to;
    const send = form.querySelector<HTMLButtonElement>('button[type=submit]');
    if (send) send.disabled = true;
    taskAsk(String(t.id), {to: actor, line, kind: data.get('ok') ? 'ok' : 'answer'}, t.version)
      .then(r => redraw(r.task), e => { if (send) send.disabled = false; fail(e); });
  };
}
