/* ui/app/pipeline-rail.ts — The manager's rail beside a pipeline's stages (docs/pipelines-v3.md S10), on a desktop.
   Closed until the chat icon in the page's header opens it (open or closed is kept in this browser); open, it is a
   column at the right of the page, as tall as the window. It is the chat with the manager and nothing else: on top the
   manager's face and name and a button that closes it, below the ordinary direct thread (thread-view.ts threadMount),
   never a chat of its own, filling the column with its box at the bottom. Task links in its messages open the task as
   in any chat (chat.ts). Late tasks show on the board's cards and stage headers; the manager's log stays in the API
   (GET /api/v2/pipelines/{id}/log) for bots and the CLI, and no screen reads it.
   The page (tasks-page.ts) gives the host through the pipeline view's rail hook (task-pipelines.ts).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

interface PrState { host: HTMLElement; pipelineId: string; manager: string; chat: ThvView | null }

let PR: PrState | null = null;
const PR_OPEN_KEY = 'tico.pipeline.rail_open';
const PR_HIDE_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';

/** Closed unless this browser opened it. */
function prOpen(): boolean {
  try { return localStorage.getItem(PR_OPEN_KEY) === '1'; } catch { return false; }
}
function prKeepOpen(open: boolean): void {
  try { localStorage.setItem(PR_OPEN_KEY, open ? '1' : '0'); } catch { /* storage blocked: not kept */ }
}
/** The page's column for the rail: there only while the rail is open. */
function prPlace(host: HTMLElement, open: boolean): void {
  host.hidden = !open;
  host.closest('.tasks-page')?.classList.toggle('has-rail', open);
}

/** A manager's actor id from its reference, stored with or without bot:. */
const prActor = (ref: string): string => 'bot:' + ref.replace(/^bot:/, '');

function prDraw(state: PrState): void {
  if (PR !== state) return;
  const host = state.host, open = prOpen();
  host.classList.add('pm-rail');
  prPlace(host, open);
  document.getElementById('task-manager')?.setAttribute('aria-pressed', String(open));
  if (!open) return;
  let shell = host.querySelector<HTMLElement>(':scope > .pm-shell');
  if (!shell) {
    host.innerHTML = '<div class="pm-shell"><div class="pm-panel"><header class="pm-head"></header><div class="pm-chat"></div></div></div>';
    shell = host.querySelector<HTMLElement>(':scope > .pm-shell');
  }
  if (!shell) return;
  const head = shell.querySelector<HTMLElement>('.pm-head');
  if (head) {
    head.innerHTML = `<span class="pm-face">${actorFace(state.manager, 32)}</span><b class="pm-name">${esc(actorLabel(state.manager))}</b>`
      + `<button type="button" class="ghost pm-x" data-pm-hide aria-label="Close" title="Close">${PR_HIDE_ICON}</button>`;
  }
  const chat = shell.querySelector<HTMLElement>('.pm-chat');
  if (chat && !state.chat) state.chat = threadMount(chat, {dm: state.manager, card: true, header: false});
}

/** The close button; everything else in the rail is the chat's own. */
function prClick(ev: MouseEvent): void {
  const state = PR, target = eventElement(ev);
  if (!state || !target || !state.host.contains(target)) return;
  if (target.closest('[data-pm-hide]')) pipelineRailToggle(false);
}

/** The header's chat icon: opens or closes the rail of the pipeline in view. */
function pipelineRailToggle(open = !prOpen()): void {
  prKeepOpen(open);
  const state = PR;
  if (!state) return;
  if (!open) { threadUnmount(state.chat); state.chat = null; }
  prDraw(state);
}

/** Whether the pipeline has a rail here: a manager, and not a phone. The header shows the chat icon for it. */
const pipelineRailFor = (rec: PipelineRec | null | undefined): boolean =>
  !!rec?.manager_id && !window.matchMedia('(max-width:600px)').matches;

/** Mounts the rail into the page's host for a pipeline with a manager, or draws it again; a pipeline with no manager,
    or a phone, has no rail. */
function pipelineRailSync(host: HTMLElement, rec: PipelineRec): void {
  if (!rec.manager_id || !pipelineRailFor(rec)) {
    if (PR?.host === host) pipelineRailLeave();
    prPlace(host, false);
    return;
  }
  const manager = prActor(rec.manager_id);
  if (!PR || PR.host !== host || PR.pipelineId !== rec.id || PR.manager !== manager || !host.isConnected) {
    pipelineRailLeave();
    host.replaceChildren();
    PR = {host, pipelineId: rec.id, manager, chat: null};
    host.addEventListener('click', prClick);
  }
  prDraw(PR);
}

/** The rail is gone (another pipeline, another page): its chat goes with it. */
function pipelineRailLeave(): void {
  const state = PR;
  if (!state) return;
  PR = null;
  state.host.removeEventListener('click', prClick);
  threadUnmount(state.chat);
  state.host.closest('.tasks-page')?.classList.remove('has-rail');
}
