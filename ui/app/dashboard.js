/* ui/app/dashboard.js — Overview › Dashboard and team pages (#/team/<group>): a grid of pinned built-in cards and
   pages (backend/cards.py). Each card shows its reader only what they may already see; a person who arranges the
   place drags, sizes, adds and removes pins. The bot sidebar draws its card and page widgets with the same pieces.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const OVERVIEW_CAMPUS = OVERVIEW + '/campus';
const TEAM_PAGE = '#/team/';
const DASH_SIZES = [['small', 'Small'], ['medium', 'Medium'], ['large', 'Large']];
// {place, view, choices, root, menu}: the place on screen. A write's answer is drawn only while it is still the place.
let DASH = null;

const dashApi = place => '/v2/places/' + encodeURIComponent(place);
const dashTabsHTML = on => `<nav class="tabs slim strat-tabs dash-tabs" aria-label="Overview">${
  [['dashboard', OVERVIEW, 'Dashboard'], ['campus', OVERVIEW_CAMPUS, 'Campus']].map(([id, href, label]) =>
    `<a href="${href}"${on === id ? ' class="cur" aria-current="page"' : ''}>${label}</a>`).join('')}</nav>`;
const dashNum = n => n == null ? '–' : Number(n).toLocaleString(undefined, {maximumFractionDigits: 2});
const dashMoney = n => n == null ? '–' : '$' + Number(n).toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0});
const dashBot = slug => typeof botDisplayName === 'function' ? botDisplayName(slug) : slug;

// ---- one card's body, from what the server says it shows this reader
function dashBars(weeks) {
  const max = Math.max(1, ...weeks.map(w => w.done)), n = weeks.length, gap = 2, w = (100 - gap * (n - 1)) / n;
  const bars = weeks.map((x, i) => {
    const h = Math.max(x.done ? 3 : 1, x.done / max * 56);
    return `<rect x="${(i * (w + gap)).toFixed(2)}" y="${(60 - h).toFixed(2)}" width="${w.toFixed(2)}" height="${h.toFixed(2)}" rx="1"${i === n - 1 ? ' class="now"' : ''}><title>Week of ${esc(x.week)}: ${x.done}</title></rect>`;
  }).join('');
  return `<svg class="dash-bars" viewBox="0 0 100 60" preserveAspectRatio="none" role="img" aria-label="Done each week">${bars}</svg>`;
}
function dashSpark(values) {
  if (values.length < 2) return '';
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1) * 100).toFixed(2)},${(36 - (v - lo) / span * 32).toFixed(2)}`).join(' ');
  return `<svg class="dash-spark" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}"/></svg>`;
}
// Every task the reader may see, by status, the card's team only when it has one (ui/app/task-list.js filters).
const dashTasksHref = card => `${TASKS}?view=list&who=all${card.settings?.team ? '&team=' + encodeURIComponent(card.settings.team) : ''}`;
function dashPageRow(p) {
  return `<li><a href="#/page/${encodeURIComponent(p.file_id)}">${esc(p.title || 'Untitled')}</a>
    <span class="muted">${esc(dashBot(p.bot))} · ${agoSpan(p.updated_at)}</span></li>`;
}
const DASH_BODY = {
  tasks_by_status: (d, card) => `<div class="dash-stats">${[['open', 'Open'], ['doing', 'Doing'], ['waiting', 'Waiting'], ['done', 'Done this week']]
    .map(([k, label]) => `<a class="dash-stat" href="${dashTasksHref(card)}"><strong>${dashNum(d.counts[k])}</strong><span>${label}</span></a>`).join('')}</div>`,
  done_per_week: d => {
    const weeks = d.weeks || [], last = weeks[weeks.length - 1]?.done ?? 0, total = weeks.reduce((s, w) => s + w.done, 0);
    return `<div class="dash-big"><strong>${dashNum(last)}</strong><span class="muted">this week · ${dashNum(total)} in ${weeks.length} weeks</span></div>${dashBars(weeks)}`;
  },
  needs_you: d => `<a class="dash-big" href="${NEEDS_YOU}"><strong>${dashNum(d.count)}</strong><span class="muted">waiting on you</span></a>
    ${d.items.length ? `<ul class="dash-list">${d.items.map(it => `<li>${it.task_id ? `<a href="#/task/${encodeURIComponent(it.task_id)}">${esc(it.title || 'Untitled')}</a>` : `<a href="${NEEDS_YOU}">${esc(it.title || 'Untitled')}</a>`}</li>`).join('')}</ul>` : ''}`,
  spend: d => {
    const pct = d.budget ? Math.min(100, d.spent / d.budget * 100) : 0;
    return `<div class="dash-big"><strong>${dashMoney(d.spent)}</strong><span class="muted">${d.budget ? 'of ' + dashMoney(d.budget) : 'no budget'} · ${esc(d.month)}</span></div>
      ${d.budget ? `<div class="dash-meter${pct >= 90 ? ' hot' : ''}" role="meter" aria-valuemin="0" aria-valuemax="${d.budget}" aria-valuenow="${d.spent}" aria-label="Spend"><span style="width:${pct.toFixed(1)}%"></span></div>` : ''}`;
  },
  kpi: d => `<div class="dash-big"><strong>${dashNum(d.value)}${d.unit ? ` <small>${esc(d.unit)}</small>` : ''}</strong>
    <span class="muted">${esc(d.name)}${d.at ? ' · ' + esc(d.at) : ''}</span></div>${dashSpark(d.spark || [])}`,
  automation_runs: d => `<a class="dash-big" href="${AUTOMATIONS}"><strong>${dashNum(d.total)}</strong><span class="muted">runs in 24h${d.failed ? ` · <span class="dash-bad">${d.failed} failed</span>` : ''}${d.running ? ` · ${d.running} running` : ''}</span></a>
    ${d.runs.length ? `<ul class="dash-list">${d.runs.map(r => `<li><span class="dash-dot ${r.status === 'failed' ? 'bad' : r.status === 'succeeded' || r.status === 'done' ? 'ok' : ''}" aria-hidden="true"></span>${esc(r.name)} <span class="muted">${agoSpan(r.started_at)}</span></li>`).join('')}</ul>` : ''}`,
  latest_pages: d => d.pages.length ? `<ul class="dash-list dash-pages">${d.pages.map(dashPageRow).join('')}</ul>` : '<p class="muted dash-none">No pages yet</p>',
};
function dashCardBody(card) {
  if (card.hidden || !card.data) return '<p class="muted dash-none">Not shown to you</p>';
  return (DASH_BODY[card.template] || (() => ''))(card.data, card);
}
// A page pin: the page itself through pageCard (ui/app/pages.js) when this build has it, else a link to it.
function dashPageEl(page, size) {
  if (typeof window.pageCard === 'function') {
    try { return window.pageCard(page, {size}); } catch { /* the link below */ }
  }
  const a = document.createElement('a');
  a.className = 'dash-page-link'; a.href = '#/page/' + encodeURIComponent(page.file_id);
  a.innerHTML = `<span class="nav-icon" aria-hidden="true">article</span><span><strong>${esc(page.title || 'Untitled')}</strong>
    <span class="muted">${esc(dashBot(page.bot))} · Updated ${agoSpan(page.updated_at)}</span></span>`;
  return a;
}
// A card names its team, except on that team's own page.
const dashTeamShown = (card, view) => card.team_name && !(view?.kind === 'team' && card.settings?.team === view.group) ? card.team_name : '';
const dashPinTitle = (pin, view) => pin.card ? pin.card.heading + (dashTeamShown(pin.card, view) ? ' · ' + pin.card.team_name : '') : pin.page?.title || 'Page';
// Who sees a team page, in a few words: Everyone, the one team or person, else how many.
function dashAudienceWords(aud) {
  if (!aud || aud.everyone !== false) return 'Everyone';
  const teams = aud.teams || [], people = aud.people || [], bots = aud.bots || [];
  const groupName = id => (S.orgGroups || []).find(g => g.id === id)?.name || id;
  const personName = id => (S.people || []).find(p => p.id === id)?.name || id;
  if (teams.length === 1 && !people.length && !bots.length) return groupName(teams[0]);
  if (people.length === 1 && !teams.length && !bots.length) return personName(people[0]);
  const parts = [[teams.length, 'team'], [people.length, 'person'], [bots.length, 'bot']].filter(([n]) => n)
    .map(([n, w]) => `${n} ${n === 1 ? w : w === 'person' ? 'people' : w + 's'}`);
  return parts.join(', ') || 'Nobody';
}

