/* ui/app/task-list-bulk.ts — the Tasks list's selection and bulk changes: the bar, select all, one-by-one and server-side
   updates, Undo, and the Status / Owner / Tags menus.
   Split out of task-list.ts. Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A server-side bulk change's filter (POST /v2/task-bulk: a type, statuses and chips, or the ids shown), and the
// selection it stands for.
type TaskBulkFilter = Record<string, unknown>;
interface TaskBulkScope { filter: TaskBulkFilter; label: string; }
// A task a bulk Close closed, as Undo puts it back. It has no owner_id: Undo compares `owner_id`, so it always sends
// the owner (unchanged when it did not move).
interface TaskBulkClosed { id: string; title: string; status: string; step_id: string; owner: string; typed: boolean; told: boolean; owner_id?: undefined; }
interface TaskBulkUndo { items: TaskBulkClosed[]; until: number; }
// What a bulk change says when it is done: the words for changed and already so, and whether it closes (Undo).
interface TaskBulkDescribe { done: string; already?: string; close?: boolean; status?: string; }
// One task's update body for a bulk change, from its newest copy (null: it is already so).
type TaskBulkBody = (t: TaskRow) => Record<string, unknown> | null;

// ---- selection and bulk changes. What is selected is always what can be seen selected: a filter, a search or a fold
// that hides a row drops it from the selection on the next draw.
function tasksVisibleKeys() {
  return [...document.querySelectorAll<HTMLElement>('#task-body [data-task-key]')].filter(el => el.getClientRects().length).map(el => el.dataset.taskKey!);
}
function tasksSelectionPrune(state: TaskState) {
  if (!state.selected.size) return;
  const seen = new Set(tasksVisibleKeys());
  for (const k of [...state.selected]) if (!seen.has(k)) { state.selected.delete(k); state.bulkScope = null; }
  for (const k of [...(state.bulkFail?.keys() || [])]) if (!seen.has(k)) state.bulkFail.delete(k);
}
function tasksSelectionPaint(state: TaskState) {
  const body = $('#task-body'); if (!body) return;
  body.classList.toggle('has-sel', state.selected.size > 0 || !!state.selectMode);
  for (const row of body.querySelectorAll('[data-task-key]')) {
    const on = state.selected.has(row.dataset.taskKey);
    row.classList.toggle('sel', on);
    const box = row.querySelector('.tl-check'); if (box) box.checked = on;
    const open = row.querySelector('[data-open-task]');
    if (on) open?.setAttribute('aria-describedby', 'tl-sel-word'); else open?.removeAttribute('aria-describedby');
  }
  tasksBulkBar(state);
}
function tasksSelect(state: TaskState, key: string, how = 'toggle') {
  if (!tasksCanSelect()) return;
  state.bulkScope = null;          // a hand-picked selection is those tasks, not a filter
  if (how === 'range' && state.anchor) {
    const keys = tasksVisibleKeys(), a = keys.indexOf(state.anchor), b = keys.indexOf(key);
    if (a >= 0 && b >= 0) { for (const k of keys.slice(Math.min(a, b), Math.max(a, b) + 1)) state.selected.add(k); tasksSelectionPaint(state); return; }
  }
  if (state.selected.has(key)) state.selected.delete(key); else state.selected.add(key);
  state.anchor = key;
  if (state.bulkMsg && !state.bulkUndo) state.bulkMsg = '';
  tasksSelectionPaint(state);
}
function tasksSelectionClear(state: TaskState) {
  state.selected.clear(); state.bulkFail = new Map(); state.bulkMsg = ''; state.bulkUndo = null; state.bulkScope = null;
  tasksSelectionPaint(state);
  for (const row of document.querySelectorAll('#task-body .tl-row.failed')) row.classList.remove('failed');
  // biome-ignore lint/suspicious/useIterableCallbackReturn: moved as it was from task-list.ts (its one biome finding); remove() returns nothing
  document.querySelectorAll('#task-body .tl-failed').forEach(el => el.remove());
}
function tasksBulkBar(state: TaskState) {
  const bar = $('#task-bulk'); if (!bar) return;
  const n = state.selected.size;
  const undo = state.bulkUndo && state.bulkUndo.until > Date.now();
  const show = tasksCanSelect() && (n || state.bulkMsg || state.selectMode);
  bar.hidden = !show;
  if (!show) { bar.innerHTML = ''; bar.dataset.html = ''; return; }
  const busy = !!state.bulkBusy, scope = n && state.bulkScope;
  // Select all: every task the list shows, when only some are picked (a board column has its own, in its header)
  const all = n && !scope && state.view !== 'board' ? tasksVisibleKeys().filter(k => k.startsWith('t')).length : 0;
  const html = `${n ? `<span class="tl-bulk-n tnum">${scope ? `All ${n} ${esc(scope.label)}` : `${n} selected`}</span>
    ${all > n ? `<button type="button" data-bulk="all"${busy ? ' disabled' : ''}>Select all ${all}</button>` : ''}
    <button type="button" data-bulk="status" aria-haspopup="true"${busy ? ' disabled' : ''}>Status</button>
    <button type="button" data-bulk="owner" aria-haspopup="true"${busy ? ' disabled' : ''}>Owner</button>
    <button type="button" data-bulk="tags" aria-haspopup="true"${busy ? ' disabled' : ''}>Tags</button>
    <button type="button" data-bulk="close" class="danger"${busy ? ' disabled' : ''}>Close</button>` : ''}
    <span class="tl-bulk-msg${state.bulkFail?.size ? ' err' : ''}" role="status" aria-live="polite">${esc(state.bulkMsg || '')}</span>
    ${undo ? '<button type="button" data-bulk="undo" class="tl-bulk-undo">Undo</button>' : ''}
    <button type="button" data-bulk="clear" class="tl-bulk-x" aria-label="${n ? 'Clear selection' : state.selectMode ? 'Done selecting' : 'Dismiss'}" title="Clear (Esc)">${TL_ICON.x}<span>${n ? 'Clear' : state.selectMode ? 'Done' : ''}</span></button>`;
  if (bar.dataset.html === html) return;          // the poll redraws the list; a focused button here keeps its focus
  const focused = bar.contains(document.activeElement) ? (document.activeElement as HTMLElement).dataset.bulk : '';
  bar.innerHTML = bar.dataset.html = html;
  if (focused) (bar.querySelector(`[data-bulk="${focused}"]`) || bar.querySelector('[data-bulk]'))?.focus();
}
function tasksBulkFilter(state: TaskState, base: TaskBulkFilter | null, keys: string[]): TaskBulkFilter | null {
  const f = state.filters || {};
  const plain = base && state.activeComplete && !state.q && !state.boardFocus
    && Object.entries(f).every(([k, v]) => !v?.length || (BULK_SERVER_CHIPS[k] && v.length === 1));
  if (plain) {
    const filter: TaskBulkFilter = {...base, lane: 'company'};
    for (const [k, field] of Object.entries(BULK_SERVER_CHIPS)) {
      const v = f[k]?.[0] as string | undefined; if (!v) continue;
      filter[field] = k === 'tag' ? v : resolveMe(v).includes(':') ? resolveMe(v) : 'bot:' + v;
    }
    return filter;
  }
  return keys.length <= BULK_IDS ? {ids: keys.map(k => k.slice(1))} : null;
}
// What the list shows is a server filter only where it is one: the open list or Everyone's (not Needs you, a person's
// plate, or a page of finished tasks), with every matching row unfolded.
function tasksListBase(state: TaskState, keys: string[]) {
  const everyone = state.view === 'foryou' && tasksWhoKey(state) === 'all' && state.side !== 'asked';
  if (state.view !== 'list' && !everyone) return null;
  if (keys.length !== taskItems(state).length) return null;
  return {type: state.type || 'general', status: 'open,doing,waiting,review,ready,declined'};
}
function tasksBulkSelectAll(state: TaskState, keys: string[], base: TaskBulkFilter | null, label: string) {
  if (!tasksCanSelect() || state.bulkBusy) return;
  keys = keys.filter(k => k.startsWith('t'));
  if (!keys.length) return;
  const filter = tasksBulkFilter(state, base, keys);
  state.selected = new Set(keys); state.anchor = keys.at(-1)!;    // keys is not empty
  state.bulkFail = new Map(); state.bulkUndo = null; state.bulkMsg = '';
  state.bulkScope = filter ? {filter, label} : null;     // past the id limit, the tasks go one by one as before
  tasksSelectionPaint(state);
}
// the selected tasks, in the order they show (the selection only ever holds visible rows)
function tasksSelectedTasks(state: TaskState) {
  const byId = tasksById(state);
  return tasksVisibleKeys().filter(k => state.selected.has(k)).map(k => byId.get(k.slice(1))).filter(Boolean) as TaskRow[];
}
// Each task is its own update (POST /v2/tasks/{id}) with its latest version; a 409 refetches it and tries once more.
// A failure is named, kept selected and marked; nothing is dropped quietly. No typed comment goes with a bulk change:
// an unsent one stays in its task's box, where the person can see it, rather than leave unseen with Done.
async function tasksBulkApply(state: TaskState, describe: TaskBulkDescribe, bodyFor: TaskBulkBody, action?: Record<string, unknown>) {
  const tasks = tasksSelectedTasks(state);
  if (!tasks.length || state.bulkBusy || !tasksCanSelect()) return;
  if (state.bulkScope && action) return tasksBulkServer(state, describe, action, tasks);
  tasksMenuClose();
  state.bulkBusy = true; state.bulkFail = new Map(); state.bulkUndo = null; state.bulkMsg = `Updating ${tasks.length}…`;
  tasksBulkBar(state);
  const failed = new Map<string, string>(), closed: TaskBulkClosed[] = [];
  let changed = 0, already = 0;
  for (let t of tasks) {
    let body = bodyFor(t);
    if (body === null) { already++; continue; }
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        await post(`/v2/tasks/${encodeURIComponent(t.id)}`, {version: t.version, ...body});
        changed++;
        if (describe.close) closed.push({id: t.id, title: t.title, status: t.status, step_id: t.step_id || '', owner: t.owner_id, typed: pipelineTypeId(t) !== 'general',
          told: !!actorSlug(taskRequester(t))});
        break;
      } catch (e) {
        if (errorStatus(e) === 409 && attempt === 0) {
          const fresh = (await v2Get(`/v2/tasks/${encodeURIComponent(t.id)}`))?.task;
          if (fresh) {
            const again = bodyFor(fresh);
            if (again === null) { already++; break; }
            body = again; t = fresh; continue;
          }
        }
        failed.set('t' + t.id, errorMessage(e, 'Not saved'));
        break;
      }
    }
  }
  if (TASKS_ST !== state) return;
  state.bulkBusy = false;
  state.bulkFail = failed;
  state.selected = new Set(failed.keys());
  const names = [...failed.keys()].map(k => tasksSelectedTitle(state, k));
  state.bulkMsg = [`${changed} ${describe.done}`, already ? `${already} already ${describe.already || 'so'}` : '',
    failed.size ? `${failed.size} not changed: ${names.slice(0, 2).join(', ')}${names.length > 2 ? ` +${names.length - 2}` : ''}` : ''].filter(Boolean).join(', ');
  if (closed.length) state.bulkUndo = {items: closed, until: Date.now() + 10000};
  if (failed.size) toast(`${failed.size} not changed: ${[...failed.entries()].map(([k, m]) => `${tasksSelectedTitle(state, k)} (${m})`).join('; ')}`, true);
  await tasksLoad(state);
  if (TASKS_ST !== state) return;
  tasksSelectionPaint(state);
  tasksBulkSettle(state, closed.length ? 10000 : 4000);
}
// One call for the whole selection. The server says what changed, what already was so, and what it skipped and why:
// a skipped task stays selected and marked, as a failed one-by-one update does. A refused call changes nothing and
// keeps the selection to try again.
async function tasksBulkServer(state: TaskState, describe: TaskBulkDescribe, action: Record<string, unknown>, tasks: TaskRow[]) {
  const scope = state.bulkScope!;     // tasksBulkApply sends only a selection with a scope here
  tasksMenuClose();
  state.bulkBusy = true; state.bulkFail = new Map(); state.bulkUndo = null; state.bulkMsg = `Updating ${tasks.length}…`;
  tasksBulkBar(state);
  let out: import('../types/api').TaskBulkResult | null = null, error = '';
  try { out = await post('/v2/task-bulk', {filter: scope.filter, ...action}); }
  catch (e) { error = errorMessage(e, 'Not saved'); }
  if (TASKS_ST !== state) return;
  state.bulkBusy = false;
  if (!out) { state.bulkMsg = error; toast(error, true); tasksBulkBar(state); return; }
  const byId = tasksById(state), skipped = out.tasks?.skipped || [];
  const failed = new Map(skipped.map(s => ['t' + s.id, s.reason || 'Not changed']));
  state.bulkScope = null;
  state.bulkFail = failed;
  state.selected = new Set(failed.keys());
  const names = skipped.map(s => s.title || '#' + s.number);
  state.bulkMsg = [`${out.changed} ${describe.done}`, out.unchanged ? `${out.unchanged} already ${describe.already || 'so'}` : '',
    out.skipped ? `${out.skipped} not changed: ${names.slice(0, 2).join(', ')}${names.length > 2 ? ` +${names.length - 2}` : ''}` : ''].filter(Boolean).join(', ');
  if (describe.close) {
    const closed = ((out.tasks?.changed || []).map(c => byId.get(String(c.id))).filter(Boolean) as TaskRow[]).map(t => ({id: t.id, title: t.title, status: t.status,
      step_id: t.step_id || '', owner: t.owner_id, typed: pipelineTypeId(t) !== 'general', told: !!actorSlug(taskRequester(t))}));
    if (closed.length) state.bulkUndo = {items: closed, until: Date.now() + 10000};
  }
  if (skipped.length) toast(`${skipped.length} not changed: ${skipped.map(s => `${s.title || '#' + s.number} (${s.reason})`).join('; ')}`, true);
  await tasksLoad(state);
  if (TASKS_ST !== state) return;
  tasksSelectionPaint(state);
  tasksBulkSettle(state, state.bulkUndo ? 10000 : 4000);
}
// The result line stays a moment (Undo for ten seconds after a Close), then goes.
function tasksBulkSettle(state: TaskState, ms: number) {
  clearTimeout(state.bulkTimer);
  state.bulkTimer = setTimeout(() => {
    if (TASKS_ST !== state) return;
    state.bulkUndo = null;
    if (!state.selected.size && !state.bulkFail.size) state.bulkMsg = '';
    tasksBulkBar(state);
  }, ms);
}
// Undo puts each closed task back as it was: its status (or step), and its owner if that moved. A bot that asked for
// the task was already told it closed; the message says so rather than pretend. Nothing else can start meanwhile.
async function tasksBulkUndo(state: TaskState) {
  if (state.bulkBusy) return;
  const items = state.bulkUndo?.items || [];
  state.bulkUndo = null; state.bulkBusy = true; state.bulkMsg = `Reopening ${items.length}…`; tasksBulkBar(state);
  let ok = 0;
  const failed: {id: string; title: string; why: string}[] = [];
  for (const prev of items) {
    try {
      const fresh = (await get<import('../types/api').GetTaskResponse>(`/v2/tasks/${encodeURIComponent(prev.id)}`)).task;
      const body: Record<string, string | undefined> = prev.typed && prev.step_id ? {step: prev.step_id} : {status: prev.status};
      if (fresh.owner_id !== prev.owner_id) body.owner = actorSlug(prev.owner_id) || prev.owner_id;
      await post(`/v2/tasks/${encodeURIComponent(prev.id)}`, {version: fresh.version, ...body});
      ok++;
    } catch (e) { failed.push({id: prev.id, title: prev.title, why: errorMessage(e, '')}); }
  }
  if (TASKS_ST !== state) return;
  state.bulkBusy = false;
  const told = items.some(p => p.told);
  const names = failed.map(f => f.title || tasksSelectedTitle(state, 't' + f.id));
  state.bulkMsg = [failed.length ? `${ok} of ${items.length} reopened` : told ? 'Reopened' : `${ok} reopened`, told ? 'bots were already told' : '',
    failed.length ? `${failed.length} not reopened: ${names.join(', ')}` : ''].filter(Boolean).join('; ');
  state.bulkFail = new Map(failed.map(f => ['t' + f.id, f.why || 'Not reopened']));
  if (told || failed.length) toast(state.bulkMsg, !!failed.length);
  await tasksLoad(state);
  tasksBulkBar(state);
  tasksBulkSettle(state, 4000);
}
function tasksBulkMenu(state: TaskState, kind: string, anchor: HTMLElement) {
  if (kind === 'clear') { state.selectMode = false; tasksSelectionClear(state); return; }
  if (kind === 'undo') { void tasksBulkUndo(state); return; }
  if (kind === 'all') { const keys = tasksVisibleKeys(); tasksBulkSelectAll(state, keys, tasksListBase(state, keys), 'matching'); return; }
  if (kind === 'close') { void tasksBulkApply(state, {close: true, done: 'closed', already: 'closed'}, t => t.status === 'closed' ? null : {close: true}, {action: 'close'}); return; }
  if (kind === 'status') {
    tasksMenu(state, anchor, `<div class="tl-menu" role="menu" aria-label="Set status"><div class="tl-menu-h" aria-hidden="true">Status</div>
      ${BULK_STATUSES.map(s => `<button type="button" role="menuitem" class="tl-mi" data-bulk-status="${s}"><span>${esc(STATUS_WORD[s] || s)}</span></button>`).join('')}</div>`, pop => {
      pop.onclick = ev => {
        const b = eventElement(ev)?.closest<HTMLElement>('[data-bulk-status]'); if (!b) return;
        const status = b.dataset.bulkStatus!, word = STATUS_WORD[status] || status;
        void tasksBulkApply(state, {status, done: `moved to ${word}`, already: word}, t => t.status === status ? null : {status}, {action: 'status', status});
      };
    });
  } else if (kind === 'owner') {
    const opts = new DOMParser().parseFromString(`<select>${taskOwnerOptions('')}</select>`, 'text/html').querySelectorAll<HTMLOptionElement>('option[value]:not([value=""])');
    tasksMenu(state, anchor, `<div class="tl-menu" role="group" aria-label="Set owner"><div class="tl-menu-h" aria-hidden="true">Owner</div>
      <input type="search" class="tl-menu-q" placeholder="Find…" aria-label="Find an owner" autocomplete="off" spellcheck="false">
      <div class="tl-menu-list">${[...opts].map(o => {
        const actor = o.value.includes(':') ? o.value : 'bot:' + o.value;
        return `<button type="button" class="tl-mi tl-opt" data-label="${esc(o.textContent.toLowerCase())}" data-bulk-owner="${esc(o.value)}"><span class="tl-opt-face">${actorFace(actor, 16)}</span><span>${esc(o.textContent)}</span></button>`;
      }).join('')}</div></div>`, pop => {
      const q = pop.querySelector<HTMLInputElement>('.tl-menu-q')!;
      q.oninput = () => { for (const o of pop.querySelectorAll<HTMLElement>('.tl-opt')) o.hidden = !o.dataset.label!.includes(q.value.trim().toLowerCase()); };
      pop.onclick = ev => {
        const b = eventElement(ev)?.closest<HTMLElement>('[data-bulk-owner]'); if (!b) return;
        const owner = b.dataset.bulkOwner!, actor = owner.includes(':') ? owner : 'bot:' + owner;
        void tasksBulkApply(state, {done: `handed to ${actorLabel(actor)}`, already: `with ${actorLabel(actor)}`}, t => t.owner_id === actor ? null : {owner: actor}, {action: 'owner', owner: actor});
      };
    });
  } else if (kind === 'tags') {
    const tasks = tasksSelectedTasks(state);
    const keys = tasksFilterOptions(state, 'tag');
    tasksMenu(state, anchor, `<div class="tl-menu" role="group" aria-label="Tags"><div class="tl-menu-h" aria-hidden="true">Tags</div>
      <form class="tl-menu-new"><input type="text" placeholder="New tag" aria-label="New tag" autocomplete="off" spellcheck="false"></form>
      <div class="tl-menu-list">${keys.map(o => {
        const all = tasks.every(t => (t.labels || []).includes(o.value));
        return `<button type="button" aria-pressed="${all}" class="tl-mi" data-bulk-tag="${esc(o.value)}"><span class="tl-dot" style="--hue:${tagHue(o.value)}"></span><span>${esc(o.label)}</span>${all ? '<span class="tl-mi-on" aria-hidden="true">✓</span>' : ''}</button>`;
      }).join('')}</div></div>`, pop => {
      const apply = (tag: string, remove: boolean) => void tasksBulkApply(state, {done: remove ? `untagged ${tag}` : `tagged ${tag}`, already: remove ? 'untagged' : 'tagged'}, t => {
        const has = (t.labels || []).includes(tag);
        if (remove ? !has : has) return null;
        return {labels: remove ? t.labels.filter(l => l !== tag) : [...(t.labels || []), tag]};
      }, {action: 'tags', [remove ? 'remove' : 'add']: [tag]});
      pop.querySelector<HTMLFormElement>('.tl-menu-new')!.onsubmit = ev => {
        ev.preventDefault();
        const tag = (ev.target as HTMLFormElement).querySelector('input')!.value.trim().toLowerCase();
        if (tag) apply(tag, false);
      };
      pop.onclick = ev => {
        const b = eventElement(ev)?.closest<HTMLElement>('[data-bulk-tag]'); if (!b) return;
        apply(b.dataset.bulkTag!, b.getAttribute('aria-pressed') === 'true');
      };
    });
  }
}
