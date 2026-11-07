/* ui/app/pipeline-review.ts — Review mode: the items a reviewer works through one at a time, across the pipeline's
   stages. Each item shows its attachments, why it is the reviewer's, and what the bot found. Approve (⌘/Ctrl+⏎ only
   while the note has focus) moves the ticket on, with a 10-second Undo. Send back and Message manager take a note.
   On a phone the same layout stacks. Classic script: its globals are shared with the other files under ui/app/,
   loaded in the order index.html lists them. */
'use strict';

const PIPELINE_UNDO_MS = 10000;
let pipelineReviewState: {
  pipelineId: string;
  items: PipelineReviewItem[];
  index: number;
  tab: number;
  busy: boolean;
  message: string;
  /** What is typed in the note box; kept across redraws of the same item. */
  note: string;
  undo: { decisionId: string; item: PipelineReviewItem; index: number; timer: number } | null;
} | null = null;

/** The "▶ N for you" count for one pipeline's board: the tickets whose requirement waits on this person. */
function pipelineForYouCount(items: { task: { requirements?: PipelineRequirement[] | null } }[], me: string): number {
  return items.reduce((n, item) => n + (item.task.requirements ?? []).filter(req => req.state === 'waiting' && req.next_actor === me).length, 0);
}

/** The ▶ button for a count (the pipeline's, or one stage's when `stage` names it), or '' when nothing waits on this person. */
function pipelineForYouHTML(count: number, pipelineId: string, stage = ''): string {
  if (!count) return '';
  const where = stage ? `in ${stage}` : 'waiting on you';
  return `<button type="button" class="pipe-play" data-pipeline-review="${esc(pipelineId)}"${stage ? ` data-review-stage="${esc(stage)}"` : ''}`
    + ` aria-label="${esc(`Review ${count} ${where}`)}"><span class="pipe-play-tri" aria-hidden="true"></span>${count} for you</button>`;
}

function pipelineReviewClose(): void {
  if (pipelineReviewState?.undo) window.clearTimeout(pipelineReviewState.undo.timer);
  pipelineReviewState = null;
  document.querySelector('.pipe-review')?.remove();
  document.removeEventListener('keydown', pipelineReviewKey, true);
}

/** Opens review mode for a pipeline's queue, or for one stage of it. An empty queue says so and offers nothing to approve. */
async function pipelineReviewOpen(pipelineId: string, stage = ''): Promise<void> {
  pipelineReviewClose();
  let items: PipelineReviewItem[] = [];
  let failed = '';
  try { items = (await pipelineReviewQueue(pipelineId)).items.filter(item => !stage || item.stage_name === stage); }
  catch (error) { failed = error instanceof Error ? error.message : 'The queue did not load.'; }
  pipelineReviewState = { pipelineId, items, index: 0, tab: 0, busy: false, message: failed, note: '', undo: null };
  const host = document.createElement('div');
  host.className = 'pipe-review';
  host.setAttribute('role', 'dialog');
  host.setAttribute('aria-modal', 'true');
  host.setAttribute('aria-label', 'Review');
  document.body.append(host);
  document.addEventListener('keydown', pipelineReviewKey, true);
  pipelineReviewRender();
  host.querySelector<HTMLButtonElement>('[data-review-exit]')?.focus();
}

