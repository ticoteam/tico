/* ui/app/bot-conversation.ts — A bot page's helpers: resume command, runs and docs
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;

// The CLI's own resume command comes from its manifest (`resume_command`); a CLI without one gets `<name> --resume`.
function botResumeCommand(runtime, sid, slug) {
  const r = String(runtime || '').toLowerCase();
  return `cd ~/tico-work/emp-${botSlug(slug)} && ${harnessCaps(r).resume_command || `${r} --resume`} ${sid}`;
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
