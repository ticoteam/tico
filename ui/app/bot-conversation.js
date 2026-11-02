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
  const state = CONV = {slug, runs: [], active: null, older: false, pending: keep?.pending || [], replyFor: null, tail: 0, poll: 0, busy: false, loadedAt: Date.now()};
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
  if (state.active) state.tail = setInterval(() => { if (!document.hidden) convTail(state); }, 3000);
  // Idle replay used to reload every 30 s even from a background tab; the live tail already skips when hidden.
  else state.poll = setInterval(() => { if (!document.hidden) loadConversation(state.slug, state); }, 30000);
}
// A tab shown again catches up on the idle reloads it skipped, so a run that started meanwhile gets its tail.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && CONV && !CONV.active && Date.now() - CONV.loadedAt >= 30000) loadConversation(CONV.slug, CONV);
});
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
  const live = r.finished_at == null;
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
    <span class="tnum" title="${esc(fmt(r.started_at))}">${r.finished_at ? ago(r.finished_at) : ago(r.started_at)}${r.duration_s != null ? ` · ${Math.round(r.duration_s / 60)}m` : ''}</span>
    ${r.local ? '' : `<a class="muted" href="${API}/runs/${esc(r.run)}/log" target="_blank" rel="noopener">log</a>`}</div>${
    live || r.local ? '' : `<div class="conv-replay">Replay of run <span class="mono">${esc(r.run)}</span> on ${
      esc(CONV?.session?.runtime || S.emps.find(e => e.name === slug)?.runtime || 'its runtime')}, ended ${esc(fmt(r.finished_at) || 'unknown')}</div>`}`;
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
  const brk = r.session_break ? `<div class="conv-break">New session started ${esc(fmt(r.started_at) || '')}</div>` : '';
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
    started_at: j.started_at || new Date().toISOString(), finished_at: null, exit: null,
    events: [], activity: {}, comments: [], local: true});
  state.active = {run: j.run, issue: null, offset: 0};
  state.sent = true; convRender(state); convSchedule(state);
}
function convRender(state) {
  const thread = $('#conv-thread'); if (!thread || CONV !== state) return;
  const slug = state.slug;
  state.openWork ||= new Set(); state.wasLive ||= new Set();
  for (const r of state.runs) {                                   // open while live, fold when it lands
    if (r.finished_at == null) { state.openWork.add(r.run); state.wasLive.add(r.run); }
    else if (state.wasLive.has(r.run)) { state.wasLive.delete(r.run); state.openWork.delete(r.run); }
  }
  // stay pinned to the newest message, unless the reader has scrolled up to read something older (ui/app/stick-scroll.js);
  // the stick belongs to the thread, so the 30 s reload (a new state) keeps the reader's place
  const stick = stickScroll(thread, {pill: $('#conv-jump')});
  if (state.sent) { state.sent = false; stick.toBottom(); }           // what you just sent is where you look
  const groups = state.runs.map(r => convGroup(r, slug)).join('');
  state.pending = state.pending.filter(p => !state.runs.some(r => r.issue === p.number));   // the run took it over
  const pending = state.pending.map(p => `<div class="conv-run"><div class="bubble you pending"><span class="who">${esc(myHandle())}</span>${esc(p.text)}${chatCopyHTML(p.text)}</div><div class="conv-run-head">Queued as <a class="mono" href="${esc(p.url)}" target="_blank" rel="noopener">#${esc(p.number)}</a> · picked up within a minute</div></div>`).join('');
  const cleared = state.cleared ? `<div class="conv-break">Session cleared by you ${esc(fmt(state.cleared) || '')}</div>` : '';
  const html = (groups + pending || '<div class="empty">Nothing yet. Say something below.</div>') + cleared;
  const newest = `${state.runs.at(-1)?.run}:${state.runs.at(-1)?.events?.length || 0}:${state.pending.length}`;
  const fresh = !!thread.dataset.newest && thread.dataset.newest !== newest;
  thread.dataset.newest = newest;
  stick.update(() => { thread.innerHTML = html; }, {fresh, prepended: state.prepending});
  state.prepending = false;
  thread.querySelectorAll('details.conv-act[data-run]').forEach(d => d.addEventListener('toggle', () => {
    if (d.open) state.openWork.add(d.dataset.run); else state.openWork.delete(d.dataset.run);
  }));
  $('#conv-older').innerHTML = state.older ? '<button class="ghost" type="button" id="conv-older-btn">Load older</button>' : '';
  const older = $('#conv-older-btn');
  if (older) older.onclick = async () => {
    older.disabled = true; older.textContent = 'Loading…';
    try {
      const d = await get(`/employees/${slug}/conversation?before=${encodeURIComponent(state.runs[0]?.run || '')}`);
      if (CONV !== state) return;
      state.runs = (d.runs || []).slice().reverse().concat(state.runs);
      state.older = !!d.older; state.prepending = true; convRender(state);   // "Load older" must not move what you are reading
    } catch (err) { older.disabled = false; older.textContent = `Could not load older runs: ${err.message}`; }
  };
  const last = state.runs[state.runs.length - 1];
  const st = $('#conv-state');
  if (st) st.innerHTML = state.active ? `<span class="pill in-progress">Running${state.active.issue ? ` on #${esc(state.active.issue)}` : ''}</span>`
    : stateOf(slug) === 'paused' ? '<span class="pill">Paused</span>'
    : `Idle${last?.finished_at ? `, last active ${ago(last.finished_at)}` : ''}`;
  pillNeeds(BOT_PILL?.slug === slug ? BOT_PILL : null, last?.needs_human && last.issue ? last.issue : null);
}

