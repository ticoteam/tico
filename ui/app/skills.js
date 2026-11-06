/* ui/app/skills.js — Library › Skills (#/library/skills): the company's approved skills, the proposals waiting on an
   admin, and what is installed for me and the bots I can write. Backend: backend/skills.py (directory, versions,
   proposals, installs) and backend/skill_usage.py (usage, install targets, my installs); docs/skills.md.
   Addresses:
     #/library/skills[?tab=proposals|installed]    the three tabs (Directory is the default)
     #/library/skills/<name>                       one skill: versions, files, Install, usage
     #/library/skills/proposals/<id>               one proposal: screening, diff, Approve / Decline
   The server decides who sees what (admins see who used a skill; anyone else totals and their own rows); this page
   only draws it. Classic script: its globals are shared with the other files under ui/app/. */
'use strict';

const SKILLS_ROUTE = '#/library/skills';
const SKILLS_TABS = [['', 'Directory'], ['proposals', 'Proposals'], ['installed', 'Installed']];
const SKILLS_TRIGGERS = [['model', 'Model'], ['person', 'Person'], ['nested', 'Nested']];
const SKILLS_SCREEN_CAP = 50;   // proposal rows whose screening risk is read for the list
let SKP = null;                 // {run}: the page on screen, so a slow answer for a page left behind draws nothing

const skillsHref = name => SKILLS_ROUTE + '/' + encodeURIComponent(name);
const skillsProposalHref = id => SKILLS_ROUTE + '/proposals/' + encodeURIComponent(id);
const skillsSha = sha => sha ? `<code class="sk-sha" title="${esc(sha)}">${esc(sha.slice(0, 7))}</code>` : '';
// A bot or person as the server names them; a row without a name falls back to the roster's.
const skillsWho = a => a?.type !== 'Bot' && a?.id === S.me?.id ? 'You'
  : a?.name || (a?.type === 'Bot' ? botDisplayName(a.id) : personDisplay(a?.id || ''));
const skillsErr = e => `<p class="err" data-sk-error>${esc(e?.message || String(e))}</p>`;
const skillsLive = run => SKP && SKP.run === run;
const skillsN = n => Number(n || 0).toLocaleString();
function skillsScreenPill(p, screen) {
  if (p.screen_status === 'screened') {
    const risk = screen?.risk || '', bad = risk === 'high' || screen?.verdict === 'unsafe';
    return `<span class="pill ${bad ? 'fail' : risk === 'medium' ? 'waiting' : 'ok'}" data-sk-screen="screened">Screened${risk ? ' · ' + esc(risk) : ''}</span>`;
  }
  if (p.screen_status === 'not_screened') return '<span class="pill fail" data-sk-screen="not_screened">Not screened</span>';
  return '<span class="pill waiting" data-sk-screen="pending">Pending</span>';
}
const skillsStatusPill = s => s === 'pending' ? '' : `<span class="pill${s === 'approved' ? ' ok' : ''}" data-sk-status="${esc(s)}">${esc(s[0].toUpperCase() + s.slice(1))}</span>`;

