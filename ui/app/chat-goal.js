/* ui/app/chat-goal.js — A chat's pinned goal: the target button in the composer (until a goal is pinned), the bar above the thread, and
   the one line a met or stopped goal leaves in the chat. The harness (Codex, Claude Code) keeps working toward it.
   A goal lives on a chat's state: the old chat's (chat.js V2C, its composer BOT_PILL, the bar #chat-goal) or a bot's
   Home or DM thread (thread-view.js, view.goals), whose `ui` names its own bar, composer, redraw and room.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// GET/POST /api/v2/conversations/{id}/goal. Old servers answer 404, which reads as "not supported".
const goalPath = conv => `/v2/conversations/${encodeURIComponent(conv)}/goal`;
const GOAL_CHIPS = {active: ['Working', 'in-progress'], paused: ['Paused', 'waiting'], met: ['Met', 'ok'], stopped: ['Stopped', 'fail']};
const GOAL_MAX = 4000;
const CHAT_GOAL_MODE_KEY = 'hub.chat.goal-mode';
function chatGoalModeSaved(slug) {
  try { return JSON.parse(localStorage.getItem(CHAT_GOAL_MODE_KEY) || '{}')?.[slug] === true; } catch { return false; }
}
function chatGoalModeStore(slug, on) {
  try {
    const modes = JSON.parse(localStorage.getItem(CHAT_GOAL_MODE_KEY) || '{}') || {};
    if (on) modes[slug] = true; else delete modes[slug];
    localStorage.setItem(CHAT_GOAL_MODE_KEY, JSON.stringify(modes));
  } catch {}
}
// Where a state's goal draws. A thread's composer brings its own label and buttons; the old chat's are the pill's.
const cgLive = state => !!state && (state.ui ? state.ui.live() : V2C === state);
const cgBar = state => state.ui ? state.ui.bar() : $('#chat-goal');
const cgPill = state => state.ui ? state.ui.pill() : BOT_PILL?.slug === state.slug ? BOT_PILL : null;
const cgRedraw = state => state.ui ? state.ui.redraw() : v2ChatRender(state);
const cgLabel = P => P.label ? P.label() : pillLabel(P);
const cgButtons = P => P.buttons ? P.buttons() : pillButtons(P);
// A goal that is still pinned to the chat: working toward it, or paused.
const goalPinned = g => !!g && (g.status === 'active' || g.status === 'paused');
const goalEnded = g => !!g && (g.status === 'met' || g.status === 'stopped');

// An empty chat has no conversation to ask yet. A bot on a Tico computer offers the goal when what its
// computer reported says goals work there (as backend/chat_goals.py reads it: the bot's own readiness, then the
// runtime's, then a harness on that runtime), or when it said nothing; the server, asked once the first goal makes
// the conversation, has the final word (chatGoalRoom).
function chatGoalGuess(slug) {
  const bot = botRow(slug);
  if (!bot || slug === assistantBot() || !isKeeper(slug) || bot.agent || !bot.machine) return false;
  const runtime = String(bot.resolved_runtime || bot.runtime || '');
  if (typeof bot.readiness?.goals === 'boolean') return bot.readiness.goals;
  let report = null;
  try { report = (SETTINGS_DATA?.machines || []).find(m => m.id === bot.machine.runner_id)?.readiness; } catch { report = null; }
  const said = report?.runtimes?.[runtime]?.goals ?? Object.values(report?.harnesses || {}).find(h => h?.runtime === runtime)?.goals;
  return typeof said === 'boolean' ? said : true;
}
// The server says this bot takes no goals. A goal being typed in the form stays there, with the reason, until Cancel.
function chatGoalRefused(state) {
  state.goalSupported = false;
  state.goalRefused = !!state.goalEditing;
  chatGoalRender(state);
  const why = cgBar(state)?.querySelector('.cg-why');
  if (why) why.textContent = "This bot's harness doesn't support goals.";
  toast("This bot's harness doesn't support goals.", true);
}
async function chatGoalLoad(state) {
  if (!state) return;
  if (!state.conv) {
    if (!cgLive(state) || !state.loaded) return;
    state.goalSupported = chatGoalGuess(state.slug); state.commands = null;
    chatGoalRender(state);
    return;
  }
  let d = null;
  const cid = state.conv.id;
  try { d = await get(goalPath(cid), {fresh: true}); } catch { d = null; }
  if (!cgLive(state) || state.conv?.id !== cid) return;
  state.goalSupported = !!d?.supported;
  state.commands = Array.isArray(d?.commands) ? d.commands : null;
  chatGoalApply(state, d?.goal || null, {quiet: true});
}
// A goal from the API or the live stream: kept on the chat, drawn in the bar and the thread, and mirrored on the
// bot's row in the sidebar.
function chatGoalApply(state, goal, {quiet = false} = {}) {
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
async function chatGoalRoom(state) {
  if (state.conv) return true;
  if (state.ui) return state.ui.room();
  try {
    const r = await post('/v2/conversations', {participants: [`bot:${state.slug}`], kind: 'chat'});
    if (V2C !== state) return false;
    if (!state.conv) Object.assign(state, {conv: r.conversation || r, listed: true, loaded: true, failed: false});
    let d = null;
    try { d = await get(goalPath(state.conv.id)); } catch { d = null; }
    if (V2C !== state) return false;
    state.goalSupported = !!d?.supported;
    state.commands = Array.isArray(d?.commands) ? d.commands : null;
    if (S.me?.cloud) v2ChatStream(state);
    // The room is the bot's one canonical room, the same the first message would use; nothing extra is left behind.
    if (!state.goalSupported) { chatGoalRefused(state); return false; }
    return true;
  } catch (e) { toast(e.message, true); return false; }
}
async function chatGoalAct(action, objective, state = V2C) {
  if (!state) return false;
  if (!state.conv && (action !== 'set' || !await chatGoalRoom(state))) return false;
  const body = {action};
  if (objective != null) body.objective = objective;
  try {
    const r = await post(goalPath(state.conv.id), body);
    chatGoalApply(state, r.goal || null);
    return true;
  } catch (e) {
    if (e.status === 409 && (e.body?.error?.code === 'goal_unsupported' || e.body?.error === 'goal_unsupported' || /goal_unsupported/.test(e.message))) {
      chatGoalRefused(state);
    } else toast(e.message, true);
    return false;
  }
}
// Set a new goal, or change the text of the one that is pinned.
const chatGoalSave = (text, state = V2C) => chatGoalAct(goalPinned(state?.goal) ? 'edit' : 'set', text, state);
function chatGoalEdit(open = true, state = V2C) {
  if (!state) return;
  state.goalEditing = open;
  if (!open) state.goalRefused = false;
  if (open) state.goalOpen = false;
  chatGoalRender(state, true);
  if (open) { const box = cgBar(state)?.querySelector('textarea'); box?.focus(); box?.setSelectionRange(box.value.length, box.value.length); }
}

function chatGoalBarHTML(g) {
  const [chip, tone] = GOAL_CHIPS[g.status] || ['', ''];
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
const chatGoalFormHTML = text => `<form class="cg-edit">
    <span class="nav-icon cg-icon" aria-hidden="true">target</span>
    <textarea rows="2" maxlength="${GOAL_MAX}" aria-label="Goal" placeholder="What should it get done?">${esc(text || '')}</textarea>
    <div class="cg-edit-go"><span class="cg-why err" role="status"></span><button class="ghost" type="button" data-goal="cancel">Cancel</button><button class="primary" type="submit">Save</button></div>
  </form>`;

// The bar sits above the thread and never scrolls with it. `force` redraws a form that is being typed in.
function chatGoalRender(state, force = false) {
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
    const actions = host.querySelector('.cg-actions'); if (actions) actions.hidden = !state.goalOpen;
  }
  const P = cgPill(state), btn = P && pq(P, '.p-goal');
  // The old chat's composer (pill.js, P.ways) has no target: Set as goal is in its ways menu, for one goal.
  if (P && (btn || P.ways)) {
    if (P.goalMode == null) P.goalMode = !P.ways && chatGoalModeSaved(state.slug);
    // A pinned goal is edited and cleared from its bar, so the composer is for chatting: no target, and Send sends.
    const pinned = goalPinned(state.goal);
    if (pinned && P.goalMode) { P.goalMode = false; chatGoalModeStore(state.slug, false); }
  }
  if (btn) {
    const pinned = goalPinned(state.goal);
    btn.hidden = pinned || (!state.goalSupported && !P.goalMode);
    btn.classList.toggle('on', !!P.goalMode);
    btn.setAttribute('aria-pressed', String(!!P.goalMode));
    btn.setAttribute('aria-label', P.goalMode ? 'Goal mode on' : 'Goal mode');
    btn.title = P.goalMode ? 'Goal mode on · Send text as the goal' : 'Goal';
  }
  if (P && (btn || P.ways)) cgLabel(P);
}
function chatGoalWireBar(state, host) {
  const bar = host.querySelector('.cg-bar');
  const toggle = () => { state.goalOpen = !state.goalOpen; chatGoalRender(state); };
  bar.onclick = toggle;
  bar.onkeydown = ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggle(); } };
  host.querySelectorAll('[data-goal]').forEach(b => b.onclick = async () => {
    const act = b.dataset.goal;
    if (act === 'edit') return chatGoalEdit(true, state);
    b.disabled = true;
    await chatGoalAct(act, undefined, state);
    b.disabled = false;
  });
}
function chatGoalWireForm(host, state = V2C) {
  const form = host.querySelector('form'), box = form.querySelector('textarea');
  // The box fits the goal being edited, up to its CSS cap.
  const fit = () => { box.style.height = 'auto'; box.style.height = box.scrollHeight + 2 + 'px'; };
  box.addEventListener('input', fit); fit();
  let busy = false;                     // Return twice sends one goal
  const save = async () => {
    const text = box.value.trim();
    if (!text) { box.focus(); return; }
    if (busy) return;
    busy = true;
    form.querySelector('[type=submit]').disabled = true;
    try { if (await chatGoalSave(text, state)) { chatGoalEdit(false, state); return; } }
    finally { busy = false; }
    form.querySelector('[type=submit]').disabled = false;
    box.focus();
  };
  form.onsubmit = ev => { ev.preventDefault(); void save(); };
  // Return saves and Shift+Return is a new line, as in the composer; Esc puts it away.
  box.onkeydown = ev => {
    if (ev.key === 'Escape') { ev.preventDefault(); chatGoalEdit(false, state); }
    else if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing && !touchKeyboard()) { ev.preventDefault(); void save(); }
  };
  form.querySelector('[data-goal="cancel"]').onclick = () => chatGoalEdit(false, state);
}
// The composer's target toggles the highlighted mode; ordinary Send and keyboard submission
// then save the text as the conversation goal, without an intermediate form.
function chatGoalButton(state = V2C) {
  const P = state && cgPill(state);
  if (!P) return;
  if (P.goalMode) {
    P.goalMode = false; chatGoalModeStore(state.slug, false); chatGoalRender(state); return;
  }
  if (!state.goalSupported) { toast("This bot's harness doesn't support goals.", true); return; }
  P.goalMode = true; if (!P.ways) chatGoalModeStore(state.slug, true); chatGoalRender(state);
}
async function chatGoalSend(P, text) {
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
  let sent; P.sent = new Promise(done => sent = done);
  const button = pq(P, '.p-send'), label = button?.getAttribute('aria-label') || 'Send';
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
function chatGoalThread(view, slug) {
  const state = {slug, conv: view.id ? {id: view.id} : null, goal: null, goalSupported: false, goalEditing: false,
                 goalRefused: false, goalOpen: false, commands: null, loaded: true};
  state.ui = {
    live: () => !view.gone && view.goals === state,
    bar: () => view.el?.querySelector('.thv-goal'),
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
      } catch (e) { toast(e.message, true); return false; }
    },
  };
  return state;
}
// The thread on screen now has an id (loaded, or made by a first message or goal): its goal, and whether it takes one.
// Without one, what the bot's computer reported decides whether the target shows.
function chatGoalThreadId(view) {
  const state = view.goals;
  if (!state) return Promise.resolve();
  if (!view.id) { state.conv = null; return chatGoalLoad(state); }
  if (state.conv?.id === view.id && state.reading) return state.reading;
  state.conv = {id: view.id};
  return state.reading = chatGoalLoad(state).finally(() => { state.reading = null; });
}
// The composer's Send in goal mode: the text is the goal (chatGoalSend), never a message. Files go as a message.
const chatGoalThreadMode = C => !!C?.goalMode && !C.files.length && !C.lib.length;

// The /goal messages Set, Edit, Pause, Resume and Clear send (refs.goal_action): the bar shows their effect.
const chatGoalControl = m => !!m?.refs?.goal_action;
function chatGoalLineHTML(status, objective, note, created) {
  const what = status === 'met' ? 'Goal met' : 'Goal stopped';
  const full = [objective, note].filter(Boolean).join(' · ');
  return `<div class="chat-system chat-goal-line ${status === 'met' ? 'met' : 'stopped'}" title="${esc(full)}"><span class="nav-icon cg-icon" aria-hidden="true">target</span>
    <b>${what}:</b> <span class="cg-line-text">${esc(objective || '')}</span>${note ? `<span class="cg-line-note">· ${esc(note)}</span>` : ''}${created
      ? `<time class="chat-time" title="${esc(fmt(created))}">${esc(ago(created))}</time>` : ''}</div>`;
}
// The server's notice for a met or stopped goal (refs.chat_goal, refs.goal_status): "Goal met: <objective>\n<note>".
function chatGoalNoticeHTML(m, goal = V2C?.goal) {
  const status = m.refs?.goal_status === 'met' ? 'met' : 'stopped';
  // Its own words when it carries them, else the goal this chat holds, else the body ("Goal met: <objective>\n<note>",
  // which cannot tell a long objective's later lines from the note).
  const g = goal?.id === m.refs.chat_goal ? goal : null;
  const [first = '', ...rest] = String(m.body || '').split('\n');
  const objective = m.refs.goal_objective || g?.objective || first.replace(/^Goal (met|stopped):\s*/i, '');
  const note = m.refs.goal_note ?? (g && g.status === status ? g.note || '' : rest.join(' ').trim());
  return chatGoalLineHTML(status, objective, note, m.created_at);
}
// A met or stopped goal leaves one line in the thread, where it ended; nothing else announces it. The notice the server
// wrote for that ending (refs.chat_goal) stands instead; a goal control never does.
function chatGoalLine(state, messages) {
  const g = state.goal;
  if (!goalEnded(g)) return null;
  if (messages.some(m => m.refs?.chat_goal === g.id && (m.refs.goal_status || g.status) === g.status)) return null;
  const end = String(g.ended_at || g.updated_at || '');
  const at = end ? messages.filter(m => String(m.created_at || '') <= end).length : messages.length;
  return {at, html: chatGoalLineHTML(g.status, g.objective, g.note)};
}
