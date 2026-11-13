/* ui/app/pipeline-ask.ts — The Ask bar on a pipeline's board, on a phone (spec "Talk to a pipeline's bots from the
   board", v4). Above the bottom bar: what goes with the message ("Product › Spec · 3 tasks on screen"), whom it goes to
   (the pipeline's manager, picked first, or the open stage's holder) and the box, "Ask Product Manager…".
   Sent, the message goes to that bot's direct chat (the same chat as on its page, never a chat of its own) and carries
   `refs.board`: the pipeline, the open stage and the ids of the tasks the tab shows, search and filter applied, at most
   PA_MAX_IDS of them (`more` counts the rest). The bot reads it as the message's context; the chat shows it as one card
   under the message (thread-view.ts thvBoardHTML). The chat opens as a sheet over the board, the chat alone (no Log, no
   Tasks): ✕, Escape or a swipe down closes it, and the board is where it was.
   A stage held by "whoever the task is for", or by no one, has no second button; a pipeline with no manager has only
   the second; where both are the same bot it shows once; with neither there is no bar.
   The page (tasks-page.ts) gives the host through the pipeline view's `ask` hook (task-pipelines.ts); the view paints
   the bar again when its tab changes (pvDraw). Desktop keeps the manager's rail (pipeline-rail.ts) and has no bar.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type PaTo = 'manager' | 'stage';
interface PaBoard { pipeline_id: string; pipeline: string; stage_id: string; stage: string; task_ids: string[]; more: number }
interface PaState { host: HTMLElement; rec: PipelineRec; stage: string; to: PaTo; small: boolean; lastY: number }

let PA: PaState | null = null;
// `byKey`: Escape put the sheet away, so the bar takes the focus back; after ✕ or a swipe it does not, since on a phone
// that would raise the keyboard over the board the person just went back to.
let PA_SHEET: {dialog: HTMLDialogElement; chat: ThvView | null; byKey: boolean} | null = null;
const PA_MAX_IDS = 50;
const PA_STAGE_HOLDER = /^(bot|human):./;

/** The bar's two choices on the open stage: the pipeline's manager and the stage's holder, '' where there is none (the
    holder is '' when it is the manager too, so that one shows once). */
function paTargets(rec: PipelineRec, stage: PipelineStageRec | undefined): {manager: string; holder: string} {
  const manager = rec.manager_id ? prActor(rec.manager_id) : '';
  const held = String(stage?.owner || '');
  const holder = PA_STAGE_HOLDER.test(held) && held !== manager ? held : '';
  return {manager, holder};
}

/** The stage the board shows and the tasks on its tab, as the view drew them (task-pipelines.ts). */
function paOnScreen(state: PaState): {stage: PipelineStageRec | undefined; tasks: TaskAny[]} {
  const view = PV && PV.rec.id === state.rec.id ? PV : null;
  const stage = state.rec.stages.find(s => s.id === (view?.stage || state.stage)) ?? state.rec.stages[0];
  return {stage, tasks: view && stage?.id ? pvInStage(view, stage.id) : []};
}

/** Whom a message goes to now: the choice made, or the one there is. */
function paWho(state: PaState): string {
  const {manager, holder} = paTargets(state.rec, paOnScreen(state).stage);
  return state.to === 'stage' && holder ? holder : manager || holder;
}

/** What a message from the bar carries: the pipeline, the open stage and the tasks on screen. */
function paBoard(state: PaState): PaBoard {
  const {stage, tasks} = paOnScreen(state);
  const ids = tasks.map(t => String(t.id));
  return {pipeline_id: state.rec.id, pipeline: state.rec.name, stage_id: stage?.id ?? '', stage: stage?.name ?? '',
    task_ids: ids.slice(0, PA_MAX_IDS), more: Math.max(0, ids.length - PA_MAX_IDS)};
}

const paTasksWords = (n: number) => `${n} ${n === 1 ? 'task' : 'tasks'} on screen`;

function paToHTML(state: PaState): string {
  const {stage} = paOnScreen(state), {manager, holder} = paTargets(state.rec, stage), who = paWho(state);
  const button = (to: PaTo, actor: string, label: string) => {
    const on = who === actor;
    return `<button type="button" role="radio" class="pa-to-b${on ? ' on' : ''}" data-pa-to="${to}" aria-checked="${on}" title="${esc(label)}">`
      + `<span class="pa-face">${actorFace(actor, 20)}</span><span class="pa-to-l">${esc(label)}</span></button>`;
  };
  return (manager ? button('manager', manager, actorLabel(manager)) : '')
    + (holder ? button('stage', holder, `${stage?.name ?? ''} · ${actorLabel(holder)}`) : '');
}

