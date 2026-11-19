import { useEffect, useEffectEvent, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react';
import { motionLevel } from '@/motion';
import { PhoneCards } from '@/shell/phone';
import { Dot } from '@/tico/dot';
import { useArrangement } from './arrangement';
import { IconButton } from '@/kit/IconButton';
import { C, layout, modeFor, press, specOf, type Arrangement, type ArrangementSpec, type Saved, type SheetId, type SheetLayout } from './layout';
import { keepOne, useSheetMemory, withFold, withWidth, withoutWidth } from './memory';
import { SheetContext, type SheetControls } from './Sheet';
import './Frame.css';

/* The ground and the sheets: Tico | the menu (or a task's list) | a second chat | the page, plus a panel, an agent's
   widgets or a call's side. Sizes come from layout() (layout.md); under 760 the phone's cards take over. */

export interface FrameProps {
  /* Tico's open sheet; folded, the frame draws its strip */
  tico: ReactNode;
  /* the menu; it also stands in a task's list place, folded to its icons */
  menu: ReactNode;
  /* the stretching sheet: the page, an open task, a call's stage, or an agent's own chat */
  page: ReactNode;
  /* a second chat beside a page, or a task's chat */
  chat?: ReactNode;
  /* a task's list, in the menu's place */
  list?: ReactNode;
  /* a record right of the page */
  panel?: ReactNode;
  /* an agent's widgets, right of its chat */
  widgets?: ReactNode;
  /* a call's chat, transcript and people */
  side?: ReactNode;
  /* by default the address decides */
  arrangement?: Arrangement;
  onCloseChat?: () => void;
  onClosePanel?: () => void;
}

const NAME: Record<SheetId, string> = { tico: 'Tico', menu: 'the menu', list: 'the list', chat: 'the chat', page: 'the page', panel: 'the panel', widgets: 'widgets', stage: 'the call', side: 'the side' };

export function Frame(p: FrameProps) {
  return p.arrangement ? <Sheets {...p} arr={p.arrangement} /> : <Routed {...p} />;
}
function Routed(p: FrameProps) {
  return <Sheets {...p} arr={useArrangement()} />;
}

function specFor(arr: Arrangement, p: FrameProps): ArrangementSpec {
  if (arr === 'page') return p.panel ? { name: 'chat', chat: false, panel: true } : 'page';
  if (arr === 'chat') return p.chat || p.panel ? { name: 'chat', chat: !!p.chat, panel: !!p.panel } : 'page';
  return arr;
}

/* The window's width and cards or sheets, with the hysteresis in layout.md 8. Resizes are never animated. */
function useWindowMode() {
  const [s, setS] = useState(() => ({ width: window.innerWidth, mode: modeFor(window.innerWidth) }));
  useEffect(() => {
    const on = () => setS(prev => ({ width: window.innerWidth, mode: modeFor(window.innerWidth, prev.mode) }));
    window.addEventListener('resize', on);
    return () => window.removeEventListener('resize', on);
  }, []);
  useEffect(() => {
    document.documentElement.dataset.layout = s.mode;
  }, [s.mode]);
  return s;
}

const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT');

interface Drag {
  id: SheetId;
  dir: 1 | -1;
  x0: number;
  w0: number;
  folded: boolean;
  max: number;
  pointer: number;
}
type Cause = 'move' | 'reset' | null;
interface Placed {
  x: number;
  folded: boolean;
  shown: boolean;
}

function Sheets(p: FrameProps & { arr: Arrangement }) {
  const { width, mode } = useWindowMode();
  const [saved, update] = useSheetMemory();
  const [draft, setDraft] = useState<Saved | null>(null);
  const [peek, setPeek] = useState<SheetId | null>(null);
  const [dragging, setDragging] = useState<SheetId | null>(null);
  const drag = useRef<Drag | null>(null);
  const cause = useRef<Cause>(null);
  const places = useRef(new Map<SheetId, HTMLElement>());
  const before = useRef<{ arr: Arrangement; at: Partial<Record<SheetId, Placed>> } | null>(null);

  const spec = specFor(p.arr, p);
  const arr = typeof spec === 'string' ? spec : spec.name; // which memory: a panel alone uses the chat one
  const L = layout(width, spec, draft ?? saved);
  const sheets = L.mode === 'sheets' ? L.sheets : [];
  const stretch = L.mode === 'sheets' ? L.stretch : null;
  const peeking = peek && sheets.some(s => s.id === peek && s.state !== 'open') ? peek : null;
  const find = (id: SheetId) => sheets.find(s => s.id === id);

  /* a fold control, a strip, or a tucked sheet's button (layout.md 5) */
  const onPress = (id: SheetId) => {
    if (peeking === id) return setPeek(null);
    const cur = find(id);
    if (!cur) return;
    if (cur.state === 'tucked' || (cur.state === 'fold' && !cur.width && cur.why === 'room')) return setPeek(id);
    const r = press(width, spec, saved, id);
    cause.current = 'move';
    if (r.saved !== saved) update(now => withFold(now, arr, id, r.saved[arr]?.fold?.[id] === true));
    setPeek(r.peek);
  };
  const resetOne = (id: SheetId) => {
    cause.current = 'reset';
    update(now => withoutWidth(now, arr, id));
  };
  const setW = (id: SheetId, w: number) => update(now => withWidth(now, arr, id, w));

  /* dragging an edge: live with no animation; saved when the drag ends (layout.md 4) */
  const onDown = (e: PointerEvent<HTMLDivElement>, s: SheetLayout, dir: 1 | -1) => {
    if (e.button !== 0) return;
    const sp = specOf(arr, s.id);
    drag.current = { id: s.id, dir, x0: e.clientX, w0: s.width, folded: s.state === 'fold', max: s.state === 'open' ? (s.dragMax ?? sp.max) : sp.max, pointer: e.pointerId };
    e.currentTarget.setPointerCapture(e.pointerId);
    document.body.classList.add('is-dragging');
    cause.current = null;
    setDraft(saved);
    setDragging(s.id);
    e.preventDefault();
  };
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || !draft) return;
    const sp = specOf(arr, d.id);
    const raw = d.w0 + (e.clientX - d.x0) * d.dir;
    let next = draft;
    if (sp.fold != null) {
      // past the floor it snaps shut; pulled back it opens (with a gap between, so it cannot flutter)
      if (!d.folded && raw < sp.min - C.SNAP) d.folded = true;
      else if (d.folded && raw >= sp.min - C.UNSNAP && press(width, spec, withFold(draft, arr, d.id, true), d.id).peek == null) d.folded = false;
      next = withFold(next, arr, d.id, d.folded);
    }
    if (!d.folded) next = withWidth(next, arr, d.id, Math.round(Math.min(Math.max(raw, sp.min), Math.max(sp.min, d.max))));
    setDraft(next);
  };
  const endDrag = (keep: boolean) => {
    const d = drag.current;
    drag.current = null;
    document.body.classList.remove('is-dragging');
    if (d && keep && draft) update(now => keepOne(now, draft, arr, d.id));
    setDraft(null);
    setDragging(null);
  };

  const onHandleKey = (e: KeyboardEvent<HTMLDivElement>, s: SheetLayout, dir: 1 | -1) => {
    const step = (e.shiftKey ? 64 : 16) * dir;
    const min = s.min ?? 0;
    const max = s.dragMax ?? min;
    const to = { ArrowLeft: s.width - step, ArrowRight: s.width + step, Home: min, End: max }[e.key];
    if (e.key === 'Enter') onPress(s.id);
    else if (e.key === 'Delete' || e.key === 'Backspace') resetOne(s.id);
    else if (e.key === 'Escape' && drag.current) endDrag(false);
    else if (to != null && s.state === 'open') setW(s.id, Math.round(Math.min(Math.max(to, min), max)));
    else return;
    e.preventDefault();
  };

  /* shortcuts (layout.md 5.1): Cmd/Ctrl+J Tico, [ the menu or list, ] widgets or a call's side, Escape */
  const onKey = useEffectEvent((e: globalThis.KeyboardEvent) => {
    if (mode === 'cards' || L.mode !== 'sheets') return;
    const ticoEl = places.current.get('tico');
    if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 'j') {
      e.preventDefault();
      const t = find('tico');
      const inside = !!ticoEl && ticoEl.contains(document.activeElement);
      if (t && t.state !== 'open' && peeking !== 'tico') {
        onPress('tico');
        requestAnimationFrame(() => places.current.get('tico')?.querySelector<HTMLElement>('textarea, input, [contenteditable="true"]')?.focus());
      } else if (!inside) ticoEl?.querySelector<HTMLElement>('textarea, input, [contenteditable="true"]')?.focus();
      else onPress('tico');
      return;
    }
    if (e.key === 'Escape') {
      if (drag.current) return endDrag(false);
      if (peeking) return setPeek(null);
      const t = e.target;
      const inChat = places.current.get('chat')?.contains(t as Node);
      const inPanel = places.current.get('panel')?.contains(t as Node);
      if (inChat && p.arr === 'chat' && p.onCloseChat && (!typing(t) || !(t as HTMLInputElement).value)) p.onCloseChat();
      else if (inPanel && p.onClosePanel) p.onClosePanel();
      return;
    }
    if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === '[') {
      const two = find('menu') ?? find('list');
      if (two) onPress(two.id);
    } else if (e.key === ']') {
      const side = find('widgets') ?? find('side');
      if (side) onPress(side.id);
    }
  });
  useEffect(() => {
    const on = (e: globalThis.KeyboardEvent) => onKey(e);
    document.addEventListener('keydown', on);
    return () => document.removeEventListener('keydown', on);
  }, []);

  /* a sheet opened over its neighbours for now goes back on a click outside it */
  useEffect(() => {
    if (!peeking) return;
    const on = (e: Event) => {
      if (!places.current.get(peeking)?.contains(e.target as Node)) setPeek(null);
    };
    document.addEventListener('pointerdown', on);
    return () => document.removeEventListener('pointerdown', on);
  }, [peeking]);

  /* folds and moves (motion.md E1-E3, E9): lay out at the end state, then play each sheet's old place to its new one
     as a transform (FLIP). Drags and window resizes are not animated. */
  useLayoutEffect(() => {
    if (mode === 'cards' || L.mode !== 'sheets' || !stretch) {
      before.current = null;
      return;
    }
    const at: Partial<Record<SheetId, Placed>> = {};
    for (const s of sheets) {
      const shown = peeking === s.id || !(s.state === 'tucked' || (s.state === 'fold' && !s.width));
      at[s.id] = { x: s.x ?? 0, folded: s.state === 'fold' && peeking !== s.id, shown };
    }
    const was = before.current;
    const why: Cause = was && was.arr !== p.arr ? 'move' : cause.current;
    before.current = { arr: p.arr, at };
    cause.current = null;
    if (!was || !why || dragging || motionLevel() === 'off' || typeof Element.prototype.animate !== 'function') return;
    const dur = why === 'reset' ? 240 : 620;
    const easing = why === 'reset' ? 'cubic-bezier(.2,0,0,1)' : 'cubic-bezier(.3,1,.3,1)';
    for (const [id, el] of places.current) {
      const a = was.at[id];
      const b = at[id];
      if (!b?.shown) continue;
      if (!a?.shown) {
        // a sheet arriving: from 60 to the right for a second chat (E5), and a fade
        el.animate([{ transform: id === 'chat' ? 'translateX(60px)' : 'none' }, { transform: 'none' }], { duration: dur, easing });
        el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 340, easing: 'cubic-bezier(.2,0,0,1)' });
        continue;
      }
      if (a.x !== b.x) {
        el.classList.add('is-moving');
        el.animate([{ transform: `translateX(${a.x - b.x}px)` }, { transform: 'none' }], { duration: dur, easing }).finished.then(
          () => el.classList.remove('is-moving'),
          () => el.classList.remove('is-moving'),
        );
      }
      if (a.folded !== b.folded) {
        // folding or opening: the sheet's content fades in at its new form (E2, E3, E4)
        for (const c of el.firstElementChild?.children ?? []) c.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 300, delay: b.folded ? 0 : 200, easing: 'cubic-bezier(.2,0,0,1)', fill: 'backwards' });
      }
    }
  });

  if (mode === 'cards' || L.mode === 'cards' || !stretch) {
    // a call takes the screen in the chat's place (phone/notes.md)
    const call = p.arr === 'call';
    return <PhoneCards page={call ? null : p.page} tico={p.tico} menu={p.menu} chat={call ? undefined : p.chat} call={call ? p.page : undefined} />;
  }

  const st = find(stretch.id);
  const stX = st?.x ?? C.MARGIN;
  const tucked = sheets
    .filter(o => o.state === 'tucked' || (o.state === 'fold' && !o.width))
    .map(o => ({ id: o.id, label: `Show ${NAME[o.id]}`, open: () => onPress(o.id) }));

  const content = (s: SheetLayout, folded: boolean) => {
    switch (s.id) {
      case 'tico':
        return folded ? <TicoStrip onOpen={() => onPress('tico')} /> : p.tico;
      case 'menu':
        return p.menu;
      case 'list':
        return folded ? p.menu : (p.list ?? p.menu);
      case 'chat':
        return s.id === stretch.id ? p.page : p.chat;
      case 'panel':
        return p.panel;
      case 'widgets':
        return p.widgets;
      case 'side':
        return p.side;
      default:
        return p.page;
    }
  };

  return (
    <div className="app-tico shell-frame" data-arrangement={p.arr}>
      {sheets.map(s => {
        const shown = peeking === s.id;
        const out = s.state === 'over' || s.state === 'tucked' || (s.state === 'fold' && !s.width);
        const folded = s.state === 'fold' && !shown && s.width > 0;
        const w = shown ? (s.wanted ?? s.width) : s.width;
        const x = shown && out && s.id !== 'chat' ? stX + stretch.width - w : (s.x ?? stX);
        const sp = s.id === stretch.id ? null : specOf(arr, s.id);
        const controls: SheetControls = {
          id: s.id,
          state: s.state,
          peek: shown,
          folded,
          canFold: sp?.fold != null,
          toggleFold: () => onPress(s.id),
          close: s.id === 'chat' && p.arr === 'chat' ? p.onCloseChat : s.id === 'panel' ? p.onClosePanel : undefined,
          tucked: s.id === stretch.id ? tucked : [],
        };
        return (
          <div
            key={s.id}
            ref={el => {
              if (el) places.current.set(s.id, el);
              else places.current.delete(s.id);
            }}
            className={`shell-place${out || shown ? ' is-over' : ''}`}
            style={{ left: x, width: w }}
            hidden={out && s.state !== 'over' && !shown}
            data-sheet={s.id}
          >
            <SheetContext value={controls}>{content(s, folded)}</SheetContext>
          </div>
        );
      })}
      {sheets
        .filter(s => s.id !== stretch.id && s.x != null && (s.state === 'open' || (s.state === 'fold' && s.width)))
        .map(s => {
          const right = sheets.indexOf(s) > sheets.findIndex(o => o.id === stretch.id);
          const dir = right ? -1 : 1;
          const x = s.x ?? 0;
          return (
            <div
              key={`h-${s.id}`}
              className={`shell-handle${dragging === s.id ? ' is-drag' : ''}`}
              role="separator"
              tabIndex={0}
              aria-orientation="vertical"
              aria-label={`Resize ${NAME[s.id]}`}
              aria-valuenow={Math.round(s.width)}
              aria-valuemin={s.state === 'open' ? s.min : s.width}
              aria-valuemax={s.dragMax}
              style={{ left: (right ? x - C.GAP : x + s.width) - 4 }}
              onPointerDown={e => onDown(e, s, dir)}
              onPointerMove={onMove}
              onPointerUp={() => endDrag(true)}
              onPointerCancel={() => endDrag(true)}
              onLostPointerCapture={() => drag.current && endDrag(true)}
              onDoubleClick={() => resetOne(s.id)}
              onKeyDown={e => onHandleKey(e, s, dir)}
            />
          );
        })}
    </div>
  );
}

/* Tico folded: the Dot at the top, the panel icon at the foot. No number beside the Dot (motion.md R1). */
function TicoStrip({ onOpen }: { onOpen: () => void }) {
  return (
    <aside className="pane tico is-fold" aria-label="Tico">
      <button className="tico-orb" type="button" aria-label="Open Tico" title="Open Tico" onClick={onOpen}>
        <Dot size={30} />
      </button>
      <span className="grow" />
      <IconButton icon="panel" label="Open Tico" quiet onClick={onOpen} />
    </aside>
  );
}