// ----------------------------------------------------------------- the frame: title, Claude Code, tabs
function skillsHead(tab) {
  return `<header class="tt-head"><h1>Skills</h1><span class="spacer"></span>
      <button type="button" class="ghost small" data-sk-helper><span class="nav-icon" aria-hidden="true">content_copy</span>Use in my Claude Code</button></header>
    <nav class="tt-tabs sk-tabs" aria-label="Skills">${SKILLS_TABS.map(([k, label]) =>
      `<a href="${SKILLS_ROUTE}${k ? '?tab=' + k : ''}" data-sk-tab="${k || 'directory'}"${tab === k ? ' class="cur" aria-current="page"' : ''}>${label}</a>`).join('')}</nav>`;
}
function skillsBindHead(root) {
  root.querySelector('[data-sk-helper]')?.addEventListener('click', skillsHelperDialog);
}
// The one line that sets up the skills helper on this computer (clients/skill_helper.mjs, served by the hub).
const skillsHelperCommand = () => `curl -fsSL --create-dirs ${publicUrl().replace(/\/+$/, '')}/api/v2/skills/helper.mjs -o ~/.tico/skills-helper.mjs && node ~/.tico/skills-helper.mjs setup`;
function skillsHelperDialog() {
  const command = skillsHelperCommand();
  const dialog = skillsDialog('Use in my Claude Code', `<pre class="sk-cmd" data-sk-command>${esc(command)}</pre>`,
    '<button type="button" class="primary" data-sk-copy>Copy</button>');
  dialog.querySelector('[data-sk-copy]').onclick = async () => {
    try { await copyText(command); toast('Copied'); } catch (e) { toast(e.message, true); }
  };
}
// A modal in the house style; `actions` is the buttons after Cancel. Returns the open <dialog>.
function skillsDialog(title, body, actions, {cancel = 'Close'} = {}) {
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal sk-dialog'; dialog.setAttribute('aria-label', title);
  dialog.innerHTML = `<form method="dialog"><div class="tmodal-title">${esc(title)}</div><div class="tmodal-body">${body}
    <p class="err" data-sk-dialog-error hidden></p>
    <div class="onb-actions"><button type="submit" class="ghost" value="cancel" data-sk-cancel>${esc(cancel)}</button><span class="spacer"></span>${actions}</div></div></form>`;
  dialog.addEventListener('close', () => dialog.remove());
  // Enter in a field presses the dialog's own button, not Close (the form's first submit button).
  dialog.addEventListener('keydown', ev => {
    if (ev.key !== 'Enter' || !ev.target.matches('input')) return;
    ev.preventDefault();
    dialog.querySelector('.onb-actions .primary')?.click();
  });
  document.body.appendChild(dialog); dialog.showModal();
  return dialog;
}
const skillsDialogError = (dialog, e) => { const p = dialog.querySelector('[data-sk-dialog-error]'); p.textContent = e?.message || String(e); p.hidden = false; };

// ----------------------------------------------------------------- the tabs
async function pageSkills(tab) {
  const run = (SKP?.run || 0) + 1;
  SKP = {run};
  $('#main').innerHTML = `<div class="tt sk" id="skills-root" data-sk-page="${tab || 'directory'}">${skillsHead(tab)}
    <div data-sk-body>${skelRows(5)}</div></div>`;
  const root = $('#skills-root');
  skillsBindHead(root);
  const body = root.querySelector('[data-sk-body]');
  try {
    if (tab === 'proposals') await skillsProposals(body, run);
    else if (tab === 'installed') await skillsInstalled(body, run);
    else await skillsDirectory(body, run);
  } catch (e) { if (skillsLive(run)) body.innerHTML = skillsErr(e); }
}

async function skillsDirectory(body, run) {
  const [list, usage] = await Promise.all([get('/v2/skills', {fresh: true}), get('/v2/skills/usage?days=30', {fresh: true})]);
  if (!skillsLive(run)) return;
  const used = Object.fromEntries((usage.skills || []).map(u => [u.name, u]));
  const rows = list.skills || [];
  if (!rows.length) { body.innerHTML = '<div class="empty" data-sk-empty>No skills yet.</div>'; return; }
  body.innerHTML = `<table class="tt-list sk-list"><thead><tr><th>Skill</th><th>Version</th><th>Installs</th><th>Uses 30d</th><th>Last used</th></tr></thead>
    <tbody>${rows.map(s => { const u = used[s.name] || {}; return `<tr data-sk-row="${esc(s.name)}">
      <td class="sk-name"><a href="${skillsHref(s.name)}"><strong>${esc(s.name)}</strong><span class="muted sk-desc">${esc(s.description || '')}</span></a></td>
      <td class="sk-ver tnum">${s.latest ? 'v' + s.latest.number : ''}</td>
      <td class="sk-num tnum" data-label="Installs">${skillsN(s.installs_count)}</td>
      <td class="sk-num tnum" data-label="Uses 30d" data-sk-uses>${skillsN(u.uses)}</td>
      <td class="sk-when">${u.last_used_at ? agoSpan(u.last_used_at) : '<span class="muted">Never</span>'}</td></tr>`; }).join('')}</tbody></table>`;
  skillsRowLinks(body);
}
// A table's whole row opens its first link.
function skillsRowLinks(box) {
  const tbody = box.querySelector('tbody'); if (!tbody) return;
  tbody.onclick = ev => {
    if (ev.target.closest('a, button, input, select')) return;
    const href = ev.target.closest('tr')?.querySelector('a[href]')?.getAttribute('href');
    if (href) location.hash = href;
  };
}

