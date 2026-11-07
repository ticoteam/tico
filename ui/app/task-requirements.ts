/* ui/app/task-requirements.ts — A ticket's stage requirements in its detail: what each asks for, what stands on the
   ticket, and who may mark one met (its ⋯ menu asks for a reason). A ticket with no requirements shows nothing. Classic
   script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const TASK_REQ_STATE_WORDS: Record<PipelineReqState, string> = {
  met: 'Met', waiting: 'Waiting', out_of_date: 'Out of date', failed: 'Not passed', missing: 'Missing',
};

/** The requirements section of the ticket, or '' when its stage has none. */
function taskRequirementsHTML(task: { id: string | number; requirements?: PipelineRequirement[] | null }): string {
  const reqs = task.requirements ?? [];
  if (!reqs.length) return '';
  const met = reqs.filter(req => req.state === 'met').length;
  const rows = reqs.map(req => {
    const more = req.can_mark_met && req.state !== 'met'
      ? `<button type="button" class="ghost task-req-more" data-req-more aria-haspopup="dialog" aria-expanded="false"
           aria-label="${esc(`More for ${req.name}`)}" title="More">⋯</button>
         <form class="task-req-mark" data-req-mark hidden>
           <label>Why <input name="why" maxlength="500" autocomplete="off" placeholder="Why"></label>
           <button type="submit" class="primary">Mark met</button>
           <span class="task-req-err" role="status"></span>
         </form>`
      : '';
    return `<div class="task-req" data-req-id="${esc(req.id)}" data-state="${esc(req.state)}">
      <span class="pdot pdot-${esc(req.state)}" aria-hidden="true"></span>
      <span class="task-req-name">${esc(req.name)}${req.detail ? ` <span class="muted">${esc(req.detail)}</span>` : ''}</span>
      <span class="task-req-state">${esc(TASK_REQ_STATE_WORDS[req.state] || req.state)}${req.next_actor && req.state !== 'met' ? ` · ${esc(actorLabel(req.next_actor))}` : ''}</span>
      <span class="task-req-end">${more}</span>
    </div>`;
  }).join('');
  return `<section class="tsec task-requirements" data-task-id="${esc(String(task.id))}" aria-label="Requirements">
    <h3 class="rail-h">Requirements · ${met} of ${reqs.length}</h3>${rows}</section>`;
}

/** Shows the Mark met form under its ⋯ button, and closes whichever form was open. */
function taskReqMenuToggle(row: HTMLElement, open?: boolean): void {
  const form = row.querySelector<HTMLFormElement>('[data-req-mark]');
  const button = row.querySelector<HTMLButtonElement>('[data-req-more]');
  if (!form || !button) return;
  const show = open ?? form.hidden;
  document.querySelectorAll<HTMLFormElement>('.task-requirements [data-req-mark]').forEach(other => {
    if (other !== form && !other.hidden) { other.hidden = true; other.closest('.task-req')?.querySelector('[data-req-more]')?.setAttribute('aria-expanded', 'false'); }
  });
  form.hidden = !show;
  button.setAttribute('aria-expanded', String(show));
  if (show) form.querySelector<HTMLInputElement>('input[name=why]')?.focus();
}

// Marking met writes one override decision; the row turns met in place, and the ticket's dots follow on the next read.
async function taskReqMarkMet(form: HTMLFormElement): Promise<void> {
  const row = form.closest<HTMLElement>('.task-req');
  const section = form.closest<HTMLElement>('.task-requirements');
  const taskId = section?.dataset.taskId ?? '';
  const why = form.querySelector<HTMLInputElement>('input[name=why]')?.value.trim() ?? '';
  const err = form.querySelector<HTMLElement>('.task-req-err');
  const submit = form.querySelector<HTMLButtonElement>('button[type=submit]');
  if (!row || !taskId || !why) { if (err) err.textContent = 'Say why it is met.'; return; }
  if (submit) submit.disabled = true;
  try {
    await pipelineMarkMet(taskId, row.dataset.reqId ?? '', why);
    row.dataset.state = 'met';
    row.querySelector('.pdot')?.setAttribute('class', 'pdot pdot-met');
    const state = row.querySelector<HTMLElement>('.task-req-state');
    if (state) state.textContent = TASK_REQ_STATE_WORDS.met;
    row.querySelector<HTMLElement>('.task-req-end')?.replaceChildren();
  } catch (error) {
    if (err) err.textContent = error instanceof Error ? error.message : 'Not marked met.';
    if (submit) submit.disabled = false;
  }
}

document.addEventListener('click', (event: MouseEvent) => {
  const target = event.target instanceof Element ? event.target : null;
  const more = target?.closest<HTMLElement>('[data-req-more]');
  if (more) {
    const row = more.closest<HTMLElement>('.task-req');
    if (row) taskReqMenuToggle(row);
  }
});
document.addEventListener('submit', (event: SubmitEvent) => {
  const form = event.target instanceof HTMLFormElement && event.target.matches('[data-req-mark]') ? event.target : null;
  if (!form) return;
  event.preventDefault();
  void taskReqMarkMet(form);
});
