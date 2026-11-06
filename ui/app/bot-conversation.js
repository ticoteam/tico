/* ui/app/bot-conversation.js — A bot page's helpers: resume command, issues, runs and docs
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;

// The CLI's own resume command comes from its manifest (`resume_command`); a CLI without one gets `<name> --resume`.
function botResumeCommand(runtime, sid, slug) {
  const r = String(runtime || '').toLowerCase();
  return `cd ~/tico-work/emp-${botSlug(slug)} && ${harnessCaps(r).resume_command || `${r} --resume`} ${sid}`;
}
async function loadBotIssues(slug) {
  try {
    const list = await get(`/employees/${encodeURIComponent(slug)}/issues`);
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
    const f = await get(`/employees/${encodeURIComponent(slug)}/files`);
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
