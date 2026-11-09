/* ui/app/rails.ts — The sidebar and the bot page's right rail are dragged wider or narrower
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   It is first in the bundle and needs nothing from the others to start, so a saved width is set before the
   page draws its content. The widths are the --rail-left and --rail-right variables (ui/styles/base.css, chat.css).
   A person's widths are kept on this device and with their account (preference ui.rails, stamped with when they
   last changed); the newer copy wins when a page loads or a tab comes back to the front. A double-click on an
   edge puts that rail back to its default; the arrow keys move a focused edge. A phone has no edges. */
'use strict';

type RailSide = 'left' | 'right';
interface RailSpec {
  prop: string;
  def: number;
  min: number;
  max: number;
  label: string;
  controls: string;
}
const RAILS: Record<RailSide, RailSpec> = {
  left: {prop: '--rail-left', def: 236, min: 200, max: 420, label: 'Resize sidebar', controls: 'side'},
  right: {prop: '--rail-right', def: 320, min: 260, max: 560, label: 'Resize tasks panel', controls: 'pane-tasks'},
};
// The data-rail value of an edge, when it names one of the two rails.
const railSide = (value: string | undefined): RailSide | null => value === 'left' || value === 'right' ? value : null;
const RAILS_KEY = 'tico.rails', RAILS_PREF = 'ui.rails';
// The middle keeps at least this much beside the sidebar, and the bot's chat this much beside the right rail
// (the main column's own side padding is 56px).
const RAILS_MID = 480, RAILS_CHAT = 360, RAILS_MAIN_PAD = 56;
const RAILS_STEP = 16;
// 0 until a rail is first applied; every read falls back to the default on 0, as it did on undefined.
const railsShown: Record<RailSide, number> = {left: 0, right: 0};
function railsSaved(): Record<string, unknown> {
  try {
    const v: Record<string, unknown> | null = JSON.parse(localStorage.getItem(RAILS_KEY) || '{}');
    return v && typeof v === 'object' ? v : {};
  } catch { return {}; }
}
function railsStore(v: Record<string, unknown>): void { try { localStorage.setItem(RAILS_KEY, JSON.stringify(v)); } catch {} }
function railsWant(side: RailSide): number { const n = Number(railsSaved()[side]); return n > 0 ? n : RAILS[side].def; }
// The widest a rail may be now: its own limit, and the room the window leaves.
function railsMax(side: RailSide): number {
  const r = RAILS[side];
  const left = railsShown.left || RAILS.left.def;
  const room = side === 'left' ? innerWidth - RAILS_MID : innerWidth - left - RAILS_MAIN_PAD - RAILS_CHAT;
  return Math.max(r.min, Math.min(r.max, room));
}
function railsApply(side: RailSide, want: number): void {
  const r = RAILS[side];
  const w = Math.round(Math.max(r.min, Math.min(Number.isFinite(want) ? want : r.def, railsMax(side))));
  railsShown[side] = w;
  document.documentElement.style.setProperty(r.prop, w + 'px');
  for (const edge of document.querySelectorAll<HTMLElement>(`.rail-edge[data-rail="${side}"]`)) railsAria(edge, side);
  if (side === 'left') railsApply('right', railsWant('right'));   // their room follows the sidebar
}
function railsAria(edge: Element, side: RailSide): void {
  edge.setAttribute('aria-valuenow', String(railsShown[side] || RAILS[side].def));
  edge.setAttribute('aria-valuemin', String(RAILS[side].min));
  edge.setAttribute('aria-valuemax', String(Math.round(railsMax(side))));
}
// The edge's markup, for a page that draws its own (ui/app/bot-page.ts); the sidebar's is added below.
function railEdgeHTML(side: RailSide, label?: string, controls?: string): string {
  const r = RAILS[side];
  return `<div class="rail-edge" data-rail="${side}" role="separator" aria-orientation="vertical" aria-label="${label || r.label}" aria-controls="${controls || r.controls}" tabindex="0" aria-valuenow="${railsShown[side] || r.def}" aria-valuemin="${r.min}" aria-valuemax="${Math.round(railsMax(side))}"></div>`;
}

