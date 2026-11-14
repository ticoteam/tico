/* ui/app/chat-outline.ts — Outline: a long chat's prompts as a list, and the jump to any message in it
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The list is the humans' prompts across every page (GET /v2/conversations/{id}/outline), so a row can name an
// exchange that is not loaded yet; the jump pages back with the same request "Load older messages" uses.
const OUTLINE_MIN = 6;               // prompts before a chat is long enough to need it
const JUMP_PAGES = 40;               // pages a jump loads before it gives up (200 messages each)
// {slug, id, conversation}: a jump waiting for that bot's chat to load; `conversation` names the room it is in when that
// is not the bot's usual one (a Slack thread's room), and the chat opens that room when the reader is in it.
interface ChatJump {
  slug: string;
  id: string;
  conversation?: string | undefined;
}
// One row of the outline (backend/people_routes.py, GET /api/v2/conversations/{cid}/outline): a prompt the reader sent.
interface OutlinePrompt {
  id: string;
  created_at: string;
  text: string;
  task?: { title: string } | null;
}
interface OutlineResponse {
  prompts?: OutlinePrompt[] | undefined;
}
// The thread's scroller on the page, when a chat is drawn.
const chatThreadEl = () => $('#conv-thread') as HTMLElement | null;
let CHAT_JUMP: ChatJump | null = ENTRY_JUMP?.slug ? {slug: ENTRY_JUMP.slug, id: ENTRY_JUMP.id, conversation: ENTRY_JUMP.conversation} : null;

function chatOutlineSync(state: ChatState) {
  const conv = $('#conv') as HTMLElement | null;
  if (!conv || V2C !== state) return;
  chatIndexSync(state);
  let btn = $('#conv-outline-btn') as HTMLButtonElement | null;
  const prompts = state.messages.filter(m => actorPerson(m.sender_id) && !m.refs?.goal_action).length;
  const show = !!state.conv && (prompts >= OUTLINE_MIN || !!state.nextBefore);
  if (!show) { if (btn) btn.hidden = true; return; }
  if (!btn) {
    btn = Object.assign(document.createElement('button'), {type: 'button', id: 'conv-outline-btn', className: 'conv-outline-btn', textContent: 'Outline'});
    btn.setAttribute('aria-haspopup', 'dialog');
    conv.prepend(btn);
  }
  btn.hidden = false;
  btn.onclick = () => void chatOutlineOpen(state, btn);
}

async function chatOutlineOpen(state: ChatState, btn: HTMLButtonElement) {
  let d = $('#chat-outline') as HTMLDialogElement | null;
  if (!d) {
    d = document.createElement('dialog'); d.id = 'chat-outline'; d.className = 'chat-outline';
    d.setAttribute('aria-label', 'Outline');
    // A click on the backdrop lands on the dialog itself.
    d.addEventListener('click', ev => { if (ev.target === d && d) d.close(); });
    d.addEventListener('keydown', ev => {
      if ((ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') || !d) return;
      const rows: (HTMLElement | null)[] = [...d.querySelectorAll<HTMLElement>('.co-row')], at = rows.indexOf(document.activeElement as HTMLElement | null);
      const next = rows[Math.max(0, Math.min(rows.length - 1, at + (ev.key === 'ArrowDown' ? 1 : -1)))];
      if (next) { ev.preventDefault(); next.focus(); }
    });
    document.body.append(d);
  }
  const r = btn.getBoundingClientRect();
  d.style.setProperty('--co-top', `${Math.round(r.bottom + 6)}px`);
  d.style.setProperty('--co-right', `${Math.max(8, Math.round(window.innerWidth - r.right))}px`);
  d.innerHTML = '<div class="co-head"><b>Outline</b><button type="button" class="ghost co-close" aria-label="Close">✕</button></div><div class="co-list"><div class="co-empty">Loading…</div></div>';
  const close = d.querySelector<HTMLButtonElement>('.co-close');
  if (!close) throw new Error('Outline: the close button is missing');
  close.onclick = () => d.close();
  if (!d.open) d.showModal();
  let list: OutlinePrompt[];
  try {
    if (!state.conv) throw new Error('Outline: the chat has no conversation');
    list = (await get<OutlineResponse>(`/v2/conversations/${encodeURIComponent(state.conv.id)}/outline`)).prompts || [];
  }
  catch (error) {
    const failed = d.querySelector<HTMLElement>('.co-list');
    if (!failed) throw new Error('Outline: the list is missing');
    failed.innerHTML = `<div class="co-empty err">${esc(errorMessage(error, ''))}</div>`; return;
  }
  if (!d.open || V2C !== state) return;
  const box = d.querySelector<HTMLElement>('.co-list');
  if (!box) throw new Error('Outline: the list is missing');
  box.innerHTML = list.map(p => `<button type="button" class="co-row" data-id="${esc(p.id)}">
      <span class="co-text">${esc(p.text || 'Attached files.')}</span><time title="${esc(fmt(p.created_at))}">${esc(ago(p.created_at))}</time>
      ${p.task ? `<span class="co-task">${esc(p.task.title)}</span>` : ''}
    </button>`).join('') || '<div class="co-empty">No prompts yet.</div>';
  box.onclick = ev => {
    const row = eventElement(ev)?.closest<HTMLElement>('.co-row'); if (!row) return;
    d.close();
    void chatJumpTo(state, row.dataset.id);
  };
  // Newest at the bottom, as in the chat.
  box.scrollTop = box.scrollHeight;
  box.querySelector<HTMLElement>('.co-row:last-of-type')?.focus();
}

// Your messages on the thread's edge (ui/app/chat-index.ts). On a computer they are the one way to your prompts, so a
// chat with older pages reads its outline once, and a tick can name a message not loaded yet (the jump pages back to
// it); the messages on the page add what came since. A phone keeps the Outline instead (styles/chat.css).
function chatIndexSync(state: ChatState) {
  const thread = chatThreadEl();
  if (!thread) return;
  if (state.pidx && state.pidx.el.isConnected && state.thread === thread) { state.pidx.sync(); return; }
  state.pidx?.destroy();
  state.pidx = promptIndexMount({scroller: thread,
    row: id => thread.querySelector<HTMLElement>(`.conv-run[data-message="${CSS.escape(id)}"]`),
    go: id => chatJumpTo(state, id),
    items: () => {
      const loaded = state.messages.filter(m => m.id && m.sender_id === myActor() && !m.refs?.goal_action && !chatGoalControl(m))
        .map(m => ({id: m.id!, text: pidxText(m.body, m.refs?.attachments || []), at: m.created_at}));
      const have = new Set(loaded.map(i => i.id));
      const outline = (state.outlineList || []).filter(p => !have.has(p.id)).map(p => ({id: p.id, text: pidxText(p.text), at: p.created_at}));
      // In the feed's order: what is not loaded is older than everything on the page.
      return [...outline, ...loaded];
    }});
  const cid = state.conv?.id;
  if (cid && state.nextBefore && state.outlineFor !== cid) {
    state.outlineFor = cid;
    get<OutlineResponse>(`/v2/conversations/${encodeURIComponent(cid)}/outline`).then(r => {
      if (V2C !== state || state.conv?.id !== cid) return;
      state.outlineList = r.prompts || [];
      state.pidx?.sync();
    }, () => { if (state.outlineFor === cid) state.outlineFor = undefined; });
  }
}

// Load older pages until the message is on the page, then bring it into view and mark it for a moment.
async function chatJumpTo(state: ChatState, id: string | undefined) {
  let paged = false;
  for (let pages = 0; !state.messages.some(m => m.id === id); pages++) {
    if (!state.nextBefore || pages >= JUMP_PAGES) { toast('That message is not in this chat', true); return false; }
    try { if (!await v2ChatOlder(state)) return false; paged = true; }
    catch (error) { toast(errorMessage(error, ''), true); return false; }
  }
  state.stick?.release();
  v2ChatRender(state);
  // Paging and the top-only control can resize the scroller. Let that layout settle
  // before the jump, so a queued scroll-to-end event cannot reclaim following.
  if (paged) {
    await new Promise(done => requestAnimationFrame(done));
    if (V2C !== state) return false;
  }
  const thread = chatThreadEl(), el = thread?.querySelector<HTMLElement>(`[data-message="${CSS.escape(String(id))}"]`);
  if (!el) { toast('That message is hidden here', true); return false; }
  state.stick?.release();
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  el.scrollIntoView({block: 'center', behavior: still || paged ? 'auto' : 'smooth'});
  el.classList.remove('jump-mark'); void el.offsetWidth; el.classList.add('jump-mark');
  setTimeout(() => el.classList.remove('jump-mark'), 2000);
  v2Jump(state);
  return true;
}

// jumpToMessage(slug, messageId): open that bot's chat (or the Assistant) and jump to the message. Anything that
// links to an exchange, such as a source link, calls this; the draft in the composer stays as it was.
function jumpToMessage(slug: string, id: string) {
  if (V2C?.slug === slug && V2C?.loaded) return chatJumpTo(V2C, id);
  CHAT_JUMP = {slug, id};
  const href = slug === assistantBot() ? ASSISTANT : `#/bot/${encodeURIComponent(slug)}`;
  if (location.hash !== href) location.hash = href;
  return Promise.resolve(true);
}
// A shared link may name the bot by its slug; the chat is held by its id.
const chatJumpFor = (slug: string): boolean => !!CHAT_JUMP && (CHAT_JUMP.slug === slug || botByRef(CHAT_JUMP.slug)?.id === slug);
const chatJumpRoom = (slug: string): string => chatJumpFor(slug) ? CHAT_JUMP?.conversation || '' : '';
async function chatJumpPending(state: ChatState) {
  if (!CHAT_JUMP || !chatJumpFor(state.slug) || V2C !== state) return;
  const {id} = CHAT_JUMP; CHAT_JUMP = null;
  await chatJumpTo(state, id);
}
