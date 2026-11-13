/* ui/app/skills.ts — Library › Skills (#/library/skills): one page, no tabs. On top, the proposals waiting for an OK
   (pending only, hidden when there are none: a decided proposal is history, kept as the skill's versions); then the
   company's skills, each with who it is on, its version and when it was last used; then an admin's Suggestions and Not
   in directory. A skill's own page is where it is installed, removed, and its versions and files are read. Backend:
   backend/skills.py (directory, versions, proposals, installs), backend/skill_usage.py (usage, install targets) and
   backend/skill_suggestions.py (admins' Remove / Ask to submit suggestions); docs/skills.md.
   Addresses:
     #/library/skills                              the list (an old ?tab= shows it too)
     #/library/skills/<name>                       one skill: versions, files, Install, Remove, usage
     #/library/skills/proposals/<id>               one proposal: screening, diff, Approve / Decline
   The server decides who sees what (admins see who used a skill; anyone else totals and their own rows); this page
   only draws it. Classic script: its globals are shared with the other files under ui/app/. */
'use strict';

// The answers the skills pages read (backend/skills.py, skill_usage.py, skill_suggestions.py). Fields the page
// reads may be absent or null; a field the page reads only inside a check is optional.
interface SkillActor { id?: string; type?: string; name?: string | null; }
interface SkillRef { number: number; }
interface SkillRow { name: string; description?: string | null; installs_count?: number; latest?: SkillRef | null; on?: SkillActor[]; }
interface SkillUsageRow { name: string; uses: number; last_used_at?: string | null; }
interface SkillUsageTotals { uses?: number; people?: number; bots?: number; by_trigger?: Record<string, number>; }
interface SkillUsageActor extends SkillActor { uses: number; last_used_at?: string | null; }
interface SkillUsageRecent { actor?: SkillActor | null; requested_by?: SkillActor | null; trigger?: string; used_at?: string; task_id?: string | null; thread_id?: string | null; }
interface SkillInstallCoverage { id?: string; target?: SkillActor | null; coverage: string; uses_30d: number; pinned_version?: number | null; removable?: boolean; }
interface SkillElsewhere { name: string; uses: number; bots: SkillActor[]; bots_count: number; }
interface SkillUsage {
  admin?: boolean; totals?: SkillUsageTotals; actors?: SkillUsageActor[]; recent?: SkillUsageRecent[];
  installs?: SkillInstallCoverage[]; skills?: SkillUsageRow[]; popular_elsewhere?: SkillElsewhere[];
}
interface SkillIdea { id: string; kind: string; name: string; target: SkillActor | null; uses_30d?: number; bots?: number; }
interface SkillProposal {
  id: string; name: string; status: string; base_version?: number | null; proposed_by?: SkillActor | null;
  screen_status?: string; created_at: string; decline_reason?: string | null;
}
interface SkillFinding { file?: string; line?: number | string; severity?: string; issue?: string; detail?: string; }
interface SkillPattern { file?: string; line?: number | string; pattern?: string; }
interface SkillScreening {
  risk?: string | null; verdict?: string | null; summary?: string | null; reason?: string | null;
  findings?: SkillFinding[] | null; patterns?: SkillPattern[] | null;
}
interface SkillFileRow { path: string; content: string; }
interface SkillVersionRow { number: number; sha256?: string | null; approved_at?: string | null; approved_by_id?: string | null; }
interface SkillInfo { name: string; description?: string | null; latest?: SkillRef | null; }
interface SkillDetail { skill: SkillInfo; versions?: SkillVersionRow[]; }
interface SkillTargetRef { type?: string; id: string; }
interface SkillInstallTargets {
  me?: { type?: string; id: string } | null;
  bots: { id: string; name: string }[];
  people: { id: string; name: string }[];
  teams: { id: string; name: string; targets: SkillTargetRef[] }[];
}
interface SkillProposalDetail {
  proposal: SkillProposal; screening?: SkillScreening | null; stale?: boolean;
  diff?: string | null; diff_latest?: string | null; files?: SkillFileRow[];
}
// The page on screen (SKP): its run, the skill's name and the usage window.
interface SkillsPage { run: number; name?: string; days?: number | undefined; }

const SKILLS_ROUTE = '#/library/skills';
const SKILLS_TRIGGERS: [string, string][] = [['model', 'Model'], ['person', 'Person'], ['nested', 'Nested']];
const SKILLS_SCREEN_CAP = 50;   // waiting proposals whose screening risk is read for the list
let SKP: SkillsPage | null = null;                 // {run}: the page on screen, so a slow answer for a page left behind draws nothing
let SKILLS_Q = '';                                 // the list's search, kept across redraws

const skillsHref = (name: string) => SKILLS_ROUTE + '/' + encodeURIComponent(name);
const skillsProposalHref = (id: string) => SKILLS_ROUTE + '/proposals/' + encodeURIComponent(id);
const skillsSha = (sha: string | null | undefined) => sha ? `<code class="sk-sha" title="${esc(sha)}">${esc(sha.slice(0, 7))}</code>` : '';
// A bot or person as the server names them; a row without a name falls back to the roster's.
const skillsWho = (a: SkillActor | null | undefined) => a?.type !== 'Bot' && a?.id === S.me?.id ? 'You'
  : a?.name || (a?.type === 'Bot' ? botDisplayName(a.id) : personDisplay(a?.id || ''));
