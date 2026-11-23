/* The phone: the page, three cards that can stand over it (Tico, the menu, an open chat) and one bar of icons at the
   bottom: Search · Tico · Company · Agent · Task. Company is the page you are on (on a task, the way back to where it
   was opened from); Agent is the open chat, there only while one is; Task is the open task. Which card is up is device
   state: a tap on the bar changes it and adds nothing to the history, so Back is navigation only. */
import { useRouter } from '@tanstack/react-router';
import { createContext, use, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, MouseEvent, ReactNode } from 'react';
import { DockSlot } from '@/kit/Dock';
import { Icon } from '@/kit/Icon';
import { Dot } from '@/tico/dot';
import { TICO_MARK } from '@/shell/layout';
import { watchKeyboard, stuckToBottom } from './keyboard';
import type { Keys } from './keyboard';
import './phone.css';

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
  /** The chat belongs to the page (a task's, an agent's own): it waits behind its icon unless the address names a chat
      (`chat=`), `raiseChat` holds, or the person raises it. */
  tucked?: boolean;
  /** The chat is where the page waits on the person (a ❓ for them in a task's chat, the chat tab's address): it comes
      up when the page opens and whenever this turns true. Turning false never puts it down. */
  raiseChat?: boolean;
  /** The ❓s that wait on the person in the chat (their ids, by spaces): each new one brings the chat up, even one put
      down by hand at the same address. */
  asked?: string;
  /** Whose chat it is (the Agent bar's page key; an open task and the chat its address names): when it changes, the
      chat card is set again from `tucked`, `raiseChat` and the address, even when they are the same as before. A card
      put down by hand stays down until then. */
  chatKey?: string;
  /** Search, from the bar. */
  onSearch?: () => void;
  /** The page is an open task: the Task icon shows, and Company leaves the task for where it was opened from. */
  onLeave?: () => void;
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

const EDITABLE = 'input,textarea,select,[contenteditable]:not([contenteditable="false"])';

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

export function PhoneCards({ page, tico, menu, chat, faces = {}, labels = {}, tucked = false, raiseChat = false, asked, chatKey, onSearch, onLeave, onCardChange }: PhoneCardsProps) {
  const router = useRouter({ warn: false });
  // a chat that opens comes up and one that ends goes down. An address that already names one (a shared link, a
  // reload) opens on it. A page's own chat (`tucked`) waits behind its icon unless the address names a chat or the
  // page says it waits on the person there.
  const second = !!chat;
  const named = () => /[?&]chat=/.test(router ? router.state.location.searchStr : window.location.search);
  const comesUp = () => second && (!tucked || raiseChat || named());
  const [card, setCard] = useState<PhoneCard>(() => (comesUp() ? 'chat' : 'none'));
  const root = useRef<HTMLDivElement>(null);
  const [dock, setDock] = useState<HTMLElement | null>(null);

  const [seen, setSeen] = useState(second);
  const [wasTucked, setWasTucked] = useState(tucked);
  const [wasKey, setWasKey] = useState(chatKey);
  const [wasRaised, setWasRaised] = useState(raiseChat);
  const [wasAsked, setWasAsked] = useState(asked);
  const newAsk = !!asked && asked.split(' ').some((q) => !wasAsked?.split(' ').includes(q));
  if (seen !== second) {
    setSeen(second);
    if (comesUp()) setCard('chat');
    else if (card === 'chat') setCard('none');
  } else if ((wasTucked !== tucked || wasKey !== chatKey) && second) {
    // a page's own chat follows its page both ways: one that stops waiting behind its icon comes up (its fold is open,
    // an ask from the page's owner waits on you, the address names it), and one that waits again goes down
    if (comesUp()) setCard('chat');
    else if (card === 'chat') setCard('none');
  } else if (((raiseChat && !wasRaised) || newAsk) && second) setCard('chat');
  if (wasTucked !== tucked) setWasTucked(tucked);
  if (wasAsked !== asked) setWasAsked(asked);
  if (wasKey !== chatKey) setWasKey(chatKey);
  if (wasRaised !== raiseChat) setWasRaised(raiseChat);

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
  // Company: from a card, back to the page; on the page, the menu (and the menu back to the page). On a task, back to
  // where the task was opened from, and the Task icon goes with it.
  const toCompany = () => {
    if (onLeave) {
      tuck();
      onLeave();
    } else setCard(card === 'none' ? 'menu' : 'none');
  };

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
      const ly = inside && keys.up ? active.closest<HTMLElement>('.ly[data-slot], .ph-dock') : null;
      const where = !inside || !keys.up ? null : (ly?.dataset.slot ?? 'page');
      const holder = where === 'page' ? el.querySelector(':scope > .pg') : ly;
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

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape' && !e.defaultPrevented && card !== 'none') {
      e.preventDefault();
      tuck();
    }
  };

  // a page opened from a card (a place in the menu, an approval card in a chat, a tracked task in Tico's): the card
  // goes down and the page comes forward. A link marked `data-stay` changes what the card holds, so it stays up.
  const onCardClick = (e: MouseEvent<HTMLElement>) => {
    const a = (e.target as HTMLElement).closest<HTMLAnchorElement>('a[href]');
    if (!a || a.target || a.origin !== location.origin || a.closest('[data-stay]')) return;
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
            <Tab name="company" label="Company" on={!onLeave && (card === 'none' || card === 'menu')} onClick={toCompany}>
              {faces.company ?? <Icon name="company" />}
            </Tab>
            {chat ? (
              <Tab name="agent" label={labels.chat ?? 'Agent'} on={card === 'chat'} onClick={() => raise('chat')}>
                {faces.chat ?? <Icon name="message" />}
              </Tab>
            ) : null}
            {onLeave ? (
              <Tab name="task" label="Task" on={card === 'none'} onClick={tuck}>
                <Icon name="file" />
              </Tab>
            ) : null}
          </nav>
        </div>
      </DockSlot>
    </Ctx>
  );
}
