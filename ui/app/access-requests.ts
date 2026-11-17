/* ui/app/access-requests.ts — A bot's access request as a card in a thread (backend/access_requests.py): who asks for
   what, Grant or Decline, and how it ended for every copy. Classic script: its globals are shared with the other files
   under ui/app/, loaded in the order index.html lists them.
   A page part (ui/parts.json): a thread that shows a request loads it, the first time its list is drawn (thread-view.ts
   thvListDraw calls arMount), so a cold open of Home does not download it (P05b). thread-view.ts draws only the card's
   place, a host with data-ar-host. */
'use strict';

interface ArCard { id: string; state: string; subject_name: string; resource_name: string; level: string; why: string; auto: boolean; by: string }
const AR_LEVELS: Record<string, string> = {read: 'Read', read_write: 'Read and write'};
const arCard = (m: ThvMessage): ArCard | null => (m.refs?.card === 'access_request' && m.refs.access_request ? m.refs.access_request as ArCard : null);
// The markup each host last drew: a redraw that changes nothing keeps the card, a button a tap left disabled included.
const AR_DRAWN = new WeakMap<HTMLElement, string>();

function arCardHTML(m: ThvMessage): string {
  const card = arCard(m);
  if (!card) return '';
  const what = `<b>${esc(card.subject_name)}</b> → ${esc(card.resource_name)} · ${esc(AR_LEVELS[card.level] || card.level)}`;
  const mark: Record<string, string> = {granted: '✓', declined: '✕', revoked: '↺', withdrawn: '–'};
  const tail = card.state === 'open'
    ? `<div class="kg-acts"><button type="button" class="primary small" data-ar-act="grant">Grant</button><button type="button" class="ghost small" data-ar-act="decline">Decline</button></div>`
    : `<div class="kg-result ${card.state === 'granted' ? 'kg-ok' : 'kg-bad'}"><span aria-hidden="true">${mark[card.state] || ''}</span>${card.by ? ` ${esc(card.by)}` : ''}${
      card.state === 'granted' && card.auto ? ` <button type="button" class="ghost small" data-ar-act="take-back">Take back</button>` : ''}</div>`;
  return `<div class="kg-card" data-ar="${esc(m.id)}" role="group" aria-label="Access request"><div class="kg-row">${what}</div>${
    card.why ? `<div class="kg-replaces">${esc(card.why)}</div>` : ''}${tail}<p class="kg-err" role="alert" hidden></p></div>`;
}

// Draws the card into each access-request host of a thread's list. A host whose request is gone draws nothing.
function arMount(view: ThvView, list: ParentNode): void {
  for (const host of list.querySelectorAll<HTMLElement>('[data-ar-host]')) {
    const m = view.messages.find(x => x.id === host.dataset.arHost);
    const html = m ? arCardHTML(m) : '';
    if (AR_DRAWN.get(host) === html) continue;
    AR_DRAWN.set(host, html);
    host.innerHTML = html;
    host.onclick = ev => arAct(view, host, ev);
  }
}

// A press on a card's Grant, Decline or Take back. The first tap decides, and every copy then shows how it ended. A
// grant made at once carries Take back for the person told of it.
function arAct(view: ThvView, host: HTMLElement, ev: Event): void {
  const button = eventElement(ev)?.closest<HTMLButtonElement>('[data-ar-act]');
  const m = view.messages.find(x => x.id === host.dataset.arHost), card = m && arCard(m);
  if (!button || !m || !card) return;
  button.disabled = true;
  void post('/v2/access-requests/' + encodeURIComponent(card.id) + '/' + (button.dataset.arAct || 'grant'), {}).then((done: ArCard) => {
    m.refs = {...m.refs, access_request: {...card, state: done.state, by: done.by, auto: done.auto}};
    thvDraw(view);
  }).catch((e: unknown) => {
    button.disabled = false;
    const err = view.el.querySelector<HTMLElement>(`[data-ar="${CSS.escape(m.id)}"] .kg-err`);
    if (err) { err.textContent = errorMessage(e, 'Could not change it'); err.hidden = false; }
  });
}