const skillsErr = (e: unknown) => `<p class="err" data-sk-error>${esc(errorMessage(e, String(e)))}</p>`;
const skillsLive = (run: number) => SKP && SKP.run === run;
const skillsN = (n: unknown) => Number(n || 0).toLocaleString();
const skillsUses = (n: number | null | undefined) => `${skillsN(n)} ${n === 1 ? 'use' : 'uses'}`;
function skillsScreenPill(p: SkillProposal, screen?: SkillScreening | null) {
  if (p.screen_status === 'screened') {
    const risk = screen?.risk || '', bad = risk === 'high' || screen?.verdict === 'unsafe';
    return `<span class="pill ${bad ? 'fail' : risk === 'medium' ? 'waiting' : 'ok'}" data-sk-screen="screened">Screened${risk ? ' · ' + esc(risk) : ''}</span>`;
  }
  if (p.screen_status === 'not_screened') return '<span class="pill fail" data-sk-screen="not_screened">Not screened</span>';
  return '<span class="pill waiting" data-sk-screen="pending">Pending</span>';
}
const skillsStatusPill = (s: string) => s === 'pending' ? '' : `<span class="pill${s === 'approved' ? ' ok' : ''}" data-sk-status="${esc(s)}">${esc(s.charAt(0).toUpperCase() + s.slice(1))}</span>`;

// ----------------------------------------------------------------- the frame: title, Claude Code
function skillsHead() {
  return `<header class="tt-head"><h1>Skills</h1><span class="spacer"></span>
      <button type="button" class="ghost small" data-sk-helper><span class="nav-icon" aria-hidden="true">content_copy</span>Use in my Claude Code</button></header>`;
}
function skillsBindHead(root: HTMLElement) {
  root.querySelector<HTMLElement>('[data-sk-helper]')?.addEventListener('click', skillsHelperDialog);
}
// The one line that sets up the skills helper on this computer (clients/skill_helper.mjs, served by the hub).
const skillsHelperCommand = () => `curl -fsSL --create-dirs ${publicUrl().replace(/\/+$/, '')}/api/v2/skills/helper.mjs -o ~/.tico/skills-helper.mjs && node ~/.tico/skills-helper.mjs setup`;
function skillsHelperDialog() {
  const command = skillsHelperCommand();
  const dialog = skillsDialog('Use in my Claude Code', `<pre class="sk-cmd" data-sk-command>${esc(command)}</pre>`,
    '<button type="button" class="primary" data-sk-copy>Copy</button>');
  const copy = dialog.querySelector<HTMLButtonElement>('[data-sk-copy]');
  if (copy) copy.onclick = async () => {
    try { await copyText(command); toast('Copied'); } catch (e) { toast(errorMessage(e, ''), true); }
  };
}
// A modal in the house style; `actions` is the buttons after Cancel. Returns the open <dialog>.
function skillsDialog(title: string, body: string, actions: string, {cancel = 'Close'}: {cancel?: string} = {}): HTMLDialogElement {
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal sk-dialog'; dialog.setAttribute('aria-label', title);
  dialog.innerHTML = `<form method="dialog"><div class="tmodal-title">${esc(title)}</div><div class="tmodal-body">${body}
    <p class="err" data-sk-dialog-error hidden></p>
    <div class="onb-actions"><button type="submit" class="ghost" value="cancel" data-sk-cancel>${esc(cancel)}</button><span class="spacer"></span>${actions}</div></div></form>`;
  dialog.addEventListener('close', () => dialog.remove());
  // Enter in a field presses the dialog's own button, not Close (the form's first submit button).
  dialog.addEventListener('keydown', ev => {
    const target = eventElement(ev);
    if (ev.key !== 'Enter' || !target?.matches('input')) return;
    ev.preventDefault();
    dialog.querySelector<HTMLElement>('.onb-actions .primary')?.click();
  });
  document.body.appendChild(dialog); dialog.showModal();
  return dialog;
}
const skillsDialogError = (dialog: HTMLDialogElement, e: unknown) => {
  const p = dialog.querySelector<HTMLElement>('[data-sk-dialog-error]'); if (!p) return;
  p.textContent = errorMessage(e, String(e)); p.hidden = false;
};

// ----------------------------------------------------------------- the list
async function pageSkills() {
  const run = (SKP?.run || 0) + 1;
  SKP = {run};
  $('#main').innerHTML = `<div class="tt sk" id="skills-root" data-sk-page="skills">${skillsHead()}
    <div data-sk-body>${skelRows(5)}</div></div>`;
  const root = $('#skills-root');
  skillsBindHead(root);
  const body = root.querySelector('[data-sk-body]');
  try { await skillsList(body, run); } catch (e) { if (skillsLive(run)) body.innerHTML = skillsErr(e); }
}

