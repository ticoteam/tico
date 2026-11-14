/* ui/app/person-page.ts — The human page (team tree): profile, tasks, Slack
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The parts of GET /v2/humans/{id}/slack the page reads: the Tico DM threads a human has with bots in Slack.
interface HumanSlackMessage {
  sender_id?: string | null;
  body?: string;
  refs?: { read_back?: boolean } | null;
}
interface HumanSlackThread {
  bot: string;
  display_name?: string | null;
  last_message_at?: string | null;
  messages?: HumanSlackMessage[] | null;
}

// ----------------------------------------------------------------- Web Push for this browser
// On with Browser notifications, so a call rings while Tico is hidden or closed (backend/web_push.py, docs/push.md).
// A browser without push, or a page that cannot reach its service worker (ui/app/boot.ts registers it), skips it.
async function pushUnsubscribe(opts?: ApiWriteOptions): Promise<void> {
  const reg = await pushRegistration();
  const sub = reg && await reg.pushManager.getSubscription();
  if (!sub) return;
  await writeRequest('DELETE', '/v2/push/subscriptions', {endpoint: sub.endpoint}, undefined, opts);
  await sub.unsubscribe();
}
// Signing out ends this browser's push, so whoever uses it next is not rung for the person who left. The same links
// decide.ts and boot-snap.ts clear on. Sign-out waits for it at most PUSH_SIGN_OUT_MS, then goes on whatever happened.
const PUSH_SIGN_OUT_MS = 1500;
document.addEventListener('click', e => {
  const link = eventElement(e)?.closest<HTMLAnchorElement>('a[href]');
  if (!link || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  if (!/\/(?:logout|cdn-cgi\/access\/logout)(?:[?#]|$)/.test(String(link.getAttribute('href')))) return;
  // Without notification permission this browser holds no push subscription: nothing to wait for.
  if (!('PushManager' in window) || !('Notification' in window) || Notification.permission !== 'granted') return;
  e.preventDefault();
  const href = link.href;
  void Promise.race([pushUnsubscribe({keepalive: true, timeout: PUSH_SIGN_OUT_MS}).catch(() => {}),
    new Promise(done => setTimeout(done, PUSH_SIGN_OUT_MS))]).then(() => { location.href = href; });
}, true);

// ----------------------------------------------------------------- human page (team tree)
const PERSON_TABS = ['profile', 'slack'];
// The Assistant tab is first, and only on your own page: it opens your private chat with the assistant, the
// Assistant page (ui/app/assistant-page.ts), the owner's too. The old #/person/<you>/assistant link lands there too.
const personTabs = (p: RosterPerson) => S.me?.id === p.id ? ['assistant', ...PERSON_TABS] : PERSON_TABS;
const PERSON_TAB_LABELS: Record<string, string> = {assistant: 'Assistant', profile: 'Profile', slack: 'Slack'};
function fieldEditor(label: string, value: string | null | undefined, {placeholder, rows, save}: {placeholder?: string; rows?: number; save: boolean}) {
  const current = String(value || '');
  return `<section class="card"><header><h2>${esc(label)}</h2>
      ${save ? `<button class="linkish" type="button" data-edit>Edit</button>` : ''}</header>
    <div data-view><p>${current ? esc(current).replace(/\n/g, '<br>') : '<span class="muted">None set.</span>'}</p></div>
    ${save ? `<form data-edit-form hidden><textarea rows="${rows || 5}" placeholder="${esc(placeholder || '')}">${esc(current)}</textarea>
      <div class="row"><button class="primary" type="submit">Save</button>
        <button class="ghost" type="button" data-cancel>Cancel</button>
        <span class="muted" data-status></span></div></form>` : ''}</section>`;
}
function bindFieldEditor(root: HTMLElement, onSave: (text: string) => Promise<unknown>) {
  const view = root.querySelector<HTMLElement>('[data-view]');
  const form = root.querySelector<HTMLFormElement>('[data-edit-form]');
  if (!form) return;
  const area = form.querySelector<HTMLTextAreaElement>('textarea');
  const edit = root.querySelector<HTMLButtonElement>('[data-edit]');
  const cancel = form.querySelector<HTMLButtonElement>('[data-cancel]');
  if (!view || !area || !edit || !cancel) return;
  edit.onclick = () => { form.hidden = false; view.hidden = true; area.focus(); };
  cancel.onclick = () => { form.hidden = true; view.hidden = false; };
  form.onsubmit = async ev => {
    ev.preventDefault();
    const status = form.querySelector<HTMLElement>('[data-status]');
    form.querySelectorAll('button').forEach(b => b.disabled = true);
    if (!status) return;
    status.textContent = 'Saving…';
    try {
      await onSave(area.value);
      form.hidden = true; view.hidden = false;
    } catch (e) { status.innerHTML = `<span class="err">${esc(errorMessage(e, ''))}</span>`; }
    finally { form.querySelectorAll('button').forEach(b => b.disabled = false); if (form.hidden) status.textContent = ''; }
  };
}
function pagePerson(id: string | undefined, tab: string | undefined) {
  const p = (S.people || []).find(row => row.id === id && !row.hidden);
  if (!p) { $('#main').innerHTML = `<div class="empty">Unknown human.</div>`; return; }
  const tabs = personTabs(p);
  if (tab === 'assistant' && tabs.includes('assistant')) { location.replace(ASSISTANT); return; }
  orgHistoryVisit('p:' + p.id);
  tab = tabs.includes(tab ?? '') ? tab : 'profile';
  // In the chart's order: the ones someone placed first (byReportOrder), then the chart's own order (chartFallback).
  const placed = byReportOrder('p:' + p.id), inOrder = (a: ChartNode, b: ChartNode) => placed(a, b) || chartFallback(a, b);
  const reports = (S.people || []).filter(row => row.reports_to === p.id && !row.hidden)
    .sort((a, b) => inOrder({kind: 'person', person: a}, {kind: 'person', person: b}));
  const bots = (p.bots || []).map(slug => botRow(slug)).filter((e): e is Employee => !!e && !isHiddenBot(e.id));    // not the Assistant or its helpers (ui/app/sidebar.ts)
  const under = shownEmps().filter(e => e.org_parent === 'p:' + p.id);
  const boss = (S.people || []).find(row => row.id === p.reports_to);
  const edit = personCanEdit(p);
  const about = goalParts(p.about || '');
  $('#main').innerHTML = `<div class="bothead">${personAvatar(p, 36)}
    <div class="botid"><h1>${esc(p.name || p.id)}</h1>
      <ul class="goal-lines head-goals" id="head-goals" aria-label="Goals"></ul>
      ${String(p.goals || '').trim() ? `<div class="role" id="head-standing">${esc(String(p.goals).trim())}</div>` : ''}
    </div>${S.me?.id !== p.id && THREADS.on !== false && p.my_access?.write !== false ? '<button type="button" class="ghost small" id="person-dm">DM</button>' : ''}</div>
  <div class="tabs bottabs" id="ptabs" role="tablist">
    ${tabs.map(t => `<button data-pt="${t}" role="tab" class="${t === tab ? 'cur' : ''}">${t === 'assistant' ? esc(myChatName()) : PERSON_TAB_LABELS[t]}</button>`).join('')}
  </div>
  <div id="pane-profile" ${tab === 'profile' ? '' : 'hidden'}>
    <section class="card"><header><h2>Contact</h2></header>
      <dl class="bot-setup">
        ${boss ? `<dt>Reports to</dt><dd><a href="#/person/${encodeURIComponent(boss.id)}">${personAvatar(boss, 18)} ${esc(boss.name)}</a></dd>` : ''}
        ${p.email ? `<dt>Email</dt><dd><a href="mailto:${esc(p.email)}">${esc(p.email)}</a></dd>` : ''}
        ${p.slack ? `<dt>Slack</dt><dd>@${esc(p.slack)}</dd>` : ''}
        ${p.github || edit ? `<dt>GitHub</dt><dd id="person-github"></dd>` : ''}
        ${p.phone ? `<dt>Phone</dt><dd><a href="tel:${esc(p.phone)}">${esc(p.phone)}</a></dd>` : ''}
        ${p.bot ? `<dt>Notes go to</dt><dd>${empChip(p.bot)}</dd>` : ''}
      </dl></section>
    ${p.title || p.team || p.about || edit ? `<section class="card" id="person-about"><header><h2>About</h2>
        ${edit ? '<button class="linkish person-edit-btn" type="button" id="person-edit">Edit</button>' : ''}</header>
      <div id="person-id">${p.title || p.team ? `<div class="meta">${esc([...new Set([p.title, p.team ? teamLabel(p.team) : ''].filter(Boolean))].join(' · '))}</div>` : ''}
        ${p.about ? `<div class="role">${esc(about.lead)}${about.rest ? `<span id="goal-rest" hidden> ${esc(about.rest)}</span> <button class="linkish" type="button" id="goal-more" aria-expanded="false">more</button>` : ''}</div>`
          : edit && !p.title ? '<div class="muted">No title or description yet.</div>' : ''}</div></section>` : ''}
    <section class="card home-card" id="person-home"><header><h2>Home</h2><div class="home-card-acts" id="person-home-acts"></div></header><div id="person-home-thread"></div></section>
    <section class="card tasks" id="person-tasks-card"><header><h2>Tasks</h2>
        <button class="ghost" type="button" id="person-task-add" aria-haspopup="dialog">+ Add task</button></header>
      <div id="person-tasks">Loading…</div></section>
    ${fieldEditor('Goals', p.goals, {placeholder: 'What they are trying to get done.', save: edit})}
    ${fieldEditor('Notes', p.notes, {placeholder: 'Working notes about this human. Visible to humans who can open this profile.', rows: 8, save: edit})}
    ${reports.length ? `<section class="card"><header><h2>Reports</h2></header>
      <div class="chip-list">${reports.map(row => `<a class="chip" href="#/person/${encodeURIComponent(row.id)}">${personAvatar(row, 20)}<span>${esc(firstName(row.name) || row.id)}</span></a>`).join('')}</div></section>` : ''}
    ${(bots.length || under.length) ? `<section class="card"><header><h2>Bots</h2></header>
      <div class="chip-list">${[...new Map([...bots, ...under].map(e => [e.id, e])).values()].sort((a, b) => inOrder({kind: 'bot', ...a}, {kind: 'bot', ...b})).map(e => `<a class="chip" href="#/bot/${encodeURIComponent(e.id)}">${avatar(e.id, 20, stateOf(e.id))}<span>${shownName(e)}</span></a>`).join('')}</div></section>` : ''}
    <section class="card" id="person-access" hidden><header><h2>Access</h2>
        <button class="linkish person-edit-btn" type="button" id="person-access-edit">Edit</button></header>
      <div class="muted" id="person-access-text"></div></section>
    ${edit ? `<section class="card" id="person-notify"><header><h2>Notifications</h2></header>
      <label class="person-notify" title="A Slack DM when a task they asked for is finished or declined">
        <input type="checkbox" role="switch" class="people-switch" id="person-notify-slack" ${p.notify_slack_task_done !== false ? 'checked' : ''}>
        <span>Task results in Slack</span></label>
      <label class="person-notify" title="Bot messages to them are copied to their Tico DM in Slack. Conversations they start in Slack always answer there">
        <input type="checkbox" role="switch" class="people-switch" id="person-notify-bots" ${p.notify_slack_bot_messages !== false ? 'checked' : ''}>
        <span>Bot messages in Slack</span></label>
      <div class="person-muted" id="person-muted" ${p.notify_slack_bot_messages === false ? 'hidden' : ''}></div>
      ${S.me?.id === p.id ? `<label class="person-notify" title="A browser notification for new threads that need you (a question, a mention, a DM, a held message), while Tico is in the background. Your browser asks permission the first time">
        <input type="checkbox" role="switch" class="people-switch" id="person-notify-browser" ${p.notify_browser === true ? 'checked' : ''}>
        <span>Browser notifications</span></label>` : ''}
      <label class="person-notify" title="One email an hour for the threads you follow on digest that have new messages for you: each title, its unread count and a link. Never the messages or who wrote them">
        <input type="checkbox" role="switch" class="people-switch" id="person-email-digest" ${p.email_digest !== false ? 'checked' : ''}>
        <span>Email digests</span></label>
      <label class="person-notify" title="Each morning the Assistant drafts their update from what everyone may see: their shared tasks and goals, their decisions, comments and answers on those tasks, the meetings they were in that are not private, and their reports' updates. Never their private chats or approvals. It posts as theirs, and they can edit or replace it; once they post their own words that day, there is no draft">
        <input type="checkbox" role="switch" class="people-switch" id="person-update-draft" ${p.update_draft !== false ? 'checked' : ''}>
        <span>Assistant drafts my update</span></label>
      <div class="err" role="status" id="person-notify-status"></div></section>` : ''}
  </div>
  <div id="pane-pslack" ${tab === 'slack' ? '' : 'hidden'}>
    <section class="card conv"><header><h2>Tico in Slack</h2></header>
      <div id="person-slack">Loading…</div></section>
  </div>`;
  void headGoalsLoad('human:' + p.id);
  void personAccessLoad(p);
  const dmButton = $('#person-dm');
  if (dmButton) dmButton.onclick = () => { location.hash = THREADS_NEW + '?to=' + encodeURIComponent('human:' + p.id); };
  $('#ptabs').onclick = (ev: Event) => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-pt]'); if (!b) return;
    location.hash = b.dataset.pt === 'assistant' ? ASSISTANT : `#/person/${encodeURIComponent(p.id)}${b.dataset.pt === 'profile' ? '' : '/' + b.dataset.pt}`;
  };
  const more = $('#goal-more');
  if (more) more.onclick = () => {
    const rest = $('#goal-rest'), open = rest.hidden;
    rest.hidden = !open; more.textContent = open ? 'less' : 'more'; more.setAttribute('aria-expanded', String(open));
  };
  const notifySwitch = (id: string, key: 'notify_slack_task_done' | 'notify_slack_bot_messages' | 'update_draft' | 'email_digest') => {
    const box = $(id);
    if (box) box.onchange = async () => {
      const value = box.checked, status = $('#person-notify-status');
      box.disabled = true; status.textContent = '';
      try {
        const row = await post(`/v2/humans/${encodeURIComponent(p.id)}`, {[key]: value});
        p[key] = row[key];
        box.checked = row[key] !== false;
      } catch (e) {
        box.checked = !value;
        status.textContent = errorMessage(e, 'Could not save.');
      } finally { box.disabled = false; }
    };
  };
  notifySwitch('#person-notify-slack', 'notify_slack_task_done');
  notifySwitch('#person-notify-bots', 'notify_slack_bot_messages');
  notifySwitch('#person-update-draft', 'update_draft');
  notifySwitch('#person-email-digest', 'email_digest');
  const browser = $('#person-notify-browser');
  if (browser) pushKeep(p.notify_browser === true);
  if (browser) browser.onchange = async () => {
    const value = browser.checked, status = $('#person-notify-status');
    status.textContent = '';
    if (value && !('Notification' in window)) { browser.checked = false; status.textContent = 'This browser has no notifications.'; return; }
    if (value && Notification.permission !== 'granted' && (Notification.permission === 'denied' || await Notification.requestPermission() !== 'granted')) {
      browser.checked = false; status.textContent = 'Your browser is not allowing notifications for Tico.'; return;
    }
    browser.disabled = true;
    try {
      const row = await post(`/v2/humans/${encodeURIComponent(p.id)}`, {notify_browser: value});
      p.notify_browser = row.notify_browser === true; S.notifyBrowser = p.notify_browser; browser.checked = p.notify_browser;
      if (p.notify_browser) pushKeep(true); else void pushUnsubscribe().catch(() => {});
    } catch (e) {
      browser.checked = !value; status.textContent = errorMessage(e, 'Could not save.');
    } finally { browser.disabled = false; }
  };
  const botsSwitch = $('#person-notify-bots');
  if (botsSwitch) botsSwitch.addEventListener('change', () => { $('#person-muted').hidden = !botsSwitch.checked; });
  // Muted bots: their messages stay in Tico while the other bots still reach Slack.
  const mutedPaint = () => {
    const box = $('#person-muted');
    if (!box) return;
    const muted = p.slack_muted_bots || [];
    const rest = shownEmps().filter(e => !muted.includes(e.id));
    box.innerHTML = `<span class="muted">Muted</span>
      ${muted.map(slug => { const e = botRow(slug);
        return `<span class="chip">${avatar(slug, 18)}<span>${e ? shownName(e) : esc(slug)}</span><button type="button" class="people-chip-x" data-unmute="${esc(slug)}" aria-label="Unmute ${empName(slug)}">×</button></span>`; }).join('')}
      ${rest.length ? `<select id="person-mute-add" aria-label="Mute a bot in Slack"><option value="">${muted.length ? '+ Bot' : 'None · mute a bot'}</option>
        ${rest.map(e => `<option value="${esc(e.id)}">${shownName(e)}</option>`).join('')}</select>` : ''}`;
    const save = async (next: string[]) => {
      const status = $('#person-notify-status');
      box.querySelectorAll('button,select').forEach((el: HTMLButtonElement | HTMLSelectElement) => el.disabled = true); status.textContent = '';
      try { p.slack_muted_bots = (await post(`/v2/humans/${encodeURIComponent(p.id)}`, {slack_muted_bots: next})).slack_muted_bots; }
      catch (e) { status.textContent = errorMessage(e, 'Could not save.'); }
      mutedPaint();
    };
    box.querySelectorAll('[data-unmute]').forEach((b: HTMLButtonElement) => b.onclick = () => save(muted.filter(x => x !== b.dataset.unmute)));
    const add = $('#person-mute-add');
    if (add) add.onchange = () => add.value && save([...muted, add.value]);
  };
  mutedPaint();
  // The GitHub login a review request names: it puts them on the task linked to that pull request.
  const githubPaint = (editing: boolean) => {
    const box = $('#person-github');
    if (!box) return;
    if (!editing) {
      box.innerHTML = `${p.github ? `<a href="https://github.com/${encodeURIComponent(p.github)}" target="_blank" rel="noopener">@${esc(p.github)}</a>`
          : '<span class="muted">None set.</span>'}
        ${edit ? `<button class="linkish person-edit-btn" type="button" data-github-edit>${p.github ? 'Edit' : 'Add'}</button>` : ''}`;
      const open = box.querySelector('[data-github-edit]');
      if (open) open.onclick = () => githubPaint(true);
      return;
    }
    box.innerHTML = `<form class="person-github-form">
        <input type="text" name="github" maxlength="40" aria-label="GitHub login" placeholder="GitHub login" value="${esc(p.github || '')}"
          pattern="@?[A-Za-z0-9][A-Za-z0-9\\-]{0,38}" autocomplete="off" spellcheck="false">
        <button class="primary" type="submit">Save</button><button class="ghost" type="button" data-cancel>Cancel</button>
        <span class="muted" data-status></span></form>`;
    const form = box.querySelector('form');
    form.github.focus();
    form.querySelector('[data-cancel]').onclick = () => githubPaint(false);
    form.onsubmit = async (ev: Event) => {
      ev.preventDefault();
      const status = form.querySelector('[data-status]');
      form.querySelectorAll('button').forEach((b: HTMLButtonElement) => b.disabled = true);
      status.textContent = 'Saving…';
      try {
        p.github = (await post(`/v2/humans/${encodeURIComponent(p.id)}`, {github: form.github.value.trim()})).github;
        githubPaint(false);
      } catch (e) {
        status.innerHTML = `<span class="err">${esc(errorMessage(e, ''))}</span>`;
        form.querySelectorAll('button').forEach((b: HTMLButtonElement) => b.disabled = false);
      }
    };
  };
  githubPaint(false);
  // Their Home under the profile: their posts and update cards, never an empty timeline.
  threadMount($('#person-home-thread'), {home: 'human:' + p.id, card: true, header: 'compact', actsHost: $('#person-home-acts'),
    hideGone: $('#person-home'), compactEmpty: true});
  const cards = $('#pane-profile').querySelectorAll('section.card');
  const goalsCard = [...cards].find(el => el.querySelector('h2')?.textContent === 'Goals');
  const notesCard = [...cards].find(el => el.querySelector('h2')?.textContent === 'Notes');
  if (goalsCard) bindFieldEditor(goalsCard, async text => {
    const row = await post(`/v2/humans/${encodeURIComponent(p.id)}`, {goals: text});
    p.goals = row.goals; pagePerson(p.id, 'profile');
  });
  if (notesCard) bindFieldEditor(notesCard, async text => {
    const row = await post(`/v2/humans/${encodeURIComponent(p.id)}`, {notes: text});
    p.notes = row.notes; pagePerson(p.id, 'profile');
  });
  // The top of a human's page is their name and goal; the title and
  // description are the About card's, edited in place there.
  const editHead = $('#person-edit');
  if (editHead) editHead.onclick = () => {
    const host = $('#person-id');
    editHead.hidden = true;
    host.innerHTML = `<form class="task person-head-form">
        <input type="text" name="title" maxlength="120" aria-label="Title" placeholder="Title, e.g. Frontend engineer" value="${esc(p.title || '')}">
        <textarea name="about" rows="3" maxlength="2000" aria-label="Description" placeholder="What they do, in a sentence or two.">${esc(p.about || '')}</textarea>
        <div class="row"><button class="primary" type="submit">Save</button><button class="ghost" type="button" data-cancel>Cancel</button>
          <span class="muted" data-status></span></div></form>`;
    const form = host.querySelector('form');
    form.about.focus();
    form.querySelector('[data-cancel]').onclick = () => pagePerson(p.id, tab);
    form.onsubmit = async (ev: Event) => {
      ev.preventDefault();
      form.querySelectorAll('button').forEach((b: HTMLButtonElement) => b.disabled = true);
      form.querySelector('[data-status]').textContent = 'Saving…';
      try {
        const row = await post(`/v2/humans/${encodeURIComponent(p.id)}`, {title: form.title.value, about: form.about.value});
        p.title = row.title; p.about = row.about;
        pagePerson(p.id, tab);
      } catch (e) {
        form.querySelector('[data-status]').innerHTML = `<span class="err">${esc(errorMessage(e, ''))}</span>`;
        form.querySelectorAll('button').forEach((b: HTMLButtonElement) => b.disabled = false);
      }
    };
  };
  $('#person-task-add').onclick = () => personTaskModal(p);
  void personTasksLoad(p);
  if (tab === 'slack') personSlackLoad(p);
}
// Shown only to whoever manages this person: the read is refused to everyone else.
async function personAccessLoad(p: RosterPerson) {
  const data = await teammateAccessRead('human', p.id), card = $('#person-access');
  if (!data || !card || !$('#person-access-text')) return;
  $('#person-access-text').textContent = accessSummary(data);
  card.hidden = false;
  $('#person-access-edit').onclick = () => teammateEditAccess('human', p.id, p.name || p.id, () => personAccessLoad(p));
}
// The person's tasks as last read and mended since: their rows, where the catch-up resumes, and how far the pages go
// (task-api.ts taskPageNext: More reads on).
let PERSON_LIST: ({id: string; rows: TaskBriefV3[]; cursor: string; paging: Promise<unknown> | null} & TaskPaging<TaskBriefV3>) | null = null;
// Its reads against its live events (task-api.ts TaskFeed): a read that lands late undoes no newer event.
let PERSON_FEED = taskFeedNew();
// What the list holds: every task the person owns, open, snoozed or finished (status=all).
const personOwns = (id: string) => (t: TaskBriefV3) => t.owner_id === 'human:' + id;
async function personTasksLoad(p: RosterPerson) {
  PERSON_TASKS = p.id;
  const box = $('#person-tasks'); if (!box) return;
  const owner = 'human:' + p.id;
  if (PERSON_LIST?.id !== p.id) { PERSON_LIST = null; PERSON_FEED = taskFeedNew(); }
  const feed = PERSON_FEED, ticket = taskFeedRead(feed);
  const read = await taskBriefsPage({owner, status: 'all'}).catch(() => null);
  if (!$('#person-tasks') || PERSON_TASKS !== p.id || PERSON_FEED !== feed) return;
  const held = read ? taskFeedLanded(feed, ticket, read.cursor) : (taskFeedFailed(feed, ticket), null);
  if (read && !held) return;   // a newer read is out
  PERSON_LIST = read ? {id: p.id, rows: read.tasks, cursor: read.cursor, more: read.more && !!read.tasks.length, edge: read.tasks.at(-1) ?? null, paging: null} : null;
  // What came while the read was out goes over it: a task removed meanwhile stays removed.
  const bulk = read && held ? taskRowsLive(read.tasks, held, personOwns(p.id)) === 'bulk' : false;
  personTasksDraw(p, read?.tasks || []);
  if (bulk) void personTasksReload();
}
function personTasksDraw(p: RosterPerson, all: TaskBriefV3[]) {
  if (!$('#person-tasks')) return;
  const active = all.filter(t => t.status === 'open');
  const finished = all.filter(t => t.status !== 'open');
  const first = firstName(p.name) || p.id;
  $('#person-tasks').innerHTML = (active.length ? `<div class="v2-group">${active.map(t => taskRowHTML(t)).join('')}</div>`
      : `<div class="empty">No active tasks. <a href="#" data-person-task-add>Give ${esc(first)} a task</a>.</div>`)
    + (finished.length ? `<details class="person-tasks-done"><summary class="muted">Finished <span class="cnt">${finished.length}</span></summary>${
        finished.map(t => taskRowHTML(t, {look: 'archive'})).join('')}</details>` : '');
  const list = PERSON_LIST?.id === p.id ? PERSON_LIST : null;
  if (list?.more) $('#person-tasks').insertAdjacentHTML('beforeend', `<div class="tl-more" id="person-task-more"><button type="button" class="ghost"${list.paging ? ' disabled' : ''}>More</button></div>`);
  const link = $('#person-tasks [data-person-task-add]');
  if (link) link.onclick = (ev: Event) => { ev.preventDefault(); personTaskModal(p); };
  const more = $('#person-task-more button');
  if (more && list) more.onclick = () => void personTasksMore(p, list);
}
// More: the next page after the last row the server gave, in the list's own order; what came meanwhile goes over it.
async function personTasksMore(p: RosterPerson, list: NonNullable<typeof PERSON_LIST>) {
  if (list.paging) return;
  const still = () => PERSON_LIST === list && PERSON_TASKS === list.id && !!$('#person-tasks');
  list.paging = taskPageNext(list, list.rows, {owner: 'human:' + p.id, status: 'all', sort: 'queue'}, PERSON_FEED, personOwns(p.id), taskQueueOrder, still);
  personTasksDraw(p, list.rows);
  let held: Awaited<ReturnType<typeof taskPageNext>> = null;
  try { held = await (list.paging as ReturnType<typeof taskPageNext>); }
  catch (e) { toast(errorMessage(e, 'More did not load.'), true); }
  finally { list.paging = null; }
  if (!still()) return;
  const bulk = Array.isArray(held) && taskRowsLive(list.rows, held, personOwns(p.id), taskQueueOrder) === 'bulk';
  personTasksDraw(p, list.rows);
  if (bulk) void personTasksReload();
}
// The live stream's task events, each in place by personOwns (refresh.ts liveTasksApply); nothing is read for one.
function personTasksLive(events: LiveTaskEvent[]) {
  events = taskFeedEvents(PERSON_FEED, events);
  const list = PERSON_LIST, p = list && PERSON_TASKS === list.id && (S.people || []).find(row => row.id === list.id);
  if (!list || !p || !$('#person-tasks')) return;
  const moved = taskRowsLive(list.rows, events, personOwns(list.id));
  if (moved === 'bulk') personTasksReload();
  else if (moved) personTasksDraw(p, list.rows);
}
// The 2-minute refresh, a bulk change and this page's own writes: what changed since the list's cursor
// (GET /v2/tasks?changed_after); a full read only without a cursor or when the server says it is too old.
async function personTasksReload() {
  const p = PERSON_TASKS && (S.people || []).find(row => row.id === PERSON_TASKS);
  if (!p || !$('#person-tasks')) return;
  const list = PERSON_LIST;
  if (!list || list.id !== p.id) { void personTasksLoad(p); return; }
  try {
    const out = await taskRowsCatchUp(list.rows, list.cursor, {owner: 'human:' + p.id, status: 'all'}, personOwns(p.id), () => PERSON_LIST === list, PERSON_FEED);
    if (!out) return;
    if (out === 'reset') { void personTasksLoad(p); return; }
    list.cursor = out.cursor;
    if (out.moved) personTasksDraw(p, list.rows);
    if (out.again) void personTasksReload();
  } catch { /* the next refresh tries again */ }
}
// "+ Add task" on a human's page: the give-a-task form in a modal, assigned to them, added by you.
function personTaskModal(p: RosterPerson) {
  let d = $('#person-task-modal');
  if (!d) {
    d = document.createElement('dialog'); d.id = 'person-task-modal'; d.className = 'tmodal';
    d.setAttribute('aria-labelledby', 'person-task-title');
    d.addEventListener('click', (ev: Event) => { if (ev.target === d) d.close(); });
    document.body.appendChild(d);
  }
  d.innerHTML = `<div class="tmodal-head"><span class="who">Give a task</span><span class="spacer"></span>
      <span>assigned to ${esc(firstName(p.name) || p.id)}, added by you</span>
      <button class="ghost tmodal-x" type="button" data-modal-close aria-label="Close">✕</button></div>
    <div class="tmodal-body">
      <form class="task" id="person-task">
        <div class="r1" style="grid-template-columns:1fr"><input type="text" name="title" required aria-label="Title" placeholder="Start with a verb: Email, Ask, Send…"></div>
        <textarea name="body" required aria-label="Details" placeholder="The ask in the first line, then any details."></textarea>
        <div class="r3"><button class="primary" type="submit">Create task</button>
          <button class="ghost" type="button" data-modal-close>Cancel</button><span class="muted" id="person-task-msg"></span></div>
      </form></div>`;
  d.querySelectorAll('[data-modal-close]').forEach((b: HTMLButtonElement) => b.onclick = () => d.close());
  const form = $('#person-task', d);
  form.onsubmit = async (ev: Event) => {
    ev.preventDefault();
    const btn = form.querySelector('[type=submit]'), msg = $('#person-task-msg', d);
    const title = form.title.value.trim(), body = form.body.value.trim();
    if (!title) return;
    btn.disabled = true; msg.textContent = 'Creating…';
    try {
      await cloudCompose('/v2/tasks', {title, body, owner: 'human:' + p.id}, []);
      toast(`Task for ${p.name || p.id} created`);
      d.close();
      await v2Refresh();
      void personTasksReload();
    } catch (e) { msg.innerHTML = `<span class="err">${esc(errorMessage(e, ''))}</span>`; btn.disabled = false; }
  };
  if (!d.open) d.showModal();
  form.title.focus();
}
async function personSlackLoad(p: RosterPerson) {
  const el = $('#person-slack'); if (!el) return;
  try {
    const d = await get<{threads?: HumanSlackThread[] | null}>(`/v2/humans/${encodeURIComponent(p.id)}/slack`);
    const threads = d.threads || [];
    if (!threads.length) {
      el.innerHTML = '<div class="empty">No Tico Slack DMs yet. When a bot messages them in Slack (through Tico), the thread lands here, including their replies.</div>';
      return;
    }
    el.innerHTML = threads.map(t => {
      const msgs = t.messages || [];
      return `<div class="conv-bots-one"><div class="conv-run-head">${avatar(t.bot, 20, stateOf(t.bot))}
          <a href="#/bot/${encodeURIComponent(t.bot)}">${esc(t.display_name || botDisplayName(t.bot))}</a>
          <span class="spacer" style="flex:1"></span><span class="tnum">${esc(ago(t.last_message_at))}</span></div>
        ${msgs.map(m => `<div class="bubble ${String(m.sender_id || '').startsWith('bot:') ? 'bot reply' : 'you'}">
            <span class="who">${esc(actorLabel(m.sender_id))}</span>
            ${m.refs?.read_back ? `<div class="read-back" style="white-space:pre-line">${esc(m.body || '')}</div>`
              : messageMd('md', m.body || '', m.sender_id)}${chatCopyHTML(m.body)}</div>`).join('') || '<div class="empty">No messages yet.</div>'}
        </div>`;
    }).join('');
  } catch (e) {
    el.innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`;
  }
}