async function skillsProposals(body, run) {
  const {proposals = []} = await get('/v2/skills/proposals', {fresh: true});
  if (!skillsLive(run)) return;
  if (!proposals.length) { body.innerHTML = '<div class="empty" data-sk-empty>No proposals.</div>'; return; }
  // The list has no screening risk: read it for the screened rows (the newest first, capped).
  const screens = {};
  await Promise.all(proposals.filter(p => p.screen_status === 'screened').slice(0, SKILLS_SCREEN_CAP).map(p =>
    get('/v2/skills/proposals/' + encodeURIComponent(p.id)).then(d => { screens[p.id] = d.screening; }, () => {})));
  if (!skillsLive(run)) return;
  body.innerHTML = `<table class="tt-list sk-list"><thead><tr><th>Skill</th><th>Kind</th><th>Proposed by</th><th>Screen</th><th>When</th></tr></thead>
    <tbody>${proposals.map(p => `<tr data-sk-proposal="${esc(p.id)}" data-sk-status="${esc(p.status)}">
      <td class="sk-name"><a href="${skillsProposalHref(p.id)}"><strong>${esc(p.name)}</strong></a> ${skillsStatusPill(p.status)}</td>
      <td class="sk-kind">${p.base_version ? 'Update' : 'New'}</td>
      <td class="sk-by">${esc(skillsWho(p.proposed_by))}</td>
      <td class="sk-screen">${skillsScreenPill(p, screens[p.id])}${p.screen_status === 'not_screened' && p.status === 'pending'
        ? ` <button type="button" class="ghost small" data-sk-rescreen="${esc(p.id)}">Re-run</button>` : ''}</td>
      <td class="sk-when">${agoSpan(p.created_at)}</td></tr>`).join('')}</tbody></table>`;
  skillsRowLinks(body);
  body.querySelectorAll('[data-sk-rescreen]').forEach(b => { b.onclick = () => skillsRescreen(b.dataset.skRescreen); });
}
async function skillsRescreen(id) {
  try { await post(`/v2/skills/proposals/${encodeURIComponent(id)}/rescreen`, {}); toast('Screening'); skillsRedraw(); }
  catch (e) { toast(e.message, true); }
}

async function skillsInstalled(body, run) {
  const {installs = []} = await get('/v2/skills/installed', {fresh: true});
  if (!skillsLive(run)) return;
  if (!installs.length) { body.innerHTML = '<div class="empty" data-sk-empty>Nothing installed.</div>'; return; }
  body.innerHTML = `<table class="tt-list sk-list sk-installed"><thead><tr><th>Skill</th><th>For</th><th>Version</th><th>Desired</th><th>Staged</th><th aria-hidden="true"></th></tr></thead>
    <tbody>${installs.map(i => {
      const staged = i.staged?.sha256, desired = i.desired?.sha256, behind = desired && staged && staged !== desired;
      return `<tr data-sk-install="${esc(i.id)}">
      <td class="sk-name"><a href="${skillsHref(i.name)}"><strong>${esc(i.name)}</strong></a></td>
      <td class="sk-by">${esc(skillsWho(i.target))}</td>
      <td class="sk-ver">${i.version_policy === 'pinned' ? `Pinned v${i.pinned_version}` : 'Latest'}${i.update_available
        ? ` <span class="pill waiting" data-sk-update>v${i.latest} out</span>` : ''}</td>
      <td class="sk-sha-cell">${i.desired ? `v${i.desired.number} ${skillsSha(desired)}` : ''}</td>
      <td class="sk-sha-cell">${staged ? skillsSha(staged) + (behind ? ' <span class="pill waiting">Behind</span>' : '') : '<span class="muted">Not yet</span>'}</td>
      <td class="sk-act"><button type="button" class="ghost small" data-sk-remove="${esc(i.id)}" data-sk-remove-name="${esc(i.name)}">Remove</button></td></tr>`; }).join('')}</tbody></table>`;
  skillsRowLinks(body);
  body.querySelectorAll('[data-sk-remove]').forEach(b => { b.onclick = async () => {
    const ok = await settingsConfirm({title: 'Remove ' + b.dataset.skRemoveName, text: 'It leaves on the next session.', ok: 'Remove'});
    if (!ok) return;
    try { await writeRequest('DELETE', '/v2/skills/installs/' + encodeURIComponent(b.dataset.skRemove)); toast('Removed'); skillsRedraw(); }
    catch (e) { toast(e.message, true); }
  }; });
}