async function skillsList(body: HTMLElement, run: number) {
  const [list, usage, waiting] = await Promise.all([get<{skills?: SkillRow[]}>('/v2/skills', {fresh: true}),
    get<SkillUsage>('/v2/skills/usage?days=30', {fresh: true}),
    get<{proposals?: SkillProposal[]}>('/v2/skills/proposals', {fresh: true})]);
  // Suggestions are for admins: the usage read says who is one, so a member never asks.
  const ideas: {suggestions?: SkillIdea[]} = usage.admin ? await get<{suggestions?: SkillIdea[]}>('/v2/skills/suggestions', {fresh: true}).catch((): {suggestions?: SkillIdea[]} => ({})) : {};
  const pending = (waiting.proposals || []).filter(p => p.status === 'pending');
  // The proposal list has no screening risk: read it for the screened rows (the newest first, capped).
  const screens: Record<string, SkillScreening | null | undefined> = {};
  await Promise.all(pending.filter(p => p.screen_status === 'screened').slice(0, SKILLS_SCREEN_CAP).map(p =>
    get<{screening?: SkillScreening | null}>('/v2/skills/proposals/' + encodeURIComponent(p.id)).then(d => { screens[p.id] = d.screening; }, () => {})));
  if (!skillsLive(run)) return;
  const used = Object.fromEntries((usage.skills || []).map((u): [string, SkillUsageRow] => [u.name, u]));
  const rows = list.skills || [];
  const table = rows.length ? `<div class="tt-bar"><input type="search" class="tt-q" data-sk-q placeholder="Search" aria-label="Search skills" autocomplete="off" spellcheck="false" value="${esc(SKILLS_Q)}"></div>
    <table class="tt-list sk-list"><thead><tr><th>Skill</th><th>On</th><th>Version</th><th>Last used</th></tr></thead>
    <tbody>${rows.map(s => { const u: Partial<SkillUsageRow> = used[s.name] || {}; return `<tr data-sk-row="${esc(s.name)}" data-sk-find="${esc((s.name + ' ' + (s.description || '')).toLowerCase())}">
      <td class="sk-name"><a href="${skillsHref(s.name)}"><strong>${esc(s.name)}</strong></a></td>
      <td class="sk-on" data-sk-on>${skillsOnHTML(s)}</td>
      <td class="sk-ver tnum">${s.latest ? 'v' + s.latest.number : ''}</td>
      <td class="sk-when">${u.last_used_at ? agoSpan(u.last_used_at) : '<span class="muted">Never</span>'}</td></tr>`; }).join('')}</tbody></table>`
    : '<div class="empty" data-sk-empty>No skills yet.</div>';
  body.innerHTML = skillsWaitingHTML(pending, screens) + table + skillsIdeasHTML(ideas.suggestions || []) + skillsElsewhereHTML(usage.popular_elsewhere || []);
  skillsRowLinks(body);
  skillsBindIdeas(body, ideas.suggestions || []);
  const q = body.querySelector<HTMLInputElement>('[data-sk-q]');
  if (q) { q.oninput = () => { SKILLS_Q = q.value; skillsFilter(body); }; skillsFilter(body); }
}
// Who a skill is on: the first few the server names (the ones the caller may see), then +N for the rest of its installs.
function skillsOnHTML(s: SkillRow) {
  const on = s.on || [], more = Math.max(0, (s.installs_count || 0) - on.length);
  return esc(on.map(skillsWho).join(', ')) + (more ? `${on.length ? ' ' : ''}<span class="muted">+${more}</span>` : '');
}
function skillsFilter(body: HTMLElement) {
  const q = SKILLS_Q.trim().toLowerCase();
  body.querySelectorAll<HTMLElement>('tr[data-sk-row]').forEach(tr => { tr.hidden = !!q && !(tr.dataset.skFind || '').includes(q); });
}
// The proposals waiting for an OK; nothing at all when none are.
function skillsWaitingHTML(pending: SkillProposal[], screens: Record<string, SkillScreening | null | undefined>) {
  if (!pending.length) return '';
  return `<section class="card sk-waiting" data-sk-waiting><header><h2>Waiting for OK</h2></header>
    <table class="tt-who-table">${pending.map(p => `<tr data-sk-proposal="${esc(p.id)}" data-sk-status="pending">
      <td class="tt-who-name"><a href="${skillsProposalHref(p.id)}"><strong>${esc(p.name)}</strong></a></td>
      <td class="sk-screen">${skillsScreenPill(p, screens[p.id])}</td>
      <td class="tt-used muted sk-by">${esc(skillsWho(p.proposed_by))}</td>
      <td class="tt-used muted sk-when">${agoSpan(p.created_at)}</td></tr>`).join('')}</table></section>`;
}
// Remove / Ask to submit suggestions from the maintenance run (backend/skill_suggestions.py). Admins only.
function skillsIdeasHTML(ideas: SkillIdea[]) {
  if (!ideas.length) return '';
  return `<section class="card sk-ideas" data-sk-ideas><header><h2>Suggestions</h2></header>
    <table class="tt-who-table">${ideas.map(i => `<tr data-sk-idea="${esc(i.id)}" data-sk-kind="${esc(i.kind)}">
      <td class="tt-who-name"><strong>${esc(i.name)}</strong> <span class="muted">${esc(skillsWho(i.target))}</span></td>
      <td class="tt-used muted">${i.kind === 'remove' ? 'Unused 30d' : `${skillsUses(i.uses_30d)} · ${skillsN(i.bots)} ${i.bots === 1 ? 'bot' : 'bots'}`}</td>
      <td class="sk-act"><button type="button" class="ghost small" data-sk-idea-go>${i.kind === 'remove' ? 'Remove' : 'Ask to submit'}</button>
        <button type="button" class="ghost small" data-sk-idea-dismiss>Dismiss</button></td></tr>`).join('')}</table></section>`;
}
function skillsBindIdeas(box: HTMLElement, ideas: SkillIdea[]) {
  box.querySelectorAll<HTMLElement>('[data-sk-idea]').forEach(tr => {
    const idea = ideas.find(i => i.id === tr.dataset.skIdea); if (!idea) return;
    const base = '/v2/skills/suggestions/' + encodeURIComponent(idea.id);
    const go = tr.querySelector<HTMLButtonElement>('[data-sk-idea-go]');
    if (go) go.onclick = async () => {
      try {
        if (idea.kind === 'remove') {
          const ok = await settingsConfirm({title: 'Remove ' + idea.name, text: 'It leaves on the next session.', ok: 'Remove'});
          if (!ok) return;
          await post(base + '/remove', {});
          toast('Removed');
        } else {
          // The admin's own message to the bot: it runs the submit itself, and an admin still approves.
          const bot = idea.target; if (!bot) return;
          await post('/v2/messages', {to: 'bot:' + bot.id,
            text: `Please run \`tico skill submit ${idea.name}\` to propose ${idea.name} for the company skills directory.`});
          await post(base + '/asked', {});
          toast('Asked');
        }
        skillsRedraw();
      } catch (e) { toast(errorMessage(e, ''), true); }
    };
    const dismiss = tr.querySelector<HTMLButtonElement>('[data-sk-idea-dismiss]');
    if (dismiss) dismiss.onclick = async () => {
      try { await post(base + '/dismiss', {}); skillsRedraw(); } catch (e) { toast(errorMessage(e, ''), true); }
    };
  });
}
// Skills bots ran that the directory does not have (admins; null for anyone else).
function skillsElsewhereHTML(rows: SkillElsewhere[]) {
  if (!rows.length) return '';
  return `<section class="card sk-elsewhere" data-sk-elsewhere><header><h2>Not in directory</h2></header>
    <table class="tt-who-table">${rows.map(r => `<tr data-sk-unlisted="${esc(r.name)}">
      <td class="tt-who-name"><strong>${esc(r.name)}</strong></td>
      <td class="tnum tt-used">${skillsUses(r.uses)}</td>
      <td class="tt-used muted sk-bots">${esc((r.bots || []).map(skillsWho).join(', '))}${r.bots_count > (r.bots || []).length ? ` +${r.bots_count - r.bots.length}` : ''}</td></tr>`).join('')}</table></section>`;
}
// A table's whole row opens its first link.
function skillsRowLinks(box: HTMLElement) {
  // Every table's body: Waiting for OK, the skills, Suggestions (rows without a link do nothing).
  box.querySelectorAll('tbody').forEach(tbody => { tbody.onclick = ev => {
    const target = eventElement(ev);
    if (target?.closest('a, button, input, select')) return;
    const href = target?.closest('tr')?.querySelector('a[href]')?.getAttribute('href');
    if (href) location.hash = href;
  }; });
}

