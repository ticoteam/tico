/* ui/app/bot-conversation.ts — A bot page's helpers: resume command, runs and docs
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const plural = (n: number, one: string, many?: string): string => `${n} ${n === 1 ? one : (many || one + 's')}`;

// The CLI's own resume command comes from its manifest (`resume_command`); a CLI without one gets `<name> --resume`.
function botResumeCommand(runtime: string | null | undefined, sid: string, slug: string): string {
  const r = String(runtime || '').toLowerCase();
  return `cd ~/tico-work/emp-${botSlug(slug)} && ${harnessCaps(r).resume_command || `${r} --resume`} ${sid}`;
}
// The bot page's Runs card, when /status came back without recent_runs: same cached /runs list.
async function fillBotRuns(slug: string) {
  if (!$('#bot-recent-runs[data-pending]')) return;
  let list: { employee: string }[] = [];
  try { list = (await recentRuns()).filter((r: { employee: string }) => r.employee === slug).slice(0, 10); } catch { /* silent */ }
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
    const tabs: [string, string | undefined][] = [['Learnings', f['memory/learnings.md']], ['Decisions', f['memory/decisions.md']]];
    for (const [n, t] of Object.entries<string>(f.playbooks || {})) if (n !== 'README.md') tabs.push(['Playbook: ' + n.replace(/\.md$/, ''), t]);
    tabs.push(['Manifest', '```yaml\n' + (f['bot.yaml'] || f['employee.yaml'] || '') + '\n```']);
    const tb = $('#tabs'), doc = $('#doc'); if (!tb) return;
    tb.innerHTML = tabs.map(([n], i) => `<button data-i="${i}" class="${i === 0 ? 'cur' : ''}">${esc(n)}</button>`).join('');
    const show = (i: number) => {
      tb.querySelectorAll('button').forEach((b: HTMLElement) => b.classList.toggle('cur', Number(b.dataset.i) === i));
      const tab = tabs[i];
      if (!tab) throw new Error(`No tab ${i}`);
      const text = tab[1];
      doc.innerHTML = text ? md(text) : '<div class="empty">Empty.</div>';
    };
    tb.onclick = (ev: Event) => { const el = eventElement(ev); if (el instanceof HTMLElement && el.dataset.i != null) show(+el.dataset.i); };
    show(0);
  } catch (e) { if ($('#doc')) $('#doc').innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`; }
}
