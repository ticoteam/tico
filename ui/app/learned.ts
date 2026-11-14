/* ui/app/learned.ts — "What it has learned": what your chat bot knows about how you like things (docs/assistant.md)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** One thing the chat bot learned, as GET /v2/assistant/learned sends it (backend/learned.py _view). */
interface LearnedEntry {
  id: string;
  text: string;
  created_at: string;
  updated_at: string | null;
  taught_in_chat: boolean;
}

/** The company layer the owner sees: the vision, the priorities (company goals) and what the team taught it. */
interface LearnedCompany {
  vision: string | null;
  priorities: { id: string; title: string }[];
  entries: LearnedEntry[];
}

/** GET /v2/assistant/learned: your own layer, and the company layer when you may read it. */
interface LearnedPage {
  personal: LearnedEntry[];
  company: LearnedCompany | null;
}

// #/assistant/learned, linked from the top of your chat (ui/app/assistant-page.ts). GET /v2/assistant/learned gives your
// own layer, how you like things ranked and summarised, which only you see; and for the owner the company layer their
// Assistant also uses: the vision (changed on Strategy) and the company goals (changed on Goals) and what they taught
// it about the company. You teach it in the chat; here you edit or delete what it saved (PATCH / DELETE /v2/assistant/learned/{id}). Your chat bot reads
// all of it again before each reply, so a deleted entry is not used from the next one.
let LEARNED_LOAD = 0;
// The shared popover's host for an entry's ⋯ (task-list-menus.ts tasksMenu keeps its anchor here).
const LEARNED_MENU: TasksMenuHost = {};
// Where it came from and when it last changed (no "edited" mark: the age is the last change's).
const learnedWhen = (e: LearnedEntry) => `${e.taught_in_chat ? 'Taught in chat' : 'Added by you'} · ${ago(e.updated_at || e.created_at)}`;
// An entry: its words, then one muted line with where it came from and ⋯ last (Edit, then Delete in red after a line).
const learnedItemHTML = (e: LearnedEntry) => `<li class="learned-item" data-learned="${esc(e.id)}">
    <div class="learned-text">${esc(e.text)}</div>
    <div class="learned-meta"><span class="muted" title="${esc(fmt(e.updated_at || e.created_at))}">${esc(learnedWhen(e))}</span>
      <button type="button" class="tl-ib learned-more" data-learned-more aria-haspopup="menu" aria-expanded="false" aria-label="More" title="More"><span class="nav-icon" aria-hidden="true">more_horiz</span></button></div></li>`;
const learnedListHTML = (entries: LearnedEntry[], empty: string) => entries.length
  ? `<ul class="learned-list">${entries.map(learnedItemHTML).join('')}</ul>` : `<p class="muted learned-empty">${esc(empty)}</p>`;

async function pageLearned() {
  const load = ++LEARNED_LOAD, name = myChatName();
  // A sub-page of your chat: the shared header's back link reads "Assistant › What it has learned" (ui/app/page-head.ts).
  $('#main').innerHTML = `<div class="learned-page">
    ${pageHead({title: 'What it has learned', hook: 'learned-head', back: {href: ASSISTANT, label: name}})}
    <section class="card learned-sec viewers" id="assistant-viewers" aria-label="Bots that can view this chat"></section>
    <div id="learned-body">${skelRows(3, {card: true})}</div></div><div id="task-filter-pop" class="tl-pop" popover></div>`;
  const pop = $('#task-filter-pop') as HTMLElement;
  pop.addEventListener('keydown', tasksMenuKeys);
  pop.addEventListener('toggle', (ev: Event & {newState?: string}) => {
    const anchor = LEARNED_MENU.menuAnchor;
    anchor?.setAttribute('aria-expanded', String(ev.newState === 'open'));
    if (ev.newState === 'closed' && anchor?.isConnected && (pop.contains(document.activeElement) || document.activeElement === document.body)) anchor.focus();
  });
  void viewersMount($('#assistant-viewers'), name);
  let d: LearnedPage;
  try { d = await get<LearnedPage>('/v2/assistant/learned'); }
  catch (e) {
    if (load !== LEARNED_LOAD || !$('#learned-body')) return;
    $('#learned-body').innerHTML = `<div class="empty-state" role="alert"><p>${esc(errorMessage(e, ''))}</p><button type="button" class="ghost" data-learned-retry>Retry</button></div>`;
    $('[data-learned-retry]').onclick = () => void pageLearned();
    return;
  }
  if (load !== LEARNED_LOAD || S.route !== LEARNED) return;
  learnedRender(d, name);
}

