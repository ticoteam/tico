/* ui/app/bot-history.ts — A bot page's run history, read on its page.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Moved from ui/app/tooltip.ts, whose other code Home runs, into the bot page part (ui/parts.json): only that part
   calls these, so a cold open of Home does not download them (P05b). */
'use strict';

async function v2HistoryLoad(slug: string): Promise<void> {
  const pills: Record<string, string> = V2_PILL, words: Record<string, string> = V2_WORD;
  const el: HTMLElement | null = $('#v2-history'); if (!el) return;
  const d: {history?: V2HistoryRow[]} | null = await v2Get(`/v2/status?bot=${encodeURIComponent(slug)}&since=7d`);
  // Looked up again: the box may have been redrawn while the history was on its way.
  const box: HTMLElement | null = $('#v2-history'); if (!box) return;
  const rows: V2HistoryRow[] = [...(d?.history || [])].sort((a, b) => String(b.started_at || '').localeCompare(String(a.started_at || '')));
  box.innerHTML = rows.length
    ? `<ul class="sh-list">${rows.map(h => { const by = h.by && h.by !== 'keeper' && actorSlug(h.by) !== slug ? actorLabel(h.by) : '';   // the bot or the hub itself goes without saying
        const why = [h.reason || '', by].filter(Boolean).join(' · ');
        return `<li class="sh-row"><span class="pill ${pills[h.state ?? ''] ?? ''}">${esc(words[h.state ?? ''] || h.state || '')}</span>
        <span class="sh-focus"${h.focus || why ? ` title="${esc([h.focus, why].filter(Boolean).join(' · '))}"` : ''}>${esc(h.focus || '')}${why ? ` <span class="muted">${esc(why)}</span>` : ''}</span>
        <span class="sh-when muted tnum" title="${esc(fmt(h.started_at))}${h.ended_at ? ' to ' + esc(fmt(h.ended_at)) : ''}">${esc(ago(h.started_at))}</span></li>`; }).join('')}</ul>`
    : '<div class="empty">No status changes in the last week.</div>';
}