// ----------------------------------------------------------------- one skill
async function pageSkill(name) {
  const run = (SKP?.run || 0) + 1;
  SKP = {run, name, days: SKP?.name === name ? SKP.days : 30};
  $('#main').innerHTML = `<div class="tt sk" id="skills-root" data-sk-page="skill">
    <div class="tt-crumb"><a href="${SKILLS_ROUTE}">Skills</a><span aria-hidden="true">›</span><span>${esc(name)}</span></div>
    <div data-sk-body>${skelRows(4)}</div></div>`;
  const body = $('#skills-root [data-sk-body]');
  let d, usage;
  try {
    [d, usage] = await Promise.all([get('/v2/skills/' + encodeURIComponent(name), {fresh: true}),
      get(`/v2/skills/${encodeURIComponent(name)}/usage?days=${SKP.days}`, {fresh: true})]);
  } catch (e) { if (skillsLive(run)) body.innerHTML = skillsErr(e); return; }
  if (!skillsLive(run)) return;
  const versions = d.versions || [];
  body.innerHTML = `<div class="tt-title"><div class="tt-title-text"><h1>${esc(d.skill.name)}</h1><p class="muted">${esc(d.skill.description || '')}</p></div>
      <div class="tt-title-act">${d.skill.latest ? `<button type="button" class="primary small" data-sk-install>Install</button>` : ''}</div></div>
    <div class="tt-cols"><div class="tt-col">
      <section class="card" id="skill-usage"><header><h2>Usage</h2><span class="spacer"></span>
        <div class="theme-seg" role="radiogroup" aria-label="Days">${[7, 30].map(n =>
          `<button type="button" role="radio" data-sk-days="${n}" aria-checked="${SKP.days === n}">${n}d</button>`).join('')}</div></header>
        <div data-sk-usage>${skillsUsageHTML(usage)}</div></section>
      <section class="card" id="skill-files"><header><h2>Files</h2><span class="spacer"></span>
        ${versions.length ? `<select data-sk-version aria-label="Version">${versions.map(v => `<option value="${v.number}">v${v.number}</option>`).join('')}</select>` : ''}</header>
        <div data-sk-files>${versions.length ? skelRows(2) : '<p class="muted">No version yet.</p>'}</div></section>
    </div><div class="tt-col">
      <section class="card" id="skill-versions"><header><h2>Versions</h2></header>
        ${versions.length ? `<table class="tt-who-table">${versions.map(v => `<tr data-sk-v="${v.number}"><td class="tt-who-name"><strong>v${v.number}</strong> ${skillsSha(v.sha256)}</td>
          <td class="tt-used muted">${v.approved_at ? agoSpan(v.approved_at) : ''}${v.approved_by_id ? ' · ' + esc(actorLabel('human:' + v.approved_by_id)) : ''}</td></tr>`).join('')}</table>`
          : '<p class="muted">None yet.</p>'}</section>
      <section class="card" id="skill-installs"><header><h2>Installs</h2></header>${skillsCoverageHTML(usage.installs || [])}</section>
    </div></div>`;
  body.querySelector('[data-sk-install]')?.addEventListener('click', () => skillsInstallDialog(d.skill, versions));
  body.querySelector('#skill-usage .theme-seg').onclick = async ev => {
    const b = ev.target.closest('[data-sk-days]'); if (!b) return;
    SKP.days = Number(b.dataset.skDays);
    body.querySelectorAll('[data-sk-days]').forEach(x => x.setAttribute('aria-checked', String(x === b)));
    try {
      const u = await get(`/v2/skills/${encodeURIComponent(name)}/usage?days=${SKP.days}`, {fresh: true});
      if (skillsLive(run)) body.querySelector('[data-sk-usage]').innerHTML = skillsUsageHTML(u);
    } catch (e) { toast(e.message, true); }
  };
  const pick = body.querySelector('[data-sk-version]');
  if (pick) { pick.onchange = () => skillsFiles(name, Number(pick.value), run); void skillsFiles(name, versions[0].number, run); }
}
function skillsUsageHTML(u) {
  const t = u.totals || {}, by = t.by_trigger || {};
  const actors = (u.actors || []).slice().sort((a, b) => b.uses - a.uses);
  const recent = u.recent || [];
  const link = r => r.task_id ? `<a href="#/task/${encodeURIComponent(r.task_id)}">Task</a>`
    : r.thread_id ? `<a href="${threadHref(r.thread_id)}">Thread</a>` : '';
  return `<div class="sk-stats"><div class="sk-stat"><strong class="tnum" data-sk-total>${skillsN(t.uses)}</strong><span class="muted">Uses</span></div>
      ${SKILLS_TRIGGERS.map(([k, label]) => `<div class="sk-stat"><strong class="tnum" data-sk-trigger="${k}">${skillsN(by[k])}</strong><span class="muted">${label}</span></div>`).join('')}
      <div class="sk-stat"><strong class="tnum">${skillsN(t.people)}</strong><span class="muted">People</span></div>
      <div class="sk-stat"><strong class="tnum">${skillsN(t.bots)}</strong><span class="muted">Bots</span></div></div>
    ${actors.length ? `<h3 class="sk-sub">${u.admin ? 'By who' : 'Mine'}</h3><table class="tt-who-table" data-sk-actors>${actors.map(a => `<tr data-sk-actor="${esc(a.id)}">
      <td class="tt-who-name">${esc(skillsWho(a))}</td><td class="tnum">${skillsN(a.uses)}</td>
      <td class="tt-used muted">${a.last_used_at ? agoSpan(a.last_used_at) : ''}</td></tr>`).join('')}</table>` : ''}
    ${recent.length ? `<h3 class="sk-sub">Recent</h3><table class="tt-log tt-log-short" data-sk-recent><tbody>${recent.map(r => `<tr>
      <td class="tt-log-what"><strong>${esc(skillsWho(r.actor))}</strong>${r.requested_by ? `<span class="muted">for ${esc(skillsWho(r.requested_by))}</span>` : ''}</td>
      <td><span class="pill">${esc((SKILLS_TRIGGERS.find(([k]) => k === r.trigger) || [, r.trigger])[1])}</span></td>
      <td class="muted">${agoSpan(r.used_at)}</td><td>${link(r)}</td></tr>`).join('')}</tbody></table>`
      : '<p class="muted" data-sk-no-uses>No uses.</p>'}`;
}
function skillsCoverageHTML(installs) {
  if (!installs.length) return '<p class="muted">Not installed.</p>';
  const pill = c => c === 'used' ? '<span class="pill ok">Used</span>' : c === 'unused' ? '<span class="pill waiting">Unused</span>' : '<span class="pill">Unknown</span>';
  return `<table class="tt-who-table">${installs.map(i => `<tr data-sk-coverage="${esc(i.coverage)}"><td class="tt-who-name">${esc(skillsWho(i.target))}</td>
    <td class="tnum tt-used">${skillsN(i.uses_30d)}</td><td>${pill(i.coverage)}</td></tr>`).join('')}</table>`;
}
async function skillsFiles(name, number, run) {
  const box = $('#skills-root [data-sk-files]'); if (!box) return;
  try {
    const {version} = await get(`/v2/skills/${encodeURIComponent(name)}/versions/${number}`);
    if (!skillsLive(run)) return;
    skillsFilesInto(box, version.files || []);
  } catch (e) { if (skillsLive(run)) box.innerHTML = skillsErr(e); }
}
// A file list and the chosen file's text (SKILL.md first).
function skillsFilesInto(box, files) {
  const sorted = files.slice().sort((a, b) => (a.path !== 'SKILL.md') - (b.path !== 'SKILL.md') || a.path.localeCompare(b.path));
  if (!sorted.length) { box.innerHTML = '<p class="muted">No files.</p>'; return; }
  box.innerHTML = `<div class="sk-paths" role="tablist">${sorted.map((f, i) =>
    `<button type="button" role="tab" data-sk-path="${i}" aria-selected="${i === 0}">${esc(f.path)}</button>`).join('')}</div>
    <pre class="sk-file" data-sk-file>${esc(sorted[0].content)}</pre>`;
  box.querySelector('.sk-paths').onclick = ev => {
    const b = ev.target.closest('[data-sk-path]'); if (!b) return;
    box.querySelectorAll('[data-sk-path]').forEach(x => x.setAttribute('aria-selected', String(x === b)));
    box.querySelector('[data-sk-file]').textContent = sorted[Number(b.dataset.skPath)].content;
  };
}

