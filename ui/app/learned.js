/* ui/app/learned.js — "What it has learned": what your chat bot knows about how you like things (docs/assistant.md)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// #/assistant/learned, linked from the top of your chat (ui/app/assistant-page.js). GET /v2/assistant/learned gives your
// own layer, how you like things ranked and summarised, which only you see; and for the owner the company layer their
// Assistant also uses: the vision (changed on Strategy) and the company goals (changed on Goals) and what they taught
// it about the company. You teach it in the chat; here you edit or delete what it saved (PATCH / DELETE /v2/assistant/learned/{id}). Your chat bot reads
// all of it again before each reply, so a deleted entry is not used from the next one.
let LEARNED_LOAD = 0;
const learnedWhen = e => `${e.taught_in_chat ? 'Taught in chat' : 'Added by you'} · ${e.updated_at ? 'edited ' + ago(e.updated_at) : ago(e.created_at)}`;
const learnedItemHTML = e => `<li class="learned-item" data-learned="${esc(e.id)}">
    <div class="learned-text">${esc(e.text)}</div>
    <div class="learned-meta"><span class="muted" title="${esc(fmt(e.updated_at || e.created_at))}">${esc(learnedWhen(e))}</span>
      <button type="button" class="ghost" data-learned-edit>Edit</button>
      <button type="button" class="ghost" data-learned-delete>Delete</button></div></li>`;
const learnedListHTML = (entries, empty) => entries.length
  ? `<ul class="learned-list">${entries.map(learnedItemHTML).join('')}</ul>` : `<p class="muted learned-empty">${esc(empty)}</p>`;

async function pageLearned() {
  const load = ++LEARNED_LOAD, name = myChatName();
  $('#main').innerHTML = `<div class="learned-page">
    <div class="learned-head"><a class="ghost learned-back" href="${ASSISTANT}">← ${esc(name)}</a>
      <h1>What it has learned</h1>
      <p class="muted">${esc(name)} reads this before every reply in your chat. Teach it there, for example “launches matter
        more than hiring news”; change or delete anything here.</p></div>
    <div id="learned-body"><div class="empty">Loading…</div></div></div>`;
  let d;
  try { d = await get('/v2/assistant/learned'); }
  catch (e) { if (load === LEARNED_LOAD && $('#learned-body')) $('#learned-body').innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  if (load !== LEARNED_LOAD || S.route !== LEARNED) return;
  learnedRender(d, name);
}

function learnedRender(d, name) {
  const body = $('#learned-body');
  if (!body) return;
  const co = d.company;
  body.innerHTML = `
    <section class="card learned-sec" id="learned-personal" aria-labelledby="learned-personal-h">
      <h2 id="learned-personal-h">About you</h2>
      <p class="muted">How you like things ranked and summarised. Only you see this.</p>
      ${learnedListHTML(d.personal || [], `Nothing yet. Tell ${name} in your chat what matters most to you.`)}
    </section>
    ${co ? `<section class="card learned-sec" id="learned-company" aria-labelledby="learned-company-h">
      <h2 id="learned-company-h">About the company</h2>
      <p class="muted">Only you change this, and only ${esc(name)} uses it.</p>
      <h3>Vision</h3>
      <p class="learned-vision">${co.vision ? esc(co.vision) : '<span class="muted">Not written yet.</span>'} <a href="${STRATEGY}">Edit on Strategy</a></p>
      <h3>Priorities</h3>
      ${(co.priorities || []).length ? `<ol class="learned-priorities">${co.priorities.map(g => `<li><a href="${GOALS}">${esc(g.title)}</a></li>`).join('')}</ol>`
        : `<p class="muted">No company goals yet. <a href="${GOALS}">Add them on Goals</a></p>`}
      <h3>What you taught it</h3>
      ${learnedListHTML(co.entries || [], `Nothing yet. Tell ${name} in your chat what matters to the company.`)}
    </section>` : ''}`;
  body.onclick = ev => {
    const item = ev.target.closest('[data-learned]');
    if (!item) return;
    if (ev.target.closest('[data-learned-edit]')) learnedEdit(item, d, name);
    else if (ev.target.closest('[data-learned-delete]')) void learnedDelete(item, ev.target.closest('[data-learned-delete]'), d, name);
  };
}

function learnedFind(d, id) {
  return [...(d.personal || []), ...(d.company?.entries || [])].find(e => e.id === id);
}

function learnedEdit(item, d, name) {
  const entry = learnedFind(d, item.dataset.learned);
  if (!entry || item.querySelector('.learned-input')) return;
  item.innerHTML = `<label class="sr-only" for="learned-input-${esc(entry.id)}">Edit what it learned</label>
    <input class="learned-input" id="learned-input-${esc(entry.id)}" maxlength="280" value="${esc(entry.text)}">
    <div class="learned-meta"><button type="button" class="primary" data-learned-save>Save</button>
      <button type="button" class="ghost" data-learned-cancel>Cancel</button></div>`;
  const input = item.querySelector('.learned-input');
  input.focus();
  input.select();
  const save = async () => {
    const text = input.value.trim();
    if (!text) { toast('Say what it learned, or delete it', true); return; }
    if (text === entry.text) { learnedRender(d, name); return; }
    const btn = item.querySelector('[data-learned-save]');
    btn.disabled = true;
    try {
      const r = await patch(`/v2/assistant/learned/${encodeURIComponent(entry.id)}`, {text});
      Object.assign(entry, r.entry);
      learnedRender(d, name);
      toast('Saved');
    } catch (e) { btn.disabled = false; toast(e.message, true); }
  };
  item.onclick = ev => {
    ev.stopPropagation();
    if (ev.target.closest('[data-learned-save]')) void save();
    else if (ev.target.closest('[data-learned-cancel]')) learnedRender(d, name);
  };
  input.onkeydown = ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); void save(); }
    else if (ev.key === 'Escape') learnedRender(d, name);
  };
}

// Delete asks once more on the same button, then the entry is gone for good: not used from the next reply on.
async function learnedDelete(item, btn, d, name) {
  if (!btn.dataset.armed) {
    btn.dataset.armed = '1';
    btn.textContent = 'Delete for good?';
    btn.classList.add('danger');
    setTimeout(() => { if (btn.isConnected) { delete btn.dataset.armed; btn.textContent = 'Delete'; btn.classList.remove('danger'); } }, 4000);
    return;
  }
  btn.disabled = true;
  const id = item.dataset.learned;
  try {
    await writeRequest('DELETE', `/v2/assistant/learned/${encodeURIComponent(id)}`);
    d.personal = (d.personal || []).filter(e => e.id !== id);
    if (d.company) d.company.entries = (d.company.entries || []).filter(e => e.id !== id);
    learnedRender(d, name);
    toast('Deleted');
  } catch (e) { btn.disabled = false; toast(e.message, true); }
}
