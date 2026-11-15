/* ui/app/bot-conversation.ts — A bot page's helpers: resume command, runs and docs
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';


// The bot page's Runs card, when /status came back without recent_runs: same cached /runs list.
async function fillBotRuns(slug: string) {
  if (!$('#bot-recent-runs[data-pending]')) return;
  let list: { employee: string }[] = [];
  try { list = (await recentRuns(slug)).filter((r: { employee: string }) => r.employee === slug).slice(0, 10); } catch { /* silent */ }
  if (BOT?.slug !== slug) return;
  const el = $('#bot-recent-runs[data-pending]');
  if (el) { el.removeAttribute('data-pending'); el.innerHTML = runsTable(list, false); }
}
async function loadDocs(slug: string) {
  try {
    const f = await get(`/employees/${encodeURIComponent(slug)}/files`);
    if (BOT?.slug !== slug) return;
    if ($('#agent')) $('#agent').innerHTML = f['AGENTS.md'] ? md(f['AGENTS.md'].replace(/^# .*\n/, '')) : '<div class="empty">Instructions have not been published by the computer yet.</div>';
    for (const [n, t] of Object.entries<string>(f.playbooks || {})) { const el = document.getElementById('pb-' + n); if (el) el.innerHTML = md(t); }
    // The working notes are sections of one card, not tabs inside the Instructions tab (style guide, "Menus: one level":
    // no tabs inside tabs). Each folds behind its heading's chevron; the first with words starts open, an empty one is
    // its heading and one quiet line.
    const notes: [string, string | undefined][] = [['Learnings', f['memory/learnings.md']], ['Decisions', f['memory/decisions.md']]];
    for (const [n, t] of Object.entries<string>(f.playbooks || {})) if (n !== 'README.md') notes.push(['Playbook: ' + n.replace(/\.md$/, ''), t]);
    const manifest = f['bot.yaml'] || f['employee.yaml'] || '';
    notes.push(['Manifest', manifest.trim() ? '```yaml\n' + manifest + '\n```' : '']);
    const host = $('#bot-notes'); if (!host || BOT?.slug !== slug) return;
    const first = notes.findIndex(([, text]) => !!text?.trim());
    host.innerHTML = notes.map(([name, text], i) => text?.trim()
      ? `<details class="bot-note"${i === first ? ' open' : ''}><summary>${esc(name)}</summary><div class="md">${md(text)}</div></details>`
      : `<div class="bot-note bot-note-empty"><span class="bot-note-h">${esc(name)}</span><span class="muted">Empty</span></div>`).join('');
  } catch (e) {
    // A failed read says so in both places it would have filled, never a "Loading…" that stays.
    if (BOT?.slug !== slug) return;
    const said = `<div class="err">${esc(errorMessage(e, 'Could not load them.'))}</div>`;
    const agent = $('#agent'), host = $('#bot-notes');
    if (agent && agent.textContent?.trim() === 'Loading…') agent.innerHTML = said;
    if (host) host.innerHTML = said;
  }
}