/** Draws the bar's context line and its choices again; what is typed in the box stays. */
function pipelineAskPaint(): void {
  const state = PA;
  if (!state || !state.host.isConnected) return;
  const {stage, tasks} = paOnScreen(state);
  if (stage?.id && stage.id !== state.stage) { state.stage = stage.id; state.to = 'manager'; }
  const who = paWho(state);
  state.host.hidden = !who;
  state.host.closest('.tasks-page')?.classList.toggle('has-ask', !!who);
  if (!who) return;
  if (!state.host.querySelector('.pa-bar')) {
    state.host.innerHTML = `<div class="pa-bar" role="region" aria-label="Ask">
      <div class="pa-ctx"><span class="pa-where"></span><span class="pa-n tnum"></span></div>
      <div class="pa-to" role="radiogroup" aria-label="Ask whom"></div>
      <form class="pa-form"><textarea class="pa-text" rows="1" maxlength="4000" required autocomplete="off"></textarea>
        <button type="submit" class="pa-send" aria-label="Send" title="Send">${ICON_SEND}</button></form></div>`;
  }
  state.host.classList.toggle('pa-small', state.small);
  const where = state.host.querySelector<HTMLElement>('.pa-where'), n = state.host.querySelector<HTMLElement>('.pa-n');
  if (where) where.textContent = stage ? `${state.rec.name} › ${stage.name}` : state.rec.name;
  if (n) n.textContent = ` · ${paTasksWords(tasks.length)}`;
  const to = state.host.querySelector<HTMLElement>('.pa-to');
  const html = paToHTML(state);
  if (to && to.dataset['html'] !== html) { to.dataset['html'] = html; to.innerHTML = html; }
  const box = state.host.querySelector<HTMLTextAreaElement>('.pa-text');
  const words = `Ask ${actorLabel(who)}…`;
  if (box && box.placeholder !== words) { box.placeholder = words; box.setAttribute('aria-label', words.slice(0, -1)); }
}

function paClick(ev: MouseEvent): void {
  const state = PA, b = eventElement(ev)?.closest<HTMLElement>('[data-pa-to]');
  if (!state || !b) return;
  state.to = b.dataset['paTo'] === 'stage' ? 'stage' : 'manager';
  pipelineAskPaint();
  state.host.querySelector<HTMLTextAreaElement>('.pa-text')?.focus();
}

function paSubmit(ev: SubmitEvent): void {
  ev.preventDefault();
  const state = PA, box = state?.host.querySelector<HTMLTextAreaElement>('.pa-text');
  const text = box?.value.trim() ?? '';
  if (!state || !box || !text) return;
  const who = paWho(state);
  if (!who) return;
  box.value = ''; box.style.height = '';
  box.blur();
  void paAsk(who, text, paBoard(state));
}

/** Return sends on a computer; on a phone Return is a new line, as in every chat box. */
function paKey(ev: KeyboardEvent): void {
  const box = eventElement(ev);
  if (!(box instanceof HTMLTextAreaElement) || ev.key !== 'Enter' || ev.isComposing) return;
  if (ev.metaKey || ev.ctrlKey || (!ev.shiftKey && !touchKeyboard())) { ev.preventDefault(); box.form?.requestSubmit(); }
}

function paInput(ev: Event): void {
  const box = eventElement(ev);
  if (!(box instanceof HTMLTextAreaElement)) return;
  box.style.height = 'auto'; box.style.height = Math.min(box.scrollHeight, 120) + 'px';
}

/** The list scrolling down shrinks the bar to its box; scrolling up, or the box taking the focus, shows it whole. */
function paScroll(): void {
  const state = PA;
  if (!state) return;
  const y = window.scrollY, by = y - state.lastY;
  if (Math.abs(by) < 8) return;
  state.lastY = y;
  const small = by > 0 && y > 40 && !state.host.contains(document.activeElement);
  if (small !== state.small) { state.small = small; state.host.classList.toggle('pa-small', small); }
}
function paFocus(): void {
  const state = PA;
  if (state?.small) { state.small = false; state.host.classList.remove('pa-small'); }
}

