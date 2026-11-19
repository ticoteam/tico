/* ui/app/bot-issues.ts — A bot page's Runs and Access tables and its repository line.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Moved from ui/app/issues.ts, whose other code Home runs, into the bot page part (ui/parts.json): only that part
   calls these, so a cold open of Home does not download them (P05b). */
'use strict';

function botRepoHTML(e: Employee | null | undefined) {
  const info = botRepoInfo(e);
  if (!info) return '';
  return info.url
    ? `Repository <a href="${esc(info.url)}" target="_blank" rel="noopener">${esc(info.label)}</a>`
    : `Repository <span class="mono">${esc(info.label)}</span>`;
}
function runsTable(list: RunRow[], showEmp = true) {
  if (!list.length) return `<div class="empty">No runs yet.</div>`;
  return `<div class="scroll"><table><tr>${showEmp ? '<th>Bot</th>' : ''}<th>Finished</th><th>Result</th><th>Duration</th><th>Cost</th><th>Model</th><th>Picked up</th><th></th></tr>
  ${list.map(r => `<tr>${showEmp ? `<td>${empChip(r.employee)}</td>` : ''}
    <td class="muted tnum">${ago(r.finished_at)}</td><td>${runLive(r) ? RUN_LIVE_PILL : runUnfinished(r) ? `<span class="pill fail">${r.didnt_start ? RUN_DIDNT_START : `${esc(r.outcome)} run`}</span><div class="mono muted">${esc(r.id)}</div>${r.failure_reason ? `<div class="muted">${esc(r.failure_reason)}</div>` : ''}${runsReportHTML(r)}` : r.exit === 0 ? '<span class="pill ok">Done</span>' : `<span class="pill fail">${esc(r.outcome || `exit ${r.exit}`)}</span>${runsReportHTML(r)}`}</td>
    <td class="tnum">${r.duration_s != null ? Math.round(r.duration_s/60) + 'm ' + (r.duration_s%60) + 's' : ''}</td>
    <td class="tnum">${r.cost_usd != null ? '$' + r.cost_usd.toFixed(2) : (r.output_tokens != null ? r.output_tokens.toLocaleString() + ' tokens out' : '')}</td>
    <td class="mono muted" title="${esc(ranOnNote(r.ran_on) || 'the model that actually ran it, which a fallback changes')}">${esc(ranOn(r.ran_on))}${(r.ran_on?.task_model || {}).source === 'fallback' ? ` <span class="pill waiting" title="${esc(ranOnNote(r.ran_on))}">task model off</span>` : ''}</td>
    <td>${r.fallback ? `<span class="pill waiting" title="ran on the fallback harness after the primary was unavailable">${esc(r.fallback)}</span>` : r.resumed ? 'Where it left off' : 'From the start'}</td><td><a href="${API}/runs/${encodeURIComponent(r.id || '')}/log" target="_blank" rel="noopener">Log</a></td></tr>`).join('')}</table></div>`;
}
function accessTable(e: Employee) {
  const acc: BotAccess[] = e.access || [];
  const mail = acc.filter(a => /gmail|email/.test(a.service));
  const emailLine = mail.length
    ? mail.map(a => `can ${a.can.includes('send') && e.outbound_send ? '<strong>send</strong>' : 'read and draft'} as <strong>${esc(a.identity || 'unknown address')}</strong>${a.can.includes('send') && !e.outbound_send ? ' <span class="muted">(send is declared but outbound is off)</span>' : ''}`).join('; ')
    : '<strong>none</strong>';
  const verb = (v: string) => `<span class="pill ${SEND_VERBS.has(v) ? (e.outbound_send ? 'ok' : 'waiting') : ''}" title="${SEND_VERBS.has(v) && !e.outbound_send ? 'gated: outbound_send is off' : ''}">${v}</span>`;
  const rows = acc.map(a => `<tr><td><strong>${esc(SERVICE_NAMES[a.service] || a.service)}</strong></td><td>${esc(a.identity || '—')}</td>
    <td class="tags">${(a.can || []).map(v => verb(v)).join(' ')}</td>
    <td>${a.connected ? '<span class="pill ok">connected</span>' : '<span class="pill">not connected</span>'}</td><td class="muted">${esc(a.note || '')}</td></tr>`).join('');
  return `<div class="acc-summary">
      <div><span class="k">Email</span> ${emailLine}</div>
      <div><span class="k">Outbound</span> ${e.outbound_send ? '<strong>on</strong>' : '<strong>off</strong>: drafts only'}</div>
      ${e.secrets_file ? '' : '<div><span class="muted">No credentials file.</span></div>'}
    </div>
    ${rows ? `<div class="scroll"><table><tr><th>Service</th><th>As</th><th>Can</th><th>Status</th><th></th></tr>${rows}</table></div>` : ''}`;
}