async function skillsRescreen(id: string) {
  try { await post(`/v2/skills/proposals/${encodeURIComponent(id)}/rescreen`, {}); toast('Screening'); skillsRedraw(); }
  catch (e) { toast(errorMessage(e, ''), true); }
}

// ----------------------------------------------------------------- one skill
async function pageSkill(name: string) {
  const run = (SKP?.run || 0) + 1;
  const page: SkillsPage = {run, name, days: SKP?.name === name ? SKP.days : 30};
  SKP = page;
  $('#main').innerHTML = `<div class="tt sk" id="skills-root" data-sk-page="skill">
    <div class="tt-crumb"><a href="${SKILLS_ROUTE}">Skills</a><span aria-hidden="true">›</span><span>${esc(name)}</span></div>
    <div data-sk-body>${skelRows(4)}</div></div>`;
  const body = $('#skills-root [data-sk-body]');
  let d: SkillDetail, usage: SkillUsage;
  try {
    [d, usage] = await Promise.all([get<SkillDetail>('/v2/skills/' + encodeURIComponent(name), {fresh: true}),
      get<SkillUsage>(`/v2/skills/${encodeURIComponent(name)}/usage?days=${page.days}`, {fresh: true})]);
  } catch (e) { if (skillsLive(run)) body.innerHTML = skillsErr(e); return; }
  if (!skillsLive(run)) return;
  const versions = d.versions || [];
  body.innerHTML = `<div class="tt-title"><div class="tt-title-text"><h1>${esc(d.skill.name)}</h1><p class="muted">${esc(d.skill.description || '')}</p></div>
      <div class="tt-title-act">${d.skill.latest ? `<button type="button" class="primary small" data-sk-install>Install</button>` : ''}</div></div>
    <div class="tt-cols"><div class="tt-col">
      <section class="card" id="skill-usage"><header><h2>Usage</h2><span class="spacer"></span>
        <div class="theme-seg" role="radiogroup" aria-label="Days">${[7, 30].map(n =>
          `<button type="button" role="radio" data-sk-days="${n}" aria-checked="${page.days === n}">${n}d</button>`).join('')}</div></header>
        <div data-sk-usage>${skillsUsageHTML(usage)}</div></section>
      <section class="card" id="skill-files"><header><h2>Files</h2><span class="spacer"></span>
        ${versions.length ? `<select data-sk-version aria-label="Version">${versions.map(v => `<option value="${v.number}">v${v.number}</option>`).join('')}</select>` : ''}</header>
        <div data-sk-files>${versions.length ? skelRows(2) : '<p class="muted">No version yet.</p>'}</div></section>
    </div><div class="tt-col">
      <section class="card" id="skill-versions"><header><h2>Versions</h2></header>
        ${versions.length ? `<table class="tt-who-table">${versions.map(v => `<tr data-sk-v="${v.number}"><td class="tt-who-name"><strong>v${v.number}</strong> ${skillsSha(v.sha256)}</td>
          <td class="tt-used muted">${v.approved_at ? agoSpan(v.approved_at) : ''}${v.approved_by_id ? ' · ' + esc(actorLabel('human:' + v.approved_by_id)) : ''}</td></tr>`).join('')}</table>`
          : '<p class="muted">None yet.</p>'}</section>
      <section class="card" id="skill-installs"><header><h2>Installs</h2></header><div data-sk-installs>${skillsCoverageHTML(usage.installs || [])}</div></section>
    </div></div>`;
  body.querySelector('[data-sk-install]')?.addEventListener('click', () => skillsInstallDialog(d.skill, versions));
  skillsBindRemove(body);
  body.querySelector('#skill-usage .theme-seg').onclick = async (ev: MouseEvent) => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-sk-days]'); if (!b) return;
    if (!SKP) return;
    SKP.days = Number(b.dataset.skDays);
    body.querySelectorAll('[data-sk-days]').forEach((x: Element) => x.setAttribute('aria-checked', String(x === b)));
    try {
      const u = await get<SkillUsage>(`/v2/skills/${encodeURIComponent(name)}/usage?days=${SKP.days}`, {fresh: true});
      if (skillsLive(run)) body.querySelector('[data-sk-usage]').innerHTML = skillsUsageHTML(u);
    } catch (e) { toast(errorMessage(e, ''), true); }
  };
  const pick = body.querySelector('[data-sk-version]');
  if (pick) { pick.onchange = () => skillsFiles(name, Number(pick.value), run); void skillsFiles(name, versions[0]?.number, run); }
}
function skillsUsageHTML(u: SkillUsage) {
  const t: SkillUsageTotals = u.totals || {}, by = t.by_trigger || {};
  const actors = (u.actors || []).slice().sort((a, b) => b.uses - a.uses);
  const recent = u.recent || [];
  const link = (r: SkillUsageRecent) => r.task_id ? `<a href="#/task/${encodeURIComponent(r.task_id)}">Task</a>`
    : r.thread_id ? `<a href="${threadHref(r.thread_id)}">Thread</a>` : '';
  return `<div class="sk-stats"><div class="sk-stat"><strong class="tnum" data-sk-total>${skillsN(t.uses)}</strong><span class="muted">Uses</span></div>
      ${SKILLS_TRIGGERS.map(([k, label]) => `<div class="sk-stat"><strong class="tnum" data-sk-trigger="${k}">${skillsN(by[k])}</strong><span class="muted">${label}</span></div>`).join('')}
      <div class="sk-stat"><strong class="tnum">${skillsN(t.people)}</strong><span class="muted">People</span></div>
      <div class="sk-stat"><strong class="tnum">${skillsN(t.bots)}</strong><span class="muted">Bots</span></div></div>
    ${actors.length ? `<h3 class="sk-sub">${u.admin ? 'By who' : 'Mine'}</h3><table class="tt-who-table" data-sk-actors>${actors.map(a => `<tr data-sk-actor="${esc(a.id)}">
      <td class="tt-who-name">${esc(skillsWho(a))}</td><td class="tnum tt-used" data-sk-actor-uses>${skillsUses(a.uses)}</td>
      <td class="tt-used muted">${a.last_used_at ? agoSpan(a.last_used_at) : ''}</td></tr>`).join('')}</table>` : ''}
    ${recent.length ? `<h3 class="sk-sub">Recent</h3><table class="tt-log tt-log-short" data-sk-recent><tbody>${recent.map(r => `<tr>
      <td class="tt-log-what"><strong>${esc(skillsWho(r.actor))}</strong>${r.requested_by ? `<span class="muted">for ${esc(skillsWho(r.requested_by))}</span>` : ''}</td>
      <td><span class="pill">${esc((SKILLS_TRIGGERS.find(([k]) => k === r.trigger) || [, r.trigger])[1])}</span></td>
      <td class="muted">${agoSpan(r.used_at)}</td><td>${link(r)}</td></tr>`).join('')}</tbody></table>`
      : '<p class="muted" data-sk-no-uses>No uses.</p>'}`;
}
function skillsCoverageHTML(installs: SkillInstallCoverage[]) {
  if (!installs.length) return '<p class="muted">Not installed.</p>';
  const pill = (c: string) => c === 'used' ? '<span class="pill ok">Used</span>' : c === 'unused' ? '<span class="pill waiting">Unused</span>' : '<span class="pill">Unknown</span>';
  return `<table class="tt-who-table">${installs.map(i => `<tr data-sk-coverage="${esc(i.coverage)}"${i.id ? ` data-sk-install-row="${esc(i.id)}"` : ''}>
    <td class="tt-who-name">${esc(skillsWho(i.target))}${i.pinned_version ? ` <span class="pill" title="Pinned" data-sk-pinned>v${i.pinned_version}</span>` : ''}</td>
    <td class="tnum tt-used">${skillsUses(i.uses_30d)}</td><td>${pill(i.coverage)}</td>
    <td class="sk-act">${i.removable && i.id ? `<button type="button" class="ghost small" data-sk-remove="${esc(i.id)}" data-sk-remove-name="${esc(skillsWho(i.target))}">Remove</button>` : ''}</td></tr>`).join('')}</table>`;
}
// Remove an install from the skill's page: the same rights as installing it, which the server says per row (`removable`).
function skillsBindRemove(box: HTMLElement) {
  box.querySelectorAll<HTMLButtonElement>('[data-sk-remove]').forEach(b => { b.onclick = async () => {
    const ok = await settingsConfirm({title: 'Remove from ' + b.dataset.skRemoveName, text: 'It leaves on the next session.', ok: 'Remove'});
    if (!ok) return;
    try { await writeRequest('DELETE', '/v2/skills/installs/' + encodeURIComponent(String(b.dataset.skRemove))); toast('Removed'); skillsRedraw(); }
    catch (e) { toast(errorMessage(e, ''), true); }
  }; });
}
async function skillsFiles(name: string, number: number | undefined, run: number) {
  const box = $('#skills-root [data-sk-files]'); if (!box) return;
  try {
    const {version} = await get<{version: {files?: SkillFileRow[]}}>(`/v2/skills/${encodeURIComponent(name)}/versions/${number}`);
    if (!skillsLive(run)) return;
    skillsFilesInto(box, version.files || []);
  } catch (e) { if (skillsLive(run)) box.innerHTML = skillsErr(e); }
}
// A file list and the chosen file's text (SKILL.md first).
function skillsFilesInto(box: HTMLElement, files: SkillFileRow[]) {
  const sorted = files.slice().sort((a, b) => Number(a.path !== 'SKILL.md') - Number(b.path !== 'SKILL.md') || a.path.localeCompare(b.path));
  if (!sorted.length) { box.innerHTML = '<p class="muted">No files.</p>'; return; }
  box.innerHTML = `<div class="sk-paths" role="tablist">${sorted.map((f, i) =>
    `<button type="button" role="tab" data-sk-path="${i}" aria-selected="${i === 0}">${esc(f.path)}</button>`).join('')}</div>
    <pre class="sk-file" data-sk-file>${esc(sorted[0]?.content)}</pre>`;
  const paths = box.querySelector<HTMLElement>('.sk-paths'); if (!paths) return;
  paths.onclick = ev => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-sk-path]'); if (!b) return;
    box.querySelectorAll('[data-sk-path]').forEach(x => x.setAttribute('aria-selected', String(x === b)));
    const file = box.querySelector<HTMLElement>('[data-sk-file]'); if (file) file.textContent = sorted[Number(b.dataset.skPath)]?.content ?? '';
  };
}

