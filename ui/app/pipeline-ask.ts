/* ui/app/pipeline-ask.ts — The board's bots, on a phone (spec #337). The page's chat mark is the header's face
   (tasks-page.ts #task-manager, style guide "Where chat lives": a phone opens the chat full screen from the page's chat
   mark), the pipeline manager's, 22 px in a 44 px button; nothing floats over the board, so no row or age is covered.
   Tapped, a small menu under it lists the board's bots (the manager, then each stage's holder, face, name and role);
   with one bot the chat opens at once. The
   chat opens as a sheet over the board, the chat alone (no Log, no Tasks), its box focused, with what goes with each
   message as a chip above the box ("Product › Spec · 7 tasks"), which ✕ removes. A message carries `refs.board`: the
   pipeline, the open stage and the ids of the tasks the tab shows, search and filter applied, at most PA_MAX_IDS of
   them (`more` counts the rest); the chat shows it as one card under the message (thread-view.ts thvBoardHTML). ✕,
   Escape or a swipe down closes the sheet, and the board is where it was.
   The page (tasks-page.ts) gives the menu's host through the pipeline view's `ask` hook (task-pipelines.ts) and sends the
   face's taps here (pipelineAskOpen); the view paints the face again when its tab changes (pvDraw). On a computer the
   manager answers the page's chat column instead (pipeline-rail.ts, page-chat.ts), with the same board.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

interface PaBoard { pipeline_id: string; pipeline: string; stage_id: string; stage: string; task_ids: string[]; more: number }
interface PaState { host: HTMLElement; rec: PipelineRec; stage: string }
interface PaBot { actor: string; role: string }

let PA: PaState | null = null;
// `byKey`: Escape put the sheet away, so the face takes the focus back.
let PA_SHEET: {dialog: HTMLDialogElement; chat: ThvView | null; byKey: boolean} | null = null;
const PA_MAX_IDS = 50;
const PA_STAGE_HOLDER = /^(bot|human):./;

/** The board's bots: the pipeline's manager first, then each other holder of a stage with the stages it holds. */
function paBots(rec: PipelineRec, open = ''): PaBot[] {
  const manager = rec.manager_id ? prActor(rec.manager_id) : '';
  const out: PaBot[] = manager ? [{actor: manager, role: 'Manager'}] : [];
  const held = new Map<string, string[]>();
  for (const st of rec.stages) {
    const who = String(st.owner || '');
    if (!PA_STAGE_HOLDER.test(who) || who === manager) continue;
    held.set(who, [...(held.get(who) || []), st.name]);
  }
  const first = String(rec.stages.find(s => s.id === open)?.owner || '');
  const order = [...held.keys()].sort((a, b) => (b === first ? 1 : 0) - (a === first ? 1 : 0));
  for (const actor of order) { const names = held.get(actor) || []; out.push({actor, role: names.length === rec.stages.length ? 'Holds every stage' : `Holds ${names.join(', ')}`}); }
  return out;
}

/** The stage the board shows and the tasks on its tab, as the view drew them (task-pipelines.ts). */
function paOnScreen(state: PaState): {stage: PipelineStageRec | undefined; tasks: TaskAny[]} {
  const view = PV && PV.rec.id === state.rec.id ? PV : null;
  const stage = state.rec.stages.find(s => s.id === (view?.stage || state.stage)) ?? state.rec.stages[0];
  return {stage, tasks: view && stage?.id ? pvInStage(view, stage.id) : []};
}

/** What a message carries: the pipeline, the open stage and the tasks on screen. */
function paBoard(state: PaState): PaBoard {
  const {stage, tasks} = paOnScreen(state);
  const ids = tasks.map(t => String(t.id));
  return {pipeline_id: state.rec.id, pipeline: state.rec.name, stage_id: stage?.id ?? '', stage: stage?.name ?? '',
    task_ids: ids.slice(0, PA_MAX_IDS), more: Math.max(0, ids.length - PA_MAX_IDS)};
}