// ---- the page
// A place seen before is drawn at once from what it showed last (S.lastRead.places), then read again; the grid is drawn
// again only when the answer differs, so its page widgets are not reloaded for nothing.
async function pageDashboard(place) {
  const team = place.startsWith('team:'), kept = (S.lastRead.places ||= {})[place];
  const main = $('#main'); main.classList.add('dash-layout');
  main.innerHTML = `<div class="dash" id="dash-root"><header class="dash-head">
      <h1>${team ? '' : 'Overview'}</h1>${team ? '' : dashTabsHTML('dashboard')}<span class="spacer"></span>
      <span class="dash-actions" hidden>${team ? '<button type="button" class="ghost dash-audience" data-dash-audience></button>' : ''}
        <button type="button" class="ghost" data-dash-add="card"><span class="dash-plus" aria-hidden="true">+</span>Card</button>
        <button type="button" class="ghost" data-dash-add="page"><span class="dash-plus" aria-hidden="true">+</span>Page</button></span></header>
    <div class="dash-grid" id="dash-grid">${kept ? '' : skelRows(4, {card: true})}</div>
    <dialog class="tmodal tmodal-shell dash-dialog" id="dash-dialog" aria-label="Card"></dialog></div>`;
  const state = DASH = {place, view: null, choices: null, root: $('#dash-root'), menu: null};
  dashWire(state);
  if (kept) { state.view = kept; dashDraw(state); }
  let view;
  try { view = await get(dashApi(place)); }
  catch (e) {
    if (DASH !== state) return;
    if (kept) delete S.lastRead.places[place];
    $('#dash-grid').innerHTML = `<p class="empty">${esc(e.status === 404 ? 'No such team' : e.status === 403 ? 'This team page is not shared with you' : e.message)}</p>`;
    return;
  }
  if (!Array.isArray(view?.pins)) view = {kind: place.split(':')[0], pins: [], can_edit: false, saved: true};
  // A read that lands after the page was left is kept for the next visit when nothing is kept yet.
  if (DASH !== state) { S.lastRead.places[place] ||= view; return; }
  // A write made while the read was out already drew its own answer.
  if (state.view !== kept && state.view) return;
  if (kept && sameData(view, kept)) return;
  state.view = view;
  dashDraw(state);
}
function dashDraw(state) {
  const v = state.view, root = state.root;
  S.lastRead.places[state.place] = v;
  if (v.kind === 'team') { root.querySelector('h1').textContent = v.name; }
  const actions = root.querySelector('.dash-actions');
  actions.hidden = !v.can_edit;
  const aud = root.querySelector('[data-dash-audience]');
  if (aud) {
    aud.innerHTML = `<span class="nav-icon" aria-hidden="true">groups</span>Seen by ${esc(dashAudienceWords(v.audience))}`;
    aud.setAttribute('aria-haspopup', 'dialog');
  }
  const grid = root.querySelector('#dash-grid');
  grid.innerHTML = v.pins.length ? '' : `<p class="empty">${v.can_edit ? 'Add a card or a page' : 'Nothing pinned'}</p>`;
  v.pins.forEach((pin, i) => {
    const el = document.createElement('article');
    el.className = 'dash-pin'; el.dataset.size = pin.size || 'medium'; el.dataset.at = String(i);
    el.innerHTML = `<header class="dash-pin-head">
        ${v.can_edit ? `<button type="button" class="dash-grip" data-dash-grip aria-label="Move ${esc(dashPinTitle(pin, v))}" title="Move">${BOT_SVG.grip}</button>` : ''}
        <h2>${pin.card ? esc(pin.card.heading) : ''}${pin.card && dashTeamShown(pin.card, v) ? ` <span class="muted">· ${esc(pin.card.team_name)}</span>` : ''}</h2>
        <span class="spacer"></span>
        ${v.can_edit ? `<button type="button" class="dash-more" data-dash-menu aria-haspopup="menu" aria-expanded="false" aria-label="${esc(dashPinTitle(pin, v))} menu" title="More"><span class="nav-icon" aria-hidden="true">more_horiz</span></button>` : ''}</header>
      <div class="dash-pin-body"></div>`;
    const body = el.querySelector('.dash-pin-body');
    if (pin.card) body.innerHTML = dashCardBody(pin.card);
    else body.append(dashPageEl(pin.page, pin.size));
    grid.append(el);
  });
}
// A place nobody saved shows its default cards without ids; the first change saves them, then finds the pin again.
async function dashSaved(state) {
  if (state.view.saved) return state.view;
  const view = await put(dashApi(state.place), {});
  if (DASH === state) state.view = view;
  return view;
}
async function dashWrite(state, work) {
  try {
    const view = await work();
    if (DASH !== state) return;
    state.view = view?.pins ? view : await get(dashApi(state.place), {fresh: true});
    dashDraw(state);
  } catch (e) { if (DASH === state) dashToast(e.message); }
}
function dashToast(text) {
  const grid = $('#dash-grid'); if (!grid) return;
  let el = $('#dash-error');
  if (!el) { el = document.createElement('p'); el.id = 'dash-error'; el.className = 'err dash-error'; el.setAttribute('role', 'alert'); grid.before(el); }
  el.textContent = text;
}
const dashPinAt = (state, at) => dashSaved(state).then(view => view.pins[at]);

