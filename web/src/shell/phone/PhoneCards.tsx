/* The phone: the page, three cards that can stand over it (Tico, the menu, an open chat) and one bar of icons at the
   bottom: Search · Tico · Company · Agent. Company is the page you are on; Agent is the open chat, there only while one
   is. A task opens over all of it (the task modal). Which card is up is device state: a tap on the bar changes it and
   adds nothing to the history, so Back is navigation only. */
import { useRouter } from '@tanstack/react-router';
import { createContext, use, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, MouseEvent, ReactNode } from 'react';
import { DockSlot } from '@/kit/Dock';
import { Icon } from '@/kit/Icon';
import { coldOf, openOf, stuckToBottom, watchKeyboard, type Keys } from '@/lib';
import { Dot } from '@/tico/dot';
import { TICO_MARK } from '@/shell/layout';
import './phone.css';

/** A link that opens a task over the page (`?task=`, or a task's own address), not a page of its own. */
const opensTask = (a: HTMLAnchorElement): boolean => {
  const search = Object.fromEntries(new URLSearchParams(a.search));
  return !!(openOf(search) ?? coldOf(a.pathname, search));
};

export type PhoneCard = 'none' | 'tico' | 'menu' | 'chat';
type Slot = Exclude<PhoneCard, 'none'>;

export interface PhoneCardsProps {
  /** The page that fills the screen above the bar. */
  page: ReactNode;
  /** Tico's card (head with `.tico-head`, chat, `.chat-foot`). */
  tico: ReactNode;
  /** The menu's card. */
  menu: ReactNode;
  /** An open chat: the task's agent, a teammate's DM, a side thread or the page owner's. The Agent icon shows with it. */
  chat?: ReactNode;
  /** What the Company and Agent icons show: the company's mark, the chat's face. */
  faces?: { company?: ReactNode; chat?: ReactNode };
  /** The chat's name, read by screen readers ("Finance Owner"). */
  labels?: { chat?: string };
  /** The chat belongs to the page (an agent's own, the page owner's): it waits behind its icon unless the address names
      a chat (`chat=`) or the person raises it. */
  tucked?: boolean;
  /** Whose chat it is (the Agent bar's page key): when it changes, the chat card is set again from `tucked` and the
      address, even when they are the same as before. A card put down by hand stays down until then. */
  chatKey?: string;
  /** Search, from the bar. */
  onSearch?: () => void;
  onCardChange?: (card: PhoneCard) => void;
}

export interface PhoneCardsControls {
  card: PhoneCard;
  raise: (card: Slot) => void;
  tuck: () => void;
}

const Ctx = createContext<PhoneCardsControls | null>(null);
/** Inside the phone cards: which card is up, and a way to raise or tuck one (a close button, Tico dropping its card
    before it changes the page). Null on a desktop layout. */
export const usePhoneCards = () => use(Ctx);

/* The shell's fold for a sheet (Frame `foldSheet`, the `shell.fold` action) while the phone's cards are drawn: Tico's
   card comes up or goes down. Other sheets have no card of their own here. */
let foldHand: ((sheet: string, folded?: boolean) => void) | null = null;
export function foldPhoneCard(sheet: string, folded?: boolean): void {
  foldHand?.(sheet, folded);
}

/* A field the keyboard types into. A checkbox, a radio or a select takes focus too, but brings no keys up, so the bar
   stays for them. An input's `type` property is the normalized one: an empty or unknown attribute reads as "text". */
const TEXT_INPUTS = new Set(['text', 'search', 'email', 'url', 'tel', 'password', 'number']);
const typedIn = (f: HTMLElement) =>
  f instanceof HTMLTextAreaElement ||
  (f instanceof HTMLInputElement && TEXT_INPUTS.has(f.type)) ||
  f.matches('[contenteditable]:not([contenteditable="false"])');

/** A chat's scroller to its newest message. A virtual list draws and measures the rows it comes to, and grows as it
    does, so it is put at its end again on each frame until it stays there. */
function toLatest(scrollers: HTMLElement[], frames = 12): void {
  let moved = false;
  for (const s of scrollers)
    if (s.scrollHeight - s.scrollTop - s.clientHeight > 1) {
      s.scrollTop = s.scrollHeight;
      moved = true;
    }
  if (moved && frames > 1) requestAnimationFrame(() => toLatest(scrollers, frames - 1));
}

