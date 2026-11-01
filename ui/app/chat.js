/* ui/app/chat.js — Bot chat over messages: load, render, send, live reply stream
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ---- chat over messages, with the reply streamed from the keeper's turn ----
let V2C = null;                                   // the live v2 chat; one per visit, like CONV
// "every switch is slow... bring down switching to less than 1s". Each bot's
// last chat (its conversation and messages) and tasks stay in memory for this page load, so going
// back to a bot paints what it showed at once while the fresh copy loads behind it.
const CHAT_CACHE = new Map(), BOT_TASKS_CACHE = new Map();
function v2ChatStop() {
  if (!V2C) return;
  try { V2C.es?.close(); } catch {}
  V2C.liveOff?.();
  clearInterval(V2C.poll);
  clearTimeout(V2C.waitTimer);
  V2C = null;
}
// Which of the viewer's rooms with a bot is its chat: the shared room, or one of their own. The bot page shows this
// one room.
function v2ChatMode(slug) {
  slug = botBranchChatTarget(slug);
  const bot = (S.emps || []).find(e => e.name === slug);
  const configured = bot?.thread_mode;
  // A shared room belongs to the people the bot works for who may read it; anyone else talks to it in a room of their own.
  const member = S.me?.role === 'owner' || (bot?.users || []).some(u => u.id === S.me?.id);
  return slug === assistantBot() || !member || (bot?.my_access && !bot.my_access.read) ? 'personal'
    : (configured || (slug === 'cpo' ? 'shared' : 'personal'));
}
function v2ChatRoom(conversations, slug, mode = v2ChatMode(slug)) {
  const me = myActor();
  slug = botBranchChatTarget(slug);
  return (conversations || []).find(c => String(c.kind) === 'chat' && !c.task_id
    && !c.closed_at
    && (c.participants || []).includes(`bot:${slug}`)
    && String(c.scope || 'direct') === mode
    && (!me || (c.participants || []).includes(me))) || null;
}
// A run still to show as live: not one that finished, nor one a person stopped or dismissed.
const v2RunLive = x => !!x && !['completed', 'cancelled'].includes(x.state);
// `room` is a chat the caller already holds (the Assistant page: GET /v2/assistant gives its room and messages), so
// nothing is looked up; `empty` is what an empty thread shows instead of the default line.
async function v2ChatLoad(slug, room = null) {
  v2ChatStop();
  const mode = v2ChatMode(slug);
  const state = V2C = {slug, mode, conv: null, messages: [], mine: [], live: null, es: null, poll: 0, rendered: false,
                       loaded: false, failed: false, empty: room?.empty || ''};
  const seen = CHAT_CACHE.get(slug);
  if (room) {
    const x = room.execution;
    Object.assign(state, {conv: room.conv, messages: room.messages || [], nextBefore: room.nextBefore, execution: x,
                          live: S.me?.cloud && v2RunLive(x) ? {text: x.text || ''} : null, listed: true, loaded: true});
    v2ChatRender(state);
  } else if (seen && seen.mode === mode && !chatJumpRoom(slug)) {
    Object.assign(state, {conv: seen.conv, messages: seen.messages, nextBefore: seen.nextBefore, execution: seen.execution,
                          live: seen.live, listed: true, loaded: true});
    v2ChatRender(state);                               // what it showed last time, at once
  }
  await v2ChatFind(state);
  if (V2C !== state) return;
  v2ChatRender(state);
  void chatJumpPending(state);
  if (S.me?.cloud && state.conv) v2ChatStream(state, true);
  void chatGoalLoad(state);
  v2BotsLoad(state);
  state.poll = setInterval(async () => {
    if (document.hidden || state.live || V2C !== state) return;
    // A load that failed keeps trying here; "Nothing yet" is only ever said about a loaded chat.
    if (!state.loaded) {
      await v2ChatFind(state);
      if (V2C !== state) return;
      v2ChatRender(state);
      if (state.loaded && S.me?.cloud && state.conv) v2ChatStream(state, true);
      void chatGoalLoad(state);
      return;
    }
    // A chat that was empty is looked up again: someone may have started it (another tab, a bot page's Start setup).
    if (!state.conv) {
      state.listed = false;
      await v2ChatFind(state);
      if (V2C !== state) return;
      if (state.conv) { v2ChatRender(state); if (S.me?.cloud) v2ChatStream(state, true); void chatGoalLoad(state); } else state.failed = false;
      return;
    }
    // Live events carry every change to this chat (ui/app/live.js); the poll reads it only without them.
    if (S.me?.cloud && state.liveOff && liveAvailable()) return;
    v2ChatMessages(state).then(() => v2ChatRender(state));
    void chatGoalLoad(state);
  }, 15000);
}
// Find my chat with this bot and its messages, trying again quietly when a request fails (a
// deploy restarting the hub, a phone on a weak signal). A failed load showed
// "Nothing yet. Say something below." for a bot with a long history.
async function v2ChatFind(state) {
  for (const wait of [0, 800, 2500]) {
    if (wait) await new Promise(done => setTimeout(done, wait));
    if (V2C !== state) return;
    if (!state.listed) {
      const list = await v2Get(`/v2/conversations?chat_with=${encodeURIComponent(state.slug)}`);
      if (!list) continue;
      if (!state.listed) {                        // a message sent while this loaded already put its chat in place
        state.listed = true;
        // A link to a message in another of my rooms with this bot opens that room; its mode keeps it out of the cache
        // the usual room is read from.
        const want = chatJumpRoom(state.slug), linked = want && (list.conversations || []).find(c => c.id === want);
        if (linked) state.mode = 'linked';
        state.conv = linked || v2ChatRoom(list.conversations, state.slug, state.mode);
      }
    }
    if (!state.conv || await v2ChatMessages(state)) { state.loaded = true; state.failed = false; return; }
  }
  state.failed = true;
}
// What this bot asked, answered, or was handed by other bots (asks and task conversations), so a person sees the COO
// check with the CMO from either page. They are side traffic in the chat (chatSideKind): each message joins the
// folded line where its time falls. Each conversation comes as its newest message until it is opened
// (v2BotsOpen). The owner-only route answers 403 for anyone else, and nothing is added.
const BOTS_SHOWN = 5;
// A chat opens at its newest 50 messages, at most 160 KB of them; older pages load on request (v2ChatOlder).
const CHAT_PAGE = 'limit=50&max_bytes=163840';
async function v2BotsLoad(state) {
  if (V2C !== state) return;
  const list = await v2Get(`/v2/conversations?bot=${encodeURIComponent(state.slug)}`);
  if (V2C !== state || !list) return;
  const me = `bot:${state.slug}`;
  const convs = (list.conversations || [])
    .filter(c => c.kind !== 'chat' && c.kind !== 'notice' && c.id !== state.conv?.id
      && (c.participants || []).some(p => p !== me && String(p).startsWith('bot:')))
    .slice(0, BOTS_SHOWN);
  if (!convs.length) return;
  const bodies = await Promise.all(convs.map(c => v2Get(`/v2/conversations/${encodeURIComponent(c.id)}/messages?limit=1`)));
  if (V2C !== state) return;
  state.botTalk = convs.flatMap((c, n) => (bodies[n]?.messages || []).map(m => ({...m,
    elsewhere: {kind: c.kind, subject: c.subject || '', id: c.id, preview: !!bodies[n].has_more}})));
  if (state.botTalk.length) v2ChatRender(state);
}
// A bot-to-bot conversation shown as its newest message: its newest page in place of the preview, then each
// "Earlier messages" loads the page before the oldest one shown (its cursor rides on that message).
async function v2BotsOpen(state, cid) {
  const mine = (state.botTalk || []).filter(m => m.elsewhere?.id === cid);
  const was = mine.find(m => m.elsewhere?.before)?.elsewhere || mine[0]?.elsewhere || {};
  const before = was.before ? `before=${encodeURIComponent(was.before)}&` : '';
  const d = await get(`/v2/conversations/${encodeURIComponent(cid)}/messages?${before}${CHAT_PAGE}`);
  if (V2C !== state) return;
  const tag = m => ({...m, elsewhere: {kind: was.kind, subject: was.subject || '', id: cid, open: true}});
  const kept = was.before ? mine.map(tag) : [];
  const page = [...(d.messages || []).filter(m => !kept.some(k => k.id === m.id)).map(tag), ...kept];
  if (d.has_more && d.next_before && page.length) page[0].elsewhere = {...page[0].elsewhere, preview: true, before: d.next_before};
  state.botTalk = [...(state.botTalk || []).filter(m => m.elsewhere?.id !== cid), ...page];
  v2ChatRender(state);
}
async function v2ChatMessages(state) {
  if (!state.conv) return false;
  const sent = state.mine.length;
  const d = await v2Get(`/v2/conversations/${encodeURIComponent(state.conv.id)}/${S.me?.cloud ? 'snapshot' : 'messages'}?${CHAT_PAGE}`);
  if (V2C !== state || !d) return false;
  // A message sent while this was loading is newer than the copy that came back: keep it, and leave the
  // live turn to the stream (this copy has not seen it start).
  const late = state.mine.slice(sent).filter(m => !(d.messages || []).some(x => x.id === m.id));
  state.messages = [...(d.messages || []), ...late];
  state.nextBefore = d.next_before;
  if (S.me?.cloud && !late.length) {
    state.execution = d.execution;
    state.live = v2RunLive(d.execution) ? {text: d.execution.text} : null;
  }
  CHAT_CACHE.set(state.slug, {mode: state.mode, conv: state.conv, messages: state.messages, nextBefore: state.nextBefore,
                              execution: state.execution, live: state.live});
  return true;
}
// An attached image is a thumbnail in the chat that opens full size, not
// "image.png". /api/v2/files serves every file as a sandboxed download, so the page fetches the
// bytes itself and shows them from a blob URL of the file's own image type; other files stay links.
const CHAT_IMAGE_RE = /\.(png|jpe?g|gif|webp|heic|avif)$/i;
const isChatImage = f => /^image\//.test(String(f?.content_type || '')) && !/svg/.test(f.content_type) || (!f?.content_type && CHAT_IMAGE_RE.test(String(f?.name || '')));
const CHAT_IMAGES = new Map();                 // file id -> {promise: Promise<object URL>, url, bytes}; a byte budget (fileKeep)
function chatImageURL(f) {
  return fileKeep(CHAT_IMAGES, f.id, () => fetch(`${API}/v2/files/${encodeURIComponent(f.id)}`, {credentials: 'same-origin'})
    .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.blob(); })
    .then(b => {
      const blob = new Blob([b], {type: /^image\//.test(f.content_type || '') ? f.content_type : 'image/png'}), url = URL.createObjectURL(blob);
      return {value: url, url, bytes: blob.size};
    }));
}
// The first visuals show as cards (ui/app/visual-cards.js); the rest stay these chips and thumbnails.
function chatAttachmentsHTML(files, key = '') {
  return visualCardsHTML(files, {key, chips: rest => chatChipsHTML(rest)});
}
// A chip or thumbnail shows the title the sender gave the file (refs.attachments[].title), else its name; data-name
// keeps the file's own name for the viewer and the download.
const chatFileLabel = f => String(f?.title || '').trim() || f?.name || 'file';
function chatChipsHTML(files) {
  if (!files.length) return '';
  return `<div class="chat-files">${files.map(f => {
    const label = chatFileLabel(f), titled = label !== (f.name || 'file');
    if (!isChatImage(f)) return `<a class="pill" href="${API}/v2/files/${encodeURIComponent(f.id)}" data-name="${esc(f.name || 'file')}"${titled ? ` title="${esc(f.name || '')}"` : ''}>${esc(label)}</a>`;   // opens in the viewer, or downloads
    const thumb = `<button type="button" class="chat-thumb" data-chat-image="${esc(f.id)}" data-type="${esc(f.content_type || '')}" data-name="${esc(f.name || 'image')}" data-title="${esc(label)}" aria-label="View ${esc(label)}" title="${esc(label)}"><img alt="${esc(label)}" hidden></button>`;
    return titled ? `<span class="chat-titled">${thumb}<span class="chat-file-title" aria-hidden="true">${esc(label)}</span></span>` : thumb;
  }).join('')}</div>`;
}
// Thumbnails fetch only near the viewport, so a long history doesn't pull every picture at once.
const CHAT_IO = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
  for (const e of entries) if (e.isIntersecting) { CHAT_IO.unobserve(e.target); chatThumbShow(e.target); }
}, {rootMargin: '300px'}) : null;
function chatThumbsLoad(root = document) {
  for (const button of root.querySelectorAll('.chat-thumb:not([data-loading])')) {
    button.dataset.loading = '1';
    if (CHAT_IMAGES.has(button.dataset.chatImage) || !CHAT_IO) chatThumbShow(button); else CHAT_IO.observe(button);
  }
}
function chatThumbShow(button) {
  const f = {id: button.dataset.chatImage, content_type: button.dataset.type, name: button.dataset.name};
  chatImageURL(f).then(url => { const img = button.querySelector('img'); img.src = url; img.hidden = false; button.classList.add('ready'); })
    .catch(() => { (button.closest('.chat-titled') || button).replaceWith(Object.assign(document.createElement('a'), {className: 'pill', href: `${API}/v2/files/${encodeURIComponent(f.id)}`, download: f.name, textContent: button.dataset.title || f.name})); });
}
let CHAT_THUMBS_QUEUED = false;
new MutationObserver(() => {
  if (CHAT_THUMBS_QUEUED) return;
  CHAT_THUMBS_QUEUED = true;
  setTimeout(() => { CHAT_THUMBS_QUEUED = false; if (document.querySelector('.chat-thumb:not([data-loading])')) chatThumbsLoad(); }, 60);
}).observe(document.documentElement, {childList: true, subtree: true});
function chatImageOpen(button) {
  let d = $('#img-lightbox');
  if (!d) {
    d = document.createElement('dialog'); d.id = 'img-lightbox'; d.className = 'img-lightbox';
    d.addEventListener('click', ev => { if (!ev.target.closest('a')) d.close(); });
    document.body.append(d);
  }
  const src = button.querySelector('img')?.src; if (!src) return;
  const download = button.dataset.chatImage ? `<a href="${API}/v2/files/${encodeURIComponent(button.dataset.chatImage)}" download>Download</a>` : '';
  d.innerHTML = `<img src="${esc(src)}" alt="${esc(button.dataset.name)}"><div class="img-lightbox-bar"><span>${esc(button.dataset.title || button.dataset.name)}</span>${download}</div>`;
  d.showModal();
}
// Attached chat images open in the one viewer (see viewerOpenFrom), with the message's other files.
function v2MessageCards(m) {
  const refs = (m.refs && typeof m.refs === 'object') ? m.refs : {};
  const bits = [];
  // Tasks are rows with their titles (v2RunHTML), never an id chip.
  for (const [k, v] of Object.entries(refs)) {
    if (k === 'action_result' || !/approval/i.test(k)) continue;
    for (const one of [].concat(v)) bits.push(`<span class="fchip">approval <span class="mono">${esc(String(one).slice(0, 8))}</span></span>`);
  }
  if (refs.action_result?.approval) bits.push(`<span class="fchip">${esc(refs.action_result.decision || 'updated')} approval <span class="mono">${esc(String(refs.action_result.approval).slice(0, 8))}</span></span>`);
  // A message that came in from Slack (docs/slack-gateway.md): where, and why the decision model sent it here.
  if (refs.slack && typeof refs.slack === 'object' && refs.slack.permalink) {
    bits.push(`<a class="fchip" href="${esc(String(refs.slack.permalink))}" target="_blank" rel="noopener">Slack ${esc(String(refs.slack.channel_name || ''))}</a>${betaPill('slack_gateway')}`);
  }
  if (refs.routing && typeof refs.routing === 'object') {
    const r = refs.routing;
    const who = (Array.isArray(r.recipients) ? r.recipients : []).map(x => botDisplayName(String(x.bot || '')) + (typeof x.confidence === 'number' ? ' ' + Math.round(x.confidence * 100) + '%' : '')).join(', ');
    bits.push(`<span class="fchip" title="${esc(JSON.stringify(r))}">routed by ${esc(String(r.routed_by === 'judge' || !r.routed_by ? 'decisions' : r.routed_by))}${who ? ' to ' + esc(who) : ' to nobody'}${r.fallback ? ' (fallback)' : ''}</span>`);
  }
  return bits.length ? `<div class="conv-deliv">${bits.join('')}</div>` : '';
}
// What a reply's turn did, as muted one-line rows in plain words (#524, after bot-desk): the task it
// was about, work it handed out, links it filed, questions and messages it sent, what the hub
// refused. Titles and names come with the message (backend/turns.py); a task this reader cannot
// open never comes, so no id is ever shown. Under them, the turn's work folded to one line.
const youAware = actor => { const who = actorLabel(actor); return who === 'You' ? 'you' : who; };
function v2RunRow(verb, what, {task = '', href = '', failed = false, title = ''} = {}) {
  const inner = `<span class="did-arrow">${failed ? '✗' : '↳'}</span><span class="did-verb">${verb}</span>${what ? `<span class="did-what">${what}</span>` : ''}`;
  const tip = title ? ` title="${esc(title)}"` : '';
  if (task) return `<button type="button" class="did" data-task-conversation="${esc(task)}"${tip}>${inner}</button>`;
  if (href) return `<a class="did" href="${esc(href)}"${/^https?:/.test(href) ? ' target="_blank" rel="noopener"' : ''}${tip}>${inner}</a>`;
  return `<div class="did${failed ? ' failed' : ''}"${tip}>${inner}</div>`;
}
const tookWords = s => s == null ? '' : s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.floor(s / 3600)}h ${Math.round(s % 3600 / 60)}m`;
// What the run did reads as one folded line under the reply, named by its first few actions.
function v2RunHTML(m) {
  const run = m.run || {}, rows = [], said = [], given = new Set((run.did || []).filter(d => d.kind === 'task').map(d => d.task_id));
  for (const [id, t] of Object.entries(m.ref_tasks || {}))
    if (!given.has(id)) rows.push(v2RunRow('task ·', esc(t.title), {task: id}));
  for (const d of run.did || []) {
    if (d.kind === 'task') {
      const verb = d.owner === m.from_actor ? 'made itself a task' : `gave ${youAware(d.owner)} a task`;
      rows.push(v2RunRow(esc(verb) + ' ·', esc(d.title), {task: d.task_id})); said.push(verb);
    } else if (d.kind === 'link') {
      const ref = shortRef(d.url);
      const mark = ref ? `<span class="ref ref-${ref.kind}">${esc(ref.label)}</span> ` : '';
      rows.push(v2RunRow('filed', mark + esc(d.title || (ref ? '' : d.url)), {href: d.url, title: d.url})); said.push('filed');
    } else if (d.kind === 'ask' || d.kind === 'say') {
      const slug = actorSlug(d.to), verb = `${d.kind === 'ask' ? 'asked' : 'messaged'} ${youAware(d.to)}`;
      rows.push(v2RunRow(esc(verb) + ' ·', esc(d.text), {href: slug ? `#/bot/${encodeURIComponent(slug)}` : ''})); said.push(verb);
    } else if (d.kind === 'refused') {
      rows.push(v2RunRow('refused ·', esc(d.text || d.rule), {failed: true, title: `Tico refused this (${d.rule})`})); said.push('refused');
    }
  }
  if (run.steps) {
    const label = [`${run.steps} step${run.steps === 1 ? '' : 's'}`, run.tool_calls ? `${run.tool_calls} tool call${run.tool_calls === 1 ? '' : 's'}` : '', tookWords(run.took_s)].filter(Boolean).join(' · ');
    const turn = m.refs?.turn_id, open = V2C?.openSteps?.has(turn);
    rows.push(`<details class="run-steps" data-turn="${esc(turn)}"${open ? ' open' : ''}><summary>${esc(label)}</summary><div class="steps-list">${
      V2C?.steps?.[turn] || '<div class="step muted">Loading…</div>'}</div></details>`);
  }
  const about = Object.values(m.ref_tasks || {})[0]?.title;
  if (!said.length && !about) return rows.length ? `<div class="run-did">${rows.join('')}</div>` : '';
  // Named by what it did; a reply that only worked its task is named by the task.
  const named = [...new Set(said)], head = named.length
    ? named.slice(0, 2).join(', ') + (named.length > 2 ? ` +${named.length - 2}` : '') : chatLine(about, 60);
  const label = [head.charAt(0).toUpperCase() + head.slice(1), tookWords(run.took_s)].filter(Boolean).join(' · ');
  return `<details class="run-did run-fold" data-fold="${esc(m.id || '')}"${v2FoldOpen(m) ? ' open' : ''}><summary>${esc(label)}</summary>${rows.join('')}</details>`;
}
const chatLine = (text, n) => { const t = String(text || '').replace(/\s+/g, ' ').trim(); return t.length > n ? t.slice(0, n - 1) + '…' : t; };
// Folded lines stay as the reader left them across redraws.
const v2FoldOpen = m => !!(m.id && V2C?.openFolds?.has(m.id));
const v2FoldAttrs = m => `data-fold="${esc(m.id || '')}"${v2FoldOpen(m) ? ' open' : ''}`;
// A turn's steps are read when someone opens them, once, and kept open across redraws.
function v2StepsHTML(d) {
  const steps = d.steps || [];
  if (!steps.length) return '<div class="step muted">No steps were recorded for this run.</div>';
  return steps.map(st => st.kind === 'thinking'
    ? `<details class="step think"><summary>Thinking</summary><div class="step-text">${esc(st.text)}</div></details>`
    : `<div class="step${st.kind === 'error' ? ' err' : ''}${st.nested ? ' nested' : ''}" title="${esc(fmt(st.at))}"><span class="step-tool">${esc(st.kind === 'tool' ? st.tool : st.kind)}</span><span class="step-sum">${esc(st.text)}</span></div>`).join('');
}
async function v2StepsOpen(state, el) {
  const turn = el.dataset.turn;
  state.openSteps ||= new Set(); state.steps ||= {};
  if (!el.open) { state.openSteps.delete(turn); return; }
  state.openSteps.add(turn);
  if (turn in state.steps) return;          // loaded, or on its way ('')
  state.steps[turn] = '';
  let html;
  try { html = state.steps[turn] = v2StepsHTML(await get(`/v2/turns/${encodeURIComponent(turn)}/steps`)); }
  catch (error) { delete state.steps[turn]; html = `<div class="step err">${esc(error.message)}</div>`; }   // reopening tries again
  if (V2C !== state) return;
  const list = $(`#conv-thread details.run-steps[data-turn="${CSS.escape(turn)}"] .steps-list`);
  if (list) list.innerHTML = html;
}
// Copy the original message, not timestamps, action labels or rendered link shortcuts.
function chatCopyHTML(text) {
  return text ? `<div class="chat-message-actions"><button type="button" class="ghost chat-message-copy" data-chat-copy="${esc(text)}" aria-label="Copy message" title="Copy message"><span class="nav-icon" aria-hidden="true">content_copy</span></button></div>` : '';
}
document.addEventListener('click', async ev => {
  const button = ev.target.closest('.bubble [data-chat-copy]');
  if (!button) return;
  ev.preventDefault();
  try { await copyText(button.dataset.chatCopy); toast('Message copied'); }
  catch { toast('Could not copy the message', true); }
});
// On touch there is no hover: a tap on a message shows its copy button under it, and a tap elsewhere hides it.
// A tap on a link, button or fold, or one that ends a text selection, does what it always did.
const CHAT_TOUCH = window.matchMedia('(pointer: coarse)');
document.addEventListener('click', ev => {
  if (!CHAT_TOUCH.matches) return;
  const bubble = ev.target.closest('.bubble');
  const keep = bubble && (ev.target.closest('a,button,summary,input,textarea,select,details') || String(getSelection?.() || ''));
  for (const el of document.querySelectorAll('.bubble.acts')) if (el !== bubble) el.classList.remove('acts');
  if (bubble && !keep && bubble.querySelector(':scope>.chat-message-actions')) bubble.classList.toggle('acts');
});
// A bot-owned task works in a room of its own. The main chat keeps one quiet row when it starts (refs.task_started)
// and one per milestone (refs.task_report: blocked, done, declined, stuck); a tap opens the task's thread.
function chatTaskMark(m) {
  const refs = m.refs || {};
  if (!refs.task || !(refs.task_started || refs.task_report)) return null;
  const id = String(typeof refs.task === 'object' ? refs.task.id || '' : refs.task);
  if (!id) return null;
  const known = m.ref_tasks?.[id] || (typeof refs.task === 'object' ? refs.task : null);
  const title = known?.title || (refs.task_started ? String(m.body || '').replace(/^Started task:\s*/i, '') : '') || 'Task';
  return {id, title, state: refs.task_report ? String(refs.task_report) : 'started'};
}
function chatTaskRowHTML(m) {
  const t = chatTaskMark(m);
  return `<button type="button" class="chat-task-row" data-task-conversation="${esc(t.id)}" data-message="${esc(m.id || '')}" title="${esc(plainActors(m.body || ''))}">
    <span class="ct-dot ${esc(t.state)}" aria-hidden="true"></span><span class="ct-title">${esc(chatLine(t.title, 90))}</span><span class="ct-state">· ${esc(t.state)}</span>${
    m.created_at ? `<time class="chat-time" title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</time>` : ''}</button>`;
}
// Task rows in a row, from one sender: more than three show the oldest, a count of the rest, and the newest.
function chatTaskRunHTML(run) {
  if (run.length <= 3) return run.map(chatTaskRowHTML).join('');
  return chatTaskRowHTML(run[0])
    + `<div class="chat-task-row chat-task-more" data-task-hidden="${run.length - 2}"><span class="ct-state">...${run.length - 2} more...</span></div>`
    + chatTaskRowHTML(run.at(-1));
}
// Tico Live was retired; the lines it left in rooms (refs.live) read as plain messages.
function v2MessageHTML(m) {
  // A Confirm card a bot left for you (BotOps: adding a person, a role, a shared credential): filled in from the action.
  // A bot asking for a secret: the card takes it here and it goes straight to Credentials (ui/credential-card.js).
  if (m.refs?.credential_request) return `<div class="conv-run chat" data-message="${esc(m.id)}"><div data-credential-host="${esc(m.refs.credential_request)}"><div class="asst-state">Loading the card…</div></div></div>`;
  if (m.refs?.action) return `<div class="conv-run chat" data-message="${esc(m.id)}"><div data-action-host="${esc(m.refs.action)}"><div class="asst-state">Loading the card…</div></div></div>`;
  // A met or stopped goal's notice from the server is the compact goal line (ui/app/chat-goal.js), not a Task line.
  if (m.refs?.chat_goal) return chatGoalNoticeHTML(m);
  if (chatTaskMark(m)) return chatTaskRowHTML(m);
  const when = `<time class="chat-time" title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</time>`;
  // A task's note is its work log: chat names the task and keeps the note one click away.
  if (m.refs?.note) {
    const title = Object.values(m.ref_tasks || {})[0]?.title || chatLine(plainActors(m.body), 80);
    return `<details class="chat-system chat-fold" ${v2FoldAttrs(m)}><summary><b>Note</b> · ${esc(title)} ${when}</summary><div class="md">${safeMd(plainActors(m.body), {shortLinks: true})}</div></details>`;
  }
  // A bot's reply held by an org rule: one line naming the rule, never the text (ui/app/org-rule-holds.js).
  if (m.refs?.org_rule && m.kind === 'notice') return ruleHeldNoteHTML(m);
  if (m.kind === 'notice') return `<div class="chat-system" data-message="${esc(m.id || '')}"><b>Task</b> ${esc(plainActors(m.body))}<div class="chat-meta">${when}</div></div>`;
  // One bot's message to another, landed in a person's room: shown as an aside, not a reply.
  if (String(m.from_actor || '').startsWith('bot:') && String(m.to_actor || '').startsWith('bot:') && m.to_actor !== m.from_actor)
    return `<details class="chat-system chat-fold" ${v2FoldAttrs(m)}><summary><b>${esc(actorLabel(m.from_actor))} → ${esc(actorLabel(m.to_actor))}</b> · ${esc(chatLine(plainActors(m.body), 80))} ${when}</summary><div class="md">${safeMd(m.body || '', {shortLinks: true})}</div></details>`;
  const pid = actorPerson(m.from_actor);
  const mine = !!pid;                       // any person's message sits on the right, under their name
  const who = mine ? esc(personHandle(pid)) : esc(actorLabel(m.from_actor));
  const me = m.from_actor === myActor();    // your own lines need no name on a bot's page
  // The message a new topic started a fresh session for (backend/topic_change.py): one quiet line above it.
  const fresh = mine && m.refs?.topic_changed ? '<div class="conv-break chat-topic-break" role="separator">Topic changed</div>' : '';
  return `${fresh}<div class="conv-run chat${me ? ' from-me' : ''}" data-message="${esc(m.id || '')}">
    <time class="chat-stamp" datetime="${esc(m.created_at || '')}" title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</time>
    <div class="conv-run-head">${mine ? `<span class="mono muted">${who}</span>` : actorChip(m.from_actor)}
      ${m.kind && m.kind !== 'say' ? `<span class="pill">${esc(m.kind)}</span>` : ''}
      <span class="spacer" style="flex:1"></span>
      <span class="tnum" title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</span></div>
    <div class="bubble ${mine ? 'you' : 'bot reply'}"><span class="who">${who}</span>${
      mine ? esc(m.body || '')
        // The Assistant's read-back of what it will approve is shown exactly as written, never formatted: a requesting
        // bot's fields can't hide part of what the person says yes to.
        : m.refs?.read_back ? `<div class="read-back" style="white-space:pre-line">${esc(m.body || '')}</div>`
        : `<div class="md">${safeMd(m.body || '', {shortLinks: true})}</div>`}${chatCopyHTML(m.body)}</div>
    ${v2DeliveryHTML(m)}
    ${(m.refs?.warnings || []).map(w => `<div class="chat-warn" title="From the runner, not the bot">${esc(w.charAt(0).toUpperCase() + w.slice(1))}</div>`).join('')}
    ${S.me?.cloud ? chatAttachmentsHTML(m.refs?.attachments || [], m.id || '') : ''}
    ${v2RunHTML(m)}${v2MessageCards(m)}</div>`;
}
// A message sent while the bot answers here is written into that turn, or held for the next one (`delivery`, from the
// harness's `steer`). Its sender, if they may stop the bot, can have it replace the turn instead: Stop and redirect.
function v2DeliveryHTML(m) {
  if (m.run?.state === 'added_to_run') return `<div class="chat-delivery">Steered into the running turn</div>`;
  const d = m.delivery;
  if (!d) return '';
  if (d.mode === 'steer') return `<div class="chat-delivery">Steering this turn</div>`;
  if (d.mode === 'redirect') return '<div class="chat-delivery">Stopping this turn; this message starts the next one</div>';
  const slug = String(m.to_actor || '').replace(/^bot:/, '');
  const can = m.from_actor === myActor() && settingsCanManageBot(S.emps.find(e => e.name === slug));
  return `<div class="chat-delivery">Queued for the next turn${can ? ` <button type="button" class="btn sm" data-redirect="${esc(m.id)}" data-redirect-bot="${esc(slug)}" data-redirect-attempt="${esc(d.attempt_id)}">Stop and redirect</button>` : ''}</div>`;
}
document.addEventListener('click', async ev => {
  const button = ev.target.closest('[data-redirect]'); if (!button) return;
  ev.preventDefault();
  button.disabled = true;
  try {
    await post(`/v2/bots/${encodeURIComponent(button.dataset.redirectBot)}/stop`,
      {attempt_id: button.dataset.redirectAttempt, redirect: button.dataset.redirect});
    button.closest('.chat-delivery').textContent = 'Stopping this turn; this message starts the next one';
  } catch (e) { button.disabled = false; toast(`Not redirected: ${e.message}`, true); }
});
// The chat is the person and the bot. A person's own words, the bot's replies to them, and anything that waits on them
// (a question, an approval, a Confirm or credential card) are the conversation. Everything else is side traffic:
// bots talking to each other, a bot's message relayed from another bot's run, the keeper's task and system notices, a
// task's notes. Side traffic folds into one line per run (v2MessagesHTML); nothing is dropped. Read from the message's
// fields, never its words: a reply that quotes another bot is still a reply.
const chatNeedsYou = m => !!(m.refs?.action || m.refs?.credential_request)
  || !!actorPerson(m.to_actor) && (m.kind === 'ask' || !!m.refs?.approval || !!m.refs?.questions);
