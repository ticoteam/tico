/* ui/app/inboxes.js — Settings > Inboxes: every inbox Tico uses, in one list, with a page each and one Settings page.
   Its addresses: #/settings/inboxes (the list, or first run until company email is connected),
   #/settings/inboxes/settings[/<section>] (Inboxes > Settings, one section a channel) and
   #/settings/inboxes/<kind>/<id> (one inbox). The old Mailboxes, Email & Phone and Mail tabs redirect here (router.js).

   This file is the shell, the list, first run and the router. Each kind of inbox is its own file, loaded after this
   one, and joins through three calls made when that file loads:

   inboxesKind(key, spec) — one kind of inbox. `key` is the address segment (#/settings/inboxes/<key>/<id>).
     group   'email' | 'texts' | 'letters-in' | 'letters-out': the Kind filter it is listed under
     label   its short name in the Kind column
     order   where its rows sort among the kinds
     rows(ctx)              → Promise<Row[]>: the inboxes of this kind the viewer may see ([] when none)
     detail(host, id, ctx)  → Promise: draws one inbox into host, with inboxesHead() and inboxesCard()
     add     optional {label, admin, open(ctx)}: its line in Add inbox (`admin`: shown to admins only)
   Row: {id, name, sub, status: 'ok'|'attention'|'pending'|'off', statusText, latest (ms since epoch or null),
         usedBy: {people, bots, names[]}, search (extra words the search box matches)}

   inboxesSetting(key, spec) — one card in Inboxes > Settings.
     section 'email' | 'texts' | 'letters-in' | 'letters-out';  order: its place in the section
     render(host, ctx) → Promise: draws the card into host (an empty host is left out)

   inboxesStep(key, spec) — one step of first run.
     order, title, optional (an optional step can be dismissed; first run ends when every required step is done)
     done(ctx) → Promise<bool>;  render(host, ctx): the step's controls

   inboxesPanel(key, spec) — one card added to another kind's inbox page, below that kind's own cards.
     kinds   the kind keys whose pages show it (['mailbox'], …);  order: its place among the panels
     render(host, inbox, ctx) → Promise: draws into host; `inbox` is what that kind's detail() resolved with
     (every kind resolves {kind, id, name, address}, plus its own fields: a mailbox adds the GET /v2/mailboxes/<id> body)

   ctx: {admin, me, once(name, load) (one load a draw, shared by every kind), live() (false once another draw began),
   refresh() (draw again), go(view)}. Helpers for kind files: inboxesHead, inboxesCard, inboxesSoon, inboxesPill,
   inboxesLink, inboxesAutomations, inboxesAutomationsLink, inboxesUsedLine. Styles: ui/styles/inboxes.css, one
   commented block a file.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// '' the list (or first run), 'settings' or 'settings/<section>', or '<kind>/<id>' (one inbox).
let INBOXES_VIEW = '';
const INBOX_KINDS = {}, INBOX_SETTINGS = [], INBOX_STEPS = [], INBOX_PANELS = [];
const INBOX_GROUPS = [['email', 'Email'], ['texts', 'Texts in'], ['letters-in', 'Letters in'], ['letters-out', 'Letters out']];
const INBOX_SECTIONS = [['email', 'Email'], ['texts', 'Texts'], ['letters-in', 'Letters in'], ['letters-out', 'Letters out']];
const INBOX_STATUS = {ok: ['ok', 'Connected'], attention: ['fail', 'Needs attention'], pending: ['waiting', 'Pending'], off: ['', 'Not in use']};
const INBOX_PAGE = 25;
const INBOX_SKIP_KEY = 'tico.inboxes.skip', INBOX_DISMISS_KEY = 'tico.inboxes.dismissed';
const IB = {run: 0, rows: [], errors: [], shown: INBOX_PAGE, q: '', kind: '', status: '', used: ''};

function inboxesKind(key, spec) { INBOX_KINDS[key] = {key, ...spec}; }
function inboxesSetting(key, spec) { INBOX_SETTINGS.push({key, ...spec}); INBOX_SETTINGS.sort((a, b) => a.order - b.order); }
function inboxesStep(key, spec) { INBOX_STEPS.push({key, ...spec}); INBOX_STEPS.sort((a, b) => a.order - b.order); }
function inboxesPanel(key, spec) { INBOX_PANELS.push({key, ...spec}); INBOX_PANELS.sort((a, b) => a.order - b.order); }

const inboxesLink = (kind, id) => `#/settings/inboxes/${kind}${id ? '/' + encodeURIComponent(id) : ''}`;
const inboxesPill = (status, text) => {
  const [tone, label] = INBOX_STATUS[status] || ['', status];
  return `<span class="pill${tone ? ' ' + tone : ''}" data-ib-status="${esc(status)}">${esc(text || label)}</span>`;
};
// "Ana, Ops bot and 2 more", or "" when nobody yet.
function inboxesUsedLine(used) {
  const names = used?.names || [], total = (used?.people || 0) + (used?.bots || 0);
  if (!total) return '';
  const more = total - names.length;
  return names.join(', ') + (more > 0 ? ` and ${more} more` : '');
}
function inboxesStore(key, value) {
  try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch { /* not kept */ }
}
function inboxesStored(key) { try { return localStorage.getItem(key); } catch { return null; } }
const inboxesDismissed = () => { try { return JSON.parse(inboxesStored(INBOX_DISMISS_KEY) || '[]'); } catch { return []; } };

