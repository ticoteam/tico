/* ui/app/growth-start.ts — Growth by chat (docs/growth.md "Getting started"): one row under the Growth header with the
   Growth Manager, Chat and Call. Growth gets its numbers by asking people, not by forms: Chat opens the viewer's chat
   with the Growth Manager and, while Growth is not set up (GET /api/v2/growth/setup, backend/growth_setup.py), asks it
   to set Growth up; its growth-interview skill asks whoever knows (founder, sales, finance, marketing, support), one
   question at a time, and writes each answer as it comes. Call calls the Growth Manager by voice (callBot,
   ui/app/thread-call.ts). Set up, the row is just its marks.
   With no Growth Manager on the team there is no row. Empty Growth cards say what fills them, in plain words.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The template slug the default org gives Growth's manager (templates/catalog/growth-manager).
const GRS_BOT = 'growth-manager';
const GRS_ASK = 'Help me set up Growth.';
// The row's state: the page it is on and whether Growth is set up (null until read).
let GRS: {state: GrowthState; done: boolean | null; seq: number} | null = null;

const grsBot = () => { const b = botByRef(GRS_BOT); return b && b.status !== 'archived' ? b : null; };

function grsRowHTML(done: boolean | null) {
  const bot = grsBot();
  if (!bot) return '';
  const name = botDisplayName(bot.id);
  return `${botAvatar(bot, 28)}<div class="grs-b"><b class="grs-t">${esc(name)}</b>${done ? '' : '<span class="grs-l">Tell me how you grow</span>'}</div>
    <span class="spacer"></span>
    <button type="button" class="${done ? 'ghost' : 'primary'} grs-go" data-grs-chat>Chat</button>
    <button type="button" class="ghost grs-call" data-grs-call aria-label="Call ${esc(name)}" title="Call">${CALL_SVG}</button>`;
}

// The row goes just under the head, over the cards; it reads whether Growth is set up once now and after every save.
function grStartMount(state: GrowthState, root: HTMLElement) {
  if (S.me?.role === 'bot' || !grsBot()) return;
  const head = root.querySelector('.dash-head');
  if (!head) return;   // G: the dashboard always draws its head
  const row = document.createElement('section');
  row.className = 'grs'; row.id = 'gr-start';
  row.setAttribute('aria-label', 'Growth Manager');
  row.innerHTML = grsRowHTML(null);
  head.after(row);
  GRS = {state, done: null, seq: 0};
  row.addEventListener('click', ev => {
    const t = ev.target instanceof Element ? ev.target : null;
    if (t?.closest('[data-grs-chat]')) void grsChat();
    else if (t?.closest('[data-grs-call]')) void grsCall();
  });
  void grStartRead(state);
}

async function grStartRead(state: GrowthState) {
  if (!GRS || GRS.state !== state) return;
  const seq = ++GRS.seq;
  let done: boolean;
  try { done = !!(await get<{done?: boolean}>('/v2/growth/setup', {fresh: true})).done; }
  catch { return; }   // the row stays as drawn
  if (!GRS || GRS.state !== state || GRS.seq !== seq || GR !== state) return;
  GRS.done = done;
  const row = document.getElementById('gr-start');
  if (row) { row.classList.toggle('grs-won', done); row.innerHTML = grsRowHTML(done); }
}

// Chat: the Growth Manager's chat; while Growth is not set up, the first message asks it to set Growth up.
async function grsChat() {
  const bot = grsBot();
  if (!bot) return;
  if (GRS?.done === false) {
    try { await post(`/v2/chat/${encodeURIComponent(GRS_BOT)}`, {text: GRS_ASK}); }
    catch (e) { toast(errorMessage(e, 'Not sent'), true); return; }
  }
  location.hash = '#/bot/' + encodeURIComponent(bot.id);
}

// Call: a voice call with the Growth Manager (callBot, ui/app/thread-call.ts).
async function grsCall() {
  await callBot(GRS_BOT);
}

// ----------------------------------------------------------------- empty cards
// What fills each empty Growth card (DASH_NONE, ui/app/dashboard.ts), in plain words. The Funnel is Goals'.
const grsNone = (line: string) => `<p class="muted dash-none grs-fill">${esc(line)}</p>`;
const GRS_KEPT = ['kept_d30', 'kept_d90', 'kept_d180', 'ltv', 'ltv_cac', 'payback_months'];
Object.assign(DASH_NONE, {
  growth_number: (card: ApiCard) => grsNone(GRS_KEPT.includes(String(card.settings?.metric)) ? 'Fills once a month has customers kept 30 days.' : 'Fills from a channel’s months.'),
  growth_channels: () => grsNone('Fills when a channel is live.'),
  growth_retention: () => grsNone('Fills once a month has customers kept 30 days.'),
  growth_funnel: () => `<p class="muted dash-none grs-fill" data-grs-fill="goals">Comes from your growth KPIs on Goals. <a href="${GR_GOALS}">Open Goals</a></p>`,
  growth_board: () => `<p class="muted dash-none grs-fill" data-grs-fill="board">Fills when someone files a growth idea. ${grBoardLink('', 'Open the Growth board')}</p>`,
});