// Install: for me, one bot, one person (admins) or a team's current members (all at once), latest or pinned.
async function skillsInstallDialog(skill: SkillInfo, versions: SkillVersionRow[]) {
  let t: SkillInstallTargets;
  try { t = await get<SkillInstallTargets>('/v2/skills/install-targets', {fresh: true}); } catch (e) { toast(errorMessage(e, ''), true); return; }
  const opts: [string, string][] = [];
  if (t.me?.type === 'Human') opts.push(['me', 'Me']);
  const bots = (t.bots || []).map((b): [string, string] => [`Bot:${b.id}`, b.name]);
  const people = (t.people || []).filter(p => p.id !== t.me?.id).map((p): [string, string] => [`Human:${p.id}`, p.name]);
  const teams = (t.teams || []).map((g): [string, string] => [`team:${g.id}`, `${g.name} (${g.targets.length})`]);
  const group = (label: string, list: [string, string][]) => list.length ? `<optgroup label="${label}">${list.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('')}</optgroup>` : '';
  const dialog = skillsDialog('Install ' + skill.name, `<label class="sk-field">For
      <select data-sk-target>${opts.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}${group('Bots', bots)}${group('People', people)}${group('Teams', teams)}</select></label>
    <label class="sk-field">Version
      <select data-sk-policy><option value="latest">Latest</option>${versions.map(v => `<option value="${v.number}">Pinned v${v.number}</option>`).join('')}</select></label>`,
  '<button type="button" class="primary" data-sk-install-go>Install</button>', {cancel: 'Cancel'});
  const go = dialog.querySelector<HTMLButtonElement>('[data-sk-install-go]');
  if (go) go.onclick = async () => {
    const target = dialog.querySelector<HTMLSelectElement>('[data-sk-target]')?.value ?? '', policy = dialog.querySelector<HTMLSelectElement>('[data-sk-policy]')?.value ?? '';
    if (!target) return;
    const version = policy === 'latest' ? {version_policy: 'latest'} : {version_policy: 'pinned', pinned_version: Number(policy)};
    const base = '/v2/skills/' + encodeURIComponent(skill.name) + '/installs';
    try {
      if (target.startsWith('team:')) {
        const team = t.teams.find(g => 'team:' + g.id === target); if (!team) return;
        const r = await post(base + '/bulk', {targets: team.targets, ...version});
        toast(`Installed for ${(r.installs || []).length}`);
      } else {
        const [type, id] = target === 'me' ? ['Human', t.me?.id] : [target.slice(0, target.indexOf(':')), target.slice(target.indexOf(':') + 1)];
        await post(base, {target_type: type, target_id: id, ...version});
        toast('Installed');
      }
      dialog.close();
      skillsRedraw();
    } catch (e) { skillsDialogError(dialog, e); }
  };
}