// The view an address names after #/settings/inboxes/: a section name opens Settings at it, <kind>/<id> one inbox.
function inboxesRoute(rest) {
  const parts = String(rest || '').replace(/\/+$/, '').split('/').filter(Boolean).map(p => decodeURIComponent(p));
  if (!parts.length) return '';
  if (parts[0] === 'settings') return INBOX_SECTIONS.some(([k]) => k === parts[1]) ? 'settings/' + parts[1] : 'settings';
  if (parts.length === 1 && INBOX_SECTIONS.some(([k]) => k === parts[0])) return 'settings/' + parts[0];
  return parts.slice(0, 2).join('/');
}
// The address the shown view writes (settingsAddress).
function inboxesAddress() {
  if (!INBOXES_VIEW) return '';
  const [head, ...rest] = INBOXES_VIEW.split('/');
  return '/' + [head, ...rest.map(encodeURIComponent)].join('/');
}
function inboxesGo(view) {
  INBOXES_VIEW = view || '';
  settingsAddress();
  return renderSettingsInboxes();
}

function inboxesCtx(run) {
  const loads = {};
  return {
    admin: settingsIsAdmin(), me: S.me, run,
    live: () => run === IB.run,
    once: (name, load) => (loads[name] ||= Promise.resolve().then(load)),
    refresh: () => renderSettingsInboxes(),
    go: inboxesGo,
  };
}

async function renderSettingsInboxes() {
  const el = $('#set-inboxes'); if (!el) return;
  inboxesBind(el);
  const run = ++IB.run, ctx = inboxesCtx(run);
  const [head, ...rest] = INBOXES_VIEW.split('/');
  el.dataset.ibView = head || 'list';
  if (!(head && INBOX_KINDS[head])) delete el.dataset.ibOpen;
  if (head === 'settings') return inboxesSettingsPage(el, rest[0] || '', ctx);
  if (head && INBOX_KINDS[head]) return inboxesDetail(el, head, rest.join('/'), ctx);
  if (head) { INBOXES_VIEW = ''; settingsAddress(); }
  return inboxesHome(el, ctx);
}