function chatSideKind(m, slug) {
  const from = String(m.from_actor || ''), refs = m.refs || {};
  if (m.elsewhere) return 'bots';                       // a conversation between bots, shown in this chat's timeline
  // A task's start or milestone: its own row in time order (not folded with side traffic), never the conversation's last line.
  if (chatTaskMark(m)) return 'task';
  if (actorPerson(from)) return '';
  if (chatNeedsYou(m) || refs.chat_goal) return '';
  if (refs.note) return 'note';
  // A reply addressed to a person (the bot's answer, or why it could not start) is the conversation.
  if (m.in_reply_to && actorPerson(m.to_actor)) return '';
  const self = slug && (from === `bot:${slug}` || from === `bot:${botBranchChatTarget(slug)}`);
  if (self && !actorSlug(m.to_actor) && m.to_actor !== 'keeper')
    // A loose message sent while a run another bot started was working: that bot's news, passed on (backend/turns.py).
    return m.prompted_by === 'bot' && !refs.turn_id ? 'relay' : '';
  return from === 'keeper' || m.kind === 'notice' ? 'notice' : 'bots';
}
// Who a side item is from, as the folded line names it: the other bot in a bot-to-bot message, else the sender.
function chatSideName(m, slug) {
  if (m.from_actor === 'keeper') return m.refs?.task ? 'tasks' : actorLabel('keeper');
  const self = [`bot:${slug}`, `bot:${botBranchChatTarget(slug)}`];
  if (self.includes(m.from_actor) && actorSlug(m.to_actor) && !self.includes(m.to_actor)) return actorLabel(m.to_actor);
  return actorLabel(m.from_actor);
}
function chatSideHead(m) {
  if (m.refs?.note) return 'Note' + ((t => t ? ' · ' + t : '')(Object.values(m.ref_tasks || {})[0]?.title || ''));
  if (m.from_actor === 'keeper') return m.refs?.task ? 'Task' : actorLabel('keeper');
  const to = m.to_actor && m.to_actor !== m.from_actor ? ' → ' + actorLabel(m.to_actor) : '';
  return actorLabel(m.from_actor) + to + (m.elsewhere?.subject ? ' · ' + m.elsewhere.subject : '');
}
// About two lines at phone width: longer text, or more than one line of it, gets "Show more".
const CHAT_SIDE_SHORT = 110;
// One side item, at most two lines; a longer one opens in place with "Show more", with its files.
function chatSideItemHTML(m) {
  const text = plainActors(m.body || ''), long = text.length > CHAT_SIDE_SHORT || /\n/.test(text.trim());
  const open = long && V2C?.openSide?.has(m.id);
  const when = m.created_at ? `<time class="chat-time" title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</time>` : '';
  return `<div class="cs-item${open ? ' open' : ''}" data-message="${esc(m.id || '')}">
    <div class="cs-head"><b>${esc(chatSideHead(m))}</b>${when}</div>
    <div class="cs-text md${open ? '' : ' clamp'}">${safeMd(text, {shortLinks: true})}</div>${open && S.me?.cloud ? chatAttachmentsHTML(m.refs?.attachments || [], m.id || '') : ''}
    ${long ? `<button type="button" class="link cs-more" data-cs-more="${esc(m.id || '')}" aria-expanded="${open}">${open ? 'Show less' : 'Show more'}</button>` : ''}${
      m.elsewhere?.preview ? `<button type="button" class="link cs-more" data-bots-open="${esc(m.elsewhere.id)}">Earlier messages</button>` : ''}</div>`;
}
// A run of side items is one muted line: one item reads as itself, more as "12 updates · Dots, BotOps, tasks · 2h".
function chatSideHTML(messages, slug) {
  const first = messages[0], last = messages.at(-1), key = `side:${first.id || ''}`;
  const when = last?.created_at ? `<time class="chat-time" title="${esc(fmt(last.created_at))}">${esc(ago(last.created_at))}</time>` : '';
  const names = [...new Set(messages.map(m => chatSideName(m, slug)).filter(Boolean))];
  const who = names.slice(0, 3).join(', ') + (names.length > 3 ? ` +${names.length - 3}` : '');
  const label = messages.length === 1
    ? `<b>${esc(chatSideHead(first))}</b><span class="cs-gist">· ${esc(chatLine(plainActors(first.body), 90))}</span>`
    : `<b>${messages.length} updates</b><span class="cs-gist">· ${esc(who)}</span>`;
  return `<details class="chat-side chat-fold" data-fold="${esc(key)}" data-side-count="${messages.length}"${v2FoldOpen({id: key}) ? ' open' : ''}>
    <summary>${label}${when}</summary><div class="chat-side-items">${messages.map(chatSideItemHTML).join('')}</div></details>`;
}
// The thread: the conversation as it was, each run of side items folded to one line. Side items that came after the
// last thing in the conversation join the line just above it, so the conversation's last message stays at the bottom
// and a bot-to-bot exchange arriving while you read changes a count instead of pushing the chat up.
function v2MessagesHTML(messages, goalLine, slug = V2C?.slug) {
  const feed = [];
  messages.forEach((message, i) => {
    if (goalLine?.at === i) feed.push({html: goalLine.html});
    const kind = chatSideKind(message, slug);
    feed.push({message, side: !!kind && kind !== 'task', task: kind === 'task'});
  });
  if (goalLine?.at === messages.length) feed.push({html: goalLine.html});
  const lastMain = feed.findLastIndex(item => !item.side);
  if (lastMain >= 0 && lastMain < feed.length - 1) feed.splice(lastMain, 0, ...feed.splice(lastMain + 1));
  const rows = [];
  for (let i = 0; i < feed.length;) {
    if (feed[i].task) {
      const run = [], from = feed[i].message.from_actor;
      while (i < feed.length && feed[i].task && feed[i].message.from_actor === from) run.push(feed[i++].message);
      rows.push(chatTaskRunHTML(run));
      continue;
    }
    if (!feed[i].side) { rows.push(feed[i].html || v2MessageHTML(feed[i].message)); i++; continue; }
    const run = [];
    while (i < feed.length && feed[i].side) run.push(feed[i++].message);
    rows.push(chatSideHTML(run, slug));
  }
  return rows.join('');
}
// What is happening to the message just sent, on the line where its reply will appear (#524): from
// the job's state and the bot's own status, both already on the page. The server's reason stays
// when it has one (a Mac offline, the bot draining). Twenty minutes with no reply says so.
const REPLY_WAIT_MS = 20 * 60000;
// Quiet — three dots and at most a word; the sentence is the tooltip.
// {word, tip, mode}: mode 'run' animates the dots, 'wait' breathes them slowly.
function v2PendingText(state) {
  const x = state.execution, name = botDisplayName(state.slug);
  if (!S.me?.cloud) return {word: '', tip: `${name} is thinking`, mode: 'run'};
  if (state.live?.interrupted) return SESSION.out ? {word: 'Signed out', tip: "You've been signed out", mode: 'wait'}
    : {word: 'Reconnecting', tip: 'Connection interrupted — reconnecting', mode: 'wait'};
  return pendingRunText(x, state.slug);
}
// The words for one run, wherever a message waits on it (this chat, a task's chat): Sent, Queued and why, Starting,
// "<Bot> is working", or how it ended (Reply failed).
function pendingRunText(x, slug) {
  const name = botDisplayName(slug);
  if (!x) return {word: 'Sent', tip: 'Sent', mode: 'wait'};
  if (x.state === 'queued') {
    if (x.label && x.label !== 'Saved — queued') return {word: x.label, tip: x.label, mode: 'wait'};   // the server's reason
    const s = v2StatusOf(slug);
    return {word: 'Queued', mode: 'wait', tip: s?.state === 'running' && s.focus
      ? `${name} is on another run: ${s.focus}` : 'Starts when its computer picks it up'};
  }
  // Its ChatGPT login is busy with another turn (backend/login_waits.py): waiting, not failed.
  if (x.waiting && ['leased', 'running'].includes(x.state)) return {word: x.waiting, tip: x.waiting, mode: 'wait'};
  if (x.state === 'leased') return {word: 'Starting', tip: `${name} is starting`, mode: 'run'};
  if (x.state === 'running') return x.label && x.label !== 'Working'
    ? {word: x.label, tip: x.label, mode: 'run'} : {word: '', tip: `${name} is working`, mode: 'run'};
  return {word: x.label || x.state, tip: x.label || x.state, mode: 'wait'};
}
// The hint a queued run carries when its bot cannot start: no AI provider, or its computer not signed in to its model.
function pendingHintHTML(x) {
  if (x?.state !== 'queued') return '';
  if (x.readiness_reason === 'missing_provider') return ' <a href="#/settings" data-gs-tab="models">Add an AI provider</a>';
  return x.readiness_reason === 'model_sign_in'
    ? ' <span>Its computer is not signed in to its model: <a href="#/settings" data-gs-tab="devices">Sign in on Settings &gt; Computers</a> · <a href="#/settings" data-gs-tab="health">Health</a></span>'
    : '';
}
function v2PendingHTML(state) {
  const x = state.execution, key = x?.message_id || 'sent';
  if (state.dismissed === key) return '';
  const sent = Date.parse(state.messages.find(m => m.id === x?.message_id)?.created_at || '') || state.sentAt || 0;
  clearTimeout(state.waitTimer);
  const late = S.me?.cloud && sent && Date.now() - sent >= REPLY_WAIT_MS;
  if (S.me?.cloud && sent && !late) state.waitTimer = setTimeout(() => v2ChatRender(state), sent + REPLY_WAIT_MS - Date.now() + 50);
  const hint = pendingHintHTML(x);
  if (late && !hint) return `<div class="thinking late" data-pending="${esc(key)}"><span class="dot"></span>
    <span>No reply after 20 minutes · <button type="button" class="dismiss" data-pending-dismiss>dismiss</button></span></div>`;
  const t = v2PendingText(state);
  return `<div class="thinking" data-pending="${esc(key)}" title="${esc(t.tip)}" role="status" aria-label="${esc(t.tip)}">
    <span class="typing ${t.mode}" aria-hidden="true"><i></i><i></i><i></i></span>${t.word ? `<span class="thinking-word" aria-hidden="true">${esc(t.word)}</span>` : ''}${hint}</div>`;
}
// Bot-to-bot messages from this bot's other conversations (v2BotsLoad), in time order with the chat's own. Those older
// than the loaded page wait until older messages are loaded, so they never pile up above the history; a conversation
// the person opened shows all it has loaded, so its "Earlier messages" stays in reach.
function v2WithBotTalk(state, messages) {
  const own = new Set(messages.map(m => m.id)), from = state.nextBefore ? Date.parse(messages[0]?.created_at || '') || 0 : 0;
  const talk = (state.botTalk || []).filter(m => !own.has(m.id) && (m.elsewhere?.open || (Date.parse(m.created_at || '') || 0) >= from));
  if (!talk.length) return messages;
  // Merged in, never re-sorted: the chat's own order stays exactly as the server gave it.
  const time = m => Date.parse(m.created_at || '') || 0, out = [];
  talk.sort((a, b) => time(a) - time(b));
  let t = 0;
  for (const m of messages) {
    while (t < talk.length && time(talk[t]) <= time(m)) out.push(talk[t++]);
    out.push(m);
  }
  return out.concat(talk.slice(t));
}
const shownById = (state, id) => state.messages.find(m => m.id === id) || (state.botTalk || []).find(m => m.id === id);
// Scrolled up when something new lands: a small pill above the composer takes you to it (ui/app/stick-scroll.js).
// Only the conversation counts, and a reply as it streams in; a side item joins its folded line without a pill.
const v2Latest = state => state.lastMain ? state.lastMain.id + (state.live?.text ? ':live' : '') : '';
function v2Jump(state) {
  if (!state.stick) return '';
  const last = v2Latest(state), fresh = !!last && last !== state.seenLast;
  if (state.stick.atBottom) state.seenLast = last;
  return fresh && (actorPerson(state.lastMain.from_actor) && !state.live?.text ? 'Latest' : 'New reply');
}
function v2ChatRender(state) {
  const thread = $('#conv-thread');
  if (!thread || V2C !== state) return;
  if (state.thread !== thread) {
    // A new thread, or one kept from the last chat on the same page, starts this chat at its newest line.
    state.thread = thread; state.html = null;
    state.stick = stickScroll(thread, {pill: $('#conv-jump'), onBottom: () => { if (V2C === state) v2Jump(state); }});
    state.stick.toBottom();
    thread.addEventListener('scroll', () => v2OlderUpdate(state), {passive: true});
    thread.addEventListener('toggle', ev => {
      if (V2C !== state) return;
      if (ev.target.matches?.('details.run-steps')) v2StepsOpen(state, ev.target);
      const fold = ev.target.dataset?.fold;
      if (fold) { state.openFolds ||= new Set(); state.openFolds[ev.target.open ? 'add' : 'delete'](fold); }
    }, true);
    thread.addEventListener('click', ev => {
      const conv = ev.target.closest('[data-bots-open]');
      if (conv && V2C === state) {
        conv.disabled = true;
        void v2BotsOpen(state, conv.dataset.botsOpen).catch(error => { conv.disabled = false; toast(error.message, true); });
        return;
      }
      const more = ev.target.closest('[data-cs-more]');
      if (more && V2C === state) {
        state.openSide ||= new Set();
        state.openSide[state.openSide.has(more.dataset.csMore) ? 'delete' : 'add'](more.dataset.csMore);
        more.closest('.cs-item').outerHTML = chatSideItemHTML(shownById(state, more.dataset.csMore));
        return;
      }
      if (!ev.target.closest('[data-pending-dismiss]') || V2C !== state) return;
      state.dismissed = ev.target.closest('[data-pending]')?.dataset.pending;
      v2ChatRender(state);
    });
  }
  // A goal's controls (the /goal messages the server sends for Set, Pause, Clear) are the bar above, not bubbles.
  const shown = v2WithBotTalk(state, state.messages.filter(m => m.refs?.maintenance !== 'checkpoint' && !chatGoalControl(m)));
  state.lastMain = shown.findLast(m => !chatSideKind(m, state.slug)) || null;
  const goalLine = chatGoalLine(state, shown);
  const groups = v2MessagesHTML(shown, goalLine, state.slug);
  const pending = state.live && !state.live.text ? v2PendingHTML(state) : '';
  if (!pending) clearTimeout(state.waitTimer);
  const live = state.live?.text
    ? `<div class="conv-run chat"><div class="bubble bot reply"><span class="who">${empName(state.slug)}</span>
        <div class="md" id="v2-live">${safeMd(state.live.text, {shortLinks: true})}</div>${chatCopyHTML(state.live.text)}</div></div>`
    : pending ? `<div class="conv-run chat">${pending}</div>` : '';
  const html = (groups + live) || (state.failed ? '<div class="empty">Could not load the conversation yet; trying again…</div>'
    : !state.loaded ? '<div class="empty">Loading the thread…</div>' : state.empty || '<div class="empty">Nothing yet. Say something below.</div>');
  // A poll or snapshot that changed nothing leaves the thread alone: no rebuild, nothing to jump.
  if (html !== state.html) {
    state.html = html;
    state.stick.update(() => {
      // A Confirm card kept through the rebuild (assistantChat.cards puts the same element back) keeps its focus too.
      const focused = thread.contains(document.activeElement) ? document.activeElement : null;
      // Only the messages that changed are redrawn; cards both trees share keep their node (ui/app/visual-cards.js).
      patchHTML(thread, html, visualSwap);
      if (thread.querySelector('[data-action-host]')) void window.assistantChat?.cards(thread, {get, post, esc, toast,
        reload: () => v2ChatMessages(state).then(() => { if (V2C === state) v2ChatRender(state); })});
      if (focused?.isConnected && document.activeElement !== focused) focused.focus({preventScroll: true});
      if (thread.querySelector('[data-credential-host]')) void window.credentialCards?.mount(thread, {get, post, esc, toast,
        reload: () => v2ChatMessages(state).then(() => { if (V2C === state) v2ChatRender(state); })});
    }, {fresh: state.rendered && v2Jump(state), prepended: state.prepending});
  }
  state.prepending = false;
  v2Jump(state);
  state.rendered = true;
  botAvatarsSync();                     // the bot breathes while it answers
  const older = $('#conv-older');
  if (older) {
    older.innerHTML = state.nextBefore ? '<button class="ghost" type="button" data-cloud-older>Load older messages</button>' : '';
    older.querySelector('[data-cloud-older]')?.addEventListener('click', async ev => {
      const button = ev.target.closest('[data-cloud-older]');
      if (state.loadingOlder || !button) return;
      state.loadingOlder = true; button.disabled = true;
      try { if (await v2ChatOlder(state)) v2ChatRender(state); }
      catch (error) { toast(error.message, true); }
      finally { state.loadingOlder = false; v2OlderUpdate(state); }
    });
    v2OlderUpdate(state);
  }
  const st = $('#conv-state');
  if (st) st.innerHTML = state.live ? (t => `<span class="pill in-progress" title="${esc(t.tip)}">${esc(t.word || 'Working')}</span>`)(v2PendingText(state)) : v2StatePill(state.slug);
  pausedRender();
  chatGoalRender(state);
  chatOutlineSync(state);
  chatStopSync(state);
  const access = $('#conv-access');
  if (access) {
    if (state.mode === 'shared') {
      const people = (state.conv?.participants || []).filter(p => String(p).startsWith('human:')).map(actorLabel);
      access.innerHTML = `<span class="pill">Shared room</span>${people.length ? ` <span class="muted">${esc(people.join(', '))}</span>` : ''}`;
    } else {
      access.innerHTML = '<span class="pill ok">Private</span> <span class="muted">only you and this bot</span>';
    }
  }
}
// Stop shows while this chat's message runs, or waits behind a run the bot is still in, for a person who manages the bot.
function chatStopSync(state) {
  const P = BOT_PILL;
  if (!P || P.slug !== state.slug) return;
  const x = state.execution, own = ['leased', 'running'].includes(x?.state);
  const busy = own || x?.state === 'queued' && v2StatusOf(state.slug)?.state === 'running';
  const running = busy && settingsCanManageBot(S.emps.find(e => e.name === state.slug)) ? {attempt_id: own ? x.attempt_id : null} : null;
  if (!running === !P.running && running?.attempt_id === P.running?.attempt_id) return;
  P.running = running; P.stopping = false;
  pillButtons(P);
}
// Keep paging discoverable at the history boundary without taking space beside new messages.
function v2OlderUpdate(state) {
  const older = $('#conv-older'), thread = $('#conv-thread');
  if (V2C !== state || !older || !thread) return;
  older.hidden = !state.nextBefore || thread.scrollTop > 40;
  const button = older.querySelector('[data-cloud-older]');
  if (button) button.disabled = !!state.loadingOlder;
}
// One page further back; the outline's jump reuses it. False when the chat changed meanwhile.
async function v2ChatOlder(state) {
  const d = await get(`/v2/conversations/${encodeURIComponent(state.conv.id)}/messages?before=${encodeURIComponent(state.nextBefore)}&${CHAT_PAGE}`);
  if (V2C !== state) return false;
  state.messages = [...d.messages, ...state.messages.filter(m => !d.messages.some(x => x.id === m.id))]; state.nextBefore = d.next_before;
  state.older = state.messages.filter(m => !state.latestIds?.has(m.id));
  state.stick?.release(); state.prepending = true;
  return true;
}
// A message sent to a bot (the composer, Start setup) lands in the chat that is open on that bot, in the conversation the
// server put it in; a chat that was empty when it loaded is not "Nothing yet" any more.
function v2ChatAdopt(slug, j) {
  const state = V2C;
  if (!state || state.slug !== slug) { CHAT_CACHE.delete(slug); return; }
  const fresh = j.conversation && j.conversation.id !== state.conv?.id;
  if (j.conversation) { state.conv = j.conversation; state.listed = true; state.loaded = true; state.failed = false; }
  if (fresh) void chatGoalLoad(state);
  if (j.message) { state.messages.push(j.message); state.mine.push(j.message); }
  state.live = {text: ''};
  state.execution = null; state.sentAt = Date.now();
  state.stick?.toBottom();                     // what you just sent is where you look
  v2ChatRender(state);
  v2ChatStream(state);
}
// `extra` adds fields to the message itself: {command: true} hands the text to the harness as its own slash command.
async function v2ChatSend(P, text, slug = P.slug, extraRefs = {}, extra = {}) {
  // Return during a send that is still out waits for it, then sends what the box still holds; returning
  // here without a word was a message lost.
  while (P.sending || P.unconfirmed?.inFlight) {
    const waiting = P.sent || P.unconfirmed?.inFlight;
    if (!waiting) return false;                     // a task being created, not a chat send
    await waiting;
    if ((pq(P, '.p-text')?.value || '').trim() !== (text || '').trim() || !text && !P.files.length) return false;
  }
  chatForgetUnconfirmed(P);
  if (chatReplayExpired(P)) {
    pillButtons(P);
    toast(CHAT_REPLAY_EXPIRED, true);
    return false;
  }
  // Keep uncertain intent separately from the request cache; a redraw cannot silently rotate its identity.
  if (!P.files.length && !P.unconfirmed) {
    const path = `/v2/chat/${encodeURIComponent(slug)}`, body = {text: text || 'Attached files.', refs: {...extraRefs}, ...extra};
    P.unconfirmed = {text: (text || '').trim(), at: Date.now(), key: newRequestId(), signature: 'POST ' + path + '\n' + JSON.stringify(body)};
    CHAT_PENDING.set(P.slug, P.unconfirmed);
    chatPendingSave();
  }
  P.sending = true;
  let sent; P.sent = new Promise(done => sent = done);
  const pending = P.unconfirmed;
  if (pending) pending.inFlight = P.sent;
  const files = P.files.slice(), draftScope = sessionScope(pq(P, '.p-text'));
  const btn = pq(P, '.p-send'), label = btn ? btn.textContent : '';
  // "Sending" is a spinner on the button, never the dimmed look of an empty box; after a few seconds a line under
  // the box says it is still going, and whether it is trying again.
  if (btn) { btn.disabled = true; btn.classList.add('p-sending'); btn.setAttribute('aria-busy', 'true'); pillBtnSay(btn, 'Sending…'); }
  const watch = chatSendWatch(P, files.length);
  try {
    let refs = {...extraRefs};
    const j = await cloudCompose(`/v2/chat/${encodeURIComponent(slug)}`, {text: text || 'Attached files.', refs, ...extra}, files,
      {timeout: P.unconfirmed ? Math.min(CHAT_SEND.timeout, CHAT_REPLAY_MS - (Date.now() - P.unconfirmed.at)) : CHAT_SEND.timeout,
        onRetry: watch.retry, onResponse: watch.response, operationId: P.unconfirmed?.key});
    // A 200 that created nothing (a sign-in page in front of the API) is not a send: the draft stays.
    if (!j?.message?.id) throw new Error('the server did not confirm it');
    watch.end('sent');
    if (pending) pending.acknowledged = true;       // restored composers share this identity too
    pillAcknowledge(P, text, files, draftScope);
    pillWaySent(P, extra);
    if (j.message?.refs?.action_result) toast(j.message.refs.action_result.decision === 'approved' ? 'Approved' : 'Declined');
    v2ChatAdopt(slug, j);
    P.retryTries = 0;
    return true;
  } catch (e) {
    // Access can fail before replay or after storage, even on the first attempt. Only a recorded refusal
    // proves this key created no message; an unmarked 4xx cannot resolve delivery.
    const accessRefused = !!pending && e.status >= 400 && e.status < 500 && !e.unconfirmed && e.body?.error?.idempotency_refused !== true;
    watch.end(accessRefused ? 'unconfirmed' : e.timedOut ? 'timeout' : e.unconfirmed ? 'network' : 'refused', e.status);
    // A confirmed rule refusal created no message. The server caches that refusal too, so a later explicit Send
    // must be able to try again after its cause is resolved. Uncertain deliveries always retain their key.
    if (pending && e.status >= 400 && e.status < 500 && !e.unconfirmed && !accessRefused) pending.refused = true;
    // A send with no answer in time ends here, with the draft still in the box and one press to try again. The page
    // cannot know whether it landed, so it says so; Retry of the same text keeps its request id, so it is not saved twice.
    if (chatReplayExpired(P)) toast(CHAT_REPLAY_EXPIRED, true);
    else if (accessRefused) toast(`Not confirmed: This message may already have sent. ${chatSendReason(e)} Check the chat before resending.`, true, null, {keepWhenSignedOut: true});
    else if (e.timedOut) toast(`Not confirmed: ${e.message} It may still arrive. Your message is still here; Retry sends it without a duplicate.`, true, {label: 'Retry', run: () => pillSend(P)});
    else if (e.unconfirmed) chatRetryLater(P, text, slug, extraRefs, extra, e.status); else toast(`Not sent: ${chatSendReason(e)}`, true);
    return false;
  }
  finally {
    if (pending) pending.inFlight = null;
    P.sending = false; P.sent = null; sent();
    if (btn) { btn.disabled = false; btn.classList.remove('p-sending'); btn.removeAttribute('aria-busy'); pillBtnSay(btn, label); }
    pillLabel(P); pillButtons(P);
  }
}
// A message without files gives up after `timeout` ms, retries included (writeRequestOnce); an upload has no limit.
// After `slow` ms a send says it is still going. One object, so the browser tests can shorten both.
const CHAT_SEND = {timeout: 30000, slow: 5000};
function chatSendReason(error) {
  if (error.status === 413) return 'The attachments are too large. Remove or shrink a file and try again.';
  if (error.status === 422 && (error.body?.detail?.some?.(e => e.type === 'string_too_long')
      || /(?:body\.)?text: String should have at most \d+ characters/.test(error.body?.error?.detail || '')))
    return 'This message is too long. Shorten it and try again.';
  if (error.status === 401 || error.signedOut) return "You've been signed out. Sign in again, then try again.";
  if (error.status === 404) return 'This bot is unavailable. Refresh the page or choose another bot.';
  if (error.status === 429 || error.status === 503) return `The server is busy (HTTP ${error.status}). Try again shortly.`;
  if (error.status >= 500) return `The server could not send this message (HTTP ${error.status}). Try again shortly.`;
  return error.message || 'The server refused this message. Try again.';
}
// What a send that is taking a while shows under the box, and the one line it leaves in the console when it was slow
// or failed: how long, how many retries, the last HTTP status and how it ended. Never the message text.
function chatSendWatch(P, fileCount) {
  const started = performance.now(), line = pq(P, '.p-slow');
  let retries = 0, status = 0, slow = false, accessReported = false;
  const report = window.TicoObservability?.chatSend?.();
  const diagnostic = outcome => report?.({outcome, duration_ms: Math.round(performance.now() - started), retries, status});
  const say = () => {
    if (!line || !slow) return;
    line.hidden = false;
    line.textContent = fileCount ? `Still sending… uploading ${fileCount === 1 ? 'the file' : `${fileCount} files`}${retries ? ', trying again' : ''}.`
      : retries ? 'Still sending… the connection is slow, trying again.' : 'Still sending…';
  };
  const timer = setTimeout(() => { slow = true; say(); diagnostic('slow'); }, CHAT_SEND.slow);
  return {
    response(code) {
      status = code;
      // Auth failures retire telemetry immediately in api.js; report before that
      // privacy boundary, never restore the retired identity to send an event.
      if ((code === 401 || code === 403) && !accessReported) { diagnostic(P.unconfirmed ? 'unconfirmed' : 'refused'); accessReported = true; }
    },
    retry(attempt, code) { retries = attempt; status = code || status; say(); P.sendRetrying = true; pillWhy(P); },
    end(outcome, code) {
      clearTimeout(timer); P.sendRetrying = false;
      if (line) { line.hidden = true; line.textContent = ''; }
      const ms = Math.round(performance.now() - started);
      status = code || status;
      if (!accessReported && (outcome !== 'sent' || slow)) diagnostic(outcome);
      if (outcome !== 'sent' || ms >= CHAT_SEND.slow)
        console.warn(`chat send ${outcome} after ${ms} ms, ${retries} retries, last status ${code || status || 'none'}, ${fileCount} files`);
    },
  };
}
// Pending text keeps its original identity until the draft is changed or acknowledged, including a later visit to
// this bot in the same tab. Beyond the server's replay retention (backend/store.py), never resend automatically.
const CHAT_REPLAY_MS = 48 * 60 * 60000;
const CHAT_REPLAY_EXPIRED = 'This message may already have sent. Check the chat before resending; edit or clear the draft to send a new message.';
const chatReplayExpired = P => !!P.unconfirmed && Date.now() - P.unconfirmed.at >= CHAT_REPLAY_MS;
function chatForgetUnconfirmed(P) {
  const u = P.unconfirmed; if (!u) return;
  if (!u.acknowledged && u.inFlight) return;        // edit-and-restore while sending is still the same uncertain send
  if (!u.acknowledged && !u.refused && (pq(P, '.p-text')?.value || '').trim() === u.text && !P.files.length) return;
  if (CHAT_PENDING.get(P.slug) === u) { CHAT_PENDING.delete(P.slug); chatPendingSave(); }
  pendingWrites.delete(u.signature); P.unconfirmed = null;
}
// "see if anything was disconnected ... and trigger retries automatically". A
// message that never reached the hub (the network dropped) sends itself when the connection is
// back, or on a backoff up to a minute. It uses the same request id, so the hub never records it
// twice. Editing or clearing the box hands it back to the person.
function chatRetryLater(P, text, slug, extraRefs, extra, status) {
  clearTimeout(P.retryTimer);
  const pending = P.unconfirmed;
  const tries = P.retryTries = (P.retryTries || 0) + 1;
  const busy = status === 429 || status >= 500;
  if (tries > 8) { P.retryTries = 0; toast(`${busy ? 'The server is still busy.' : 'Still offline or no connection.'} Your message may already have sent. Check the chat before resending.`, true); return; }
  if (tries === 1) toast(busy ? `The server is busy (HTTP ${status}). Your message may still arrive; trying again automatically.`
    : 'Offline or connection lost. Your message will send by itself when you are back online.');
  const again = () => {
    window.removeEventListener('online', again); clearTimeout(P.retryTimer);
    if (P.sending) return;
    if (pending && P.unconfirmed !== pending) return;    // editing away and back is still a new draft
    if ((pq(P, '.p-text')?.value || '').trim() !== (text || '').trim()) { P.retryTries = 0; return; }
    void v2ChatSend(P, text, slug, extraRefs, extra);
  };
  window.addEventListener('online', again);
  P.retryTimer = setTimeout(again, Math.min(60000, 5000 * 2 ** (tries - 1)));
}
// A snapshot read (the newest messages, the run and its text, the goal) put on the page: from the first load, and
// again whenever live events say this conversation changed.
function v2ChatApply(state, d) {
  state.latestIds = new Set((d.messages || []).map(m => m.id));
  // A snapshot the server built just before a message of mine landed does not list it yet. What I sent stays on
  // the page, and the run the send started stays live; the next snapshot lists it and this copy drops away.
  const newest = String((d.messages || []).at(-1)?.created_at || '');
  const late = state.mine.filter(m => !state.latestIds.has(m.id) && String(m.created_at || '') >= newest);
  state.messages = [...(state.older || []).filter(m => !state.latestIds.has(m.id)), ...(d.messages || []), ...late];
  if (!state.older?.length) state.nextBefore = d.next_before;
  const wasRunning = !!state.live;
  if (!late.length) {
    state.execution = d.execution;
    state.live = v2RunLive(d.execution) ? {text: d.execution.text} : null;
  }
  if (d.goal !== undefined) chatGoalApply(state, d.goal, {quiet: true});
  CHAT_CACHE.set(state.slug, {mode: state.mode, conv: state.conv, messages: state.messages, nextBefore: state.nextBefore,
                              execution: state.execution, live: state.live});
  v2ChatRender(state);
  // A finished turn may have closed or created tasks: show them now.
  if (wasRunning && !state.live && BOT?.slug === state.slug) {
    if (BOT.loaded.has('tasks')) void loadBotTasksV2(BOT.slug);
    void loadBotChatTasks(BOT.slug);
  }
}
// Read the snapshot again: one read at a time, and one more after it when changes arrived meanwhile, so a run
// streaming its reply costs a read per burst rather than one per word.
async function v2ChatSnapshot(state) {
  if (V2C !== state || !state.conv) return;
  if (state.reading) { state.readAgain = true; return; }
  state.reading = true;
  try {
    const d = await v2Get(`/v2/conversations/${encodeURIComponent(state.conv.id)}/snapshot?${CHAT_PAGE}`);
    if (V2C === state && d) v2ChatApply(state, d);
  } finally {
    state.reading = false;
    if (state.readAgain && V2C === state) { state.readAgain = false; liveSoon('chat:' + state.conv?.id, () => v2ChatSnapshot(state), 150); }
  }
}
// A goal set, paused, met or stopped: read it as the goal route shows it.
async function v2ChatGoalChanged(state) {
  if (V2C !== state || !state.conv) return;
  const d = await v2Get(goalPath(state.conv.id));
  if (V2C === state && d) chatGoalApply(state, d.goal || null);
}
// This chat's messages and runs, from the page's one live stream (ui/app/live.js). `loaded` when the snapshot was
// just read; otherwise (a send, a new room) it is read once now, for what happened before the stream followed it.
// A reply's message and its run's end arrive in one stream read, ms apart: 30 ms still reads them as one snapshot.
const CHAT_SOON_MS = 30;
function v2ChatStream(state, loaded = false) {
  if (!state.conv) return;
  if (S.me?.cloud) {
    if (!liveAvailable()) return;                   // the 15 s poll reads it instead
    const cid = state.conv.id;
    if (!loaded) liveSoon('chat:' + cid, () => v2ChatSnapshot(state), 120);
    if (state.liveOff && state.liveCid === cid) return;
    state.liveOff?.();
    state.liveCid = cid;
    const mine = d => d.conversation_id === cid && V2C === state;
    let down = 0;
    const offs = [
      liveFollow(cid),
      liveOn('messages', d => {
        if (!mine(d)) return;
        if (d.goal_id) void v2ChatGoalChanged(state);
        liveSoon('chat:' + cid, () => v2ChatSnapshot(state), CHAT_SOON_MS);
      }),
      // A run's output arrives step by step (hundreds a minute while it works): the snapshot reads again at most every
      // 0.4 s for the first 10 s of output, so a short reply streams, then every 3 s; never in a hidden tab. A run
      // starting or ending reads at once.
      liveOn('runs', d => {
        if (!mine(d)) return;
        if (d.output) {
          const first = state.outputSince ||= Date.now();
          liveThrottle('chat-output:' + cid, () => { if (V2C === state) void v2ChatSnapshot(state); },
                       Date.now() - first < 10000 ? 400 : 3000);
        } else {
          state.outputSince = 0;
          liveSoon('chat:' + cid, () => v2ChatSnapshot(state), CHAT_SOON_MS);
        }
      }),
      liveOn('reset', () => { if (V2C === state) void v2ChatSnapshot(state); }),
      // A stream that stays down for a few seconds (not the moment it takes to reconnect) marks the reply as interrupted;
      // persisted messages stay on the page meanwhile.
      liveOn('status', st => {
        if (V2C !== state) return;
        clearTimeout(down);
        if (!st.connected) down = setTimeout(() => { if (V2C === state && state.live && !liveConnected()) { state.live.interrupted = true; v2ChatRender(state); } }, 3000);
        else if (state.live?.interrupted) void v2ChatSnapshot(state);
      }),
    ];
    state.liveOff = () => { clearTimeout(down); offs.forEach(off => off()); state.liveOff = null; state.liveCid = null; };
    return;
  }
  if (typeof EventSource === 'undefined') return;
  try { state.es?.close(); } catch {}
  const es = state.es = new EventSource(`${API}/v2/stream?conversation=${encodeURIComponent(state.conv.id)}`);
  const stop = () => { try { es.close(); } catch {} if (state.es === es) state.es = null; };
  es.addEventListener('delta', ev => {
    if (V2C !== state) return stop();
    let d = {}; try { d = JSON.parse(ev.data); } catch {}
    state.live = {text: (state.live?.text || '') + String(d.text || '')};
    const box = $('#v2-live');
    if (box && state.live.text) {
      // The thread's stick follows the reply as it grows, if the reader is at the bottom.
      box.innerHTML = safeMd(state.live.text, {shortLinks: true});
      const copy = box.parentElement.querySelector('[data-chat-copy]');
      if (copy) copy.dataset.chatCopy = state.live.text;
    } else v2ChatRender(state);
  });
  es.addEventListener('goal', ev => {
    if (V2C !== state) return stop();
    let d = {}; try { d = JSON.parse(ev.data); } catch {}
    chatGoalApply(state, d.goal || null);
  });
  es.addEventListener('message', ev => {
    stop();
    if (V2C !== state) return;
    let d = {}; try { d = JSON.parse(ev.data); } catch {}
    if (d.message) state.messages.push(d.message);
    state.live = null;
    v2ChatRender(state);
    void v2Refresh();
  });
  es.addEventListener('error', () => {
    stop();
    if (V2C !== state) return;
    state.live = null;
    v2ChatMessages(state).then(() => v2ChatRender(state));
  });
}

