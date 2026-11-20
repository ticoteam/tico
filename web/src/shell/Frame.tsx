import { Fragment, useEffect, useEffectEvent, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react';
import { motionLevel } from '@/motion';
import { PhoneCards, type PhoneCardsProps } from '@/shell/phone';
import { Dot } from '@/tico/dot';
import { useArrangement } from './arrangement';
import { useDesktopInset } from '@/desktop';
import { Icon } from '@/kit/Icon';
import { C, forwardFor, layout, modeFor, press, specOf, TICO_MARK, type Arrangement, type ArrangementSpec, type Saved, type SheetId, type SheetLayout } from './layout';
import { keepOne, useSheetMemory, withFold, withWidth, withoutWidth } from './memory';
import { SheetContext, type SheetControls } from './Sheet';
import './Frame.css';
import './desktop.css';

/* The ground and the sheets: Tico | the menu (or a task's list) | a second chat | the page, plus a panel, an agent's
   widgets or a call's side. Sizes come from layout() (layout.md, then plan/layers.html); under 760 the phone's cards
   take over. The stacking order never changes: the leftmost sheet is on top. A tucked sheet keeps its card under its
   left neighbour and shows its edge, one button that opens it. */

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
  /* on the phone: what a card's lip shows at rest (the menu's: the company and its Flow count) */
  lips?: PhoneCardsProps['lips'];
  /* on the phone: a control on a card's lip that is not its tap target (mute on a call's) */
  lipActs?: PhoneCardsProps['lipActs'];
  /* by default the address decides */
  arrangement?: Arrangement;
  onCloseChat?: () => void;
  onClosePanel?: () => void;
}

/* How long sheets take to move (motion.md E1-E3). A change of arrangement moves at E1's 620ms (--dur-sheet). A sheet
   the person folds or opens moves about three times as fast, so the fold feels snappy; its content fades in to match.
   Transform and opacity only; Off (and the system's reduced motion) jumps. */
const SHEET_MOVE = { ms: 620, easing: 'cubic-bezier(.3,1,.3,1)', arriveMs: 340, fadeMs: 300, fadeDelayMs: 200 };
const SHEET_FOLD = { ms: 200, easing: 'cubic-bezier(.3,1,.3,1)', arriveMs: 120, fadeMs: 100, fadeDelayMs: 70 };
const SHEET_RESET = { ms: 240, easing: 'cubic-bezier(.2,0,0,1)', arriveMs: 340, fadeMs: 300, fadeDelayMs: 200 };

/* the least head a sheet keeps beside the desktop window's buttons; narrower, its content starts below them */
const BESIDE_LIGHTS = 160;

/* what a tucked sheet's edge says, under its mark */
const EDGE_NAME: Record<SheetId, string> = { tico: 'Tico', menu: 'Menu', list: 'Tasks', chat: 'Chat', page: 'Page', panel: 'Panel', widgets: 'Page', stage: 'Call', side: 'Side' };
const EDGE_ICON = { menu: 'company', list: 'tasks', chat: 'message', page: 'file', panel: 'panel', widgets: 'file', stage: 'call', side: 'panel' } as const;

const NAME: Record<SheetId, string> = { tico: 'Tico', menu: 'the menu', list: 'the list', chat: 'the chat', page: 'the page', panel: 'the panel', widgets: 'widgets', stage: 'the call', side: 'the side' };

/* The mounted frame's hand fold, for the `shell.fold` action: fold (true) or open (false) a sheet; undefined flips it. */
let foldHand: ((id: SheetId, folded?: boolean) => void) | null = null;
export function foldSheet(id: string, folded?: boolean): void {
  if (!foldHand) throw new Error('the sheets are not drawn yet');
  foldHand(id as SheetId, folded);
}

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
type Cause = 'move' | 'fold' | 'reset' | null;
interface Placed {
  x: number;
  shut: boolean;
}