// ---- menu, drag and keys
function dashMenuClose(state) { state.menu?.remove(); state.menu = null; state.root.querySelector('[data-dash-menu][aria-expanded="true"]')?.setAttribute('aria-expanded', 'false'); }
function dashMenuOpen(state, button) {
  const pinEl = button.closest('.dash-pin'), at = Number(pinEl.dataset.at), pin = state.view.pins[at];
  dashMenuClose(state);
  const menu = document.createElement('div');
  menu.className = 'dash-menu'; menu.setAttribute('role', 'menu');
  menu.innerHTML = `${pin.card ? '<button type="button" role="menuitem" data-dash-edit>Edit</button>' : ''}
    ${DASH_SIZES.map(([id, label]) => `<button type="button" role="menuitemradio" aria-checked="${pin.size === id}" data-dash-size="${id}">${label}</button>`).join('')}
    <button type="button" role="menuitem" data-dash-remove class="danger">Remove</button>`;
  pinEl.querySelector('.dash-pin-head').append(menu);
  button.setAttribute('aria-expanded', 'true');
  state.menu = menu;
  menu.querySelector('button')?.focus();
  menu.addEventListener('click', ev => {
    const b = ev.target.closest('button'); if (!b) return;
    dashMenuClose(state);
    if (b.hasAttribute('data-dash-edit')) return void dashCardForm(state, at);
    if (b.dataset.dashSize) return void dashWrite(state, async () => patch(`/v2/pins/${encodeURIComponent((await dashPinAt(state, at)).id)}`, {size: b.dataset.dashSize}));
    if (b.hasAttribute('data-dash-remove')) return void dashWrite(state, async () => writeRequest('DELETE', `/v2/pins/${encodeURIComponent((await dashPinAt(state, at)).id)}`));
  });
  menu.addEventListener('keydown', ev => {
    const items = [...menu.querySelectorAll('button')], i = items.indexOf(document.activeElement);
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') { ev.preventDefault(); items[(i + (ev.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus(); }
    if (ev.key === 'Escape') { ev.preventDefault(); dashMenuClose(state); button.focus(); }
  });
}
// The new order goes to the server as the pins' ids; until then the grid shows it as dropped.
function dashOrder(state, order) {
  void dashWrite(state, async () => {
    const view = await dashSaved(state);
    return post(dashApi(state.place) + '/order', {ids: order.map(at => view.pins[at].id)});
  });
}
function dashSortable(state, grid) {
  grid.addEventListener('pointerdown', ev => {
    const grip = ev.target.closest('[data-dash-grip]');
    if (!grip || ev.button) return;
    const item = grip.closest('.dash-pin'), sx = ev.clientX, sy = ev.clientY;
    let moved = false;
    const move = e => {
      if (!moved && Math.hypot(e.clientX - sx, e.clientY - sy) < 5) return;
      if (!moved) { moved = true; item.classList.add('dragging'); }
      e.preventDefault();
      const over = [...grid.children].find(el => el !== item && el.matches('.dash-pin') && (r => e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom)(el.getBoundingClientRect()));
      if (!over) return;
      const r = over.getBoundingClientRect(), after = r.width > r.height * 1.5 ? e.clientX > r.left + r.width / 2 : e.clientY > r.top + r.height / 2;
      grid.insertBefore(item, after ? over.nextElementSibling : over);
    };
    const up = () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      document.removeEventListener('pointercancel', up);
      if (!moved) return;
      item.classList.remove('dragging');
      const order = [...grid.querySelectorAll(':scope > .dash-pin')].map(el => Number(el.dataset.at));
      if (order.some((at, i) => at !== i)) dashOrder(state, order);
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
    document.addEventListener('pointercancel', up);
  });
}
function dashWire(state) {
  const root = state.root;
  dashSortable(state, root.querySelector('#dash-grid'));
  root.addEventListener('click', ev => {
    const more = ev.target.closest('[data-dash-menu]');
    if (more) return more.getAttribute('aria-expanded') === 'true' ? dashMenuClose(state) : dashMenuOpen(state, more);
    if (state.menu && !ev.target.closest('.dash-menu')) dashMenuClose(state);
    const add = ev.target.closest('[data-dash-add]');
    if (add) return void (add.dataset.dashAdd === 'card' ? dashCardForm(state, -1) : dashPageForm(state));
    if (ev.target.closest('[data-dash-audience]')) return void dashAudienceForm(state);
  });
  root.addEventListener('keydown', ev => {
    const grip = ev.target.closest('[data-dash-grip]'); if (!grip) return;
    const by = {ArrowUp: -1, ArrowLeft: -1, ArrowDown: 1, ArrowRight: 1}[ev.key]; if (!by) return;
    ev.preventDefault();
    const at = Number(grip.closest('.dash-pin').dataset.at), to = at + by, n = state.view.pins.length;
    if (to < 0 || to >= n) return;
    const order = [...Array(n).keys()];
    [order[at], order[to]] = [order[to], order[at]];
    void dashWrite(state, async () => {
      const view = await dashSaved(state);
      return post(dashApi(state.place) + '/order', {ids: order.map(i => view.pins[i].id)});
    }).then(() => DASH === state && $(`#dash-grid > .dash-pin[data-at="${to}"] [data-dash-grip]`)?.focus());
  });
}