function pipelineReviewRender(): void {
  const host = document.querySelector<HTMLElement>('.pipe-review');
  const state = pipelineReviewState;
  if (!host || !state) return;
  const total = state.items.length;
  const item = state.items[state.index];
  const undo = state.undo ? `<div class="pipe-review-toast" role="status">Approved · <button type="button" class="linkish" data-review-undo>Undo</button></div>` : '';
  if (!item) {
    host.innerHTML = `<header class="pipe-review-head"><span class="pipe-review-count">${state.message ? 'Queue not loaded' : 'For you · none'}</span>
      <span class="spacer"></span><button type="button" class="ghost" data-review-exit>Exit <kbd>Esc</kbd></button></header>
      <p class="pipe-review-empty" role="status">${esc(state.message || 'Nothing is waiting on you.')}</p>${undo}`;
    return;
  }
  const attachments = item.attachments;
  const tab = attachments[state.tab] ?? attachments[0];
  const preview = !tab ? '<p class="muted">Nothing attached yet.</p>'
    : tab.kind === 'image' && tab.url ? `<img class="pipe-review-img" src="${esc(tab.url)}" alt="${esc(tab.label)}">`
    : `<div class="pipe-review-card"><strong>${esc(tab.label)}</strong>${tab.detail ? `<span class="muted">${esc(tab.detail)}</span>` : ''}${tab.url ? `<a href="${esc(tab.url)}" target="_blank" rel="noopener">Open</a>` : ''}</div>`;
  const assignee = item.assignee?.name ?? 'assignee';
  host.innerHTML = `<header class="pipe-review-head">
      <button type="button" class="ghost" data-review-exit aria-label="Exit review">Exit <kbd>Esc</kbd></button>
      <span class="pipe-review-count">For you · ${state.index + 1} of ${total}</span>
      <span class="muted">${esc([item.stage_name, item.task_title].filter(Boolean).join(' · '))}</span>
      <span class="spacer"></span>
      <button type="button" class="ghost" data-review-prev aria-label="Previous" ${state.index === 0 ? 'disabled' : ''}>‹</button>
      <button type="button" class="ghost" data-review-next aria-label="Next" ${state.index >= total - 1 ? 'disabled' : ''}>›</button>
    </header>
    <div class="pipe-review-body">
      <section class="pipe-review-main">
        <nav class="pipe-review-tabs" aria-label="Attachments">${attachments.map((a, i) =>
          `<button type="button" class="pipe-review-tab${i === state.tab ? ' on' : ''}" data-review-tab="${i}">${esc(a.label)}</button>`).join('')}</nav>
        <div class="pipe-review-view">${preview}</div>
        ${item.bot_note ? `<p class="pipe-review-bot">Bot: ${esc(item.bot_note)}</p>` : ''}
      </section>
      <aside class="pipe-review-side">
        <h2 class="pipe-h">Why you</h2>
        <p>${esc(item.why)}</p>
        <label class="pipe-review-note-label">Note
          <textarea name="note" rows="3" placeholder="Note">${esc(state.note)}</textarea></label>
        ${state.message ? `<p class="pipe-review-err" role="alert">${esc(state.message)}</p>` : ''}
        <div class="pipe-review-sendback">
          <button type="button" class="btn" data-review-send="assignee">Message ${esc(assignee)}</button>
          <button type="button" class="btn" data-review-send="manager">Message manager</button>
        </div>
        <button type="button" class="pipe-review-approve" data-review-approve ${state.busy ? 'disabled' : ''}>Approve <kbd class="pipe-review-kbd">⌘⏎</kbd></button>
        <p class="muted pipe-review-moves">${item.moves_to ? `Last requirement · moves ${esc(item.task_id)} to ${esc(item.moves_to)}` : ''}</p>
      </aside>
    </div>
    ${undo}`;
}

function pipelineReviewNoteValue(): string {
  return pipelineReviewState?.note.trim() ?? '';
}

/** The keys review mode takes: Esc exits, ‹ › move. ⌘/Ctrl+⏎ approves only while the note box has focus. */
function pipelineReviewKey(event: KeyboardEvent): void {
  if (!pipelineReviewState) return;
  const noteFocused = event.target instanceof HTMLTextAreaElement && event.target.name === 'note';
  if (event.key === 'Escape') { event.preventDefault(); pipelineReviewClose(); return; }
  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey) && noteFocused) { event.preventDefault(); void pipelineReviewApprove(); }
}

function pipelineReviewStep(delta: number): void {
  const state = pipelineReviewState;
  if (!state) return;
  const next = Math.min(state.items.length - 1, Math.max(0, state.index + delta));
  if (next === state.index) return;
  state.index = next; state.tab = 0; state.message = ''; state.note = '';
  pipelineReviewRender();
}