/** One icon on the bar: no words, its name read by screen readers; the one for what is on screen is marked current. */
function Tab({ name, label, on = false, onClick, children }: { name: string; label: string; on?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button className={`tab tab-${name}`} type="button" aria-label={label} title={label} aria-current={on || undefined} onClick={onClick}>
      {children}
    </button>
  );
}

export function PhoneCards({ page, tico, menu, chat, faces = {}, labels = {}, tucked = false, chatKey, onSearch, onCardChange }: PhoneCardsProps) {
  const router = useRouter({ warn: false });
  // a chat that opens comes up and one that ends goes down. An address that already names one (a shared link, a
  // reload) opens on it. A page's own chat (`tucked`) waits behind its icon unless the address names a chat.
  const second = !!chat;
  const named = () => /[?&]chat=/.test(router ? router.state.location.searchStr : window.location.search);
  const comesUp = () => second && (!tucked || named());
  const [card, setCard] = useState<PhoneCard>(() => (comesUp() ? 'chat' : 'none'));
  const root = useRef<HTMLDivElement>(null);
  const [dock, setDock] = useState<HTMLElement | null>(null);

  const [seen, setSeen] = useState(second);
  const [wasTucked, setWasTucked] = useState(tucked);
  const [wasKey, setWasKey] = useState(chatKey);
  if (seen !== second) {
    setSeen(second);
    if (comesUp()) setCard('chat');
    else if (card === 'chat') setCard('none');
  } else if ((wasTucked !== tucked || wasKey !== chatKey) && second) {
    // a page's own chat follows its page both ways: one that stops waiting behind its icon comes up (its fold is open,
    // an ask opened it on a narrow computer, the address names it), and one that waits again goes down
    if (comesUp()) setCard('chat');
    else if (card === 'chat') setCard('none');
  }
  if (wasTucked !== tucked) setWasTucked(tucked);
  if (wasKey !== chatKey) setWasKey(chatKey);

  // ---------- focus and the callback: everything that follows a change of card ----------
  const was = useRef<PhoneCard>('none');
  useEffect(() => {
    const prev = was.current;
    if (prev === card) return;
    was.current = card;
    onCardChange?.(card);
    const el = root.current;
    if (!el) return;
    const up = card === 'none' ? null : el.querySelector<HTMLElement>(`:scope > .ly-${card}`);
    // a card that came up with the cursor already in it (Tico's box, from the bar) keeps it there
    if (up && !up.contains(document.activeElement)) up.focus({ preventScroll: true });
    else if (!up && prev !== 'none' && el.querySelector(`:scope > .ly-${prev}`)?.contains(document.activeElement))
      el.querySelector<HTMLElement>(':scope > .tabbar [aria-current]')?.focus({ preventScroll: true });
  }, [card, onCardChange]);

  const raise = (next: Slot) => setCard(next);
  const tuck = () => setCard('none');
  useEffect(() => {
    foldHand = (sheet, folded) => {
      if (sheet !== 'tico') return;
      setCard((now) => {
        const up = folded === undefined ? now !== 'tico' : !folded;
        return up ? 'tico' : now === 'tico' ? 'none' : now;
      });
    };
    return () => {
      foldHand = null;
    };
  }, []);
  /* Tico from the bar: its card comes up at its latest message with the cursor in its box. iOS raises the keyboard
     only for a focus() inside the tap's own handler, so the box (drawn all along, out of reach while the card is down)
     is let out and focused here, before the card moves. */
  const typeToTico = () => {
    const ly = root.current?.querySelector<HTMLElement>(':scope > .ly-tico');
    const box = ly?.querySelector<HTMLElement>('.composer textarea, textarea, [contenteditable="true"]');
    if (ly && box) {
      ly.inert = false;
      ly.classList.add('is-up'); // seen, or the focus does not take
      box.focus({ preventScroll: true });
    }
    toLatest([...(ly?.querySelectorAll<HTMLElement>('[data-scroll], .chat-body') ?? [])]);
    raise('tico');
  };
  // Company: from a card, back to the page; on the page, the menu (and the menu back to the page)
  const toCompany = () => setCard(card === 'none' ? 'menu' : 'none');

  // ---------- the keyboard: the card being typed in rides it and keeps its last line in view ----------
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    let keys: Keys = { kb: 0, top: 0, up: false };
    // the field a keyboard was measured up for: once those keys go down with the field still focused, typing is over
    let raisedFor: Element | null = null;
    let blur = 0;
    let frame = 0;
    const update = () => {
      const active = document.activeElement;
      const focused = active instanceof HTMLElement && el.contains(active) && typedIn(active);
      if (!focused) raisedFor = null;
      else if (keys.up) raisedFor = active;
      else if (raisedFor !== active) raisedFor = null;
      // typing is the focus alone: some iPhones never report the keyboard through visualViewport (#3562), and the bar
      // must still go. Where the keyboard was measured up for this field, its closing ends typing even though the field
      // keeps the focus. The measurement only lifts what is typed in above the keys.
      const closed = focused && raisedFor === active && !keys.up;
      const inside = focused && !closed;
      const ly = inside ? active.closest<HTMLElement>('.ly[data-slot], .ph-dock') : null;
      const where = inside ? (ly?.dataset.slot ?? 'page') : null;
      const lift = where && keys.up;
      const holder = where === 'page' ? el.querySelector(':scope > .pg') : ly;
      const stuck = stuckToBottom(holder);
      if (where) el.dataset.typing = where;
      else delete el.dataset.typing;
      el.style.setProperty('--kb', `${lift ? keys.kb : 0}px`);
      el.style.setProperty('--vv-top', `${lift ? keys.top : 0}px`);
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => stuck.forEach((s) => (s.scrollTop = s.scrollHeight)));
    };
    const stop = watchKeyboard((k) => {
      keys = k;
      update();
    });
    const later = () => {
      raisedFor = null; // a field focused again starts a new session, whatever its last keyboard did
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

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape' && !e.defaultPrevented && card !== 'none') {
      e.preventDefault();
      tuck();
    }
  };

  // a page opened from a card (a place in the menu, a page linked in a chat): the card goes down and the page comes
  // forward. A task (an approval card, a tracked task in Tico's) opens over everything, the card left as it is for its
  // Close; a link marked `data-stay` changes what the card holds, so it stays up too.
  const onCardClick = (e: MouseEvent<HTMLElement>) => {
    const a = (e.target as HTMLElement).closest<HTMLAnchorElement>('a[href]');
    if (!a || a.target || a.origin !== location.origin || a.closest('[data-stay]')) return;
    if (opensTask(a)) return;
    tuck();
  };

  const cardOf = (s: Slot, content: ReactNode, label: string) => {
    const up = card === s;
    return (
      <section
        className={`ly ly-${s}${up ? ' is-up' : ''}`}
        data-slot={s}
        role={up ? 'dialog' : undefined}
        aria-label={label}
        tabIndex={up ? -1 : undefined}
        inert={!up}
        onClickCapture={onCardClick}
      >
        {content}
      </section>
    );
  };

  return (
    <Ctx value={{ card, raise, tuck }}>
      <DockSlot value={dock}>
        <div ref={root} className="phone" data-card={card} onKeyDown={onKeyDown}>
          <div className="pg" inert={card !== 'none'}>
            {page}
          </div>
          {chat ? cardOf('chat', chat, labels.chat ?? 'Agent') : null}
          {cardOf('menu', menu, 'Menu')}
          {cardOf('tico', tico, 'Tico')}
          {/* what holds the bottom (a task's decision, kit Dock): above the bar, outside the page and the cards, so it is
              in reach with the chat up */}
          <div className="ph-dock" data-slot="dock" ref={setDock} />
          <nav className="tabbar" aria-label="Bar">
            <Tab name="search" label="Search" onClick={() => onSearch?.()}>
              <Icon name="search" />
            </Tab>
            <Tab name="tico" label="Tico" on={card === 'tico'} onClick={typeToTico}>
              <Dot size={TICO_MARK} />
            </Tab>
            <Tab name="company" label="Company" on={card === 'none' || card === 'menu'} onClick={toCompany}>
              {faces.company ?? <Icon name="company" />}
            </Tab>
            {chat ? (
              <Tab name="agent" label={labels.chat ?? 'Agent'} on={card === 'chat'} onClick={() => raise('chat')}>
                {faces.chat ?? <Icon name="message" />}
              </Tab>
            ) : null}
          </nav>
        </div>
      </DockSlot>
    </Ctx>
  );
}
