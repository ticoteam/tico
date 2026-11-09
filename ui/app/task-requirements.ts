/* ui/app/task-requirements.ts — A ticket's stage requirements in its detail: what each asks for, what stands on the
   ticket, and who may mark one met (its ⋯ menu asks for a reason). A ticket with no requirements shows nothing. Classic
   script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** The ticket fields the section reads, as GET /v2/tasks/{id} types them (ui/types/api.d.ts Task): its id, its
    pipeline (type_id, null off a pipeline) and its requirements, the board's dots, which the schema types loosely. */
type TaskReqTicket = Pick<import('../types/api').Task, 'id' | 'type_id' | 'requirements'>;

const TASK_REQ_STATE_WORDS: Record<PipelineReqState, string> = {
  met: 'Met', waiting: 'Waiting', out_of_date: 'Out of date', failed: 'Not passed', missing: 'Missing',
};

/** What the ticket shows for one piece of evidence. Its link is the content's own address (href: a company file's pinned
    version, so a later version never replaces what was reviewed). An image shows a preview that opens it; anything else is
    a link named by what it is. The newest one of a requirement that is out of date says so. */
function taskReqAttachmentHTML(att: PipelineReqAttachment, stale: boolean): string {
  const href = att.href ?? '';
  const label = att.name || (att.source === 'link' ? 'Link' : 'File');
  const name = !href ? esc(label)
    : att.format === 'image'
      ? `<a class="task-req-thumb" href="${esc(href)}" target="_blank" rel="noopener"><img src="${esc(href)}" alt="${esc(label)}" loading="lazy"></a>`
      : `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(label)}</a>`;
  return `<li>${name}${att.created_at ? ` <span class="muted">${esc(ageShort(att.created_at))}</span>` : ''}${stale ? ' <span class="task-req-old">out of date</span>' : ''}</li>`;
}

/** The state words of a requirement on a ticket. A met one says who passed it, and under which version when the rule has
    changed since (the decision stands under the version it was made under). */
function taskReqStateText(req: PipelineRequirement): string {
  const word = TASK_REQ_STATE_WORDS[req.state] || req.state;
  if (req.state === 'met' && req.passed) {
    const who = req.passed.by ? actorLabel(req.passed.by) : '';
    const old = req.passed.version < req.passed.current ? `under v${req.passed.version}` : '';
    return [old ? `${word} ${old}` : word, who].filter(Boolean).join(' · ');
  }
  return `${word}${req.next_actor && req.state !== 'met' ? ` · ${actorLabel(req.next_actor)}` : ''}`;
}

/** The section's markup for these requirements, or '' when there are none. */
function taskRequirementsSection(taskId: string, typeId: string, reqs: PipelineRequirement[]): string {
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
    const files = req.attachments?.length
      ? `<ul class="task-req-files">${req.attachments.map((att, index) => taskReqAttachmentHTML(att, index === 0 && req.state === 'out_of_date')).join('')}</ul>`
      : '';
    return `<div class="task-req" data-req-id="${esc(req.id)}" data-state="${esc(req.state)}">
      <span class="pdot pdot-${esc(req.state)}" aria-hidden="true"></span>
      <span class="task-req-name">${esc(req.name)}${req.detail ? ` <span class="muted">${esc(req.detail)}</span>` : ''}${files}</span>
      <span class="task-req-state">${esc(taskReqStateText(req))}</span>
      <span class="task-req-end">${more}</span>
    </div>`;
  }).join('');
  const attach = reqs.filter(req => req.kind === 'attachment');
  const attachUI = attach.length
    ? `<div class="task-req-attach"><button type="button" class="ghost" data-req-attach-open aria-haspopup="menu" aria-expanded="false">Attach to ⌄</button>
        <div class="task-req-attach-menu" role="menu" hidden>${attach.map(req => `<button type="button" role="menuitem" data-req-attach="${esc(req.id)}">${esc(req.name)}</button>`).join('')}</div>
        <form class="task-req-attach-form" data-req-attach-form hidden>
          <span class="task-req-attach-for"></span>
          <label class="ghost task-req-file">File<input type="file" name="file" hidden></label>
          <input type="url" name="url" inputmode="url" autocomplete="off" placeholder="Link" aria-label="Link">
          <button type="submit" class="primary">Add</button>
          <span class="task-req-err" role="status"></span>
        </form></div>`
    : '';
  return `<section class="tsec task-requirements" data-task-id="${esc(taskId)}" data-type-id="${esc(typeId)}" aria-label="Requirements">
    <h3 class="rail-h">Requirements · ${met} of ${reqs.length}</h3>${rows}${attachUI}</section>`;
}

/** The requirements section of the ticket as the board's dots give it, or '' when its stage has none. It fills in from
    the ticket's own requirements (taskRequirementsLoad) once it is on the page. */
function taskRequirementsHTML(task: TaskReqTicket): string {
  return taskRequirementsSection(String(task.id), task.type_id ?? '', pipelineBoardDots(task.requirements ?? []));
}

/** Fills the ticket's requirements from GET /v2/tasks/{id}/requirements (what each asks for, who decided, what is
    attached). A read that fails leaves the dots' version. */
async function taskRequirementsLoad(root: ParentNode): Promise<void> {
  for (const section of root.querySelectorAll<HTMLElement>('.task-requirements[data-task-id]')) {
    const taskId = section.dataset.taskId ?? '';
    const typeId = section.dataset.typeId ?? '';
    try {
      // One read: the requirements come with their attachments, so there is no request per attachment requirement.
      const reqs = await pipelineTaskRequirements(taskId);
      const may = pipelineMayEditRules(typeId);
      for (const req of reqs) req.can_mark_met = may && req.state !== 'met';
      if (!section.isConnected) continue;
      const html = taskRequirementsSection(taskId, typeId, reqs);
      if (!html) continue;
      const box = document.createElement('div');
      box.innerHTML = html;
      const next = box.firstElementChild;
      if (next) section.replaceWith(next);
    } catch {
      // The ticket's requirements are not there to read (an older server): the dots' list stays.
    }
  }
}

