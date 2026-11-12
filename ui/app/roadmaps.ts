/* Roadmap publication, internal requesters, ranking and company settings. */
'use strict';

// A requester of a task as GET /v2/tasks/{id}/requesters lists it: a teammate or an outside person.
interface RoadmapRequester {
  id: string;
  kind: string;
  source: string;
  actor?: string | null;
  name?: string | null;
  email?: string | null;
  phone?: string | null;
  other?: string | null;
  organization?: string | null;
  note?: string | null;
  important?: boolean;
}
interface RoadmapRequestersPage {
  requesters?: RoadmapRequester[];
  next_offset?: number | null;
  my_requester_id?: string | null;
}
// The Roadmap page's sort, direction and paging. ROADMAP_RANKING holds it while the page is open.
interface RoadmapRankingState {
  sort: string | undefined;
  direction: 'asc' | 'desc';
  offset: number;
  previous: number[];
  next: number | null | undefined;
  seq: number;
  menuAnchor?: HTMLElement | null;
  menuField?: string;
}
interface RoadmapRankTask {
  id: string;
  title: string;
  step?: string | null;
  requester_count?: number | null;
  external_requesters?: number | null;
  important_requesters?: number | null;
  organization?: string | null;
  size?: number | string | null;
}
interface RoadmapRankPage {
  tasks?: RoadmapRankTask[];
  next_offset?: number | null;
}
interface RoadmapComment {
  id: string;
  kind?: string;
  public?: boolean;
  hidden_at?: string | null;
  refs?: { comment?: unknown; [key: string]: unknown } | null;
}
interface RoadmapReceiver {
  source?: string;
  bot?: string | null;
  name?: string | null;
}
interface RoadmapSettings {
  labels: string[];
  cors_origins: string[];
  suggestions_bot?: string | null;
  suggestions_receiver?: RoadmapReceiver | null;
  action_limit: number;
  suggestion_limit?: number | null;
  bot_public_approval?: boolean;
}
interface RoadmapTags {
  tags?: { key: string; label?: string | null; is_template?: boolean }[];
}

// A roadmap task is one in the Product pipeline (D31); its id is the preset's until the pipelines list says otherwise.
let ROADMAP_PRODUCT_ID = 'product';
const roadmapTask = (t: PropTask): boolean => t.pipeline_id === ROADMAP_PRODUCT_ID;
const REQUEST_SOURCE: Record<string, string> = {manual: 'Added by hand', public_form: 'Public roadmap', inbox: 'Email', meeting: 'Meeting'};
const ROADMAP_SORTS = [['requester_count', 'Requests'], ['external_requesters', 'External'], ['important', 'Important'], ['organization', 'Organization'], ['size', 'Size']];

