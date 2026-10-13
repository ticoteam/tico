/* ui/app/bot-conversation.js — A bot's conversation replay and its session view
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- conversation (docs/conversation.md)
// One live object per visit. Every async callback checks `CONV === state` before touching the DOM,
// so a route change or a reload can never write into a page that is gone.
let CONV = null;
function convStop() {
  v2ChatStop();                     // the keeper-hosted thread polls and streams; stop it too
  if (!CONV) return;
  clearInterval(CONV.tail); clearInterval(CONV.poll);
  CONV = null;
}
async function loadConversation(slug, keep) {
  convStop();
  const state = CONV = {slug, runs: [], active: null, older: false, pending: keep?.pending || [], replyFor: null, tail: 0, poll: 0, busy: false};
  let d;
  try { d = await get(`/employees/${slug}/conversation`); }
  catch (err) {
    if (CONV !== state || !$('#conv-thread')) return;
    $('#conv-state').textContent = '';
    $('#conv-thread').innerHTML = `<div class="err">Conversation unavailable: ${esc(err.message)}</div>`;
    return;
  }
  if (CONV !== state || !$('#conv-thread')) return;
  state.runs = (d.runs || []).slice().reverse();      // API is newest first; the thread reads oldest first
  state.active = d.active || null;
  state.older = !!d.older;
  state.session = d.session || null;
  convRender(state); convSchedule(state);
}
function convSchedule(state) {
  clearInterval(state.tail); clearInterval(state.poll);
  if (state.active) state.tail = setInterval(() => convTail(state), 3000);
  else state.poll = setInterval(() => loadConversation(state.slug, state), 30000);
}
async function convTail(state) {
  if (CONV !== state || state.busy || !state.active) return;
  state.busy = true;
  try {
    const d = await get(`/employees/${state.slug}/conversation/tail?run=${encodeURIComponent(state.active.run)}&from=${state.active.offset || 0}`);
    if (CONV !== state || !$('#conv-thread')) return;
    if (d.offset != null) state.active.offset = d.offset;
    const g = state.runs.find(r => r.run === (d.run || state.active.run));
    if (g && d.events?.length) g.events = (g.events || []).concat(d.events);
    if (d.done) { state.active = null; convRender(state); convSchedule(state); loadConversation(state.slug, state); return; }
    convRender(state);
  } catch { /* a tail that fails keeps the thread on screen; the next tick tries again */ }
  finally { state.busy = false; }
}
const CONV_COUNTS = ev => ({
  commands: ev.filter(x => x.kind === 'command').length, files: ev.filter(x => x.kind === 'files').length,
  tools: ev.filter(x => x.kind === 'tool').length, searches: ev.filter(x => x.kind === 'search').length,
  diagnostics: ev.filter(x => x.kind === 'diagnostic').length,
});
const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;
function convActivityWords(c) {
  return [c.commands ? `Ran ${plural(c.commands, 'command')}` : '', c.files ? `changed ${plural(c.files, 'file')}` : '',
    c.tools ? plural(c.tools, 'tool call') : '', c.searches ? plural(c.searches, 'search', 'searches') : '',
    c.diagnostics ? plural(c.diagnostics, 'note') : ''].filter(Boolean).join(' · ');
}
function convEvent(e) {
  if (e.kind === 'command') return `<div class="conv-ev"><code>${esc(e.command)}</code>${e.exit ? ` <span class="pill fail">exit ${esc(e.exit)}</span>` : ''}${e.output ? `<pre>${esc(e.output)}</pre>` : ''}</div>`;
  if (e.kind === 'files') return `<div class="conv-ev">Changed <code>${(e.paths || []).map(esc).join('</code>, <code>')}</code>${e.kinds?.length ? ` <span class="muted">(${(e.kinds || []).map(esc).join(', ')})</span>` : ''}</div>`;
  if (e.kind === 'tool') return `<div class="conv-ev">${esc(e.server || 'tool')} · <code>${esc(e.tool || '')}</code> ${e.status && e.status !== 'completed' ? `<span class="pill fail">${esc(e.status)}</span>` : '<span class="pill ok">ok</span>'}</div>`;
  if (e.kind === 'search') return `<div class="conv-ev">Searched the web for <em>${esc(e.query)}</em></div>`;
  if (e.kind === 'reasoning') return `<div class="conv-ev muted">${esc(e.text)}</div>`;
  if (e.kind === 'diagnostic') return `<div class="conv-ev muted">${esc(e.text)}</div>`;
  if (e.kind === 'usage') return '';
  return `<div class="conv-ev muted">${esc(e.kind)}</div>`;
}
// the dispatcher's own routine tickets: the body is a template, not a person's ask
const isRecurring = r => r.recurring === true || /_Created by dispatcher from schedule/.test(String(r.issue_body || ''));
function convGroup(r, slug) {
  const ev = r.events || [];
  const live = r.finished == null;
  const agents = ev.filter(x => x.kind === 'agent' && String(x.text || '').trim());
  const hasReply = typeof r.reply === 'string' && r.reply.trim();
  const reply = hasReply ? r.reply : (agents.length ? agents[agents.length - 1].text : '');
  const inter = hasReply ? agents : agents.slice(0, -1);          // everything before the answer is working
  const counts = CONV_COUNTS(ev);
  if (!Object.values(counts).some(Boolean) && r.activity) Object.assign(counts, r.activity);
  counts.notes = (counts.diagnostics || 0) + inter.length;
  const detail = ev.filter(x => x.kind !== 'agent' && x.kind !== 'usage');
  const chat = r.kind === 'chat' || !!r.message;
  const head = `<div class="conv-run-head">
    ${r.issue ? `<a class="mono" href="${esc(r.issue_url || `${GH}/issues/${r.issue}`)}" target="_blank" rel="noopener">#${esc(r.issue)}</a>` : '<span class="mono muted">chat</span>'}
    <span class="ttl">${esc(chat ? '' : (r.issue_title || 'Untitled task'))}</span>
    ${live ? '<span class="pill in-progress">running</span>' : r.exit !== 0 && r.exit != null ? `<span class="pill fail">failed · exit ${esc(r.exit)}</span>` : ''}
    ${r.needs_human ? `<span class="pill needs">${!personHandle(r.issue_from) || personHandle(r.issue_from) === myHandle() ? 'Needs you' : 'Needs ' + esc(firstName(personDisplay(personHandle(r.issue_from))))}</span>` : ''}
    <span class="spacer" style="flex:1"></span>
    <span class="tnum" title="${esc(fmt(r.started))}">${r.finished ? ago(r.finished) : ago(r.started)}${r.duration_s != null ? ` · ${Math.round(r.duration_s / 60)}m` : ''}</span>
    ${r.local ? '' : `<a class="muted" href="${API}/runs/${esc(r.run)}/log" target="_blank" rel="noopener">log</a>`}</div>${
    live || r.local ? '' : `<div class="conv-replay">Replay of run <span class="mono">${esc(r.run)}</span> on ${
      esc(CONV?.session?.runtime || S.emps.find(e => e.name === slug)?.runtime || 'its runtime')}, ended ${esc(fmt(r.finished) || 'unknown')}${
      r.session_id ? ` · <a href="#" data-sess="${esc(r.session_id)}" data-sess-rt="${esc(CONV?.session?.runtime || S.emps.find(e => e.name === slug)?.runtime || '')}">open the session file</a>` : ''}</div>`}`;
  const ask = chat ? convChatAsk(r)
    : !r.issue_body ? ''
    : isRecurring(r) ? `<div class="bubble sched"><span class="who">Routine</span>${esc(r.issue_title || 'Routine')}
        <details class="sched-body"><summary>show template</summary><div class="q">${esc(r.issue_body)}</div></details></div>`
    : `<div class="bubble you"><span class="who">${esc(personHandle(r.issue_from) || ownerHandle())}</span>${esc(unsigned(r.issue_body))}${chatCopyHTML(unsigned(r.issue_body))}</div>`;
  const words = convActivityWords(counts);
  const working = words || detail.length || inter.length
    ? `<details class="conv-act" data-run="${esc(r.run)}"${CONV?.openWork?.has(r.run) ? ' open' : ''}>
        <summary>Working${words ? ` · ${esc(words)}` : ''}</summary>
        ${inter.map(x => `<div class="conv-ev"><div class="md">${md(x.text)}</div></div>`).join('')}
        ${detail.map(convEvent).join('')}</details>`
    : '';
  const answer = reply ? `<div class="bubble bot reply"><span class="who">${empName(slug)}</span><div class="md">${md(reply)}</div>${chatCopyHTML(reply)}</div>`
    : live ? `<div class="thinking"><span class="dot running"></span>${empName(slug)} is thinking…</div>` : '';
  const comments = (r.comments || []).map(c =>
    c.author === 'human' ? `<div class="bubble you"><span class="who">${esc(personHandle(c.who) || ownerHandle())}</span>${esc(unsigned(c.body))}${chatCopyHTML(unsigned(c.body))}</div>`
    : c.author === 'bot' ? `<div class="bubble bot"><span class="who">${empName(slug)}</span><div class="md">${md(c.body)}</div>${chatCopyHTML(c.body)}</div>`
    : `<div class="conv-note muted">${esc(plainActors(c.body))}</div>`).join('');
  const brk = r.session_break ? `<div class="conv-break">New session started ${esc(fmt(r.started) || '')}</div>` : '';
  return `${brk}<div class="conv-run${chat ? ' chat' : ''}">${head}${ask}${working}${answer}${comments}</div>`;
}
function convChatAsk(r) {
  const m = r.message || {};
  const chips = (m.files || []).map(f => `<span class="fchip">${esc(f)}</span>`).join('');
  return `<div class="bubble you"><span class="who">${esc(personHandle(m.from) || ownerHandle())}</span>${esc(m.text || '')}${chips ? `<div class="bfiles">${chips}</div>` : ''}${chatCopyHTML(m.text)}</div>`;
}
// a chat turn starts a run at once: show it before the first tail comes back
function convChatStarted(slug, j, text, files) {
  const state = CONV; if (!state || state.slug !== slug) return;
  state.runs.push({run: j.run, issue: null, kind: 'chat', message: {from: myHandle(), text, files},
    started: j.started || new Date().toISOString(), finished: null, exit: null,
    events: [], activity: {}, comments: [], local: true});
  state.active = {run: j.run, issue: null, offset: 0};
  convRender(state); convSchedule(state);
}
function convRender(state) {
  const thread = $('#conv-thread'); if (!thread || CONV !== state) return;
  const slug = state.slug;
  state.openWork ||= new Set(); state.wasLive ||= new Set();
  for (const r of state.runs) {                                   // open while live, fold when it lands
    if (r.finished == null) { state.openWork.add(r.run); state.wasLive.add(r.run); }
    else if (state.wasLive.has(r.run)) { state.wasLive.delete(r.run); state.openWork.delete(r.run); }
  }
  // stay pinned to the newest message, unless the reader has scrolled up to read something older
  const atEnd = !state.rendered || thread.scrollTop + thread.clientHeight >= thread.scrollHeight - 40;
  const wasHeight = thread.scrollHeight, wasTop = thread.scrollTop;
  const groups = state.runs.map(r => convGroup(r, slug)).join('');
  state.pending = state.pending.filter(p => !state.runs.some(r => r.issue === p.number));   // the run took it over
  const pending = state.pending.map(p => `<div class="conv-run"><div class="bubble you pending"><span class="who">${esc(myHandle())}</span>${esc(p.text)}${chatCopyHTML(p.text)}</div><div class="conv-run-head">Queued as <a class="mono" href="${esc(p.url)}" target="_blank" rel="noopener">#${esc(p.number)}</a> · picked up within a minute</div></div>`).join('');
  const cleared = state.cleared ? `<div class="conv-break">Session cleared by you ${esc(fmt(state.cleared) || '')}</div>` : '';
  thread.innerHTML = (groups + pending || '<div class="empty">Nothing yet. Say something below.</div>') + cleared;
  thread.querySelectorAll('details.conv-act[data-run]').forEach(d => d.addEventListener('toggle', () => {
    if (d.open) state.openWork.add(d.dataset.run); else state.openWork.delete(d.dataset.run);
  }));
  thread.querySelectorAll('[data-sess]').forEach(a => a.onclick = ev => {      // replay -> the real file
    ev.preventDefault(); sessionOpen(slug, a.dataset.sessRt || '', a.dataset.sess);
  });
  if (atEnd) thread.scrollTop = thread.scrollHeight;
  else thread.scrollTop = wasTop + (thread.scrollHeight - wasHeight);   // "Load older" must not move what you are reading
  state.rendered = true;
  $('#conv-older').innerHTML = state.older ? '<button class="ghost" type="button" id="conv-older-btn">Load older</button>' : '';
  const older = $('#conv-older-btn');
  if (older) older.onclick = async () => {
    older.disabled = true; older.textContent = 'Loading…';
    try {
      const d = await get(`/employees/${slug}/conversation?before=${encodeURIComponent(state.runs[0]?.run || '')}`);
      if (CONV !== state) return;
      state.runs = (d.runs || []).slice().reverse().concat(state.runs);
      state.older = !!d.older; convRender(state);
    } catch (err) { older.disabled = false; older.textContent = `Could not load older runs: ${err.message}`; }
  };
  const last = state.runs[state.runs.length - 1];
  const st = $('#conv-state');
  if (st) st.innerHTML = state.active ? `<span class="pill in-progress">Running${state.active.issue ? ` on #${esc(state.active.issue)}` : ''}</span>`
    : stateOf(slug) === 'paused' ? '<span class="pill">Paused</span>'
    : `Idle${last?.finished ? `, last active ${ago(last.finished)}` : ''}`;
  pillNeeds(BOT_PILL?.slug === slug ? BOT_PILL : null, last?.needs_human && last.issue ? last.issue : null);
}

