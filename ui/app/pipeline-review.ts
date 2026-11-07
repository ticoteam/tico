/* ui/app/pipeline-review.ts — Review mode: the items a reviewer works through one at a time, across the pipeline's
   stages. Each item shows its attachments, why it is the reviewer's, and what the bot found. Approve (⌘/Ctrl+⏎ only
   while the note has focus) moves the ticket on, with a 10-second Undo. Send back and Message manager take a note.
   On a phone the same layout stacks. Classic script: its globals are shared with the other files under ui/app/,
   loaded in the order index.html lists them. */
'use strict';

const PIPELINE_UNDO_MS = 10000;
/** The Exit button: "Exit Esc" on a desktop, a 44px round × on a phone (the words hide there). */
const PIPELINE_REVIEW_EXIT = '<button type="button" class="ghost pipe-review-exit" data-review-exit aria-label="Exit review">'
  + '<span class="pipe-review-exit-word">Exit <kbd>Esc</kbd></span><span class="pipe-review-x" aria-hidden="true">×</span></button>';
let pipelineReviewState: {
  pipelineId: string;
  /** The stage the review is for (a stage id), or '' for the whole pipeline's queue. Kept for every reload. */
  stage: string;
  items: PipelineReviewItem[];
  index: number;
  tab: number;
  busy: boolean;
  message: string;
  /** What is typed in the note box; kept across redraws of the same item. */
  note: string;
  /** The item (task and requirement) whose claim was taken or asked for, so each item is claimed once. */
  claimed: string;
  undo: { decisionId: string; item: PipelineReviewItem; index: number; timer: number } | null;
  /** What the last Not validated did (the follow-up it filed), shown until the next send. */
  notice: string;
} | null = null;

/** The queue rows that wait on this person, by ticket: the board dots do not say who reviews, the queue does. */
const pipelineMine = new Map<string, number>();
let pipelineMineFor = '';
let pipelineMineAt = 0;

/** The "▶ N for you" count for a set of cards: the queue rows on them that are this person's. */
function pipelineForYouCount(items: { task: { id: string } }[]): number {
  return items.reduce((n, item) => n + (pipelineMine.get(String(item.task.id)) ?? 0), 0);
}

/** Reads the queue for the pipeline on show (at most once a minute) and redraws the cards when the count moved. */
async function pipelineMineSync(pipelineId: string, force = false): Promise<void> {
  if (!pipelineId) return;
  if (!force && pipelineId === pipelineMineFor && Date.now() - pipelineMineAt < 60000) return;
  pipelineMineFor = pipelineId;
  pipelineMineAt = Date.now();
  let queue: PipelineReviewQueue;
  try { queue = await pipelineReviewQueue(pipelineId); } catch { return; }
  if (pipelineMineFor !== pipelineId) return;
  const me = myActor();
  const next = new Map<string, number>();
  for (const item of queue.items) if (item.reviewer_id === me) next.set(item.task_id, (next.get(item.task_id) ?? 0) + 1);
  const same = next.size === pipelineMine.size && [...next].every(([id, n]) => pipelineMine.get(id) === n);
  if (same) return;
  pipelineMine.clear();
  next.forEach((n, id) => pipelineMine.set(id, n));
  tasksRender(TASKS_ST);
}

/** The ▶ button for a count, or '' when nothing waits on this person. The header's (no `stage`) reads "▶ 2 for you" beside
    the pipeline picker; a stage's is `compact`: a bare "▶ 2", as tall as the count. `stage` is the stage id it opens. */
function pipelineForYouHTML(count: number, pipelineId: string, stage = '', compact = false): string {
  if (!count) return '';
  const stageAttr = stage ? ` data-review-stage="${esc(stage)}"` : '';
  const tri = '<span class="pipe-play-tri" aria-hidden="true"></span>';
  if (compact) return `<button type="button" class="pipe-play pipe-play-chip" data-pipeline-review="${esc(pipelineId)}"${stageAttr} aria-label="${count} for you">${tri}${count}</button>`;
  return `<button type="button" class="pipe-play" data-pipeline-review="${esc(pipelineId)}"${stageAttr} aria-label="${esc(`Review ${count} waiting on you`)}">${tri}<span class="pipe-play-text">${count}<span class="pipe-play-word"> for you</span></span></button>`;
}

/** Draws the header's ▶ beside the pipeline picker: on the board, the queue rows on its tickets that are this person's.
    Returns whether it changed, so the caller refits the header row. */
function pipelinePlayPaint(state: { view: string; tasks: { id: string | number }[] }): boolean {
  const slot = document.getElementById('task-play');
  if (!slot) return false;
  const type = state.view === 'board' ? (pipelineSelectedType(state) as { id: string } | undefined) : undefined;
  const count = type ? pipelineForYouCount(state.tasks.map(task => ({task: {id: String(task.id)}}))) : 0;
  const html = type && count ? pipelineForYouHTML(count, type.id) : '';
  if (slot.innerHTML === html) return false;
  slot.innerHTML = html;
  slot.hidden = !html;
  return true;
}