/** The sheet with the bot's chat, over the board: its face and name, ✕, then the chat alone. */
function paSheetOpen(who: string): ThvView | null {
  if (PA_SHEET?.chat && PA_SHEET.chat.opts.dm === who && PA_SHEET.dialog.open) return PA_SHEET.chat;
  paSheetClose();
  const dialog = document.createElement('dialog');
  dialog.className = 'pa-sheet';
  dialog.setAttribute('aria-label', `Chat with ${actorLabel(who)}`);
  dialog.innerHTML = `<header class="pa-head"><span class="pa-grip" aria-hidden="true"></span>`
    + `<span class="pa-face">${actorFace(who, 28)}</span><b class="pa-name">${esc(actorLabel(who))}</b>`
    + `<button type="button" class="ghost pa-x" data-pa-close aria-label="Close" title="Close">✕</button></header>`
    + `<div class="pa-chat"></div>`;
  document.body.append(dialog);
  const sheet: {dialog: HTMLDialogElement; chat: ThvView | null; byKey: boolean} = {dialog, chat: null, byKey: false};
  PA_SHEET = sheet;
  dialog.addEventListener('cancel', () => { sheet.byKey = true; });
  dialog.addEventListener('close', () => { if (PA_SHEET === sheet) paSheetClose(); });
  dialog.addEventListener('click', ev => { if (eventElement(ev)?.closest('[data-pa-close]')) dialog.close(); });
  // A swipe down on the sheet's head puts it away.
  let from: number | null = null;
  const head = dialog.querySelector<HTMLElement>('.pa-head');
  head?.addEventListener('touchstart', ev => { from = ev.touches[0]?.clientY ?? null; }, {passive: true});
  head?.addEventListener('touchend', ev => {
    const to = ev.changedTouches[0]?.clientY;
    if (from !== null && to !== undefined && to - from > 60) dialog.close();
    from = null;
  }, {passive: true});
  dialog.showModal();
  const host = dialog.querySelector<HTMLElement>('.pa-chat');
  // Not a card: the chat fills the sheet, with its box at the bottom.
  sheet.chat = host ? threadMount(host, {dm: who, header: false}) : null;
  return sheet.chat;
}

/** The sheet is put away: its chat goes with it, and after Escape the bar has the focus back. */
function paSheetClose(): void {
  const sheet = PA_SHEET;
  if (!sheet) return;
  PA_SHEET = null;
  threadUnmount(sheet.chat);
  if (sheet.dialog.open) sheet.dialog.close();
  sheet.dialog.remove();
  if (sheet.byKey) PA?.host.querySelector<HTMLTextAreaElement>('.pa-text')?.focus({preventScroll: true});
}

/** Sends the words to the bot's direct chat with the board they were asked from, and shows the chat over the board. */
async function paAsk(who: string, text: string, board: PaBoard): Promise<void> {
  const view = paSheetOpen(who);
  if (!view) return;
  await thvSend(view, {text, refs: {board}});
}

/** The bar on a pipeline's board on a phone; elsewhere (a computer, the archive, another page) there is none. */
function pipelineAskSync(host: HTMLElement, rec: PipelineRec, phone: boolean): void {
  if (!phone) { if (PA?.host === host) pipelineAskLeave(); host.hidden = true; return; }
  if (!PA || PA.host !== host || PA.rec.id !== rec.id || !host.isConnected) {
    pipelineAskLeave();
    host.replaceChildren();
    PA = {host, rec, stage: '', to: 'manager', small: false, lastY: window.scrollY};
    host.addEventListener('click', paClick);
    host.addEventListener('submit', paSubmit);
    host.addEventListener('keydown', paKey);
    host.addEventListener('input', paInput);
    host.addEventListener('focusin', paFocus);
    window.addEventListener('scroll', paScroll, {passive: true});
  }
  PA.rec = rec;
  pipelineAskPaint();
}

/** The board is gone (another pipeline, the archive, another page): the bar and its sheet go with it. */
function pipelineAskLeave(): void {
  const state = PA;
  if (!state) return;
  PA = null;
  paSheetClose();
  state.host.removeEventListener('click', paClick);
  state.host.removeEventListener('submit', paSubmit);
  state.host.removeEventListener('keydown', paKey);
  state.host.removeEventListener('input', paInput);
  state.host.removeEventListener('focusin', paFocus);
  window.removeEventListener('scroll', paScroll);
  state.host.hidden = true;
  state.host.replaceChildren();
  state.host.closest('.tasks-page')?.classList.remove('has-ask');
}