function inboxesBind(el) {
  if (el.dataset.ibBound) return;
  el.dataset.ibBound = '1';
  el.addEventListener('input', event => {
    if (event.target.matches('[data-ib-q]')) { IB.q = event.target.value; IB.shown = INBOX_PAGE; inboxesDraw(el); }
  });
  el.addEventListener('change', event => {
    const f = event.target.closest('[data-ib-filter]');
    if (f) { IB[f.dataset.ibFilter] = f.value; IB.shown = INBOX_PAGE; inboxesDraw(el); }
  });
  el.addEventListener('click', event => {
    const b = event.target.closest('button, a[data-ib-go]');
    if (!b || !el.contains(b)) return;
    if (b.hasAttribute('data-ib-more')) { IB.shown += INBOX_PAGE; inboxesDraw(el); return; }
    if (b.hasAttribute('data-ib-clear')) { Object.assign(IB, {q: '', kind: '', status: '', used: '', shown: INBOX_PAGE}); inboxesDraw(el, true); return; }
    if (b.dataset.ibJump) { $('#inboxes-' + b.dataset.ibJump)?.scrollIntoView({block: 'start', behavior: 'smooth'}); return; }
    if (b.dataset.ibAddKind) { b.closest('details')?.removeAttribute('open'); INBOX_KINDS[b.dataset.ibAddKind]?.add?.open(inboxesCtx(IB.run)); return; }
    if (b.hasAttribute('data-ib-skip')) { inboxesStore(INBOX_SKIP_KEY, '1'); void renderSettingsInboxes(); return; }
    if (b.hasAttribute('data-ib-setup')) { inboxesStore(INBOX_SKIP_KEY, null); void renderSettingsInboxes(); return; }
    if (b.dataset.ibDismiss) {
      inboxesStore(INBOX_DISMISS_KEY, JSON.stringify([...new Set([...inboxesDismissed(), b.dataset.ibDismiss])]));
      b.closest('[data-ib-step]')?.remove();
      return;
    }
  });
}

// ---------------------------------------------------------------------------------------------------------- Home
// First run while a required step is undone (an admin only, until they skip it), else the list. The demo starts set
// up with no Google connection, so it always opens on the list.
async function inboxesHome(el, ctx) {
  if (!el.querySelector('[data-ib-home]')) el.innerHTML = `<div data-ib-home>${skelRows(4, {card: true, dot: false})}</div>`;
  const rows = inboxesLoad(ctx);
  if (ctx.admin && !S.config?.demo && inboxesStored(INBOX_SKIP_KEY) !== '1') {
    const required = INBOX_STEPS.filter(s => !s.optional);
    const done = await Promise.all(required.map(s => Promise.resolve(s.done(ctx)).catch(() => false)));
    if (!ctx.live()) return;
    if (done.some(d => !d)) return inboxesFirstRun(el, ctx);
  }
  await rows;
  if (!ctx.live()) return;
  inboxesDraw(el, true);
}

async function inboxesLoad(ctx) {
  const kinds = Object.values(INBOX_KINDS).sort((a, b) => a.order - b.order);
  const got = await Promise.allSettled(kinds.map(k => Promise.resolve(k.rows(ctx))));
  if (!ctx.live()) return;
  IB.rows = []; IB.errors = [];
  got.forEach((r, n) => {
    const kind = kinds[n];
    if (r.status === 'rejected') { IB.errors.push(`${kind.label}: ${r.reason?.message || r.reason}`); return; }
    for (const row of r.value || []) IB.rows.push({...row, kind: kind.key, group: kind.group, kindLabel: row.kindLabel || kind.label});
  });
}

function inboxesMatch(row) {
  const used = row.usedBy || {}, people = used.people || 0, bots = used.bots || 0;
  if (IB.kind && row.group !== IB.kind) return false;
  if (IB.status && row.status !== IB.status) return false;
  if (IB.used === 'people' && !people) return false;
  if (IB.used === 'bots' && !bots) return false;
  if (IB.used === 'nobody' && (people || bots)) return false;
  const q = IB.q.trim().toLowerCase();
  if (!q) return true;
  return [row.name, row.sub, row.kindLabel, row.statusText, ...(used.names || []), row.search].join(' ').toLowerCase().includes(q);
}

const inboxesOption = (value, label, cur) => `<option value="${esc(value)}"${value === cur ? ' selected' : ''}>${esc(label)}</option>`;

