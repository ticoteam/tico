/* ui/app/finance-statement-upload.ts — Finance › Sources: "Upload statement" for a bank or card account Tico cannot connect.
   A CSV, OFX or QFX file is previewed first: the tie-out (opening, in, out, ends at, what the statement says), then each
   row New or Already in Tico, with its vendor and the rule that would attribute it. Post puts the New rows in (statement_
   post, finance.book) with the preview's token, so it posts what was shown. A gap in the tie-out is a warning: the post
   still goes through, and the account shows "Off by" until a reconciliation clears it. The server decides which way the
   file's amounts read (statement_files.py); the "shows positive" box turns that over and previews again, and the post
   sends the same choice. Only the newest preview counts: a new file or sign choice drops the shown one at once, and a
   reply to an older request is dropped. Post sends that preview's snapshot (file, signs, token) and nothing else.
   Reads and writes: POST /api/v2/finance/statements/preview; statement_post through POST /api/v2/finance/actions.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The most a statement file may be (backend/finance/core/statement_files.py MAX_BYTES).
const FIN_STMT_MAX_BYTES = 1024 * 1024;

// The money account a statement is for, as the Sources page has it.
interface FinStmtMoney { id: string; name: string; last4: string | null; kind: string }
interface FinStmtTie { opening: number; added: number; removed: number; ends: number; statement_says: number | null; off_by: number }
interface FinStmtRow {
  id: string; date: string; description: string; amount: number; vendor: {id: string; name: string};
  attribution: {account: string; rule: string} | null; status: 'new' | 'duplicate';
}
// A preview (backend/finance/statement_upload.py preview): what a post would do, and the token that says it posts it.
interface FinStmtReview {
  token: string; expires_at: string; money_account_id: string; file_name: string; file_format: string; start: string;
  end: string; card: boolean; last4: string | null; signs: {flipped: boolean; by: string}; new: number; duplicate: number;
  tie: FinStmtTie; rows: FinStmtRow[];
}
// How the file's amounts read: the server decides (auto), or the person turned it over.
type FinStmtSigns = 'auto' | 'as_shown' | 'flipped';
// The file being reviewed: its bytes as base64 (the server keeps none), the signs it was read with, its preview, and the
// key its post goes with (kept while a post's answer is lost, so Post again is the same action, never a second one).
interface FinStmtState { file: File; data: string; signs: FinStmtSigns; review: FinStmtReview; op: string }
// Where the dialog is: no preview yet, a preview on its way, a preview Post can send, a post on its way, or a failed
// preview or post (Preview starts again).
type FinStmtPhase = 'empty' | 'previewing' | 'reviewed' | 'posting' | 'error';

// Money in cents, signed, two decimals: "−$59.99", "$4,212.80".
const finStmtUsd = (c: number): string =>
  (c < 0 ? '−$' : '$') + (Math.abs(c) / 100).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
// The file's bytes as base64, which is what the server reads. Built in slices, so a large file does not overflow the stack.
async function finStmtBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let text = '';
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...Array.from(bytes.subarray(i, i + 0x8000)));
  return btoa(text);
}
// "October 2026" for a statement that ends in October; "Oct 02" for a row's day.
const finStmtMonth = (day: string): string => new Date(day + 'T12:00:00Z').toLocaleDateString('en-US', {month: 'long', year: 'numeric', timeZone: 'UTC'});
const finStmtDay = (day: string): string => new Date(day + 'T12:00:00Z').toLocaleDateString('en-US', {month: 'short', day: '2-digit', timeZone: 'UTC'});

// One row of the review. A row already in Tico has no attribution (it posts nothing).
function finStmtRowHtml(x: FinStmtRow): string {
  const isNew = x.status === 'new';
  const attributed = !isNew ? '<span class="muted">—</span>'
    : x.attribution ? `${esc(x.attribution.account)} <span class="muted">(rule)</span>` : '<span class="muted">Unattributed</span>';
  const pill = isNew ? '<span class="pill ok">New</span>' : '<span class="pill">Already in Tico</span>';
  return `<tr><td class="fin-nowrap">${esc(finStmtDay(x.date))}</td><td>${esc(x.description)}</td>
    <td class="fin-nowrap">${esc(finStmtUsd(x.amount))}</td><td>${x.vendor?.name ? esc(x.vendor.name) : '<span class="muted">—</span>'}</td>
    <td>${attributed}</td><td>${pill}</td></tr>`;
}

// The review: a header line (account, month, file), the tie-out with its warning when the rows miss the statement, then
// the table of rows. A card reads its tie-out in charges and payments, a bank in money in and out.
function finStmtReviewHtml(money: FinStmtMoney, r: FinStmtReview): string {
  const t = r.tie, [inWord, outWord] = r.card ? ['Charges', 'Payments'] : ['In', 'Out'];
  const says = t.statement_says == null ? '' : ` | Statement says ${esc(finStmtUsd(t.statement_says))}`;
  const off = t.off_by ? ` <span class="pill needs">${esc(finStmtUsd(Math.abs(t.off_by)))} off</span>` : '';
  const flip = `<label class="fin-check"><input type="checkbox" data-fin-stmt-flip${r.signs.flipped ? ' checked' : ''}>${r.card ? 'Charges show positive' : 'Money out shows positive'}</label>`;
  const head = `${esc(money.name)}${money.last4 ? ` ··${esc(money.last4)}` : ''} · ${esc(finStmtMonth(r.end))} · ${esc(r.file_name)}`;
  return `<p class="fin-stmt-head">${head}</p>
    <p class="fin-stmt-tie">Opening ${esc(finStmtUsd(t.opening))} + ${inWord} ${esc(finStmtUsd(t.added))} − ${outWord} ${esc(finStmtUsd(t.removed))}
      = Ends at ${esc(finStmtUsd(t.ends))}${says}${off}</p>
    ${flip}
    <div class="fin-scroll"><table class="fin-t fin-stmt-t"><thead><tr><th>Date</th><th>Description</th><th>Amount</th><th>Vendor</th>
      <th>Attributed to</th><th>Status</th></tr></thead><tbody>${r.rows.map(finStmtRowHtml).join('')}</tbody></table></div>`;
}

// Shows a review under the file picker, and names the button for what Post will do.
function finStmtShow(form: HTMLFormElement, money: FinStmtMoney, r: FinStmtReview): void {
  const box = finNeed(form.querySelector<HTMLElement>('[data-fin-stmt-review]'), 'the statement review');
  box.innerHTML = finStmtReviewHtml(money, r);
  box.hidden = false;
  finNeed(form.querySelector<HTMLButtonElement>('[type=submit]'), 'the submit button').textContent =
    r.new === 1 ? 'Post 1 row' : `Post ${r.new} rows`;
}
// Forgets the review (a new file, or a post that failed, which needs a fresh preview) and puts the button back to Preview.
function finStmtReset(form: HTMLFormElement): void {
  const box = finNeed(form.querySelector<HTMLElement>('[data-fin-stmt-review]'), 'the statement review');
  box.hidden = true;
  box.innerHTML = '';
  finNeed(form.querySelector<HTMLButtonElement>('[type=submit]'), 'the submit button').textContent = 'Preview';
}

// Upload statement for a money account: pick the file, Preview it, then Post its New rows. Cancel closes it. The signs box
// previews the same file again read the other way. Each preview request takes the next revision; only the newest one may
// show its reply, and Post works only on a shown preview (phase 'reviewed') with the snapshot that preview captured.
function finStatementUpload(money: FinStmtMoney): void {
  let phase: FinStmtPhase = 'empty', revision = 0, state: FinStmtState | null = null;
  const body = `<label>File<input type="file" name="file" accept=".csv,.ofx,.qfx,text/csv" required></label>
    <div class="fin-stmt" data-fin-stmt-review hidden></div>`;
  const submit = (form: HTMLFormElement) => finNeed(form.querySelector<HTMLButtonElement>('[type=submit]'), 'the submit button');
  // Drops the shown preview at once (a new file or sign choice, or the dialog closing): a reply still on its way is now
  // an older revision's and is dropped when it lands.
  const forget = (): number => { state = null; return ++revision; };
  // The file picker and signs box stay as they are while a post is on its way.
  const lock = (form: HTMLFormElement, on: boolean) => form.querySelectorAll<HTMLInputElement>('input').forEach(x => { x.disabled = on; });
  const review = async (form: HTMLFormElement, file: File, data: string, signs: FinStmtSigns): Promise<void> => {
    const mine = forget();
    phase = 'previewing';
    submit(form).disabled = true;
    let shown: FinStmtReview;
    try {
      shown = await post('/v2/finance/statements/preview', {money_account_id: money.id, file: data, file_name: file.name, signs});
    } catch (e) {
      if (mine !== revision) return;
      phase = 'error';
      finStmtReset(form);
      submit(form).disabled = false;
      throw e;
    }
    if (mine !== revision) return;
    state = {file, data, signs, review: shown, op: newRequestId()};
    phase = 'reviewed';
    finStmtShow(form, money, shown);
    submit(form).disabled = false;
    form.querySelector<HTMLInputElement>('[data-fin-stmt-flip]')?.addEventListener('change', ev => {
      const flipped = (ev.currentTarget as HTMLInputElement).checked;
      const err = form.querySelector<HTMLElement>('[data-error]');
      if (err) err.hidden = true;
      review(form, file, data, flipped ? 'flipped' : 'as_shown').catch((e: unknown) => {
        if (err) { err.textContent = e instanceof Error ? e.message : 'Not read'; err.hidden = false; }
      });
    });
  };
  const dlg = finDialog('Upload statement', body, 'Preview', async (form: HTMLFormElement) => {
    const input = finNeed(form.querySelector<HTMLInputElement>('input[type=file]'), 'the file input');
    const file = input.files?.[0];
    if (!file) throw new Error('Pick a CSV, OFX or QFX file');
    if (file.size > FIN_STMT_MAX_BYTES) throw new Error('A statement file is 1 MB at most');
    if (phase === 'previewing' || phase === 'posting') throw new Error('Wait for the preview to finish');
    const snap = state;
    if (phase !== 'reviewed' || !snap || snap.file !== file) {
      await review(form, file, await finStmtBase64(file), 'auto');
      return false;
    }
    if (snap.review.new === 0) throw new Error('No new rows to post');
    phase = 'posting';
    lock(form, true);
    let done: {status?: string; result?: {new?: number}};
    try {
      done = await finAct('statement_post', {
        money_account_id: money.id, file: snap.data, file_name: snap.file.name, preview_token: snap.review.token,
        ...(snap.signs === 'auto' ? {} : {signs: snap.signs}),
      }, snap.op);
    } catch (e) {
      lock(form, false);
      // No answer came back, so it may have posted: keep this preview and its key, and Post again sends the same action.
      if ((e as {unconfirmed?: boolean} | null)?.unconfirmed && state === snap) { phase = 'reviewed'; throw e; }
      // The preview may be stale (its token lapses after 30 minutes, or the books moved): the next Preview starts again.
      forget();
      phase = 'error';
      finStmtReset(form);
      throw e;
    }
    if (done.status === 'committed') toast(`Posted ${done.result?.new ?? snap.review.new} rows`);
    void finSrcLoad();
    return true;
  });
  const form = finNeed(dlg.querySelector<HTMLFormElement>('form'), 'the upload form');
  form.querySelector('input[type=file]')?.addEventListener('change', () => {
    forget();
    phase = 'empty';
    finStmtReset(form);
    submit(form).disabled = false;
  });
  dlg.addEventListener('close', () => { forget(); });
}
