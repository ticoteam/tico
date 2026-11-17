/* ui/app/finance-shared.ts — What the Finance tabs share: parsing cents, the right rail, the head's actions and View as, CSV, dialogs and bars.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Moved from ui/app/finance.ts, whose other code Home runs, into the finance page part (ui/parts.json): only that part
   calls these, so a cold open of Home does not download them (P05b). */
'use strict';

// A typed dollar amount to cents: "1,200", "$45.50"; null when it is not one.
function finParseCents(text: string | null | undefined) {
  const t = String(text ?? '').trim().replace(/^\$\s*/, '').replace(/,/g, '');
  if (!/^\d+(\.\d{1,2})?$/.test(t)) return null;
  return Math.round(Number(t) * 100);
}
// The teams directly under `id` ('' for the top, which takes in teams whose parent isn't shown), in the org chart's
// order (S.orgGroups), then by name.
function finTeamKids<T extends {id: string; name: string; parent: string | null}>(teams: T[], id: string) {
  const rank = (t: T) => (S.orgGroups || []).find(g => g.id === t.id)?.order ?? 1e9;
  return teams.filter(t => (t.parent || '') === (id || '') || (!id && t.parent && !teams.some(x => x.id === t.parent)))
    .sort((a, b) => rank(a) - rank(b) || String(a.name).localeCompare(String(b.name)));
}
// A built-in helper (the Assistant, BotOps, Strategy Owner, Librarian, General Manager, Finance Owner…; ui/app/sidebar.js
// isHelperBot): listed together as Helpers, after the org chart.
function finHelper(actor: string) {
  if (!String(actor || '').startsWith('bot:')) return false;
  const id = actor.slice(4), e = (S.emps || []).find(x => x.id === id);
  return e ? isHelperBot(e) : isBuiltInBot(id);
}
function finRail(html: string) { const rail = $('#fin-rail'); if (rail) rail.innerHTML = html; }
function finTrim(table: HTMLTableElement | null | undefined, key: string) {
  const head = table?.tHead?.rows[0];
  if (!table || !head) return;
  const n = [...head.cells].reduce((t, c) => t + c.colSpan, 0);
  const seen = FIN_COLS_SEEN.get(key) || new Set<number>();
  FIN_COLS_SEEN.set(key, seen);
  const rows = [...table.rows];
  // Each row's cells by the column they start at, for rows that have one cell per column.
  const plain = rows.filter(r => r.cells.length === n && r !== head && !r.closest('thead'));
  for (let i = 0; i < n; i++) if (plain.some(r => finCellHas(r.cells[i]))) seen.add(i);
  head.querySelectorAll('[data-fin-keep]').forEach(th => { seen.add((th as HTMLTableCellElement).cellIndex); });
  for (let i = n - 1; i >= 0; i--) {
    if (seen.has(i)) continue;
    for (const r of rows) {
      let at = 0;
      for (const c of [...r.cells]) {
        if (i >= at && i < at + c.colSpan) { if (c.colSpan > 1) c.colSpan--; else c.remove(); break; }
        at += c.colSpan;
      }
    }
  }
}

// An action with a fresh Idempotency-Key, or `operationId` when the caller keeps one so a retry after a lost answer is the
// same action: committed, or sent to someone for approval; anything else is an error.
async function finAct(kind: string, payload: object, operationId?: string) {
  const r: FinActResult | null = await post('/v2/finance/actions', {kind, payload}, operationId);
  if (r?.status === 'committed') return r;
  if (r?.status === 'needs_approval') { toast(r.approver ? `Sent to ${finWho(r.approver)} for approval` : 'Sent for approval'); return r; }
  throw new Error(r?.reason || 'Not done');
}

// What is on screen as a CSV file.
function finCsv(rows: unknown[][], name: string) {
  const cell = (v: unknown) => { const s = String(v ?? ''); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const text = rows.map(r => r.map(cell).join(',')).join('\n') + '\n';
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([text], {type: 'text/csv'}));
  link.download = name;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

// A small dialog: a title, a form, Cancel and the primary button. `onSave(form)` throws to show an error, and returns
// false to keep the dialog open (a decision that now names its approver).
function finDialog<F extends HTMLFormElement = HTMLFormElement>(title: string, body: string, saveLabel: string, onSave: (form: F) => unknown) {
  for (const d of document.querySelectorAll<HTMLDialogElement>('dialog.fin-dlg')) d.close();
  const dlg = document.createElement('dialog');
  dlg.className = 'tmodal fin-dlg';
  dlg.setAttribute('aria-label', title);
  dlg.innerHTML = `<div class="tmodal-head"><b>${esc(title)}</b><span class="spacer"></span>
      <button type="button" class="ghost tmodal-x" data-close aria-label="Close">✕</button></div>
    <form class="tmodal-body fin-form" novalidate>${body}<p class="err" data-error hidden></p>
      <div class="fin-foot"><button type="button" class="ghost" data-close>Cancel</button><button type="submit" class="primary">${esc(saveLabel)}</button></div></form>`;
  document.body.appendChild(dlg);
  dlg.querySelectorAll<HTMLButtonElement>('[data-close]').forEach(b => { b.onclick = () => dlg.close(); });
  dlg.onclose = () => dlg.remove();
  const form = dlg.querySelector<F>('form')!, err = dlg.querySelector<HTMLElement>('[data-error]')!, save = dlg.querySelector<HTMLButtonElement>('[type=submit]')!;
  // While a save runs the button says so (a Mercury connect tests the token and maps accounts, which takes seconds).
  // A save that relabels the button (Preview → Import, Send, Done) keeps its label: only the busy text is put back.
  let idle = '';
  const busy = (on: boolean) => {
    save.disabled = on; save.setAttribute('aria-busy', on ? 'true' : 'false');
    if (on) { idle = save.textContent; save.textContent = `${idle}…`; }
    else if (save.textContent === `${idle}…`) save.textContent = idle;
  };
  form.onsubmit = async ev => {
    ev.preventDefault(); err.hidden = true; busy(true);
    try { if (await onSave(form) === false) busy(false); else dlg.close(); }
    catch (e) { err.textContent = errorMessage(e, ''); err.hidden = false; busy(false); }
  };
  dlg.showModal();
  form.querySelector<HTMLElement>('input:not([type=hidden]),select')?.focus();
  return dlg;
}
// The open tab's quiet + (or its add menu) in the header's filled-button place; '' for none.
function finHeadAct(html: string) { const head = $('#fin-head'); if (head) pageHeadSet(head, 'primary', html); }
// The open tab's rarer actions (Export, Expand all…) as ⋯ items: plain buttons, before Copy link.
function finHeadMore(html: string) { const head = $('#fin-head'); if (head) pageHeadSet(head, 'more', html + PAGE_HEAD_COPY); }
// The owner views Finance as a teammate sees it: every tab reads as them, and nothing can be changed meanwhile.
function finViewAs(who: string | null | undefined) {
  FIN_VIEW_AS = who || '';
  location.hash = finHref('/pnl');
}

// The rail's bar list: [{label, amount}] scaled to the largest.
function finBars(rows: {label: string; amount?: number | null}[]) {
  const top = Math.max(1, ...rows.map(r => Math.abs(r.amount || 0)));
  return `<div class="fin-bars">${rows.map(r => `<span class="fin-bars-k">${esc(r.label)}</span><span class="fin-bars-b" aria-hidden="true"><i style="width:${Math.round(Math.abs(r.amount || 0) / top * 100)}%"></i></span><span class="fin-bars-v">${finNum(r.amount)}</span>`).join('')}</div>`;
}