// ----------------------------------------------------------------- one proposal
async function pageSkillProposal(id: string) {
  const run = (SKP?.run || 0) + 1;
  SKP = {run};
  $('#main').innerHTML = `<div class="tt sk" id="skills-root" data-sk-page="proposal">
    <div class="tt-crumb" data-sk-crumb><a href="${SKILLS_ROUTE}">Skills</a></div>
    <div data-sk-body>${skelRows(4)}</div></div>`;
  const body = $('#skills-root [data-sk-body]');
  let d: SkillProposalDetail, admin = false;
  try {
    // Whether the caller approves: the usage read says (admin), so the page never guesses the rule.
    const [detail, usage] = await Promise.all([get<SkillProposalDetail>('/v2/skills/proposals/' + encodeURIComponent(id), {fresh: true}),
      get<SkillUsage>('/v2/skills/usage?days=7').catch((): SkillUsage => ({}))]);
    d = detail; admin = !!usage.admin;
  } catch (e) { if (skillsLive(run)) body.innerHTML = skillsErr(e); return; }
  if (!skillsLive(run)) return;
  const p = d.proposal, s = d.screening, pending = p.status === 'pending';
  $('#skills-root [data-sk-crumb]')?.insertAdjacentHTML('beforeend', `<span aria-hidden="true">›</span><span>${esc(p.name)}</span>`);
  const mine = p.proposed_by?.id === S.me?.id && p.proposed_by?.type === 'Human';
  const ack = !!s && (s.risk === 'high' || s.verdict === 'unsafe');
  const canApprove = admin && pending && p.screen_status === 'screened' && !d.stale;
  body.innerHTML = `<div class="tt-title"><div class="tt-title-text"><h1>${esc(p.name)}</h1>
      <p class="muted">${p.base_version ? `Update to v${p.base_version + 1}` : 'New'} · ${esc(skillsWho(p.proposed_by))} · ${agoSpan(p.created_at)}</p></div>
      <div class="tt-title-act">${skillsStatusPill(p.status)}${d.stale && pending ? '<span class="pill fail" data-sk-stale>Stale</span>' : ''}</div></div>
    ${p.decline_reason ? `<p class="muted" data-sk-decline-reason>${esc(p.decline_reason)}</p>` : ''}
    <section class="card" id="proposal-screening"><header><h2>Screening</h2><span class="spacer"></span>${skillsScreenPill(p, s)}</header>
      ${skillsScreeningHTML(p, s, pending)}</section>
    ${pending && (admin || mine) ? `<div class="sk-decide" data-sk-decide>
      ${admin && ack ? `<label class="sk-ack"><input type="checkbox" data-sk-ack> I've read this</label>` : ''}
      ${admin ? `<button type="button" class="primary small" data-sk-approve${canApprove && !ack ? '' : ' disabled'}>Approve</button>
        <button type="button" class="ghost small" data-sk-decline>Decline</button>` : ''}
      ${mine ? '<button type="button" class="ghost small" data-sk-withdraw>Withdraw</button>' : ''}</div>` : ''}
    <section class="card" id="proposal-changes"><header><h2>${p.base_version ? 'Changes' : 'Files'}</h2></header>
      <div data-sk-changes></div>
      ${d.stale && d.diff_latest != null && p.base_version ? `<h3 class="sk-sub">Against latest</h3>${skillsDiffHTML(d.diff_latest)}` : ''}</section>`;
  const changes = body.querySelector('[data-sk-changes]');
  if (p.base_version) changes.innerHTML = skillsDiffHTML(d.diff);
  else skillsFilesInto(changes, d.files || []);
  const approve = body.querySelector('[data-sk-approve]'), box = body.querySelector('[data-sk-ack]');
  if (box && approve) box.onchange = () => { approve.disabled = !(canApprove && box.checked); };
  body.querySelector('[data-sk-rescreen]')?.addEventListener('click', () => skillsRescreen(id));
  approve?.addEventListener('click', async () => {
    try {
      const r = await post(`/v2/skills/proposals/${encodeURIComponent(id)}/approve`, {acknowledged: !!box?.checked});
      toast(r.version ? `Approved v${r.version.number}` : 'Approved');
      skillsRedraw();
    } catch (e) {
      // The server asks for the tick too (422 acknowledgement_required): show it and wait for it.
      if (errorCode(e) === 'acknowledgement_required' && box) { box.focus(); approve.disabled = true; }
      toast(errorMessage(e, ''), true);
    }
  });
  body.querySelector('[data-sk-decline]')?.addEventListener('click', () => {
    const dialog = skillsDialog('Decline ' + p.name, '<label class="sk-field">Reason<input type="text" maxlength="300" required data-sk-reason></label>',
      '<button type="button" class="primary" data-sk-decline-go>Decline</button>', {cancel: 'Cancel'});
    const input = dialog.querySelector<HTMLInputElement>('[data-sk-reason]'); if (!input) return;
    input.focus();
    const go = dialog.querySelector<HTMLButtonElement>('[data-sk-decline-go]');
    if (go) go.onclick = async () => {
      const reason = input.value.trim(); if (!reason) { input.focus(); return; }
      try { await post(`/v2/skills/proposals/${encodeURIComponent(id)}/decline`, {reason}); dialog.close(); toast('Declined'); skillsRedraw(); }
      catch (e) { skillsDialogError(dialog, e); }
    };
  });
  body.querySelector('[data-sk-withdraw]')?.addEventListener('click', async () => {
    try { await post(`/v2/skills/proposals/${encodeURIComponent(id)}/withdraw`, {}); toast('Withdrawn'); skillsRedraw(); }
    catch (e) { toast(errorMessage(e, ''), true); }
  });
}
function skillsScreeningHTML(p: SkillProposal, s: SkillScreening | null | undefined, pending: boolean) {
  const rerun = pending ? ' <button type="button" class="ghost small" data-sk-rescreen>Re-run</button>' : '';
  if (p.screen_status === 'pending') return '<p class="muted">Screening runs before anyone can approve.</p>';
  if (p.screen_status === 'not_screened' || !s) return `<p class="muted" data-sk-screen-reason>${esc(s?.reason || 'Not screened.')}${rerun}</p>`;
  const sev = (v?: string) => `<span class="pill ${v === 'high' ? 'fail' : v === 'medium' ? 'waiting' : ''}">${esc(v || '')}</span>`;
  const where = (x: {file?: string; line?: number | string}) => `<code>${esc(x.file || '')}${x.line ? ':' + esc(x.line) : ''}</code>`;
  const findings = s.findings || [], patterns = s.patterns || [];
  return `<p data-sk-summary>${esc(s.summary || '')}</p>
    <dl class="tt-kv"><dt>Verdict</dt><dd data-sk-verdict>${esc(s.verdict || '')}</dd><dt>Risk</dt><dd data-sk-risk>${esc(s.risk || '')}</dd></dl>
    ${findings.length ? `<h3 class="sk-sub">Findings</h3><table class="tt-log tt-log-short" data-sk-findings><tbody>${findings.map(f =>
      `<tr><td>${where(f)}</td><td>${sev(f.severity)}</td><td class="tt-log-what">${esc(f.issue || f.detail || '')}</td></tr>`).join('')}</tbody></table>` : ''}
    ${patterns.length ? `<h3 class="sk-sub">Pattern hits</h3><table class="tt-log tt-log-short" data-sk-patterns><tbody>${patterns.map(x =>
      `<tr><td>${where(x)}</td><td class="tt-log-what"><code>${esc(x.pattern || '')}</code></td></tr>`).join('')}</tbody></table>` : ''}`;
}
function skillsDiffHTML(diff: string | null | undefined) {
  if (!diff) return '<p class="muted">No changes.</p>';
  const cls = (l: string) => l.startsWith('+++') || l.startsWith('---') ? 'file' : l.startsWith('@@') ? 'hunk' : l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : '';
  return `<pre class="sk-diff" data-sk-diff>${diff.split('\n').map(l => `<span${cls(l) ? ` class="${cls(l)}"` : ''}>${esc(l)}</span>`).join('')}</pre>`;
}

// ----------------------------------------------------------------- routes
function skillsRedraw() { skillsRoute(S.route || ''); }
// The redesign's router asks first (ui/app/redesign.ts).
function skillsRoute(r: string) {
  const [path = ''] = r.split('?');
  if (!rdUnder(path, SKILLS_ROUTE)) return false;
  const parts = path.slice(SKILLS_ROUTE.length).split('/').filter(Boolean).map(x => { try { return decodeURIComponent(x); } catch { return x; } });
  if (parts[0] === 'proposals' && parts[1]) { void pageSkillProposal(parts[1]); return true; }
  if (parts.length) { void pageSkill(parts.join('/')); return true; }
  void pageSkills();
  return true;
}
// Leaving the page drops its state, so a late answer draws nothing.
function skillsStop(r: string) { if (!rdUnder((r || '').split('?')[0] ?? '', SKILLS_ROUTE)) SKP = null; }