function learnedRender(d: LearnedPage, name: string) {
  const body = $('#learned-body') as HTMLElement | null;
  if (!body) return;
  const co = d.company;
  body.innerHTML = `
    <section class="card learned-sec" id="learned-personal" aria-labelledby="learned-personal-h">
      <h2 id="learned-personal-h">About you${learnedLock('Only you see this')}</h2>
      ${learnedListHTML(d.personal || [], `Nothing yet. Tell ${name} in your chat what matters most to you.`)}
    </section>
    ${co ? `<section class="card learned-sec" id="learned-company" aria-labelledby="learned-company-h">
      <h2 id="learned-company-h">About the company${learnedLock(`Only you change this, and only ${name} uses it`)}</h2>
      <h3>Vision</h3>
      <p class="learned-vision">${co.vision ? esc(co.vision) : '<span class="muted">Not written yet.</span>'} <a href="${STRATEGY}">Edit on Strategy</a></p>
      <h3>Priorities</h3>
      ${(co.priorities || []).length ? `<ol class="learned-priorities">${co.priorities.map(g => `<li><a href="${GOALS}">${esc(g.title)}</a></li>`).join('')}</ol>`
        : `<p class="muted">No company goals yet. <a href="${GOALS}">Add them on Goals</a></p>`}
      <h3>What you taught it</h3>
      ${learnedListHTML(co.entries || [], `Nothing yet. Tell ${name} in your chat what matters to the company.`)}
    </section>` : ''}`;
  body.onclick = ev => {
    const target = eventElement(ev);
    const item = target?.closest<HTMLElement>('[data-learned]');
    if (!item) return;
    const more = target?.closest<HTMLButtonElement>('[data-learned-more]');
    if (more) learnedMenu(more, item, d, name);
  };
}
// Who sees a section, as a mark rather than a caption: a lock with the words for hover and screen readers.
const learnedLock = (words: string): string => ` <span class="nav-icon learned-lock" role="img" aria-label="${esc(words)}" title="${esc(words)}">lock</span>`;
// An entry's ⋯: Edit, then Delete in red after a line (the shared popover, task-list-menus.ts).
function learnedMenu(button: HTMLButtonElement, item: HTMLElement, d: LearnedPage, name: string): void {
  tasksMenu(LEARNED_MENU, button, `<div class="tl-menu" role="menu" aria-label="Entry"><button type="button" class="tl-mi" role="menuitem" data-learned-edit>Edit</button>`
    + '<div class="tl-menu-sep" role="separator"></div><button type="button" class="tl-mi danger" role="menuitem" data-learned-delete>Delete</button></div>', pop => {
    pop.onclick = ev => {
      const target = eventElement(ev);
      if (target?.closest('[data-learned-edit]')) { tasksMenuClose(); learnedEdit(item, d, name); }
      else if (target?.closest('[data-learned-delete]')) { tasksMenuClose(); void learnedDelete(item, button, d, name); }
    };
  });
}

function learnedFind(d: LearnedPage, id: string): LearnedEntry | undefined {
  return [...(d.personal || []), ...(d.company?.entries || [])].find(e => e.id === id);
}

function learnedEdit(item: HTMLElement, d: LearnedPage, name: string) {
  const entry = learnedFind(d, item.dataset.learned ?? '');
  if (!entry || item.querySelector('.learned-input')) return;
  item.innerHTML = `<label class="sr-only" for="learned-input-${esc(entry.id)}">Edit what it learned</label>
    <input class="learned-input" id="learned-input-${esc(entry.id)}" maxlength="280" value="${esc(entry.text)}">
    <div class="learned-meta learned-btns"><button type="button" class="ghost" data-learned-cancel>Cancel</button><button type="button" class="primary" data-learned-save>Save</button></div>`;
  const input = item.querySelector<HTMLInputElement>('.learned-input');
  if (!input) throw new Error('learned edit: the input was not drawn');
  input.focus();
  input.select();
  const save = async () => {
    const text = input.value.trim();
    if (!text) { toast('Say what it learned, or delete it', true); return; }
    if (text === entry.text) { learnedRender(d, name); return; }
    const btn = item.querySelector<HTMLButtonElement>('[data-learned-save]');
    if (!btn) throw new Error('learned edit: the save button was not drawn');
    btn.disabled = true;
    try {
      const r: {entry: LearnedEntry} = await patch(`/v2/assistant/learned/${encodeURIComponent(entry.id)}`, {text});
      Object.assign(entry, r.entry);
      learnedRender(d, name);
      toast('Saved');
    } catch (e) { btn.disabled = false; toast(errorMessage(e, ''), true); }
  };
  item.onclick = ev => {
    ev.stopPropagation();
    const target = eventElement(ev);
    if (target?.closest('[data-learned-save]')) void save();
    else if (target?.closest('[data-learned-cancel]')) learnedRender(d, name);
  };
  input.onkeydown = ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); void save(); }
    else if (ev.key === 'Escape') learnedRender(d, name);
  };
}

// Delete asks once more, then the entry is gone for good: not used from the next reply on. `btn` is the entry's ⋯, held
// while the question is up and the delete is sent.
async function learnedDelete(item: HTMLElement, btn: HTMLButtonElement, d: LearnedPage, name: string) {
  if (!(await askConfirm('Delete this for good? It is not used from the next reply on.', btn))) { btn.focus(); return; }
  btn.disabled = true;
  const id = item.dataset.learned ?? '';
  try {
    await writeRequest('DELETE', `/v2/assistant/learned/${encodeURIComponent(id)}`);
    d.personal = (d.personal || []).filter(e => e.id !== id);
    if (d.company) d.company.entries = (d.company.entries || []).filter(e => e.id !== id);
    learnedRender(d, name);
    toast('Deleted');
  } catch (e) { btn.disabled = false; toast(errorMessage(e, ''), true); }
}
