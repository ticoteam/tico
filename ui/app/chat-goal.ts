/* ui/app/chat-goal.ts — A chat's pinned goal: the target button in the composer (until a goal is pinned), the bar above the thread, and
   the one line a met or stopped goal leaves in the chat. The harness (Codex, Claude Code) keeps working toward it.
   A goal lives on a chat's state: the old chat's (chat.js V2C, its composer BOT_PILL, the bar #chat-goal) or a bot's
   Home or DM thread (thread-view.js, view.goals), whose `ui` names its own bar, composer, redraw and room.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// GET/POST /api/v2/conversations/{id}/goal. Old servers answer 404, which reads as "not supported".
const goalPath = (conv: string) => `/v2/conversations/${encodeURIComponent(conv)}/goal`;
const GOAL_CHIPS: Record<string, [string, string]> = {active: ['Working', 'in-progress'], paused: ['Paused', 'waiting'], met: ['Met', 'ok'], stopped: ['Stopped', 'fail']};
const GOAL_MAX = 4000;
const CHAT_GOAL_MODE_KEY = 'hub.chat.goal-mode';
// What GET/POST /v2/conversations/{id}/goal answers (api.d.ts).
type GoalAnswer = import('../types/api').ChatGoalResult;
// A goal as the goal route answers it (GET/POST /v2/conversations/{id}/goal) and as the live stream sends it.
interface GoalRecord {
  id?: string | undefined;
  objective?: string | undefined;
  status?: string | undefined;
  note?: string | undefined;
  updated_at?: string | undefined;
  ended_at?: string | null | undefined;
}
// A goal action about to be sent: the verb, and the text for set and edit.
interface GoalIntent { slug: string; text: string; action: 'set' | 'edit' }
// The composer's goal fields: the old chat's BOT_PILL (pill.ts) and a bot thread's composer (thread-view.ts).
interface GoalComposer extends Pill {
  ways?: boolean | undefined;
  goalsOf?: (() => ChatGoalState | null | undefined) | undefined;
  goalPending?: GoalIntent | null | undefined;
  sent?: Promise<unknown> | null | undefined;
  label?: (() => void) | undefined;
}
// What a bot's thread supplies for its goal: its bar, its composer, its redraw, and the room its first goal makes.
interface ChatGoalUi {
  live(): boolean;
  bar(): HTMLElement | null | undefined;
  pill(): GoalComposer | null;
  redraw(): void;
  room(): Promise<boolean>;
}
// The fields the goal code reads and sets on a chat's state, common to the old chat (ChatState, chat.ts, which has no
// thread UI) and a bot's thread (chatGoalThread).
interface ChatGoalBase {
  slug: string;
  conv: { id: string } | null;
  loaded?: boolean | undefined;
  goal?: GoalRecord | null | undefined;
  goalSupported?: boolean | undefined;
  goalEditing?: boolean | undefined;
  goalRefused?: boolean | undefined;
  goalOpen?: boolean | undefined;
  commands?: Record<string, unknown>[] | null | undefined;
}
// The old chat: its state is the ChatState the rest of chat.ts keeps, with no thread UI.
type ChatGoalChat = ChatState & ChatGoalBase & { ui?: undefined };
// A bot's thread goal state: it always has its own UI.
interface ChatGoalThreadState extends ChatGoalBase {
  ui: ChatGoalUi;
  reading?: Promise<void> | null | undefined;
}
type ChatGoalState = ChatGoalChat | ChatGoalThreadState;
function chatGoalModeSaved(slug: string) {
  try { return JSON.parse(localStorage.getItem(CHAT_GOAL_MODE_KEY) || '{}')?.[slug] === true; } catch { return false; }
}
function chatGoalModeStore(slug: string, on: boolean) {
  try {
    const modes: Record<string, boolean> = JSON.parse(localStorage.getItem(CHAT_GOAL_MODE_KEY) || '{}') || {};
    if (on) modes[slug] = true; else delete modes[slug];
    localStorage.setItem(CHAT_GOAL_MODE_KEY, JSON.stringify(modes));
  } catch {}
}
// Where a state's goal draws. A thread's composer brings its own label and buttons; the old chat's are the pill's.
const cgLive = (state: ChatGoalState | null | undefined): boolean => !!state && (state.ui ? state.ui.live() : V2C === state);
const cgBar = (state: ChatGoalState): HTMLElement | null | undefined => state.ui ? state.ui.bar() : $('#chat-goal');
const cgPill = (state: ChatGoalState): GoalComposer | null => state.ui ? state.ui.pill() : BOT_PILL?.slug === state.slug ? BOT_PILL : null;
const cgRedraw = (state: ChatGoalState) => state.ui ? state.ui.redraw() : v2ChatRender(state);
const cgLabel = (P: GoalComposer) => P.label ? P.label() : pillLabel(P);
const cgButtons = (P: GoalComposer) => P.buttons ? P.buttons() : pillButtons(P);
// A goal that is still pinned to the chat: working toward it, or paused.
const goalPinned = (g: GoalRecord | null | undefined): g is GoalRecord => !!g && (g.status === 'active' || g.status === 'paused');
const goalEnded = (g: GoalRecord | null | undefined): g is GoalRecord => !!g && (g.status === 'met' || g.status === 'stopped');

// An empty chat has no conversation to ask yet. A bot on a Tico computer offers the goal when what its
// computer reported says goals work there (as backend/chat_goals.py reads it: the bot's own readiness, then the
// runtime's, then a harness on that runtime), or when it said nothing; the server, asked once the first goal makes
// the conversation, has the final word (chatGoalRoom).
function chatGoalGuess(slug: string): boolean {
  const bot = botRow(slug);
  if (!bot || slug === assistantBot() || bot.agent || !bot.machine) return false;
  const runtime = String(bot.resolved_runtime || bot.runtime || '');
  if (typeof bot.readiness?.goals === 'boolean') return bot.readiness.goals;
  const runnerId = bot.machine.runner_id;
  let report: MachineReadiness | null | undefined = null;
  try { report = (SETTINGS_DATA?.machines || []).find(m => m.id === runnerId)?.readiness; } catch { report = null; }
  const harnesses: Record<string, MachineHarness | undefined> = report?.harnesses || {};
  const said = report?.runtimes?.[runtime]?.goals ?? Object.values(harnesses).find(h => h?.runtime === runtime)?.goals;
  return typeof said === 'boolean' ? said : true;
}
// The server says this bot takes no goals. A goal being typed in the form stays there, with the reason, until Cancel.
function chatGoalRefused(state: ChatGoalState) {
  state.goalSupported = false;
  state.goalRefused = !!state.goalEditing;
  chatGoalRender(state);
  const why = cgBar(state)?.querySelector('.cg-why');
  if (why) why.textContent = "This bot's harness doesn't support goals.";
  toast("This bot's harness doesn't support goals.", true);
}
async function chatGoalLoad(state: ChatGoalState | null | undefined) {
  if (!state) return;
  if (!state.conv) {
    if (!cgLive(state) || !state.loaded) return;
    state.goalSupported = chatGoalGuess(state.slug); state.commands = null;
    chatGoalRender(state);
    return;
  }
  let d: GoalAnswer | null = null;
  const cid = state.conv.id;
  try { d = await get<GoalAnswer>(goalPath(cid), {fresh: true}); } catch { d = null; }
  if (!cgLive(state) || state.conv?.id !== cid) return;
  state.goalSupported = !!d?.supported;
  state.commands = Array.isArray(d?.commands) ? d.commands : null;
  chatGoalApply(state, d?.goal || null, {quiet: true});
}
// A goal from the API or the live stream: kept on the chat, drawn in the bar and the thread, and mirrored on the
// bot's row in the sidebar.
function chatGoalApply(state: ChatGoalState, goal: GoalRecord | null | undefined, {quiet = false}: {quiet?: boolean | undefined} = {}) {
  if (!cgLive(state)) return;
  const was = state.goal;
  if (goal && was && goal.id === was.id && goal.updated_at && was.updated_at && goal.updated_at < was.updated_at) return;   // a stale event
  state.goal = goal && goal.status !== 'cleared' ? goal : null;
  if (!goalPinned(state.goal)) state.goalOpen = false;
  // The row's mark covers every chat the viewer can read, so a quiet load only ever adds it; a change made or
  // streamed here sets it either way.
  const bot = botRow(state.slug), active = state.goal?.status === 'active';
  if (bot && !!bot.goal_active !== active && (active || !quiet)) { bot.goal_active = active; renderTree(); }
  chatGoalRender(state);
  if (!quiet || goalEnded(state.goal) !== goalEnded(was)) cgRedraw(state);
}
// The first goal in an empty chat makes its conversation (the bot's own room, as the first message would), then
// asks the server whether this bot takes goals before setting it.
async function chatGoalRoom(state: ChatGoalState): Promise<boolean> {
  if (state.conv) return true;
  if (state.ui) return state.ui.room();
  try {
    const r: {conversation?: {id: string} | undefined; id: string} = await post('/v2/conversations', {participants: [`bot:${state.slug}`], kind: 'chat'});
    if (V2C !== state) return false;
    const made = r.conversation || r;
    if (!state.conv) Object.assign(state, {conv: made, listed: true, loaded: true, failed: false});
    const conv = state.conv || made;
    let d: GoalAnswer | null = null;
    try { d = await get<GoalAnswer>(goalPath(conv.id)); } catch { d = null; }
    if (V2C !== state) return false;
    state.goalSupported = !!d?.supported;
    state.commands = Array.isArray(d?.commands) ? d.commands : null;
    if (S.me?.cloud) v2ChatStream(state);
    // The room is the bot's one canonical room, the same the first message would use; nothing extra is left behind.
    if (!state.goalSupported) { chatGoalRefused(state); return false; }
    return true;
  } catch (e) { toast(errorMessage(e, 'Something went wrong'), true); return false; }
}
async function chatGoalAct(action: string, objective: string | null | undefined, state: ChatGoalState | null = V2C): Promise<boolean> {
  if (!state) return false;
  if (!state.conv && (action !== 'set' || !await chatGoalRoom(state))) return false;
  const body: {action: string; objective?: string} = {action};
  if (objective != null) body.objective = objective;
  try {
    if (!state.conv) throw new Error('this chat has no conversation');
    const r: {goal?: GoalRecord | null | undefined} = await post(goalPath(state.conv.id), body);
    chatGoalApply(state, r.goal || null);
    return true;
  } catch (e) {
    // The server's goal_unsupported answer: an error object with a code, or a bare error string.
    const failure: Record<string, unknown> = isErrorRecord(e) && isErrorRecord(e.body) ? e.body : {};
    const message = isErrorRecord(e) ? e.message : undefined;
    const unsupported = (isErrorRecord(failure.error) && failure.error.code === 'goal_unsupported') || failure.error === 'goal_unsupported' ||
      (typeof message === 'string' && /goal_unsupported/.test(message));
    if (errorStatus(e) === 409 && unsupported) {
      chatGoalRefused(state);
    } else toast(errorMessage(e, 'Something went wrong'), true);
    return false;
  }
}
// Set a new goal, or change the text of the one that is pinned.
const chatGoalSave = (text: string, state: ChatGoalState | null = V2C) => chatGoalAct(goalPinned(state?.goal) ? 'edit' : 'set', text, state);
function chatGoalEdit(open = true, state: ChatGoalState | null = V2C) {
  if (!state) return;
  state.goalEditing = open;
  if (!open) state.goalRefused = false;
  if (open) state.goalOpen = false;
  chatGoalRender(state, true);
  if (open) { const box = cgBar(state)?.querySelector('textarea'); box?.focus(); box?.setSelectionRange(box.value.length, box.value.length); }
}

function chatGoalBarHTML(g: GoalRecord) {
  const [chip, tone] = GOAL_CHIPS[g.status ?? ''] || ['', ''];
  return `<div class="cg-bar" role="button" tabindex="0" aria-expanded="false" aria-label="Goal">
      <span class="nav-icon cg-icon" aria-hidden="true">target</span>
      <div class="cg-text">${esc(g.objective || '')}</div>
      <span>${chip ? `<span class="pill ${tone} cg-chip">${chip}</span>` : ''}${betaPill('chat_goals')}</span></div>
    <div class="cg-actions" hidden>
      <button class="ghost" type="button" data-goal="edit">Edit</button>
      <button class="ghost" type="button" data-goal="${g.status === 'paused' ? 'resume' : 'pause'}">${g.status === 'paused' ? 'Resume' : 'Pause'}</button>
      <button class="ghost" type="button" data-goal="clear">Clear</button>
    </div>`;
}
const chatGoalFormHTML = (text: string | null | undefined) => `<form class="cg-edit">
    <span class="nav-icon cg-icon" aria-hidden="true">target</span>
    <textarea rows="2" maxlength="${GOAL_MAX}" aria-label="Goal" placeholder="What should it get done?">${esc(text || '')}</textarea>
    <div class="cg-edit-go"><span class="cg-why err" role="status"></span><button class="ghost" type="button" data-goal="cancel">Cancel</button><button class="primary" type="submit">Save</button></div>
  </form>`;

// The bar sits above the thread and never scrolls with it. `force` redraws a form that is being typed in.
function chatGoalRender(state: ChatGoalState, force = false) {
  if (!cgLive(state)) return;
  const host = cgBar(state);
  if (host) {
    const g = state.goal, editing = state.goalEditing && (state.goalSupported || state.goalRefused);
    if (editing) {
      if (force || !host.querySelector('.cg-edit')) {
        host.innerHTML = chatGoalFormHTML(goalPinned(g) ? g.objective : '');
        chatGoalWireForm(host, state);
      }
    } else if (goalPinned(g)) {
      const key = `${g.id}|${g.status}|${g.updated_at}|${g.objective}`;
      if (force || host.dataset.key !== key || !host.querySelector('.cg-bar')) {
        host.innerHTML = chatGoalBarHTML(g); host.dataset.key = key;
        chatGoalWireBar(state, host);
      }
    } else { host.innerHTML = ''; host.dataset.key = ''; }
    host.hidden = !host.innerHTML;
    host.classList.toggle('open', !!state.goalOpen && !editing);
    host.querySelector('.cg-bar')?.setAttribute('aria-expanded', String(!!state.goalOpen));
    const actions = host.querySelector<HTMLElement>('.cg-actions'); if (actions) actions.hidden = !state.goalOpen;
  }
  const P = cgPill(state), btn: HTMLButtonElement | null | undefined = P && pq(P, '.p-goal');
  // A bot's thread composer (thread-view.js, P.ways) has no target: Set as goal is in its ways menu, for one goal.
  if (P && (btn || P.ways)) {
    if (P.goalMode == null) P.goalMode = !P.ways && chatGoalModeSaved(state.slug);
    // A pinned goal is edited and cleared from its bar, so the composer is for chatting: no target, and Send sends.
    const pinned = goalPinned(state.goal);
    if (pinned && P.goalMode) { P.goalMode = false; chatGoalModeStore(state.slug, false); }
  }
  if (P && btn) {
    const pinned = goalPinned(state.goal);
    btn.hidden = pinned || (!state.goalSupported && !P.goalMode);
    btn.classList.toggle('on', !!P.goalMode);
    btn.setAttribute('aria-pressed', String(!!P.goalMode));
    btn.setAttribute('aria-label', P.goalMode ? 'Goal mode on' : 'Goal mode');
    btn.title = P.goalMode ? 'Goal mode on · Send text as the goal' : 'Goal';
  }
  if (P && (btn || P.ways)) cgLabel(P);
}
function chatGoalWireBar(state: ChatGoalState, host: HTMLElement) {
  const bar = host.querySelector<HTMLElement>('.cg-bar');
  if (!bar) return;
  const toggle = () => { state.goalOpen = !state.goalOpen; chatGoalRender(state); };
  bar.onclick = toggle;
  bar.onkeydown = ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggle(); } };
  host.querySelectorAll<HTMLButtonElement>('[data-goal]').forEach(b => b.onclick = async () => {
    const act = b.dataset.goal ?? '';
    if (act === 'edit') return chatGoalEdit(true, state);
    b.disabled = true;
    await chatGoalAct(act, undefined, state);
    b.disabled = false;
  });
}
function chatGoalWireForm(host: HTMLElement, state: ChatGoalState | null = V2C) {
  const form = host.querySelector<HTMLFormElement>('form');
  const box = form?.querySelector<HTMLTextAreaElement>('textarea');
  const submit = form?.querySelector<HTMLButtonElement>('[type=submit]');
  const cancel = form?.querySelector<HTMLButtonElement>('[data-goal="cancel"]');
  if (!form || !box || !submit || !cancel) return;
  // The box fits the goal being edited, up to its CSS cap.
  const fit = () => { box.style.height = 'auto'; box.style.height = box.scrollHeight + 2 + 'px'; };
  box.addEventListener('input', fit); fit();
  let busy = false;                     // Return twice sends one goal
  const save = async () => {
    const text = box.value.trim();
    if (!text) { box.focus(); return; }
    if (busy) return;
    busy = true;
    submit.disabled = true;
    try { if (await chatGoalSave(text, state)) { chatGoalEdit(false, state); return; } }
    finally { busy = false; }
    submit.disabled = false;
    box.focus();
  };
  form.onsubmit = ev => { ev.preventDefault(); void save(); };
  // Return saves and Shift+Return is a new line, as in the composer; Esc puts it away.
  box.onkeydown = ev => {
    if (ev.key === 'Escape') { ev.preventDefault(); chatGoalEdit(false, state); }
    else if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing && !touchKeyboard()) { ev.preventDefault(); void save(); }
  };
  cancel.onclick = () => chatGoalEdit(false, state);
}
// The composer's target toggles the highlighted mode; ordinary Send and keyboard submission
// then save the text as the conversation goal, without an intermediate form.
function chatGoalButton(state: ChatGoalState | null = V2C) {
  if (!state) return;
  const P = cgPill(state);
  if (!P) return;
  if (P.goalMode) {
    P.goalMode = false; chatGoalModeStore(state.slug, false); chatGoalRender(state); return;
  }
  if (!state.goalSupported) { toast("This bot's harness doesn't support goals.", true); return; }
  P.goalMode = true; if (!P.ways) chatGoalModeStore(state.slug, true); chatGoalRender(state);
}
async function chatGoalSend(P: GoalComposer, text: string): Promise<boolean> {
  const state = P.goalsOf ? P.goalsOf() : V2C, box = pq(P, '.p-text');
  if (!text) { box?.focus(); return false; }
  if (!state || state.slug !== P.slug) return false;
  if (!state.goalSupported) { toast("Goal mode isn't available for this chat. Turn Goal mode off to send a regular message.", true); return false; }
  const scope = sessionScope(box);
  while (P.sending) {
    if (!P.sent) return false;
    await P.sent;
    if ((pq(P, '.p-text')?.value || '').trim() !== text) return false;
  }
  P.sending = true;
  let sent!: (value?: unknown) => void;
  P.sent = new Promise(done => sent = done);
  const button: HTMLButtonElement | null | undefined = pq(P, '.p-send'), label = button?.getAttribute('aria-label') || 'Send';
  if (button) { button.disabled = true; pillBtnSay(button, 'Saving goal…'); }
  try {
    // Keep the same operation shape through an uncertain retry, even if a live refresh arrives
    // before the person presses Send again.
    let intent = P.goalPending;
    if (!intent || intent.slug !== state.slug || intent.text !== text) {
      intent = P.goalPending = {slug: state.slug, text, action: goalPinned(state.goal) ? 'edit' : 'set'};
    }
    if (!await chatGoalAct(intent.action, text, state)) return false;
    sessionAcknowledge(box, scope);
    if (P.goalPending === intent) P.goalPending = null;
    if (P.ways) P.goalMode = false;                  // Set as goal from the ways menu is for one goal
    // Do not erase edits made while the server was saving. Goal writes use the shared API's
    // stable Idempotency-Key retries, and failed/uncertain writes leave the draft untouched.
    if ((pq(P, '.p-text')?.value || '').trim() === text) {
      const current = pq(P, '.p-text'); current.value = ''; current.style.height = 'auto';
      chatDraftSave(P); cgButtons(P);
    }
    return true;
  } finally {
    P.sending = false; P.sent = null; sent();
    if (button) { button.disabled = false; pillBtnSay(button, label); }
    cgLabel(P); cgButtons(P);
  }
}
// ----------------------------------------------------------------- a bot's Home or DM
// The bot page's thread (thread-view.js, {goals: slug}) keeps its goal as the old chat did: the thread is the
// conversation the goal route takes (docs/dev/threads-contract.md: a thread id is a conversation id). An empty DM has
// none yet; its first goal makes it, as its first message would.
function chatGoalThread(view: ThvView, slug: string): ChatGoalThreadState {
  const state: ChatGoalThreadState = {slug, conv: view.id ? {id: view.id} : null, goal: null, goalSupported: false, goalEditing: false,
                 goalRefused: false, goalOpen: false, commands: null, loaded: true, ui: {
    live: () => !view.gone && view.goals === state,
    bar: () => view.el?.querySelector<HTMLElement>('.thv-goal'),
    pill: () => view.composer?.el ? view.composer : null,
    redraw: () => thvDraw(view),
    room: async () => {
      try {
        if (!view.id && view.opts.dm) {
          const made = await post('/v2/threads/dm', {members: [view.opts.dm]});
          const t = made?.thread || made;
          if (t?.id) { view.thread = t; thvSetId(view, t.id); }
        }
        if (!view.id || !state.ui.live()) return false;
        await chatGoalThreadId(view);
        if (!state.ui.live()) return false;
        if (!state.goalSupported) { chatGoalRefused(state); return false; }
        return true;
      } catch (e) { toast(errorMessage(e, 'Something went wrong'), true); return false; }
    },
  }};
  return state;
}
// The thread on screen now has an id (loaded, or made by a first message or goal): its goal, and whether it takes one.
// Without one, what the bot's computer reported decides whether the target shows.
function chatGoalThreadId(view: ThvView) {
  const state = view.goals;
  if (!state) return Promise.resolve();
  if (!view.id) { state.conv = null; return chatGoalLoad(state); }
  if (state.conv?.id === view.id && state.reading) return state.reading;
  state.conv = {id: view.id};
  return state.reading = chatGoalLoad(state).finally(() => { state.reading = null; });
}
// The composer's Send in goal mode: the text is the goal (chatGoalSend), never a message. Files go as a message.
const chatGoalThreadMode = (C: ThvComposer | null | undefined) => !!C?.goalMode && !C.files.length && !C.lib.length;

// The /goal messages Set, Edit, Pause, Resume and Clear send (refs.goal_action): the bar shows their effect.
const chatGoalControl = (m: ChatMessage | null | undefined) => !!m?.refs?.goal_action;
function chatGoalLineHTML(status: string | undefined, objective: string | null | undefined, note: string | null | undefined, created?: string | undefined) {
  const what = status === 'met' ? 'Goal met' : 'Goal stopped';
  const full = [objective, note].filter(Boolean).join(' · ');
  return `<div class="chat-system chat-goal-line ${status === 'met' ? 'met' : 'stopped'}" title="${esc(full)}"><span class="nav-icon cg-icon" aria-hidden="true">target</span>
    <b>${what}:</b> <span class="cg-line-text">${esc(objective || '')}</span>${note ? `<span class="cg-line-note">· ${esc(note)}</span>` : ''}${created
      ? `<time class="chat-time" title="${esc(fmt(created))}">${esc(ago(created))}</time>` : ''}</div>`;
}
// The server's notice for a met or stopped goal (refs.chat_goal, refs.goal_status): "Goal met: <objective>\n<note>".
function chatGoalNoticeHTML(m: ChatMessage, goal: GoalRecord | null | undefined = V2C?.goal) {
  const refs: ChatRefs = m.refs || {};
  const status = refs.goal_status === 'met' ? 'met' : 'stopped';
  // Text the server sent as text; anything else (typed unknown) is no text.
  const refObjective = typeof refs.goal_objective === 'string' ? refs.goal_objective : undefined;
  const refNote = typeof refs.goal_note === 'string' ? refs.goal_note : undefined;
  // Its own words when it carries them, else the goal this chat holds, else the body ("Goal met: <objective>\n<note>",
  // which cannot tell a long objective's later lines from the note).
  const g = goal?.id === refs.chat_goal ? goal : null;
  const [first = '', ...rest] = String(m.body || '').split('\n');
  const objective = refObjective || g?.objective || first.replace(/^Goal (met|stopped):\s*/i, '');
  const note = refNote ?? (g && g.status === status ? g.note || '' : rest.join(' ').trim());
  return chatGoalLineHTML(status, objective, note, m.created_at);
}
// A met or stopped goal leaves one line in the thread, where it ended; nothing else announces it. The notice the server
// wrote for that ending (refs.chat_goal) stands instead; a goal control never does.
function chatGoalLine(state: ChatGoalState, messages: ChatMessage[]) {
  const g = state.goal;
  if (!goalEnded(g)) return null;
  if (messages.some(m => m.refs?.chat_goal === g.id && (m.refs?.goal_status || g.status) === g.status)) return null;
  const end = String(g.ended_at || g.updated_at || '');
  const at = end ? messages.filter(m => String(m.created_at || '') <= end).length : messages.length;
  return {at, html: chatGoalLineHTML(g.status, g.objective, g.note)};
}
