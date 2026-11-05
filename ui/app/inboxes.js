/* ui/app/inboxes.js — The inbox kinds' pages under Tools (ui/app/tools.js routes them): one mailbox, Tico address or
   phone number, mailing address and letters out, their settings page and Messages. They were Settings › Inboxes; its
   list is the Tools list now (GET /api/v2/tool-index), so this file keeps only the pages and the registry they join.
   Addresses: #/tools/mailbox/<address or id>[/activity], #/tools/number/<id>, #/tools/address/<id>,
   #/tools/address/outbox, #/tools/settings[/<section>] (one section a channel) and #/tools/<view> (a page a file adds,
   such as Messages).

   Each kind is its own file, loaded after this one, and joins through the calls below, made when that file loads:

   inboxesKind(key, spec) — one kind of inbox. `key` is what its pages are drawn as; INBOX_SEGMENTS maps it to its
     Tools address.
     label   its short name;  order: where it sorts among the kinds (Add tool lists them in this order)
     detail(host, id, ctx)    → Promise: draws one inbox into host, with inboxesHead() and inboxesCard()
     activity(host, id, ctx)  optional → Promise: its Activity tab (#/tools/<segment>/<id>/activity)
     add     optional {label, admin, open(ctx)}: its line in Tools' Add tool (`admin`: shown to admins only)

   inboxesSetting(key, spec) — one card on Tools › Settings.
     section 'email' | 'texts' | 'letters-in' | 'letters-out';  order: its place in the section
     render(host, ctx) → Promise: draws the card into host (an empty host is left out)

   inboxesPanel(key, spec) — one card added to another kind's page, below that kind's own cards.
     kinds   the kind keys whose pages show it (['mailbox'], …);  order: its place among the panels
     render(host, inbox, ctx) → Promise: draws into host; `inbox` is what that kind's detail() resolved with
     (every kind resolves {kind, id, name, address}, plus its own fields: a mailbox adds the GET /v2/mailboxes/<id> body)

   inboxesView(key, spec) — a page of its own at #/tools/<key>.
     label   the page's name;  render(host, ctx) → Promise: draws the page into host, which is kept for the whole visit
     so a later draw can update what an earlier one drew

   ctx: {admin, me, once(name, load) (one load a draw, shared by every kind), live() (false once another draw began),
   refresh() (draw again), go(view) (open another view: '' the Tools list, '<kind>/<id>', 'settings/<section>')}.
   Helpers for kind files: inboxesHead, inboxesCard, inboxesSoon, inboxesPill, inboxesLink, inboxesAutomations,
   inboxesAutomationsLink. Styles: ui/styles/inboxes.css, one commented block a file.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// 'settings' or 'settings/<section>', '<view>' (inboxesView), or '<kind>/<id>[/activity]' (one inbox). Set by Tools' router.
let INBOXES_VIEW = '';
const INBOX_KINDS = {}, INBOX_VIEWS = {}, INBOX_SETTINGS = [], INBOX_PANELS = [];
const INBOX_SECTIONS = [['email', 'Email'], ['texts', 'Texts'], ['letters-in', 'Letters in'], ['letters-out', 'Letters out']];
const INBOX_STATUS = {ok: ['ok', 'Connected'], attention: ['fail', 'Needs attention'], pending: ['waiting', 'Pending'], off: ['', 'Not in use']};
// A kind's segment in its Tools address. Tico addresses and phone numbers are one page (inDetail tells them apart);
// letters out is the company's one outbox, so it has a fixed id.
const INBOX_SEGMENTS = {mailbox: 'mailbox', tico: 'number', number: 'number', postal: 'address', outbox: 'address'};
const INBOX_OUTBOX_ID = 'outbox';
const IB = {run: 0};

function inboxesKind(key, spec) { INBOX_KINDS[key] = {key, ...spec}; }
function inboxesView(key, spec) { INBOX_VIEWS[key] = {key, ...spec}; }
function inboxesSetting(key, spec) { INBOX_SETTINGS.push({key, ...spec}); INBOX_SETTINGS.sort((a, b) => a.order - b.order); }
function inboxesPanel(key, spec) { INBOX_PANELS.push({key, ...spec}); INBOX_PANELS.sort((a, b) => a.order - b.order); }

// The Tools address of an inbox ('mailbox', id), of Tools › Settings ('settings', section) or of a view ('messages').
function inboxesLink(kind, id) {
  if (kind === 'settings') return '#/tools/settings' + (id ? '/' + encodeURIComponent(id) : '');
  if (kind === 'outbox') return '#/tools/address/' + INBOX_OUTBOX_ID;
  if (!INBOX_SEGMENTS[kind]) return '#/tools/' + kind;
  return `#/tools/${INBOX_SEGMENTS[kind]}${id ? '/' + encodeURIComponent(id) : ''}`;
}
const inboxesPill = (status, text) => {
  const [tone, label] = INBOX_STATUS[status] || ['', status];
  return `<span class="pill${tone ? ' ' + tone : ''}" data-ib-status="${esc(status)}">${esc(text || label)}</span>`;
};

// The view a Tools address names (its parts after #/tools/), or null when it is not one of these pages.
function inboxesRoute(parts) {
  const [seg, id, tab] = parts;
  if (seg === 'settings') return INBOX_SECTIONS.some(([k]) => k === id) ? 'settings/' + id : 'settings';
  if (INBOX_VIEWS[seg] && !id) return seg;
  if (!id) return null;
  if (seg === 'address' && id === INBOX_OUTBOX_ID) return 'outbox/company';
  const kind = {mailbox: 'mailbox', number: 'number', address: 'postal'}[seg];
  if (!kind) return null;
  return kind + '/' + id + (tab === 'activity' && INBOX_KINDS[kind]?.activity ? '/activity' : '');
}
// A view's address: what ctx.go opens.
function inboxesHref(view) {
  if (!view) return TOOLS_ROUTE;
  const [head, ...rest] = view.split('/');
  if (head === 'settings') return inboxesLink('settings', rest[0] || '');
  if (INBOX_VIEWS[head]) return inboxesLink(head);
  if (head === 'outbox') return inboxesLink('outbox');
  return inboxesLink(head, rest[0] || '') + (rest[1] === 'activity' ? '/activity' : '');
}
function inboxesGo(view) {
  const href = inboxesHref(view);
  if (location.hash === href) return inboxesRender();
  location.hash = href;
}

function inboxesCtx(run) {
  const loads = {};
  return {
    admin: settingsIsAdmin(), me: S.me, run,
    live: () => run === IB.run,
    once: (name, load) => (loads[name] ||= Promise.resolve().then(load)),
    refresh: () => inboxesRender(),
    go: inboxesGo,
  };
}

async function inboxesRender() {
  const el = $('#tools-inbox'); if (!el) return;
  inboxesBind(el);
  const run = ++IB.run, ctx = inboxesCtx(run);
  el.dataset.ibShown = INBOXES_VIEW;
  const [head, ...rest] = INBOXES_VIEW.split('/');
  el.dataset.ibView = head;
  if (!(head && INBOX_KINDS[head])) delete el.dataset.ibOpen;
  if (head === 'settings') return inboxesSettingsPage(el, rest[0] || '', ctx);
  if (head && INBOX_VIEWS[head] && !rest.length) return inboxesPage(el, head, ctx);
  if (head && INBOX_KINDS[head]) return inboxesDetail(el, head, rest[0] || '', rest[1] === 'activity', ctx);
}

function inboxesBind(el) {
  if (el.dataset.ibBound) return;
  el.dataset.ibBound = '1';
  el.addEventListener('click', event => {
    const b = event.target.closest('button[data-ib-jump]');
    if (b && el.contains(b)) $('#inboxes-' + b.dataset.ibJump)?.scrollIntoView({block: 'start', behavior: 'smooth'});
  });
}
const inboxesCrumb = name => `<nav class="tl-crumb" aria-label="Breadcrumb"><a href="${TOOLS_ROUTE}">Tools</a><span aria-hidden="true">›</span><span data-ib-crumb>${esc(name)}</span></nav>`;

// ---------------------------------------------------------------------------------------------------------- Settings
// Built once a visit (and again when the viewer's role changes), then drawn card by card in place: each card keeps
// what it shows until its own read answers, so a redraw never takes a control away mid-save.
async function inboxesSettingsPage(el, section, ctx) {
  const page = el.querySelector('[data-ib-settings-page]');
  if (!page || page.dataset.ibAdmin !== String(ctx.admin)) el.innerHTML = `<div class="ib-settings" data-ib-settings-page data-ib-admin="${ctx.admin}">
    ${inboxesCrumb('Settings')}
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

// A card on Tools › Settings, or on an inbox's page.
const inboxesCard = (id, title, body, action = '') =>
  `<section class="card ib-card"${id ? ` id="${esc(id)}"` : ''}><header><h2>${esc(title)}</h2>${action}</header>${body}</section>`;
// A placeholder: the card's title and its Coming soon pill, nothing under them.
const inboxesSoon = (id, title) => inboxesCard(id, title, '', '<span class="pill" data-ib-soon>Coming soon</span>');

// ---------------------------------------------------------------------------------------------------------- A view
// A page another file adds (inboxesView): its frame is built once a visit and every draw renders into the same host.
async function inboxesPage(el, key, ctx) {
  const view = INBOX_VIEWS[key];
  if (!el.querySelector(`[data-ib-page="${CSS.escape(key)}"] [data-ib-page-body]`)) {
    el.innerHTML = `<div class="ib-page" data-ib-page="${esc(key)}">${inboxesCrumb(view.label)}<div data-ib-page-body></div></div>`;
  }
  const host = el.querySelector('[data-ib-page-body]');
  try { await view.render(host, ctx); }
  catch (e) { if (ctx.live() && host.isConnected) host.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

// ---------------------------------------------------------------------------------------------------------- One inbox
async function inboxesDetail(el, kind, id, activity, ctx) {
  const spec = INBOX_KINDS[kind], open = kind + '/' + id + (activity ? '/activity' : '');
  if (el.dataset.ibOpen !== open || !el.querySelector('[data-ib-detail-body]')) {
    el.dataset.ibOpen = open;
    el.innerHTML = `<div class="ib-detail" data-ib-detail="${esc(kind)}" data-ib-tab="${activity ? 'activity' : 'overview'}"
      data-ib-tabs="${spec.activity ? esc(inboxesLink(kind, id)) : ''}">
      ${inboxesCrumb(id)}
      <div data-ib-detail-body>${skelRows(3, {card: true, dot: false})}</div><div data-ib-panels></div></div>`;
  }
  const host = el.querySelector('[data-ib-detail-body]'), panels = el.querySelector('[data-ib-panels]');
  let inbox;
  try { inbox = await (activity ? spec.activity : spec.detail)(host, id, ctx); }
  catch (e) { if (ctx.live() && host.isConnected) host.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (!ctx.live() || !inbox || activity) return;
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
// An inbox page's title: its name, status and one line, its actions (Check now, Stop using in Tico), and Overview /
// Activity when its kind has an Activity tab.
function inboxesHead(host, {name, status, statusText, sub = '', actions = ''}) {
  const page = host.closest('.ib-detail'), crumb = page?.querySelector('[data-ib-crumb]');
  if (crumb) crumb.textContent = name;
  const base = page?.dataset.ibTabs, tab = page?.dataset.ibTab;
  const tabs = base ? `<nav class="tl-tabs" aria-label="${esc(name)}">${[['overview', base, 'Overview'], ['activity', base + '/activity', 'Activity']]
    .map(([k, href, label]) => `<a href="${esc(href)}" data-ib-tab-link="${k}"${tab === k ? ' class="cur" aria-current="page"' : ''}>${label}</a>`).join('')}</nav>` : '';
  return `<header class="ib-title" id="inbox-head"><div class="ib-title-text"><h1>${esc(name)}</h1>
      <p class="ib-title-sub">${inboxesPill(status, statusText)}${sub ? ` <span class="muted" data-ib-sub>${sub}</span>` : ''}</p></div>
    ${actions ? `<div class="ib-actions">${actions}</div>` : ''}</header>${tabs}`;
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
  `<a class="ghost settings-head-act" href="#/automations${address ? '?q=' + encodeURIComponent(address) : ''}">Open</a>`;
