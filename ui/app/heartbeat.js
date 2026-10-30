/* ui/app/heartbeat.js — The account status dot and its lines, Needs you, account menu label, download link
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Only urgent issues with a concrete human action interrupt the person. The full diagnostic
// list remains in Settings, including temporary outages and bot execution reviews.
const needsPerson = issue => issue.needs_person === true && !!(issue.action || issue.restart);
const needsYouKey = issue => [issue.kind, issue.bot || '', issue.machine || '', issue.title].join('|');
const NEEDS_YOU_KEY = 'tico.needsYou';
function needsYouMemory() { try { return JSON.parse(localStorage.getItem(NEEDS_YOU_KEY) || '{}'); } catch { return {}; } }
function needsYouRemember(patch) { try { localStorage.setItem(NEEDS_YOU_KEY, JSON.stringify({...needsYouMemory(), ...patch})); } catch {} }
// One status dot, left of the person's name in the account button (Chris, Oct 22): coloured by the worst line,
// red when something is broken and needs the person, amber for a warning or something that clears by itself.
// The same lines head the account menu and a hover card on the dot, one plain line each with its fix. No line, no dot.
const ACCOUNT_HEALTH = '<a class="ghost" href="#/settings" data-gs-tab="health" title="Open Health in Settings">Fix</a>';
const firstSentence = text => { const s = String(text || '').trim(); const m = s.match(/^.*?[.!?](?=\s|$)/); return (m ? m[0] : s).replace(/[.]$/, ''); };
function accountIssueFix(issue) {
  if (issue.kind === 'uncertain_work' && issue.bot) return `<button class="ghost" type="button" data-account-review="${esc(issue.bot)}">Review</button>`;
  if (issue.restart && issue.machine) return '<button class="ghost" type="button" data-account-needs-you title="Open Needs you to restart this computer">Open</button>';
  if (issue.kind === 'machine' && issue.machine) return '<a class="ghost" href="#/settings" data-gs-tab="devices">Fix</a>';
  if (issue.bot) return `<a class="ghost" href="#/bot/${esc(issue.bot)}/more" title="Open the bot">Fix</a>`;
  return '<button class="ghost" type="button" data-account-needs-you title="Open Needs you">Open</button>';
}
function accountStatusLines() {
  const st = S.status, lines = [];
  if (!st) {
    // A lost connection comes back by itself, so it is a warning, never red.
    if (!S.statusPending) lines.push({level: 'warn', text: "Can't reach Tico. Trying again", fix: '<button class="ghost" type="button" data-account-retry>Retry</button>'});
  } else if (!st.keeper_alive) {
    lines.push({level: 'bad', text: st.cloud ? "Bots aren't running: the cloud scheduler stopped" : "Tico stopped on this computer, so bots aren't running", fix: ACCOUNT_HEALTH});
  }
  for (const issue of (st?.health_issues || []).filter(needsPerson)) {
    lines.push({level: issue.severity === 'warning' ? 'warn' : 'bad', text: [issue.title, issue.kind === 'machine' ? String(issue.detail || '').replace(/[.]$/, '') : firstSentence(issue.detail)].filter(Boolean).join(': '), fix: accountIssueFix(issue)});
  }
  // A computer offline for a moment (asleep, a restart) comes back by itself: Health calls it bad at once, but the
  // server asks a person only after NEEDS_YOU_MINUTES (backend/views.py, "stopped checking in"). Until a machine issue
  // needs this person, the offline checks are amber; with no computer set up at all, nothing clears, so red stays.
  const attention = window.hlAttention?.() || [];
  const machineNeedsYou = (st?.health_issues || []).some(issue => issue.kind === 'machine' && needsPerson(issue));
  const computers = window.hlComputers?.();
  const noComputer = Array.isArray(computers) && !computers.length;
  const offlineComputers = (computers || []).filter(computer => !computer.online);
  const namedOutages = new Set((st?.health_issues || []).filter(issue => issue.kind === 'machine' && needsPerson(issue)).map(issue => issue.machine));
  // Omit the aggregate only when every offline computer has its own actionable line.
  const allOutagesNamed = offlineComputers.length > 0 && offlineComputers.every(computer => namedOutages.has(computer.id));
  const offline = new Set(['computers', 'waiting']);
  // The aggregate keeps the affected bot names when no per-computer action covers them.
  const computersLine = attention.some(check => check.id === 'computers');
  const waiting = attention.find(check => check.id === 'waiting');
  for (const check of attention) {
    if ((check.id === 'computers' || check.id === 'waiting') && allOutagesNamed) continue;
    if (check.id === 'waiting' && computersLine) continue;
    const fix = check.fixes?.[0];
    const level = check.status !== 'bad' ? 'warn' : offline.has(check.id) && !machineNeedsYou && !noComputer ? 'warn' : 'bad';
    // A fix that leaves Tico (GitHub's settings, a provider's docs) opens in its own tab.
    const away = /^https?:/.test(fix?.href || '') ? ' target="_blank" rel="noopener"' : '';
    lines.push({level, text: [check.label, [firstSentence(check.summary), check.id === 'computers' && waiting ? firstSentence(waiting.summary) : ''].filter(Boolean).join('. ')].filter(Boolean).join(': '),
      fix: fix ? window.hlFixHtml({...fix, label: 'Fix'}).replace(/^<(a|button) /, `<$1 title="${esc(fix.label)}"${away} `) : ACCOUNT_HEALTH});
  }
  return lines.sort((a, b) => (a.level === 'bad' ? 0 : 1) - (b.level === 'bad' ? 0 : 1));
}
function renderAccountStatus() {
  const hb = $('#heartbeat');
  if (!hb) return;
  const st = S.status, lines = accountStatusLines();
  const level = lines.some(line => line.level === 'bad') ? 'bad' : lines.length ? 'warn' : '';
  const label = lines.length === 1 ? lines[0].text : lines.length ? `${lines.length} things to look at`
    : !st ? 'Connecting' : st.cloud ? 'Cloud backend connected' : 'Computer running';
  hb.title = label;
  hb.setAttribute('aria-label', label);
  hb.dataset.level = level;
  hb.hidden = !level;
  const html = lines.map(line => `<li class="account-status-line ${line.level}" data-level="${line.level}"><span class="account-status-text" title="${esc(line.text)}">${esc(line.text)}</span>${line.fix}</li>`).join('');
  for (const box of [$('#account-status'), $('#account-status-pop')]) {
    if (!box) continue;
    patchLiveHTML(box, html ? `<ul>${html}</ul>` : '');
    if (!html) box.hidden = true;
    else if (box.id === 'account-status') box.hidden = false;
  }
}
function renderHeartbeat() {
  renderAccountStatus();
  needsYouAutoOpen((S.status?.health_issues || []).filter(needsPerson));
}
// Desktop: hovering the dot shows the lines without opening the menu. A tap opens the menu, which starts with them.
(() => {
  const wrap = $('.account-wrap'), pop = $('#account-status-pop');
  if (!wrap || !pop) return;
  let hide = 0;
  const canHover = () => window.matchMedia('(hover: hover)').matches;
  wrap.addEventListener('mouseover', ev => {
    if (!canHover() || !ev.target.closest('#heartbeat, #account-status-pop') || !$('#account-menu').hidden || !pop.innerHTML) return;
    clearTimeout(hide); pop.hidden = false;
  });
  wrap.addEventListener('mouseout', ev => {
    if (ev.relatedTarget?.closest?.('#heartbeat, #account-status-pop')) return;
    clearTimeout(hide); hide = setTimeout(() => { pop.hidden = true; }, 200);
  });
  wrap.addEventListener('click', () => { pop.hidden = true; });
})();
document.addEventListener('click', ev => {
  if (ev.target.closest('[data-account-retry]')) { void refresh(false); return; }
  if (ev.target.closest('[data-account-needs-you]')) { needsYou(); return; }
  const review = ev.target.closest('[data-account-review]');
  if (review) executionReview(review.dataset.accountReview);
});
// The first time a new issue that needs a person shows up, it opens in front of them, wherever
// they are in the app. Closing it is remembered per issue, and Snooze quiets everything for an
// hour, so a person is asked once, not on every poll.
function needsYouAutoOpen(urgent) {
  // Only an issue the server explicitly marked for a person opens by itself; anything else waits
  // behind the sidebar alert. Never over another dialog: whatever the person is doing comes first.
  const explicit = urgent.filter(needsPerson);
  if (!explicit.length || document.querySelector('dialog[open], .needs-you')) return;
  const memory = needsYouMemory();
  if (memory.snoozedUntil && Date.now() < memory.snoozedUntil) return;
  const seen = new Set(memory.seen || []);
  if (explicit.every(issue => seen.has(needsYouKey(issue)))) return;
  needsYou();
}
// A panel above the sidebar alert, not a modal: it pops open in front of the person without
// trapping them or covering what they were doing, and it is not a <dialog>, so nothing that
// waits for `dialog[open]` (the review it can launch, for one) is confused by it.
// A computer with a Tico update restarts from here. The runner takes main when
// that is safe and restarts once nothing is running, so the line can say nothing is interrupted.
const runnerRestartButton = issue => issue.restart && issue.machine
  ? `<button class="primary" type="button" data-runner-restart="${esc(issue.machine)}">Restart</button>` : '';
function bindRunnerRestart(root) {
  root.querySelectorAll('[data-runner-restart]').forEach(button => {
    button.onclick = async () => {
      button.disabled = true; button.textContent = 'Restarting…';
      const line = button.closest('.settings-issue')?.querySelector('p');
      try {
        const r = await post(`/v2/computers/${encodeURIComponent(button.dataset.runnerRestart)}/restart`, {});
        if (line) line.textContent = r.running
          ? `Restarting after ${r.running} run${r.running === 1 ? ' finishes' : 's finish'}.`
          : 'Restarting now. Nothing is running, so nothing will be interrupted.';
        button.textContent = 'Restarting';
      } catch (e) { button.disabled = false; button.textContent = 'Restart'; toast(e.message, true); }
    };
  });
}
// A choice saved while the bot was busy keeps the run listed (backend execution.stopped_rows `decided`): its line says
// what Tico will do and offers no second choice, which the server would refuse. Once some of a run's folded requests
// have a choice, only that choice is offered, for the rest.
const stoppedChosen = run => run.decided || run.jobs?.find(job => job.decided)?.decided;
// Whether a settled line's choice still waits for the bot's turn: a choice the server already held (no `deferred` of
// this page's own click) was saved while the bot was busy.
const stoppedWaits = run => !run.jobs || run.jobs.some(job => job.deferred !== false);
// A bot's stopped runs, one short line each: the task, why it stopped (saying so when that was a restart or a limit,
// not a bug) and its own Try again / Don't retry, the safe pick first by the review card's rule.
// The issue's title opens that card (settings.js executionReview) with the evidence.
const RETRY_HINT = 'The bot checks what already happened first';
function stoppedLines(issue, list) {
  if (!issue.runs?.length) return '';
  const button = (run, i, decision, label, lead) => `<button type="button"${lead ? '' : ' class="ghost"'} data-run-decision="${decision}" data-run="${i}"${decision === 'resume' ? ` title="${RETRY_HINT}"` : ''}${run.decided || stoppedChosen(run) && stoppedChosen(run) !== decision ? ' disabled' : ''}>${label}</button>`;
  // When every run stopped the same routine way the card's own line already says it is not a bug.
  const shared = issue.runs.every(run => run.routine && run.cause === issue.runs[0].cause);
  const line = (run, i) => `<li class="stopped-line" data-run-row="${i}"${run.decided ? ` data-decided="${esc(run.decided)}"` : ''}>
    <span class="stopped-name" title="${esc(run.title || '')}">${esc(run.title || 'A request you can’t open')}</span>
    <span class="stopped-why">${esc(run.why)}${run.routine && !shared ? ' · not a bug' : ''}</span>
    ${issue.can_review ? `<div class="stopped-picks">${run.retry ? button(run, i, 'resume', 'Try again', true) + button(run, i, 'dismiss', 'Don’t retry')
      : button(run, i, 'dismiss', 'Don’t retry', true) + button(run, i, 'resume', 'Try again')}</div>` : ''}
    <p class="stopped-result" data-result role="status" aria-live="polite">${run.decided ? esc(stoppedOutcome(run.decided, stoppedWaits(run))) : ''}</p></li>`;
  list.push(issue);
  return `<ul class="stopped-lines" data-runs="${list.length - 1}">${issue.runs.map(line).join('')}</ul>
    ${issue.more_runs ? `<p class="muted">And ${issue.more_runs} more</p>` : ''}`;
}
function bindStoppedLines(panel, list) {
  panel.querySelectorAll('[data-runs]').forEach(ul => {
    const issue = list[+ul.dataset.runs];
    ul.querySelectorAll('[data-run-row]').forEach(li => {
      const run = issue.runs[+li.dataset.runRow], buttons = li.querySelectorAll('button'), result = li.querySelector('[data-result]');
      // A group holding this line (bindUpdateGroup) hears when its choice starts and ends saving, so no run gets both.
      const saving = on => { run.saving = on; li.dispatchEvent(new Event('run-saving', {bubbles: true})); };
      buttons.forEach(button => button.onclick = async () => {
        const decision = button.dataset.runDecision;
        buttons.forEach(b => b.disabled = true);
        result.classList.remove('err'); result.textContent = 'Saving choice…';
        saving(true);
        try {
          await decideRun(run, decision);
          li.dataset.decided = run.decided;
          result.textContent = stoppedOutcome(run.decided, stoppedWaits(run));
        } catch (e) {
          result.classList.add('err'); result.textContent = e.message;
          buttons.forEach(b => b.disabled = !!stoppedChosen(run) && b.dataset.runDecision !== stoppedChosen(run));
          return;
        } finally { saving(false); }
        if (!ul.querySelector('[data-run-row]:not([data-decided])')) void refresh(false);
      });
    });
  });
}
// Saves one run's choice on each of its requests. A message folded into the run rides on it, so one choice settles them
// all; a request already settled (an earlier click that stopped partway, or a choice saved while the bot was busy) is
// not sent again. The run keeps the outcome, so Needs you closed and reopened before a refresh still shows it.
async function decideRun(run, decision) {
  for (const job of run.jobs.filter(job => !job.decided)) {
    const done = await reconcileStopped(job, decision);
    job.decided = decision; job.deferred = !!done.deferred;
  }
  run.decided = run.jobs.every(job => job.decided === decision) ? decision : 'mixed';
  return run.decided;
}
// Tico's own update restarts every computer at once, so the runs it stops arrive together. The ones the server puts in
// the update's group (`update_group`: no named outside action, no approval used) and that nobody has started deciding
// share one choice, two or more stopped within ten minutes of the first; a later update makes a group of its own.
// Each keeps its own line under Details, and any other run keeps its own choice outside the group.
const UPDATE_GROUP_MS = 10 * 60e3;
function updateGroups(issues) {
  const members = issues.flatMap(issue => (issue.runs || []).map((run, i) => ({issue, run, i, at: Date.parse(run.at || '')})))
    .filter(m => m.run.cause === 'update' && m.run.update_group && !m.run.chosen && !stoppedChosen(m.run) && Number.isFinite(m.at))
    .sort((a, b) => a.at - b.at);
  const groups = [];
  for (const m of members) {
    const last = groups.at(-1);
    if (last && m.at - last.at <= UPDATE_GROUP_MS) last.members.push(m);
    else groups.push({members: [m], at: m.at});
  }
  return groups.filter(g => g.members.length > 1).map((g, k) => ({...g, k, at: new Date(g.at)}));
}
// Only the people who may decide a run see its buttons: the others read the same line with no choice to make.
// The update's stop is timing, not proof its computer restarted, so Try all again is offered only when every run is
// one the review card recommends retrying. Otherwise the row says how many ran commands Tico cannot check and offers
// Don't retry for all of them; retrying any of them is that run's own choice on its line, under the title.
const updateGroupRow = group => {
  const n = group.members.length, deciding = group.members.filter(m => m.issue.can_review && m.run.jobs).length;
  const scope = deciding === n ? 'Applies to all of them' : `Applies to the ${deciding} you can decide`;
  const risky = group.members.filter(m => !m.run.retry).length;
  const resume = cls => `<button class="${cls}" type="button" data-group-decision="resume" title="${scope}. ${RETRY_HINT}">Try all again</button>`;
  const dismiss = cls => `<button class="${cls}" type="button" data-group-decision="dismiss" title="${scope}">Don’t retry</button>`;
  const line = !deciding ? 'Only each bot’s operator can decide' : risky ? `${risky === n ? 'All of them' : `${risky} of them`} ran commands Tico can’t check` : '';
  return `<div class="settings-issue stopped-group" data-update-group="${group.k}">
    <div>${needsTitle(`${n} run${n === 1 ? '' : 's'} stopped during Tico’s update at ${group.at.toISOString().slice(11, 16)} UTC`,
      'button type="button" data-group-details aria-expanded="false"')}${line ? `<p>${line}</p>` : ''}</div>
    <div class="issue-buttons">${deciding ? (risky ? dismiss('primary') : resume('primary') + dismiss('ghost')) : ''}</div>
    <ul class="stopped-lines" data-group-runs hidden></ul><p class="stopped-result" data-group-result role="status" aria-live="polite"></p></div>`;
};
function bindUpdateGroup(panel, group, stopped) {
  const el = panel.querySelector(`[data-update-group="${group.k}"]`);
  if (!el) return;
  const runs = el.querySelector('[data-group-runs]'), result = el.querySelector('[data-group-result]');
  // Each member's own line moves under Details, keeping the buttons bound by bindStoppedLines.
  for (const m of group.members) {
    m.li = panel.querySelector(`[data-runs="${stopped.indexOf(m.issue)}"] [data-run-row="${m.i}"]`);
    if (m.li) runs.append(m.li);
  }
  const details = el.querySelector('[data-group-details]');
  details.onclick = () => {
    runs.hidden = !runs.hidden;
    details.setAttribute('aria-expanded', String(!runs.hidden));
  };
  const picks = [...el.querySelectorAll('[data-group-decision]')];
  // The group's choices wait while a run's own choice saves, and stay closed once the group's are saved.
  let busy = false;
  const free = () => picks.forEach(b => b.disabled = busy || group.members.some(m => m.run.saving));
  el.addEventListener('run-saving', free);
  picks.forEach(button => button.onclick = async () => {
    if (busy || group.members.some(m => m.run.saving)) return;
    const decision = button.dataset.groupDecision;
    const todo = group.members.filter(m => m.issue.can_review && m.run.jobs && !stoppedChosen(m.run) && m.li);
    // Each run's own choices wait while the group's are saved, so no run gets both; afterwards they show what was
    // saved, as a run's own line does.
    const lines = todo.map(m => [m.run, [...m.li.querySelectorAll('[data-run-decision]')]]);
    const settle = () => lines.forEach(([run, buttons]) => buttons.forEach(b =>
      b.disabled = !!run.decided || !!stoppedChosen(run) && b.dataset.runDecision !== stoppedChosen(run)));
    busy = true; free();
    lines.forEach(([, buttons]) => buttons.forEach(b => b.disabled = true));
    result.classList.remove('err'); result.textContent = 'Saving choices…';
    try {
      // One request per run, in order: a refusal stops the rest and shows here, and the runs already saved stay saved.
      for (const m of todo) {
        if (stoppedChosen(m.run)) continue;
        await decideRun(m.run, decision);
        m.li.dataset.decided = m.run.decided;
        m.li.querySelector('[data-result]').textContent = stoppedOutcome(m.run.decided, stoppedWaits(m.run));
      }
    } catch (e) {
      result.classList.add('err'); result.textContent = e.message;
      busy = false; free();
      settle();
      return;
    }
    settle();
    result.textContent = decision === 'resume' ? 'Queued to try again.' : 'Dismissed. Tico won’t retry these runs.';
    void refresh(false);
  });
}
// A card is a title, at most one short line and at most two buttons. The title is the way to the details (the stopped
// runs card, the bot, the computer, Health), so Details and Open bot are not buttons of their own; what the server
// wrote in full stays on the title's tooltip.
const needsTitle = (text, tag, tip = '') => `<${tag} class="needs-title"${tip ? ` title="${esc(tip)}"` : ''}><span>${esc(text)}</span><span class="nav-icon" aria-hidden="true">chevron_right</span></${tag.split(' ')[0]}>`;
const needsTitleTag = issue => issue.kind === 'uncertain_work' && issue.bot ? `button type="button" data-execution-review="${esc(issue.bot)}"`
  : issue.machine && !issue.bot ? 'a href="#/settings" data-gs-tab="devices"'
  : issue.bot ? `a href="#/bot/${esc(issue.bot)}/more"` : 'a href="#/settings" data-gs-tab="health"';
function needsYou() {
  document.querySelector('.needs-you')?.remove();
  const issues = (S.status?.health_issues || []).filter(needsPerson);
  const groups = updateGroups(issues);
  const panel = document.createElement('section'); panel.className = 'needs-you';
  panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', 'Needs you');
  const stopped = [];
  // Listed runs say it themselves, so their issue's own line goes; otherwise the line is the detail's first sentence.
  const full = issue => [issue.detail, issue.action !== 'review' && issue.action].filter(Boolean).join(' ');
  const line = issue => issue.runs?.length || !issue.detail ? '' : `<p>${esc(firstSentence(issue.detail))}</p>`;
  // The text and its lines take the full width; the actions sit under them, each as wide as its words.
  const row = issue => `<div class="settings-issue" data-key="${esc(needsYouKey(issue))}"><div>${needsTitle(issue.title, needsTitleTag(issue), full(issue))}${line(issue)}${stoppedLines(issue, stopped)}</div>
    <div class="issue-buttons">${runnerRestartButton(issue)}</div></div>`;
  panel.innerHTML = `<header><h2></h2><button class="ghost" data-close aria-label="Close">Close</button></header>
    <div data-list>${groups.map(updateGroupRow).join('')}${issues.map(row).join('') || '<p>Nothing needs you right now.</p>'}</div>
    <div class="row"><button class="ghost" data-snooze>Snooze for an hour</button><a class="ghost" href="#/settings">Open Settings</a></div>`;
  const close = () => { needsYouRemember({seen: issues.map(needsYouKey)}); panel.remove(); };
  panel.querySelector('[data-close]').onclick = close;
  panel.querySelector('[data-snooze]').onclick = () => { needsYouRemember({snoozedUntil: Date.now() + 3600e3}); close(); };
  panel.querySelectorAll('[data-execution-review]').forEach(button => {
    button.onclick = () => { close(); executionReview(button.dataset.executionReview); };
  });
  panel.querySelectorAll('a[href]').forEach(a => a.addEventListener('click', close));
  bindRunnerRestart(panel);
  bindStoppedLines(panel, stopped);
  groups.forEach(group => bindUpdateGroup(panel, group, stopped));
  // An issue whose every run moved into an update's group would only repeat that group.
  panel.querySelectorAll('[data-runs]').forEach(ul => {
    if (!ul.children.length && !stopped[+ul.dataset.runs].more_runs) ul.closest('.settings-issue').hidden = true;
  });
  const n = panel.querySelectorAll('[data-list] > .settings-issue:not([hidden])').length;
  panel.querySelector('h2').textContent = n === 1 ? 'One thing needs you' : `${n} things need you`;
  document.body.appendChild(panel);
  return panel;
}
function renderAccount() {
  const el = $('#account'), me = S.me;
  if (!el) return;
  if (!me) { el.querySelector('.account-email').textContent = 'Not signed in'; return; }
  el.title = `Signed in as ${me.email}${me.local ? ' (local app)' : ''}`;
  el.querySelector('.account-email').textContent = me.email || 'Not signed in';
  renderAccountStatus();
  renderInboxNav();
  renderNewVersion();
  renderDownloadLink();
  // Only a session an identity proxy vouches for has anything to end; a bearer or the local app does not.
  $('#sign-out').hidden = !me.proxy_session;
}
// The company app gets a browser nudge; a generic build is only a manual-setup option in
// the account menu. Neither appears inside the app. Dismissal hides the nudge for a week.
const visitorOS = () => /Win/.test(navigator.platform) ? 'windows' : /Linux/.test(navigator.platform) && !/Android/.test(navigator.userAgent) ? 'linux' : /Mac/.test(navigator.platform) ? 'mac' : '';
const OS_WORDS = {mac: 'Mac', windows: 'Windows', linux: 'Linux'};
let downloadRequest = 0;
async function renderDownloadLink() {
  const request = ++downloadRequest;
  const a = $('#download-app'), nudge = $('#app-nudge');
  const os = visitorOS();
  const eligible = () => !S.me?.local && os && visitorOS() === os && !document.body.classList.contains('native');
  if (a) { a.hidden = true; a.removeAttribute('href'); }
  if (nudge) { nudge.hidden = true; nudge.querySelector('.app-nudge-get').removeAttribute('href'); }
  if (!a || !eligible()) return;
  try {
    const r = await fetch('/api/download/' + os, {credentials: 'same-origin'});
    const d = r.ok ? await r.json() : null;
    if (request !== downloadRequest || !eligible() || !d?.available || typeof d.url !== 'string' || !d.url.trim()) return;
    // The API validates the installer source. Use that exact version, without selecting a
    // manifest again, and never turn a non-web URL into an executable link.
    const url = new URL(d.url, location.href);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return;
    const company = d.app_kind === 'company';
    const label = company ? `Download Tico for ${companyName()}` : 'Generic Tico · manual setup';
    a.href = d.url;
    a.textContent = '';
    a.insertAdjacentHTML('beforeend', `<span class="nav-icon" aria-hidden="true">download</span>${esc(label)}${os === 'mac' ? betaPill('mac_app') : ''}`);
    a.title = `${company ? appName() : 'Generic Tico'} ${d.version || ''} for ${OS_WORDS[os]} (${d.size_mb || '?'} MB)${company ? '' : '. Requires manual server setup'}${d.notarized || os !== 'mac' ? '' : '. First open: System Settings → Privacy & Security → Open Anyway'}`;
    a.hidden = false;
    if (!nudge || !company) return;
    let snoozed = 0;
    try { snoozed = Number(localStorage.getItem('hub.app-nudge.until') || 0); } catch {}
    if (snoozed > Date.now()) { nudge.hidden = true; return; }
    nudge.querySelector('.app-nudge-text').textContent = `Tico for ${companyName()} opens your company directly, with no server setup.`;
    const get = nudge.querySelector('.app-nudge-get');
    get.href = d.url; get.textContent = label;
    nudge.querySelector('.app-nudge-close').onclick = () => { nudge.hidden = true; try { localStorage.setItem('hub.app-nudge.until', String(Date.now() + 7 * 86400e3)); } catch {} };
    nudge.hidden = false;
  } catch { if (request === downloadRequest) { a.hidden = true; if (nudge) nudge.hidden = true; } }
}