// The CLI's own resume command comes from its manifest (`resume_command`); a CLI without one gets `<name> --resume`.
function botResumeCommand(runtime, sid, slug) {
  const r = String(runtime || '').toLowerCase();
  return `cd ~/tico-work/emp-${slug} && ${harnessCaps(r).resume_command || `${r} --resume`} ${sid}`;
}
async function loadBotIssues(slug) {
  try {
    const list = await get(`/employees/${slug}/issues`);
    const open = list.filter(i => i.state === 'OPEN').sort((a,b) => (a.needs_human ? 0 : 1) - (b.needs_human ? 0 : 1) || (a.priority||'p9').localeCompare(b.priority||'p9'));
    // Done Recently: closed in the last 14 days, as for a Tico bot's tasks.
    const since = new Date(Date.now() - BOT_DONE_RECENT_DAYS * 86400000).toISOString();
    const closed = list.filter(i => i.state === 'CLOSED' && String(i.closedAt || '') >= since).sort((a,b) => b.closedAt.localeCompare(a.closedAt));
    if (!$('#t-open')) return;
    botTaskCount('cnt-done', closed.length);
    const row = (i, when, label) => `<details class="trow"><summary>
        <span class="mono muted">#${i.number}</span><span class="ttl">${esc(i.title)}</span>
        <span class="tags">${i.state === 'OPEN' ? statusPill(i) : ''}${i.priority && i.priority !== 'p2' ? ' ' + prioPill(i) : ''}</span>
        <span class="muted tnum">${ago(when)}</span></summary>
      <div class="tbody">${i.last_comment ? `<div class="lbl">${label}</div><div class="q">${esc(i.last_comment)}</div>` : i.body ? `<div class="q">${esc(i.body)}</div>` : '<div class="muted">No details yet.</div>'}
        </div></details>`;
    $('#t-open').innerHTML = open.length ? open.map(i => row(i, i.updatedAt, 'Last comment')).join('') : '<div class="rail-empty">None</div>';
    $('#t-done').innerHTML = closed.length ? closed.map(i => row(i, i.closedAt, 'Closing comment')).join('') : '<div class="rail-empty">Nothing done in the last two weeks.</div>';
    // An Issues bot has no questions or waits in Tico: those tabs are simply empty.
    $('#t-needs').innerHTML = '<div class="rail-empty">Nothing needs you.</div>';
    $('#t-blocked').innerHTML = '<div class="rail-empty">Nothing is blocked.</div>';
  } catch (e) { if ($('#t-open')) $('#t-open').innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
// The bot page's Runs card, when /status came back without recent_runs: same cached /runs list.
async function fillBotRuns(slug) {
  if (!$('#bot-recent-runs[data-pending]')) return;
  let list = [];
  try { list = (await recentRuns()).filter(r => r.employee === slug).slice(0, 10); } catch { /* silent */ }
  if (BOT?.slug !== slug) return;
  const el = $('#bot-recent-runs[data-pending]');
  if (el) { el.removeAttribute('data-pending'); el.innerHTML = runsTable(list, false); }
}
async function loadDocs(slug) {
  try {
    const f = await get(`/employees/${slug}/files`);
    if (BOT?.slug !== slug) return;
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
