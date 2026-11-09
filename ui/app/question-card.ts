/* ui/app/question-card.ts — the one question card: a bot's question to this person, drawn the same in Decide
   (decide.js) and in the task page's Needs you box (task-modal.js), from the same Needs you item (GET /v2/needs-you):
   who really asks, how long ago, what it holds up; the question and its one "why it matters" line; the picks, each
   with its "what happens" line (`then`); the fallback line small at the bottom; ids, paths and the rest under Details.
   Several picks are checkboxes, the recommended ones ticked, answered with "Yes to all" or "Send ticked (n)": the bot
   hears each unticked pick as "not this one" (backend/task_review.py answer_task).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The shapes this file reads of a Needs you item (`S.v2.needs`) and of its ask. A field is optional where the page may
// be sent without it; the server sends more than is named here.
/** One pick on a question: its label, its "what happens" line, its description and its picture ("<file id>@<version>"). */
interface QcOption {
  label: string;
  recommended?: boolean;
  then?: string | null;
  description?: string | null;
  file?: string | null;
}
/** One question of an ask: its text, its why line, its picks; `multi` ticks several, `other` takes the note as Other. */
interface QcQuestion {
  id?: string;
  question?: string;
  why?: string | null;
  multi?: boolean;
  other?: boolean;
  options: QcOption[];
}
/** A file a review asks about: the exact version under review, by its label. */
interface QcReviewFile {
  label?: string;
  file?: string | null;
  version?: number | string | null;
}
/** A review question's files (refs.review): `pick` when the picks are the files themselves. */
interface QcReview {
  pick?: boolean;
  files: QcReviewFile[];
}
/** A file on a review question (refs.attachments): its address, its kind, and whether it was removed. */
interface QcAttachment {
  file_id?: string | null;
  version?: number | string | null;
  name?: string | null;
  title?: string | null;
  kind: TfKind;
  url?: string | null;
  removed?: boolean;
  label?: string | null;
}
/** The refs an ask carries: its questions, its files, its review, its spend, what happens if nobody answers, its target. */
interface QcRefs {
  questions?: QcQuestion[] | null;
  attachments?: QcAttachment[];
  review?: QcReview | null;
  spend?: { label?: string; amount?: string | number; account?: string; what?: string } | null;
  if_no_answer?: { do?: string } | null;
  target?: { task?: string; file?: string; version?: number | string } | null;
}
/** A bot's ask to this person (`it.ask`). */
interface QcAsk {
  id?: string;
  body?: string | null;
  sender_id?: string;
  recipient_id?: string | null;
  created_at?: string;
  goes_ahead_at?: string | null;
  refs?: QcRefs | null;
}
/** One row of Needs you (`S.v2.needs`), as the card reads it. */
interface QcItem {
  id: string;
  kind?: string;
  title?: string | null;
  why?: string | null;
  body?: string | null;
  created_at?: string | null;
  holds_up?: { title?: string | null } | null;
  ask?: QcAsk | null;
}
/** The task a card belongs to: its id is all the card reads of it. */
interface QcTaskRef {
  id?: string | number | null | undefined;
}
/** The question a card shows, found in the Needs you data: the item and its one question. */
interface QcFound {
  it: QcItem;
  q: QcQuestion;
}
/** A question box's ticks and note, kept while the page redraws it. */
interface QcDraft {
  ticked: Set<number>;
  note: string;
}