const ICON_CHAT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4c-4.7 0-8.5 3.1-8.5 7 0 2 1 3.8 2.6 5.1L5 20.5l4.3-1.6c.9.2 1.8.4 2.7.4 4.7 0 8.5-3.1 8.5-7S16.7 4 12 4z"/></svg>';
const taskConversationButton = task => `<button class="chat-btn" type="button" data-task-conversation="${esc(task)}" aria-label="Open task conversation" title="Open task conversation">${ICON_CHAT}</button>`;
document.addEventListener('click', async ev => {
  const button = ev.target.closest('[data-task-conversation]'); if (!button) return;
  ev.preventDefault(); ev.stopPropagation();
  await taskThreadOpen(button.dataset.taskConversation);       // the task's own thread (ui/app/task-modal.js)
});
// Bots link a task as <hub>/tasks/<id>, an address the server does not serve, or as #/task/<id>,
// which leaves the chat. Either opens the task pop-up in place.
function taskLinkId(a) {
  let url; try { url = new URL(a.getAttribute('href') || '', location.href); } catch { return ''; }
  if (!hubUrl(url)) return '';
  const m = url.hash.match(/^#\/tasks?\/([^/?#]+)$/) || (!url.hash && url.pathname.match(/^\/tasks?\/([A-Za-z0-9-]{8,80})\/?$/));
  if (!m) return '';
  try { return decodeURIComponent(m[1]); } catch { return ''; }
}
document.addEventListener('click', async ev => {
  if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
  const a = ev.target.closest('a[href]');
  if (!a || a.hasAttribute('download')) return;
  const id = taskLinkId(a);
  // a #/task/ link outside a message (the board, a run row) still goes to the Tasks page
  if (!id || (a.getAttribute('href').startsWith('#') && !a.closest('.md, .bubble, .conv-run, .upd-card, .upd-msg'))) return;
  ev.preventDefault();
  try {
    const data = await get('/v2/tasks/' + encodeURIComponent(id));
    void taskModalShow(data.task);
  } catch (error) { toast(error.message, true); }
});