function roadmapTaskHTML(t: PropTask): string {
  if (!roadmapTask(t) || !('public_title' in t) || !('public_summary' in t)) return '';
  const edit = taskPropRights(t).reopen;
  return `<section class="tsec roadmap-requesters" aria-label="Requested by"><h3 class="rail-h">Requested by
      <button class="requester-chip" type="button" data-requester-self aria-pressed="false">I want this</button>
      ${edit ? `<button type="button" class="prop-add" data-requester-add aria-label="Add requester" title="Add requester">${TL_ICON.plus}</button>` : ''}</h3>
      <div data-requester-list></div></section>
    <section class="tsec roadmap-publication"><h3 class="rail-h">Public text</h3>
      <small>${t.visibility === 'public' ? 'Shown on the public roadmap.' : 'Written separately from the internal title and body; a public title is needed to publish.'}</small>
      <label>Title<input data-public-field="public_title" data-task-field="public_title" aria-label="Public title" maxlength="300" value="${esc(t.public_title ?? '')}" placeholder="Needed to publish" ${edit ? '' : 'readonly'}></label>
      <label>Summary<textarea data-public-field="public_summary" data-task-field="public_summary" aria-label="Public summary" rows="2" maxlength="8000" placeholder="Optional" ${edit ? '' : 'readonly'}>${esc(t.public_summary ?? '')}</textarea></label>
    </section>`;
}
function roadmapTaskBind(d: TaskDialog, task: PropTask, change: unknown) {
  if (!roadmapTask(task)) return;
  const add = $('[data-requester-add]', d);
  if (add) add.onclick = () => roadmapRequesterEditor(d, task);
  void roadmapRequestersLoad(d, task);
}
async function roadmapRequestersLoad(d: TaskDialog, task: PropTask, offset: number | null = 0, previous: RoadmapRequester[] = []) {
  const host: HTMLElement | null = $('[data-requester-list]', d);
  if (!host) return;
  try {
    const data = await get<RoadmapRequestersPage>(`/v2/tasks/${encodeURIComponent(task.id)}/requesters?offset=${offset}`);
    if (!host.isConnected || String(d.dataset.task) !== String(task.id)) return;
    const rows = [...previous, ...(data.requesters || [])], edit = taskPropRights(task).reopen;
    const self = $('[data-requester-self]', d), own: { id: string } | null = rows.find(r => r.actor === myActor()) || (data.my_requester_id ? {id: data.my_requester_id} : null);
    if (self) {
      self.disabled = false; self.textContent = own ? 'Requested' : 'I want this'; self.setAttribute('aria-pressed', String(!!own));
      self.onclick = async () => {
        self.disabled = true;
        try {
          const base = `/v2/tasks/${encodeURIComponent(task.id)}/requesters`;
          await post(base + (own ? '/' + encodeURIComponent(own.id) + '/delete' : ''), own ? {} : {actor: myActor()});
          await roadmapRequestersLoad(d, task); roadmapRankingRefresh();
        } catch (error) { toast(errorMessage(error, ''), true); self.disabled = false; }
      };
    }
    const group = (kind: string, label: string): string => {
      const items = rows.filter(r => r.kind === kind);
      if (!items.length) return '';
      return `<div class="requester-group"><h4 class="rail-h">${label}</h4>${items.map(r => {
        const contacts: (string | null | undefined)[] = [r.email, r.phone, r.other];
        if (kind === 'external' && r.source !== 'manual') contacts.push(REQUEST_SOURCE[r.source]);
        return `<${edit ? 'button type="button"' : 'div'} class="requester-row" ${edit ? `data-requester-edit="${esc(r.id)}"` : ''}>
          <span class="requester-name">${r.actor ? actorFace(r.actor, 16) : ''}<strong>${esc(r.name || (r.actor ? actorLabel(r.actor) : r.email || 'Anonymous'))}</strong>${r.important ? '<span class="muted">Important</span>' : ''}${r.organization ? `<span class="muted">${esc(r.organization)}</span>` : ''}</span>
          ${contacts.some(Boolean) ? `<span class="requester-contacts">${contacts.filter(Boolean).map(esc).join(' · ')}</span>` : ''}
          ${r.note ? `<span class="requester-note">${esc(r.note)}</span>` : ''}</${edit ? 'button' : 'div'}>`;
      }).join('')}</div>`;
    };
    host.innerHTML = group('external', 'External') + group('teammate', 'Team');
    if (data.next_offset != null) host.insertAdjacentHTML('beforeend', '<button class="ghost" type="button" data-requester-more>Show more</button>');
    $('[data-requester-more]', host)?.addEventListener('click', () => roadmapRequestersLoad(d, task, data.next_offset, rows));
    host.querySelectorAll<HTMLElement>('[data-requester-edit]').forEach(button => button.onclick = () => roadmapRequesterEditor(d, task, rows.find(r => r.id === button.dataset.requesterEdit)));
  } catch (error) { if (host.isConnected) host.textContent = errorMessage(error, ''); }
}
function roadmapRequesterEditor(parent: TaskDialog, task: PropTask, requester: RoadmapRequester | null = null) {
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal requester-editor';
  dialog.setAttribute('aria-label', requester ? 'Edit requester' : 'Add requester');
  const fields: [keyof RoadmapRequester, string, number][] = [['name', 'Name', 200], ['email', 'Email', 320], ['phone', 'Phone', 100], ['other', 'Other contact', 500]];
  const actors = [...S.people.map(p => ({value: 'human:' + p.id, text: p.name || p.id})), ...S.emps.map(b => ({value: 'bot:' + b.id, text: b.display_name || b.slug}))];
  dialog.innerHTML = `<div class="tmodal-head"><h2>${requester ? 'Edit requester' : 'Add requester'}</h2><span class="spacer"></span><button type="button" class="ghost" data-close aria-label="Close">✕</button></div>
    <form class="tmodal-body roadmap-form"><label>Requester<select name="actor" ${requester ? 'disabled' : ''}><option value="">Someone outside the team</option>${actors.map(a => `<option value="${esc(a.value)}"${requester?.actor === a.value ? ' selected' : ''}>${esc(a.text)}</option>`).join('')}</select></label>
      <div class="roadmap-fields" data-external-fields>${fields.map(([key, label, max]) => `<label>${label}<input name="${key}" maxlength="${max}" value="${esc(requester?.[key] || '')}"></label>`).join('')}</div>
      <label>Organization<input name="organization" maxlength="200" value="${esc(requester?.organization || '')}"></label>
      <label>Quote or note<textarea name="note" rows="2" maxlength="4000">${esc(requester?.note || '')}</textarea></label>
      <label data-requester-source>Source<select name="source">${Object.entries(REQUEST_SOURCE).map(([value, label]) => `<option value="${value}"${requester?.source === value ? ' selected' : ''}>${label}</option>`).join('')}</select></label>
      <label class="roadmap-check"><input type="checkbox" name="important"${requester?.important ? ' checked' : ''}>Important</label>
      <div class="row"><button class="primary" type="submit">Save</button>${requester ? '<button type="button" class="ghost danger" data-requester-remove>Remove</button>' : ''}<span data-error class="err" role="status"></span></div></form>`;
  document.body.append(dialog); dialog.showModal();
  $('[data-close]', dialog).onclick = () => dialog.close(); dialog.addEventListener('close', () => dialog.remove());
  const form = $('form', dialog), actor = form.elements.actor;
  const fieldsVisible = () => {
    const external: HTMLElement = $('[data-external-fields]', dialog);
    external.hidden = !!actor.value; $('[data-requester-source]', dialog).hidden = !!actor.value;
    external.querySelectorAll<HTMLInputElement>('input').forEach(input => input.disabled = !!actor.value);
    form.elements.source.disabled = !!actor.value;
  };
  actor.onchange = fieldsVisible; fieldsVisible();
  const after = async () => { dialog.close(); await roadmapRequestersLoad(parent, task); roadmapRankingRefresh(); };
  form.onsubmit = async (ev: Event) => {
    ev.preventDefault(); const button = $('[type=submit]', form), body = Object.fromEntries(new FormData(form));
    body.important = form.elements.important.checked; if (!body.actor) delete body.actor;
    button.disabled = true;
    try { await post(`/v2/tasks/${encodeURIComponent(task.id)}/requesters` + (requester ? '/' + encodeURIComponent(requester.id) : ''), body); await after(); }
    catch (error) { $('[data-error]', dialog).textContent = errorMessage(error, ''); button.disabled = false; }
  };
  const remove: HTMLButtonElement | null = $('[data-requester-remove]', dialog);
  remove?.addEventListener('click', async () => {
    if (remove) remove.disabled = true;
    try { await post(`/v2/tasks/${encodeURIComponent(task.id)}/requesters/${encodeURIComponent(requester?.id ?? '')}/delete`, {}); await after(); }
    catch (error) { $('[data-error]', dialog).textContent = errorMessage(error, ''); $('[data-requester-remove]', dialog).disabled = false; }
  });
}
function roadmapCommentMark(m: RoadmapComment, taskId: string | number): string {
  if (!m.public || !m.refs?.comment || m.kind !== 'say') return '';
  const mark = `<span class="pill">${m.hidden_at ? 'Hidden' : 'Public'}</span>`;
  const task = TASK_CHAT?.id === taskId ? TASK_CHAT.dialog.liveTask : null;
  if (!task || !taskPropRights(task).reopen) return mark;
  return mark + `<span class="roadmap-moderation"><button type="button" data-public-moderate="${m.hidden_at ? 'show' : 'hide'}" data-message="${esc(m.id)}">${m.hidden_at ? 'Restore' : 'Hide'}</button><button type="button" data-public-moderate="delete" data-message="${esc(m.id)}">Delete</button></span>`;
}
function roadmapCommentsBind(state: TaskChatState) {
  const form = $('form', state.host);
  if (form && !$('[data-comment-public]', form) && state.dialog.liveTask?.visibility === 'public' && !state.dialog.liveTask?.private) {
    $('button[type=submit]', form).insertAdjacentHTML('beforebegin', '<label class="prop-check roadmap-check"><input type="checkbox" data-comment-public>Public</label>');
  }
  state.host.querySelectorAll<HTMLButtonElement>('[data-public-moderate]').forEach(button => button.onclick = async () => {
    if (button.dataset.publicModerate === 'delete' && !(await askConfirm('Delete this public comment?', button))) return;
    button.disabled = true;
    try { await post(`/v2/tasks/${encodeURIComponent(state.id)}/comments/${encodeURIComponent(String(button.dataset.message))}/moderate`, {action: button.dataset.publicModerate}); await taskChatRead(state); }
    catch (error) { toast(errorMessage(error, ''), true); button.disabled = false; }
  });
}
let ROADMAP_RANKING: RoadmapRankingState | null = null;
function roadmapRankingRefresh() { if (S.route === '#/roadmap' && ROADMAP_RANKING) void roadmapRankingLoad(ROADMAP_RANKING); }
// The public roadmap reads the Product pipeline (D31).
const roadmapPipeline = () => pipelinesAll().then(list => {
  const product = list.find(p => !p.retired_at && (p.preset === 'product' || p.id === 'product'));
  if (product) ROADMAP_PRODUCT_ID = product.id;
  return product;
}).catch(() => undefined);
function pageRoadmap() {
  const state: RoadmapRankingState = ROADMAP_RANKING = {sort: 'requester_count', direction: 'desc', offset: 0, previous: [], next: null, seq: 0};
  $('#main').innerHTML = `<div class="roadmap-page"><header class="meeting-head"><h1 data-roadmap-title></h1><button class="primary" type="button" data-roadmap-new>New task</button></header>
    <div class="roadmap-sort"><button type="button" class="tl-chip tl-chip-main tl-groupchip" data-roadmap-sort aria-haspopup="menu"><span class="k">Sort</span><span data-sort-word>Requests</span>${TL_ICON.caret}</button><button class="ghost" type="button" data-roadmap-direction aria-label="Sort ascending">↓</button></div>
    <div data-roadmap-table></div><div class="row roadmap-pager"><button class="ghost" type="button" data-roadmap-back disabled>Previous</button><button class="ghost" type="button" data-roadmap-next disabled>Next</button><span class="err" role="status" data-roadmap-error></span></div></div><div id="task-filter-pop" class="tl-pop" popover></div>`;
  $('#task-filter-pop').onkeydown = tasksMenuKeys;
  $('[data-roadmap-new]').onclick = () => { void roadmapPipeline().then(product => openTaskCreate(botByRef(product?.manager_id)?.id || '', {pipeline: product?.id || 'product'})); };
  $('[data-roadmap-sort]').onclick = (ev: MouseEvent) => tasksMenu(state, ev.currentTarget as HTMLElement | null, `<div class="tl-menu" role="menu" aria-label="Sort roadmap">${ROADMAP_SORTS.map(([key, label]) => `<button type="button" class="tl-mi" role="menuitemradio" aria-checked="${state.sort === key}" data-sort="${key}">${label}</button>`).join('')}</div>`, (pop: HTMLElement) => pop.onclick = (e: MouseEvent) => { const button = eventElement(e)?.closest<HTMLElement>('[data-sort]'); if (button) { state.sort = button.dataset.sort; state.offset = 0; state.previous = []; tasksMenuClose(); void roadmapRankingLoad(state); } });
  $('[data-roadmap-direction]').onclick = () => { state.direction = state.direction === 'desc' ? 'asc' : 'desc'; state.offset = 0; state.previous = []; void roadmapRankingLoad(state); };
  $('[data-roadmap-back]').onclick = () => { state.offset = state.previous.pop() || 0; void roadmapRankingLoad(state); };
  $('[data-roadmap-next]').onclick = () => { if (state.next != null) { state.previous.push(state.offset); state.offset = state.next; void roadmapRankingLoad(state); } };
  void roadmapRankingLoad(state);
  void roadmapPipeline().then(product => { const title = $('[data-roadmap-title]'); if (title) title.textContent = product?.name || ''; });
}
async function roadmapRankingLoad(state: RoadmapRankingState) {
  const seq = ++state.seq;
  try {
    const data = await get<RoadmapRankPage>(`/v2/roadmap-tasks?sort=${state.sort}&direction=${state.direction}&offset=${state.offset}&limit=25`);
    if (ROADMAP_RANKING !== state || S.route !== '#/roadmap' || seq !== state.seq) return;
    state.next = data.next_offset;
    const columns: [string, string][] = [['requester_count', 'Requests'], ['important', 'Important'], ['organization', 'Organization'], ['size', 'Size']];
    const requests = (t: RoadmapRankTask): string => `${t.requester_count || 0} (${t.external_requesters || 0} ext)`;
    $('[data-sort-word]').textContent = ROADMAP_SORTS.find(([key]) => state.sort === key)?.[1] || 'Requests';
    const direction = $('[data-roadmap-direction]'); direction.textContent = state.direction === 'desc' ? '↓' : '↑'; direction.setAttribute('aria-label', state.direction === 'desc' ? 'Sort ascending' : 'Sort descending');
    const tasks = data.tasks || [];
    $('[data-roadmap-table]').innerHTML = tasks.length ? `<table class="roadmap-table"><thead><tr><th>Task</th>${columns.map(([key, label]) => `<th aria-sort="${state.sort === key ? (state.direction === 'asc' ? 'ascending' : 'descending') : 'none'}"><button type="button" data-sort-column="${key}">${label}</button></th>`).join('')}</tr></thead><tbody>${tasks.map(t => `<tr tabindex="0" role="button" data-roadmap-task="${esc(t.id)}" aria-label="${esc(t.title)}"><td><strong>${esc(t.title)}</strong><div class="muted">${esc(t.step || '')}</div><div class="roadmap-phone-summary muted">${esc([`${t.requester_count || 0} requests`, `${t.external_requesters || 0} external`, `${t.important_requesters || 0} important`, t.organization].filter(Boolean).join(' · '))}</div></td><td>${requests(t)}</td><td>${t.important_requesters || 0}</td><td>${esc(t.organization || '—')}</td><td>${esc(t.size ?? '—')}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">No roadmap tasks</p>';
    document.querySelectorAll<HTMLElement>('[data-roadmap-task]').forEach(row => { row.onclick = () => { if (row.dataset.roadmapTask) void taskModalOpen(row.dataset.roadmapTask); }; row.onkeydown = ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); row.click(); } }; });
    document.querySelectorAll<HTMLElement>('[data-sort-column]').forEach(button => button.onclick = () => { if (state.sort === button.dataset.sortColumn) state.direction = state.direction === 'asc' ? 'desc' : 'asc'; else state.sort = button.dataset.sortColumn; state.offset = 0; state.previous = []; void roadmapRankingLoad(state); });
    $('[data-roadmap-back]').disabled = !state.offset; $('[data-roadmap-next]').disabled = state.next == null; $('[data-roadmap-error]').textContent = '';
  } catch (error) { if (S.route === '#/roadmap' && seq === state.seq) $('[data-roadmap-error]').textContent = errorMessage(error, ''); }
}
// Set by the #/settings/roadmap route (Health's fix link): bring the Public roadmap card into view once it is drawn.
let ROADMAP_SETTINGS_REVEAL = false;
// Display names of a chosen bot the roster leaves out (archived bots are not in /employees), read on mount.
const ROADMAP_BOT_NAMES: Record<string, string> = {};
const roadmapBotName = (slug: string): string => ROADMAP_BOT_NAMES[slug] || botDisplayName(slug);
// suggestions_receiver (backend/roadmaps.py) resolves Automatic on the server; the screen only says what it answered.
function roadmapReceiverText(settings: RoadmapSettings): string {
  const receiver: RoadmapReceiver = settings.suggestions_receiver || {}, to = receiver.bot ? receiver.name || receiver.bot : '';
  if (receiver.source === 'automatic') return 'Automatic: ' + (to || 'no bot can take suggestions');
  if (receiver.source === 'chosen') return '';
  const why = receiver.source === 'chosen_archived' ? 'is archived' : "isn't active";
  return `The bot chosen for roadmap suggestions ${why}; ` + (to ? `suggestions now go to ${to}` : 'no bot can take suggestions');
}
function roadmapReceiverOptions(settings: RoadmapSettings): string {
  const picked = settings.suggestions_bot || '', chosen = botByRef(picked)?.id || picked, receiver: RoadmapReceiver = settings.suggestions_receiver || {}, source = receiver.source;
  const active = (S.emps || []).filter(e => e.status === 'active')
    .map(e => ({slug: e.id, name: botDisplayName(e.id)})).sort((a, b) => a.name.localeCompare(b.name));
  const option = (value: string, text: string, extra = ''): string => `<option value="${esc(value)}"${value === chosen ? ' selected' : ''}${extra}>${esc(text)}</option>`;
  // A chosen bot that stopped being active stays visible as itself, never as a blank or as Automatic.
  const gone = !chosen || active.some(bot => bot.slug === chosen) ? ''
    : source === 'chosen' ? option(chosen, receiver.name || roadmapBotName(chosen)) : (() => {
    const words: Record<string, string> = {paused: 'paused', planned: 'setting up', quarantined: 'quarantined'};
    const state = source === 'chosen_archived' ? 'archived' : words[botRow(chosen)?.status ?? ''] || 'not active';
    return option(chosen, `${roadmapBotName(chosen)} (${state})`, ' disabled');
  })();
  return option('', chosen ? 'Automatic' : roadmapReceiverText(settings)) + gone + active.map(bot => option(bot.slug, bot.name)).join('');
}
function roadmapSettingsRender(host: HTMLElement, settings: RoadmapSettings, tags: RoadmapTags) {
  const edit = settingsIsAdmin();
  host.innerHTML = `<header><h2>Public roadmap</h2></header><form class="roadmap-form"><fieldset class="roadmap-settings-fields"${edit ? '' : ' disabled'}><div><span>Tags shown publicly</span><div class="roadmap-labels">${(tags.tags || []).filter(t => !t.is_template).map(t => `<label class="roadmap-check"><input type="checkbox" name="label" value="${esc(t.key)}"${settings.labels.includes(t.key) ? ' checked' : ''}>${esc(t.label || t.key)}</label>`).join('')}</div></div>
      <label>Websites that can show it<textarea name="cors_origins" rows="2" placeholder="https://acme.example">${esc(settings.cors_origins.join('\n'))}</textarea></label>
      <div class="roadmap-receiver"><label>Suggestions go to<select name="suggestions_bot" aria-describedby="roadmap-receiver-note">${roadmapReceiverOptions(settings)}</select></label><span class="muted" id="roadmap-receiver-note" data-receiver>${esc(settings.suggestions_bot ? roadmapReceiverText(settings) : '')}</span></div>
      <label>Requests per visitor per day<input class="roadmap-limit" type="number" name="action_limit" min="1" max="10000" required value="${settings.action_limit}"></label>
      <label>Visitor suggestions waiting for review<input class="roadmap-limit" type="number" name="suggestion_limit" min="1" max="1000" required value="${settings.suggestion_limit ?? 20}"></label>
      <label class="roadmap-check">${peopleSwitch('name="bot_public_approval"', settings.bot_public_approval, false, 'Bots need approval to post publicly')}Bots need approval to post publicly</label></fieldset>
      <div class="row">${edit ? '<button class="primary" type="submit">Save</button>' : '<span class="muted">Only owners and admins change these.</span>'}<span class="muted" data-status role="status"></span></div></form>`;
  const form = $('form', host), picker = form.elements.suggestions_bot, saved = botByRef(settings.suggestions_bot)?.id || settings.suggestions_bot || '';
  picker.onchange = () => { $('[data-receiver]', form).textContent = picker.value !== saved ? 'Takes effect for the next suggestion once you save.' : saved ? roadmapReceiverText(settings) : ''; };
  form.onsubmit = async (ev: Event) => {
    ev.preventDefault(); const values = new FormData(form), button = $('[type=submit]', form), status = $('[data-status]', form);
    const body: Record<string, unknown> = {labels: values.getAll('label'), cors_origins: String(values.get('cors_origins')).split(/[\n,]/).map(s => s.trim()).filter(Boolean), action_limit: Number(values.get('action_limit')), suggestion_limit: Number(values.get('suggestion_limit')), bot_public_approval: form.elements.bot_public_approval.checked};
    // Sent only when changed: the server refuses an inactive bot, and an archived choice must not block saving the rest.
    if (picker.value !== saved) body.suggestions_bot = picker.value || null;
    button.disabled = true; status.textContent = '';
    try {
      const next: RoadmapSettings = await post('/v2/roadmaps/settings', body);
      if (!host.isConnected) return;
      roadmapSettingsRender(host, next, tags); $('[data-status]', host).textContent = 'Saved'; $('[type=submit]', host).focus();
    } catch (error) { status.textContent = errorMessage(error, ''); button.disabled = false; }
  };
}
function roadmapSettingsReveal(host: HTMLElement) {
  if (!ROADMAP_SETTINGS_REVEAL || host.dataset.loaded !== 'yes') return;
  ROADMAP_SETTINGS_REVEAL = false;
  // After the frame: route() resets #main's scroll once the page is drawn, which would undo a scroll made now.
  requestAnimationFrame(() => {
    if (!host.isConnected) return;
    host.scrollIntoView({block: 'start'});
    $('select[name=suggestions_bot]', host)?.focus({preventScroll: true});
  });
}
async function roadmapSettingsMount() {
  const host: HTMLElement | null = $('#settings-roadmaps');
  if (!host) return;
  if (host.dataset.loaded) { roadmapSettingsReveal(host); return; }
  host.dataset.loaded = 'loading';
  try {
    const [settings, tags] = await Promise.all([get<RoadmapSettings>('/v2/roadmaps/settings'), get<RoadmapTags>('/v2/tags')]);
    const chosen = settings.suggestions_bot;
    if (chosen && !botByRef(chosen))
      ROADMAP_BOT_NAMES[chosen] = await get<{ display_name?: string | null }>('/v2/bots/' + encodeURIComponent(chosen)).then(bot => bot.display_name || '', () => '');
    if (!host.isConnected) return;
    roadmapSettingsRender(host, settings, tags);
    host.dataset.loaded = 'yes';
    roadmapSettingsReveal(host);
  } catch (error) { if (host.isConnected) { host.textContent = errorMessage(error, ''); delete host.dataset.loaded; } }
}