const QC_MAX_OPTIONS = 6;
// The card's question: one question with 2 to 6 options, put to this person by someone else. Several questions in one
// ask stay a task card that links to the task.
function qcardQuestion(it: QcItem): QcQuestion | null {
  const ask = it?.ask, qs = ask?.refs?.questions;
  if (!ask || !Array.isArray(qs) || qs.length !== 1) return null;
  const q = qs[0], n = (q?.options || []).length;
  if (!q || n < 2 || n > QC_MAX_OPTIONS || ask.recipient_id !== myActor() || ask.sender_id === myActor()) return null;
  return q;
}
// Who really asks: the bot by its name, or Tico for its own checks. Never the assistant's name or "You asked".
const qcardAsker = (actor?: string | null) => !actor || actor === 'keeper' || !/^(bot|human):/.test(actor) ? KEEPER_LABEL : actorLabel(actor);
function qcardAge(iso?: string | null): string {
  if (!iso) return '';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (isNaN(s)) return '';
  if (s < 3600) return 'asked just now';
  if (s < 86400) { const h = Math.floor(s / 3600); return `asked ${h} hour${h === 1 ? '' : 's'} ago`; }
  const d = Math.floor(s / 86400);
  return `asked ${d} day${d === 1 ? '' : 's'} ago`;
}
// The header line after the asker's name: "asked 3 days ago · holds up: Website sales chat build".
function qcardSub(it: QcItem): string {
  const held = it.holds_up?.title ? `holds up: ${plainActors(it.holds_up.title)}` : '';
  return [qcardAge(it.ask?.created_at || it.created_at), held].filter(Boolean).join(' · ');
}
// The picks ticked when the card opens: the recommended ones.
const qcardRecommended = (q: QcQuestion): Set<number> => new Set((q.options || []).map((o, i) => o.recommended ? i : -1).filter(i => i >= 0));
const qcardThen = (text?: string | null) => text ? `<span class="qc-then">${esc(text)}</span>` : '';
// A button's "what happens" line: the asker's own, else what is true of every answer, that the asker hears it (a
// Yes / No asked with no options, or a person's question, has none of its own).
const qcardOutcome = (o: QcOption, bot: string) => String(o.then || '').trim() || `${bot} hears "${o.label}"`;
// An option's picture from the task's files (task-files.js tfNorm), the same in Decide and on the task page; a box
// that fills in once the files load when they haven't yet.
function qcardPicture(files: TfFile[] | undefined, o: unknown): string {
  // The option's picture key, "<file id>@<version>", or '' when it has none.
  const file = typeof o === 'object' && o !== null && 'file' in o ? String(o.file || '') : '';
  const [fid, n] = file.split('@');
  const f = fid && (files || []).find(x => x.id === fid);
  if (!f) return fid ? `<span class="dc-pic tf-skel" data-qc-pic="${esc(file)}"></span>` : '';
  const v = tfVersion(f, n), kind = tfKind(f.name, v.mime || f.mime);
  const src = kind === 'image' && Number(v.size || 0) <= TF_INLINE_IMAGE ? v.url : tfPicture(v, kind);
  return src ? `<span class="dc-pic tf-skel"><img alt="" data-tf-src="${esc(src)}"></span>`
    : `<span class="dc-pic dc-glyph">${esc(tfGlyph(kind, f.name))}</span>`;
}
// The task's files arrived: fill in the pictures drawn before they did.
function qcardPaintPictures(root: ParentNode | null | undefined, files: TfFile[] | undefined): void {
  for (const el of root?.querySelectorAll?.<HTMLElement>('[data-qc-pic]') || []) {
    const html = qcardPicture(files, {file: el.dataset.qcPic});
    if (html && !html.includes('data-qc-pic')) el.outerHTML = html;
  }
}
const qcardDesc = (o: QcOption) => o.description ? `<span class="dc-desc">${esc(o.description)}</span>` : '';

// A review question's files (backend/file_reviews.py): the exact versions under review, as the server read them for
// this reader with fresh addresses (refs.attachments). The first four show inline (visual-cards.js), the rest chips.
const QC_CHANGES = 'Request changes';
const qcardReview = (it: QcItem): QcReview | null => { const r = it?.ask?.refs?.review; return r && Array.isArray(r.files) && r.files.length ? r : null; };
// Request changes on a review asks for the note that says what to change.
const qcardNeedsNote = (it: QcItem, q: QcQuestion | undefined, labels: string[]) => !!qcardReview(it) && labels.length === 1 && labels[0] === QC_CHANGES
  && !!q?.options.some(o => o.label === QC_CHANGES);
