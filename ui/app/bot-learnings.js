/* ui/app/bot-learnings.js — A bot's learnings history (docs/learnings.md): the commits that changed its memory/
   and knowledge/, as its computer reported them (GET /api/v2/bots/{bot}/memory). A small button beside the bot's
   name shows how many are new; it opens a panel on a desktop and a sheet on a phone with the updates, newest first,
   and the bot's learnings.md and decisions.md, working rules first and then newest entries first (learnNewestFirst).
   Opening it marks the updates seen.
   Dream (backend/dream.py): a button at the top of the panel starts a cleanup task for the bot over its own memory.
   While it runs the panel is in dream mode: a status line, its questions as cards to answer, then its summary and
   the commits it made. A question also shows as a small card on the bot's page, and the button carries a mark. */
'use strict';

const learnApi = slug => `/v2/bots/${encodeURIComponent(slug)}/memory`;
const dreamApi = slug => `/v2/bots/${encodeURIComponent(slug)}/dream`;
const DREAM_LINES = {starting: 'Falling asleep…', dreaming: 'Dreaming…', asking: 'A few questions', done: 'Awake', stopped: 'Awake'};
const dreamOpen = dream => !!dream && !['done', 'stopped'].includes(dream.state);

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
      const [current, dream] = await Promise.all([get(learnApi(slug)), dreamFetch(slug)]);
      if (BOT?.slug === slug && button.isConnected) learnOpen(slug, current, dream);
    } catch (e) { toast(e.message); }
    finally { button.disabled = false; }
  };
}

// The bot's dream, or null: an older server has none, and a failed read shows the panel as it was.
async function dreamFetch(slug, fresh = false) {
  try { return (await get(dreamApi(slug), {fresh}))?.dream || null; } catch { return null; }
}

// With the bot's page: the mark on the Learnings button, and its open questions as a card near the top.
async function botDreamLoad(slug) {
  const dream = await dreamFetch(slug);
  if (BOT?.slug === slug) dreamPageShow(slug, dream);
}

function dreamPageShow(slug, dream) {
  const button = $('#bot-learn');
  if (button) {
    button.classList.toggle('dreaming', dreamOpen(dream));
    button.title = dreamOpen(dream) ? 'Learnings · Dreaming' : 'Learnings';
  }
  const host = $('#bot-dream-host');
  if (!host) return;
  const q = dream?.state === 'asking' && dream.questions?.mine ? dream.questions : null;
  if (!q) { host.innerHTML = ''; host.dataset.ask = ''; return; }
  if (host.dataset.ask === q.message_id) return;              // the same question: keep what was typed
  host.dataset.ask = q.message_id;
  host.innerHTML = `<section class="dream-card-page" aria-label="Dream questions">
    <div class="dream-sky" aria-hidden="true"><i></i><i></i><i></i></div>
    <div class="dream-page-head"><span class="nav-icon" aria-hidden="true">auto_awesome</span>A few questions</div>
    ${dreamQuestionsHTML(q)}</section>`;
  dreamWire(host, q, () => { host.innerHTML = ''; host.dataset.ask = ''; });
}

// A question's text as cards: numbered questions ("1. …", "2) …") each get one, anything else is one card. A
// "Suggested: …" (or "Suggestion:", "Suggested answer:") at its end becomes a chip that fills the box.
function dreamSplit(text) {
  const lines = String(text || '').split('\n');
  const parts = [];
  let lead = [];
  for (const line of lines) {
    const m = line.match(/^\s*(?:\*\*)?(\d{1,2})[.)](?:\*\*)?\s+(.*)$/);
    if (m) parts.push({n: m[1], lines: [m[2]]});
    else if (parts.length) parts[parts.length - 1].lines.push(line);
    else lead.push(line);
  }
  const card = (n, body) => {
    const t = body.join('\n').trim();
    const s = t.match(/\(?\b(?:suggested(?: answer)?|suggestion|my suggestion)\s*:\s*([^\n]+?)\)?\s*$/i);
    return {n, text: s ? t.slice(0, s.index).trim() : t, suggested: s ? s[1].trim() : ''};
  };
  if (parts.length < 2) return [card('', lines)];
  const intro = lead.join('\n').trim();
  return parts.map((p, i) => ({...card(p.n, p.lines), intro: i === 0 ? intro : ''}));
}