// ----------------------------------------------------------------- session (the runtime's own file)
// The Chat tab replays what the dispatcher logged, run by run. This tab reads the file the runtime
// itself keeps for the thread the bot resumes — one source, so nothing on screen can drift from
// what the agent actually has. Read fresh on every visit; it does not poll.
let SESS_PICK = null;                    // a run row asked for one session instead of the current one
function sessionOpen(slug, runtime, sid) {
  SESS_PICK = {runtime, sid};
  location.hash = `#/bot/${slug}/session`;
}
const SESS_WHO = {user: 'Prompt', reasoning: 'Thinking', tool: 'Tool', system: 'System prompt'};
const sessTime = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleTimeString(undefined, {hour: '2-digit', minute: '2-digit'}); };
function sessionTurn(t, slug) {
  const when = t.ts ? `<span class="when" title="${esc(fmt(t.ts))}">${esc(sessTime(t.ts))}</span>` : '';
  const who = t.role === 'assistant' ? empName(slug) : esc(SESS_WHO[t.role] || t.role);
  if (t.role === 'tool') {
    const line = t.command || (t.files || []).join(', ') || 'tool call';
    const body = t.output ? `<pre>${esc(t.output)}</pre>`
      : t.files?.length ? `<pre>${esc((t.files || []).join('\n'))}</pre>`
      : '<pre class="muted">No output recorded in the session file.</pre>';
    return `<div class="sess-turn tool">${when}<details><summary><code>${esc(line)}</code>${
      t.exit ? ` <span class="pill fail">exit ${esc(t.exit)}</span>` : ''}</summary>${body}</details></div>`;
  }
  const body = t.role === 'assistant' ? `<div class="md">${md(t.text || '')}</div>`
    : `<div class="body">${esc(t.text || '')}</div>`;
  return `<div class="sess-turn ${esc(t.role)}">${when}<span class="who">${who}</span>${body}</div>`;
}
function botResumeCommand(runtime, sid, slug) {
  const r = String(runtime || '').toLowerCase();
  let cmd;
  if (r === 'codex') cmd = `codex resume ${sid}`;
  else if (r === 'claude') cmd = `claude --resume ${sid}`;
  else if (r === 'gemini') cmd = `gemini --resume ${sid}`;
  else if (r === 'grok') cmd = `grok --resume ${sid}`;
  else if (r === 'pi') cmd = `pi --resume ${sid}`;
  else if (r === 'cursor') cmd = `cursor-agent --resume ${sid}`;
  else if (r === 'antigravity') cmd = `antigravity --resume ${sid}`;
  else cmd = `${r} --resume ${sid}`;
  return `cd ~/tico-work/emp-${slug} && ${cmd}`;
}
function sessionHead(d, slug) {
  const runs = (d.runs || []).map(r => r.issue ? `#${r.issue}` : 'chat');
  const pointer = d.provider_session;
  const sid = pointer?.thread_id || (d.session_id && d.session_id !== 'cloud' ? d.session_id : '');
  const resumeCmd = sid ? botResumeCommand(d.runtime, sid, slug) : '';
  const resumeBox = sid ? `
    <div class="sess-resume" style="margin:8px 0;padding:10px 12px;background:var(--surface2);border-radius:var(--radius);display:flex;flex-direction:column;gap:8px">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">
        <div><span class="muted">Session ID:</span> <code class="mono" style="color:var(--ink);cursor:pointer" data-copy-sess-id="${esc(sid)}" title="Tap to copy">${esc(sid)}</code></div>
        <button class="ghost" style="padding:3px 8px;font-size:11.5px" type="button" data-copy-sess-id="${esc(sid)}">Copy ID</button>
      </div>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">
        <div style="overflow-wrap:anywhere"><span class="muted">Resume:</span> <code class="mono" style="color:var(--ink);cursor:pointer" data-copy-sess-cmd="${esc(resumeCmd)}" title="Tap to copy">${esc(resumeCmd)}</code></div>
        <button class="primary" style="padding:3px 8px;font-size:11.5px" type="button" data-copy-sess-cmd="${esc(resumeCmd)}">Copy command</button>
      </div>
    </div>` : '';
  const pointerLine = pointer
    ? `Tico pointer: <span class="mono">${esc(pointer.thread_id)}</span>`
      + (pointer.tokens_in != null ? ` · ${esc(String(pointer.tokens_in))} tokens in` : '')
      + (pointer.updated ? ` · saved ${esc(ago(pointer.updated))}` : '')
      + (pointer.runner_id ? ` · runner ${esc(pointer.runner_id)}` : '')
    : 'Tico pointer: none yet';
  return `${d.cloud ? 'Cloud session' : d.current ? 'Live session file' : 'Session file'}: <strong>${esc(harnessWords(d.runtime))}</strong>
    ${d.model ? `<span class="muted">${esc(modelWords(d.model))}</span>` : ''}
    <span class="mono">${esc(d.session_id)}</span>
    ${pointerLine}
    ${resumeBox}
    ${d.first_ts ? `started ${esc(fmt(d.first_ts))}` : ''} · ${esc(String(d.turn_count))} runs${
      d.shown < d.turn_count ? ` <span class="muted">(showing the last ${esc(String(d.shown))})</span>` : ''}
    ${runs.length ? ` · covers runs ${esc(runs.join(', '))}` : ' · unused'}
    ${d.last_ts ? ` · last written ${esc(ago(d.last_ts))}` : ''}
    ${d.skipped ? ` · <span title="record types this reader does not know">${esc(String(d.skipped))} records skipped</span>` : ''}
    ${d.current ? '' : ' · <a href="#" data-sess-current="1">back to the current session</a>'}
    <span class="mono">${esc(d.path || '')}</span>`;
}
async function sessionLoad(slug, pick) {
  const head = $('#sess-head'), body = $('#sess-turns');
  if (!head || !body) return;
  head.textContent = 'Reading the session file…'; body.innerHTML = '';
  let d;
  try {
    d = await get(pick ? `/sessions/${encodeURIComponent(pick.runtime)}/${encodeURIComponent(pick.sid)}`
                       : `/employees/${slug}/session`);
  } catch (err) {
    if (!BOT || BOT.slug !== slug || !$('#sess-head')) return;
    head.innerHTML = `<span class="muted">${esc(err.message)}</span>`;
    body.innerHTML = `<div class="empty">Nothing here. See the Chat tab.</div>`;
    return;
  }
  if (!BOT || BOT.slug !== slug || !$('#sess-head')) return;
  head.innerHTML = sessionHead(d, slug);
  head.querySelectorAll('[data-copy-sess-id]').forEach(el => {
    el.onclick = ev => {
      ev.preventDefault();
      void copyText(el.dataset.copySessId).then(() => toast('Session ID copied'));
    };
  });
  head.querySelectorAll('[data-copy-sess-cmd]').forEach(el => {
    el.onclick = ev => {
      ev.preventDefault();
      void copyText(el.dataset.copySessCmd).then(() => toast('Resume command copied'));
    };
  });
  const back = head.querySelector('[data-sess-current]');
  if (back) back.onclick = ev => { ev.preventDefault(); sessionLoad(slug, null); };
  body.innerHTML = (d.turns || []).map(t => sessionTurn(t, slug)).join('')
    || '<div class="empty">No runs yet.</div>';
  body.scrollTop = body.scrollHeight;                       // newest last, like the thread
}

