/* The phone's layered cards (B/phone/notes.md is the model; motion.md section 6 the gestures; platforms.md 2 the
   Safari rules). The page is the back card. Tico, the menu and an open chat (or a call) rest as lips at the bottom
   and come up over the page by tap, Enter or a drag. The cards behind a raised one come up with it and keep a peek of
   their top edge above it, in order, so the next layer is one tap away. Which card is up is device state, but each
   raised card owns a history entry so Back closes it. Everything that follows a finger is written straight to the DOM, never through
   React state, so a drag or a scroll costs no render. */
import { useRouter } from '@tanstack/react-router';
import { createContext, use, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { KeyboardEvent, MouseEvent, PointerEvent, ReactNode } from 'react';
import { Dot } from '@/tico/dot';
import { TICO_MARK_LIP } from '@/shell/layout';
import { watchKeyboard, stuckToBottom } from './keyboard';
import type { Keys } from './keyboard';
import { GESTURE, pickStop, progress, rubber, velocity } from './stops';
import type { Sample } from './stops';
import './phone.css';

export type PhoneCard = 'none' | 'tico-half' | 'tico' | 'menu' | 'chat';
type Slot = 'tico' | 'menu' | 'chat';

export interface PhoneCardsProps {
  /** The back card: the page that fills the screen. */
  page: ReactNode;
  /** Tico's card when up (head with `.tico-head`, chat, `.tico-foot`). */
  tico: ReactNode;
  /** The menu's card when up. */
  menu: ReactNode;
  /** An open second chat. It comes up when it opens and rests as a lip behind the menu. */
  chat?: ReactNode;
  /** A live call. It takes the screen when it starts and becomes a lip when flicked down. Takes the chat's place. */
  call?: ReactNode;
  /** What each lip shows. Defaults: "Message Tico", "Menu", the chat's or the call's name. */
  lips?: Partial<Record<'tico' | 'menu' | 'chat' | 'call', ReactNode>>;
  /** A control on a lip that is not part of its tap target: "Join" on Tico's, mute on a call's. */
  lipActs?: Partial<Record<'tico' | 'chat' | 'call', ReactNode>>;
  /** Tico speaking while tucked: up to two lines on its lip. */
  peek?: ReactNode;
  /** The page holds the bottom itself (a task that waits on you): the stack draws in to Tico's edge under it. */
  handle?: boolean;
  /** Names for the chat and call cards, read by screen readers ("Finance Owner", "Seed box pricing"). */
  labels?: { chat?: string; call?: string };
  /** The chat belongs to the page (a task's, an agent's own): it rests as a lip, so the page's head can be used, and
      comes up only when the address names a chat (`chat=`) or the person raises it. */
  tucked?: boolean;
  onCardChange?: (card: PhoneCard) => void;
}

export interface PhoneCardsControls {
  card: PhoneCard;
  raise: (card: Exclude<PhoneCard, 'none'>) => void;
  tuck: () => void;
}

const Ctx = createContext<PhoneCardsControls | null>(null);
/** Inside the phone cards: which card is up, and a way to raise or tuck one (a close button, Tico dropping its card
    before it changes the page). Null on a desktop layout. */
export const usePhoneCards = () => use(Ctx);

const KEY = 'tico-next:card';
const GRAB = '.lip,.nub,.grab,.tico-head,.m-head,.c-head,.ly-chat .side-head,.call-head,[data-grab]';
const PRESSABLE = 'button,a,input,textarea,select,label,[contenteditable],[role="button"],[role="tab"],[role="link"]';
const EDITABLE = 'input,textarea,select,[contenteditable]:not([contenteditable="false"])';
const LIP = { tico: 58, peek: 84, menu: 38, chat: 38 } as const;
/** Back to front. A card behind the raised one keeps a peek of its top edge above it (phone.css --peek). */
const ORDER: readonly Slot[] = ['chat', 'menu', 'tico'];
const PEEK = 18;
const SCROLL = { start: 20, draw: 48, dead: 2, idle: 120 } as const;

const slotOf = (card: PhoneCard): Slot | null => (card === 'none' ? null : card === 'menu' || card === 'chat' ? card : 'tico');
const historyCard = (state: unknown): PhoneCard | undefined =>
  state && typeof state === 'object' && KEY in state ? ((state as Record<string, PhoneCard>)[KEY] ?? undefined) : undefined;

/** A card behind the one a finger holds. Taken up, the held card pushes it along `depth` peeks above; taken down, it
    follows at the gap it began with. */
type Rider = { el: HTMLElement; depth: number; top: number; y0: number };

type Drag = {
  slot: Slot | 'nub';
  card: HTMLElement | null;
  top: number;
  riders: Rider[];
  id: number;
  x0: number;
  y0: number;
  owned: boolean;
  ty0: number;
  ty: number;
  stops: number[];
  cards: PhoneCard[];
  start: number;
  size: number;
  samples: Sample[];
};

export function PhoneCards({ page, tico, menu, chat, call, lips = {}, lipActs = {}, peek, handle = false, labels = {}, tucked = false, onCardChange }: PhoneCardsProps) {
  const router = useRouter({ warn: false });
  // a chat that opens comes up; a call that starts takes over; either one ending tucks its card. An address that
  // already names one (a shared link, a reload) opens on it. A page's own chat (`tucked`) waits as a lip unless the
  // address names a chat.
  const second = call ? 'call' : chat ? 'chat' : null;
  const named = () => /[?&]chat=/.test(router ? router.state.location.searchStr : window.location.search);
  const comesUp = (s: typeof second) => s === 'call' || (s === 'chat' && (!tucked || named()));
  const [card, setCard] = useState<PhoneCard>(() => (comesUp(second) ? 'chat' : 'none'));
  const root = useRef<HTMLDivElement>(null);
  const id = useId();

  const [seen, setSeen] = useState(second);
  if (seen !== second) {
    setSeen(second);
    if (second === 'call' || (second === 'chat' && !seen && comesUp(second))) setCard('chat');
    else if (card === 'chat') setCard('none');
  }

  const isUp = (s: Slot) => slotOf(card) === s;
  const raised = slotOf(card);
  // the cards behind the raised one that are there to show: Tico's has the menu and an open chat, the menu's the chat
  const behindOf = (front: Slot | null): Slot[] => (front ? ORDER.slice(0, ORDER.indexOf(front)).filter((s) => s !== 'chat' || !!second) : []);
  const behind = behindOf(raised);
  const handleMode = handle || card === 'chat';
  const back = card === 'menu' || card === 'tico' || (card === 'chat' && second !== 'call');
  const peeking = !!peek && card === 'none';
  const label = { tico: 'Tico', menu: 'Menu', chat: second === 'call' ? (labels.call ?? 'Call') : (labels.chat ?? 'Chat') };

  // ---------- history, focus and the callback: everything that follows a change of card ----------
  const was = useRef<PhoneCard>('none');
  const fromHistory = useRef(false);
  const navigating = useRef(false);
  const cardNow = useRef(card);
  useLayoutEffect(() => {
    cardNow.current = card;
  });
  // declared before the effect below so it runs first: a reload keeps the entry but not the card, so a stale mark is
  // dropped (a tuck must never leave the page). A mark for the card the page opened on (a chat address) is kept.
  useEffect(() => {
    const marked = historyCard(window.history.state);
    if (marked && marked !== cardNow.current) {
      const rest = { ...(window.history.state as Record<string, unknown>) };
      delete rest[KEY];
      window.history.replaceState(rest, '');
    }
    const onPop = (e: PopStateEvent) => {
      const next = historyCard(e.state) ?? 'none';
      if (next === cardNow.current) return;
      fromHistory.current = true;
      setCard(next);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);
  const opened = useRef(false);
  useEffect(() => {
    const first = !opened.current;
    opened.current = true;
    const prev = was.current;
    if (prev === card) return;
    was.current = card;
    const restoring = fromHistory.current;
    const leaving = navigating.current;
    fromHistory.current = false;
    navigating.current = false;
    if (!restoring) {
      // The router writes a navigation to the address a moment later (a microtask), and it rereads the address on any
      // other write. A card that comes up with that navigation (a chat opened from a menu sheet) would write first
      // and take the router back to the old address, so the router's own write goes first.
      router?.history.flush();
      const state = (window.history.state ?? {}) as Record<string, unknown>;
      // the card the page opened on, after a reload that kept its mark: that entry is already its own
      const reopened = first && historyCard(state) === card;
      if (card !== 'none' && prev === 'none' && !reopened) window.history.pushState({ ...state, [KEY]: card }, '');
      else if (card !== 'none') window.history.replaceState({ ...state, [KEY]: card }, '');
      // a link in the menu pushes its own entry; going back here would race it. Back later returns to the menu.
      else if (historyCard(state) && !leaving) window.history.back();
    }
    onCardChange?.(card);
    const el = root.current;
    if (!el) return;
    const prevSlot = slotOf(prev);
    const nextSlot = slotOf(card);
    if (nextSlot && nextSlot !== prevSlot) el.querySelector<HTMLElement>(`.ly-${nextSlot}`)?.focus({ preventScroll: true });
    else if (!nextSlot && prevSlot) {
      const from = el.querySelector(`.ly-${prevSlot}`);
      if (from?.contains(document.activeElement)) from.querySelector<HTMLElement>(':scope > .lip')?.focus({ preventScroll: true });
    }
  }, [card, onCardChange, router]);

  const raise = (next: Exclude<PhoneCard, 'none'>) => setCard(next);
  const tuck = () => setCard('none');
  const tapLip = (s: Slot) => (s === 'tico' ? raise(card === 'tico-half' ? 'tico' : 'tico-half') : raise(s));

  // ---------- the lips draw down while the page is read downward (motion.md section 6) ----------
  const c = useRef(0);
  useEffect(() => {
    const el = root.current;
    const pg = el?.querySelector<HTMLElement>(':scope > .pg');
    if (!el || !pg) return;
    const last = new WeakMap<EventTarget, number>();
    let idle = 0;
    let raf = 0;
    const put = (v: number, follow: boolean) => {
      c.current = v;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        el.toggleAttribute('data-scrolling', follow);
        el.style.setProperty('--c', String(v));
      });
    };
    const onScroll = (e: Event) => {
      const t = e.target as HTMLElement;
      if (!(t instanceof HTMLElement) || cardNow.current !== 'none') return;
      const y = t.scrollTop;
      const dy = y - (last.get(t) ?? y);
      last.set(t, y);
      clearTimeout(idle);
      // at its end a page has nothing more to read (a timeline opens there and follows what arrives): the lips are back
      if (y < SCROLL.start || dy < -SCROLL.dead || t.scrollHeight - y - t.clientHeight < 1) {
        if (c.current > 0) put(0, false);
        return;
      }
      if (dy > 0) put(Math.min(1, c.current + dy / SCROLL.draw), true);
      idle = window.setTimeout(() => {
        if (c.current > 0 && c.current < 1) put(c.current >= 0.5 ? 1 : 0, false);
        else el.removeAttribute('data-scrolling');
      }, SCROLL.idle);
    };
    pg.addEventListener('scroll', onScroll, { capture: true, passive: true });
    // another page starts with the lips up: one too short to scroll could never bring them back
    const off = router?.subscribe('onResolved', (ev) => {
      if (ev.pathChanged && c.current > 0) put(0, false);
    });
    return () => {
      off?.();
      pg.removeEventListener('scroll', onScroll, { capture: true });
      clearTimeout(idle);
      cancelAnimationFrame(raf);
    };
  }, [router]);

  // ---------- the keyboard: the card being typed in rides it and keeps its last line in view ----------
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    let keys: Keys = { kb: 0, top: 0, up: false };
    let blur = 0;
    let frame = 0;
    const update = () => {
      const active = document.activeElement;
      const inside = active instanceof HTMLElement && el.contains(active) && active.matches(EDITABLE);
      const where = !inside || !keys.up ? null : active.closest('.ly-tico') ? 'tico' : active.closest('.ly-menu') ? 'menu' : active.closest('.ly-chat') ? 'chat' : 'page';
      const holder = where === 'page' ? el.querySelector(':scope > .pg') : where ? el.querySelector(`:scope > .ly-${where}`) : null;
      const stuck = stuckToBottom(holder);
      if (where) el.dataset.typing = where;
      else delete el.dataset.typing;
      el.style.setProperty('--kb', `${where ? keys.kb : 0}px`);
      el.style.setProperty('--vv-top', `${where ? keys.top : 0}px`);
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => stuck.forEach((s) => (s.scrollTop = s.scrollHeight)));
    };
    const stop = watchKeyboard((k) => {
      keys = k;
      update();
    });
    const later = () => {
      clearTimeout(blur);
      blur = window.setTimeout(update, 50);
    };
    el.addEventListener('focusin', update);
    el.addEventListener('focusout', later);
    return () => {
      stop();
      clearTimeout(blur);
      cancelAnimationFrame(frame);
      el.removeEventListener('focusin', update);
      el.removeEventListener('focusout', later);
    };
  }, []);

  // ---------- drag: the card follows the finger, release picks a stop by distance and speed ----------
  const drag = useRef<Drag | null>(null);
  const dragged = useRef(false);

  const stopsFor = (slot: Slot, el: HTMLElement): { stops: number[]; cards: PhoneCard[] } => {
    const r = root.current!;
    const size = el.offsetHeight;
    const sab = r.querySelector<HTMLElement>(':scope > .ph-sab')?.offsetHeight ?? 0;
    const gap = parseFloat(getComputedStyle(r).getPropertyValue('--peek')) || PEEK;
    // a scroll draws the back lips down to their peek
    const draw = (LIP.menu - gap) * c.current;
    const lipT = peeking ? LIP.peek : LIP.tico;
    // raised, a card stands below the peeks of the cards behind it
    const top = behindOf(slot).length * gap;
    if (slot === 'tico') {
      const half = parseFloat(getComputedStyle(r).getPropertyValue('--half')) || 410;
      return { stops: [top, size - half, size - sab - lipT], cards: ['tico', 'tico-half', 'none'] };
    }
    const menuLip = LIP.menu - draw;
    const lip = slot === 'menu' ? size - sab - lipT - menuLip : size - sab - lipT - menuLip - (LIP.chat - draw);
    return { stops: [top, lip], cards: [slot, 'none'] };
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || drag.current) return;
    const target = e.target as HTMLElement;
    const zone = target.closest<HTMLElement>(GRAB);
    if (!zone || !root.current?.contains(zone)) return;
    const isHead = !zone.matches('.lip,.nub,.grab');
    const pressed = target.closest(PRESSABLE);
    if (isHead && pressed && zone.contains(pressed)) return;
    const el = zone.closest<HTMLElement>('.ly');
    // a peek behind the raised card is a tap target only: a sloppy tap must not drag the stack down
    if (el?.classList.contains('is-behind')) return;
    const slot: Slot | 'nub' = zone.matches('.nub') ? 'nub' : el?.classList.contains('ly-tico') ? 'tico' : el?.classList.contains('ly-menu') ? 'menu' : 'chat';
    if (isHead && !el?.classList.contains('is-up')) return;
    let stops: number[] = [];
    let cards: PhoneCard[] = [];
    let ty0 = 0;
    const ty = (n: HTMLElement) => new DOMMatrixReadOnly(getComputedStyle(n).transform).m42; // mid-settle, take it where it is
    const riders: Rider[] = [];
    if (el && slot !== 'nub') {
      ({ stops, cards } = stopsFor(slot, el));
      ty0 = ty(el);
      behindOf(slot).forEach((s, i, all) => {
        const b = root.current?.querySelector<HTMLElement>(`:scope > .ly-${s}`);
        if (b) riders.push({ el: b, depth: all.length - i, top: b.offsetTop, y0: b.offsetTop + ty(b) });
      });
    }
    const at = cards.indexOf(card);
    drag.current = {
      slot,
      card: el,
      top: el?.offsetTop ?? 0,
      riders,
      id: e.pointerId,
      x0: e.clientX,
      y0: e.clientY,
      owned: false,
      ty0,
      ty: ty0,
      stops,
      cards,
      start: at < 0 ? cards.length - 1 : at,
      size: el?.offsetHeight ?? 0,
      samples: [{ t: e.timeStamp, y: e.clientY }],
    };
    zone.setPointerCapture?.(e.pointerId);
  };

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    const r = root.current;
    if (!d || !r || e.pointerId !== d.id) return;
    const dx = e.clientX - d.x0;
    const dy = e.clientY - d.y0;
    if (!d.owned) {
      if (Math.abs(dx) > GESTURE.sideCancel && Math.abs(dx) >= Math.abs(dy)) {
        drag.current = null;
        return;
      }
      if (Math.abs(dy) <= GESTURE.own || Math.abs(dy) <= Math.abs(dx)) return;
      d.owned = true;
      if (d.slot !== 'nub') {
        if (r.dataset.typing) (document.activeElement as HTMLElement | null)?.blur(); // a drag puts the keyboard away first
        r.dataset.drag = d.slot;
      }
    }
    d.samples.push({ t: e.timeStamp, y: e.clientY });
    if (d.samples.length > 12) d.samples.shift();
    if (d.slot === 'nub' || !d.card) return;
    const top = d.stops[0] ?? 0;
    const lip = d.stops[d.stops.length - 1] ?? 0;
    d.ty = rubber(d.ty0 + dy, top, lip, d.size);
    d.card.style.transform = `translateY(${d.ty}px)`;
    // the cards behind come up with it, a peek above each other; taken down, they follow at the gap they had
    const gap = parseFloat(getComputedStyle(r).getPropertyValue('--peek')) || PEEK;
    const y = d.top + d.ty;
    for (const b of d.riders) {
      const at = d.ty <= d.ty0 ? Math.min(b.y0, y - b.depth * gap) : y + b.y0 - (d.top + d.ty0);
      b.el.style.transform = `translateY(${at - b.top}px)`;
    }
    const half = d.slot === 'tico' ? (d.stops[1] ?? lip) : lip;
    const takeover = d.slot === 'chat' && second === 'call';
    r.style.setProperty('--p', String(takeover ? 0 : progress(Math.max(d.ty, top), half, top)));
    r.style.setProperty('--f', String(progress(d.ty, lip, top, 24, 60)));
  };

  const endDrag = (e: PointerEvent<HTMLDivElement>, cancelled: boolean) => {
    const d = drag.current;
    const r = root.current;
    if (!d || !r || e.pointerId !== d.id) return;
    drag.current = null;
    if (!d.owned) return;
    dragged.current = true;
    d.samples.push({ t: e.timeStamp, y: e.clientY });
    const v = velocity(d.samples);
    const dy = e.clientY - d.y0;
    let next: PhoneCard = card;
    if (d.slot === 'nub') {
      if (!cancelled && (dy < -GESTURE.drag || v < -GESTURE.flick)) next = 'tico-half';
    } else if (!cancelled) {
      next = d.cards[pickStop({ stops: d.stops, start: d.start, ty: d.ty, dy, v })] ?? card;
    }
    // hand the card back to CSS in this same frame, so its transition runs from where the finger left it
    if (d.card) d.card.style.transform = '';
    for (const b of d.riders) b.el.style.transform = '';
    r.style.removeProperty('--p');
    r.style.removeProperty('--f');
    delete r.dataset.drag;
    if (next !== card) setCard(next);
  };

  const onClickCapture = (e: { preventDefault(): void; stopPropagation(): void }) => {
    if (!dragged.current) return;
    dragged.current = false;
    e.preventDefault();
    e.stopPropagation();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape' && !e.defaultPrevented && card !== 'none') {
      e.preventDefault();
      tuck();
    }
  };

  // a place picked in the menu: the menu tucks and the page comes forward. The menu's history entry gives way to the
  // page it opens (a replace, before the link's own push), so Back goes to the page before, not to a stranded menu.
  const onMenuClick = (e: MouseEvent<HTMLDivElement>) => {
    const a = (e.target as HTMLElement).closest<HTMLAnchorElement>('a[href]');
    if (!a) return;
    navigating.current = true;
    const plain = e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey && !a.target && a.origin === location.origin;
    if (router && plain && historyCard(window.history.state)) {
      e.preventDefault();
      e.stopPropagation();
      void router.navigate({ href: a.pathname + a.search + a.hash, replace: true });
    }
    tuck();
  };

  // a page opened from the chat (an approval card, plan/layers.html): the chat tucks and the page comes forward. Its own
  // entry stays, so Back (the page's ✕ or back arrow) brings the chat up again.
  const onChatClick = (e: MouseEvent<HTMLDivElement>) => {
    const a = (e.target as HTMLElement).closest<HTMLAnchorElement>('a[href]');
    if (!a || a.target || a.origin !== location.origin) return;
    navigating.current = true;
    tuck();
  };

  const cls = ['phone', second && 'has-chat', peeking && 'is-peek', handleMode && 'is-handle', back && 'is-back', second === 'call' && 'is-call'].filter(Boolean).join(' ');
  const nubOn = handleMode && slotOf(card) !== 'tico';
  // a card that cannot be seen takes no focus: focusing it would scroll the stack and strand the cards mid-screen.
  // Out of view: below the screen (no chat open, or the page holds the bottom). A card behind a raised one peeks.
  const away = (s: Slot) => (s === 'chat' && !second) || (handleMode && !isUp(s) && !behind.includes(s));

  const cardOf = (s: Slot, full: ReactNode, lip: ReactNode, act: ReactNode, extra?: ReactNode) => {
    const up = isUp(s);
    return (
      <section
        className={`ly ly-${s}${up ? ' is-up' : behind.includes(s) ? ' is-behind' : ''}`}
        role={up ? 'dialog' : undefined}
        aria-label={label[s]}
        tabIndex={up ? -1 : undefined}
        inert={away(s)}
      >
        {extra}
        <i className="grab" aria-hidden="true" />
        <button
          className="lip"
          type="button"
          aria-label={`Open ${label[s]}`}
          aria-expanded={up}
          aria-controls={`${id}-${s}`}
          tabIndex={up ? -1 : undefined}
          aria-hidden={up || undefined}
          onClick={() => tapLip(s)}
        >
          {lip}
        </button>
        {act && <div className="lip-act">{act}</div>}
        <div className="full" id={`${id}-${s}`} inert={!up} onClickCapture={s === 'menu' ? onMenuClick : s === 'chat' && second === 'chat' ? onChatClick : undefined}>
          {full}
        </div>
      </section>
    );
  };

  const secondLip = second === 'call' ? (lips.call ?? <b className="lip-t">{label.chat}</b>) : (lips.chat ?? <b className="lip-t">{label.chat}</b>);

  return (
    <Ctx value={{ card, raise, tuck }}>
      <div
        ref={root}
        className={cls}
        data-card={card}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={(e) => endDrag(e, false)}
        onPointerCancel={(e) => endDrag(e, true)}
        onClickCapture={onClickCapture}
        onKeyDown={onKeyDown}
      >
        <div className="pg" inert={back}>
          {page}
        </div>
        <div className="pg-tuck" aria-hidden="true" onClick={tuck} />
        {cardOf('chat', call ?? chat, secondLip, second === 'call' ? lipActs.call : lipActs.chat)}
        {cardOf('menu', menu, lips.menu ?? <span className="lip-t">Menu</span>, null)}
        {cardOf(
          'tico',
          tico,
          peeking ? <span className="lip-say">{peek}</span> : (lips.tico ?? <span className="lip-t">Message Tico</span>),
          lipActs.tico,
          // Tico's one Dot on a phone, lip and head alike: it follows Tico, as the desktop head's does.
          <span className="tico-dot" aria-hidden="true">
            <Dot size={TICO_MARK_LIP} />
          </span>,
        )}
        {/* Tico drawn in to its edge under whatever holds the bottom: the stack's last line, never on a field */}
        <button className="nub" type="button" aria-label="Open Tico" inert={!nubOn} aria-hidden={!nubOn || undefined} onClick={() => raise('tico-half')}>
          <Dot state="rest" size={14} />
        </button>
        <i className="ph-sab" aria-hidden="true" />
      </div>
    </Ctx>
  );
}