let railsTimer: number | undefined;
function railsSave(): void {
  if (typeof S === 'undefined' || !S.me?.cloud) return;
  clearTimeout(railsTimer);
  railsTimer = setTimeout(() => { void post('/v2/preferences/' + RAILS_PREF, {value: railsSaved()}).catch(() => {}); }, 800);
}
// `w` null: back to the default.
function railsKeep(side: RailSide, w: number | null): void {
  const v = railsSaved();
  if (w == null) delete v[side]; else v[side] = w;
  v.at = Date.now();
  railsStore(v);
  railsSave();
}
async function railsSync(): Promise<void> {
  if (typeof S === 'undefined' || !S.me?.cloud) return;
  const v: Record<string, unknown> | null | undefined = (await v2Get('/v2/preferences/' + RAILS_PREF))?.value;
  const at = Number(v?.at) || 0, localAt = Number(railsSaved().at) || 0;
  if (v && at > localAt) {
    const keep: Record<string, number> = {at};
    const sides: RailSide[] = ['left', 'right'];
    for (const side of sides) if (Number(v[side]) > 0) keep[side] = Math.round(Number(v[side]));
    railsStore(keep);
    railsApply('left', railsWant('left'));
  } else if (at < localAt) railsSave();
}

(() => {
  railsApply('left', railsWant('left'));
  document.body.insertAdjacentHTML('beforeend', railEdgeHTML('left'));

  // The drag is followed on the window, not the edge: the pointer leaves an 8px edge at once.
  let drag: {side: RailSide; el: HTMLElement; x: number; w: number} | null = null;
  const move = (e: PointerEvent): void => {
    if (!drag) return;
    if (e.buttons === 0) { end(); return; }   // released somewhere the release was not seen
    const dx = e.clientX - drag.x;
    railsApply(drag.side, drag.side === 'left' ? drag.w + dx : drag.w - dx);
  };
  const end = (): void => {
    if (!drag) return;
    const {side, el} = drag;
    drag = null;
    el.classList.remove('active');
    document.body.classList.remove('rail-dragging');
    removeEventListener('pointermove', move);
    removeEventListener('pointerup', end);
    removeEventListener('pointercancel', end);
    railsKeep(side, railsShown[side]);
  };
  document.addEventListener('pointerdown', e => {
    const el = eventElement(e)?.closest<HTMLElement>('.rail-edge');
    const side = railSide(el?.dataset.rail);
    if (!el || !side || e.button !== 0) return;
    e.preventDefault();
    drag = {side, el, x: e.clientX, w: railsShown[side]};
    el.classList.add('active');
    document.body.classList.add('rail-dragging');
    addEventListener('pointermove', move);
    addEventListener('pointerup', end);
    addEventListener('pointercancel', end);
  });
  document.addEventListener('dblclick', e => {
    const el = eventElement(e)?.closest<HTMLElement>('.rail-edge');
    const side = railSide(el?.dataset.rail);
    if (!el || !side) return;
    railsApply(side, RAILS[side].def);
    railsKeep(side, null);
  });
  document.addEventListener('keydown', e => {
    const el = eventElement(e)?.closest<HTMLElement>('.rail-edge');
    const side = railSide(el?.dataset.rail);
    if (!el || !side || e.altKey || e.ctrlKey || e.metaKey) return;
    const now = railsShown[side];
    // The edge moves the way the arrow points: right widens the sidebar and narrows the right rail.
    const step = (e.shiftKey ? 4 : 1) * RAILS_STEP * (side === 'left' ? 1 : -1);
    const to = e.key === 'ArrowRight' ? now + step : e.key === 'ArrowLeft' ? now - step
      : e.key === 'Home' ? RAILS[side].min : e.key === 'End' ? railsMax(side) : null;
    if (to == null) return;
    e.preventDefault();
    railsApply(side, to);
    railsKeep(side, railsShown[side]);
  });
  // A narrower window squeezes the rails for now; the saved widths come back when there is room.
  let soon = 0;                                     // once a frame while the window is dragged
  addEventListener('resize', () => { cancelAnimationFrame(soon); soon = requestAnimationFrame(() => railsApply('left', railsWant('left'))); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) void railsSync(); });
})();
