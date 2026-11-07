/* The weekly market briefing read in full, in a side panel: the bets it touches, then the briefing's page
   (backend/market_briefing.py, docs/market-briefing.md). Read under Market's What changed? opens it (ui/app/redesign-market.js). */
'use strict';
interface MarketBriefingTouch { n: number | string; statement: string; }
interface MarketBriefing { title: string; body?: string; touched?: MarketBriefingTouch[]; }
const briefingStyle = document.createElement('style');
briefingStyle.textContent = `.brief-panel .brief-touch{margin:0 0 10px;padding:0;list-style:none;display:grid;gap:4px;font-size:var(--t-sm)}
.brief-panel .brief-touch b{font-weight:600}`;
document.head.appendChild(briefingStyle);

function briefingOpen(b: MarketBriefing): void {
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal gpanel brief-panel';
  const touched = (b.touched || []).map(t => `<li><b>Bet ${esc(t.n)}</b> ${esc(t.statement)}</li>`).join('');
  dialog.innerHTML = `<div class="tmodal-head"><h2>${esc(b.title)}</h2><span class="spacer"></span><button class="ghost tmodal-x" type="button" aria-label="Close">✕</button></div>
    <div class="tmodal-body">${touched ? `<ul class="brief-touch">${touched}</ul>` : ''}<div class="md market-md">${safeMd(b.body || '')}</div></div>`;
  document.body.append(dialog);
  const close = dialog.querySelector<HTMLButtonElement>('.tmodal-x');
  if (close) close.onclick = () => dialog.close();
  dialog.onclose = () => dialog.remove();
  dialog.showModal();
}
