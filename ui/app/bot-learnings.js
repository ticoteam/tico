/* ui/app/bot-learnings.js — A bot's learnings history (docs/learnings.md): the commits that changed its memory/
   and knowledge/, as its computer reported them (GET /api/v2/bots/{bot}/memory). A small button beside the bot's
   name shows how many are new; it opens a panel on a desktop and a sheet on a phone with the updates, newest first,
   and the bot's learnings.md and decisions.md, working rules first and then newest entries first (learnNewestFirst).
   Opening it marks the updates seen. */
'use strict';

const learnApi = slug => `/v2/bots/${encodeURIComponent(slug)}/memory`;

async function botLearningsLoad(slug) {
  const button = $('#bot-learn');
  if (!button) return;
  let data;
  try { data = await get(`${learnApi(slug)}?include_documents=false`); } catch { return; }          // an older server has no history: no button
  if (BOT?.slug !== slug || !button.isConnected) return;
  const docs = data.documents || {};
  if (!(data.updates || []).length && !Object.values(docs).some(t => String(t).trim()) &&
      !Object.values(data.document_status || {}).some(s => s.oversized || s.updated_at)) return;
  learnBadge(button, data.unseen);
  button.hidden = false;
  button.onclick = async () => {
    button.disabled = true;
    try {
      const current = await get(learnApi(slug));
      if (BOT?.slug === slug && button.isConnected) learnOpen(slug, current);
    } catch (e) { toast(e.message); }
    finally { button.disabled = false; }
  };
}

function learnBadge(button, unseen) {
  const n = button.querySelector('.bl-n');
  n.textContent = unseen > 9 ? '9+' : String(unseen || '');
  n.hidden = !unseen;
  button.setAttribute('aria-label', unseen ? `Learnings, ${unseen} new` : 'Learnings');
}

// A diff as lines: added and removed ones tinted with their marker dropped, each hunk header a small gap.
function learnDiff(text) {
  return text.split('\n').filter(l => !/^(diff --git|index |--- |\+\+\+ |\\ No newline)/.test(l)).map(l => {
    if (l.startsWith('@@')) return '<span class="hunk">⋯</span>';
    const kind = l[0] === '+' ? 'add' : l[0] === '-' ? 'del' : '';
    return `<span class="${kind}">${esc(l.slice(1)) || ' '}</span>`;
  }).join('').replace(/^<span class="hunk">⋯<\/span>/, '');
}

// A memory file as the panel shows it: its title and intro, then any working-rules section, then the other `## `
// sections newest first by the date in their heading, and inside each the top-level `- ` entries newest first by
// their `added:` or leading date (`2026-10-14`, or `Oct 14` in the year of the file's latest dated line). Bots append
// at the bottom, so file order opens on the oldest entry. An undated
// section or entry stays with the dated one above it; undated ones above all dated ones stay first; nothing is lost.
function learnNewestFirst(text) {
  const lines = String(text || '').split('\n');
  const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
  const year = (String(text || '').match(/\b20\d\d(?=-\d\d-\d\d\b)/g) || [String(new Date().getFullYear())]).sort().pop();
  const when = t => {                                             // the first date in t, as YYYY-MM-DD
    const iso = t.match(/\b(20\d\d-\d\d-\d\d)\b/);
    if (iso) return iso[1];
    const md = t.match(/\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (\d{1,2})\b/i);
    return md ? `${year}-${String(MONTHS.indexOf(md[1].toLowerCase()) + 1).padStart(2, '0')}-${md[2].padStart(2, '0')}` : '';
  };
  const blocks = (rows, starts) => {                              // [lead, ...items]: each item begins where starts() is true
    const out = [[]];
    let fence = false;
    for (const l of rows) {
      if (/^\s*(```|~~~)/.test(l)) fence = !fence;
      if (!fence && starts(l) && !/^\s*(```|~~~)/.test(l)) out.push([]);
      out[out.length - 1].push(l);
    }
    return out;
  };
  const newest = (items, dateOf) => {                             // groups: a dated item and the undated ones after it
    const groups = [];
    for (const item of items) {
      const d = dateOf(item);
      if (d || !groups.length) groups.push({d, items: [item]});
      else groups[groups.length - 1].items.push(item);
    }
    const lead = groups.length && !groups[0].d ? groups.shift().items : [];
    groups.sort((a, b) => b.d.localeCompare(a.d));                // stable: same-day groups keep file order
    return lead.concat(...groups.map(g => g.items));
  };
  const dateOf = item => (item.join('\n').match(/added:\s*(20\d\d-\d\d-\d\d)/) || [])[1] || when(item[0].match(/^[-*]\s+\W{0,3}(.{0,12})/)[1]);
  const split = rows => {                                         // [prose ahead of the first entry, entries newest first]
    const [head, ...items] = blocks(rows, l => /^[-*] /.test(l));
    return [head, [].concat(...newest(items, dateOf))];
  };
  const entries = rows => [].concat(...split(rows));
  // A flat log (the template's `# Learnings`, a line of intro, then entries) has no `## ` at all: its entries are
  // the intro's, sorted too, and they follow the rules.
  const [intro, ...sections] = blocks(lines, l => /^## /.test(l));
  const [title, flat] = split(intro);
  const rules = sections.filter(s => !when(s[0]) && /\brules?\b/i.test(s[0]));
  const log = newest(sections.filter(s => !rules.includes(s)), s => when(s[0]));
  return title.concat(...rules, flat, ...log.map(entries)).join('\n');
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
    <div class="lr-meta">${esc(u.author)} · <time datetime="${esc(u.when)}" title="${esc(new Date(u.when).toLocaleString())}">${esc(ago(u.when))}</time> ${u.url ? ` · <a href="${esc(u.url)}" target="_blank" rel="noopener">GitHub</a>` : ''}${u.shared ? '' : ' · <span class="lr-local">Not pushed</span>'}</div>
    ${u.source ? `<div class="lr-source">From ${learnSource(u)}</div>` : ''}
    ${u.diff ? `<details class="lr-diff"><summary>${esc(files)}</summary><pre>${learnDiff(u.diff)}${u.truncated ? '<span class="hunk">…</span>' : ''}</pre></details>`
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
  const statuses = data.document_status || {};
  const views = [['updates', 'Updates'], ['memory/learnings.md', 'Learnings'], ['memory/decisions.md', 'Decisions']]
    .filter(([k]) => k === 'updates' || String(docs[k] || '').trim() || statuses[k]?.oversized);
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
    if (view !== 'updates') {
      const status = statuses[view];
      const banner = status?.oversized ? `<div class="learn-warning" role="status">${status.updated_at
        ? `File too large, showing the copy from ${esc(new Date(status.updated_at).toLocaleString())}.`
        : 'File too large. No saved copy is available.'}</div>` : '';
      body.innerHTML = `${banner}<div class="md">${md(learnNewestFirst(docs[view]))}</div>`;
      return;
    }
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
        const page = await get(`${learnApi(slug)}?before=${encodeURIComponent(next)}&include_documents=false`);
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
