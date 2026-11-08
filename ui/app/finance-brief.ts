/* ui/app/finance-brief.ts — What changed: the short Finance briefing at the top of the Finance page (plan step 4b). A few
   lines about the money the viewer may see (their budgets, their team's, cash and burn where they see the company), with
   the time those numbers last moved. Hidden when there are no lines. Read: GET /api/v2/finance/brief (view_as passes
   through like the other Finance reads). Nothing here writes.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   TypeScript: npm run build:ui compiles it to build/ui/app/finance-brief.js, which the page runs in this file's place. */
'use strict';

// The brief as GET /api/v2/finance/brief sends it: lines are already cut to the viewer.
interface FinBriefRead { lines: string[]; as_of: string; changed_since?: string | null; version: number }

// "9:40 AM": the time a brief's numbers last moved, in the viewer's clock.
function finBriefTime(iso: string): string {
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? '' : when.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
}

// The block: a heading, the time, and the lines as a short list.
function finBriefHtml(brief: FinBriefRead): string {
  const at = finBriefTime(brief.as_of);
  return `<section class="fin-brief" aria-label="What changed">
    <div class="fin-brief-head"><h2>What changed</h2>${at ? `<span class="muted fin-brief-at">as of ${esc(at)}</span>` : ''}</div>
    <ul class="fin-brief-lines">${brief.lines.map(line => `<li>${esc(line)}</li>`).join('')}</ul>
  </section>`;
}

// Loads the brief into the page's top slot. `state` is the page's Finance state: a page left before the read returns is
// not drawn over. A failed read or an empty brief leaves the slot empty.
async function finBriefLoad(state: object): Promise<void> {
  const box = $('#fin-brief');
  if (!box) return;
  const view = typeof FIN_VIEW_AS === 'string' && FIN_VIEW_AS ? `?view_as=${encodeURIComponent(FIN_VIEW_AS)}` : '';
  let brief: FinBriefRead | null = null;
  try { brief = await get('/v2/finance/brief' + view, {fresh: true}) as FinBriefRead; } catch { brief = null; }
  if (FIN !== state || !$('#fin-brief')) return;
  $('#fin-brief')!.innerHTML = brief && brief.lines?.length ? finBriefHtml(brief) : '';
}
