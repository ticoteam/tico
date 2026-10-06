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
  V2C.resize?.disconnect();
  V2C = null;
}
// Which of the viewer's rooms with a bot is its chat: the shared room, or one of their own. The bot page and the
// Goal Manager on Goals both show this one room.
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
// `room` is a chat the caller already holds (the Assistant page: GET /v2/assistant gives its room and messages), so
// nothing is looked up; `empty` is what an empty thread shows instead of the default line.
async function v2ChatLoad(slug, room = null) {
  v2ChatStop();
  const mode = v2ChatMode(slug);
  const state = V2C = {slug, mode, conv: null, messages: [], mine: [], live: null, es: null, poll: 0, rendered: false, followLatest: true,
                       loaded: false, failed: false, empty: room?.empty || ''};
  const seen = CHAT_CACHE.get(slug);
  if (room) {
    const x = room.execution;
    Object.assign(state, {conv: room.conv, messages: room.messages || [], nextBefore: room.nextBefore, execution: x,
                          live: S.me?.cloud && x && x.state !== 'completed' ? {text: x.text || ''} : null, listed: true, loaded: true});
    v2ChatRender(state);
  } else if (seen && seen.mode === mode) {
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
        state.conv = v2ChatRoom(list.conversations, state.slug, state.mode);
      }
    }
    if (!state.conv || await v2ChatMessages(state)) { state.loaded = true; state.failed = false; return; }
  }
  state.failed = true;
}
// Between bots: what this bot asked, answered, or was handed by other bots (asks and task
// conversations), newest first, so a person can see the COO check with the CMO from either
// page. The owner-only route answers 403 for anyone else, and the section stays empty.
const BOTS_SHOWN = 5;
async function v2BotsLoad(state) {
  const el = $('#conv-bots');
  if (!el || V2C !== state) return;
  const list = await v2Get(`/v2/conversations?bot=${encodeURIComponent(state.slug)}`);
  if (V2C !== state || !list) return;
  const me = `bot:${state.slug}`;
  const convs = (list.conversations || [])
    .filter(c => c.kind !== 'chat' && c.kind !== 'notice'
      && (c.participants || []).some(p => p !== me && String(p).startsWith('bot:')))
    .slice(0, BOTS_SHOWN);
  if (!convs.length) { el.innerHTML = ''; return; }
  const bodies = await Promise.all(convs.map(c => v2Get(`/v2/conversations/${encodeURIComponent(c.id)}/messages`)));
  if (V2C !== state) return;
  el.innerHTML = `<details class="conv-bots-box"><summary>Between bots<span class="muted">${convs.length} recent</span></summary>${
    convs.map((c, n) => {
      const other = (c.participants || []).find(p => p !== me) || '';
      const msgs = (bodies[n]?.messages || []);
      return `<div class="conv-bots-one"><div class="conv-run-head">${actorChip(other)}
          <span class="pill">${esc(c.kind || '')}</span>${c.subject ? `<span class="muted">${esc(c.subject)}</span>` : ''}
          <span class="spacer" style="flex:1"></span><span class="tnum">${esc(ago(c.last_message_at || c.created))}</span></div>
        ${msgs.map(m => `<div class="bubble ${m.from_actor === me ? 'bot reply' : 'you'}"><span class="who">${esc(actorLabel(m.from_actor))}</span><div class="md">${safeMd(m.body || '')}</div>${chatCopyHTML(m.body)}</div>`).join('')
          || '<div class="empty">No messages yet.</div>'}</div>`;
    }).join('')}</details>`;
}
async function v2ChatMessages(state) {
  if (!state.conv) return false;
  const sent = state.mine.length;
  const d = await v2Get(`/v2/conversations/${encodeURIComponent(state.conv.id)}/${S.me?.cloud ? 'snapshot' : 'messages'}`);
  if (V2C !== state || !d) return false;
  // A message sent while this was loading is newer than the copy that came back: keep it, and leave the
  // live turn to the stream (this copy has not seen it start).
  const late = state.mine.slice(sent).filter(m => !(d.messages || []).some(x => x.id === m.id));
  state.messages = [...(d.messages || []), ...late];
  state.nextBefore = d.next_before;
  if (S.me?.cloud && !late.length) {
    state.execution = d.execution;
    state.live = d.execution && d.execution.state !== 'completed' ? {text: d.execution.text} : null;
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
const CHAT_IMAGES = new Map();                 // file id -> Promise<object URL>
function chatImageURL(f) {
  if (!CHAT_IMAGES.has(f.id)) CHAT_IMAGES.set(f.id, fetch(`${API}/v2/files/${encodeURIComponent(f.id)}`, {credentials: 'same-origin'})
    .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.blob(); })
    .then(b => URL.createObjectURL(new Blob([b], {type: /^image\//.test(f.content_type || '') ? f.content_type : 'image/png'})))
    .catch(error => { CHAT_IMAGES.delete(f.id); throw error; }));
  return CHAT_IMAGES.get(f.id);
}
function chatAttachmentsHTML(files) {
  if (!files.length) return '';
  return `<div class="chat-files">${files.map(f => isChatImage(f)
    ? `<button type="button" class="chat-thumb" data-chat-image="${esc(f.id)}" data-type="${esc(f.content_type || '')}" data-name="${esc(f.name || 'image')}" aria-label="View ${esc(f.name || 'image')}" title="${esc(f.name || 'image')}"><img alt="${esc(f.name || 'image')}" hidden></button>`
    : `<a class="pill" href="${API}/v2/files/${encodeURIComponent(f.id)}">${esc(f.name)}</a>`).join('')}</div>`;   // opens in the viewer, or downloads
}
function chatThumbsLoad(root = document) {
  for (const button of root.querySelectorAll('.chat-thumb:not([data-loading])')) {
    button.dataset.loading = '1';
    const f = {id: button.dataset.chatImage, content_type: button.dataset.type, name: button.dataset.name};
    chatImageURL(f).then(url => { const img = button.querySelector('img'); img.src = url; img.hidden = false; button.classList.add('ready'); })
      .catch(() => { button.replaceWith(Object.assign(document.createElement('a'), {className: 'pill', href: `${API}/v2/files/${encodeURIComponent(f.id)}`, download: f.name, textContent: f.name})); });
  }
}
new MutationObserver(() => { if (document.querySelector('.chat-thumb:not([data-loading])')) chatThumbsLoad(); })
  .observe(document.documentElement, {childList: true, subtree: true});
function chatImageOpen(button) {
  let d = $('#img-lightbox');
  if (!d) {
    d = document.createElement('dialog'); d.id = 'img-lightbox'; d.className = 'img-lightbox';
    d.addEventListener('click', ev => { if (!ev.target.closest('a')) d.close(); });
    document.body.append(d);
  }
  const src = button.querySelector('img')?.src; if (!src) return;
  const download = button.dataset.chatImage ? `<a href="${API}/v2/files/${encodeURIComponent(button.dataset.chatImage)}" download>Download</a>` : '';
  d.innerHTML = `<img src="${esc(src)}" alt="${esc(button.dataset.name)}"><div class="img-lightbox-bar"><span>${esc(button.dataset.name)}</span>${download}</div>`;
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
    bits.push(`<a class="fchip" href="${esc(String(refs.slack.permalink))}" target="_blank" rel="noopener">Slack ${esc(String(refs.slack.channel_name || ''))}</a>`);
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
function v2RunHTML(m) {
  const run = m.run || {}, rows = [], given = new Set((run.did || []).filter(d => d.kind === 'task').map(d => d.task_id));
  for (const [id, t] of Object.entries(m.ref_tasks || {}))
    if (!given.has(id)) rows.push(v2RunRow('task ·', esc(t.title), {task: id}));
  for (const d of run.did || []) {
    if (d.kind === 'task') rows.push(v2RunRow(d.owner === m.from_actor ? 'made itself a task ·' : `gave ${esc(youAware(d.owner))} a task ·`, esc(d.title), {task: d.task_id}));
    else if (d.kind === 'link') {
      const ref = shortRef(d.url);
      const mark = ref ? `<span class="ref ref-${ref.kind}">${esc(ref.label)}</span> ` : '';
      rows.push(v2RunRow('filed', mark + esc(d.title || (ref ? '' : d.url)), {href: d.url, title: d.url}));
    } else if (d.kind === 'ask' || d.kind === 'say') {
      const slug = actorSlug(d.to);
      rows.push(v2RunRow(`${d.kind === 'ask' ? 'asked' : 'messaged'} ${esc(youAware(d.to))} ·`, esc(d.text), {href: slug ? `#/bot/${encodeURIComponent(slug)}` : ''}));
    } else if (d.kind === 'refused') rows.push(v2RunRow('refused ·', esc(d.text || d.rule), {failed: true, title: `Tico refused this (${d.rule})`}));
  }
  if (run.steps) {
    const label = [`${run.steps} step${run.steps === 1 ? '' : 's'}`, run.tool_calls ? `${run.tool_calls} tool call${run.tool_calls === 1 ? '' : 's'}` : '', tookWords(run.took_s)].filter(Boolean).join(' · ');
    const turn = m.refs?.turn_id, open = V2C?.openSteps?.has(turn);
    rows.push(`<details class="run-steps" data-turn="${esc(turn)}"${open ? ' open' : ''}><summary>${esc(label)}</summary><div class="steps-list">${
      V2C?.steps?.[turn] || '<div class="step muted">Loading…</div>'}</div></details>`);
  }
  return rows.length ? `<div class="run-did">${rows.join('')}</div>` : '';
}
// A turn's steps are read when someone opens them, once, and kept open across redraws.
function v2StepsHTML(d) {
  const steps = d.steps || [];
  if (!steps.length) return '<div class="step muted">No steps were recorded for this run.</div>';
  return steps.map(st => st.kind === 'thinking'
    ? `<details class="step think"><summary>Thinking</summary><div class="step-text">${esc(st.text)}</div></details>`
    : `<div class="step${st.kind === 'error' ? ' err' : ''}" title="${esc(fmt(st.at))}"><span class="step-tool">${esc(st.kind === 'tool' ? st.tool : st.kind)}</span><span class="step-sum">${esc(st.text)}</span></div>`).join('');
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
const COMMIT_EXCLUSION_NOTICE = /^left out of the commit: (.+) \(contains a secret\)$/;
const COMMIT_NOT_PUSHED = 'not pushed: a commit made this turn contains a secret';
function v2CommitExclusionSource(m) {
  const refs = m?.refs || {}, attempt = refs.run?.attempt_id;
  return typeof m?.from_actor === 'string' && m.from_actor.startsWith('bot:') &&
    typeof refs.turn_id === 'string' && refs.turn_id.length > 0 && attempt === refs.turn_id;
}
function v2CommitNoticeParagraph(value) {
  if (typeof value !== 'string') return null;
  const match = value.match(COMMIT_EXCLUSION_NOTICE);
  return match ? {path: match[1], reason: 'contains a secret'} : null;
}
function v2CommitExclusionDisplay(m) {
  const original = String(m?.body || '');
  if (!v2CommitExclusionSource(m)) return {text: original, notices: []};
  const refs = m.refs || {};
  const supplied = refs.commit_exclusions;
  const structured = Array.isArray(supplied) && supplied.length > 0 && supplied.length <= 200 &&
    supplied.every(item => item && typeof item.path === 'string' && item.path.length > 0 &&
      item.path.length <= 4096 && item.reason === 'contains a secret');
  const stripGenerated = (value, block) => {
    if (value === block) return '';
    if (!value.endsWith(block)) return null;
    const prefix = value.slice(0, -block.length), separator = prefix.match(/\n+$/)?.[0] || '';
    return separator.length >= 2 ? prefix.slice(0, -2) : null;
  };
  let text = original, warning = '';
  if (text === COMMIT_NOT_PUSHED) { warning = text; text = ''; }
  else if (text.endsWith(COMMIT_NOT_PUSHED)) {
    const visible = stripGenerated(text, COMMIT_NOT_PUSHED);
    if (visible !== null) { warning = COMMIT_NOT_PUSHED; text = visible; }
  }
  let notices = [];
  if (structured) {
    const block = supplied.map(item => `left out of the commit: ${item.path} (contains a secret)`).join('\n\n');
    const visible = stripGenerated(text, block);
    if (visible !== null) {
      text = visible;
      notices = supplied;
    }
  }
  if (!notices.length) {
    // Historical runner replies have only the text suffix. Restrict recognition to an exact
    // terminal paragraph block and a server-authenticated bot run reply; quotes stay ordinary text.
    const paragraphs = text.replace(/\n+$/, '').split(/\n{2,}/), tail = [];
    while (paragraphs.length) {
      const notice = v2CommitNoticeParagraph(paragraphs[paragraphs.length - 1]);
      if (!notice) break;
      tail.unshift(notice); paragraphs.pop();
    }
    if (tail.length) { notices = tail; text = paragraphs.join('\n\n'); }
  }
  if (warning) text = text ? `${text}\n\n${warning}` : warning;
  return {text, notices};
}
function v2CommitExclusionsHTML(notices, open = false) {
  if (!notices.length) return '';
  const count = notices.length, summary = `${count} ${count === 1 ? 'file' : 'files'} excluded from commit`;
  return `<details class="commit-exclusions"${open ? ' open' : ''}><summary>${summary}</summary><ul>${notices.map(item =>
    `<li><code>${esc(item.path)}</code><span>${esc(item.reason === 'contains a secret' ? 'Contains a secret' : item.reason)}</span></li>`
  ).join('')}</ul></details>`;
}
document.addEventListener('click', async ev => {
  const button = ev.target.closest('.bubble [data-chat-copy]');
  if (!button) return;
  ev.preventDefault();
  try { await copyText(button.dataset.chatCopy); toast('Message copied'); }
  catch { toast('Could not copy the message', true); }
});
// Tico Live was retired; the lines it left in rooms (refs.live) read as plain messages.
function v2MessageHTML(m) {
  // A Confirm card a bot left for you (BotOps: adding a person, a role, a shared credential): filled in from the action.
  // A bot asking for a secret: the card takes it here and it goes straight to Credentials (ui/credential-card.js).
  if (m.refs?.credential_request) return `<div class="conv-run chat" data-message="${esc(m.id)}"><div data-credential-host="${esc(m.refs.credential_request)}"><div class="asst-state">Loading the card…</div></div></div>`;
  if (m.refs?.action) return `<div class="conv-run chat" data-message="${esc(m.id)}"><div data-action-host="${esc(m.refs.action)}"><div class="asst-state">Loading the card…</div></div></div>`;
  // A met or stopped goal's notice from the server is the compact goal line (ui/app/chat-goal.js), not a Task line.
  if (m.refs?.chat_goal) return chatGoalNoticeHTML(m);
  if (m.kind === 'notice' || m.refs?.note) {
    const when = `<div class="chat-meta"><time class="chat-time" title="${esc(fmt(m.created))}">${esc(ago(m.created))}</time></div>`;
    return `<div class="chat-system"><b>${m.refs?.note ? 'Note' : 'Task'}</b> ${esc(plainActors(m.body))}${when}</div>`;
  }
  const pid = actorPerson(m.from_actor);
  const mine = !!pid;                       // any person's message sits on the right, under their name
  const who = mine ? esc(personHandle(pid)) : esc(actorLabel(m.from_actor));
  const me = m.from_actor === myActor();    // your own lines need no name on a bot's page
  const commitExclusion = !mine ? v2CommitExclusionDisplay(m) : {text: m.body || '', notices: []};
  const exclusionsOpen = commitExclusion.notices.length && V2C?.openCommitExclusions?.has(m.id) ? ' open' : '';
  const body = mine ? esc(m.body || '') : `${commitExclusion.text ? `<div class="md">${safeMd(commitExclusion.text, {shortLinks: true})}</div>` : ''}${v2CommitExclusionsHTML(commitExclusion.notices, !!exclusionsOpen)}`;
  return `<div class="conv-run chat${me ? ' from-me' : ''}" data-message="${esc(m.id || '')}">
    <time class="chat-stamp" datetime="${esc(m.created || '')}" title="${esc(fmt(m.created))}">${esc(ago(m.created))}</time>
    <div class="conv-run-head">${mine ? `<span class="mono muted">${who}</span>` : actorChip(m.from_actor)}
      ${m.kind && m.kind !== 'say' ? `<span class="pill">${esc(m.kind)}</span>` : ''}
      <span class="spacer" style="flex:1"></span>
      <span class="tnum" title="${esc(fmt(m.created))}">${esc(ago(m.created))}</span></div>
    <div class="bubble ${mine ? 'you' : 'bot reply'}"><span class="who">${who}</span>${body}${chatCopyHTML(m.body)}</div>
    ${S.me?.cloud ? chatAttachmentsHTML(m.refs?.attachments || []) : ''}
    ${v2RunHTML(m)}${v2MessageCards(m)}</div>`;
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
  if (state.live?.interrupted) return {word: 'Reconnecting', tip: 'Connection interrupted — reconnecting', mode: 'wait'};
  if (!x) return {word: 'Sent', tip: 'Sent', mode: 'wait'};
  if (x.state === 'queued') {
    if (x.label && x.label !== 'Saved — queued') return {word: x.label, tip: x.label, mode: 'wait'};   // the server's reason
    const s = v2StatusOf(state.slug);
    return {word: 'Queued', mode: 'wait', tip: s?.state === 'running' && s.focus
      ? `${name} is on another run: ${s.focus}` : 'Starts when its computer picks it up'};
  }
  if (x.state === 'leased') return {word: 'Starting', tip: `${name} is starting`, mode: 'run'};
  if (x.state === 'running') return x.label && x.label !== 'Working'
    ? {word: x.label, tip: x.label, mode: 'run'} : {word: '', tip: `${name} is working`, mode: 'run'};
  return {word: x.label || x.state, tip: x.label || x.state, mode: 'wait'};
}
function v2PendingHTML(state) {
  const x = state.execution, key = x?.message_id || 'sent';
  if (state.dismissed === key) return '';
  const sent = Date.parse(state.messages.find(m => m.id === x?.message_id)?.created || '') || state.sentAt || 0;
  clearTimeout(state.waitTimer);
  const late = S.me?.cloud && sent && Date.now() - sent >= REPLY_WAIT_MS;
  if (S.me?.cloud && sent && !late) state.waitTimer = setTimeout(() => v2ChatRender(state), sent + REPLY_WAIT_MS - Date.now() + 50);
  const providerMissing = x?.state === 'queued' && x?.readiness_reason === 'missing_provider';
  if (late && !providerMissing) return `<div class="thinking late" data-pending="${esc(key)}"><span class="dot"></span>
    <span>No reply after 20 minutes · <button type="button" class="dismiss" data-pending-dismiss>dismiss</button></span></div>`;
  const t = v2PendingText(state);
  return `<div class="thinking" data-pending="${esc(key)}" title="${esc(t.tip)}" role="status" aria-label="${esc(t.tip)}">
    <span class="typing ${t.mode}" aria-hidden="true"><i></i><i></i><i></i></span>${t.word ? `<span class="thinking-word" aria-hidden="true">${esc(t.word)}</span>` : ''}${providerMissing ? ' <a href="#/settings" data-gs-tab="providers">Add an AI provider</a>' : ''}</div>`;
}
// Scrolled up when something new lands: a small pill above the composer takes you to it.
function v2Jump(state, fresh) {
  const jump = $('#conv-jump'), thread = $('#conv-thread');
  if (!jump || !thread) return;
  const last = state.messages.at(-1);
  if (state.followLatest) state.seenLast = last?.id;
  const show = !state.followLatest && !!last && last.id !== state.seenLast;
  if (show && fresh) jump.textContent = actorPerson(last.from_actor) ? 'Latest ↓' : 'New reply ↓';
  jump.hidden = !show;
  jump.onclick = () => {
    state.followLatest = true; thread.scrollTop = thread.scrollHeight;
    v2Jump(state);
  };
}
function v2ChatRender(state) {
  const thread = $('#conv-thread');
  if (!thread || V2C !== state) return;
  state.openCommitExclusions ||= new Set();
  if (!state.resize) {
    state.threadHeight = thread.clientHeight;
    thread.addEventListener('scroll', () => {
      if (V2C === state && thread.clientHeight === state.threadHeight) {
        state.followLatest = thread.scrollTop + thread.clientHeight >= thread.scrollHeight - 40;
        if (state.followLatest) v2Jump(state);
      }
    }, {passive: true});
    thread.addEventListener('toggle', ev => {
      if (V2C !== state) return;
      if (ev.target.matches?.('details.run-steps')) { v2StepsOpen(state, ev.target); return; }
      if (ev.target.matches?.('details.commit-exclusions')) {
        const id = ev.target.closest('.conv-run.chat')?.dataset.message;
        if (!id) return;
        if (ev.target.open) state.openCommitExclusions.add(id); else state.openCommitExclusions.delete(id);
      }
    }, true);
    thread.addEventListener('click', ev => {
      if (!ev.target.closest('[data-pending-dismiss]') || V2C !== state) return;
      state.dismissed = ev.target.closest('[data-pending]')?.dataset.pending;
      v2ChatRender(state);
    });
    state.resize = new ResizeObserver(() => {
      if (V2C === state && state.followLatest) thread.scrollTop = thread.scrollHeight;
      state.threadHeight = thread.clientHeight;
    });
    state.resize.observe(thread);
  }
  const atEnd = !state.rendered || state.followLatest;
  const wasTop = thread.scrollTop, wasHeight = thread.scrollHeight;
  // A goal's controls (the /goal messages the server sends for Set, Pause, Clear) are the bar above, not bubbles.
  const shown = state.messages.filter(m => m.refs?.maintenance !== 'checkpoint' && !chatGoalControl(m)), rows = shown.map(v2MessageHTML);
  const goalLine = chatGoalLine(state, shown);
  if (goalLine) rows.splice(goalLine.at, 0, goalLine.html);
  const groups = rows.join('');
  const pending = state.live && !state.live.text ? v2PendingHTML(state) : '';
  if (!pending) clearTimeout(state.waitTimer);
  const live = state.live?.text
    ? `<div class="conv-run chat"><div class="bubble bot reply"><span class="who">${empName(state.slug)}</span>
        <div class="md" id="v2-live">${safeMd(state.live.text, {shortLinks: true})}</div>${chatCopyHTML(state.live.text)}</div></div>`
    : pending ? `<div class="conv-run chat">${pending}</div>` : '';
  thread.innerHTML = (groups + live) || (state.failed ? '<div class="empty">Could not load the conversation yet; trying again…</div>'
    : !state.loaded ? '<div class="empty">Loading the thread…</div>' : state.empty || '<div class="empty">Nothing yet. Say something below.</div>');
  if (thread.querySelector('[data-action-host]')) void window.assistantChat?.cards(thread, {get, post, esc, toast,
    reload: () => v2ChatMessages(state).then(() => { if (V2C === state) v2ChatRender(state); })});
  if (thread.querySelector('[data-credential-host]')) void window.credentialCards?.mount(thread, {get, post, esc, toast,
    reload: () => v2ChatMessages(state).then(() => { if (V2C === state) v2ChatRender(state); })});
  if (atEnd) thread.scrollTop = thread.scrollHeight;
  else thread.scrollTop = wasTop + (state.prepending ? thread.scrollHeight - wasHeight : 0);
  state.prepending = false;
  v2Jump(state, state.rendered);
  state.rendered = true;
  botAvatarsSync();                     // the bot breathes while it answers
  const older = $('#conv-older');
  if (older) {
    older.innerHTML = state.nextBefore ? '<button class="ghost" type="button" data-cloud-older>Load older messages</button>' : '';
    older.querySelector('[data-cloud-older]')?.addEventListener('click', async ev => {
      ev.target.disabled = true;
      try { if (await v2ChatOlder(state)) v2ChatRender(state); }
      catch (error) {toast(error.message, true); ev.target.disabled = false;}
    });
  }
  const st = $('#conv-state');
  if (st) st.innerHTML = state.live ? (t => `<span class="pill in-progress" title="${esc(t.tip)}">${esc(t.word || 'Working')}</span>`)(v2PendingText(state)) : v2StatePill(state.slug);
  pausedRender();
  chatGoalRender(state);
  chatOutlineSync(state);
  const access = $('#conv-access');
  if (access) {
    if (state.mode === 'shared') {
      const people = (state.conv?.participants || []).filter(p => String(p).startsWith('human:')).map(actorLabel);
      access.innerHTML = `<span class="pill">Shared room</span>${people.length ? ` <span class="muted">${esc(people.join(', '))}</span>` : ''}`;
    } else {
      access.innerHTML = '<span class="pill ok">Private</span> <span class="muted">only you and this bot</span>';
    }
  }
  // Header updates above can resize the thread after its content renders.
  requestAnimationFrame(() => { if (V2C === state && state.followLatest) thread.scrollTop = thread.scrollHeight; });
}
// One page further back; the outline's jump reuses it. False when the chat changed meanwhile.
async function v2ChatOlder(state) {
  const d = await get(`/v2/conversations/${encodeURIComponent(state.conv.id)}/messages?before=${encodeURIComponent(state.nextBefore)}`);
  if (V2C !== state) return false;
  state.messages = [...d.messages, ...state.messages.filter(m => !d.messages.some(x => x.id === m.id))]; state.nextBefore = d.next_before;
  state.older = state.messages.filter(m => !state.latestIds?.has(m.id));
  state.followLatest = false; state.prepending = true;
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
  state.followLatest = true;
  v2ChatRender(state);
  v2ChatStream(state);
}
// `extra` adds fields to the message itself: {command: true} hands the text to the harness as its own slash command.
async function v2ChatSend(P, text, slug = P.slug, extraRefs = {}, extra = {}) {
  // Return during a send that is still out waits for it, then sends what the box still holds; returning
  // here without a word was a message lost.
  while (P.sending) {
    if (!P.sent) return false;                       // a task being created, not a chat send
    await P.sent;
    if ((pq(P, '.p-text')?.value || '').trim() !== (text || '').trim() || !text && !P.files.length) return false;
  }
  P.sending = true;
  let sent; P.sent = new Promise(done => sent = done);
  const files = P.files.slice();
  const btn = pq(P, '.p-send'), label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; pillBtnSay(btn, 'Sending…'); }
  try {
    let refs = {...extraRefs};
    const j = await cloudCompose(`/v2/chat/${encodeURIComponent(slug)}`, {text: text || 'Attached files.', refs, ...extra}, files);
    // A 200 that created nothing (a sign-in page in front of the API) is not a send: the draft stays.
    if (!j?.message?.id) throw new Error('the server did not confirm it');
    pillAcknowledge(P, text, files);
    if (j.message?.refs?.action_result) toast(j.message.refs.action_result.decision === 'approved' ? 'Approved' : 'Declined');
    v2ChatAdopt(slug, j);
    P.retryTries = 0;
    return true;
  } catch (e) {
    if (e.unconfirmed) chatRetryLater(P, text, slug, extraRefs, extra); else toast(`Not sent: ${e.message}`, true);
    return false;
  }
  finally { P.sending = false; P.sent = null; sent(); if (btn) { btn.disabled = false; pillBtnSay(btn, label); } pillLabel(P); pillButtons(P); }
}
// "see if anything was disconnected ... and trigger retries automatically". A
// message that never reached the hub (the network dropped) sends itself when the connection is
// back, or on a backoff up to a minute. It uses the same request id, so the hub never records it
// twice. Editing or clearing the box hands it back to the person.
function chatRetryLater(P, text, slug, extraRefs, extra) {
  clearTimeout(P.retryTimer);
  const tries = P.retryTries = (P.retryTries || 0) + 1;
  if (tries > 8) { P.retryTries = 0; toast('Still no connection. Your message is still here, so send it again once you are back online.', true); return; }
  if (tries === 1) toast('No connection. Your message will send by itself when you are back online.');
  const again = () => {
    window.removeEventListener('online', again); clearTimeout(P.retryTimer);
    if (P.sending) return;
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
  const newest = String((d.messages || []).at(-1)?.created || '');
  const late = state.mine.filter(m => !state.latestIds.has(m.id) && String(m.created || '') >= newest);
  state.messages = [...(state.older || []).filter(m => !state.latestIds.has(m.id)), ...(d.messages || []), ...late];
  if (!state.older?.length) state.nextBefore = d.next_before;
  const wasRunning = !!state.live;
  if (!late.length) {
    state.execution = d.execution;
    state.live = d.execution && d.execution.state !== 'completed' ? {text: d.execution.text} : null;
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
    const d = await v2Get(`/v2/conversations/${encodeURIComponent(state.conv.id)}/snapshot`);
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
        liveSoon('chat:' + cid, () => v2ChatSnapshot(state), 120);
      }),
      liveOn('runs', d => { if (mine(d)) liveSoon('chat:' + cid, () => v2ChatSnapshot(state), 120); }),
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
      box.innerHTML = safeMd(state.live.text, {shortLinks: true});
      const copy = box.parentElement.querySelector('[data-chat-copy]');
      if (copy) copy.dataset.chatCopy = state.live.text;
      const thread = $('#conv-thread'); if (thread) thread.scrollTop = thread.scrollHeight;
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
  try {
    const data = await get('/v2/tasks/' + encodeURIComponent(button.dataset.taskConversation));
    void taskModalShow(data.task);
  } catch (error) { toast(error.message, true); }
});