const qcardReviewChips = (rest: QcAttachment[]) => {
  const list = rest.filter(f => !f.removed && f.url);
  return list.length ? `<div class="qc-rchips">${list.map(f => `<a class="dc-file" href="${esc(f.url)}" target="_blank" rel="noopener"><span class="tc-kind">${esc(tfGlyph(f.kind, f.name || ''))}</span><span class="dc-fname">${esc(f.title || f.name || 'file')}</span><span class="muted tnum">v${Number(f.version)}</span></a>`).join('')}</div>` : '';
};
function qcardMediaHTML(it: QcItem, files: QcAttachment[] | undefined, key: string): string {
  return visualCardsHTML(files, {key: `qc:${it.ask?.id}${key}`, chips: (rest: QcAttachment[]) => qcardReviewChips(rest)});
}
// A pick between files: each file's media with its own button, then Request changes.
function qcardReviewPicksHTML(it: QcItem, q: QcQuestion, review: QcReview, attr: string): string {
  const files = new Map((it.ask?.refs?.attachments || []).map((f): [string, QcAttachment] => [`${f.file_id}@${f.version}`, f]));
  const picks = q.options.map((o, i) => {
    const e = review.files.find(x => x.label === o.label), f = e && files.get(`${e.file}@${e.version}`);
    return e ? `<div class="qc-rpick">${f ? qcardMediaHTML(it, [f], ':' + i) : ''}<button type="button" class="qc-btn" data-${attr}="pick" data-i="${i}"><span class="qc-bl"><b class="tnum">${i + 1}</b> ${esc(o.label)}</span></button></div>` : '';
  }).join('');
  const changes = q.options.findIndex(o => o.label === QC_CHANGES);
  return `<div class="qc-rpicks">${picks}</div>${changes < 0 ? '' : `<button type="button" class="qc-btn qc-changes" data-${attr}="pick" data-i="${changes}"><span class="qc-bl">${QC_CHANGES}</span></button>`}`;
}
// What a pick also approves: a spend the bot then makes once.
function qcardSpendHTML(it: QcItem): string {
  const s = it?.ask?.refs?.spend;
  return s ? `<p class="qc-spend">${esc(s.label)} also approves spending ${esc(s.amount)} (${esc(s.account)}): ${esc(s.what)}</p>` : '';
}

// The question, its why line and its picks. `attr` names the data attribute its buttons carry (Decide's `dc`, the
// task page's `qc`); `ticked` is the set of ticked picks; `pic(o)` draws an option's picture, when the page has one.
function qcardBodyHTML(it: QcItem, q: QcQuestion, {attr = 'qc', ticked = qcardRecommended(q), pic = () => ''}: {attr?: string; ticked?: Set<number>; pic?: (o: QcOption) => string} = {}): string {
  const why = String(it.why || q.why || '').trim(), review = qcardReview(it);
  let picks = '';
  if (review?.pick) picks = qcardReviewPicksHTML(it, q, review, attr);
  else if (q.multi) {
    picks = `<ul class="qc-picks">${q.options.map((o, i) => `<li><label class="qc-pick${o.recommended ? ' rec' : ''}">
      <input type="checkbox" data-qc-tick="${i}"${ticked.has(i) ? ' checked' : ''}>${pic(o)}<span class="qc-pl"><span class="qc-l"><b class="tnum">${i + 1}.</b> ${esc(o.label)}${o.recommended
        ? ' <span class="qc-rec">(recommended)</span>' : ''}</span>${qcardThen(o.then)}${qcardDesc(o)}</span></label></li>`).join('')}</ul>`;
  } else {
    const pics = q.options.some(o => o.file), bot = qcardAsker(it.ask?.sender_id);
    if (q.options.length === 2 && pics) picks = `<div class="dc-pair">${q.options.map((o, i) =>
      `<button type="button" class="dc-opt" data-${attr}="pick" data-i="${i}" aria-label="${esc(o.label)}">${pic(o)}<span class="dc-l">${esc(o.label)}</span>${qcardThen(qcardOutcome(o, bot))}${qcardDesc(o)}</button>`).join('')}</div>`;
    else if (q.options.length === 2) picks = q.options.some(o => o.description)
      ? `<ul class="dc-descs">${q.options.map(o => `<li><b>${esc(o.label)}</b>${qcardDesc(o)}</li>`).join('')}</ul>` : '';
    else picks = `<div class="dc-tiles">${q.options.map((o, i) =>
      `<button type="button" class="dc-tile${o.file ? ' pic' : ''}" data-${attr}="pick" data-i="${i}">${o.file ? pic(o) : ''}<span class="dc-tl"><b class="tnum">${i + 1}</b><span class="dc-l">${esc(o.label)}</span></span><span class="qc-then dc-tthen">${esc(qcardOutcome(o, bot))}</span>${qcardDesc(o)}</button>`).join('')}</div>`;
  }
  const media = review && !review.pick ? `<div class="qc-review">${qcardMediaHTML(it, it.ask?.refs?.attachments || [], '')}</div>` : '';
  return `<p class="dc-q">${esc(q.question)}</p>${why ? `<p class="dc-ctx qc-why">Why it matters: ${esc(plainActors(why))}</p>` : ''}${media}${picks}${qcardSpendHTML(it)}`;
}

