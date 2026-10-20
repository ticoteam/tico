/* After the wizard: the tour, and one line under the org list while there are no bots of your own
   (docs/onboarding.md). Whether that line shows comes from GET /api/v2/setup/getting-started; the only thing
   kept per person is whether they saw the tour. The Market page's empty state (ui/market-page.js) is
   where the market is asked for. */
'use strict';
let GS = null;                     // the last answer, or null when the server has none to give
let GS_LOAD = 0;

async function gsRefresh() {
  if (!S.me?.cloud) return null;
  const seq = ++GS_LOAD;
  const data = await v2Get('/v2/setup/getting-started');
  if (seq !== GS_LOAD) return GS;
  GS = data && Array.isArray(data.items) ? data : null;
  gsOrgHint();
  return GS;
}

async function gsState(change) {
  try {
    const next = await post('/v2/setup/getting-started/state', change);
    if (GS && next) GS = {...GS, tour_seen: next.tour};
  } catch { /* a choice that did not save is asked again next time */ }
}

// "Talk to BotOps to add or edit your bots", for whoever may add bots, until there is one of your own.
function gsOrgHint() {
  const line = $('#gs-org-hint');
  if (!line) return;
  const bot = GS?.items.find(item => item.id === 'first_bot');
  line.hidden = !(bot && !bot.done && GS.can_build);
}

// ---------------------------------------------------------------- the tour
const GS_STEPS = [
  ['[data-nav="updates"]', 'Updates', 'Every bot posts a short update each day, and a fuller one on Fridays.'],
  ['[data-nav="tasks"]', 'Tasks', 'Work for bots and humans. Give a task an owner and it gets done, or comes back with a question.'],
  ['#nav-organisation', 'Team', 'Your team is listed here. Open a bot to chat, see its work and change its settings.'],
  ['[data-nav="docs"]', 'Docs', 'Your docs, searchable, with questions answered from them.'],
  ['[data-nav="market"]', 'Market', 'A map of your competitors, customers and channels that a bot keeps current.'],
  ['[data-nav="meetings"]', 'Meetings', 'Connect a source or add notes, and bots pull out the tasks and follow-ups.'],
];
let GS_TOUR = null;

function gsTourStart() {
  if (GS_TOUR) return;
  const phone = drawerMedia.matches;
  if (phone) setDrawer(true, false, $('#mobile-more'));
  const steps = GS_STEPS.filter(([selector]) => {
    const el = $('#side ' + selector);
    return el && el.getClientRects().length;
  });
  if (!steps.length) { if (phone) setDrawer(false); return; }
  const root = document.createElement('div');
  root.className = 'gs-tour';
  root.setAttribute('role', 'dialog');
  root.setAttribute('aria-modal', 'true');
  root.setAttribute('aria-labelledby', 'gs-tour-title');
  root.innerHTML = `<div class="gs-tour-hole"></div><div class="gs-tour-card">
    <p class="gs-tour-count muted"></p><h2 id="gs-tour-title"></h2><p class="gs-tour-text"></p>
    <div class="gs-tour-actions"><button class="ghost" type="button" data-tour-skip>Skip</button>
      <button class="primary" type="button" data-tour-next>Next</button></div></div>`;
  document.body.appendChild(root);
  const opener = document.activeElement;
  GS_TOUR = {root, steps, at: 0, phone, opener};
  const onKey = event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); gsTourEnd(); }
    else if (event.key === 'Tab') {
      const buttons = [...root.querySelectorAll('button')];
      const i = buttons.indexOf(document.activeElement);
      event.preventDefault();
      buttons[(i + (event.shiftKey ? buttons.length - 1 : 1)) % buttons.length].focus();
    }
  };
  const onFocus = event => { if (!root.contains(event.target)) root.querySelector('[data-tour-next]').focus(); };
  document.addEventListener('keydown', onKey, true);
  document.addEventListener('focusin', onFocus);
  window.addEventListener('resize', gsTourPlace);
  GS_TOUR.off = () => {
    document.removeEventListener('keydown', onKey, true);
    document.removeEventListener('focusin', onFocus);
    window.removeEventListener('resize', gsTourPlace);
  };
  root.querySelector('[data-tour-skip]').onclick = () => gsTourEnd();
  root.querySelector('[data-tour-next]').onclick = () => {
    if (GS_TOUR.at === steps.length - 1) gsTourEnd(); else { GS_TOUR.at++; gsTourShow(); }
  };
  gsTourShow();
  root.querySelector('[data-tour-next]').focus();
}