/** The chip's words: "Product › Spec · 7 tasks". */
function paBoardWords(state: PaState): string {
  const {stage, tasks} = paOnScreen(state), n = tasks.length;
  return `${stage ? `${state.rec.name} › ${stage.name}` : state.rec.name} · ${n} ${n === 1 ? 'task' : 'tasks'}`;
}

const paPop = (state: PaState) => state.host.querySelector<HTMLElement>('.pa-pop');
/** The face in the page's header that opens the board's bots. */
const paFace = (): HTMLElement | null => document.getElementById('task-manager');

/** Draws the face and its menu again (another pipeline, another manager, another stage's holder). The page's header
 * shows the face on a pipeline (tasks-page.ts tasksHeadPaint); with no bot on the board it is hidden here. */
function pipelineAskPaint(): void {
  const state = PA;
  if (!state || !state.host.isConnected) return;
  const {stage} = paOnScreen(state);
  if (stage?.id && stage.id !== state.stage) state.stage = stage.id;
  const bots = paBots(state.rec, state.stage), first = bots[0]?.actor || '', one = bots.length === 1;
  const btn = paFace();
  if (btn) {
    const shown = !!first && !pageChatNone();
    btn.hidden = !shown;
    if (shown) {
      const words = one ? `Chat with ${actorLabel(first)}` : 'Chat with…', face = actorFace(first, 22);
      if (btn.dataset['face'] !== face) { btn.dataset['face'] = face; btn.innerHTML = face; }
      btn.setAttribute('aria-label', words); btn.title = words;
      btn.removeAttribute('aria-pressed');            // a phone has no rail to show pressed
      btn.setAttribute('aria-haspopup', one ? 'dialog' : 'menu');
      if (one) btn.removeAttribute('aria-expanded');
      else if (!btn.hasAttribute('aria-expanded')) btn.setAttribute('aria-expanded', 'false');
    }
  }
  // The host holds only the menu, placed under the face when it opens (paMenu).
  const html = first && !one ? `<div class="pa-pop" role="menu" aria-label="Chat with" hidden>${bots.map(b =>
      `<button type="button" role="menuitem" class="pa-pick" data-pa-who="${esc(b.actor)}"><span class="pa-face">${actorFace(b.actor, 28)}</span>`
      + `<span class="pa-pick-t"><span class="pa-pick-n">${esc(actorLabel(b.actor))}</span><span class="pa-pick-r">${esc(b.role)}</span></span></button>`).join('')}</div>` : '';
  if (state.host.dataset['html'] !== html) { state.host.dataset['html'] = html; state.host.innerHTML = html; btn?.setAttribute('aria-expanded', 'false'); }
  state.host.hidden = !html;
}

function paMenu(state: PaState, open: boolean): void {
  const pop = paPop(state), fab = paFace();
  if (!pop || !fab) return;
  if (open) {
    // Under the face, its right edge on the face's: the menu is fixed, so it stays put however the board scrolls.
    const r = fab.getBoundingClientRect();
    state.host.style.top = `${Math.round(r.bottom + 4)}px`;
    state.host.style.right = `${Math.max(8, Math.round(document.documentElement.clientWidth - r.right))}px`;
  }
  pop.hidden = !open;
  fab.setAttribute('aria-expanded', String(open));
  if (open) pop.querySelector<HTMLElement>('.pa-pick')?.focus();
}

/** A pick in the menu opens that bot's chat. */
function paClick(ev: MouseEvent): void {
  const state = PA, pick = eventElement(ev)?.closest<HTMLElement>('[data-pa-who]');
  if (!state || !pick) return;
  paMenu(state, false);
  paSheetOpen(state, pick.dataset['paWho'] || '');
}

/** The header's face, tapped on a phone: one bot's chat at once, else the menu of the board's bots (or closes it). */
function pipelineAskOpen(): void {
  const state = PA;
  if (!state) return;
  const pop = paPop(state);
  if (!pop) { const b = paBots(state.rec, state.stage)[0]; if (b) paSheetOpen(state, b.actor); return; }
  paMenu(state, Boolean(pop.hidden));
}