// ---- forms: a card's settings, a page to pin, a team page's audience
async function dashChoices(state, team = '') {
  const key = 'c:' + team;
  if (!state.choicesBy) state.choicesBy = {};
  return state.choicesBy[key] ||= get('/v2/cards/choices' + (team ? '?team=' + encodeURIComponent(team) : ''));
}
const dashTeamOptions = (teams, value) => `<option value="">Company</option>${teams.map(t => `<option value="${esc(t.id)}"${t.id === value ? ' selected' : ''}>${esc(t.name)}</option>`).join('')}`;
function dashFieldsHTML(template, choices, settings) {
  const fields = choices.templates.find(t => t.id === template)?.fields || [];
  return fields.map(f => f === 'team' ? `<label>Team<select name="team">${dashTeamOptions(choices.teams, settings.team || '')}</select></label>`
    : f === 'weeks' ? `<label>Weeks<input type="number" name="weeks" min="4" max="26" value="${esc(settings.weeks ?? 8)}" required></label>`
    : f === 'count' ? `<label>Pages<input type="number" name="count" min="1" max="10" value="${esc(settings.count ?? 5)}" required></label>`
    : f === 'kpi' ? `<label>KPI<select name="kpi" required>${choices.kpis.length ? choices.kpis.map(k => `<option value="${esc(k.id)}"${k.id === settings.kpi ? ' selected' : ''}>${esc(k.name)}</option>`).join('') : '<option value="">No KPIs</option>'}</select></label>`
    : '').join('');
}
function dashSettingsOf(form) {
  const out = {};
  for (const el of form.querySelectorAll('[name]')) {
    if (['title', 'template', 'size'].includes(el.name)) continue;
    out[el.name] = el.type === 'number' ? Number(el.value) : el.value;
  }
  return out;
}
function dashDialog(state, title, inner, submit) {
  const dlg = state.root.querySelector('#dash-dialog');
  dlg.setAttribute('aria-label', title);
  dlg.innerHTML = `<form><header class="tmodal-head"><h2>${esc(title)}</h2><span class="spacer"></span>
      <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
    <div class="bot-editor-body dash-form">${inner}</div>
    <footer class="dash-form-foot"><span class="err" role="status" data-dash-status></span><span class="spacer"></span>
      <button type="button" class="ghost" data-close>Cancel</button><button class="primary" type="submit">Save</button></footer></form>`;
  const form = dlg.querySelector('form');
  dlg.querySelectorAll('[data-close]').forEach(b => { b.onclick = () => dlg.close(); });
  form.onsubmit = async ev => {
    ev.preventDefault();
    const status = form.querySelector('[data-dash-status]'), button = form.querySelector('[type="submit"]');
    button.disabled = true; status.textContent = '';
    try { await submit(form); if (dlg.open) dlg.close(); }
    catch (e) { status.textContent = e.message; }
    finally { button.disabled = false; }
  };
  if (!dlg.open) dlg.showModal();
  return form;
}
// at < 0: a new card for this place; else the card pinned at that position.
async function dashCardForm(state, at) {
  let choices;
  try { choices = await dashChoices(state); } catch (e) { return dashToast(e.message); }
  if (DASH !== state) return;
  const card = at >= 0 ? state.view.pins[at].card : null;
  const team = state.view.kind === 'team' ? state.view.group : '';
  const settings = card ? card.settings : {team};
  const template = card ? card.template : choices.templates[0].id;
  const form = dashDialog(state, card ? card.heading : 'Add card', `
    ${card ? '' : `<label>Card<select name="template">${choices.templates.map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')}</select></label>`}
    <label>Title<input name="title" maxlength="80" value="${esc(card?.title || '')}" placeholder="${esc(card?.name || choices.templates[0].name)}"></label>
    <div class="dash-fields">${dashFieldsHTML(template, choices, settings)}</div>
    ${card ? '' : `<label>Size<select name="size">${DASH_SIZES.map(([id, label]) => `<option value="${id}"${id === 'medium' ? ' selected' : ''}>${label}</option>`).join('')}</select></label>`}`,
  async f => {
    const el = f.elements, body = {title: el.namedItem('title').value.trim(), settings: dashSettingsOf(f)};
    if (card) {
      await dashWrite(state, async () => {
        const pin = await dashPinAt(state, at);
        await patch(`/v2/cards/${encodeURIComponent(pin.card.id)}`, body);
      });
    } else {
      await post('/v2/cards', {...body, template: el.namedItem('template').value, place: state.place, size: el.namedItem('size').value});
      await dashWrite(state, async () => null);
    }
  });
  const pick = form.querySelector('[name="template"]');
  if (pick) pick.onchange = () => {
    form.querySelector('.dash-fields').innerHTML = dashFieldsHTML(pick.value, choices, settings);
    form.elements.namedItem('title').placeholder = choices.templates.find(t => t.id === pick.value)?.name || '';
  };
}
async function dashPageForm(state) {
  let choices;
  const team = state.view.kind === 'team' ? state.view.group : '';
  try { choices = await dashChoices(state, team); } catch (e) { return dashToast(e.message); }
  if (DASH !== state) return;
  const pinned = new Set(state.view.pins.map(p => p.page?.file_id).filter(Boolean));
  const pages = choices.pages.filter(p => !pinned.has(p.file_id));
  dashDialog(state, 'Add page', pages.length
    ? `<fieldset class="dash-pick"><legend class="sr-only">Page</legend>${pages.map((p, i) => `<label class="dash-pick-row"><input type="radio" name="page" value="${esc(p.file_id)}"${i ? '' : ' checked'} required>
        <span><strong>${esc(p.title || 'Untitled')}</strong> <span class="muted">${esc(dashBot(p.bot))} · ${agoSpan(p.updated_at)}</span></span></label>`).join('')}</fieldset>
      <label>Size<select name="size">${DASH_SIZES.map(([id, label]) => `<option value="${id}"${id === 'medium' ? ' selected' : ''}>${label}</option>`).join('')}</select></label>`
    : '<p class="muted">No pages to add</p>',
  async f => {
    const value = f.querySelector('[name="page"]:checked')?.value;
    if (!value) return;
    await dashWrite(state, async () => { await dashSaved(state); return post('/v2/pins', {place: state.place, item: 'page:' + value, size: f.elements.namedItem('size').value}); });
  });
}
async function dashAudienceForm(state) {
  let choices;
  try { choices = await dashChoices(state); } catch (e) { return dashToast(e.message); }
  if (DASH !== state) return;
  const aud = state.view.audience || {everyone: true}, people = (S.people || []).filter(p => p.id);
  const box = (name, id, label, on) => `<label class="dash-pick-row"><input type="checkbox" name="${name}" value="${esc(id)}"${on ? ' checked' : ''}><span>${esc(label)}</span></label>`;
  const form = dashDialog(state, 'Who sees this page', `
    <label class="dash-pick-row"><input type="checkbox" name="everyone"${aud.everyone !== false ? ' checked' : ''}><span>Everyone</span></label>
    <div class="dash-chosen"${aud.everyone !== false ? ' hidden' : ''}>
      ${choices.teams.length ? `<fieldset class="dash-pick"><legend>Teams</legend>${choices.teams.map(t => box('teams', t.id, t.name, (aud.teams || []).includes(t.id))).join('')}</fieldset>` : ''}
      ${people.length ? `<fieldset class="dash-pick"><legend>People</legend>${people.map(p => box('people', p.id, p.name || p.id, (aud.people || []).includes(p.id))).join('')}</fieldset>` : ''}</div>`,
  async f => {
    const all = name => [...f.querySelectorAll(`[name="${name}"]:checked`)].map(el => el.value);
    const audience = f.elements.namedItem('everyone').checked ? {everyone: true} : {everyone: false, teams: all('teams'), people: all('people'), bots: aud.bots || []};
    await dashWrite(state, () => put(dashApi(state.place), {audience}));
  });
  const every = form.elements.namedItem('everyone');
  every.onchange = () => { form.querySelector('.dash-chosen').hidden = every.checked; };
}
