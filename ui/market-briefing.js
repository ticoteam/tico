/* The weekly market briefing on the Market page: one line under the Strategy tabs with the week, the title, how many
   items it holds and the bets it touches, and Read, which opens the Librarian's page in a side panel
   (backend/market_briefing.py, docs/market-briefing.md). With no briefing there is nothing on the page. */
'use strict';
const briefingStyle = document.createElement('style');
briefingStyle.textContent = `.brief-card{flex:none;display:flex;align-items:center;flex-wrap:wrap;gap:6px 10px;padding:8px 28px;border-bottom:1px solid var(--line);background:var(--surface);color:var(--ink);font-size:var(--t-sm)}
.brief-card .brief-week{color:var(--muted);white-space:nowrap}
.brief-card .brief-title{font-weight:600;min-width:0;overflow-wrap:anywhere}
.brief-card .brief-count{color:var(--muted);white-space:nowrap}
.brief-card .brief-bets{display:flex;flex-wrap:wrap;gap:4px}
.brief-card .brief-bets a{text-decoration:none;color:var(--ink)}
.brief-card .brief-read{margin-left:auto}
.brief-panel .brief-touch{margin:0 0 10px;padding:0;list-style:none;display:grid;gap:4px;font-size:var(--t-sm)}
.brief-panel .brief-touch b{font-weight:600}
@media (max-width:600px){.brief-card{padding:8px 16px}}`;
document.head.appendChild(briefingStyle);

function briefingCount(items) {
  const n = (items || []).length;
  return n ? `${n} item${n === 1 ? '' : 's'}` : '';
}

function briefingCardHtml(b) {
  const bets = (b.touched || []).map(t => `<a class="pill" href="#/strategy" title="${esc(t.statement)}">Bet ${esc(t.n)}</a>`).join('');
  return `<span class="brief-week">${esc(b.week)}</span><span class="brief-title">${esc(b.title)}</span>`
    + (briefingCount(b.items) ? `<span class="brief-count">${esc(briefingCount(b.items))}</span>` : '')
    + (bets ? `<span class="brief-bets" aria-label="Bets this touches">${bets}</span>` : '')
    + '<button class="ghost brief-read" type="button" data-brief-read>Read</button>';
}

function briefingOpen(b) {
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal gpanel brief-panel';
  const touched = (b.touched || []).map(t => `<li><b>Bet ${esc(t.n)}</b> ${esc(t.statement)}</li>`).join('');
  dialog.innerHTML = `<div class="tmodal-head"><h2>${esc(b.title)}</h2><span class="spacer"></span><button class="ghost tmodal-x" type="button" aria-label="Close">✕</button></div>
    <div class="tmodal-body">${touched ? `<ul class="brief-touch">${touched}</ul>` : ''}<div class="md market-md">${safeMd(b.body || '')}</div></div>`;
  document.body.append(dialog);
  dialog.querySelector('.tmodal-x').onclick = () => dialog.close();
  dialog.onclose = () => dialog.remove();
  dialog.showModal();
}

// Puts the newest briefing's line before `before` (the market shell). Quiet when there is none or the call fails.
window.marketBriefingCard = async function marketBriefingCard(before) {
  document.getElementById('market-briefing')?.remove();
  try {
    const {briefing} = await get('/v2/market/briefings/latest');
    if (!briefing || !before?.isConnected) return;
    const card = document.createElement('div');
    card.id = 'market-briefing';
    card.className = 'brief-card';
    card.innerHTML = briefingCardHtml(briefing);
    card.querySelector('[data-brief-read]').onclick = () => briefingOpen(briefing);
    before.before(card);
  } catch {
    /* the page works without it */
  }
};