// A two-option question's buttons, in the order they read (Yes / No reads No, Yes like an approval).
const qcardYesNo = (q: QcQuestion) => !q.multi && q.options.length === 2 && q.options.map(o => String(o.label).toLowerCase()).join() === 'yes,no';
// The card's buttons, each with its "what happens" line: Yes to all and Send ticked for several picks; one button per
// option for two; none for more (the tiles are the buttons). `keys` labels a button with its key.
function qcardActionsHTML(it: QcItem, q: QcQuestion, {attr = 'qc', ticked = qcardRecommended(q), keys = false}: {attr?: string; ticked?: Set<number>; keys?: boolean} = {}): string {
  const k = (key: string | number) => keys ? `<kbd>${key}</kbd>` : '';
  const bot = qcardAsker(it.ask?.sender_id), n = q.options.length, t = ticked.size;
  const btn = (cls: string, data: string, label: string, then: string, extra = '') =>
    `<button type="button" class="qc-btn${cls ? ' ' + cls : ''}" data-${attr}="${data}"${extra}><span class="qc-bl">${label}</span>${qcardThen(then)}</button>`;
  if (q.multi) return btn('', 'all', `Yes to all${k('A')}`, `All ${n} go to ${bot} as yes`)
    + btn('primary', 'ticked', `Send ticked (<span class="tnum qc-count">${t}</span>)${k('T')}`,
      t === n ? `All ${n} go to ${bot} as yes` : `${t ? `These ${t} as yes; ` : ''}${bot} hears "not this one" for the rest`,
      t ? '' : ' disabled');
  if (n !== 2) return '';
  return (qcardYesNo(q) ? [1, 0] : [0, 1]).map((i, pos) => {
    const o = q.options[i];
    if (!o) return '';
    const key = qcardYesNo(q) ? (String(o.label)[0] ?? '').toUpperCase() : i + 1;
    return btn(pos && String(o.label).toLowerCase() === 'yes' ? 'primary' : '', 'pick', `${esc(o.label)}${k(key)}`, qcardOutcome(o, bot), ` data-i="${i}"`);
  }).join('');
}

