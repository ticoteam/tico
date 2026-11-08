/* ui/app/finance-statement-upload.ts — Finance › Sources: "Upload statement" for a bank or card account Tico cannot connect.
   A CSV, OFX or QFX file is previewed first: the tie-out (opening, in, out, ends at, what the statement says), then each
   row New or Already in Tico, with its vendor and the rule that would attribute it. Post puts the New rows in (statement_
   post, finance.book) with the preview's token, so it posts what was shown. A gap in the tie-out is a warning: the post
   still goes through, and the account shows "Off by" until a reconciliation clears it.
   Reads and writes: POST /api/v2/finance/statements/preview; statement_post through POST /api/v2/finance/actions.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The most a statement file may be (backend/finance/core/statement_files.py MAX_BYTES).
const FIN_STMT_MAX_BYTES = 4 * 1024 * 1024;

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
  end: string; card: boolean; last4: string | null; new: number; duplicate: number; tie: FinStmtTie; rows: FinStmtRow[];
}
// The file being reviewed: its bytes as base64 (the server keeps none), and its preview.
interface FinStmtState { file: File; data: string; review: FinStmtReview }

// Money in cents, signed, two decimals: "−$59.99", "$4,212.80".
const finStmtUsd = (c: number): string =>
  (c < 0 ? '−$' : '$') + (Math.abs(c) / 100).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
// The file's bytes as base64, which is what the server reads. Built in slices, so a 4 MB file does not overflow the stack.
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
  const head = `${esc(money.name)}${money.last4 ? ` ··${esc(money.last4)}` : ''} · ${esc(finStmtMonth(r.end))} · ${esc(r.file_name)}`;
  return `<p class="fin-stmt-head">${head}</p>
    <p class="fin-stmt-tie">Opening ${esc(finStmtUsd(t.opening))} + ${inWord} ${esc(finStmtUsd(t.added))} − ${outWord} ${esc(finStmtUsd(t.removed))}
      = Ends at ${esc(finStmtUsd(t.ends))}${says}${off}</p>
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

// Upload statement for a money account: pick the file, Preview it, then Post its New rows. Cancel closes it.
function finStatementUpload(money: FinStmtMoney): void {
  let state: FinStmtState | null = null;
  const body = `<label>File<input type="file" name="file" accept=".csv,.ofx,.qfx,text/csv" required></label>
    <div class="fin-stmt" data-fin-stmt-review hidden></div>`;
  const dlg = finDialog('Upload statement', body, 'Preview', async (form: HTMLFormElement) => {
    const input = finNeed(form.querySelector<HTMLInputElement>('input[type=file]'), 'the file input');
    const file = input.files?.[0];
    if (!file) throw new Error('Pick a CSV, OFX or QFX file');
    if (file.size > FIN_STMT_MAX_BYTES) throw new Error('A statement file is 4 MB at most');
    if (!state || state.file !== file) {
      const data = await finStmtBase64(file);
      const review: FinStmtReview = await post('/v2/finance/statements/preview', {money_account_id: money.id, file: data, file_name: file.name});
      state = {file, data, review};
      finStmtShow(form, money, review);
      return false;
    }
    const {data, review} = state;
    if (review.new === 0) throw new Error('No new rows to post');
    let done: {status?: string; result?: {new?: number}};
    try {
      done = await finAct('statement_post', {money_account_id: money.id, file: data, file_name: file.name, preview_token: review.token});
    } catch (e) {
      // The preview may be stale (its token lapses after 30 minutes, or the books moved): the next Preview starts again.
      state = null;
      finStmtReset(form);
      throw e;
    }
    if (done.status === 'committed') toast(`Posted ${done.result?.new ?? review.new} rows`);
    void finSrcLoad();
    return true;
  });
  const form = finNeed(dlg.querySelector<HTMLFormElement>('form'), 'the upload form');
  form.querySelector('input[type=file]')?.addEventListener('change', () => { state = null; finStmtReset(form); });
}