// Install: for me, one bot, one person (admins) or a team's current members (all at once), latest or pinned.
async function skillsInstallDialog(skill, versions) {
  let t;
  try { t = await get('/v2/skills/install-targets', {fresh: true}); } catch (e) { toast(e.message, true); return; }
  const opts = [];
  if (t.me?.type === 'Human') opts.push(['me', 'Me']);
  const bots = (t.bots || []).map(b => [`Bot:${b.id}`, b.name]);
  const people = (t.people || []).filter(p => p.id !== t.me?.id).map(p => [`Human:${p.id}`, p.name]);
  const teams = (t.teams || []).map(g => [`team:${g.id}`, `${g.name} (${g.targets.length})`]);
  const group = (label, list) => list.length ? `<optgroup label="${label}">${list.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('')}</optgroup>` : '';
  const dialog = skillsDialog('Install ' + skill.name, `<label class="sk-field">For
      <select data-sk-target>${opts.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}${group('Bots', bots)}${group('People', people)}${group('Teams', teams)}</select></label>
    <label class="sk-field">Version
      <select data-sk-policy><option value="latest">Latest</option>${versions.map(v => `<option value="${v.number}">Pinned v${v.number}</option>`).join('')}</select></label>`,
  '<button type="button" class="primary" data-sk-install-go>Install</button>', {cancel: 'Cancel'});
  dialog.querySelector('[data-sk-install-go]').onclick = async () => {
    const target = dialog.querySelector('[data-sk-target]').value, policy = dialog.querySelector('[data-sk-policy]').value;
    if (!target) return;
    const version = policy === 'latest' ? {version_policy: 'latest'} : {version_policy: 'pinned', pinned_version: Number(policy)};
    const base = '/v2/skills/' + encodeURIComponent(skill.name) + '/installs';
    try {
      if (target.startsWith('team:')) {
        const team = t.teams.find(g => 'team:' + g.id === target);
        const r = await post(base + '/bulk', {targets: team.targets, ...version});
        toast(`Installed for ${(r.installs || []).length}`);
      } else {
        const [type, id] = target === 'me' ? ['Human', t.me.id] : [target.slice(0, target.indexOf(':')), target.slice(target.indexOf(':') + 1)];
        await post(base, {target_type: type, target_id: id, ...version});
        toast('Installed');
      }
      dialog.close();
      skillsRedraw();
    } catch (e) { skillsDialogError(dialog, e); }
  };
}

