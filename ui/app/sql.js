/* ui/app/sql.js — SQL page (owner and admins)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- SQL (owner and admins)
// Read-only queries over the hub database: POST /api/v2/sql, one SELECT at a time. What a
// query may see is decided in the database layer (backend/sql.py: per-request views and
// SQLite's authorizer), never here; this page only sends the text and draws the rows.
const SQL_EXAMPLES = [
  ['Queued work', "SELECT bot_id, count(*) AS queued, min(created_at) AS oldest FROM jobs WHERE state='queued' GROUP BY bot_id ORDER BY queued DESC"],
  ['Open tasks by owner', "SELECT owner_id, status, count(*) AS n FROM tasks WHERE status IN ('open','doing','waiting') GROUP BY owner_id, status ORDER BY owner_id, status"],
  ['Last run per bot', "SELECT bot, max(started_at) AS last_turn, sum(exit='ok') AS ok, count(*) AS turns FROM turns WHERE started_at > strftime('%Y-%m-%dT%H:%M:%S','now','-7 days') GROUP BY bot ORDER BY last_turn DESC"],
  ['Who asked what today', "SELECT created_at, sender_id, recipient_id, substr(body,1,90) AS ask FROM messages WHERE kind='ask' AND created_at >= strftime('%Y-%m-%d','now') ORDER BY created_at DESC"],
  ['Routine outcomes', "SELECT s.bot, s.title, o.occurrence, o.outcome, t.status FROM schedule_occurrences o JOIN schedules s ON s.id=o.schedule_id LEFT JOIN tasks t ON t.id=o.task_id ORDER BY o.occurrence DESC LIMIT 50"],
  ['Tables', "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"],
];
function pageSql() {
  if (S.me && !S.me.can_see_sql) { location.hash = TASKS; return; }
  let last = ''; try { last = localStorage.getItem('hub.sql.last') || ''; } catch {}
  $('#main').innerHTML = `<div class="meeting-head"><div><h1>Query the database</h1></div></div>
    <section class="card">
      <textarea id="sql-text" class="sql-editor" rows="6" spellcheck="false" aria-label="SQL query" placeholder="SELECT bot, state, focus FROM bot_status">${esc(last)}</textarea>
      <div class="row sql-bar"><button class="primary" type="button" id="sql-run">Run</button><span class="muted">⌘↩ / Ctrl+Enter</span><span class="mono muted" id="sql-meta" role="status"></span></div>
      <div class="err" id="sql-error" hidden></div>
      <div class="sql-results" id="sql-results"></div>
      <details class="sql-examples"><summary>Examples</summary><p>Read-only, one statement. <a href="${GH}/blob/main/docs/hub-sql.md" target="_blank" rel="noopener">Tables and examples</a></p><ul>${SQL_EXAMPLES.map(([title, sql], i) =>
        `<li><a href="#" data-sql-example="${i}">${esc(title)}</a><code>${esc(sql)}</code></li>`).join('')}</ul></details>
    </section>`;
  $('#sql-run').onclick = () => sqlRun();
  $('#sql-text').addEventListener('keydown', ev => { if ((ev.metaKey || ev.ctrlKey) && ev.key === 'Enter') { ev.preventDefault(); sqlRun(); } });
  $('.sql-examples').onclick = ev => {
    const a = ev.target.closest('[data-sql-example]'); if (!a) return;
    ev.preventDefault(); $('#sql-text').value = SQL_EXAMPLES[+a.dataset.sqlExample][1]; sqlRun();
  };
}
async function sqlRun() {
  const box = $('#sql-text'), sql = box?.value.trim();
  if (!sql) return;
  try { localStorage.setItem('hub.sql.last', sql); } catch {}
  const btn = $('#sql-run'), meta = $('#sql-meta'), err = $('#sql-error');
  btn.disabled = true; err.hidden = true; meta.textContent = 'Running…';
  try {
    const r = await post('/v2/sql', {sql});
    if (S.route !== SQL_PAGE) return;
    const cell = v => v === null ? '<span class="muted">null</span>' : esc(typeof v === 'object' ? JSON.stringify(v) : v);
    $('#sql-results').innerHTML = r.columns.length ? `<table><thead><tr>${r.columns.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead>
      <tbody>${r.rows.map(row => `<tr>${row.map(v => `<td title="${esc(v ?? '')}">${cell(v)}</td>`).join('')}</tr>`).join('')}</tbody></table>` : '';
    meta.textContent = `${r.row_count} row${r.row_count === 1 ? '' : 's'}${r.truncated ? ' shown, more available' : ''} · ${r.ms} ms${r.note ? ' · ' + r.note : ''}`;
  } catch (e) {
    if (S.route !== SQL_PAGE) return;
    err.textContent = e.message; err.hidden = false; meta.textContent = '';
  } finally { btn.disabled = false; }
}