async function loadBotIssues(slug) {
  try {
    const list = await get(`/employees/${slug}/issues`);
    const open = list.filter(i => i.state === 'OPEN').sort((a,b) => (a.needs_human ? 0 : 1) - (b.needs_human ? 0 : 1) || (a.priority||'p9').localeCompare(b.priority||'p9'));
    const closed = list.filter(i => i.state === 'CLOSED').sort((a,b) => b.closedAt.localeCompare(a.closedAt)).slice(0, 10);
    if (!$('#t-open')) return;
    $('#cnt-done').textContent = list.length - open.length || '';
    const row = (i, when, label) => `<details class="trow"><summary>
        <span class="mono muted">#${i.number}</span><span class="ttl">${esc(i.title)}</span>
        <span class="tags">${i.state === 'OPEN' ? statusPill(i) : ''}${i.priority && i.priority !== 'p2' ? ' ' + prioPill(i) : ''}</span>
        <span class="muted tnum">${ago(when)}</span></summary>
      <div class="tbody">${i.last_comment ? `<div class="lbl">${label}</div><div class="q">${esc(i.last_comment)}</div>` : i.body ? `<div class="q">${esc(i.body)}</div>` : '<div class="muted">No details yet.</div>'}
        </div></details>`;
    $('#t-open').innerHTML = open.length ? open.map(i => row(i, i.updatedAt, 'Last comment')).join('') : '<div class="rail-empty">None</div>';
    $('#t-done').innerHTML = closed.length ? closed.map(i => row(i, i.closedAt, 'Closing comment')).join('') : '<div class="rail-empty">None</div>';
  } catch (e) { if ($('#t-open')) $('#t-open').innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
// The bot page's Runs card, when /status came back without recent_runs: same cached /runs list.
async function fillBotRuns(slug) {
  if (!$('#bot-recent-runs[data-pending]')) return;
  let list = [];
  try { list = (await recentRuns()).filter(r => r.employee === slug).slice(0, 10); } catch { /* silent */ }
  const el = $('#bot-recent-runs[data-pending]');
  if (el) { el.removeAttribute('data-pending'); el.innerHTML = runsTable(list, false); }
}
async function loadDocs(slug) {
  try {
    const f = await get(`/employees/${slug}/files`);
    if ($('#agent')) $('#agent').innerHTML = f['AGENT.md'] ? md(f['AGENT.md'].replace(/^# .*\n/, '')) : '<div class="empty">Instructions have not been published by the computer yet.</div>';
    for (const [n, t] of Object.entries(f.playbooks || {})) { const el = document.getElementById('pb-' + n); if (el) el.innerHTML = md(t); }
    const tabs = [['Status note', f['state.md']], ['Learnings', f['memory/learnings.md']], ['Decisions', f['memory/decisions.md']]];
    for (const [n, t] of Object.entries(f.playbooks || {})) if (n !== 'README.md') tabs.push(['Playbook: ' + n.replace(/\.md$/, ''), t]);
    tabs.push(['Manifest', '```yaml\n' + (f['bot.yaml'] || f['employee.yaml'] || '') + '\n```']);
    const tb = $('#tabs'), doc = $('#doc'); if (!tb) return;
    tb.innerHTML = tabs.map(([n], i) => `<button data-i="${i}" class="${i === 0 ? 'cur' : ''}">${esc(n)}</button>`).join('');
    const show = i => { tb.querySelectorAll('button').forEach(b => b.classList.toggle('cur', +b.dataset.i === i)); doc.innerHTML = tabs[i][1] ? md(tabs[i][1]) : '<div class="empty">Empty.</div>'; };
    tb.onclick = ev => { if (ev.target.dataset.i != null) show(+ev.target.dataset.i); };
    show(0);
  } catch (e) { if ($('#doc')) $('#doc').innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