/** Approve the item shown. A refusal because the work changed reloads the queue rather than approving stale work. */
async function pipelineReviewApprove(): Promise<void> {
  const state = pipelineReviewState;
  const item = state?.items[state.index];
  if (!state || !item || state.busy) return;
  state.busy = true; state.message = '';
  pipelineReviewRender();
  try {
    const result = await pipelineApprove(item, pipelineReviewNoteValue());
    if (!pipelineReviewState) return;
    const at = state.index;
    state.items = state.items.filter(other => other !== item);
    state.index = Math.min(state.index, Math.max(0, state.items.length - 1));
    state.tab = 0; state.note = '';
    const timer = window.setTimeout(() => { if (pipelineReviewState) { pipelineReviewState.undo = null; pipelineReviewRender(); } }, PIPELINE_UNDO_MS);
    state.undo = { decisionId: result.decision_id, item, index: at, timer };
  } catch (error) {
    if (!pipelineReviewState) return;
    const status = (error as ApiFailure).status;
    state.message = status === 409
      ? 'This changed since it was shown. The queue is reloaded.'
      : error instanceof Error ? error.message : 'Not approved.';
    if (status === 409) { await pipelineReviewReload(); return; }
  } finally {
    if (pipelineReviewState) pipelineReviewState.busy = false;
    pipelineReviewRender();
  }
}

/** Takes back the last approval within its 10 seconds: the item returns to the queue where it was. */
async function pipelineReviewUndo(): Promise<void> {
  const state = pipelineReviewState;
  const undo = state?.undo;
  if (!state || !undo) return;
  window.clearTimeout(undo.timer);
  state.undo = null;
  try {
    await pipelineUndo(undo.decisionId);
    state.items.splice(undo.index, 0, undo.item);
    state.index = undo.index; state.tab = 0; state.note = '';
    state.message = '';
  } catch (error) {
    state.message = error instanceof Error ? error.message : 'Not undone.';
  }
  pipelineReviewRender();
}

/** Sends the item back to its assignee or to the manager with the note. Nothing is approved. */
async function pipelineReviewSend(to: 'assignee' | 'manager'): Promise<void> {
  const state = pipelineReviewState;
  const item = state?.items[state.index];
  const note = pipelineReviewNoteValue();
  if (!state || !item || state.busy) return;
  if (!note) { state.message = 'Write a note first.'; pipelineReviewRender(); return; }
  state.busy = true; state.message = '';
  try {
    if (to === 'assignee') await pipelineSendBack(item.task_id, item.requirement_id, note);
    else await pipelineMessageManager(item.task_id, item.requirement_id, note);
    if (!pipelineReviewState) return;
    state.items = state.items.filter(other => other !== item);
    state.index = Math.min(state.index, Math.max(0, state.items.length - 1));
    state.tab = 0; state.note = '';
  } catch (error) {
    if (pipelineReviewState) state.message = error instanceof Error ? error.message : 'Not sent.';
  } finally {
    if (pipelineReviewState) pipelineReviewState.busy = false;
    pipelineReviewRender();
  }
}

async function pipelineReviewReload(): Promise<void> {
  const state = pipelineReviewState;
  if (!state) return;
  try { state.items = (await pipelineReviewQueue(state.pipelineId)).items; state.index = 0; state.tab = 0; state.note = ''; }
  catch (error) { state.message = error instanceof Error ? error.message : 'The queue did not load.'; }
  if (pipelineReviewState) pipelineReviewRender();
}

document.addEventListener('input', (event: Event) => {
  if (pipelineReviewState && event.target instanceof HTMLTextAreaElement && event.target.name === 'note') pipelineReviewState.note = event.target.value;
});
document.addEventListener('click', (event: MouseEvent) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const open = target.closest<HTMLElement>('[data-pipeline-review]');
  if (open?.dataset.pipelineReview) { void pipelineReviewOpen(open.dataset.pipelineReview, open.dataset.reviewStage || ''); return; }
  if (!target.closest('.pipe-review')) return;
  if (target.closest('[data-review-exit]')) return pipelineReviewClose();
  if (target.closest('[data-review-prev]')) return pipelineReviewStep(-1);
  if (target.closest('[data-review-next]')) return pipelineReviewStep(1);
  if (target.closest('[data-review-approve]')) return void pipelineReviewApprove();
  if (target.closest('[data-review-undo]')) return void pipelineReviewUndo();
  const tab = target.closest<HTMLElement>('[data-review-tab]');
  if (tab && pipelineReviewState) { pipelineReviewState.tab = Number(tab.dataset.reviewTab) || 0; pipelineReviewRender(); return; }
  const send = target.closest<HTMLElement>('[data-review-send]');
  if (send) void pipelineReviewSend(send.dataset.reviewSend === 'manager' ? 'manager' : 'assignee');
});