function gsTourShow() {
  const {root, steps, at} = GS_TOUR;
  const [selector, title, text] = steps[at];
  root.querySelector('.gs-tour-count').textContent = `${at + 1} of ${steps.length}`;
  root.querySelector('#gs-tour-title').textContent = title;
  root.querySelector('.gs-tour-text').textContent = text;
  root.querySelector('[data-tour-next]').textContent = at === steps.length - 1 ? 'Done' : 'Next';
  $('#side ' + selector)?.scrollIntoView({block: 'nearest'});
  gsTourPlace();
}

function gsTourPlace() {
  if (!GS_TOUR) return;
  const {root, steps, at} = GS_TOUR;
  const target = $('#side ' + steps[at][0]);
  if (!target) return;
  const box = target.getBoundingClientRect(), pad = 4;
  const hole = root.querySelector('.gs-tour-hole'), card = root.querySelector('.gs-tour-card');
  Object.assign(hole.style, {left: box.left - pad + 'px', top: box.top - pad + 'px',
    width: box.width + pad * 2 + 'px', height: Math.min(box.height, innerHeight - box.top) + pad * 2 + 'px'});
  const width = card.offsetWidth, height = card.offsetHeight;
  if (GS_TOUR.phone) {
    // The list is in the drawer, so the card takes whichever end of the screen the row is not at.
    const low = box.top + box.height / 2 > innerHeight / 2;
    Object.assign(card.style, {left: '12px', right: '12px', width: 'auto', top: low ? '12px' : 'auto', bottom: low ? 'auto' : '12px'});
    return;
  }
  Object.assign(card.style, {right: 'auto', bottom: 'auto', width: '',
    left: Math.min(box.right + 16, innerWidth - width - 12) + 'px',
    top: Math.max(12, Math.min(box.top, innerHeight - height - 12)) + 'px'});
}

function gsTourEnd() {
  if (!GS_TOUR) return;
  const {root, phone, opener} = GS_TOUR;
  GS_TOUR.off();
  root.remove();
  GS_TOUR = null;
  if (phone) setDrawer(false, true);
  // Started from the account menu, which closed as the tour took focus: back to the button that opens it.
  else if (opener?.closest?.('#account-menu')) $('#account').focus();
  else if (opener && opener.isConnected) opener.focus();
  void gsState({tour: true});
}

window.gsStartTour = gsTourStart;
window.gsBoot = function () {
  // The first look around, once per person, never over the wizard: the owner who just created the team has Show me
  // around on that screen (ui/app/welcome.js), else gets it on the next visit, like someone who joins later.
  void gsRefresh().then(seen => {
    if (seen && !seen.tour_seen && !S.config?.onboarding_needed && S.route !== '#/welcome') gsTourStart();
  });
  setInterval(() => { if (!document.hidden && S.me?.cloud) void gsRefresh(); }, 60000);
};

// ---------------------------------------------------------------- one listener for all of it
document.addEventListener('click', event => {
  const t = event.target;
  if (t.closest('[data-gs-tour]')) { gsTourStart(); return; }
  const link = t.closest('[data-gs-tab]');
  if (link) {
    event.preventDefault();
    SETTINGS_TAB = link.dataset.gsTab;
    if (location.hash === link.getAttribute('href')) route(); else location.hash = link.getAttribute('href');
  }
});
