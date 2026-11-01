/* ui/app/org-rule-holds.js — What org rule checks show (backend/org_rule_check.py, docs/rules-plan.md §3, board Held):
   on a review task (tagged rule-review) each held output with its rule and the reviewer's three actions, Keep blocked,
   Send anyway and Edit rule; the one-line note a person sees in a chat when a bot's reply was held; and the checks line
   in the Rules page header ("Checks on · 12 today · 0 unchecked · $48 left"). GET /api/v2/rule-holds?task=<id> lists a
   task's holds a page at a time, as summaries; each card's text (and the reviewer's nonce) is read from
   GET /api/v2/rule-holds/<hold>. Both answer only the reviewer and the author bot.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const RH_LABEL = 'rule-review';
const RH_OUTCOME = {blocked: ['Block', 'rh-blk'], escalated: ['Escalate', 'rh-esc']};

// ----------------------------------------------------------------- a review task's holds
// The section moves into each redraw of the task, as its pages do (taskPagesAdopt), so a redraw reads nothing again.
function ruleHoldsAdopt(d, task) {
  const slot = $('[data-rule-holds]', d);
  if (!slot) return;
  const id = String(task.id);
  if (!(task.labels || []).includes(RH_LABEL)) { slot.hidden = true; return; }
  if (d.rhEl && d.rhEl.dataset.task === id) { slot.replaceWith(d.rhEl); return; }
  d.rhEl = slot; slot.dataset.task = id;
  void ruleHoldsLoad(slot, id);
}
// One page of summaries, then each hold opened for its text. `cursor` continues the list below the cards drawn.
async function ruleHoldsLoad(slot, id, cursor = '') {
  let page = {holds: []};
  try {
    page = await get('/v2/rule-holds?task=' + encodeURIComponent(id) + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''),
      {fresh: true});
  } catch { page = {holds: []}; }
  const holds = await Promise.all((page.holds || []).map(h =>
    get('/v2/rule-holds/' + encodeURIComponent(h.id), {fresh: true}).catch(() => h)));
  if (slot.dataset.task !== id) return;
  $('.rh-more', slot)?.remove();
  if (!cursor) slot.innerHTML = '';
  slot.insertAdjacentHTML('beforeend', holds.map(ruleHoldHTML).join('') +
    (page.next_cursor ? `<button class="ghost small rh-more" type="button">Show more</button>` : ''));
  slot.hidden = !slot.querySelector('[data-hold]');
  ruleHoldsWire(slot, id);
  $('.rh-more', slot)?.addEventListener('click', ev => {
    ev.currentTarget.disabled = true;
    void ruleHoldsLoad(slot, id, page.next_cursor);
  }, {once: true});
}
// A decided card is drawn again from its own hold: the cards around it keep their place.
async function ruleHoldRedraw(slot, id, card) {
  const h = await get('/v2/rule-holds/' + encodeURIComponent(card.dataset.hold), {fresh: true});
  if (slot.dataset.task !== id) return;
  card.outerHTML = ruleHoldHTML(h);
  ruleHoldsWire(slot, id);
}
function ruleHoldHTML(h) {
  const [word, cls] = RH_OUTCOME[h.outcome] || RH_OUTCOME.blocked;
  const rules = (h.rules || []).map(r => `<div class="rh-rule">${esc(r.text)}</div>`).join('');
  const reviewer = !!h.nonce || h.can_send;
  const decided = h.state === 'sent' ? `Sent anyway${h.decided_by ? ' by ' + esc(actorLabel(h.decided_by)) : ''}`
    : h.state === 'kept' ? `Kept blocked${h.decided_by ? ' by ' + esc(actorLabel(h.decided_by)) : ''}` : '';
  const rule = h.rules?.[0];
  return `<article class="rh-card" data-hold="${esc(h.id)}">
    <header><span class="rh-pill ${cls}">${word}</span><span class="rh-to muted">${esc(h.author_name || actorLabel(h.author))}${
      (h.to || []).length ? ' → ' + esc(h.to.join(', ')) : ''}</span></header>
    <div><div class="rh-lbl">Tried to send</div><div class="rh-msg">${esc(h.text || '')}${h.text_cut ? '…' : ''}</div></div>
    <div><div class="rh-lbl">Rule</div>${rules || '<div class="rh-rule muted">A rule that no longer exists</div>'}</div>
    ${h.why ? `<div><div class="rh-lbl">Appeal</div><div class="rh-why">${esc(h.why)}</div></div>` : ''}
    ${decided ? `<p class="muted rh-done">${decided}</p>` : reviewer ? `<div class="rh-acts">
      ${rule ? `<a class="ghost small rh-btn" href="${esc(ruHref(rule.kind, {rule: rule.id}))}">Edit rule</a>` : ''}
      ${h.can_send ? `<button class="ghost small" type="button" data-hold-send="${esc(h.nonce || '')}">Send anyway</button>` : ''}
      <span class="spacer"></span><button class="primary small" type="button" data-hold-keep>Keep blocked</button></div>` : ''}
    <p class="rh-err" role="status" hidden></p>
  </article>`;
}
function ruleHoldsWire(slot, id) {
  slot.querySelectorAll('[data-hold]:not([data-wired])').forEach(card => {
    card.dataset.wired = '1';
    const hid = card.dataset.hold, err = $('.rh-err', card);
    const act = async (path, body, button) => {
      card.querySelectorAll('button').forEach(b => { b.disabled = true; });
      err.hidden = true;
      try {
        await post('/v2/rule-holds/' + encodeURIComponent(hid) + '/' + path, body);
        await ruleHoldRedraw(slot, id, card);
      } catch (e) {
        err.textContent = e.message; err.hidden = false;
        card.querySelectorAll('button').forEach(b => { b.disabled = false; });
        button?.focus();
      }
    };
    $('[data-hold-keep]', card)?.addEventListener('click', ev => act('keep', {}, ev.currentTarget));
    $('[data-hold-send]', card)?.addEventListener('click', ev => act('send', {nonce: ev.currentTarget.dataset.holdSend}, ev.currentTarget));
  });
}

// ----------------------------------------------------------------- a held reply in a chat
// The keeper's one line where a bot's reply would have been: the rule, never the text (execution.complete).
function ruleHeldNoteHTML(m) {
  return `<div class="chat-system rh-note" data-message="${esc(m.id || '')}"><span class="rh-note-dot" aria-hidden="true"></span>${
    esc(plainActors(m.body))}<div class="chat-meta"><time class="chat-time" title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</time></div></div>`;
}

// ----------------------------------------------------------------- the Rules page header
// "Checks on · N today · N unchecked · $N left", or "Checks off". Mounted into the Rules page's header (ui/app/org-rules.js)
// whenever that page is drawn; a person who may not read the stats sees nothing.
function ruleChecksLineText(s) {
  if (!s) return '';
  if (!s.on) return 'Checks off';
  const parts = ['Checks on', `${s.checks_today} today`, `${s.unchecked_today} unchecked`];
  if (s.usd_left != null) parts.push(`$${Math.round(s.usd_left)} left`);
  return parts.join(' · ');
}
// Called by pageRules once it has drawn its header.
async function ruleChecksMount(head) {
  if (!head || head.querySelector('.ru-checks')) return;
  const line = document.createElement('span');
  line.className = 'ru-checks muted tnum';
  head.querySelector('h1')?.after(line);
  try {
    const stats = await get('/v2/rules/stats', {fresh: true});
    line.textContent = ruleChecksLineText(stats);
    line.classList.toggle('warn', !!stats.on && stats.unchecked_today > 0 && stats.unchecked_today >= stats.checks_today / 4);
  } catch { line.remove(); }
}
