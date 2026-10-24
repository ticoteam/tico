/* ui/app/subscriptions.js — the line under a bot's model saying which login it runs on. Each computer has one login
   per AI tool, used by all its bots: a bot has no sign-in of its own, and a different account means another computer
   (backend/subscriptions.py).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A computer as the server names it: {runner_id, label}, or a bare runner id.
function subsComputerLabel(c) {
  if (c && typeof c === 'object') return c.label || subsComputerLabel(c.runner_id);
  return (SETTINGS_DATA?.machines || []).find(m => m.id === c)?.label || c || '';
}
// A saved form is no longer a draft: its fields take their saved values as their own, so the pop-up redraws it.
function subsSettle(form) {
  form.querySelectorAll('input, textarea').forEach(field => { field.defaultValue = field.value; });
  if (form.contains(document.activeElement)) document.activeElement.blur();
}

// ---- the bot editor: the line under Model
const BOT_SUB_LINE = new Map();          // slug -> GET /v2/bots/{bot}/subscription, so a redraw paints at once
const subsToolName = line => harnessRow(line?.harness)?.name || harnessRow(line?.runtime)?.name || harnessWords(line?.runtime) || 'its model';
// {source: 'computer', computer: {runner_id, label} | null, runtime, harness, signed_in, problem}; `here` leaves the
// computer out, for the computer's own pop-up.
function subsBotLineHTML(line, here = false) {
  if (!line) return '';
  const where = here ? '' : line.computer ? ` on ${subsComputerLabel(line.computer)}` : ' · no computer yet';
  // The server's own words when it has them (a runner too old to say).
  const warn = line.problem || (line.signed_in === false ? 'Signed out' : '');
  return `<span class="sb-sub-text">Runs on ${esc(subsToolName(line))} · Computer's login${esc(where)}</span>${warn ? ` <span class="pill fail sb-sub-warn">${esc(warn)}</span>` : ''}`;
}
function subsBotPaint(rows, slug) {
  const lineEl = rows.querySelector('[data-bot-sub-line]');
  const line = BOT_SUB_LINE.get(slug);
  if (lineEl) { lineEl.innerHTML = subsBotLineHTML(line); lineEl.hidden = !line; }
}
async function subsBotMount(rows, slug) {
  if (!rows || !S.me?.cloud) return;
  subsBotPaint(rows, slug);
  const line = await get(`/v2/bots/${encodeURIComponent(slug)}/subscription`).catch(() => null);
  if (line) BOT_SUB_LINE.set(slug, line); else BOT_SUB_LINE.delete(slug);
  if (rows.isConnected) subsBotPaint(rows, slug);
}