/** A tap outside the menu, or Escape, closes it. */
function paOutside(ev: Event): void {
  const state = PA, pop = state && paPop(state);
  if (!state || !pop || pop.hidden) return;
  if (ev instanceof KeyboardEvent) {
    const items = [...pop.querySelectorAll<HTMLElement>('.pa-pick')], at = items.indexOf(document.activeElement as HTMLElement);
    const go = (i: number) => { ev.preventDefault(); items[Math.max(0, Math.min(items.length - 1, i))]?.focus(); };
    if (ev.key === 'Escape') { ev.preventDefault(); paMenu(state, false); paFace()?.focus(); }
    else if (ev.key === 'ArrowDown') go(at + 1);
    else if (ev.key === 'ArrowUp') go(at - 1);
    else if (ev.key === 'Home') go(0);
    else if (ev.key === 'End') go(items.length - 1);
    else if (ev.key === 'Tab') paMenu(state, false);
    return;
  }
  // A press on the menu, or on the face (its click closes the menu itself), is not outside.
  const at = eventElement(ev);
  if (state.host.contains(at) || (at && paFace()?.contains(at))) return;
  // The tap that closes the menu does nothing else: the card under it does not open.
  ev.preventDefault();
  const swallow = (e: Event) => { e.preventDefault(); e.stopPropagation(); };
  document.addEventListener('click', swallow, {capture: true, once: true});
  // A press that never becomes a click (a scroll) must not swallow a later tap.
  setTimeout(() => document.removeEventListener('click', swallow, {capture: true}), 700);
  paMenu(state, false);
}

/** The sheet with the bot's chat, over the board: its face and name, ✕, then the chat alone with its box focused and
    the board as a chip above the box. */
function paSheetOpen(state: PaState, who: string): ThvView | null {
  if (!who) return null;
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
  // Not a card: the chat fills the sheet, with its box at the bottom. The board goes with each message until ✕.
  sheet.chat = host ? threadMount(host, {dm: who, header: false, autofocus: true,
    context: {label: () => paBoardWords(state), refs: () => ({board: paBoard(state)})}}) : null;
  return sheet.chat;
}

/** The sheet is put away: its chat goes with it, and after Escape the face has the focus back. */
function paSheetClose(): void {
  const sheet = PA_SHEET;
  if (!sheet) return;
  PA_SHEET = null;
  threadUnmount(sheet.chat);
  if (sheet.dialog.open) sheet.dialog.close();
  sheet.dialog.remove();
  if (sheet.byKey) paFace()?.focus({preventScroll: true});
}

/** The board's bots on a pipeline on a phone; elsewhere (a computer, the archive, another page) the face is the rail's
 * (tasks-page.ts) or there is none. */
function pipelineAskSync(host: HTMLElement, rec: PipelineRec, phone: boolean): void {
  if (!phone) { if (PA?.host === host) pipelineAskLeave(); host.hidden = true; return; }
  if (!PA || PA.host !== host || PA.rec.id !== rec.id || !host.isConnected) {
    pipelineAskLeave();
    host.replaceChildren();
    delete host.dataset['html'];
    PA = {host, rec, stage: ''};
    host.addEventListener('click', paClick);
    document.addEventListener('pointerdown', paOutside, true);
    document.addEventListener('keydown', paOutside, true);
  }
  PA.rec = rec;
  pipelineAskPaint();
}

/** The board is gone (another pipeline, the archive, another page): the face and its sheet go with it. */
function pipelineAskLeave(): void {
  const state = PA;
  if (!state) return;
  PA = null;
  paSheetClose();
  state.host.removeEventListener('click', paClick);
  document.removeEventListener('pointerdown', paOutside, true);
  document.removeEventListener('keydown', paOutside, true);
  state.host.hidden = true;
  state.host.replaceChildren();
  delete state.host.dataset['html'];
}
