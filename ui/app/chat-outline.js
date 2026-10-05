/* ui/app/chat-outline.js — Outline: a long chat's prompts as a list, and the jump to any message in it
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The list is the humans' prompts across every page (GET /v2/conversations/{id}/outline), so a row can name an
// exchange that is not loaded yet; the jump pages back with the same request "Load older messages" uses.
const OUTLINE_MIN = 6;               // prompts before a chat is long enough to need it
const JUMP_PAGES = 40;               // pages a jump loads before it gives up (200 messages each)
let CHAT_JUMP = null;                // {slug, id}: a jump waiting for that bot's chat to load

function chatOutlineSync(state) {
  const conv = $('#conv');
  if (!conv || V2C !== state) return;
  let btn = $('#conv-outline-btn');
  const prompts = state.messages.filter(m => actorPerson(m.from_actor) && !m.refs?.goal_action).length;
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

async function chatOutlineOpen(state, btn) {
  let d = $('#chat-outline');
  if (!d) {
    d = document.createElement('dialog'); d.id = 'chat-outline'; d.className = 'chat-outline';
    d.setAttribute('aria-label', 'Outline');
    // A click on the backdrop lands on the dialog itself.
    d.addEventListener('click', ev => { if (ev.target === d) d.close(); });
    d.addEventListener('keydown', ev => {
      if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') return;
      const rows = [...d.querySelectorAll('.co-row')], at = rows.indexOf(document.activeElement);
      const next = rows[Math.max(0, Math.min(rows.length - 1, at + (ev.key === 'ArrowDown' ? 1 : -1)))];
      if (next) { ev.preventDefault(); next.focus(); }
    });
    document.body.append(d);
  }
  const r = btn.getBoundingClientRect();
  d.style.setProperty('--co-top', `${Math.round(r.bottom + 6)}px`);
  d.style.setProperty('--co-right', `${Math.max(8, Math.round(window.innerWidth - r.right))}px`);
  d.innerHTML = '<div class="co-head"><b>Outline</b><button type="button" class="ghost co-close" aria-label="Close">✕</button></div><div class="co-list"><div class="co-empty">Loading…</div></div>';
  d.querySelector('.co-close').onclick = () => d.close();
  if (!d.open) d.showModal();
  let list;
  try { list = (await get(`/v2/conversations/${encodeURIComponent(state.conv.id)}/outline`)).prompts || []; }
  catch (error) { d.querySelector('.co-list').innerHTML = `<div class="co-empty err">${esc(error.message)}</div>`; return; }
  if (!d.open || V2C !== state) return;
  const box = d.querySelector('.co-list');
  box.innerHTML = list.map(p => `<button type="button" class="co-row" data-id="${esc(p.id)}">
      <span class="co-text">${esc(p.text || 'Attached files.')}</span><time title="${esc(fmt(p.created))}">${esc(ago(p.created))}</time>
      ${p.task ? `<span class="co-task">${esc(p.task.title)}</span>` : ''}
    </button>`).join('') || '<div class="co-empty">No prompts yet.</div>';
  box.onclick = ev => {
    const row = ev.target.closest('.co-row'); if (!row) return;
    d.close();
    void chatJumpTo(state, row.dataset.id);
  };
  // Newest at the bottom, as in the chat.
  box.scrollTop = box.scrollHeight;
  box.querySelector('.co-row:last-of-type')?.focus();
}

// Load older pages until the message is on the page, then bring it into view and mark it for a moment.
async function chatJumpTo(state, id) {
  for (let pages = 0; !state.messages.some(m => m.id === id); pages++) {
    if (!state.nextBefore || pages >= JUMP_PAGES) { toast('That message is not in this chat', true); return false; }
    try { if (!await v2ChatOlder(state)) return false; }
    catch (error) { toast(error.message, true); return false; }
  }
  v2ChatRender(state);
  const thread = $('#conv-thread'), el = thread?.querySelector(`[data-message="${CSS.escape(id)}"]`);
  if (!el) { toast('That message is hidden here', true); return false; }
  state.followLatest = false;
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  el.scrollIntoView({block: 'center', behavior: still ? 'auto' : 'smooth'});
  el.classList.remove('jump-mark'); void el.offsetWidth; el.classList.add('jump-mark');
  setTimeout(() => el.classList.remove('jump-mark'), 2000);
  v2Jump(state);
  return true;
}

// jumpToMessage(slug, messageId): open that bot's chat (or the Assistant) and jump to the message. Anything that
// links to an exchange, such as a source link, calls this; the draft in the composer stays as it was.
function jumpToMessage(slug, id) {
  if (V2C?.slug === slug && V2C.loaded) return chatJumpTo(V2C, id);
  CHAT_JUMP = {slug, id};
  const href = slug === assistantBot() ? ASSISTANT : `#/bot/${encodeURIComponent(slug)}`;
  if (location.hash !== href) location.hash = href;
  return Promise.resolve(true);
}
async function chatJumpPending(state) {
  if (!CHAT_JUMP || CHAT_JUMP.slug !== state.slug || V2C !== state) return;
  const {id} = CHAT_JUMP; CHAT_JUMP = null;
  await chatJumpTo(state, id);
}