let dreamForms = 0;                                       // each form's boxes get their own ids
function dreamQuestionsHTML(q) {
  const cards = dreamSplit(q.text), key = `dream-a${++dreamForms}`;
  return `<form class="dream-form" data-dream-ask="${esc(q.message_id)}">
    ${cards.map((c, i) => `<div class="dream-q" style="--i:${i}">
      ${c.intro ? `<div class="dream-intro">${md(c.intro)}</div>` : ''}
      <label class="dream-q-text" for="${key}-${i}">${c.n ? `<span class="dream-n">${esc(c.n)}</span>` : ''}<span class="md">${md(c.text)}</span></label>
      ${c.suggested ? `<button type="button" class="dream-chip" data-dream-fill="${i}" title="Use the suggested answer">${esc(c.suggested)}</button>` : ''}
      <textarea id="${key}-${i}" rows="2" data-dream-n="${esc(c.n)}" placeholder="Your answer"></textarea>
    </div>`).join('')}
    <div class="dream-actions"><button class="primary" type="submit">Send</button></div>
  </form>`;
}

// Send the answers as one reply to the question, numbered to match, as Needs you answers it: it wakes the bot.
function dreamWire(root, q, sent) {
  const form = root.querySelector('.dream-form');
  if (!form) return;
  const cards = dreamSplit(q.text);
  form.addEventListener('click', ev => {
    const chip = ev.target.closest('[data-dream-fill]');
    if (!chip) return;
    const box = form.querySelectorAll('textarea')[Number(chip.dataset.dreamFill)];
    box.value = cards[Number(chip.dataset.dreamFill)].suggested;
    box.focus();
  });
  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    const boxes = [...form.querySelectorAll('textarea')];
    if (!boxes.some(b => b.value.trim())) { boxes[0].focus(); return; }
    const text = boxes.length === 1 ? boxes[0].value.trim()
      : boxes.map(b => `${b.dataset.dreamN}. ${b.value.trim() || 'No answer'}`).join('\n');
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      await post(`/v2/messages/${encodeURIComponent(q.message_id)}/answer`, {text});
      sent();
    } catch (e) { button.disabled = false; toast(e.message); }
  });
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