// What happens if nobody answers (backend/ask_deadlines.py), small and at the bottom: never styled as a pick.
function qcardFallbackHTML(it: QcItem): string {
  const ask = it.ask, d = ask?.refs?.if_no_answer;
  if (!d?.do) return '';
  const bot = qcardAsker(ask?.sender_id), at = ask?.goes_ahead_at ? new Date(ask.goes_ahead_at) : null;
  const when = at && !isNaN(at.getTime()) ? at.toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'}) : '';
  return `<p class="qc-fallback">${esc(when ? `If you don't answer by ${when}: ${d.do}`
    : `If you don't answer, ${bot} waits for you before it goes ahead (${d.do}).`)}</p>`;
}

// Details, folded: the whole message, the task's own words and a link to the task. Ids, paths and error text a bot
// wrote stay here, never above.
function qcardDetailsHTML(it: QcItem, q: QcQuestion, {link = true}: {link?: boolean} = {}): string {
  const text = (s: unknown) => String(s || '').trim();
  const asked = text(it.ask?.body), body = text(it.body);
  const parts = [asked && asked !== text(q.question) ? `<div class="dc-rest md">${safeMd(plainActors(asked))}</div>` : '',
    body && body !== asked && body !== text(it.title) ? `<div class="dc-rest md">${safeMd(plainActors(body))}</div>` : '',
    link && it.id ? `<p><a href="#/task/${encodeURIComponent(it.id)}">Open the task: ${esc(plainActors(it.title || ''))}</a></p>` : ''];
  const inner = parts.join('');
  return inner ? `<details class="dc-details qc-details"><summary>Details</summary>${inner}</details>` : '';
}

// The answer a card sends, as requests run in order: the picks (and the note, as the question's Other text when it
// takes one, else a comment). `labels` are the picks said yes to; the rest of several picks are "not this one".
function qcardWrites(it: QcItem, q: QcQuestion, labels: (string | undefined)[], note: string | undefined): {path: string; body: unknown}[] {
  const ask = it.ask, id = encodeURIComponent(it.id), t = ask?.refs?.target;
  // A question task (its ask is the task itself) answers with {task}; its answer closes it.
  const target = t?.task ? {task: t.task} : t?.file ? {file: t.file, version: Number(t.version)} : {comment: ask?.id};
  const other = note && q.other !== false;
  const writes: {path: string; body: unknown}[] = [{path: `/v2/tasks/${id}/answers`, body: {target, answers: {[String(q.id)]: labels}, ...(other ? {other: note} : {})}}];
  if (note && !other) writes.push({path: `/v2/tasks/${id}/comments`, body: {text: note}});
  return writes;
}
// What an answer's toast says.
function qcardSaid(it: QcItem, q: QcQuestion, labels: string[]): string {
  const to = qcardAsker(it.ask?.sender_id);
  if (!q.multi) return `Picked ${labels[0]} · sent to ${to}`;
  return labels.length === q.options.length ? `Yes to all · sent to ${to}` : `Sent ${labels.length} ticked · sent to ${to}`;
}
// The labels a card's action says yes to: one pick, all of them, or the ticked ones.
function qcardLabels(q: QcQuestion | undefined, what: string | undefined, i: number | undefined, ticked?: Set<number>): string[] {
  const options = q?.options ?? [];
  if (what === 'pick') {
    const picked = options[Number(i)];
    return picked ? [picked.label] : [];
  }
  if (what === 'all') return options.map(o => o.label);
  return options.filter((o, n) => ticked?.has(n)).map(o => o.label);
}
// A tick changed: the Send ticked button says how many, and what happens.
function qcardRetick(root: ParentNode | null, it: QcItem, q: QcQuestion, ticked: Set<number>, attr: string): void {
  const actions: ParentNode | null | undefined = root?.querySelector('.qc-actions') || root;
  const old = actions?.querySelector<HTMLElement>(`[data-${attr}="ticked"]`);
  if (!old) return;
  const html = qcardActionsHTML(it, q, {attr, ticked, keys: !!old.querySelector('kbd')});
  const box = document.createElement('div');
  box.innerHTML = html;
  const fresh = box.querySelector(`[data-${attr}="ticked"]`);
  if (fresh) old.replaceWith(fresh);
}

// ---- the task page's Needs you box: the same card, from the same Needs you item.
// What the person has ticked and typed on a task's card, kept while the page redraws it (the task's details landing,
// a property saved). Keyed by the ask and the question as asked, so a question the bot rewords starts afresh.
const QC_DRAFTS = new Map<string, QcDraft>();
const qcardDraftKey = (it: QcItem, q: QcQuestion): string => JSON.stringify([it.id, it.ask?.id, q.id, q.question, (q.options || []).map(o => o.label), sessionIdentity()]);
// A draft belongs to the question the box shows (its `data-qc-key`), never to a newer one the Needs you data holds:
// if the bot rewords while the old box is open, the next draw finds the draft stale and says so.
function qcardDraft(box: HTMLElement): void {
  const key = box.dataset.qcKey;
  if (!key) return;
  for (const k of QC_DRAFTS.keys()) if (JSON.parse(k)[0] === JSON.parse(key)[0]) QC_DRAFTS.delete(k);
  QC_DRAFTS.set(key, {
    ticked: new Set([...box.querySelectorAll<HTMLInputElement>('[data-qc-tick]:checked')].map(x => Number(x.dataset.qcTick))),
    note: box.querySelector<HTMLTextAreaElement>('.qc-note')?.value || ''});
}
// The question a box shows, or null when the Needs you data now holds a different one (or none) for its task.
function qcardShown(box: HTMLElement): QcFound | null {
  const found = qcardItem({id: box.dataset.qcard});
  return found && qcardDraftKey(found.it, found.q) === box.dataset.qcKey ? found : null;
}
// Draw a box again from the current question: a stale draft is dropped there, with its explanation.
function qcardRedraw(box: HTMLElement): void {
  box.outerHTML = qcardBoxHTML({id: box.dataset.qcard});
}
function qcardItem(t: QcTaskRef): QcFound | null {
  const it: QcItem | undefined = [...(S.v2?.needs || []), needsOneHeld(t?.id)].find(x => x && String(x.id) === String(t?.id) && ['question', 'waiting', 'task'].includes(x.kind));
  if (!it) return null;
  const q = qcardQuestion(it);
  return q ? {it, q} : null;
}
function qcardBoxHTML(t: QcTaskRef): string {
  const found = qcardItem(t);
  if (!found) return '';
  const {it, q} = found, key = qcardDraftKey(it, q), draft = QC_DRAFTS.get(key);
  // A draft made on wording the bot has since replaced isn't sent on the new question: say so once, and start afresh.
  const stale = [...QC_DRAFTS.keys()].filter(k => k !== key && JSON.parse(k)[0] === it.id);
  stale.forEach(k => QC_DRAFTS.delete(k));
  const ticked = draft ? new Set(draft.ticked) : qcardRecommended(q), files = TF_CACHE.get(String(t.id));
  return `<section class="task-ask qcard" data-qcard="${esc(it.id)}" data-qc-key="${esc(key)}" aria-label="Needs you">
    <div class="qc-head"><span class="lbl">Needs you</span> <b class="qc-who">${esc(qcardAsker(it.ask?.sender_id))}</b><span class="qc-sub"> · ${esc(qcardSub(it))}</span></div>
    ${stale.length ? '<p class="qc-changed muted">The question changed, so your earlier ticks and note were cleared.</p>' : ''}
    ${qcardBodyHTML(it, q, {attr: 'qc', ticked, pic: o => qcardPicture(files, o)})}
    <div class="qc-actions">${qcardActionsHTML(it, q, {attr: 'qc', ticked})}</div>
    <textarea class="qc-note" rows="1" maxlength="4000" aria-label="Note" placeholder="Add a note">${esc(draft?.note || '')}</textarea>
    <div class="qc-foot">${qcardFallbackHTML(it)}${qcardDetailsHTML(it, q, {link: false})}</div>
  </section>`;
}
document.addEventListener('change', ev => {
  const el = eventElement(ev);
  const box = el?.closest<HTMLElement>('.qcard[data-qcard]');
  if (!el || !box || !el.matches('[data-qc-tick]')) return;
  qcardDraft(box);
  const found = qcardShown(box);
  if (!found) { qcardRedraw(box); return; }
  const ticked = new Set([...box.querySelectorAll<HTMLInputElement>('[data-qc-tick]:checked')].map(x => Number(x.dataset.qcTick)));
  qcardRetick(box, found.it, found.q, ticked, 'qc');
});
document.addEventListener('input', ev => {
  const el = eventElement(ev);
  const box = el?.closest<HTMLElement>('.qcard[data-qcard]');
  if (box && el?.matches('.qc-note')) qcardDraft(box);
});
document.addEventListener('click', async ev => {
  const b = eventElement(ev)?.closest<HTMLButtonElement>('.qcard[data-qcard] [data-qc]');
  if (!b || b.disabled) return;
  const box = b.closest<HTMLElement>('.qcard');
  if (!box) return;
  const found = qcardShown(box);
  if (!found) {
    // The question changed under this box: never send its answer against the new one.
    qcardDraft(box);
    qcardRedraw(box);
    toast('The question changed. Check it again before you answer.', true);
    return;
  }
  const {it, q} = found;
  const ticked = new Set([...box.querySelectorAll<HTMLInputElement>('[data-qc-tick]:checked')].map(x => Number(x.dataset.qcTick)));
  const labels = qcardLabels(q, b.dataset.qc, Number(b.dataset.i), ticked);
  if (!labels.length) return;
  const note = box.querySelector<HTMLTextAreaElement>('.qc-note')?.value.trim() || '';
  if (!note && qcardNeedsNote(it, q, labels)) {
    const n = box.querySelector<HTMLTextAreaElement>('.qc-note');
    if (n) { n.placeholder = 'What should change?'; n.focus(); }
    return;
  }
  // Busy while the answer is on its way: nothing draws the box again meanwhile (nativeVisualRepaint skips it).
  box.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLTextAreaElement>('button, input, textarea').forEach(x => { x.disabled = true; });
  box.setAttribute('aria-busy', 'true');
  try {
    for (const w of qcardWrites(it, q, labels, note)) await post(w.path, w.body, newRequestId());
    QC_DRAFTS.delete(qcardDraftKey(it, q));
    toast(qcardSaid(it, q, labels));
    void v2Refresh();
  } catch (e) {
    box.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLTextAreaElement>('button, input, textarea').forEach(x => { x.disabled = false; });
    box.removeAttribute('aria-busy');
    toast(errorMessage(e, ''), true);
  }
});
