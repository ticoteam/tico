/* ui/app/mail.ts — Message bots page (mail read live from Gmail)
   The one page header: the title, Messages and Instructions as tabs when a mailbox has instructions (one view is no
   tabs), search and the shared filter icon (labels and attachments, chips under the header as on Tasks). The mailboxes
   list sits beside the messages only when there are two or more to choose from. Every empty state is said once.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The mail API's answers as this page reads them: /v2/mail/mailboxes, /v2/mail/messages, /v2/mail/threads/:id, /v2/mail/agent.
interface MailPageBox {
  address: string;
  name?: string | null;
  synced_at?: string | null;
  error?: string | null;
  message_count?: number | null;
  agent_bot?: boolean;
  agent_instructions?: string | null;
}
interface MailPageAttachment {
  name?: string | null;
  filename?: string | null;
}
interface MailPageMessage {
  thread_id: string;
  mailbox: string;
  date?: string | null;
  epoch?: number | null;
  from_header?: string | null;
  from_addr?: string | null;
  subject?: string | null;
  snippet?: string | null;
  labels?: string[] | null;
  has_attachment?: boolean;
  body?: string | null;
  body_truncated?: boolean;
  attachments?: MailPageAttachment[] | null;
  rule_hits?: { id?: string | null }[] | null;
}
interface MailPageList {
  messages?: MailPageMessage[] | null;
  next_cursor?: string | null;
}
interface MailPageThread {
  messages: MailPageMessage[];
}
interface MailPageAgent {
  updated_at?: string | null;
  instructions?: string | null;
}
// The page's state from the hash (mailParams); a patch may name any field, or leave it out.
interface MailPageState {
  mailbox: string;
  q: string;
  label: string;
  attach: boolean;
  thread: string;
  tbox: string;
  view: string;
}
type MailPageOpts = { [K in keyof MailPageState]?: MailPageState[K] | undefined };
interface MailPageAcc {
  key: string;
  messages: MailPageMessage[];
  cursor: string | null;
}

// ----------------------------------------------------------------- Mail
// Server-stored copies only (GET /api/v2/mail). Bodies stay in <pre>; never md() them.
// The label filter's choices ('' is any label).
const MAIL_CHIPS: [string, string][] = [
  ['', 'Any label'], ['INBOX', 'Inbox'], ['UNREAD', 'Unread'], ['STARRED', 'Starred'],
];
const MAIL_LABEL: Record<string, string> = {INBOX: 'Messages', UNREAD: 'Unread', STARRED: 'Starred', SENT: 'Sent',
  IMPORTANT: 'Important', DRAFT: 'Drafts', SPAM: 'Spam', TRASH: 'Trash'};
let MAIL_ACC: MailPageAcc = {key: '', messages: [], cursor: null};
// The filter popover's anchor (task-list-menus.ts keeps it here to know a second click on the icon closes it).
const MAIL_MENU: TasksMenuHost = {};
// A page element the drawn markup must hold: a missing one throws a plain Error that names it.
function mailNeed<T extends Element = HTMLElement>(selector: string, root: ParentNode = document): T {
  const el = root.querySelector<T>(selector);
  if (!el) throw new Error(`${selector} is not on the page`);
  return el;
}
function mailForget() { MAIL_ACC = {key: '', messages: [], cursor: null}; }
function mailParams(): MailPageState {
  const raw = (S.route.startsWith(MAIL) ? S.route.slice(MAIL.length) : '');
  const q = new URLSearchParams(raw.startsWith('?') ? raw.slice(1) : '');
  return {mailbox: q.get('mailbox') || '', q: q.get('q') || '', label: q.get('label') || '',
          attach: q.get('attach') === '1', thread: q.get('thread') || '', tbox: q.get('tbox') || '',
          view: q.get('view') === 'agent' ? 'agent' : 'inbox'};
}
function mailHash(st: MailPageOpts) {
  const q = new URLSearchParams();
  if (st.mailbox) q.set('mailbox', st.mailbox);
  if (st.q) q.set('q', st.q);
  if (st.label) q.set('label', st.label);
  if (st.attach) q.set('attach', '1');
  if (st.view === 'agent') q.set('view', 'agent');
  // `tbox` is which mailbox the open thread belongs to: reading across every mailbox still has to
  // survive a reload, and the list the thread was picked from may be pages away by then.
  if (st.thread) { q.set('thread', st.thread); if (st.tbox) q.set('tbox', st.tbox); }
  const s = q.toString();
  return s ? MAIL + '?' + s : MAIL;
}
function mailBoxParam(mailbox: string | null | undefined): string {
  return mailbox && mailbox !== 'all' ? mailbox : '';
}
function mailFilterKey(st: MailPageState) {
  return [st.mailbox, st.q, st.label, st.attach ? '1' : ''].join('\0');
}
function mailGo(patch: MailPageOpts) {
  const next = mailHash({...mailParams(), ...patch});
  if (next !== S.route) location.hash = next;
  else pageMail();
}
function mailWhen(iso: string | null | undefined, epoch: number | null | undefined): string {
  const d = iso ? new Date(iso) : (epoch ? new Date(epoch * 1000) : null);
  if (!d || Number.isNaN(+d)) return '';
  const same = d.toDateString() === new Date().toDateString();
  return same ? d.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})
              : d.toLocaleDateString([], {month: 'short', day: 'numeric'});
}
function mailFrom(m: MailPageMessage): string | null | undefined {
  const h = (m.from_header || '').trim();
  if (h) return h.replace(/\s*<[^>]+>\s*$/, '').trim() || m.from_addr;
  return m.from_addr || '';
}
function mailLabelName(label: string): string | undefined {
  if (MAIL_LABEL[label]) return MAIL_LABEL[label];
  if (label.startsWith('CATEGORY_')) return '';
  return label.startsWith('hub/') ? label.slice(4) : label;
}
// A message's labels as pills, less Inbox: nearly every message has it, and a pill is for what stands out.
function mailTags(labels: string[] | null | undefined): string {
  return (labels || []).filter(l => l !== 'INBOX').map(l => { const n = mailLabelName(l); return n ? `<span class="pill">${esc(n)}</span>` : ''; }).join('');
}
// When the server last read a mailbox's Gmail, for its tooltip, or why that read failed.
function mailFresh(box: MailPageBox | null | undefined): string {
  if (!box) return '';
  if (box.error) return box.error;
  return box.synced_at ? `Checked ${ago(box.synced_at)}` : 'Not checked yet';
}
function mailQuery(st: MailPageState, cursor: string | null | undefined): string {
  const qs = new URLSearchParams();
  const mailbox = mailBoxParam(st.mailbox);
  if (mailbox) qs.set('mailbox', mailbox);
  if (st.q) qs.set('q', st.q);
  if (st.label) qs.set('label', st.label);
  if (st.attach) qs.set('has_attachment', 'true');
  qs.set('limit', '50');
  if (cursor) qs.set('cursor', cursor);
  return '/v2/mail/messages?' + qs.toString();
}
async function mailFill(st: MailPageState, append: boolean): Promise<void> {
  const page = await get<MailPageList>(mailQuery(st, append ? MAIL_ACC.cursor : ''));
  MAIL_ACC.messages = append ? MAIL_ACC.messages.concat(page.messages || []) : (page.messages || []);
  MAIL_ACC.cursor = page.next_cursor || null;
}
function mailDrawList(st: MailPageState, listed: MailPageMessage[], boxes: MailPageBox[]): void {
  // An empty list is the page's one empty line, and the reading pane beside it folds away.
  $('#mail-split')?.classList.toggle('mail-none', !listed.length);
  if (!listed.length) {
    mailNeed('#mail-list').innerHTML = `<div class="empty-state">${st.q || st.label || st.attach
      ? 'No matches'
      : 'No messages'}</div>`;
    return;
  }
  const filterBox = mailBoxParam(st.mailbox);
  mailNeed('#mail-list').innerHTML = listed.map(m => `<button type="button" class="mail-row${st.thread === m.thread_id && (!filterBox || filterBox === m.mailbox) ? ' on' : ''}"
      data-thread="${esc(m.thread_id)}" data-mailbox="${esc(m.mailbox)}">
      <span class="when" title="${esc(fmt(m.date))}">${esc(mailWhen(m.date, m.epoch))}</span>
      <span>
        <div class="who">${esc(mailFrom(m))}</div>
        <div class="subj">${esc(m.subject || '(no subject)')}</div>
        <div class="snip">${esc(m.snippet || '')}</div>
        <div class="tags">${mailTags(m.labels)}${m.has_attachment ? '<span class="pill">Attachment</span>' : ''}</div>
      </span></button>`).join('') +
    (MAIL_ACC.cursor ? '<div class="mail-more"><button type="button" class="ghost" data-mail-more>Load more</button></div>' : '');
}
async function mailMore(): Promise<void> {
  const st = mailParams();
  if (!MAIL_ACC.cursor) return;
  const load = MAIL_LOAD;
  try { await mailFill(st, true); }
  catch (e) { toast(errorMessage(e, ''), true); return; }
  if (load !== MAIL_LOAD) return;
  mailDrawList(st, MAIL_ACC.messages, []);
}
// The filter: one popover (task-list-menus.ts tasksMenu) with the label as a choice of one and attachments as a tick.
function mailFilterMenu(anchor: HTMLElement): void {
  const st = mailParams();
  tasksMenu(MAIL_MENU, anchor, `<div class="tl-menu" role="group" aria-label="Filter">
    <div class="tl-menu-h" aria-hidden="true">Label</div>
    <div class="tl-menu-list">${MAIL_CHIPS.map(([id, label]) => `<label class="tl-opt"><input type="radio" name="mail-label" value="${id}"${st.label === id ? ' checked' : ''}><span>${esc(label)}</span></label>`).join('')}</div>
    <div class="tl-menu-sep" role="separator"></div>
    <label class="tl-opt"><input type="checkbox" name="mail-attach"${st.attach ? ' checked' : ''}><span>Has attachment</span></label>
  </div>`, pop => {
    pop.onchange = ev => {
      const t = eventElement(ev);
      if (!(t instanceof HTMLInputElement)) return;
      if (t.name === 'mail-attach') mailGo({attach: t.checked, thread: ''});
      else mailGo({label: t.value, thread: ''});
    };
  });
}
// The badge on the filter icon and a chip under the header for each filter in force.
function mailFilterPaint(st: MailPageState): void {
  const label = MAIL_CHIPS.find(([id]) => id && id === st.label)?.[1] || (st.label ? mailLabelName(st.label) || st.label : '');
  const chip = (key: string, k: string, v: string) => `<span class="tl-chip" data-chip="${key}"><button type="button" class="tl-chip-main" data-mail-edit aria-haspopup="true" title="${esc(k ? `${k}: ${v}` : v)}">${k ? `<span class="k">${esc(k)}</span>` : ''}<span class="v">${esc(v)}</span></button><button type="button" class="tl-chip-x" data-mail-drop="${key}" aria-label="Remove ${esc(k || v)}">${TL_ICON.x}</button></span>`;
  const chips = [label ? chip('label', 'Label', label) : '', st.attach ? chip('attach', '', 'Has attachment') : ''].filter(Boolean);
  const bar = $('#mail-filters'), host = $('#mail-chips'), badge = $('#mail-filter .tl-fbadge');
  if (host) host.innerHTML = chips.join('');
  if (bar) bar.hidden = !chips.length;
  if (badge) { badge.hidden = !chips.length; badge.textContent = String(chips.length); }
  $('#mail-filter')?.setAttribute('aria-label', chips.length ? `Filter, ${chips.length} on` : 'Filter');
}
// The mail page's search box (drawn with the page): a timer and the list read it when they run.
function mailSearchBox(): HTMLInputElement | null { return $('#mail-q') as HTMLInputElement | null; }
async function pageMail(): Promise<void> {
  if (S.me && !S.me.mail_access) { location.hash = TASKS; return; }
  mailNeed('#main').classList.add('mail-layout');
  const st = mailParams();
  const load = ++MAIL_LOAD;
  if (!$('#mail-page')) {
    mailNeed('#main').innerHTML = `<div id="mail-page">
      ${pageHead({title: 'Message bots', hook: 'mail-head', after: betaPill('message_bots'), tabsHook: 'mail-tabs', tabsAttrs: 'aria-label="Mailbox view"',
        tools: '<input id="mail-q" type="search" autocomplete="off" placeholder="Search messages" aria-label="Search messages">'
          + `<button type="button" class="tl-ib" id="mail-filter" aria-haspopup="true" aria-expanded="false" aria-label="Filter" title="Filter">${filterIcon(13)}<span class="tl-fbadge tnum" aria-hidden="true" hidden></span></button>`})}
      <div class="tl-bar mail-filter-bar" id="mail-filters" hidden><div class="tl-filters" role="group" aria-label="Filters"><span class="tl-chipset" id="mail-chips"></span></div></div>
      <p class="err mail-fresh" id="mail-fresh" role="status" hidden></p>
      <div class="mail-shell" id="mail-shell">
        <nav class="mail-boxes" aria-label="Mailboxes" hidden><div class="mail-box-list" id="mail-box-list"></div></nav>
        <div class="mail-main">
          <h2 class="mail-current" id="mail-current" hidden></h2>
          <div id="mail-inbox-view" role="tabpanel">
            <div class="mail-split" id="mail-split">
              <div class="mail-list" id="mail-list">${skelRows(4)}</div>
              <div class="mail-thread" id="mail-thread"></div>
            </div>
          </div>
          <section class="mail-agent-view" id="mail-agent-view" role="tabpanel" hidden></section>
        </div>
      </div>
      <div id="task-filter-pop" class="tl-pop" popover></div>
    </div>`;
    const tabBar = $('.mail-tabs') as HTMLElement;
    const searchBox = mailNeed<HTMLInputElement>('#mail-q');
    ($('#mail-box-list') as HTMLElement).onclick = ev => {
      const button = eventElement(ev)?.closest<HTMLElement>('[data-mailbox]'); if (!button) return;
      mailGo({mailbox: button.dataset.mailbox, thread: '', tbox: '', view: 'inbox'});
    };
    tabBar.onclick = ev => {
      const button = eventElement(ev)?.closest<HTMLElement>('[data-mail-view]'); if (!button) return;
      mailGo({view: button.dataset.mailView});
    };
    tabBar.onkeydown = ev => {
      if (!['ArrowLeft', 'ArrowRight'].includes(ev.key)) return;
      const tabs = [...document.querySelectorAll<HTMLElement>('.mail-tab:not([hidden])')];
      const index = tabs.indexOf(document.activeElement as HTMLElement);
      if (index < 0) return;
      ev.preventDefault();
      const next = tabs[(index + (ev.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length]!;
      next.focus(); next.click();
    };
    searchBox.oninput = () => {
      clearTimeout(MAIL_SEARCH);
      MAIL_SEARCH = setTimeout(() => mailGo({q: mailNeed<HTMLInputElement>('#mail-q').value.trim(), thread: ''}), 280);
    };
    searchBox.onkeydown = ev => {
      if (ev.key === 'Enter') { ev.preventDefault(); clearTimeout(MAIL_SEARCH); mailGo({q: searchBox.value.trim(), thread: ''}); }
    };
    const filterBtn = mailNeed('#mail-filter'), pop = mailNeed('#task-filter-pop');
    filterBtn.onclick = () => mailFilterMenu(filterBtn);
    ($('#mail-chips') as HTMLElement).onclick = ev => {
      const drop = eventElement(ev)?.closest<HTMLElement>('[data-mail-drop]');
      if (drop) { mailGo(drop.dataset.mailDrop === 'attach' ? {attach: false, thread: ''} : {label: '', thread: ''}); filterBtn.focus(); return; }
      if (eventElement(ev)?.closest('[data-mail-edit]')) mailFilterMenu(filterBtn);
    };
    pop.addEventListener('keydown', tasksMenuKeys);
    pop.addEventListener('toggle', (ev: Event & {newState?: string}) => {
      filterBtn.setAttribute('aria-expanded', String(ev.newState === 'open'));
      if (ev.newState === 'closed' && (pop.contains(document.activeElement) || document.activeElement === document.body)) filterBtn.focus();
    });
    ($('#mail-list') as HTMLElement).onclick = ev => {
      if (eventElement(ev)?.closest('[data-mail-more]')) { mailMore(); return; }
      const row = eventElement(ev)?.closest<HTMLElement>('[data-thread]'); if (!row) return;
      const cur = mailParams();
      mailGo({mailbox: cur.mailbox || row.dataset.mailbox, thread: row.dataset.thread,
              tbox: row.dataset.mailbox});
    };
    ($('#mail-thread') as HTMLElement).onclick = ev => {
      if (eventElement(ev)?.closest('.mail-back')) mailGo({thread: ''});
    };
  }
  if (load !== MAIL_LOAD) return;
  let boxes: MailPageBox[] = [];
  try { boxes = (await get<{mailboxes?: MailPageBox[] | null}>('/v2/mail/mailboxes')).mailboxes || []; }
  catch (e) { if (load === MAIL_LOAD) mailNeed('#mail-list').innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`; return; }
  if (load !== MAIL_LOAD) return;
  const mine = (S.me?.email || '').toLowerCase();
  // Mail is read from one mailbox's Gmail at a time: no mailbox (or the old "all") opens the reader's own.
  if ((!st.mailbox || st.mailbox === 'all') && boxes.length) {
    const prefer = boxes.find(b => b.address === mine) || boxes[0]!;
    mailGo({mailbox: prefer.address});
    return;
  }
  const filterBox = mailBoxParam(st.mailbox);
  const box = boxes.find(b => b.address === filterBox);
  // The mailboxes list only when there is a choice; with one, the heading over the messages names it.
  const several = boxes.length > 1;
  $('#mail-box-list')!.innerHTML = boxes.map(b => `<button class="mail-box-link${st.mailbox === b.address ? ' on' : ''}" type="button" data-mailbox="${esc(b.address)}" title="${esc([b.address, mailFresh(b)].join(' · '))}"${st.mailbox === b.address ? ' aria-current="true"' : ''}>
      <span class="name">${esc(b.name || b.address)}</span>${b.agent_bot ? '<span class="agent-mark" aria-hidden="true">mail</span>' : ''}</button>`).join('');
  ($('.mail-boxes') as HTMLElement).hidden = !several;
  $('#mail-shell')!.classList.toggle('one', !several);
  const current = $('#mail-current') as HTMLElement;
  current.hidden = several || !box;
  current.textContent = box ? (box.name ? `${box.name} · ${box.address}` : box.address) : '';
  current.title = mailFresh(box);
  // Messages and Instructions are tabs only when the mailbox has instructions: one view is no tabs.
  const agent = !!box?.agent_instructions;
  if (st.view === 'agent' && !agent) { mailGo({view: 'inbox'}); return; }
  const head = $('.mail-head') as HTMLElement;
  pageHeadSet(head, 'tabs', agent ? [['inbox', 'Messages'], ['agent', 'Instructions']].map(([v, label]) =>
    `<button class="ph-tab mail-tab" type="button" role="tab" data-mail-view="${v}" aria-controls="mail-${v === 'agent' ? 'agent' : 'inbox'}-view" aria-selected="${st.view === v}" tabindex="${st.view === v ? 0 : -1}">${label}</button>`).join('') : '');
  ($('#mail-inbox-view') as HTMLElement).hidden = st.view === 'agent';
  const agentView = $('#mail-agent-view') as HTMLElement;
  agentView.hidden = st.view !== 'agent';
  // Search and the filter narrow the messages, so they rest while Instructions is open.
  for (const el of [$('#mail-q'), $('#mail-filter')] as HTMLInputElement[]) el.disabled = st.view === 'agent';
  mailFilterPaint(st);
  if (st.view === 'agent') {
    const target = agentView;
    target.innerHTML = '<div class="empty">Loading…</div>';
    try {
      const response = await get<MailPageAgent>(`/v2/mail/agent?mailbox=${encodeURIComponent(filterBox)}`);
      if (load !== MAIL_LOAD) return;
      target.title = response.updated_at ? `Updated ${ago(response.updated_at)}` : '';
      target.innerHTML = `<div class="mail-agent-text">${esc(response.instructions)}</div>`;
    } catch (e) {
      if (load === MAIL_LOAD) target.innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`;
    }
    return;
  }
  const qbox = mailSearchBox(); if (qbox && qbox.value !== st.q) qbox.value = st.q;
  // Freshness is in the mailbox's tooltip; only a failed sync is said on the page, and it stays until the sync works.
  const failed = (filterBox ? [box] : boxes).filter((b): b is MailPageBox => !!b?.error);
  const fresh = $('#mail-fresh') as HTMLElement;
  fresh.hidden = !failed.length;
  fresh.textContent = failed.map(b => `${b.name || b.address}: ${mailFresh(b)}`).join(' · ');
  const key = mailFilterKey(st);
  if (MAIL_ACC.key !== key) MAIL_ACC = {key, messages: [], cursor: null};
  if (!MAIL_ACC.messages.length) {
    try { await mailFill(st, false); }
    catch (e) { if (load === MAIL_LOAD) mailNeed('#mail-list').innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`; return; }
  }
  if (load !== MAIL_LOAD) return;
  const listed = MAIL_ACC.messages;
  const split = $('#mail-split');
  if (split) split.classList.toggle('thread-open', !!st.thread);
  mailDrawList(st, listed, boxes);
  if (!st.thread) {
    mailNeed('#mail-thread').innerHTML = '';
    return;
  }
  mailNeed('#mail-thread').innerHTML = '<div class="empty">Loading thread…</div>';
  try {
    const t = await get<MailPageThread>(`/v2/mail/threads/${encodeURIComponent(st.thread)}?mailbox=${encodeURIComponent(filterBox || st.tbox || listed.find(m => m.thread_id === st.thread)?.mailbox || '')}`);
    if (load !== MAIL_LOAD) return;
    const subject = (t.messages[0] && t.messages[0].subject) || '(no subject)';
    mailNeed('#mail-thread').innerHTML = `<button type="button" class="mail-back">← Messages</button>
      <h2>${esc(subject)}</h2>
      ${t.messages.map(m => `<article class="mail-msg">
        <div class="meta"><span class="who">${esc(mailFrom(m))}</span>
          <span class="muted">${esc(m.from_addr)}</span>
          <span class="muted tnum" title="${esc(fmt(m.date))}">${esc(fmt(m.date) || mailWhen(m.date, m.epoch))}</span></div>
        <div class="tags" style="margin-bottom:8px">${mailTags(m.labels)}${(m.rule_hits || []).map(h => `<span class="pill">${esc(h.id || 'rule')}</span>`).join('')}</div>
        <pre class="mail-body">${esc(m.body || '')}</pre>
        ${m.body_truncated ? '<p class="muted" style="margin:6px 0 0">Body was truncated; open it in Gmail for the rest.</p>' : ''}
        ${(m.attachments || []).length ? `<p class="muted" style="margin:8px 0 0">${m.attachments!.map(a => esc(a.name || a.filename || 'attachment')).join(' · ')}</p>` : ''}
      </article>`).join('')}`;
  } catch (e) {
    if (load === MAIL_LOAD) mailNeed('#mail-thread').innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`;
  }
}