function learnOpen(slug, data, dream = null) {
  $('#learn-panel')?.remove();
  const d = document.createElement('dialog');
  d.id = 'learn-panel'; d.className = 'learn-panel';
  d.setAttribute('aria-labelledby', 'learn-title');
  let poll = 0;
  d.addEventListener('click', ev => { if (ev.target === d) d.close(); });
  d.addEventListener('close', () => { clearInterval(poll); d.remove(); }, {once: true});
  let docs = data.documents || {};
  let statuses = data.document_status || {};
  const viewsOf = () => [['updates', 'Updates'], ['memory/learnings.md', 'Learnings'], ['memory/decisions.md', 'Decisions']]
    .filter(([k]) => k === 'updates' || String(docs[k] || '').trim() || statuses[k]?.oversized);
  const switchHTML = views => views.length > 1 ? `<div class="learn-switch" role="tablist">${views.map(([k, label], i) =>
    `<button type="button" role="tab" data-learn-view="${esc(k)}" aria-selected="${i === 0}">${label}</button>`).join('')}</div>` : '';
  d.innerHTML = `<div class="dream-sky" aria-hidden="true"><i></i><i></i><i></i></div>
    <div class="learn-head"><h2 id="learn-title">Learnings</h2>
      <span class="learn-tabs">${switchHTML(viewsOf())}</span>
      <button class="ghost learn-dream" type="button" data-dream-start><span class="nav-icon" aria-hidden="true">auto_awesome</span>Dream</button>
      <button class="ghost tmodal-x" type="button" data-learn-close aria-label="Close">✕</button></div>
    <div class="learn-body" id="learn-body"></div>`;
  document.body.appendChild(d);
  let updates = data.updates || [], next = data.next_before;
  const body = $('#learn-body', d);
  // Dream mode: the status line, then the questions, or the summary and this dream's commits once it is over.
  let shown = null;                                       // the dream as last drawn
  const dreamDraw = async latest => {
    const before = shown;
    shown = latest;
    dreamPageShow(slug, latest);
    if (!latest) { dreamLeave(); return; }
    const ask = latest.state === 'asking' && latest.questions ? latest.questions : null;
    if (before && before.state === latest.state && before.questions?.message_id === ask?.message_id) return;
    const line = DREAM_LINES[latest.state] || 'Dreaming…';
    let content = '';
    if (ask) content = ask.mine ? dreamQuestionsHTML(ask) : `<div class="dream-quiet md">${md(ask.text)}</div>`;
    else if (!dreamOpen(latest)) {
      let mine = [];
      try {
        const page = await get(`${learnApi(slug)}?include_documents=false`, {fresh: true});
        mine = (page.updates || []).filter(u => u.source?.task === latest.task);
      } catch {}
      if (shown !== latest) return;
      content = `${latest.summary ? `<div class="dream-summary md">${md(latest.summary)}</div>` : ''}
        ${mine.length ? `<ol class="learn-list dream-commits">${mine.map(learnRow).join('')}</ol>` : ''}
        <div class="dream-actions"><button class="primary" type="button" data-dream-wake>Wake up</button></div>`;
    }
    body.innerHTML = `<div class="dream-view">
      <p class="dream-line" role="status">${esc(line)}</p>
      <div class="dream-content">${content}</div>
      ${dreamOpen(latest) ? `<div class="dream-foot"><a href="#/task/${encodeURIComponent(latest.task)}">Task</a>
        <button class="ghost" type="button" data-dream-stop>Stop</button></div>` : ''}
    </div>`;
    if (ask?.mine) dreamWire(body, ask, () => { void dreamRefresh(); });
  };
  const dreamRefresh = async () => {
    const latest = await dreamFetch(slug, true);
    if (d.open && d.classList.contains('dreaming') && latest !== null) await dreamDraw(latest);
  };
  const dreamEnter = latest => {
    d.classList.add('dreaming');
    clearInterval(poll);
    poll = setInterval(() => { void dreamRefresh(); }, 4000);
    void dreamDraw(latest);
  };
  function dreamLeave() {
    clearInterval(poll);
    d.classList.remove('dreaming');
    shown = null;
    show('updates');
  }
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
    else if (t.matches('[data-dream-start]')) {
      t.disabled = true;
      try {
        const started = (await post(dreamApi(slug), {})).dream;
        // Closed while the start was in flight: the close already cleared polling, so only the page shows it.
        if (d.open && d.isConnected) dreamEnter(started);
        else dreamPageShow(slug, started);
      }
      catch (e) { toast(e.message); }
      finally { t.disabled = false; }
    } else if (t.matches('[data-dream-stop]')) {
      t.disabled = true;
      try {
        const task = (await get(`/v2/tasks/${encodeURIComponent(shown.task)}`, {fresh: true})).task;
        await post(`/v2/tasks/${encodeURIComponent(shown.task)}`, {version: task.version, close: true});
        await dreamRefresh();
      } catch (e) { t.disabled = false; toast(e.message); }
    } else if (t.matches('[data-dream-wake]')) {
      t.disabled = true;
      try {
        const fresh = await get(learnApi(slug), {fresh: true});
        docs = fresh.documents || {}; statuses = fresh.document_status || {};
        updates = (fresh.updates || []).map(u => ({...u, new: false})); next = fresh.next_before;
        $('.learn-tabs', d).innerHTML = switchHTML(viewsOf());
      } catch (e) { toast(e.message); }
      dreamLeave();
      dreamPageShow(slug, null);
    }
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
  if (dreamOpen(dream)) dreamEnter(dream);
  updates.forEach(u => { u.new = false; });                 // marked once; reopening shows them as read
  if (data.unseen) {
    learnBadge($('#bot-learn'), 0);
    data.unseen = 0;
    void post(`${learnApi(slug)}/seen`, {}).catch(() => {});
  }
}
