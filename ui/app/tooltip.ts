/* ui/app/tooltip.ts — Status tooltip on hover or focus
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ---- status on hover: one tooltip, moved to whichever bot the pointer or focus is on ----
const TIP_HOVER_DELAY_MS = 650;
let TIP: HTMLElement | null = null, TIP_EL: Element | null = null, TIP_SLUG: string | null = null, TIP_PENDING: Element | null = null;
let TIP_TIMER: number | undefined = undefined;
function tipBox(): HTMLElement {
  if (!TIP) {
    const tip = document.createElement('div');
    tip.className = 'tip'; tip.id = 'bot-tip'; tip.setAttribute('role', 'tooltip'); tip.hidden = true;
    document.body.append(tip);
    TIP = tip;
  }
  return TIP;
}
function tipHTML(slug: string): string {
  const pills: Record<string, string> = V2_PILL, words: Record<string, string> = V2_WORD;
  const e = botRow(slug), s = v2StatusOf(slug);
  const rows: [string, string][] = [];
  if (s) {
    rows.push(['State', `<span class="pill ${pills[s.state] ?? ''}">${esc(words[s.state] || s.state || 'unknown')}</span>${
      s.changed_at ? ` <span class="muted">${esc(ago(s.changed_at))}</span>` : ''}`]);
    if (s.focus) rows.push(['Focus', esc(s.focus)]);
    if (s.last_result) rows.push(['Last result', esc(String(s.last_result).slice(0, 160))]);
    rows.push(['Next run', s.next_due_at ? `${esc(until(s.next_due_at))} <span class="muted">${esc(fmt(s.next_due_at))}</span>` : '—']);
    rows.push(['Open tasks', esc(String(s.open_tasks ?? 0))]);
    rows.push(['Needs humans', esc(String(s.needs_human ?? 0))]);
  } else {
    rows.push(['State', botStatePill(slug)]);
    rows.push(['Open', esc(`${openCount(slug)} task${openCount(slug) === 1 ? '' : 's'}`)]);
  }
  if (S.me?.cloud && e && e.agent) {
    rows.push(['Run by', esc(`${agentKind(e.agent)}${e.agent.profile ? ` · ${e.agent.profile}` : ''}`)]);
    rows.push(['Status', agentPresenceLabel(e.agent, e.online)]);
    if (e.agent.last_seen_at) rows.push([e.agent.synced ? 'Last sync' : 'Last seen', esc(ago(e.agent.last_seen_at))]);
  } else if (S.me?.cloud && e) {
    rows.push(['Computer', esc(e.machine?.label || 'Not registered')]);
    rows.push(['Status', e.online ? (e.ready ? 'Online · ready' : 'Online · setup needed') : 'Offline']);
    // Its turns run on the Tico AI Gateway until its subscription is signed in (backend/models_preset.py as_served).
    if (e.online && e.readiness?.gateway) rows.push(['Model', esc(e.readiness.gateway)]);
    if (e.machine?.last_seen_at) rows.push(['Last seen', esc(ago(e.machine.last_seen_at))]);
  }
  return `<div class="tip-head">${avatar(slug, 18, stateOf(slug))}<strong>${empName(slug)}</strong></div>
    ${e?.description ? `<div class="tip-sub">${esc(e.description)}</div>` : ''}
    <dl>${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('')}</dl>`;
}
function tipShow(el: Element, slug: string | undefined): void {
  if (!slug || !botRow(slug)) return;
  clearTimeout(TIP_TIMER); TIP_TIMER = undefined; TIP_PENDING = null;
  const tip = tipBox();
  tip.innerHTML = tipHTML(slug);
  tip.hidden = false;
  tip.style.left = '0px'; tip.style.top = '0px';
  const r = el.getBoundingClientRect(), box = tip.getBoundingClientRect();
  const left = Math.max(8, Math.min(r.left, window.innerWidth - box.width - 8));
  let top = r.bottom + 8;
  if (top + box.height > window.innerHeight - 8) top = Math.max(8, r.top - box.height - 8);
  tip.style.left = `${Math.round(left)}px`; tip.style.top = `${Math.round(top)}px`;
  if (TIP_EL && TIP_EL !== el) TIP_EL.removeAttribute('aria-describedby');
  el.setAttribute('aria-describedby', 'bot-tip');
  TIP_EL = el;
  TIP_SLUG = slug;
}
function tipRefresh(slug: string | null = TIP_SLUG): void {
  if (!TIP || TIP.hidden || !TIP_SLUG || slug !== TIP_SLUG) return;
  // Sidebar redraws can replace the anchor while the tooltip is open. Follow its current bot row.
  const el = TIP_EL?.isConnected ? TIP_EL : [...document.querySelectorAll<HTMLElement>('[data-tip-bot]')]
    .find(e => e.dataset.tipBot === TIP_SLUG && e.getClientRects().length);
  if (el) tipShow(el, TIP_SLUG); else tipHide();
}
function tipHide(): void {
  clearTimeout(TIP_TIMER); TIP_TIMER = undefined; TIP_PENDING = null;
  if (TIP) TIP.hidden = true;
  if (TIP_EL) { TIP_EL.removeAttribute('aria-describedby'); TIP_EL = null; }
  TIP_SLUG = null;
}
function tipSchedule(el: Element, slug: string | undefined): void {
  if (TIP_EL === el && TIP && !TIP.hidden || TIP_PENDING === el) return;
  tipHide();
  TIP_PENDING = el;
  TIP_TIMER = setTimeout(() => {
    TIP_TIMER = undefined; TIP_PENDING = null;
    if (el.matches(':hover')) tipShow(el, slug);
  }, TIP_HOVER_DELAY_MS);
}
document.addEventListener('mouseover', ev => {
  const t = eventElement(ev)?.closest<HTMLElement>('[data-tip-bot]');
  if (t) tipSchedule(t, t.dataset.tipBot); else tipHide();
});
document.addEventListener('mouseout', ev => {
  const t = eventElement(ev)?.closest<HTMLElement>('[data-tip-bot]');
  const to = ev.relatedTarget;
  if (t && !(to instanceof Node && t.contains(to))) tipHide();
});
document.addEventListener('focusin', ev => {
  const t = eventElement(ev)?.closest<HTMLElement>('[data-tip-bot]');
  if (t) tipShow(t, t.dataset.tipBot); else tipHide();
});
document.addEventListener('keydown', ev => { if (ev.key === 'Escape') tipHide(); });
window.addEventListener('scroll', tipHide, true);
window.addEventListener('hashchange', tipHide);

// The bot's Status history under More: newest first, one line a change (state, focus, why and who changed it
// when that was someone else, when it began).
/** One row of a bot's status history (GET /api/v2/status, `history`). */
interface V2HistoryRow {
  by?: string;
  reason?: string;
  state?: string;
  focus?: string;
  started_at?: string;
  ended_at?: string | null;
}
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