function Sheets(p: FrameProps & { arr: Arrangement }) {
  const { width, mode } = useWindowMode();
  const lights = useDesktopInset();
  const [saved, update] = useSheetMemory();
  const [draft, setDraft] = useState<Saved | null>(null);
  /* the sheet opened by its edge, for this arrangement only */
  const [opened, setOpened] = useState<{ arr: Arrangement; id: SheetId } | null>(null);
  const [dragging, setDragging] = useState<SheetId | null>(null);
  const drag = useRef<Drag | null>(null);
  const cause = useRef<Cause>(null);
  const places = useRef(new Map<SheetId, HTMLElement>());
  const before = useRef<{ arr: Arrangement; at: Partial<Record<SheetId, Placed>> } | null>(null);

  const spec = specFor(p.arr, p);
  const arr = typeof spec === 'string' ? spec : spec.name; // which memory: a panel alone uses the chat one
  const mem = draft ?? saved;
  // opened by its edge only while it would be tucked without it: once the window has room, the usual layout is back
  const forward = forwardFor(width, spec, mem, opened?.arr === p.arr ? opened.id : null);
  const L = layout(width, spec, mem, forward);
  const sheets = L.mode === 'sheets' ? L.sheets : [];
  const stretch = L.mode === 'sheets' ? L.stretch : null;
  const find = (id: SheetId) => sheets.find(s => s.id === id);

  /* a fold control, a shortcut, or a tucked sheet's edge (layout.md 5; plan/layers.html) */
  const onPress = (id: SheetId) => {
    if (!find(id)) return;
    const r = press(width, spec, saved, id, forward);
    cause.current = 'fold';
    if (r.saved !== saved) update(now => withFold(now, arr, id, r.saved[arr]?.fold?.[id] === true));
    setOpened(r.forward ? { arr: p.arr, id: r.forward } : null);
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
    drag.current = { id: s.id, dir, x0: e.clientX, w0: s.width, folded: false, max: s.dragMax ?? sp.max, pointer: e.pointerId };
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
    if (sp.fold) {
      // past the floor it snaps under; pulled back it opens (with a gap between, so it cannot flutter)
      if (!d.folded && raw < sp.min - C.SNAP) d.folded = true;
      else if (d.folded && raw >= sp.min - C.UNSNAP && press(width, spec, withFold(draft, arr, d.id, true), d.id).forward == null) d.folded = false;
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
      if (t && t.state !== 'open') {
        onPress('tico');
        requestAnimationFrame(() => places.current.get('tico')?.querySelector<HTMLElement>('textarea, input, [contenteditable="true"]')?.focus());
      } else if (!inside) ticoEl?.querySelector<HTMLElement>('textarea, input, [contenteditable="true"]')?.focus();
      else onPress('tico');
      return;
    }
    if (e.key === 'Escape') {
      if (drag.current) return endDrag(false);
      if (forward) {
        cause.current = 'fold';
        return setOpened(null);
      }
      const t = e.target;
      const inChat = places.current.get('chat')?.contains(t as Node);
      const inPanel = places.current.get('panel')?.contains(t as Node);
      if (inChat && p.arr === 'chat' && p.onCloseChat && (!typing(t) || !(t as HTMLInputElement).value)) p.onCloseChat();
      else if (inPanel && p.onClosePanel) p.onClosePanel();
      return;
    }
    if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
    // handled here, so the action layer's key listener (on window, after this one) does not flip it back
    if (e.key === '[') {
      const two = find('menu') ?? find('list');
      if (two) onPress(two.id);
      e.preventDefault();
    } else if (e.key === ']') {
      const side = find('widgets') ?? find('side');
      if (side) onPress(side.id);
      e.preventDefault();
    }
  });
  const onFold = useEffectEvent((id: SheetId, folded?: boolean) => {
    const cur = find(id);
    if (!cur) return;
    const shut = cur.state !== 'open';
    if (folded === undefined || folded !== shut) onPress(id);
  });
  useEffect(() => {
    foldHand = (id, folded) => onFold(id, folded);
    return () => {
      foldHand = null;
    };
  }, []);

  useEffect(() => {
    const on = (e: globalThis.KeyboardEvent) => onKey(e);
    document.addEventListener('keydown', on);
    return () => document.removeEventListener('keydown', on);
  }, []);

  /* tucks and moves (motion.md E1-E3, E9): lay out at the end state, then play each card's old place to its new one
     as a transform (FLIP). Drags and window resizes are not animated; Off (and the system's reduced motion) jumps. */
  useLayoutEffect(() => {
    if (mode === 'cards' || L.mode !== 'sheets' || !stretch) {
      before.current = null;
      return;
    }
    const at: Partial<Record<SheetId, Placed>> = {};
    for (const s of sheets) at[s.id] = { x: s.card?.x ?? s.x ?? 0, shut: s.state !== 'open' };
    const was = before.current;
    const why: Cause = was && was.arr !== p.arr ? 'move' : cause.current;
    before.current = { arr: p.arr, at };
    cause.current = null;
    if (!was || !why || dragging || motionLevel() === 'off' || typeof Element.prototype.animate !== 'function') return;
    const m = why === 'reset' ? SHEET_RESET : why === 'fold' ? SHEET_FOLD : SHEET_MOVE;
    const { ms: dur, easing } = m;
    for (const [id, el] of places.current) {
      const a = was.at[id];
      const b = at[id];
      if (!b) continue;
      if (!a) {
        // a sheet arriving: from 60 to the right for a second chat (E5), and a fade
        el.animate([{ transform: id === 'chat' ? 'translateX(60px)' : 'none' }, { transform: 'none' }], { duration: dur, easing });
        el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: m.arriveMs, easing: 'cubic-bezier(.2,0,0,1)' });
        continue;
      }
      if (a.x !== b.x) {
        el.classList.add('is-moving');
        el.animate([{ transform: `translateX(${a.x - b.x}px)` }, { transform: 'none' }], { duration: dur, easing }).finished.then(
          () => el.classList.remove('is-moving'),
          () => el.classList.remove('is-moving'),
        );
      }
      if (a.shut !== b.shut) {
        // tucking shows the edge; opening fades the sheet's content in at its new form (E2, E3, E4)
        const shown = b.shut ? [el.nextElementSibling].filter(e => e?.classList.contains('shell-edge')) : [...(el.firstElementChild?.children ?? [])];
        for (const c of shown) c?.animate([{ opacity: 0 }, { opacity: 1 }], { duration: m.fadeMs, delay: b.shut ? 0 : m.fadeDelayMs, easing: 'cubic-bezier(.2,0,0,1)', fill: 'backwards' });
      }
    }
  });

  if (mode === 'cards' || L.mode === 'cards' || !stretch) {
    // a call takes the screen in the chat's place (phone/notes.md)
    const call = p.arr === 'call';
    // a teammate's own page: their page at the back, their chat the card over it. Theirs and a task's chat belong to
    // the page: they wait as a lip, so the page's head is free, unless the address asks for the chat.
    const tucked = p.arr === 'agent' || p.arr === 'task';
    if (p.arr === 'agent' && p.widgets) return <PhoneCards page={p.widgets} tico={p.tico} menu={p.menu} chat={p.page} tucked lips={p.lips} lipActs={p.lipActs} />;
    return <PhoneCards page={call ? null : p.page} tico={p.tico} menu={p.menu} chat={call ? undefined : p.chat} call={call ? p.page : undefined} tucked={tucked} lips={p.lips} lipActs={p.lipActs} />;
  }

  const content = (s: SheetLayout) => {
    switch (s.id) {
      case 'tico':
        return p.tico;
      case 'menu':
        return p.menu;
      case 'list':
        return p.list ?? p.menu;
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
  const mark = (id: SheetId) =>
    id === 'tico' ? <Dot size={TICO_MARK} /> : id === 'menu' && p.lips?.menu ? <span className="shell-edge-lip">{p.lips.menu}</span> : <Icon name={EDGE_ICON[id]} />;
  const top = sheets.length + 1; // the handles sit over every sheet

  return (
    <div className="app-tico shell-frame" data-arrangement={p.arr}>
      {sheets.map((s, i) => {
        const shut = s.state !== 'open';
        const card = s.card ?? { x: s.x ?? 0, width: s.width };
        const x = s.x ?? card.x;
        const under = x - card.x;
        // the desktop window's buttons sit over the top left corner: a sheet under them moves its head right past
        // them, or, too narrow for that (an edge), starts below them (desktop.css)
        const past = Math.max(0, lights.left - x);
        const clear = past > 0 && lights.top > 0 ? (s.width - past < BESIDE_LIGHTS ? 'top' : 'left') : undefined;
        const style = { left: card.x, width: card.width, zIndex: s.z, '--depth': s.depth } as CSSProperties & Record<'--depth' | '--under' | '--lights-left', number | string>;
        if (under > 0) style['--under'] = `${under}px`;
        if (clear) style['--lights-left'] = `${past}px`;
        const controls: SheetControls = {
          id: s.id,
          state: s.state,
          peek: forward === s.id,
          folded: false,
          canFold: !!s.canFold,
          toggleFold: () => onPress(s.id),
          close: s.id === 'chat' && p.arr === 'chat' ? p.onCloseChat : s.id === 'panel' ? p.onClosePanel : undefined,
          tucked: [],
        };
        const depth = { '--depth': s.depth } as CSSProperties;
        return (
          <Fragment key={s.id}>
            <div
              ref={el => {
                if (el) places.current.set(s.id, el);
                else places.current.delete(s.id);
              }}
              className="shell-place"
              style={style}
              inert={shut}
              aria-hidden={shut || undefined}
              data-lights={clear}
              data-shut={shut || undefined}
              data-under={under > 0 || undefined}
              data-cast={s.cast || undefined}
              data-sheet={s.id}
              data-target={`sheet:${s.id}`}
            >
              <SheetContext value={controls}>{content(s)}</SheetContext>
            </div>
            {shut ? (
              <button
                className="shell-edge"
                type="button"
                style={{ ...depth, left: x, zIndex: s.z }}
                data-first={i === 0 || undefined}
                data-lights={clear}
                data-edge={s.id}
                aria-label={`Open ${NAME[s.id]}`}
                title={`Open ${NAME[s.id]}`}
                onClick={() => onPress(s.id)}
              >
                <span className="shell-edge-mark">{mark(s.id)}</span>
                <span className="shell-edge-name">{EDGE_NAME[s.id]}</span>
              </button>
            ) : null}
          </Fragment>
        );
      })}
      {sheets
        .filter((s, i) => {
          if (s.id === stretch.id || !s.canDrag || s.x == null) return false;
          // a handle only in a gap: between this sheet and its open neighbour on the stretching sheet's side
          const right = i > sheets.findIndex(o => o.id === stretch.id);
          return sheets[right ? i - 1 : i + 1]?.state === 'open';
        })
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
              aria-valuemin={s.min}
              aria-valuemax={s.dragMax}
              style={{ left: (right ? x - C.GAP : x + s.width) - 4, zIndex: top }}
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
