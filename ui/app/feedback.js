/* ui/app/feedback.js — 👍 / 👎 on a bot's work: its messages (Assistant chat, Threads and DMs, task comments), tasks a bot
   asked for, and the approvals and questions it puts on Decide cards. 👍 is one tap (again removes it); 👎 saves at once
   and opens a sheet for what was wrong, to tell the bot or make a rule of it (backend/feedback.py).
   A message's row under it holds Copy, 👍 and 👎: with a pointer it shows on hover, and the chosen thumb stays there,
   filled; a chosen 👎 opens what happened to it. On touch a long press on the item opens them, with Copy.
   Every control stays hidden until the server answers the batched read with a list: an older server without these
   routes (404, or a fixture's empty object) never shows them.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// on: null until the first batched read answers, then whether the server has feedback at all.
// items: subject key ('message:<id>') -> every feedback row the viewer may see on it. asked: keys already read.
const FB = {on: null, items: new Map(), asked: new Set(), reasons: null, queued: false, press: null, quiet: 0};
const FB_PRESS_MS = 450;          // a long press, short of iOS's own text selection
const FB_FIXED = ["Shouldn't exist", 'Badly written', 'Wrong person'];
const FB_REASON_MAX = 1000;       // the API's cap on a reason and on a rule's text
const fbKey = (type, id) => `${type}:${id}`;
const fbSplit = key => { const i = String(key).indexOf(':'); return {type: key.slice(0, i), id: key.slice(i + 1)}; };
const fbMine = key => (FB.items.get(key) || []).find(f => f.person_id === myActor()) || null;
// People rate; a bot's own session never does (the server refuses it too).
const fbAllowed = () => S.me?.role !== 'bot' && !!myActor();
const fbBot = actor => String(actor || '').startsWith('bot:');
// What a task card or row rates: the task, when a bot asked for it.
const fbTaskKey = t => t?.id && fbBot(t.requester_id) ? fbKey('task', t.id) : '';
const fbNorm = s => String(s || '').trim().toLowerCase();

// ---- markup each surface puts in its rows
// The icon font's glyphs; a chosen one draws filled (feedback.css).
const FB_ICON = {up: '<span class="nav-icon fb-i" aria-hidden="true">thumb_up</span>', down: '<span class="nav-icon fb-i" aria-hidden="true">thumb_down</span>',
  copy: '<span class="nav-icon fb-i" aria-hidden="true">content_copy</span>'};
// `row`: a list row's roving focus keeps its own keys, so the pair takes no Tab stop there (the task window has one).
function fbButtonsHTML(key, {row = false, fixed = false} = {}) {
  if (!key) return '';
  const mine = fbMine(key)?.rating || '', tab = row ? ' tabindex="-1"' : '';
  return `<span class="fb-ctl" data-fb="${esc(key)}"${fixed ? ' data-fb-fixed' : ''}>`
    + `<button type="button" class="fb-btn" data-fb-rate="up" aria-pressed="${mine === 'up'}" aria-label="Good" title="Good"${tab}>${FB_ICON.up}</button>`
    + `<button type="button" class="fb-btn" data-fb-rate="down" aria-pressed="${mine === 'down'}" aria-label="Not good" title="Not good"${tab}>${FB_ICON.down}</button></span>`;
}
// A message's row under its words (Threads, task comments): Copy, 👍, 👎. The chat keeps its own Copy line (chatCopyHTML).
const fbRowHTML = (key, text) => key ? `<div class="fb-row">${text ? `<button type="button" class="fb-btn fb-copy" data-fb-copy="${esc(text)}" aria-label="Copy message" title="Copy message">${FB_ICON.copy}</button>` : ''}${fbButtonsHTML(key)}</div>` : '';
// A task row's rating after its title: only a glyph, inside the row's open button.
function fbMarkInner(key) {
  const r = fbMine(key)?.rating;
  return r ? `<span class="fb-mark-i" role="img" aria-label="${r === 'up' ? 'You said good' : 'You said not good'}">${r === 'up' ? FB_ICON.up : FB_ICON.down}</span>` : '';
}
const fbMarkHTML = key => key ? `<span class="fb-mark" data-fb-mark="${esc(key)}">${fbMarkInner(key)}</span>` : '';

// ---- the batched read: one request per subject type for everything drawn and not yet read
function fbSync() {
  if (FB.on === false || FB.queued || !fbAllowed()) return;
  FB.queued = true;
  requestAnimationFrame(() => { FB.queued = false; void fbLoad(); });
}
async function fbLoad() {
  const want = new Map();
  for (const el of document.querySelectorAll('[data-fb],[data-fb-mark],[data-fb-press]')) {
    const key = el.dataset.fb || el.dataset.fbMark || el.dataset.fbPress;
    if (!key || FB.asked.has(key)) continue;
    const {type, id} = fbSplit(key);
    if (!want.has(type)) want.set(type, []);
    want.get(type).push(id);
    FB.asked.add(key);
  }
  for (const [type, ids] of want) {
    for (let i = 0; i < ids.length; i += 200) {
      const part = ids.slice(i, i + 200);
      let j;
      try { j = await get(`/v2/feedback?subject_type=${encodeURIComponent(type)}&subject_ids=${part.map(encodeURIComponent).join(',')}`); }
      catch (e) {
        if (e.status === 404) { fbTurn(false); return; }
        for (const id of part) FB.asked.delete(fbKey(type, id));     // read again with the next drawing
        continue;
      }
      if (!Array.isArray(j?.items)) { fbTurn(false); return; }
      for (const id of part) FB.items.set(fbKey(type, id), []);
      for (const f of j.items) {
        const k = fbKey(f.subject_type, f.subject_id);
        FB.items.set(k, [...(FB.items.get(k) || []).filter(x => x.id !== f.id), f]);
      }
      fbTurn(true);
    }
  }
  fbPaint();
}
function fbTurn(on) {
  if (FB.on === on) return;
  FB.on = on;
  document.body.classList.toggle('fb-on', on);
}
// Marks and pressed states, in place: a surface's next drawing writes the same thing from FB.items.
function fbPaint() {
  for (const el of document.querySelectorAll('[data-fb]')) {
    const mine = fbMine(el.dataset.fb)?.rating || '';
    for (const b of el.querySelectorAll('[data-fb-rate]')) b.setAttribute('aria-pressed', String(b.dataset.fbRate === mine));
  }
  for (const el of document.querySelectorAll('[data-fb-mark]')) {
    const html = fbMarkInner(el.dataset.fbMark);
    if (el.innerHTML !== html) el.innerHTML = html;
  }
}
function fbStore(f) {
  const k = fbKey(f.subject_type, f.subject_id);
  FB.items.set(k, [...(FB.items.get(k) || []).filter(x => x.id !== f.id && x.person_id !== f.person_id), f]);
  fbPaint();
}
function fbForget(key, f) {
  FB.items.set(key, (FB.items.get(key) || []).filter(x => x.id !== f.id));
  fbPaint();
}

// ---- writes
async function fbSave(key, rating, {reason, tell = false} = {}) {
  const {type, id} = fbSplit(key);
  const body = {subject_type: type, subject_id: id, rating};
  if (reason !== undefined) body.reason = String(reason).slice(0, FB_REASON_MAX);
  if (tell) body.tell_bot = true;
  const f = await post('/v2/feedback', body);
  fbStore(f);
  return f;
}
async function fbRemove(key, f) {
  await writeRequest('DELETE', '/v2/feedback/' + encodeURIComponent(f.id));
  fbForget(key, f);
}
// 👍 is one tap; on a 👍 it takes it back.
async function fbUp(key) {
  const mine = fbMine(key);
  try {
    if (mine?.rating === 'up') await fbRemove(key, mine);
    else await fbSave(key, 'up');
  } catch (e) { toast(e.message, true); }
}
// 👎 is saved at once, so closing the sheet keeps it; the sheet adds why and what to do about it.
async function fbDown(key, opts = {}) {
  let f = fbMine(key);
  if (f?.rating !== 'down') {
    try { f = await fbSave(key, 'down'); } catch (e) { toast(e.message, true); return; }
  }
  fbSheet(key, f, opts);
}
async function fbReasons() {
  if (FB.reasons) return FB.reasons;
  try {
    const j = await get('/v2/feedback/recent-reasons');
    FB.reasons = (Array.isArray(j?.reasons) ? j.reasons : []).filter(r => r?.text).slice(0, 5);
  } catch (e) {
    if (e.status === 404) fbTurn(false);
    FB.reasons = [];
  }
  return FB.reasons;
}

// ---- the sheet: a bottom sheet on a phone, a small dialog with a pointer
function fbDialog(cls, label) {
  document.querySelector('dialog.fb-sheet')?.close();
  const d = document.createElement('dialog');
  d.className = 'fb-sheet ' + cls;
  d.setAttribute('aria-label', label);
  document.body.append(d);
  d.addEventListener('close', () => d.remove());
  // A tap on the backdrop (the dialog's own box outside its content) closes it.
  d.addEventListener('click', ev => {
    if (ev.target !== d) return;
    const r = d.getBoundingClientRect();
    if (ev.clientX < r.left || ev.clientX > r.right || ev.clientY < r.top || ev.clientY > r.bottom) d.close();
  });
  return d;
}
// No title: the close button alone, in the corner.
const FB_X = '<button type="button" class="ghost fb-x" data-fb-close aria-label="Close" title="Close">✕</button>';
const fbHeadHTML = title => title ? `<div class="fb-head"><h2 tabindex="-1">${esc(title)}</h2>${FB_X}</div>` : `<div class="fb-corner">${FB_X}</div>`;
// `fixed`: a task or a Decide card, whose chips start with the three ways a request goes wrong.
function fbSheet(key, f, {fixed = fbSplit(key).type !== 'message'} = {}) {
  const d = fbDialog('fb-down', 'Not good');
  const st = {key, f, chip: '', saved: String(f?.reason || '')};
  d.innerHTML = `${fbHeadHTML('')}<div class="fb-chips" role="group" aria-label="Reasons"></div>
    <textarea class="fb-why" rows="3" maxlength="${FB_REASON_MAX}" aria-label="What was wrong?" placeholder="What was wrong?">${esc(st.saved)}</textarea>
    <p class="fb-nudge" hidden>You've said this before.</p>
    <div class="fb-acts"><button type="button" class="ghost" data-fb-rule>Make a rule</button><button type="button" class="primary" data-fb-tell>Tell the bot</button></div>`;
  d.showModal();
  const box = $('.fb-why', d), chips = $('.fb-chips', d), nudge = $('.fb-nudge', d);
  const rule = $('[data-fb-rule]', d), tell = $('[data-fb-tell]', d);
  let reasons = [];
  const counts = () => new Map(reasons.map(r => [fbNorm(r.text), Number(r.count) || 0]));
  // Said twice before: one line says so and a rule becomes the main way out.
  const renudge = () => {
    const again = (counts().get(fbNorm(st.chip || box.value)) || 0) >= 2;
    nudge.hidden = !again;
    rule.className = again ? 'primary' : 'ghost'; tell.className = again ? 'ghost' : 'primary';
  };
  const drawChips = () => {
    const seen = new Set(), list = [];
    for (const text of [...(fixed ? FB_FIXED : []), ...reasons.map(r => r.text)]) {
      const n = fbNorm(text);
      if (!n || seen.has(n)) continue;
      seen.add(n); list.push(text);
    }
    chips.innerHTML = list.map(t => `<button type="button" class="fb-chip" data-fb-chip="${esc(t)}" aria-pressed="${fbNorm(t) === fbNorm(box.value)}">${esc(t)}</button>`).join('');
    chips.hidden = !list.length;
  };
  drawChips();
  void fbReasons().then(r => { reasons = r; if (d.open) { drawChips(); renudge(); } });
  chips.onclick = ev => {
    const c = ev.target.closest('[data-fb-chip]');
    if (!c) return;
    st.chip = c.dataset.fbChip; box.value = st.chip;
    for (const b of chips.querySelectorAll('[data-fb-chip]')) b.setAttribute('aria-pressed', String(b === c));
    renudge(); box.focus();
  };
  box.oninput = () => {
    if (fbNorm(box.value) !== fbNorm(st.chip)) st.chip = '';
    for (const b of chips.querySelectorAll('[data-fb-chip]')) b.setAttribute('aria-pressed', String(fbNorm(b.dataset.fbChip) === fbNorm(box.value)));
    renudge();
  };
  const busy = on => { for (const b of d.querySelectorAll('.fb-acts button')) b.disabled = on; };
  // The reason as typed, saved when it changed; the row the rule or note hangs on.
  const settle = async (extra = {}) => {
    const reason = box.value.trim();
    if (!extra.tell && reason === st.saved.trim() && st.f) return st.f;
    st.f = await fbSave(key, 'down', {reason, ...extra});
    st.saved = reason;
    FB.reasons = null;               // the new reason is among the recent ones next time
    return st.f;
  };
  tell.onclick = async () => {
    busy(true);
    try { await settle({tell: true}); st.closing = true; d.close(); toast('Sent to bot'); }
    catch (e) { toast(e.message, true, null, {host: d}); busy(false); }
  };
  rule.onclick = async () => {
    busy(true); rule.textContent = 'Drafting…';
    try {
      const saved = await settle();
      const draft = await post('/v2/feedback/' + encodeURIComponent(saved.id) + '/rule-draft', {});
      st.closing = true; d.close();
      fbRuleSheet(key, saved, String(draft?.text || box.value.trim()));
    } catch (e) { toast(e.message, true, null, {host: d}); busy(false); rule.textContent = 'Make a rule'; }
  };
  $('[data-fb-close]', d).onclick = () => d.close();
  // Closed any other way, a reason typed is still kept with the 👎.
  d.addEventListener('close', () => {
    if (st.closing || box.value.trim() === st.saved.trim()) return;
    fbSave(key, 'down', {reason: box.value.trim()}).then(() => { FB.reasons = null; }, e => toast(e.message, true));
  });
  box.focus();
}
// The rule as drafted, to change before it is saved; All bots widens Who from this bot to every bot.
function fbRuleSheet(key, f, text) {
  const d = fbDialog('fb-rule', 'Make a rule');
  d.innerHTML = `${fbHeadHTML('Make a rule')}
    <textarea class="fb-rule-text" rows="3" maxlength="${FB_REASON_MAX}" aria-label="Rule">${esc(text.slice(0, FB_REASON_MAX))}</textarea>
    <label class="fb-all"><input type="checkbox" role="switch" class="people-switch" data-fb-all> All bots</label>
    <div class="fb-acts"><button type="button" class="ghost" data-fb-close>Cancel</button><button type="button" class="primary" data-fb-save>Save rule</button></div>`;
  d.showModal();
  const box = $('.fb-rule-text', d), save = $('[data-fb-save]', d);
  for (const b of d.querySelectorAll('[data-fb-close]')) b.onclick = () => d.close();
  box.oninput = () => { save.disabled = !box.value.trim(); };
  save.onclick = async () => {
    save.disabled = true;
    try {
      const j = await post('/v2/feedback/' + encodeURIComponent(f.id) + '/rule', {text: box.value.trim(), all_bots: $('[data-fb-all]', d).checked});
      if (j?.feedback) fbStore(j.feedback);
      d.close();
      const status = j?.feedback?.rule_status || j?.rule?.status || '';
      toast(status === 'active' ? 'Rule added' : 'Rule suggested');
    } catch (e) { toast(e.message, true, null, {host: d}); save.disabled = false; }
  };
  box.focus(); box.setSelectionRange(box.value.length, box.value.length);
}
// A 👎 mark: why, what came of it, and a way to take it back.
function fbOutcome(f) {
  if (f.outcome === 'rule' && f.org_rule_id) {
    const href = typeof ruHref === 'function' ? ruHref(f.rule_status === 'suggested' ? 'suggested' : 'internal', {rule: f.org_rule_id}) : '#/rules';
    return `<a href="${esc(href)}" data-fb-close>Rule</a>`;
  }
  if (f.outcome === 'told_bot' || f.note_id) {
    if (['settled', 'carried'].includes(f.note_status)) return 'Bot has read it';
    if (f.note_status === 'waiting') return 'Sent to bot';
  }
  return '';
}
function fbShow(key) {
  const f = fbMine(key);
  if (!f) return;
  const d = fbDialog('fb-show', 'Not good');
  const out = fbOutcome(f);
  d.innerHTML = `${fbHeadHTML('')}<p class="fb-reason${f.reason ? '' : ' muted'}" tabindex="-1">${esc(f.reason || 'No reason')}</p>
    ${out ? `<p class="fb-out muted">${out}</p>` : ''}
    <div class="fb-acts"><button type="button" class="ghost" data-fb-remove>Remove</button><button type="button" class="primary" data-fb-edit>Edit</button></div>`;
  d.showModal();
  for (const b of d.querySelectorAll('[data-fb-close]')) b.addEventListener('click', () => d.close());
  $('[data-fb-edit]', d).onclick = () => { d.close(); fbSheet(key, f); };
  $('[data-fb-remove]', d).onclick = async ev => {
    ev.currentTarget.disabled = true;
    try { await fbRemove(key, f); d.close(); }
    catch (e) { toast(e.message, true, null, {host: d}); ev.currentTarget.disabled = false; }
  };
  $('.fb-reason', d).focus();
}
// A long press: 👍, 👎 and Copy, as one row of large buttons.
function fbMenu(key, copy, opts = {}) {
  const d = fbDialog('fb-menu', 'Message actions');
  const mine = fbMine(key)?.rating || '';
  d.innerHTML = `<div class="fb-menu-row">
    <button type="button" data-fb-pick="up" aria-pressed="${mine === 'up'}" aria-label="Good">${FB_ICON.up}</button>
    <button type="button" data-fb-pick="down" aria-pressed="${mine === 'down'}" aria-label="Not good">${FB_ICON.down}</button>
    ${copy ? `<button type="button" data-fb-pick="copy" aria-label="Copy">${FB_ICON.copy}</button>` : ''}</div>`;
  d.showModal();
  d.onclick = ev => {
    const b = ev.target.closest('[data-fb-pick]');
    if (!b) return;
    d.close();
    if (b.dataset.fbPick === 'up') void fbUp(key);
    else if (b.dataset.fbPick === 'down') { if (mine === 'down') fbShow(key); else void fbDown(key, opts); }
    else void copyText(copy).then(() => toast('Message copied'), () => toast('Could not copy the message', true));
  };
  $('[data-fb-pick]', d)?.focus();
}

// ---- clicks, before each surface's own: a thumb never opens the row or card it sits on
const fbOpts = el => el.closest('[data-fb-fixed]') ? {fixed: true} : {};
document.addEventListener('click', ev => {
  // The long press's own click, just after the lift; with the menu open the page is inert, so it lands on the menu.
  if (FB.quiet && Date.now() < FB.quiet) { ev.preventDefault(); ev.stopPropagation(); FB.quiet = 0; return; }
  const rate = ev.target.closest('[data-fb-rate]'), copy = ev.target.closest('[data-fb-copy]');
  if (!rate && !copy) return;
  ev.preventDefault(); ev.stopPropagation();
  if (copy) { void copyText(copy.dataset.fbCopy).then(() => toast('Message copied'), () => toast('Could not copy the message', true)); return; }
  const key = rate.closest('[data-fb]')?.dataset.fb;
  if (!key) return;
  if (rate.dataset.fbRate === 'up') void fbUp(key);
  else if (fbMine(key)?.rating === 'down') fbShow(key);
  else void fbDown(key, fbOpts(rate));
}, true);

// ---- the long press, on touch only: a move or a scroll is not a press
function fbPressEnd() { if (FB.press) { clearTimeout(FB.press.timer); FB.press = null; } }
document.addEventListener('pointerdown', ev => {
  if (ev.pointerType !== 'touch' || !FB.on || !ev.isPrimary) return;
  const el = ev.target.closest('[data-fb-press]');
  // A control keeps its own touch, except a task row's title, which is a button over the whole row: a long press there
  // is the row's, and the quiet click after it keeps the task from opening too.
  const own = ev.target.closest('a, button, input, textarea, select, summary, [contenteditable]');
  if (!el || (own && !own.matches('.tl-open'))) return;
  fbPressEnd();
  FB.press = {el, x: ev.clientX, y: ev.clientY, timer: setTimeout(() => {
    const key = el.dataset.fbPress, copy = el.querySelector('[data-chat-copy],[data-fb-copy]');
    FB.press = null;
    FB.quiet = Date.now() + 60000;   // until the finger lifts (fbPressUp), however long it is held
    getSelection?.()?.removeAllRanges();
    navigator.vibrate?.(8);
    fbMenu(key, copy?.dataset.chatCopy || copy?.dataset.fbCopy || '', fbOpts(el));
  }, FB_PRESS_MS)};
}, true);
document.addEventListener('pointermove', ev => {
  if (FB.press && Math.hypot(ev.clientX - FB.press.x, ev.clientY - FB.press.y) > 10) fbPressEnd();
}, true);
// The press's own click comes just after the lift; a click later than that is a new tap.
function fbPressUp() {
  fbPressEnd();
  if (FB.quiet > Date.now() + 1000) FB.quiet = Date.now() + 400;
}
for (const type of ['pointerup', 'pointercancel']) document.addEventListener(type, fbPressUp, true);
document.addEventListener('scroll', fbPressEnd, {capture: true, passive: true});
document.addEventListener('contextmenu', ev => { if ((FB.press || Date.now() < FB.quiet) && ev.target.closest?.('[data-fb-press]')) ev.preventDefault(); }, true);
