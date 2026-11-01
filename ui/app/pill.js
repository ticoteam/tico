/* ui/app/pill.js — The composer pill at the foot of a bot's Chat tab
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- the pill (talk to a bot)
// The composer at the bottom of a bot's Chat tab: Send is a chat message, the caret offers task or reply.
// Each instance owns its own state.
const ICON_CLIP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11.5l-8 8a5 5 0 0 1-7-7l8.2-8.2a3.3 3.3 0 0 1 4.7 4.7l-8.2 8.2a1.7 1.7 0 0 1-2.4-2.4l7.6-7.6"/></svg>';
const ICON_STOP = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/></svg>';
const ICON_SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h15M13 6l6 6-6 6"/></svg>';
const clock = ms => { const t = Math.max(0, Math.round((ms || 0) / 1000)), h = Math.floor(t / 3600);
  return (h ? `${h}:` : '') + String(Math.floor(t % 3600 / 60)).padStart(2, '0') + ':' + String(t % 60).padStart(2, '0'); };
const PILLS = new Set();
let PILL_WHY_N = 0;
let BOT_PILL = null;
// What was typed to one bot is still there after visiting another and coming
// back. The text is kept per bot in this browser, so a reload keeps it too; attached files are
// kept for the session only. Sending clears it.
const CHAT_DRAFTS = 'hub.chat.drafts', CHAT_FILES = new Map();
const CHAT_PENDING_KEY = 'hub.chat.pending';
const chatPendingRead = () => {
  try {
    return new Map(JSON.parse(sessionStorage.getItem(CHAT_PENDING_KEY) || '[]').filter(([slug, u]) =>
      typeof slug === 'string' && u && typeof u.text === 'string' && typeof u.signature === 'string'
      && typeof u.key === 'string' && u.key.length > 0 && u.key.length <= 200 && Number.isFinite(u.at)));
  } catch { return new Map(); }
};
const CHAT_PENDING = chatPendingRead();             // per tab, including reloads; no in-flight promises are persisted
function chatPendingSave() {
  try {
    sessionStorage.setItem(CHAT_PENDING_KEY, JSON.stringify([...CHAT_PENDING].map(([slug, u]) =>
      [slug, {text: u.text, signature: u.signature, key: u.key, at: u.at}])));
  } catch {}                                      // like draft storage, memory still works when storage is unavailable
}
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
  P.unconfirmed = CHAT_PENDING.get(P.slug) || null;
  P.unconfirmed?.inFlight?.then(() => pillButtons(P));
  if (draft || files?.length) pillButtons(P);
  return P;
}
const activeEmps = () => shownEmps().filter(e => e.status === 'active');
function toastSent(r) {
  const t = document.createElement('div'); t.className = 'toast';
  t.textContent = r.pending ? `Saved. Sending to ${S.emps.find(e => e.name === r.slug)?.display_name || r.slug} in the background—you can close this page.` : `Sent to ${S.emps.find(e => e.name === r.slug)?.display_name || r.slug}`;

  document.body.appendChild(t); setTimeout(() => t.remove(), 9000);
}

// ----------------------------------------------------------------- how a message reaches its bot
// One vocabulary for the bot chat and task comments (docs/tasks.md "Comments"): Steer goes into the bot's running turn
// now, Queue right after its current turn (now when idle), Defer waits for its next turn there, at the latest after a
// while. A way is kept as one string: 'steer', 'queue', 'goal' or 'defer:<for>'. Set as goal is an action, not a way.
const DELIVERY_FOR = ['30m', '1h', '4h', '24h', '2d', '7d', 'never'];
const deliveryWay = mode => String(mode || '').split(':')[0];
const deliveryFor = mode => String(mode || '').split(':')[1] || '';
const deliveryForWord = f => f === 'never' ? 'Never' : f;
const deliveryWord = mode => {
  const way = deliveryWay(mode), f = deliveryFor(mode);
  if (way === 'defer') return f && f !== 'never' ? `Defer ${f}` : 'Defer';
  return {steer: 'Steer', queue: 'Queue', goal: 'Set as goal'}[way] || 'Send';
};
// The request fields for a way: none for no pick; defer carries its duration.
const deliveryBody = mode => {
  const way = deliveryWay(mode);
  if (!way) return {};
  return way === 'defer' ? {delivery: way, defer_for: deliveryFor(mode) || 'never'} : {delivery: way};
};
// The menu: Steer now, Queue, Defer with its durations, then Set as goal apart. `current` is checked; `deferFor` is
// the duration the Defer row itself picks.
function deliveryMenuHTML({steer = true, queue = true, goal = false, current = 'queue', deferFor = '24h'}) {
  const row = (val, label, on) => `<button type="button" role="menuitemradio" aria-checked="${on}" data-deliver="${esc(val)}"><span>${esc(label)}</span>${
    on ? '<span class="tick" aria-hidden="true">✓</span>' : ''}</button>`;
  const way = deliveryWay(current), f = way === 'defer' ? deliveryFor(current) : '';
  const chips = DELIVERY_FOR.map(d => `<button type="button" role="menuitemradio" aria-checked="${f === d}" aria-label="Defer ${esc(deliveryForWord(d))}" data-deliver="defer:${d}">${esc(deliveryForWord(d))}</button>`).join('');
  return (steer ? row('steer', 'Steer now', way === 'steer') : '') + (queue ? row('queue', 'Queue', way === 'queue') : '')
    + row('defer:' + (f || deferFor), 'Defer', way === 'defer')
    + `<div class="p-for" role="group" aria-label="Defer for">${chips}</div>`
    + (goal ? `<div class="sep" role="separator"></div>${row('goal', 'Set as goal', way === 'goal')}` : '');
}
// Arrows move through the menu's buttons; the menu's own handlers do the rest.
function deliveryMenuKeys(menu, ev) {
  if (!['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight'].includes(ev.key)) return;
  const all = [...menu.querySelectorAll('button')], i = all.indexOf(document.activeElement);
  if (i < 0) return;
  ev.preventDefault();
  all[(i + (ev.key === 'ArrowDown' || ev.key === 'ArrowRight' ? 1 : -1) + all.length) % all.length].focus();
}

// A phone or tablet typing on its screen keyboard: a coarse pointer, or touch on a narrow screen.
const touchKeyboard = () => matchMedia('(pointer: coarse)').matches || (navigator.maxTouchPoints > 0 && innerWidth <= 760);
// cfg.send replaces where Send goes (the Assistant page posts to /v2/assistant/messages); cfg.files === false
// leaves out attaching, for a chat whose server route takes text only.
function makePill(cfg) {
  const P = {
    mode: cfg.mode, slug: cfg.slug || null, dest: cfg.slug,
    action: 'chat', needsIssue: null,
    el: null, files: [], send: cfg.send || null, way: null,
  };
  const attach = cfg.files !== false;
  // A bot's own chat (not the Assistant page) has the goal target, the "/" menu and the ways to send.
  const goals = P.ways = P.mode === 'chat' && !!P.slug && !cfg.send;
  const el = P.el = document.createElement('div');
  el.className = 'ask pill-' + P.mode;
  el.innerHTML = `<div class="p-hint muted" hidden></div>
    <div class="p-pill">
      ${P.mode === 'chat' && P.slug ? `<button class="p-bot" type="button" aria-label="Switch bot" aria-haspopup="menu" aria-expanded="false" aria-controls="org-fan" title="Switch bot">${avatar(P.slug, 30)}</button>` : ''}
      <textarea class="p-text" rows="1" autocorrect="on" autocapitalize="sentences" spellcheck="true" aria-label="${esc(cfg.label || 'Message this bot')}" placeholder="${esc(cfg.placeholder || 'Type a message…')}"></textarea>
      ${goals ? '<button class="p-goal p-icon" type="button" title="Goal" aria-label="Goal" aria-pressed="false" hidden><span class="nav-icon" aria-hidden="true">target</span></button>' : ''}
      ${attach ? `<button class="p-attach p-icon" type="button" title="Attach files" aria-label="Attach files">${ICON_CLIP}</button>
      <input type="file" multiple hidden class="p-file">` : ''}
      ${goals ? `<span class="p-sendbox"><button class="p-stop p-send-icon" type="button" aria-label="Stop" title="Stop" hidden>${ICON_STOP}</button>` : ''}
      <button class="p-send p-send-icon" type="button" aria-label="Send" title="Send">${ICON_SEND}</button>
      ${goals ? `<button class="p-way p-icon" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="Ways to send" title="Ways to send" hidden><span class="nav-icon" aria-hidden="true">expand_more</span></button>
      <div class="p-menu p-ways" role="menu" aria-label="Ways to send" hidden></div></span>` : ''}
    </div>
    <div class="p-slow muted" role="status" hidden></div>
    <div class="p-why muted" id="p-why-${++PILL_WHY_N}" role="status"></div>
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
  box.addEventListener('input', () => { P.sendNotice = ''; grow(); pillButtons(P); chatDraftSave(P); if (P.unconfirmed) chatForgetUnconfirmed(P); });
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
  // Acknowledge the physical press before validation or any request. Keep the
  // real button as the target, including its transparent border, and retain focus.
  el.querySelectorAll('.p-send, .p-stop').forEach(btn => btn.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    const started = performance.now();
    P.releasePress?.();
    btn.classList.add('p-pressed');
    const release = event => {
      window.removeEventListener('pointerup', release);
      window.removeEventListener('pointercancel', release);
      window.removeEventListener('blur', release);
      clearTimeout(P.pressTimer);
      const clear = () => btn.classList.remove('p-pressed');
      if (event?.type === 'pointerup') P.pressTimer = setTimeout(clear, Math.max(0, 120 - (performance.now() - started)));
      else clear();
    };
    P.releasePress = release;
    window.addEventListener('pointerup', release);
    window.addEventListener('pointercancel', release);
    window.addEventListener('blur', release);
  }));
  if (goals) {
    q('.p-stop').onclick = () => pillStop(P);
    // The caret: how the next message reaches the bot. A way picked here is for one send.
    q('.p-way').onclick = () => pillWayMenu(P, q('.p-ways').hidden);
    q('.p-ways').onclick = ev => { const b = ev.target.closest?.('[data-deliver]'); if (b) pillWayPick(P, b.dataset.deliver); };
    q('.p-ways').addEventListener('keydown', ev => deliveryMenuKeys(q('.p-ways'), ev));
    // Escape closes the menu only, wherever focus is in the composer.
    el.addEventListener('keydown', ev => {
      if (ev.key !== 'Escape' || q('.p-ways').hidden) return;
      ev.preventDefault(); ev.stopPropagation();
      pillWayMenu(P, false);
      if (!touchKeyboard()) box.focus();
    }, true);
  }
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
  chatForgetUnconfirmed(P);
  const fileChat = P.files.length && P.action === 'chat';
  const btn = pq(P, '.p-send');
  btn.classList.toggle('p-off', !box.value.trim() && !fileChat);
  btn.disabled = !!P.sending || !!P.unconfirmed?.inFlight || P.action === 'chat' && !P.send && !pillGoalMode(P) && chatReplayExpired(P);
  // While the bot runs (chatStopSync sets P.running), Stop takes Send's place until something is typed: a message
  // typed meanwhile still queues for after the run.
  const stop = pq(P, '.p-stop');
  let stopping = false;
  if (stop) {
    const show = stopping = !!P.running && !box.value.trim() && !P.files.length;
    stop.hidden = !show; btn.hidden = show; stop.disabled = !!P.stopping;
  }
  const caret = pq(P, '.p-way');
  if (caret) {
    caret.hidden = !pillWaysOn(P) || stopping;
    caret.disabled = btn.disabled;
    if (caret.hidden || caret.disabled) pillWayMenu(P, false);
  }
  pillWhy(P);
}
// The bot chat's ways to send: a hub bot's own chat, sending a message (not a task).
const pillWaysOn = P => !!P.ways && isKeeper(P.slug) && P.action !== 'task';
// The way the next send goes, when it is not plain Send (Steer, as a chat message always went): the menu's pick, for
// one send.
const pillWay = P => pillWaysOn(P) && !pillGoalMode(P) ? P.way : null;
const PILL_DEFER_DEFAULT = 'never';
function pillWayMenu(P, open) {
  const menu = pq(P, '.p-ways'), caret = pq(P, '.p-way'); if (!menu || !caret) return;
  if (!open && menu.hidden) return;
  const state = V2C?.slug === P.slug ? V2C : null;
  const goal = !!P.goalMode || !!state?.goalSupported && !goalPinned(state.goal);
  const current = pillGoalMode(P) ? 'goal' : P.way || 'steer';
  menu.innerHTML = open ? deliveryMenuHTML({goal, current, deferFor: deliveryWay(current) === 'defer' ? deliveryFor(current) : PILL_DEFER_DEFAULT}) : '';
  menu.hidden = !open;
  caret.setAttribute('aria-expanded', String(!!open));
  // On a phone the keyboard stays up: focus stays in the box.
  if (open && !touchKeyboard()) (menu.querySelector('[aria-checked="true"]') || menu.querySelector('button'))?.focus();
}
// A picked way is for one send: once a message sent that way lands, Send is plain Send again.
function pillWaySent(P, extra) {
  if (!P.way || !extra?.delivery) return;
  const was = deliveryBody(P.way);
  if (was.delivery === extra.delivery && (was.defer_for || '') === (extra.defer_for || '')) P.way = null;
}
function pillWayPick(P, mode) {
  pillWayMenu(P, false);
  if (mode === 'goal') {
    P.way = null;
    if (!P.goalMode) chatGoalButton();               // the same goal mode as the target button
  } else {
    if (P.goalMode) chatGoalButton();                // a way to send leaves goal mode
    P.way = mode === 'steer' ? null : mode;
  }
  pillLabel(P); pillButtons(P);
  if (!touchKeyboard()) pq(P, '.p-text')?.focus();
}
// A press outside the open menu closes it.
document.addEventListener('pointerdown', ev => {
  for (const P of PILLS) {
    const box = pq(P, '.p-sendbox');
    if (box && !pq(P, '.p-ways').hidden && !box.contains(ev.target)) pillWayMenu(P, false);
  }
});
// Ask the bot's running turn to stop; the button stays pressed until the run ends.
async function pillStop(P) {
  if (!P.running || P.stopping) return;
  P.stopping = true; pillButtons(P);
  try { await post(`/v2/bots/${encodeURIComponent(P.slug)}/stop`, P.running.attempt_id ? {attempt_id: P.running.attempt_id} : {}); }
  catch (e) { P.stopping = false; pillButtons(P); toast(`Not stopped: ${e.message}`, true); }
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
  P.sendNotice = '';
  const el = pq(P, '.p-chips'); if (!el) return;
  el.innerHTML = P.files.map((f, i) => {
    const src = pillPreview(f);
    const thumb = src ? `<button class="p-thumb" type="button" data-name="${esc(f.name)}" title="Open ${esc(f.name)}" aria-label="Open ${esc(f.name)}"><img src="${esc(src)}" alt=""></button>` : '';
    return `<span${src ? ' class="has-thumb"' : ''}>${thumb}${esc(f.name)} <a href="#" data-rm="${i}" aria-label="remove ${esc(f.name)}">×</a></span>`;
  }).join('');
  pillLabel(P); pillButtons(P);
}
// The chat composer's send is an arrow: a busy word goes to its label, never over the icon. A busy word said while
// the button is disabled stays until it is enabled again, so pillLabel's action word never covers it.
const pillBtnSay = (btn, text) => {
  if (!btn) return;
  if (btn.disabled && text) btn.dataset.busy = text; else delete btn.dataset.busy;
  if (btn.classList.contains('p-send-icon')) { btn.setAttribute('aria-label', text || 'Send'); btn.title = PILL_BUSY[text] || text || 'Send'; }
  else btn.textContent = text;
  // Every busy start (a send, a goal save, a task) goes through here, so the reason and the phone line come at once.
  for (const P of PILLS) if (P.el?.contains(btn)) pillWhy(P);
};
// What a disabled (or, for an empty box, dimmed) Send or Stop says on hover and to a screen reader, in plain words.
// On a phone, with no hover, the busy reasons show as a line under the box (ui/styles/chat.css).
const PILL_BUSY = {'Sending…': 'Sending… waiting for Tico to answer'};
function pillWhy(P) {
  const btn = pq(P, '.p-send'), why = pq(P, '.p-why'); if (!btn || !why) return;
  const busy = btn.disabled && (btn.dataset.busy || (P.sending || P.unconfirmed?.inFlight ? 'Sending…' : ''));
  const stop = pq(P, '.p-stop'), stopping = stop && !stop.hidden && stop.disabled;
  let text = '';
  if (stopping) text = 'Stopping…';
  else if (busy) text = busy === 'Sending…' && P.sendRetrying ? 'Sending… trying again' : PILL_BUSY[busy] || busy;
  else if (btn.disabled && chatReplayExpired(P)) text = CHAT_REPLAY_EXPIRED;
  else if (btn.classList.contains('p-off'))
    text = pillGoalMode(P) ? 'Type a goal to set' : P.action === 'chat' ? 'Type a message or attach a file to send' : 'Type a message to send';
  if (P.sendNotice && !busy && !stopping) text = P.sendNotice;
  why.textContent = text;
  why.classList.toggle('p-why-notice', !!P.sendNotice && !busy && !stopping);
  why.classList.toggle('p-why-busy', !!(stopping || busy || btn.disabled && chatReplayExpired(P)));
  if (stop) {
    stop.title = stopping ? text : 'Stop';
    if (stopping) stop.setAttribute('aria-describedby', why.id); else stop.removeAttribute('aria-describedby');
  }
  if (text && !stopping) { btn.title = text; btn.setAttribute('aria-describedby', why.id); }
  else { btn.title = btn.dataset.word || 'Send'; btn.removeAttribute('aria-describedby'); }
}
// Goal mode (ui/app/chat-goal.js) turns Send into Set goal. Attached files always go as a message, so they are never lost.
const pillGoalMode = P => !!P.goalMode && P.action === 'chat' && !P.send && !P.files.length;
// The label always says what Send will do; in goal mode it says so in words, and so does the empty box.
function pillLabel(P) {
  const btn = pq(P, '.p-send'); if (!btn) return;
  const goal = pillGoalMode(P), way = pillWay(P);
  const word = goal ? 'Set goal' : way ? deliveryWord(way) : P.action === 'task' ? 'Send as a task' : P.action === 'reply' ? `Reply on #${P.needsIssue}` : 'Send';
  // A redraw that replaces the arrow between press and release loses the click in every engine, so the
  // contents change only when goal mode or a picked way (Steer, Defer) swaps the arrow for words (and
  // ui/styles/chat.css makes the button itself the press target).
  const shows = goal || way ? word : 'icon';
  if (btn.dataset.shows !== shows) { btn.innerHTML = goal || way ? esc(word) : ICON_SEND; btn.dataset.shows = shows; }
  btn.dataset.word = word;
  btn.setAttribute('aria-label', btn.disabled && btn.dataset.busy || word);
  btn.classList.toggle('p-send-goal', goal || !!way);
  pillWhy(P);
  const box = pq(P, '.p-text');
  if (box) {
    box.dataset.placeholder ??= box.placeholder;
    box.placeholder = goal ? `Goal for ${botDisplayName(P.slug)}…` : box.dataset.placeholder;
  }
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
function pillAcknowledge(P, text, files, scope) {
  sessionAcknowledge(pq(P, '.p-text'), scope);
  // A slow upload must not erase the person's next draft or newly selected files.
  const nextText = pq(P, '.p-text').value;
  const remaining = P.files.filter(file => !files.includes(file));
  pillClear(P);
  if (nextText.trim() !== text) {
    const box = pq(P, '.p-text');
    box.value = nextText; box.dispatchEvent(new Event('input', {bubbles: true}));
  }
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
  const files = P.files.slice(), scope = sessionScope(pq(P, '.p-text'));
  const names = files.map(f => f.name);
  try {
    const fd = new FormData();
    fd.append('text', text);
    P.files.forEach(f => fd.append('files', f, f.name));
    const res = await fetch(`${API}/employees/${encodeURIComponent(P.slug)}/chat`, {method: 'POST', body: await memoryBody(fd)});
    const j = await res.json().catch(() => ({}));
    if (res.status === 409) { pillBusy(P, j, text); return; }
    if (!res.ok) throw new Error(j.error || res.statusText);
    convChatStarted(P.slug, j, text, names);
    pillAcknowledge(P, text, files, scope);
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
  const files = P.files.slice(), scope = sessionScope(pq(P, '.p-text'));
  try {
    const fd = new FormData();
    fd.append('slug', slug); fd.append('title', text.split('\n')[0].trim().slice(0, 80));
    fd.append('body', text); fd.append('priority', 'p2'); fd.append('type', 'task');
    P.files.forEach(f => fd.append('files', f, f.name));
    const res = await fetch(`${API}/issues`, {method: 'POST', body: await memoryBody(fd)});
    const j = await res.json().catch(() => ({})); if (!res.ok) throw new Error(j.error || res.statusText);
    if (CONV?.slug === slug) { CONV.pending.push({text, number: j.number, url: j.url}); CONV.sent = true; convRender(CONV); }
    pillAcknowledge(P, text, files, scope); toast(`Queued as #${j.number}`);
    await refresh(true); if (BOT?.slug === slug) loadBotIssues(slug);
  } catch (e) { toast(e.message, true); }
  btn.disabled = false; pillBtnSay(btn, label); pillLabel(P); pillButtons(P);
}
async function pillReply(P, text) {
  const n = P.needsIssue, btn = pq(P, '.p-send'), label = btn.textContent;
  btn.disabled = true; pillBtnSay(btn, 'Replying…');
  const files = P.files.slice(), scope = sessionScope(pq(P, '.p-text'));
  try {
    await post(`/issues/${n}/comment`, {body: text, clear_needs_human: true});
    toast(`Replied on #${n}`);
    pillAcknowledge(P, text, files, scope); await refresh(true);
    if (CONV?.slug === P.slug) loadConversation(P.slug, CONV);
  } catch (e) { toast(e.message, true); }
  btn.disabled = false; pillBtnSay(btn, label); pillLabel(P); pillButtons(P);
}
async function pillSend(P) {
  if (!P.el) return;
  const box = pq(P, '.p-text'), text = box.value.trim();
  if (!text && !(P.action === 'chat' && P.files.length)) {
    P.sendNotice = pillGoalMode(P) ? 'Type a goal to set' : P.action === 'chat' ? 'Type a message or attach a file to send' : 'Type a message to send';
    pillWhy(P); box.focus(); return;
  }
  P.sendNotice = ''; pillWhy(P);
  // Goal mode keeps the familiar Send button and Enter shortcut; only the destination changes.
  // The Assistant's custom sender and explicit task/reply actions retain their own contracts.
  if (pillGoalMode(P)) return chatGoalSend(P, text);
  if (P.send) return P.send(P, text);
  const slash = slashRun(P, text);
  if (slash === true) return;
  if (slash !== 'plain' && runCommand(text, pq(P, '.p-cmd'), P.slug)) {
    sessionAcknowledge(box); pillClear(P, true); return;
  }
  if (isKeeper(P.slug)) {                                     // hub.db, not an Issue comment
    // Plain Send names no way (the server steers, as before); a picked way goes once (v2ChatSend puts the arrow back).
    return P.action === 'task' ? v2PillTask(P, text) : v2ChatSend(P, text, P.slug, {}, deliveryBody(pillWay(P)));
  }
  if (P.action === 'task') return pillTask(P, text);
  if (P.action === 'reply' && P.needsIssue) return pillReply(P, text);
  return pillChat(P, text);
}
