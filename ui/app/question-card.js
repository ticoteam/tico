/* ui/app/question-card.js — the one question card: a bot's question to this person, drawn the same in Decide
   (decide.js) and in the task page's Needs you box (task-modal.js), from the same Needs you item (GET /v2/needs-you):
   who really asks, how long ago, what it holds up; the question and its one "why it matters" line; the picks, each
   with its "what happens" line (`then`); the fallback line small at the bottom; ids, paths and the rest under Details.
   Several picks are checkboxes, the recommended ones ticked, answered with "Yes to all" or "Send ticked (n)": the bot
   hears each unticked pick as "not this one" (backend/task_review.py answer_task).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const QC_MAX_OPTIONS = 6;
// The card's question: one question with 2 to 6 options, put to this person by someone else. Several questions in one
// ask stay a task card that links to the task.
function qcardQuestion(it) {
  const ask = it?.ask, qs = ask?.refs?.questions;
  if (!ask || !Array.isArray(qs) || qs.length !== 1) return null;
  const q = qs[0], n = (q.options || []).length;
  if (n < 2 || n > QC_MAX_OPTIONS || ask.to_actor !== myActor() || ask.from_actor === myActor()) return null;
  return q;
}
// Who really asks: the bot by its name, or Tico for its own checks. Never the assistant's name or "You asked".
const qcardAsker = actor => !actor || actor === 'keeper' || !/^(bot|human):/.test(actor) ? 'Tico' : actorLabel(actor);
function qcardAge(iso) {
  const s = (Date.now() - new Date(iso)) / 1000;
  if (!iso || isNaN(s)) return '';
  if (s < 3600) return 'asked just now';
  if (s < 86400) { const h = Math.floor(s / 3600); return `asked ${h} hour${h === 1 ? '' : 's'} ago`; }
  const d = Math.floor(s / 86400);
  return `asked ${d} day${d === 1 ? '' : 's'} ago`;
}
// The header line after the asker's name: "asked 3 days ago · holds up: Website sales chat build".
function qcardSub(it) {
  const held = it.holds_up?.title ? `holds up: ${plainActors(it.holds_up.title)}` : '';
  return [qcardAge(it.ask?.created || it.created), held].filter(Boolean).join(' · ');
}
// The picks ticked when the card opens: the recommended ones.
const qcardRecommended = q => new Set((q.options || []).map((o, i) => o.recommended ? i : -1).filter(i => i >= 0));
const qcardThen = text => text ? `<span class="qc-then">${esc(text)}</span>` : '';
// A button's "what happens" line: the asker's own, else what is true of every answer, that the asker hears it (a
// Yes / No asked with no options, or a person's question, has none of its own).
const qcardOutcome = (o, bot) => String(o.then || '').trim() || `${bot} hears "${o.label}"`;
// An option's picture from the task's files (task-files.js tfNorm), the same in Decide and on the task page; a box
// that fills in once the files load when they haven't yet.
function qcardPicture(files, o) {
  const [fid, n] = String(o.file || '').split('@');
  const f = fid && (files || []).find(x => x.id === fid);
  if (!f) return fid ? `<span class="dc-pic tf-skel" data-qc-pic="${esc(o.file)}"></span>` : '';
  const v = tfVersion(f, n), kind = tfKind(f.name, v.mime || f.mime);
  const src = kind === 'image' && Number(v.size || 0) <= TF_INLINE_IMAGE ? v.url : tfPicture(v, kind);
  return src ? `<span class="dc-pic tf-skel"><img alt="" data-tf-src="${esc(src)}"></span>`
    : `<span class="dc-pic dc-glyph">${esc(tfGlyph(kind, f.name))}</span>`;
}
// The task's files arrived: fill in the pictures drawn before they did.
function qcardPaintPictures(root, files) {
  for (const el of root?.querySelectorAll?.('[data-qc-pic]') || []) {
    const html = qcardPicture(files, {file: el.dataset.qcPic});
    if (html && !html.includes('data-qc-pic')) el.outerHTML = html;
  }
}
const qcardDesc = o => o.description ? `<span class="dc-desc">${esc(o.description)}</span>` : '';

// The question, its why line and its picks. `attr` names the data attribute its buttons carry (Decide's `dc`, the
// task page's `qc`); `ticked` is the set of ticked picks; `pic(o)` draws an option's picture, when the page has one.
function qcardBodyHTML(it, q, {attr = 'qc', ticked = qcardRecommended(q), pic = () => ''} = {}) {
  const why = String(it.why || q.why || '').trim();
  let picks = '';
  if (q.multi) {
    picks = `<ul class="qc-picks">${q.options.map((o, i) => `<li><label class="qc-pick${o.recommended ? ' rec' : ''}">
      <input type="checkbox" data-qc-tick="${i}"${ticked.has(i) ? ' checked' : ''}>${pic(o)}<span class="qc-pl"><span class="qc-l"><b class="tnum">${i + 1}.</b> ${esc(o.label)}${o.recommended
        ? ' <span class="qc-rec">(recommended)</span>' : ''}</span>${qcardThen(o.then)}${qcardDesc(o)}</span></label></li>`).join('')}</ul>`;
  } else {
    const pics = q.options.some(o => o.file), bot = qcardAsker(it.ask?.from_actor);
    if (q.options.length === 2 && pics) picks = `<div class="dc-pair">${q.options.map((o, i) =>
      `<button type="button" class="dc-opt" data-${attr}="pick" data-i="${i}" aria-label="${esc(o.label)}">${pic(o)}<span class="dc-l">${esc(o.label)}</span>${qcardThen(qcardOutcome(o, bot))}${qcardDesc(o)}</button>`).join('')}</div>`;
    else if (q.options.length === 2) picks = q.options.some(o => o.description)
      ? `<ul class="dc-descs">${q.options.map(o => `<li><b>${esc(o.label)}</b>${qcardDesc(o)}</li>`).join('')}</ul>` : '';
    else picks = `<div class="dc-tiles">${q.options.map((o, i) =>
      `<button type="button" class="dc-tile${o.file ? ' pic' : ''}" data-${attr}="pick" data-i="${i}">${o.file ? pic(o) : ''}<span class="dc-tl"><b class="tnum">${i + 1}</b><span class="dc-l">${esc(o.label)}</span></span><span class="qc-then dc-tthen">${esc(qcardOutcome(o, bot))}</span>${qcardDesc(o)}</button>`).join('')}</div>`;
  }
  return `<p class="dc-q">${esc(q.question)}</p>${why ? `<p class="dc-ctx qc-why">Why it matters: ${esc(plainActors(why))}</p>` : ''}${picks}`;
}

// A two-option question's buttons, in the order they read (Yes / No reads No, Yes like an approval).
const qcardYesNo = q => !q.multi && q.options.length === 2 && q.options.map(o => String(o.label).toLowerCase()).join() === 'yes,no';
// The card's buttons, each with its "what happens" line: Yes to all and Send ticked for several picks; one button per
// option for two; none for more (the tiles are the buttons). `keys` labels a button with its key.
function qcardActionsHTML(it, q, {attr = 'qc', ticked = qcardRecommended(q), keys = false} = {}) {
  const k = key => keys ? `<kbd>${key}</kbd>` : '';
  const bot = qcardAsker(it.ask?.from_actor), n = q.options.length, t = ticked.size;
  const btn = (cls, data, label, then, extra = '') =>
    `<button type="button" class="qc-btn${cls ? ' ' + cls : ''}" data-${attr}="${data}"${extra}><span class="qc-bl">${label}</span>${qcardThen(then)}</button>`;
  if (q.multi) return btn('', 'all', `Yes to all${k('A')}`, `All ${n} go to ${bot} as yes`)
    + btn('primary', 'ticked', `Send ticked (<span class="tnum qc-count">${t}</span>)${k('T')}`,
      t === n ? `All ${n} go to ${bot} as yes` : `${t ? `These ${t} as yes; ` : ''}${bot} hears "not this one" for the rest`,
      t ? '' : ' disabled');
  if (n !== 2) return '';
  return (qcardYesNo(q) ? [1, 0] : [0, 1]).map((i, pos) => {
    const o = q.options[i];
    const key = qcardYesNo(q) ? String(o.label)[0].toUpperCase() : i + 1;
    return btn(pos && String(o.label).toLowerCase() === 'yes' ? 'primary' : '', 'pick', `${esc(o.label)}${k(key)}`, qcardOutcome(o, bot), ` data-i="${i}"`);
  }).join('');
}

// What happens if nobody answers (backend/ask_deadlines.py), small and at the bottom: never styled as a pick.
function qcardFallbackHTML(it) {
  const ask = it.ask, d = ask?.refs?.if_no_answer;
  if (!d?.do) return '';
  const bot = qcardAsker(ask.from_actor), at = ask.goes_ahead_at ? new Date(ask.goes_ahead_at) : null;
  const when = at && !isNaN(at) ? at.toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'}) : '';
  return `<p class="qc-fallback">${esc(when ? `If you don't answer by ${when}: ${d.do}`
    : `If you don't answer, ${bot} waits for you before it goes ahead (${d.do}).`)}</p>`;
}

// Details, folded: the whole message, the task's own words and a link to the task. Ids, paths and error text a bot
// wrote stay here, never above.
function qcardDetailsHTML(it, q, {link = true} = {}) {
  const text = s => String(s || '').trim();
  const asked = text(it.ask?.body), body = text(it.body);
  const parts = [asked && asked !== text(q.question) ? `<div class="dc-rest md">${safeMd(plainActors(asked))}</div>` : '',
    body && body !== asked && body !== text(it.title) ? `<div class="dc-rest md">${safeMd(plainActors(body))}</div>` : '',
    link && it.kind !== 'ask' && it.id ? `<p><a href="#/task/${encodeURIComponent(it.id)}">Open the task: ${esc(plainActors(it.title || ''))}</a></p>` : ''];
  const inner = parts.join('');
  return inner ? `<details class="dc-details qc-details"><summary>Details</summary>${inner}</details>` : '';
}

// The answer a card sends, as requests run in order: the picks (and the note, as the question's Other text when it
// takes one, else a comment). `labels` are the picks said yes to; the rest of several picks are "not this one".
function qcardWrites(it, q, labels, note) {
  const ask = it.ask, id = encodeURIComponent(it.id), t = ask.refs?.target;
  const target = t?.file ? {file: t.file, version: Number(t.version)} : {comment: ask.id};
  const other = note && q.other !== false;
  const writes = [{path: `/v2/tasks/${id}/answers`, body: {target, answers: {[q.id]: labels}, ...(other ? {other: note} : {})}}];
  if (note && !other) writes.push({path: `/v2/tasks/${id}/comments`, body: {text: note}});
  return writes;
}
// What an answer's toast says.
function qcardSaid(it, q, labels) {
  const to = qcardAsker(it.ask?.from_actor);
  if (!q.multi) return `Picked ${labels[0]} · sent to ${to}`;
  return labels.length === q.options.length ? `Yes to all · sent to ${to}` : `Sent ${labels.length} ticked · sent to ${to}`;
}
// The labels a card's action says yes to: one pick, all of them, or the ticked ones.
function qcardLabels(q, what, i, ticked) {
  if (what === 'pick') return [q.options[i].label];
  if (what === 'all') return q.options.map(o => o.label);
  return q.options.filter((o, n) => ticked.has(n)).map(o => o.label);
}
// A tick changed: the Send ticked button says how many, and what happens.
function qcardRetick(root, it, q, ticked, attr) {
  const actions = root.querySelector('.qc-actions') || root;
  const old = actions.querySelector(`[data-${attr}="ticked"]`);
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
const QC_DRAFTS = new Map();
const qcardDraftKey = (it, q) => JSON.stringify([it.id, it.ask?.id, q.id, q.question, (q.options || []).map(o => o.label)]);
function qcardDraft(box) {
  const found = qcardItem({id: box.dataset.qcard});
  if (!found) return;
  for (const k of QC_DRAFTS.keys()) if (JSON.parse(k)[0] === found.it.id) QC_DRAFTS.delete(k);   // a reworded question's draft goes
  QC_DRAFTS.set(qcardDraftKey(found.it, found.q), {
    ticked: new Set([...box.querySelectorAll('[data-qc-tick]:checked')].map(x => Number(x.dataset.qcTick))),
    note: box.querySelector('.qc-note')?.value || ''});
}
function qcardItem(t) {
  const it = (S.v2?.needs || []).find(x => String(x.id) === String(t?.id) && ['question', 'waiting', 'task'].includes(x.kind));
  const q = it && qcardQuestion(it);
  return q ? {it, q} : null;
}
function qcardBoxHTML(t) {
  const found = qcardItem(t);
  if (!found) return '';
  const {it, q} = found, key = qcardDraftKey(it, q), draft = QC_DRAFTS.get(key);
  // A draft made on wording the bot has since replaced isn't sent on the new question: say so once, and start afresh.
  const stale = [...QC_DRAFTS.keys()].filter(k => k !== key && JSON.parse(k)[0] === it.id);
  stale.forEach(k => QC_DRAFTS.delete(k));
  const ticked = draft ? new Set(draft.ticked) : qcardRecommended(q), files = TF_CACHE.get(String(t.id));
  return `<section class="task-ask qcard" data-qcard="${esc(it.id)}" aria-label="Needs you">
    <div class="qc-head"><span class="lbl">Needs you</span> <b class="qc-who">${esc(qcardAsker(it.ask.from_actor))}</b><span class="qc-sub"> · ${esc(qcardSub(it))}</span></div>
    ${stale.length ? '<p class="qc-changed muted">The question changed, so your earlier ticks and note were cleared.</p>' : ''}
    ${qcardBodyHTML(it, q, {attr: 'qc', ticked, pic: o => qcardPicture(files, o)})}
    <div class="qc-actions">${qcardActionsHTML(it, q, {attr: 'qc', ticked})}</div>
    <textarea class="qc-note" rows="1" maxlength="4000" aria-label="Note" placeholder="Add a note">${esc(draft?.note || '')}</textarea>
    <div class="qc-foot">${qcardFallbackHTML(it)}${qcardDetailsHTML(it, q, {link: false})}</div>
  </section>`;
}
document.addEventListener('change', ev => {
  const box = ev.target.closest?.('.qcard[data-qcard]');
  if (!box || !ev.target.matches('[data-qc-tick]')) return;
  const found = qcardItem({id: box.dataset.qcard});
  if (!found) return;
  const ticked = new Set([...box.querySelectorAll('[data-qc-tick]:checked')].map(x => Number(x.dataset.qcTick)));
  qcardRetick(box, found.it, found.q, ticked, 'qc');
  qcardDraft(box);
});
document.addEventListener('input', ev => {
  const box = ev.target.closest?.('.qcard[data-qcard]');
  if (box && ev.target.matches('.qc-note')) qcardDraft(box);
});
document.addEventListener('click', async ev => {
  const b = ev.target.closest?.('.qcard[data-qcard] [data-qc]');
  if (!b || b.disabled) return;
  const box = b.closest('.qcard'), found = qcardItem({id: box.dataset.qcard});
  if (!found) return;
  const {it, q} = found;
  const ticked = new Set([...box.querySelectorAll('[data-qc-tick]:checked')].map(x => Number(x.dataset.qcTick)));
  const labels = qcardLabels(q, b.dataset.qc, Number(b.dataset.i), ticked);
  if (!labels.length) return;
  const note = box.querySelector('.qc-note')?.value.trim() || '';
  box.querySelectorAll('button, input, textarea').forEach(x => { x.disabled = true; });
  try {
    for (const w of qcardWrites(it, q, labels, note)) await post(w.path, w.body, newRequestId());
    QC_DRAFTS.delete(qcardDraftKey(it, q));
    toast(qcardSaid(it, q, labels));
    void v2Refresh();
  } catch (e) {
    box.querySelectorAll('button, input, textarea').forEach(x => { x.disabled = false; });
    toast(e.message, true);
  }
});