// ----------------------------------------------------------------- one proposal
async function pageSkillProposal(id) {
  const run = (SKP?.run || 0) + 1;
  SKP = {run};
  $('#main').innerHTML = `<div class="tt sk" id="skills-root" data-sk-page="proposal">
    <div class="tt-crumb"><a href="${SKILLS_ROUTE}">Skills</a><span aria-hidden="true">›</span><a href="${SKILLS_ROUTE}?tab=proposals">Proposals</a></div>
    <div data-sk-body>${skelRows(4)}</div></div>`;
  const body = $('#skills-root [data-sk-body]');
  let d, admin = false;
  try {
    // Whether the caller approves: the usage read says (admin), so the page never guesses the rule.
    const [detail, usage] = await Promise.all([get('/v2/skills/proposals/' + encodeURIComponent(id), {fresh: true}),
      get('/v2/skills/usage?days=7').catch(() => ({}))]);
    d = detail; admin = !!usage.admin;
  } catch (e) { if (skillsLive(run)) body.innerHTML = skillsErr(e); return; }
  if (!skillsLive(run)) return;
  const p = d.proposal, s = d.screening, pending = p.status === 'pending';
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
      if (e.body?.error?.code === 'acknowledgement_required' && box) { box.focus(); approve.disabled = true; }
      toast(e.message, true);
    }
  });
  body.querySelector('[data-sk-decline]')?.addEventListener('click', () => {
    const dialog = skillsDialog('Decline ' + p.name, '<label class="sk-field">Reason<input type="text" maxlength="300" required data-sk-reason></label>',
      '<button type="button" class="primary" data-sk-decline-go>Decline</button>', {cancel: 'Cancel'});
    const input = dialog.querySelector('[data-sk-reason]'); input.focus();
    dialog.querySelector('[data-sk-decline-go]').onclick = async () => {
      const reason = input.value.trim(); if (!reason) { input.focus(); return; }
      try { await post(`/v2/skills/proposals/${encodeURIComponent(id)}/decline`, {reason}); dialog.close(); toast('Declined'); skillsRedraw(); }
      catch (e) { skillsDialogError(dialog, e); }
    };
  });
  body.querySelector('[data-sk-withdraw]')?.addEventListener('click', async () => {
    try { await post(`/v2/skills/proposals/${encodeURIComponent(id)}/withdraw`, {}); toast('Withdrawn'); skillsRedraw(); }
    catch (e) { toast(e.message, true); }
  });
}
function skillsScreeningHTML(p, s, pending) {
  const rerun = pending ? ' <button type="button" class="ghost small" data-sk-rescreen>Re-run</button>' : '';
  if (p.screen_status === 'pending') return '<p class="muted">Screening runs before anyone can approve.</p>';
  if (p.screen_status === 'not_screened' || !s) return `<p class="muted" data-sk-screen-reason>${esc(s?.reason || 'Not screened.')}${rerun}</p>`;
  const sev = v => `<span class="pill ${v === 'high' ? 'fail' : v === 'medium' ? 'waiting' : ''}">${esc(v || '')}</span>`;
  const where = x => `<code>${esc(x.file || '')}${x.line ? ':' + esc(x.line) : ''}</code>`;
  return `<p data-sk-summary>${esc(s.summary || '')}</p>
    <dl class="tt-kv"><dt>Verdict</dt><dd data-sk-verdict>${esc(s.verdict || '')}</dd><dt>Risk</dt><dd data-sk-risk>${esc(s.risk || '')}</dd></dl>
    ${(s.findings || []).length ? `<h3 class="sk-sub">Findings</h3><table class="tt-log tt-log-short" data-sk-findings><tbody>${s.findings.map(f =>
      `<tr><td>${where(f)}</td><td>${sev(f.severity)}</td><td class="tt-log-what">${esc(f.issue || f.detail || '')}</td></tr>`).join('')}</tbody></table>` : ''}
    ${(s.patterns || []).length ? `<h3 class="sk-sub">Pattern hits</h3><table class="tt-log tt-log-short" data-sk-patterns><tbody>${s.patterns.map(x =>
      `<tr><td>${where(x)}</td><td class="tt-log-what"><code>${esc(x.pattern || '')}</code></td></tr>`).join('')}</tbody></table>` : ''}`;
}
function skillsDiffHTML(diff) {
  if (!diff) return '<p class="muted">No changes.</p>';
  const cls = l => l.startsWith('+++') || l.startsWith('---') ? 'file' : l.startsWith('@@') ? 'hunk' : l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : '';
  return `<pre class="sk-diff" data-sk-diff>${diff.split('\n').map(l => `<span${cls(l) ? ` class="${cls(l)}"` : ''}>${esc(l)}</span>`).join('')}</pre>`;
}

// ----------------------------------------------------------------- routes
function skillsRedraw() { skillsRoute(S.route || ''); }
// The redesign's router asks first (ui/app/redesign.js).
function skillsRoute(r) {
  const [path, query = ''] = r.split('?');
  if (!rdUnder(path, SKILLS_ROUTE)) return false;
  const parts = path.slice(SKILLS_ROUTE.length).split('/').filter(Boolean).map(x => { try { return decodeURIComponent(x); } catch { return x; } });
  if (parts[0] === 'proposals' && parts[1]) { void pageSkillProposal(parts[1]); return true; }
  if (parts.length) { void pageSkill(parts.join('/')); return true; }
  const tab = new URLSearchParams(query).get('tab');
  void pageSkills(SKILLS_TABS.some(([k]) => k && k === tab) ? tab : '');
  return true;
}
// Leaving the page drops its state, so a late answer draws nothing.
function skillsStop(r) { if (!rdUnder((r || '').split('?')[0], SKILLS_ROUTE)) SKP = null; }
