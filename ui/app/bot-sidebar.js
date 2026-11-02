/* ui/app/bot-sidebar.js — A bot's sidebar: its widgets in order, hidden or shown and how tall, and the edit mode that
   changes them and the task tabs in place (backend/bot_task_tabs.py). One layout per bot, the same for everyone who
   opens it; the Assistant's is each person's own.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A saved layout may still name the old Routines widget (they are automations now); the page has none, so it is skipped.
const BOT_WIDGETS = [['tasks', 'Tasks'], ['updates', 'Updates'], ['files', 'Files']];
const BOT_WIDGET_DEFAULT = BOT_WIDGETS.map(([id]) => ({id, hidden: false, height: null}));
const BOT_WIDGET_MIN = 80, BOT_WIDGET_MAX = 2000;
// slug -> {tabs, widgets, can_edit, personal}: what the server last said, or what this browser kept from last time.
const BOT_TASK_LAYOUT = new Map();
// {slug, tabs, widgets, saving} while a person edits a bot's sidebar; Done saves it in one write, Cancel drops it.
let BOT_EDIT = null;
// The draft may change: editing this bot's sidebar and not saving it. A change made while Done's write is on its way
// would be dropped when the write's answer ends edit mode, so nothing changes until the write succeeds or fails.
const botDrafting = slug => BOT_EDIT?.slug === slug && !BOT_EDIT.saving;
const BOT_SVG = {
  grip: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><g fill="currentColor"><circle cx="5.5" cy="3.5" r="1.3"/><circle cx="10.5" cy="3.5" r="1.3"/><circle cx="5.5" cy="8" r="1.3"/><circle cx="10.5" cy="8" r="1.3"/><circle cx="5.5" cy="12.5" r="1.3"/><circle cx="10.5" cy="12.5" r="1.3"/></g></svg>',
  eye: '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.6" d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>',
  shut: '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.6" d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"/><path stroke="currentColor" stroke-width="1.6" d="M4 4l16 16"/></svg>',
};
const botWidgetName = id => (BOT_WIDGETS.find(([w]) => w === id) || [, id])[1];
// Pages and built-in cards pinned to the sidebar (`page:<file id>`, `card:<id>`): slug -> item -> the pin as
// GET /api/v2/places/bot:<slug> gives it, so a widget's name and body come from what this reader may see.
const BOT_PINS = new Map();
const botPinItem = id => /^(page|card):/.test(id);
const botWidgetLabel = (slug, id) => {
  if (!botPinItem(id)) return botWidgetName(id);
  const pin = BOT_PINS.get(slug)?.get(id);
  return pin ? dashPinTitle(pin) : id.startsWith('page:') ? 'Page' : 'Card';
};

// ---- the layout: from memory, else from this browser's copy, else not yet (the rail says Loading)
const botSidebarCacheKey = slug => `tico.sidebar.${S.me?.id || ''}.${slug}`;
function botSidebarLayout(slug) {
  if (BOT_TASK_LAYOUT.has(slug)) return BOT_TASK_LAYOUT.get(slug);
  let kept = null;
  try { kept = JSON.parse(localStorage.getItem(botSidebarCacheKey(slug)) || 'null'); } catch {}
  if (!kept?.tabs?.length) return null;
  BOT_TASK_LAYOUT.set(slug, kept);
  return kept;
}
function botSidebarKeep(slug, layout) {
  BOT_TASK_LAYOUT.set(slug, layout);
  const {tabs, widgets, can_edit, personal} = layout;
  try { localStorage.setItem(botSidebarCacheKey(slug), JSON.stringify({tabs, widgets, can_edit, personal})); } catch {}
}
// Read the bot's layout; draw again only what changed. A failed first read shows the four built-in tabs rather than
// leave the rail loading.
async function botSidebarLoad(slug) {
  const layout = await v2Get(`/v2/bots/${encodeURIComponent(slug)}/task-tabs`);
  if (BOT?.slug !== slug) return;
  const before = JSON.stringify(botSidebarLayout(slug)?.tabs || null);
  if (Array.isArray(layout?.tabs)) botSidebarKeep(slug, layout);
  else if (!botSidebarLayout(slug)) BOT_TASK_LAYOUT.set(slug, {tabs: BOT_TASK_TABS.map(([builtin, name]) => ({builtin, name})), widgets: BOT_WIDGET_DEFAULT, can_edit: false});
  if (BOT_EDIT?.slug === slug) return;
  if (before !== JSON.stringify(botSidebarLayout(slug).tabs)) botTaskTabsPaint(slug);
  botSidebarApply(slug);
}

// ---- the widgets as the page shows them
// One widget: its edit bar (move, name, hide), what it shows, and its bottom edge for the height.
function botWidgetHTML(id, inner, slug = BOT?.slug) {
  const name = esc(botWidgetLabel(slug, id));
  return `<div class="bot-w" data-widget="${esc(id)}">
    <div class="bot-w-bar"><button type="button" class="bot-w-grip" data-w-grip aria-label="Move ${name}" title="Move">${BOT_SVG.grip}</button>
      <span class="bot-w-name">${name}</span><span class="spacer"></span>
      <button type="button" class="rail-ico bot-w-eye" data-w-eye aria-pressed="false" aria-label="Hide ${name}" title="Hide">${BOT_SVG.eye}</button>
      ${botPinItem(id) ? `<button type="button" class="rail-ico bot-w-x" data-w-remove aria-label="Remove ${name}" title="Remove">✕</button>` : ''}</div>
    ${inner}
    <div class="bot-w-foot"><div class="bot-w-size" data-w-size role="separator" aria-orientation="horizontal" tabindex="0" aria-label="${name} height" title="Height"></div>
      <button type="button" class="ghost bot-w-reset" data-w-reset hidden>Reset</button></div>
  </div>`;
}
// The widgets in the layout's order, as `parts` (id -> its HTML) has them; a widget the page does not have is skipped.
function botWidgetsHTML(slug, parts) {
  const order = botSidebarLayout(slug)?.widgets || BOT_WIDGET_DEFAULT;
  return order.filter(w => parts[w.id] || botPinItem(w.id)).map(w => botWidgetHTML(w.id, parts[w.id] || BOT_PIN_BODY, slug)).join('');
}
const BOT_PIN_BODY = '<section class="rail-sec bot-pin" data-pin-body></section>';
// A pinned page or card's body: the page through pageCard when this build has it, else a link; a card as the
// Dashboard draws it. Drawn again only when what it shows changed.
function botPinFill(slug, el) {
  const pin = BOT_PINS.get(slug)?.get(el.dataset.widget), body = el.querySelector('[data-pin-body]');
  if (!body) return;
  const key = pin ? JSON.stringify([pin.size, pin.card || pin.page]) : '';
  if (body.dataset.drawn === key && key) return;
  body.dataset.drawn = key;
  const name = botWidgetLabel(slug, el.dataset.widget);
  el.querySelectorAll('.bot-w-name').forEach(n => { n.textContent = name; });
  if (!pin) { body.innerHTML = '<p class="muted dash-none">Loading…</p>'; return; }
  // A page carries its own title; a card gets its name above it, as the rail's other sections do.
  if (pin.card) body.innerHTML = `<h2 class="rail-h">${esc(name)}</h2>${dashCardBody(pin.card)}`;
  else { body.innerHTML = ''; body.append(dashPageEl(pin.page, 'small')); }
}
async function botPinsLoad(slug) {
  const view = await v2Get('/v2/places/' + encodeURIComponent('bot:' + slug));
  if (BOT?.slug !== slug || !view?.pins) return;
  const kept = BOT_PINS.get(slug) || new Map(), pins = new Map(view.pins.map(p => [p.item, p]));
  // A page added in this edit and not saved yet keeps the brief the add form had.
  for (const [item, pin] of kept) if (!pins.has(item) && BOT_EDIT?.widgets.some(w => w.id === item)) pins.set(item, pin);
  BOT_PINS.set(slug, pins);
  botSidebarApply(slug);
}
function botWidgetHeight(el, height) {
  if (height) { el.style.setProperty('--w-max', height + 'px'); el.dataset.h = String(height); }
  else { el.style.removeProperty('--w-max'); delete el.dataset.h; }
  const reset = el.querySelector('[data-w-reset]');
  if (reset) reset.hidden = !height;
}
// Order, hidden and heights onto the rail, from the draft while editing. Until the layout is known the rail says
// Loading instead of drawing the default and then jumping.
function botSidebarApply(slug) {
  const rail = $('#pane-tasks');
  if (!rail || BOT?.slug !== slug || !rail.querySelector('.bot-w')) return;
  const layout = botSidebarLayout(slug), editing = BOT_EDIT?.slug === slug;
  rail.toggleAttribute('data-loading', !layout && isKeeper(slug));
  const loading = rail.querySelector('.bot-rail-loading');
  if (loading) loading.hidden = !rail.hasAttribute('data-loading');
  const widgets = editing ? BOT_EDIT.widgets : layout?.widgets || BOT_WIDGET_DEFAULT;
  // Pinned pages and cards come and go with the layout; the built-in widgets are always on the page.
  const want = new Set(widgets.map(w => w.id));
  rail.querySelectorAll(':scope > .bot-w').forEach(el => { if (botPinItem(el.dataset.widget) && !want.has(el.dataset.widget)) el.remove(); });
  for (const w of widgets) {
    let el = rail.querySelector(`:scope > .bot-w[data-widget="${CSS.escape(w.id)}"]`);
    if (!el && botPinItem(w.id)) {
      rail.insertAdjacentHTML('beforeend', botWidgetHTML(w.id, BOT_PIN_BODY, slug));
      el = rail.lastElementChild;
    }
    if (!el) continue;
    if (botPinItem(w.id)) botPinFill(slug, el);
    rail.append(el);
    el.toggleAttribute('data-off', !!w.hidden);
    botWidgetHeight(el, w.height);
    const eye = el.querySelector('[data-w-eye]'), name = botWidgetLabel(slug, w.id);
    eye.setAttribute('aria-pressed', String(!!w.hidden));
    eye.setAttribute('aria-label', (w.hidden ? 'Show ' : 'Hide ') + name);
    eye.title = w.hidden ? 'Show' : 'Hide';
    eye.innerHTML = w.hidden ? BOT_SVG.shut : BOT_SVG.eye;
  }
  rail.classList.toggle('editing', editing);
  const pencil = rail.querySelector('#bot-sidebar-edit');
  if (pencil) pencil.hidden = !layout?.can_edit || editing;
  const bar = rail.querySelector('.bot-rail-editbar');
  if (bar) bar.hidden = !editing;
  const add = rail.querySelector('#bot-sidebar-add');
  if (add) add.hidden = !!layout?.personal;
  // The rail hides while its layout loads, so the latest update is only seen once it shows.
  if (!editing && !rail.hasAttribute('data-loading')) botLatestSeen();
}

// ---- edit mode
const botLayoutCopy = value => JSON.parse(JSON.stringify(value));
function botSidebarEdit(slug) {
  const layout = botSidebarLayout(slug);
  if (!layout?.can_edit || BOT_EDIT) return;
  BOT_EDIT = {slug, tabs: botLayoutCopy(layout.tabs.map(botTaskTabDefaults)), widgets: botLayoutCopy(layout.widgets || BOT_WIDGET_DEFAULT)};
  void taskTypesLoad().catch(() => TASK_TYPES);
  botTaskTabsPaint(slug);
  botSidebarApply(slug);
  const status = $('#bot-sidebar-status');
  if (status) status.textContent = '';
  $('#bot-sidebar-done')?.focus({preventScroll: true});
}
function botSidebarStop(slug) {
  BOT_EDIT = null;
  botTaskTabsPaint(slug);
  botSidebarApply(slug);
  $('#bot-sidebar-edit')?.focus({preventScroll: true});
}
async function botSidebarDone(slug) {
  if (BOT_EDIT?.slug !== slug) return;
  if (BOT_EDIT.saving) return;
  const rail = $('#pane-tasks'), status = $('#bot-sidebar-status');
  const buttons = [...rail.querySelectorAll('.bot-rail-editbar button, [data-w-grip], [data-w-eye], [data-w-reset], [data-bot-task-tab], [data-bot-tab-add]')]
    .filter(button => !button.disabled);
  BOT_EDIT.saving = true;
  rail.toggleAttribute('data-saving', true);
  buttons.forEach(button => { button.disabled = true; });
  status.textContent = 'Saving…';
  try {
    const saved = await put(`/v2/bots/${encodeURIComponent(slug)}/task-tabs`, {tabs: BOT_EDIT.tabs, widgets: BOT_EDIT.widgets});
    if (BOT?.slug !== slug) return;
    botSidebarKeep(slug, saved);
    botSidebarStop(slug);
    void botTaskFilterRead(slug);
    void botPinsLoad(slug);
  } catch (error) {
    status.innerHTML = `<span class="err">${esc(error.message)}</span>`;
  } finally {
    if (BOT_EDIT?.slug === slug) BOT_EDIT.saving = false;
    rail.removeAttribute('data-saving');
    buttons.forEach(button => { button.disabled = false; });
  }
}
// The draft's order from the page after a drag: [data attribute holding each item's place before it].
function botDraftOrder(items, attr, list) {
  return items.map(el => list[Number(el.getAttribute(attr))]).filter(Boolean);
}
// Drag by a handle to reorder siblings, along x or y, with a pointer (mouse, pen or finger). `done` gets the items
// in their new order. A drag that moved swallows the click that ends it, and only that one.
function botSortable(container, itemSel, handleSel, axis, done) {
  container.addEventListener('pointerdown', ev => {
    const handle = ev.target.closest(handleSel);
    if (!handle || ev.button || !BOT_EDIT || BOT_EDIT.saving || !container.contains(handle)) return;
    const item = handle.closest(itemSel);
    if (!item || item.parentElement !== container) return;
    const start = axis === 'x' ? ev.clientX : ev.clientY;
    let moved = false;
    const move = e => {
      const at = axis === 'x' ? e.clientX : e.clientY;
      if (!moved && Math.abs(at - start) < 5) return;
      if (!moved) { moved = true; item.classList.add('dragging'); }
      e.preventDefault();
      // Near either end of a bar that scrolls sideways, it scrolls, so a pill can reach a tab out of view.
      if (axis === 'x') { const r = container.getBoundingClientRect(); container.scrollLeft += at < r.left + 24 ? -16 : at > r.right - 24 ? 16 : 0; }
      const others = [...container.children].filter(el => el !== item && el.matches(itemSel));
      const before = others.find(el => { const r = el.getBoundingClientRect(); return at < (axis === 'x' ? r.left + r.width / 2 : r.top + r.height / 2); });
      const target = before || container.querySelector(':scope > [data-sort-tail]');
      if (target ? item.nextElementSibling !== target : container.lastElementChild !== item) container.insertBefore(item, target);
    };
    const up = () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      document.removeEventListener('pointercancel', up);
      if (!moved) return;
      item.classList.remove('dragging');
      const swallow = e => { e.stopPropagation(); e.preventDefault(); };
      window.addEventListener('click', swallow, {capture: true, once: true});
      setTimeout(() => window.removeEventListener('click', swallow, {capture: true}), 0);
      done([...container.children].filter(el => el.matches(itemSel)));
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
    document.addEventListener('pointercancel', up);
  });
}
function botWidgetsReordered(slug, items) {
  if (!botDrafting(slug)) return botSidebarApply(slug);
  const byId = new Map(BOT_EDIT.widgets.map(w => [w.id, w]));
  BOT_EDIT.widgets = [...items.map(el => byId.get(el.dataset.widget)).filter(Boolean),
                      ...BOT_EDIT.widgets.filter(w => !items.some(el => el.dataset.widget === w.id))];
  botSidebarApply(slug);
}
function botTabsReordered(slug, items) {
  if (!botDrafting(slug)) return botTaskTabsPaint(slug);
  const keys = botTaskTabList(slug).map(tab => tab.key);
  BOT_EDIT.tabs = items.map(el => BOT_EDIT.tabs[keys.indexOf(el.dataset.botTaskTab)]).filter(Boolean);
  botTaskTabsPaint(slug);
}
// A widget's height while editing: drag its bottom edge, or the arrow keys on it; Reset goes back to its own.
function botWidgetResize(slug, rail) {
  rail.addEventListener('pointerdown', ev => {
    const grip = ev.target.closest('[data-w-size]');
    if (!grip || ev.button || !botDrafting(slug)) return;
    ev.preventDefault();
    const el = grip.closest('.bot-w'), w = BOT_EDIT.widgets.find(x => x.id === el.dataset.widget);
    const body = el.querySelector(':scope > .rail-sec');
    const from = ev.clientY, was = Math.round(body && !body.hidden ? body.getBoundingClientRect().height : 160);
    const move = e => {
      if (!botDrafting(slug)) return;
      w.height = Math.max(BOT_WIDGET_MIN, Math.min(BOT_WIDGET_MAX, Math.round(was + e.clientY - from)));
      botWidgetHeight(el, w.height);
    };
    const up = () => { document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
  });
  rail.addEventListener('keydown', ev => {
    const grip = ev.target.closest('[data-w-size]'), by = {ArrowDown: 20, ArrowUp: -20}[ev.key];
    if (!grip || !by || !botDrafting(slug)) return;
    ev.preventDefault();
    const el = grip.closest('.bot-w'), w = BOT_EDIT.widgets.find(x => x.id === el.dataset.widget);
    const body = el.querySelector(':scope > .rail-sec');
    const now = w.height || Math.round(body && !body.hidden ? body.getBoundingClientRect().height : 160);
    w.height = Math.max(BOT_WIDGET_MIN, Math.min(BOT_WIDGET_MAX, now + by));
    botWidgetHeight(el, w.height);
  });
}
// The rail's own clicks and keys while editing: hide or show, reset a height, move a widget with the keyboard,
// open a tab's pop-up or add one, Done and Cancel.
function botSidebarBind(slug) {
  const rail = $('#pane-tasks');
  if (!rail || rail.dataset.sidebarBound) return;
  rail.dataset.sidebarBound = '1';
  botSortable(rail, '.bot-w', '[data-w-grip]', 'y', items => botWidgetsReordered(slug, items));
  const bar = rail.querySelector('.bot-task-tabs');
  if (bar) botSortable(bar, '[data-bot-task-tab]', '[data-bot-task-tab]', 'x', items => botTabsReordered(slug, items));
  botWidgetResize(slug, rail);
  rail.addEventListener('click', ev => {
    if (ev.target.closest('#bot-sidebar-edit')) return botSidebarEdit(slug);
    if (!botDrafting(slug)) return;
    if (ev.target.closest('#bot-sidebar-done')) return void botSidebarDone(slug);
    // A card made during this edit is already pinned on the server: Cancel reads the layout again.
    if (ev.target.closest('#bot-sidebar-cancel')) { botSidebarStop(slug); void botSidebarLoad(slug); void botPinsLoad(slug); return; }
    if (ev.target.closest('#bot-sidebar-add')) return void botPinAddOpen(slug);
    const el = ev.target.closest('.bot-w'), w = el && BOT_EDIT.widgets.find(x => x.id === el.dataset.widget);
    if (w && ev.target.closest('[data-w-eye]')) { w.hidden = !w.hidden; botSidebarApply(slug); return; }
    if (w && ev.target.closest('[data-w-remove]')) { BOT_EDIT.widgets = BOT_EDIT.widgets.filter(x => x !== w); botSidebarApply(slug); return; }
    if (w && ev.target.closest('[data-w-reset]')) { w.height = null; botWidgetHeight(el, null); return; }
    if (ev.target.closest('[data-bot-tab-add]')) return void botFilterOpen(slug, -1);
    const pill = ev.target.closest('[data-bot-task-tab]');
    if (pill) {
      ev.stopPropagation();
      void botFilterOpen(slug, botTaskTabList(slug).findIndex(tab => tab.key === pill.dataset.botTaskTab));
    }
  }, true);
  rail.addEventListener('keydown', ev => {
    if (!botDrafting(slug)) return;
    if (ev.key === 'Escape' && !document.querySelector('dialog[open]')) { botSidebarStop(slug); return; }
    const grip = ev.target.closest('[data-w-grip]'), pill = ev.target.closest('[data-bot-task-tab]');
    const by = grip ? {ArrowUp: -1, ArrowDown: 1}[ev.key] : pill ? {ArrowLeft: -1, ArrowRight: 1}[ev.key] : undefined;
    if (!by) return;
    ev.preventDefault();
    if (grip) {
      const at = BOT_EDIT.widgets.findIndex(w => w.id === grip.closest('.bot-w').dataset.widget), to = at + by;
      if (to < 0 || to >= BOT_EDIT.widgets.length) return;
      [BOT_EDIT.widgets[at], BOT_EDIT.widgets[to]] = [BOT_EDIT.widgets[to], BOT_EDIT.widgets[at]];
      botSidebarApply(slug);
      grip.focus();
    } else {
      const keys = botTaskTabList(slug).map(tab => tab.key), at = keys.indexOf(pill.dataset.botTaskTab), to = at + by;
      if (to < 0 || to >= keys.length) return;
      [BOT_EDIT.tabs[at], BOT_EDIT.tabs[to]] = [BOT_EDIT.tabs[to], BOT_EDIT.tabs[at]];
      botTaskTabsPaint(slug);
      $('#bot-tab-t-' + botTaskTabList(slug)[to].key)?.focus();
    }
  });
}

// ---- Add: a page (saved with the layout on Done) or a built-in card (made now, pinned here, then part of the draft)
async function botPinAddOpen(slug) {
  let choices;
  try { choices = await get('/v2/cards/choices'); } catch (e) { $('#bot-sidebar-status').innerHTML = `<span class="err">${esc(e.message)}</span>`; return; }
  if (!botDrafting(slug)) return;
  let dlg = $('#bot-pin-dialog');
  if (!dlg) { dlg = document.createElement('dialog'); dlg.id = 'bot-pin-dialog'; dlg.className = 'tmodal tmodal-shell dash-dialog'; document.body.append(dlg); }
  const have = new Set(BOT_EDIT.widgets.map(w => w.id));
  const pages = choices.pages.filter(p => !have.has('page:' + p.file_id)).sort((a, b) => (b.bot === slug) - (a.bot === slug));
  dlg.setAttribute('aria-label', 'Add to sidebar');
  dlg.innerHTML = `<form><header class="tmodal-head"><h2>Add to sidebar</h2><span class="spacer"></span>
      <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
    <div class="bot-editor-body dash-form">
      <nav class="tabs slim strat-tabs" aria-label="Kind"><a href="#" data-kind="page" class="cur">Page</a><a href="#" data-kind="card">Card</a></nav>
      <div data-pane="page">${pages.length ? `<fieldset class="dash-pick"><legend class="sr-only">Page</legend>${pages.map((p, i) => `<label class="dash-pick-row"><input type="radio" name="page" value="${esc(p.file_id)}"${i ? '' : ' checked'}>
        <span><strong>${esc(p.title || 'Untitled')}</strong> <span class="muted">${esc(dashBot(p.bot))} · ${agoSpan(p.updated_at)}</span></span></label>`).join('')}</fieldset>` : '<p class="muted">No pages to add</p>'}</div>
      <div data-pane="card" hidden><label>Card<select name="template">${choices.templates.filter(t => t.id !== 'kpi').map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')}</select></label>
        <label>Team<select name="team">${dashTeamOptions(choices.teams, '')}</select></label></div></div>
    <footer class="dash-form-foot"><span class="err" role="status" data-status></span><span class="spacer"></span>
      <button type="button" class="ghost" data-close>Cancel</button><button class="primary" type="submit">Add</button></footer></form>`;
  const form = dlg.querySelector('form');
  let kind = 'page';
  dlg.querySelectorAll('[data-close]').forEach(b => { b.onclick = () => dlg.close(); });
  dlg.querySelectorAll('[data-kind]').forEach(a => {
    a.onclick = ev => {
      ev.preventDefault(); kind = a.dataset.kind;
      dlg.querySelectorAll('[data-kind]').forEach(x => x.classList.toggle('cur', x === a));
      dlg.querySelectorAll('[data-pane]').forEach(p => { p.hidden = p.dataset.pane !== kind; });
    };
  });
  form.onsubmit = async ev => {
    ev.preventDefault();
    if (!botDrafting(slug)) return dlg.close();
    const status = form.querySelector('[data-status]');
    const pins = BOT_PINS.get(slug) || new Map();
    BOT_PINS.set(slug, pins);
    try {
      let item;
      if (kind === 'page') {
        const fid = form.querySelector('[name="page"]:checked')?.value;
        if (!fid) return;
        item = 'page:' + fid;
        pins.set(item, {item, size: 'medium', page: pages.find(p => p.file_id === fid)});
      } else {
        const team = form.elements.namedItem('team').value;
        const made = await post('/v2/cards', {template: form.elements.namedItem('template').value, settings: team ? {team} : {}, place: 'bot:' + slug});
        item = 'card:' + made.card.id;
        pins.set(item, {item, size: 'medium', card: made.card});
      }
      if (!botDrafting(slug)) return dlg.close();
      if (!BOT_EDIT.widgets.some(w => w.id === item)) BOT_EDIT.widgets.push({id: item, hidden: false, height: null});
      dlg.close();
      botSidebarApply(slug);
    } catch (e) { status.textContent = e.message; }
  };
  dlg.showModal();
}

// ---- a tab's pop-up: Name, Show count, its rows (a filter's), Delete and Save. Nothing else.
const BOT_FILTER_FIELDS = [['status', 'Status'], ['step', 'Step'], ['assigned_to', 'Assigned to'], ['asked_by', 'Asked by'],
  ['type', 'Type'], ['needs', 'Needs'], ['waiting_on', 'Waiting on'], ['created_within', 'Created within'], ['updated_within', 'Updated within'],
  ['done_within', 'Done within'], ['due', 'Due'], ['label', 'Label']];
const BOT_FILTER_STATUSES = [['open', 'Open'], ['doing', 'Doing'], ['waiting', 'Waiting'], ['review', 'In review'], ['ready', 'Ready'],
  ['done', 'Done'], ['closed', 'Closed'], ['declined', 'Declined']];
const BOT_FILTER_WAITING = [['any', 'Anyone'], ['person', 'A person'], ['bot', 'A bot'], ['me', 'Me'], ['not_me', 'Someone else']];
const BOT_FILTER_START = {status: ['open', 'doing', 'waiting'], step: '', assigned_to: 'this_bot', asked_by: 'this_bot', type: ['general'],
  needs: 'you', waiting_on: 'any', created_within: 7, updated_within: 7, done_within: 14, due: 'overdue', label: ''};
function botFilterParties(slug, others) {
  const people = (S.people || []).map(p => [actorOf('human', p.id), p.id === S.me?.id ? `${p.name || p.id} (you)` : p.name || p.id]);
  const bots = (S.emps || []).map(e => [`bot:${e.name}`, e.display_name || e.name]);
  return [['this_bot', empName(slug)], ['me', 'Me'], ...(others ? [['others', 'Others']] : []), ...people, ...bots];
}
const actorOf = (kind, id) => `${kind}:${id}`;
function botFilterValueHTML(slug, row, at) {
  const v = row.value, name = `data-cond-value="${at}"`;
  const options = (list, picked) => list.map(([value, word]) => `<option value="${esc(value)}"${String(picked) === String(value) ? ' selected' : ''}>${esc(word)}</option>`).join('');
  const checks = (list, picked) => `<span class="bot-cond-checks">${list.map(([value, word]) =>
    `<label><input type="checkbox" ${name} value="${esc(value)}"${[].concat(picked || []).includes(value) ? ' checked' : ''}>${esc(word)}</label>`).join('')}</span>`;
  const days = value => `<input type="number" ${name} min="1" max="3650" step="1" value="${esc(value ?? '')}" aria-label="Days" required> days`;
  switch (row.field) {
    case 'status': return checks(BOT_FILTER_STATUSES, v);
    case 'type': return checks((TASK_TYPES?.length ? TASK_TYPES : [{id: 'general', name: 'General'}]).map(t => [t.id, t.name]), v);
    case 'step': return `<select ${name} aria-label="Step" required><option value=""></option>${(TASK_TYPES || []).map(t =>
      `<optgroup label="${esc(t.name)}">${options((t.steps || []).map(s => [s.id, s.name]), [].concat(v)[0])}</optgroup>`).join('')}</select>`;
    case 'assigned_to': case 'asked_by':
      return `<select ${name} aria-label="Who">${options(botFilterParties(slug, row.field === 'assigned_to'), v)}</select>`;
    case 'needs': return `<select ${name} aria-label="Who">${options([['you', 'You (assigned to you, or waiting on you)']], v)}</select>`;
    case 'waiting_on': return `<select ${name} aria-label="Who">${options(BOT_FILTER_WAITING, v)}</select>`;
    case 'due': return `<select data-cond-due="${at}" aria-label="Due">${options([['overdue', 'Overdue'], ['within', 'Within']], v === 'overdue' ? 'overdue' : 'within')}</select>${
      v === 'overdue' ? '' : ` <input type="number" ${name} min="0" max="3650" step="1" value="${esc(v)}" aria-label="Days" required> days`}`;
    case 'label': return `<input type="text" ${name} value="${esc(v || '')}" maxlength="60" aria-label="Label" required>`;
    default: return days(v);
  }
}
async function botFilterOpen(slug, at) {
  if (!botDrafting(slug)) return;
  await taskTypesLoad().catch(() => TASK_TYPES);
  if (!botDrafting(slug)) return;
  const adding = at < 0, was = adding ? {name: '', show_count: true, conditions: [{field: 'status', value: BOT_FILTER_START.status}]} : BOT_EDIT.tabs[at];
  if (!was) return;
  const draft = botLayoutCopy({show_count: true, ...botTaskTabDefaults(was)});
  const editable = draft.builtin !== 'done';
  let dialog = $('#bot-filter-dialog');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.className = 'owner-picker bot-filter-dialog'; dialog.id = 'bot-filter-dialog';
    dialog.setAttribute('aria-label', 'Tab');
    document.body.append(dialog);
  }
  // What the form holds, read back into the draft before each redraw and on Save.
  const read = () => {
    draft.name = dialog.querySelector('[data-filter-name]').value.trim();
    draft.show_count = dialog.querySelector('[data-filter-count]').checked;
    if (!editable) return;
    draft.conditions = draft.conditions.map((row, i) => {
      const inputs = [...dialog.querySelectorAll(`[data-cond-value="${i}"]`)];
      if (['status', 'type'].includes(row.field)) return {field: row.field, value: inputs.filter(x => x.checked).map(x => x.value)};
      if (row.field === 'step') return {field: row.field, value: inputs[0]?.value ? [inputs[0].value] : []};
      if (row.field === 'due' && dialog.querySelector(`[data-cond-due="${i}"]`).value === 'overdue') return {field: 'due', value: 'overdue'};
      if (/_within$|^due$/.test(row.field)) return {field: row.field, value: inputs[0]?.value === '' ? '' : Number(inputs[0]?.value)};
      return {field: row.field, value: inputs[0]?.value ?? ''};
    });
  };
  const paint = () => {
    dialog.innerHTML = `<form class="bot-filter-form" novalidate>
      <label class="bot-filter-name">Name <input type="text" data-filter-name value="${esc(draft.name)}" maxlength="40" required></label>
      <label class="bot-filter-count"><input type="checkbox" data-filter-count${draft.show_count !== false ? ' checked' : ''}> Show count</label>
      ${!editable ? '' : `<ol class="bot-cond-list">${draft.conditions.map((row, i) => `<li class="bot-cond" data-cond="${i}">
        <select data-cond-field="${i}" aria-label="Field">${BOT_FILTER_FIELDS.map(([value, word]) => `<option value="${value}"${row.field === value ? ' selected' : ''}>${word}</option>`).join('')}</select>
        <span class="bot-cond-value">${botFilterValueHTML(slug, row, i)}</span>
        <button type="button" class="ghost bot-cond-x" data-cond-remove="${i}" aria-label="Remove condition" title="Remove">✕</button></li>`).join('')}</ol>
      <button type="button" class="ghost bot-cond-add" data-cond-add>+ Condition</button>`}
      ${draft.builtin === 'needs' ? '<p>Also shows this bot’s approvals and questions for you</p>' : ''}
      ${draft.builtin === 'needs' && JSON.stringify(draft.conditions) !== JSON.stringify(BOT_TASK_DEFAULT_CONDITIONS.needs)
        ? `<p>The org chart number still counts everything ${esc(empName(slug))} needs from you, not this tab.</p>` : ''}
      ${BOT_TASK_DEFAULT_CONDITIONS[draft.builtin] ? '<button type="button" class="ghost" data-filter-reset>Reset to default</button>' : ''}
      <div class="row bot-filter-actions"><button type="button" class="ghost" data-filter-delete${BOT_EDIT.tabs.length > 1 || adding ? '' : ' disabled'}>Delete</button><span class="spacer"></span>
        <span class="err" data-filter-error role="alert"></span><button type="submit" class="primary">Save</button></div></form>`;
    dialog.querySelector('[data-filter-reset]')?.addEventListener('click', () => {
      read(); draft.conditions = botLayoutCopy(BOT_TASK_DEFAULT_CONDITIONS[draft.builtin]); paint();
    });
    dialog.querySelectorAll('[data-cond-field]').forEach(select => select.onchange = () => {
      read();
      const i = Number(select.dataset.condField);
      draft.conditions[i] = {field: select.value, value: botLayoutCopy(BOT_FILTER_START[select.value])};
      paint();
      dialog.querySelector(`[data-cond-field="${i}"]`)?.focus();
    });
    dialog.querySelectorAll('[data-cond-due]').forEach(select => select.onchange = () => {
      read();
      const i = Number(select.dataset.condDue);
      draft.conditions[i].value = select.value === 'overdue' ? 'overdue' : 7;
      paint();
    });
    dialog.querySelectorAll('[data-cond-remove]').forEach(button => button.onclick = () => {
      read(); draft.conditions.splice(Number(button.dataset.condRemove), 1); paint();
    });
    dialog.querySelector('[data-cond-add]')?.addEventListener('click', () => {
      read(); draft.conditions.push({field: 'status', value: botLayoutCopy(BOT_FILTER_START.status)}); paint();
      dialog.querySelector(`[data-cond-field="${draft.conditions.length - 1}"]`)?.focus();
    });
    dialog.querySelector('[data-filter-delete]').onclick = () => {
      if (!adding) BOT_EDIT.tabs.splice(at, 1);
      dialog.close();
      botTaskTabsPaint(slug);
    };
    dialog.querySelector('form').onsubmit = event => {
      event.preventDefault();
      read();
      const problem = !draft.name ? 'Name it.' : (draft.conditions || []).some(row => row.value === '' || (Array.isArray(row.value) && !row.value.length)
        || (typeof row.value === 'number' && !(row.value >= (row.field === 'due' ? 0 : 1) && row.value <= 3650))) ? 'Fill in every condition.' : '';
      if (problem) { dialog.querySelector('[data-filter-error]').textContent = problem; return; }
      const tab = draft.builtin ? {builtin: draft.builtin, name: draft.name, show_count: draft.show_count, ...(editable ? {conditions: draft.conditions} : {})}
        : {name: draft.name, show_count: draft.show_count, conditions: draft.conditions};
      if (adding) BOT_EDIT.tabs.push(tab); else BOT_EDIT.tabs[at] = tab;
      dialog.close();
      botTaskTabsPaint(slug);
    };
  };
  dialog.onclose = () => { dialog.innerHTML = ''; };
  // A click on the backdrop closes it, as Escape does.
  dialog.onclick = ev => { if (ev.target === dialog) dialog.close(); };
  paint();
  dialog.showModal();
  dialog.querySelector('[data-filter-name]').focus();
}
