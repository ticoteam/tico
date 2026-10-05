/* ui/app/bot-learnings.js — A bot's learnings history (docs/learnings.md): the commits that changed its memory/
   and knowledge/, as its computer reported them (GET /api/v2/bots/{bot}/memory). A small button beside the bot's
   name shows how many are new; it opens a panel on a desktop and a sheet on a phone with the updates, newest first,
   and the bot's learnings.md and decisions.md. Opening it marks the updates seen. */
'use strict';

const learnApi = slug => `/v2/bots/${encodeURIComponent(slug)}/memory`;

async function botLearningsLoad(slug) {
  const button = $('#bot-learn');
  if (!button) return;
  let data;
  try { data = await get(learnApi(slug)); } catch { return; }          // an older server has no history: no button
  if (BOT?.slug !== slug || !button.isConnected) return;
  const docs = data.documents || {};
  if (!(data.updates || []).length && !Object.values(docs).some(t => String(t).trim())) return;
  learnBadge(button, data.unseen);
  button.hidden = false;
  button.onclick = () => learnOpen(slug, data);
}

function learnBadge(button, unseen) {
  const n = button.querySelector('.bl-n');
  n.textContent = unseen > 9 ? '9+' : String(unseen || '');
  n.hidden = !unseen;
  button.setAttribute('aria-label', unseen ? `Learnings, ${unseen} new` : 'Learnings');
}

function learnDiff(text) {
  return text.split('\n').filter(l => !/^(diff --git|index |--- |\+\+\+ )/.test(l)).map(l => {
    const kind = l.startsWith('@@') ? 'hunk' : l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : '';
    return `<span class="${kind}">${esc(l) || ' '}</span>`;
  }).join('\n');
}

function learnSource(u) {
  const s = u.source;
  if (!s) return '';
  if (s.task) return `<a href="#/task/${encodeURIComponent(s.task)}">${esc(s.title || 'Task')}</a>`;
  return `<a href="#/bot/${encodeURIComponent(s.bot)}" data-learn-jump="${esc(s.message)}" data-learn-bot="${esc(s.bot)}">${esc(s.excerpt || 'Chat')}</a>`;
}

function learnRow(u) {
  const files = u.files.map(f => f.replace(/^(memory|knowledge)\//, '')).join(', ');
  return `<li class="learn-row${u.new ? ' new' : ''}">
    <div class="lr-subject">${esc(u.subject)}</div>
    <div class="lr-meta">${esc(u.author)} · <time datetime="${esc(u.when)}" title="${esc(new Date(u.when).toLocaleString())}">${esc(ago(u.when))}</time> · <code>${esc(u.sha.slice(0, 7))}</code>${u.shared ? '' : ' · <span class="lr-local">Not pushed</span>'}</div>
    ${u.source ? `<div class="lr-source">From ${learnSource(u)}</div>` : ''}
    ${u.diff ? `<details class="lr-diff"><summary>${esc(files)}</summary><pre>${learnDiff(u.diff)}${u.truncated ? '\n<span class="hunk">…</span>' : ''}</pre></details>`
      : `<div class="lr-files">${esc(files)}</div>`}
  </li>`;
}

function learnOpen(slug, data) {
  $('#learn-panel')?.remove();
  const d = document.createElement('dialog');
  d.id = 'learn-panel'; d.className = 'learn-panel';
  d.setAttribute('aria-labelledby', 'learn-title');
  d.addEventListener('click', ev => { if (ev.target === d) d.close(); });
  d.addEventListener('close', () => d.remove(), {once: true});
  const docs = data.documents || {};
  const views = [['updates', 'Updates'], ['memory/learnings.md', 'Learnings'], ['memory/decisions.md', 'Decisions']]
    .filter(([k]) => k === 'updates' || String(docs[k] || '').trim());
  d.innerHTML = `<div class="learn-head"><h2 id="learn-title">Learnings</h2>
      ${views.length > 1 ? `<div class="learn-switch" role="tablist">${views.map(([k, label], i) =>
        `<button type="button" role="tab" data-learn-view="${esc(k)}" aria-selected="${i === 0}">${label}</button>`).join('')}</div>` : ''}
      <button class="ghost tmodal-x" type="button" data-learn-close aria-label="Close">✕</button></div>
    <div class="learn-body" id="learn-body"></div>`;
  document.body.appendChild(d);
  let updates = data.updates || [], next = data.next_before;
  const body = $('#learn-body', d);
  const show = view => {
    d.querySelectorAll('[data-learn-view]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.learnView === view)));
    if (view !== 'updates') { body.innerHTML = `<div class="md">${md(docs[view])}</div>`; return; }
    body.innerHTML = updates.length
      ? `<ol class="learn-list">${updates.map(learnRow).join('')}</ol>${next ? '<button class="ghost learn-more" type="button" data-learn-more>More</button>' : ''}`
      : '<div class="empty">No memory updates yet.</div>';
  };
  d.addEventListener('click', async ev => {
    const t = ev.target.closest('button, a');
    if (!t) return;
    if (t.matches('[data-learn-close]')) d.close();
    else if (t.dataset.learnView) show(t.dataset.learnView);
    else if (t.matches('[data-learn-more]')) {
      t.disabled = true;
      try {
        const page = await get(`${learnApi(slug)}?before=${encodeURIComponent(next)}`);
        updates = updates.concat(page.updates || []); next = page.next_before;
        show('updates');
      } catch (e) { t.disabled = false; toast(e.message); }
    } else if (t.dataset.learnJump && typeof jumpToMessage === 'function') {
      ev.preventDefault(); d.close();
      void jumpToMessage(t.dataset.learnBot, t.dataset.learnJump);
    } else if (t.tagName === 'A') d.close();
  });
  show('updates');
  d.showModal();
  updates.forEach(u => { u.new = false; });                 // marked once; reopening shows them as read
  if (data.unseen) {
    learnBadge($('#bot-learn'), 0);
    data.unseen = 0;
    void post(`${learnApi(slug)}/seen`, {}).catch(() => {});
  }
}