function pipelineReviewClose(): void {
  if (pipelineReviewState?.undo) window.clearTimeout(pipelineReviewState.undo.timer);
  pipelineReviewState = null;
  document.querySelector('.pipe-review')?.remove();
  document.removeEventListener('keydown', pipelineReviewKey, true);
}

/** The queue's items for a review: the whole pipeline's, or only those at one stage (by stage id). */
function pipelineReviewItems(items: PipelineReviewItem[], stage: string): PipelineReviewItem[] {
  return stage ? items.filter(item => item.stage_id === stage) : items;
}

/** Opens review mode for a pipeline's queue, or for one stage of it. An empty queue says so and offers nothing to approve. */
async function pipelineReviewOpen(pipelineId: string, stage = ''): Promise<void> {
  pipelineReviewClose();
  let items: PipelineReviewItem[] = [];
  let failed = '';
  try { items = pipelineReviewItems((await pipelineReviewQueue(pipelineId)).items, stage); }
  catch (error) { failed = error instanceof Error ? error.message : 'The queue did not load.'; }
  pipelineReviewState = { pipelineId, stage, items, index: 0, tab: 0, busy: false, message: failed, note: '', claimed: '', undo: null, notice: '' };
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
  const notice = state.notice ? `<p class="pipe-review-notice" role="status">${esc(state.notice)}</p>` : '';
  if (!item) {
    host.innerHTML = `<header class="pipe-review-head"><span class="pipe-review-count">${state.message ? 'Queue not loaded' : 'For you · none'}</span>
      <span class="spacer"></span>${PIPELINE_REVIEW_EXIT}</header>
      <p class="pipe-review-empty" role="status">${esc(state.message || 'Nothing is waiting on you.')}</p>${notice}${undo}`;
    return;
  }
  const attachments = item.attachments;
  const tab = attachments[state.tab] ?? attachments[0];
  const preview = !tab ? '<p class="muted">Nothing attached yet.</p>'
    : tab.kind === 'image' && tab.url ? `<img class="pipe-review-img" src="${esc(tab.url)}" alt="${esc(tab.label)}">`
    : `<div class="pipe-review-card"><strong>${esc(tab.label)}</strong>${tab.detail ? `<span class="muted">${esc(tab.detail)}</span>` : ''}${tab.url ? `<a href="${esc(tab.url)}" target="_blank" rel="noopener">Open</a>` : ''}</div>`;
  const assignee = item.assignee?.name ?? 'assignee';
  // The item on show is claimed for ten minutes (once per item); another reviewer's claim disables Approve.
  const key = `${item.task_id}:${item.requirement_id}`;
  if (state.claimed !== key) { state.claimed = key; void pipelineReviewClaimShown(); }
  host.innerHTML = `<header class="pipe-review-head">
      ${PIPELINE_REVIEW_EXIT}
      <div class="pipe-review-title"><span class="pipe-review-count">For you · ${state.index + 1} of ${total}</span>
        <span class="muted pipe-review-stage">${esc(item.stage_name || '')}${item.task_title ? `<span class="pipe-review-where"> · ${esc(item.task_title)}</span>` : ''}</span></div>
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
        <h2 class="pipe-h">${!item.reviewer_id || item.reviewer_id === myActor() ? 'Why you' : 'Why'}</h2>
        <p>${esc(item.why)}</p>
        <label class="pipe-review-note-label">Note
          <textarea name="note" rows="3" placeholder="Note">${esc(state.note)}</textarea></label>
        ${state.message ? `<p class="pipe-review-err" role="alert">${esc(state.message)}</p>` : ''}
        <div class="pipe-review-sendback">
          <button type="button" class="btn" data-review-send="assignee">Message ${esc(assignee)}</button>
          <button type="button" class="btn" data-review-send="manager">Message manager</button>
        </div>
        ${item.claim ? `<p class="pipe-review-claim" role="status">${esc(item.claim.label)}</p>` : ''}
        <button type="button" class="pipe-review-approve" data-review-approve ${state.busy || item.claim ? 'disabled' : ''}>Approve <kbd class="pipe-review-kbd">⌘⏎</kbd></button>
        ${item.can_mark_met ? '<button type="button" class="pipe-review-met" data-review-send="met">Mark met</button>' : ''}
        ${item.can_not_validate ? '<button type="button" class="pipe-review-met" data-review-not-validated>Not validated</button>' : ''}
        <p class="muted pipe-review-moves">${item.moves_to ? `Last requirement · moves ${esc(item.task_id)} to ${esc(item.moves_to)}` : ''}</p>
        ${notice}
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

/** Takes the claim on the item on show. Another reviewer's claim (409) stays on the item: Approve waits and the item names them. */
async function pipelineReviewClaimShown(): Promise<void> {
  const state = pipelineReviewState;
  const item = state?.items[state.index];
  if (!state || !item || item.claim) return;
  try { await pipelineClaim(item.task_id, item.requirement_id); }
  catch (error) {
    const failure = error as ApiFailure & { body?: { error?: { claimed_by?: string } } };
    if (failure.status !== 409) return;
    item.claim = { by: failure.body?.error?.claimed_by ?? '', label: error instanceof Error && error.message ? error.message : 'Someone is reviewing' };
  }
  if (pipelineReviewState?.items[pipelineReviewState.index] === item) pipelineReviewRender();
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
  if (!state || !item || state.busy || item.claim) return;
  state.busy = true; state.message = '';
  pipelineReviewRender();
  try {
    const result = await pipelineApprove(item, pipelineReviewNoteValue());
    void pipelineMineSync(state.pipelineId, true);
    if (!pipelineReviewState) return;
    const at = state.index;
    state.items = state.items.filter(other => other !== item);
    state.index = Math.min(state.index, Math.max(0, state.items.length - 1));
    state.tab = 0; state.note = '';
    // Each approval owns its own Undo: a newer approval replaces this toast, so this one's timer must not clear it.
    if (state.undo) window.clearTimeout(state.undo.timer);
    const decisionId = result.decision_id;
    const timer = window.setTimeout(() => {
      if (pipelineReviewState?.undo?.decisionId === decisionId) { pipelineReviewState.undo = null; pipelineReviewRender(); }
    }, PIPELINE_UNDO_MS);
    state.undo = { decisionId, item, index: at, timer };
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
    void pipelineMineSync(state.pipelineId, true);
    state.items.splice(undo.index, 0, undo.item);
    state.index = undo.index; state.tab = 0; state.note = '';
    state.message = '';
  } catch (error) {
    state.message = error instanceof Error ? error.message : 'Not undone.';
  }
  pipelineReviewRender();
}

/** Validate's "Not validated": the note says why; the item leaves the queue and a follow-up is filed. */
async function pipelineReviewNotValidated(): Promise<void> {
  const state = pipelineReviewState;
  const item = state?.items[state.index];
  const note = pipelineReviewNoteValue();
  if (!state || !item || state.busy) return;
  if (!note) { state.message = 'Write a note first.'; pipelineReviewRender(); return; }
  state.busy = true; state.message = ''; state.notice = '';
  try {
    const res = await pipelineNotValidated(item, note);
    void pipelineMineSync(state.pipelineId, true);
    if (!pipelineReviewState) return;
    state.items = state.items.filter(other => other !== item);
    state.index = Math.min(state.index, Math.max(0, state.items.length - 1));
    state.tab = 0; state.note = '';
    state.notice = `Back to ${res.moved_back_to ?? 'In development'} · follow-up ${res.follow_up ?? ''} filed`;
  } catch (error) {
    if (pipelineReviewState) state.message = error instanceof Error ? error.message : 'Not sent.';
  } finally {
    if (pipelineReviewState) pipelineReviewState.busy = false;
    pipelineReviewRender();
  }
}

/** Sends the item back to its assignee or to the manager with the note. Nothing is approved. */
async function pipelineReviewSend(to: 'assignee' | 'manager' | 'met'): Promise<void> {
  const state = pipelineReviewState;
  const item = state?.items[state.index];
  const note = pipelineReviewNoteValue();
  if (!state || !item || state.busy) return;
  if (!note) { state.message = 'Write a note first.'; pipelineReviewRender(); return; }
  state.busy = true; state.message = ''; state.notice = '';
  try {
    if (to === 'assignee') await pipelineSendBack(item.task_id, item.requirement_id, note);
    else if (to === 'met') await pipelineMarkMet(item.task_id, item.requirement_id, note);
    else await pipelineMessageManager(item.task_id, item.requirement_id, note);
    void pipelineMineSync(state.pipelineId, true);
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
  try { state.items = pipelineReviewItems((await pipelineReviewQueue(state.pipelineId)).items, state.stage); state.index = 0; state.tab = 0; state.note = ''; }
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
  if (target.closest('[data-review-not-validated]')) return void pipelineReviewNotValidated();
  const tab = target.closest<HTMLElement>('[data-review-tab]');
  if (tab && pipelineReviewState) { pipelineReviewState.tab = Number(tab.dataset.reviewTab) || 0; pipelineReviewRender(); return; }
  const send = target.closest<HTMLElement>('[data-review-send]');
  if (send) void pipelineReviewSend(send.dataset.reviewSend === 'manager' ? 'manager' : send.dataset.reviewSend === 'met' ? 'met' : 'assignee');
});