function inboxesAddMenu(ctx) {
  const adds = Object.values(INBOX_KINDS).filter(k => k.add && (!k.add.admin || ctx.admin)).sort((a, b) => a.order - b.order);
  if (!adds.length) return '';
  return `<details class="ib-add" data-ib-add><summary class="primary">Add inbox</summary><div class="ib-add-menu" role="menu">
    ${adds.map(k => `<button type="button" role="menuitem" data-ib-add-kind="${esc(k.key)}">${esc(k.add.label)}</button>`).join('')}</div></details>`;
}

function inboxesDraw(el, full = false) {
  const home = el.querySelector('[data-ib-home]'); if (!home) return;
  const ctx = inboxesCtx(IB.run);
  if (full || !home.querySelector('[data-ib-list]')) {
    home.innerHTML = `<div data-ib-list>
      <header class="ib-head"><h2>Inboxes <span class="muted tnum" data-ib-count></span></h2>
        <div class="ib-head-act"><a class="ghost" href="#/settings/inboxes/settings" data-ib-settings>Settings</a>${inboxesAddMenu(ctx)}</div></header>
      <div class="ib-filters" role="search">
        <input type="search" data-ib-q placeholder="Search address, person or bot" aria-label="Search inboxes" value="${esc(IB.q)}" autocomplete="off" spellcheck="false">
        <select data-ib-filter="kind" aria-label="Kind">${inboxesOption('', 'All kinds', IB.kind)}${INBOX_GROUPS.map(([k, l]) => inboxesOption(k, l, IB.kind)).join('')}</select>
        <select data-ib-filter="status" aria-label="Status">${inboxesOption('', 'Any status', IB.status)}${
          [['ok', 'Connected'], ['attention', 'Needs attention'], ['pending', 'Pending'], ['off', 'Not in use']].map(([k, l]) => inboxesOption(k, l, IB.status)).join('')}</select>
        <select data-ib-filter="used" aria-label="Used by">${inboxesOption('', 'Anyone', IB.used)}${
          [['people', 'People'], ['bots', 'Bots'], ['nobody', 'Nobody yet']].map(([k, l]) => inboxesOption(k, l, IB.used)).join('')}</select>
      </div>
      <div data-ib-errors></div>
      <div data-ib-table></div></div>`;
  }
  home.querySelector('[data-ib-count]').textContent = IB.rows.length ? String(IB.rows.length) : '';
  home.querySelector('[data-ib-errors]').innerHTML = IB.errors.map(e => `<p class="err" data-ib-error>${esc(e)}</p>`).join('');
  const host = home.querySelector('[data-ib-table]');
  if (!IB.rows.length) {
    host.innerHTML = `<div class="empty" data-ib-empty>No inboxes yet.${ctx.admin && inboxesStored(INBOX_SKIP_KEY) === '1'
      ? ' <button type="button" class="linkish" data-ib-setup>Set up inboxes</button>' : ''}</div>`;
    return;
  }
  const matched = IB.rows.filter(inboxesMatch);
  if (!matched.length) {
    host.innerHTML = '<div class="empty" data-ib-none>No inboxes match. <button type="button" class="linkish" data-ib-clear>Clear filters</button></div>';
    return;
  }
  const shown = matched.slice(0, IB.shown);
  host.innerHTML = `<div class="ib-scroll"><table class="settings-table ib-table"><thead><tr><th>Inbox</th><th>Kind</th><th>Status</th><th>Latest</th><th aria-hidden="true"></th></tr></thead>
    <tbody>${shown.map(r => {
      const used = inboxesUsedLine(r.usedBy), href = inboxesLink(r.kind, r.id);
      const sub = [r.sub, used].filter(Boolean).map(esc).join(' · ');
      return `<tr data-ib-row="${esc(r.kind + ':' + r.id)}" data-ib-kind="${esc(r.kind)}">
        <td class="ib-name"><a href="${href}">${esc(r.name)}</a>${sub ? `<span class="muted">${sub}</span>` : ''}</td>
        <td class="ib-kind">${esc(r.kindLabel)}</td>
        <td class="ib-status">${inboxesPill(r.status, r.statusText)}</td>
        <td class="ib-latest muted tnum">${r.latest ? esc(ago(new Date(r.latest).toISOString())) : '—'}</td>
        <td class="ib-chev" aria-hidden="true"><a href="${href}" tabindex="-1">›</a></td></tr>`;
    }).join('')}</tbody></table></div>
    ${matched.length > shown.length ? `<p class="ib-more muted">Showing ${shown.length} of ${matched.length} · <button type="button" class="linkish" data-ib-more>Show more</button></p>` : ''}`;
}