/** Opens or closes the Attach to menu and its form. */
function taskReqAttachToggle(section: HTMLElement, open: boolean): void {
  const menu = section.querySelector<HTMLElement>('.task-req-attach-menu');
  if (menu) menu.hidden = !open;
  section.querySelector('[data-req-attach-open]')?.setAttribute('aria-expanded', String(open));
}

/** Attaches a file or a link as evidence for the requirement the form is open for, then reads the section again. */
async function taskReqAttachSend(form: HTMLFormElement, file: File | null): Promise<void> {
  const section = form.closest<HTMLElement>('.task-requirements');
  const taskId = section?.dataset.taskId ?? '';
  const requirementId = form.dataset.reqId ?? '';
  const url = form.querySelector<HTMLInputElement>('input[name=url]')?.value.trim() ?? '';
  const err = form.querySelector<HTMLElement>('.task-req-err');
  if (!section || !taskId || !requirementId) return;
  if (!file && !url) { if (err) err.textContent = 'Choose a file or paste a link.'; return; }
  form.querySelectorAll<HTMLButtonElement>('button').forEach(button => { button.disabled = true; });
  try {
    if (file) await pipelineAttachUpload(taskId, requirementId, file);
    else await pipelineAttachLink(taskId, requirementId, url);
    await taskRequirementsLoad(section.parentElement ?? document);
  } catch (error) {
    if (err) err.textContent = error instanceof Error ? error.message : 'Not attached.';
    form.querySelectorAll<HTMLButtonElement>('button').forEach(button => { button.disabled = false; });
  }
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
  const section = target?.closest<HTMLElement>('.task-requirements');
  if (!target || !section) return;
  if (target.closest('[data-req-attach-open]')) {
    taskReqAttachToggle(section, section.querySelector<HTMLElement>('.task-req-attach-menu')?.hidden ?? false);
    return;
  }
  const pick = target.closest<HTMLElement>('[data-req-attach]');
  const form = section.querySelector<HTMLFormElement>('[data-req-attach-form]');
  if (pick && form) {
    form.dataset.reqId = pick.dataset.reqAttach ?? '';
    const label = form.querySelector('.task-req-attach-for');
    if (label) label.textContent = pick.textContent ?? '';
    form.hidden = false;
    taskReqAttachToggle(section, false);
    form.querySelector<HTMLInputElement>('input[name=url]')?.focus();
  }
});
document.addEventListener('change', (event: Event) => {
  const input = event.target instanceof HTMLInputElement && event.target.type === 'file' ? event.target : null;
  const form = input?.closest<HTMLFormElement>('[data-req-attach-form]');
  const file = input?.files?.[0];
  if (form && file) void taskReqAttachSend(form, file);
});
document.addEventListener('submit', (event: SubmitEvent) => {
  const attach = event.target instanceof HTMLFormElement && event.target.matches('[data-req-attach-form]') ? event.target : null;
  if (attach) { event.preventDefault(); void taskReqAttachSend(attach, null); return; }
  const form = event.target instanceof HTMLFormElement && event.target.matches('[data-req-mark]') ? event.target : null;
  if (!form) return;
  event.preventDefault();
  void taskReqMarkMet(form);
});

// ----------------------------------------------------------------- a move past unmet requirements
// A rule editor whose drag was refused for its requirements (rule "requirement") may move the ticket anyway, with a
// reason. The move is sent again with override_requirements; the server writes one logged override per requirement and
// decides who may (backend/pipeline_engine.py check_move).

/** Offers Move anyway for a refused drag. True when it did (the caller shows nothing else); false for any other refusal. */
function pipelineOverrideOffer(
  state: unknown,
  task: { id: string | number; type_id?: string },
  body: Record<string, unknown>,
  fields: Record<string, unknown>,
  error: unknown,
): boolean {
  if (errorCode(error) !== 'requirement' || body.override_requirements || !body.step || !pipelineMayEditRules(task.type_id)) return false;
  document.querySelector('.pipe-override')?.remove();
  const dialog = document.createElement('dialog');
  dialog.className = 'pipe-override';
  dialog.innerHTML = `<form method="dialog"><p>${esc(errorMessage(error, 'Requirements are not met.'))}</p>
    <label>Why <input name="why" maxlength="500" autocomplete="off" required></label>
    <span class="task-req-err" role="status"></span>
    <div class="pipe-override-actions"><button type="button" class="ghost" data-override-cancel>Cancel</button><button type="submit" class="primary">Move anyway</button></div></form>`;
  document.body.append(dialog);
  const close = (): void => { dialog.close(); dialog.remove(); };
  dialog.querySelector('[data-override-cancel]')?.addEventListener('click', close);
  dialog.addEventListener('cancel', () => dialog.remove());
  dialog.querySelector('form')?.addEventListener('submit', (event: Event) => {
    event.preventDefault();
    const why = dialog.querySelector<HTMLInputElement>('input[name=why]')?.value.trim() ?? '';
    if (!why) { const err = dialog.querySelector('.task-req-err'); if (err) err.textContent = 'Say why.'; return; }
    close();
    taskBoardSave(state, task, { ...body, override_requirements: true, note: why }, fields, 'Not moved');
  });
  dialog.showModal();
  dialog.querySelector<HTMLInputElement>('input[name=why]')?.focus();
  return true;
}
