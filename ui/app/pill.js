/* ui/app/pill.js — The composer pill at the foot of a bot's Chat tab
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- the pill (talk to a bot)
// The composer at the bottom of a bot's Chat tab: Send is a chat message, the caret offers task or reply.
// Each instance owns its own state.
const ICON_CLIP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11.5l-8 8a5 5 0 0 1-7-7l8.2-8.2a3.3 3.3 0 0 1 4.7 4.7l-8.2 8.2a1.7 1.7 0 0 1-2.4-2.4l7.6-7.6"/></svg>';
const ICON_SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h15M13 6l6 6-6 6"/></svg>';
const clock = ms => { const t = Math.max(0, Math.round((ms || 0) / 1000)), h = Math.floor(t / 3600);
  return (h ? `${h}:` : '') + String(Math.floor(t % 3600 / 60)).padStart(2, '0') + ':' + String(t % 60).padStart(2, '0'); };
const PILLS = new Set();
let BOT_PILL = null;
// What was typed to one bot is still there after visiting another and coming
// back. The text is kept per bot in this browser, so a reload keeps it too; attached files are
// kept for the session only. Sending clears it.
const CHAT_DRAFTS = 'hub.chat.drafts', CHAT_FILES = new Map();
const chatDrafts = () => { try { return JSON.parse(localStorage.getItem(CHAT_DRAFTS) || '{}') || {}; } catch { return {}; } };
function chatDraftSave(P) {
  if (P?.mode !== 'chat' || !P.slug) return;
  const text = pq(P, '.p-text')?.value || '', all = chatDrafts();
  if (text.trim()) all[P.slug] = text; else delete all[P.slug];
  if (P.files.length) CHAT_FILES.set(P.slug, P.files.slice()); else CHAT_FILES.delete(P.slug);
  try { localStorage.setItem(CHAT_DRAFTS, JSON.stringify(all)); } catch {}
}
function botPill(slug) {
  if (BOT_PILL?.slug === slug) return BOT_PILL;
  if (BOT_PILL) { chatDraftSave(BOT_PILL); PILLS.delete(BOT_PILL); }
  return BOT_PILL = pillRestore(makePill({mode: 'chat', slug}));
}
// The draft (and files) last left in this bot's box come back.
function pillRestore(P) {
  const draft = chatDrafts()[P.slug], files = CHAT_FILES.get(P.slug);
  if (draft) pq(P, '.p-text').value = draft;
  if (files?.length) { P.files.push(...files); pillChips(P); }
  if (draft || files?.length) pillButtons(P);
  return P;
}
const activeEmps = () => shownEmps().filter(e => e.status === 'active');
function toastSent(r) {
  const t = document.createElement('div'); t.className = 'toast';
  t.textContent = r.pending ? `Saved. Sending to ${S.emps.find(e => e.name === r.slug)?.display_name || r.slug} in the background—you can close this page.` : `Sent to ${S.emps.find(e => e.name === r.slug)?.display_name || r.slug}`;

  document.body.appendChild(t); setTimeout(() => t.remove(), 9000);
}

// A phone or tablet typing on its screen keyboard: a coarse pointer, or touch on a narrow screen.
const touchKeyboard = () => matchMedia('(pointer: coarse)').matches || (navigator.maxTouchPoints > 0 && innerWidth <= 760);
// cfg.send replaces where Send goes (the Assistant page posts to /v2/assistant/messages); cfg.files === false
// leaves out attaching, for a chat whose server route takes text only.
function makePill(cfg) {
  const P = {
    mode: cfg.mode, slug: cfg.slug || null, dest: cfg.slug,
    action: 'chat', needsIssue: null,
    el: null, files: [], send: cfg.send || null,
  };
  const attach = cfg.files !== false;
  // A bot's own chat (not the Assistant page) has the goal target and the "/" menu.
  const goals = P.mode === 'chat' && !!P.slug && !cfg.send;
  const el = P.el = document.createElement('div');
  el.className = 'ask pill-' + P.mode;
  el.innerHTML = `<div class="p-hint muted" hidden></div>
    <div class="p-pill">
      ${P.mode === 'chat' && P.slug ? `<button class="p-bot" type="button" aria-label="Switch bot" aria-haspopup="menu" aria-expanded="false" aria-controls="org-fan" title="Switch bot">${avatar(P.slug, 30)}</button>` : ''}
      <textarea class="p-text" rows="1" autocorrect="on" autocapitalize="sentences" spellcheck="true" aria-label="${esc(cfg.label || 'Message this bot')}" placeholder="${esc(cfg.placeholder || 'Type a message…')}"></textarea>
      ${goals ? '<button class="p-goal p-icon" type="button" title="Goal" aria-label="Goal" aria-pressed="false" hidden><span class="nav-icon" aria-hidden="true">target</span></button>' : ''}
      ${attach ? `<button class="p-attach p-icon" type="button" title="Attach files" aria-label="Attach files">${ICON_CLIP}</button>
      <input type="file" multiple hidden class="p-file">` : ''}
      <button class="p-send p-send-icon" type="button" aria-label="Send" title="Send">${ICON_SEND}</button>
    </div>
    <div class="p-chips"></div>
    <div class="p-cmd" hidden></div>`;

  const q = sel => el.querySelector(sel);
  const box = q('.p-text'), pill = q('.p-pill');
  // The cap is the textarea's CSS max-height: 120px, or up to 300px in a desktop bot chat (#524).
  // IPhone autocorrect never landed in the composer. Collapsing the box to
  // height:auto on every keystroke re-lays it out mid-word, which drops iOS's pending correction.
  // Grow only when the text overflows; collapse and re-measure only when text was removed.
  let lastLength = 0;
  const grow = () => {
    const shrank = box.value.length < lastLength; lastLength = box.value.length;
    if (shrank) { box.style.height = 'auto'; box.style.height = box.scrollHeight + 'px'; }
    else if (box.scrollHeight > box.clientHeight) box.style.height = box.scrollHeight + 'px';
  };
  box.addEventListener('input', () => { grow(); pillButtons(P); chatDraftSave(P); });
  // "lets do the triple return behavior on mobile send, but on desktop, lets
  // make it enter send, and shift enter new line." On a computer Return sends and Shift+Return is
  // a new line; on a phone Return is a line and three in a row send (below). Cmd/Ctrl+Return and
  // the arrow always send.
  box.addEventListener('keydown', ev => {
    if (ev.key !== 'Enter' || ev.isComposing) return;
    const send = ev.metaKey || ev.ctrlKey || (!ev.shiftKey && !touchKeyboard());
    if (send) { ev.preventDefault(); pillSend(P); }
  });
  // "if i press enter 3 times, then send it. if i press 1 or twice, page break."
  // Counted on beforeinput, not keydown, so a phone keyboard's Return counts too; the two line
  // breaks the first presses made come off before it sends.
  let breaks = 0;
  box.addEventListener('beforeinput', ev => {
    if (P.mode !== 'chat' || !touchKeyboard()) return;
    if (ev.inputType !== 'insertLineBreak' && ev.inputType !== 'insertParagraph') { breaks = 0; return; }
    const at = box.selectionStart;
    if (++breaks < 3 || at !== box.selectionEnd || box.value.slice(at - 2, at) !== '\n\n') return;
    ev.preventDefault(); breaks = 0;
    box.value = box.value.slice(0, at - 2) + box.value.slice(at);
    box.setSelectionRange(at - 2, at - 2);
    pillSend(P);
  });
  box.addEventListener('pointerdown', () => { breaks = 0; });
  if (attach) q('.p-attach').onclick = () => q('.p-file').click();
  if (goals) { q('.p-goal').onclick = () => chatGoalButton(); slashAttach(P); }
  if (q('.p-bot')) q('.p-bot').onclick = ev => { ev.preventDefault(); $('#org-fan').hidden ? orgFanOpen({chat: P.slug, anchor: q('.p-bot')}) : orgFanClose(); };
  if (attach) q('.p-file').onchange = ev => { P.files.push(...ev.target.files); pillChips(P); chatDraftSave(P); ev.target.value = ''; };
  box.addEventListener('paste', ev => {
    const clipboard = ev.clipboardData;
    if (!clipboard || !attach) return;
    const images = Array.from(clipboard.items || [])
      .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
      .map(item => item.getAsFile()).filter(Boolean);
    if (!images.length) return; // Ordinary text keeps the browser's normal paste behavior.
    ev.preventDefault();
    P.files.push(...images);
    const text = clipboard.getData('text/plain');
    if (text) {
      box.setRangeText(text, box.selectionStart, box.selectionEnd, 'end');
      box.dispatchEvent(new Event('input', {bubbles: true}));
    }
    pillChips(P);
  });
  q('.p-chips').onclick = ev => {
    const thumb = ev.target.closest('.p-thumb'); if (thumb) { ev.preventDefault(); chatImageOpen(thumb); return; }
    const i = ev.target.dataset.rm;
    if (i != null) {
      ev.preventDefault();
      const [gone] = P.files.splice(+i, 1);
      if (gone && PILL_PREVIEWS.has(gone)) { URL.revokeObjectURL(PILL_PREVIEWS.get(gone)); PILL_PREVIEWS.delete(gone); }
      pillChips(P);
    }
  };
  q('.p-send').onclick = () => pillSend(P);
  // Safari does not focus buttons on tap. Keep the composer in place until its
  // control click runs; restoring navigation on blur can swallow that tap.
  // Delegate so attachment-removal controls added after selection work too.
  el.addEventListener('pointerdown', event => {
    if (document.activeElement === box && event.target.closest('button,[data-rm]'))
      event.preventDefault();
  });
  PILLS.add(P);
  pillLabel(P); pillButtons(P);
  return P;
}
const pq = (P, sel) => P.el?.querySelector(sel);
// An empty box only dims Send: text that arrived without an input event (autofill, dictation, a tool) must still
// send on a click, and pillSend puts the cursor back in an empty box.
function pillButtons(P) {
  const box = pq(P, '.p-text'); if (!box) return;
  const fileChat = P.files.length && P.action === 'chat';
  const btn = pq(P, '.p-send');
  btn.classList.toggle('p-off', !box.value.trim() && !fileChat);
  btn.disabled = !!P.sending;
}
// "when i paste an image into a box, can we show a clickable preview of it?"
// An image waiting to be sent shows as a small thumbnail; a tap opens it full size.
const PILL_PREVIEWS = new WeakMap();
const pillPreview = f => {
  if (!/^image\//.test(f.type || '')) return '';
  if (!PILL_PREVIEWS.has(f)) PILL_PREVIEWS.set(f, URL.createObjectURL(f));
  return PILL_PREVIEWS.get(f);
};
function pillChips(P) {
  const el = pq(P, '.p-chips'); if (!el) return;
  el.innerHTML = P.files.map((f, i) => {
    const src = pillPreview(f);
    const thumb = src ? `<button class="p-thumb" type="button" data-name="${esc(f.name)}" title="Open ${esc(f.name)}" aria-label="Open ${esc(f.name)}"><img src="${esc(src)}" alt=""></button>` : '';
    return `<span${src ? ' class="has-thumb"' : ''}>${thumb}${esc(f.name)} <a href="#" data-rm="${i}" aria-label="remove ${esc(f.name)}">×</a></span>`;
  }).join('');
  pillButtons(P);
}
// The chat composer's send is an arrow: a busy word goes to its label, never over the icon.
const pillBtnSay = (btn, text) => {
  if (!btn) return;
  if (btn.classList.contains('p-send-icon')) { btn.setAttribute('aria-label', text || 'Send'); btn.title = text || 'Send'; }
  else btn.textContent = text;
};
// The label always says what Send will do
function pillLabel(P) {
  const btn = pq(P, '.p-send'); if (!btn) return;
  const word = P.action === 'task' ? 'Send as a task' : P.action === 'reply' ? `Reply on #${P.needsIssue}` : 'Send';
  btn.innerHTML = ICON_SEND; btn.setAttribute('aria-label', word); btn.title = word;
  const hint = pq(P, '.p-hint');
  const waiting = P.needsIssue && P.action === 'reply';
  hint.hidden = !waiting;
  if (waiting) hint.innerHTML = `<strong>${empName(P.slug)}</strong> needs you — your answer goes there and clears "Needs you".`;
}
// the bot page tells the pill when the newest task needs the viewer
function pillNeeds(P, issue) {
  if (!P) return;
  const was = P.needsIssue;
  P.needsIssue = issue || null;
  if (issue && !was && P.action === 'chat') P.action = 'reply';        // default to answering
  if (!issue && P.action === 'reply') P.action = 'chat';
  pillLabel(P);
}
function pillMenu(P, open) {
  const menu = pq(P, '.p-menu'); if (!menu) return;
  pq(P, '.p-caret')?.setAttribute('aria-expanded', String(!!open));
  menu.hidden = !open;
  if (!open) return;
  const row = (val, label, art, on) => `<button type="button" role="menuitemradio" data-val="${esc(val)}" aria-checked="${on}">${art}<span>${label}</span>${on ? '<span class="tick" aria-hidden="true">✓</span>' : ''}</button>`;
  menu.innerHTML = row('chat', 'Send <span class="muted">(chat)</span>', '<span class="auto" aria-hidden="true">✦</span>', P.action === 'chat')
      + row('task', 'Send as task', '<span class="auto" aria-hidden="true">＋</span>', P.action === 'task')
      + (P.needsIssue ? row('reply', `Reply on #${esc(P.needsIssue)}`, '<span class="auto" aria-hidden="true">↩</span>', P.action === 'reply') : '');
  menu.querySelectorAll('[data-val]').forEach(b => b.onclick = () => {
    P.action = b.dataset.val;
    pillLabel(P); pillButtons(P); pillMenu(P, false); pq(P, '.p-text').focus();
  });
}
function pillAcknowledge(P, text, files) {
  // A slow upload must not erase the person's next draft or newly selected files.
  const nextText = pq(P, '.p-text').value;
  const remaining = P.files.filter(file => !files.includes(file));
  pillClear(P);
  if (nextText.trim() !== text) pq(P, '.p-text').value = nextText;
  P.files.push(...remaining); pillChips(P); pillButtons(P); chatDraftSave(P);
}
function pillClear(P, keepText) {
  const box = pq(P, '.p-text');
  box.value = ''; box.style.height = 'auto';
  P.files.length = 0; pillChips(P); chatDraftSave(P);
  if (keepText) pillButtons(P);
}
// a chat message: the run starts at once and the thread tails it
async function pillChat(P, text) {
  const btn = pq(P, '.p-send'), label = btn.textContent;
  btn.disabled = true; pillBtnSay(btn, 'Sending…');
  const names = P.files.map(f => f.name);
  try {
    const fd = new FormData();
    fd.append('text', text);
    P.files.forEach(f => fd.append('files', f, f.name));
    const res = await fetch(`${API}/employees/${encodeURIComponent(P.slug)}/chat`, {method: 'POST', body: fd});
    const j = await res.json().catch(() => ({}));
    if (res.status === 409) { pillBusy(P, j, text); return; }
    if (!res.ok) throw new Error(j.error || res.statusText);
    convChatStarted(P.slug, j, text, names);
    pillClear(P);
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; pillBtnSay(btn, label); pillLabel(P); pillButtons(P); }
}
function pillBusy(P, j, text) {
  const out = pq(P, '.p-cmd'); if (!out) return;
  const name = empName(P.slug);
  out.hidden = false;
  out.innerHTML = j.error === 'busy' && j.issue
    ? `<div class="cmd-out"><span>${name} is busy on  — send this as a task instead, or wait.</span>
       <div class="row" style="margin-top:7px"><button class="primary" type="button" data-busy="task">Send as task</button>
       <button class="ghost" type="button" data-busy="wait">Wait</button></div></div>`
    : `<div class="cmd-out"><span>${name} cannot take a chat message right now${j.error ? ` (${esc(j.error)})` : ''}. Send it as a task instead, or wait.</span>
       <div class="row" style="margin-top:7px"><button class="primary" type="button" data-busy="task">Send as task</button>
       <button class="ghost" type="button" data-busy="wait">Wait</button></div></div>`;
  out.querySelector('[data-busy="wait"]').onclick = () => { out.hidden = true; out.innerHTML = ''; };
  out.querySelector('[data-busy="task"]').onclick = () => {
    out.hidden = true; out.innerHTML = '';
    P.action = 'task'; pillLabel(P);
    if (text) { pq(P, '.p-text').value = text; pillButtons(P); }
    pillSend(P);
  };
}
async function pillTask(P, text) {
  const slug = P.slug, btn = pq(P, '.p-send'), label = btn.textContent;
  btn.disabled = true; pillBtnSay(btn, 'Creating…');
  try {
    const fd = new FormData();
    fd.append('slug', slug); fd.append('title', text.split('\n')[0].trim().slice(0, 80));
    fd.append('body', text); fd.append('priority', 'p2'); fd.append('type', 'task');
    P.files.forEach(f => fd.append('files', f, f.name));
    const res = await fetch(`${API}/issues`, {method: 'POST', body: fd});
    const j = await res.json().catch(() => ({})); if (!res.ok) throw new Error(j.error || res.statusText);
    if (CONV?.slug === slug) { CONV.pending.push({text, number: j.number, url: j.url}); convRender(CONV); }
    pillClear(P); toast(`Queued as #${j.number}`);
    await refresh(true); if (BOT?.slug === slug) loadBotIssues(slug);
  } catch (e) { toast(e.message, true); }
  btn.disabled = false; pillBtnSay(btn, label); pillLabel(P); pillButtons(P);
}
async function pillReply(P, text) {
  const n = P.needsIssue, btn = pq(P, '.p-send'), label = btn.textContent;
  btn.disabled = true; pillBtnSay(btn, 'Replying…');
  try {
    await post(`/issues/${n}/comment`, {body: text, clear_needs_human: true});
    toast(`Replied on #${n}`);
    pillClear(P); await refresh(true);
    if (CONV?.slug === P.slug) loadConversation(P.slug, CONV);
  } catch (e) { toast(e.message, true); }
  btn.disabled = false; pillBtnSay(btn, label); pillLabel(P); pillButtons(P);
}
async function pillSend(P) {
  if (!P.el) return;
  const box = pq(P, '.p-text'), text = box.value.trim();
  if (!text && !(P.action === 'chat' && P.files.length)) { box.focus(); return; }
  if (P.send) return P.send(P, text);
  const slash = slashRun(P, text);
  if (slash === true) return;
  if (slash !== 'plain' && runCommand(text, pq(P, '.p-cmd'), P.slug)) { pillClear(P, true); return; }
  if (isKeeper(P.slug)) {                                     // hub.db, not an Issue comment
    return P.action === 'task' ? v2PillTask(P, text) : v2ChatSend(P, text);
  }
  if (P.action === 'task') return pillTask(P, text);
  if (P.action === 'reply' && P.needsIssue) return pillReply(P, text);
  return pillChat(P, text);
}