// ---------------------------------------------------------------------------------------------------------- First run
async function inboxesFirstRun(el, ctx) {
  const gone = new Set(inboxesDismissed());
  const steps = INBOX_STEPS.filter(s => !s.optional || !gone.has(s.key));
  el.querySelector('[data-ib-home]').innerHTML = `<section class="card ib-first" data-ib-first><header><h2>Set up inboxes</h2></header>
    <ol class="ib-steps">${steps.map((s, n) => `<li class="ib-step" data-ib-step="${esc(s.key)}">
      <div class="ib-step-head"><span class="ib-step-n" aria-hidden="true">${n + 1}</span><h3>${esc(s.title)}</h3>
        ${s.optional ? `<span class="muted">Optional</span><button type="button" class="ghost ib-dismiss" data-ib-dismiss="${esc(s.key)}" aria-label="Dismiss ${esc(s.title)}">×</button>` : ''}</div>
      <div class="ib-step-body" data-ib-step-body="${esc(s.key)}"></div></li>`).join('')}</ol>
    <footer class="ib-first-foot"><span class="muted">After setup this moves to Inboxes › Settings.</span>
      <button type="button" class="ghost" data-ib-skip>Skip for now</button></footer></section>`;
  await Promise.all(steps.map(async s => {
    const host = el.querySelector(`[data-ib-step-body="${CSS.escape(s.key)}"]`);
    try {
      const done = await Promise.resolve(s.done(ctx)).catch(() => false);
      if (!ctx.live() || !host?.isConnected) return;
      host.closest('[data-ib-step]').classList.toggle('done', !!done);
      await s.render(host, ctx, !!done);
    } catch (e) { if (host?.isConnected) host.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  }));
}

// ---------------------------------------------------------------------------------------------------------- Settings
async function inboxesSettingsPage(el, section, ctx) {
  el.innerHTML = `<div class="ib-settings" data-ib-settings-page>
    <nav class="ib-crumb" aria-label="Breadcrumb"><a href="#/settings/inboxes">Inboxes</a> / <span>Settings</span></nav>
    <nav class="ib-jump" aria-label="Sections">${INBOX_SECTIONS.map(([k, l]) => `<button type="button" class="linkish" data-ib-jump="${k}">${esc(l)}</button>`).join('')}</nav>
    ${INBOX_SECTIONS.map(([k, l]) => `<section class="ib-section" id="inboxes-${k}" data-ib-section="${k}"><h2>${esc(l)}</h2>
      ${INBOX_SETTINGS.filter(s => s.section === k).map(s => `<div data-ib-setting="${esc(s.key)}"></div>`).join('')}</section>`).join('')}
  </div>`;
  await Promise.all(INBOX_SETTINGS.map(async s => {
    const host = el.querySelector(`[data-ib-setting="${CSS.escape(s.key)}"]`);
    try { await s.render(host, ctx); }
    catch (e) { if (host?.isConnected) host.innerHTML = `<section class="card"><p class="err">${esc(e.message)}</p></section>`; }
  }));
  if (section && ctx.live()) $('#inboxes-' + section)?.scrollIntoView({block: 'start'});
}

// A card in Inboxes > Settings, or on an inbox's page.
const inboxesCard = (id, title, body, action = '') =>
  `<section class="card ib-card"${id ? ` id="${esc(id)}"` : ''}><header><h2>${esc(title)}</h2>${action}</header>${body}</section>`;
const inboxesSoon = (id, title, line = '') =>
  inboxesCard(id, title, `<p class="muted" data-ib-soon>${esc(line || 'Coming soon.')}</p>`, '<span class="pill">Coming soon</span>');

// ---------------------------------------------------------------------------------------------------------- One inbox
async function inboxesDetail(el, kind, id, ctx) {
  if (el.dataset.ibOpen !== kind + '/' + id || !el.querySelector('[data-ib-detail-body]')) {
    el.dataset.ibOpen = kind + '/' + id;
    el.innerHTML = `<div class="ib-detail" data-ib-detail="${esc(kind)}">
      <nav class="ib-crumb" aria-label="Breadcrumb"><a href="#/settings/inboxes">Inboxes</a> / <span data-ib-crumb>${esc(id)}</span></nav>
      <div data-ib-detail-body>${skelRows(3, {card: true, dot: false})}</div><div data-ib-panels></div></div>`;
  }
  const host = el.querySelector('[data-ib-detail-body]'), panels = el.querySelector('[data-ib-panels]');
  let inbox;
  try { inbox = await INBOX_KINDS[kind].detail(host, id, ctx); }
  catch (e) { if (ctx.live() && host.isConnected) host.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (!ctx.live() || !inbox) return;
  // Other files' cards for this kind's page (inboxesPanel), each in a host of its own drawn once a visit.
  const mine = INBOX_PANELS.filter(p => p.kinds.includes(kind));
  if (formBusy(panels)) return;   // a half-typed panel stays put
  panels.innerHTML = mine.map(p => `<div data-ib-panel="${esc(p.key)}"></div>`).join('');
  await Promise.all(mine.map(async p => {
    const slot = panels.querySelector(`[data-ib-panel="${CSS.escape(p.key)}"]`);
    try { await p.render(slot, inbox, ctx); }
    catch (e) { if (slot?.isConnected) slot.innerHTML = `<section class="card"><p class="err">${esc(e.message)}</p></section>`; }
  }));
}
// An inbox page's title: its name, status and one line, and its actions (Check connection, Stop using in Tico).
function inboxesHead(host, {name, status, statusText, sub = '', actions = ''}) {
  const crumb = host.closest('.ib-detail')?.querySelector('[data-ib-crumb]');
  if (crumb) crumb.textContent = name;
  return `<header class="ib-title" id="inbox-head"><div class="ib-title-text"><h2>${esc(name)}</h2>
      <p class="ib-title-sub">${inboxesPill(status, statusText)}${sub ? ` <span class="muted" data-ib-sub>${sub}</span>` : ''}</p></div>
    ${actions ? `<div class="ib-actions">${actions}</div>` : ''}</header>`;
}
// The message automations whose trigger hears `address`, for an inbox whose kind's page has no list of its own.
async function inboxesAutomations(ctx, address) {
  const all = await ctx.once('automations', () => get('/v2/automations?limit=500', {fresh: true}).then(d => d.automations || [], () => []));
  const want = String(address || '').toLowerCase();
  const mine = all.filter(a => a.trigger?.type === 'message' && String(a.trigger?.inbox || '').toLowerCase() === want);
  return mine.length ? `<ul class="ib-autos">${mine.map(a => `<li><a href="#/automations/${encodeURIComponent(a.id)}">${esc(a.name || a.id)}</a>${
      a.enabled === false ? ' <span class="muted">· off</span>' : ''}</li>`).join('')}</ul>`
    : '<p class="muted" data-ib-autos-none>None</p>';
}
const inboxesAutomationsLink = address =>
  `<a class="ghost settings-head-act" href="#/automations${address ? '?q=' + encodeURIComponent(address) : ''}">Automations</a>`;

// ---------------------------------------------------------------------------------------------------------- Give inboxes to bots
inboxesStep('bots', {order: 50, title: 'Give inboxes to bots', optional: true,
  done: async ctx => {
    const d = await ctx.once('mailboxes', () => get('/v2/mailboxes', {fresh: true}));
    return (d.mailboxes || []).some(m => m.used_by?.bots);
  },
  render: (host, ctx, done) => {
    host.innerHTML = done ? '<p class="muted">Done.</p>'
      : '<p><button type="button" class="ghost" data-ib-skip>Choose</button></p>';
  }});
