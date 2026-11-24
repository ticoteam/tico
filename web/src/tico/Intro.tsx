import { useQuery } from '@tanstack/react-query';
import { useLocation, useRouterState } from '@tanstack/react-router';
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { run } from '@/actions';
import { sendToDm, ticoRoomQuery } from '@/conversation';
import { configQuery, directoryQuery, meQuery, pageOwnersQuery, useActor, type Actor } from '@/data';
import { providerReady, subscriptionsQuery } from '@/features/settings/routing';
import { needsSetup } from '@/features/start/routing';
import { Button } from '@/kit/Button';
import { Pill } from '@/kit/Pill';
import { fromPage, pageAt } from '@/shell/pageOwner';
import { useAnswering } from './Chips';
import { INTROS, introKey, isPhone, nextBeat, nowBeat, nowShown, play, replayCount, setHere, setWhere, sheetsNow, spot, stop, subscribe, teamWho, whereFor } from './intros';
import { introsSeenQuery, markIntroSeen, ticoPowerQuery } from './queries';
import './intro.css';

/* A page's first-visit intro (intros.ts), mounted once beside the frame. It waits for a quiet moment (the address
   settled, setup not owed or open, Tico not answering, and somewhere to show it), then plays the page's line as a card
   pinned over Tico's box (`IntroPin`, outside the scrolling chat, so it is in view while it shows) with its spotlights
   over the page. On a phone with its page up the card sits over the page's foot instead, and the page makes room for
   it; a phone has no spotlights. With Tico folded or off on a computer, or a phone's agent chat or menu up, it waits.
   The page is marked seen once the card has drawn. Never a dialog, and it never sends anything by itself: "Got it"
   puts it away. Agents never get one. */

/** How long a spotlight stays on its element. */
export const SPOT_MS = 1800;
/** How long a beat waits for its element to draw (a list still reading) before it is skipped. */
const FIND_MS = 1000;

const useShown = () => useSyncExternalStore(subscribe, nowShown, nowShown);
const useBeat = () => useSyncExternalStore(subscribe, nowBeat, nowBeat);
const useReplays = () => useSyncExternalStore(subscribe, replayCount, replayCount);

/* The frame marks a tucked sheet's place `data-shut`, a phone the card that is up (`data-card`), and the window's
   layout on the root: what covers the page, kept current. */
function watchSheets(fn: () => void) {
  const mo = new MutationObserver(fn);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-layout'] });
  mo.observe(document.body, { subtree: true, attributes: true, attributeFilter: ['data-shut', 'data-card'] });
  return () => mo.disconnect();
}
const useSheets = () => useSyncExternalStore(watchSheets, sheetsNow, () => 'shut');

/** An agent that is set up and on the team: never a planned, paused or archived one. */
const live = (a: Actor | undefined): a is Actor => a?.kind === 'agent' && a.lifecycle !== 'planned' && a.lifecycle !== 'paused' && a.lifecycle !== 'archived';

/** Pages whose card drew this session: a seen mark the server refused never plays one again and again. */
const played = new Set<string>();

/** For tests: a fresh session. */
export function resetIntros(): void {
  played.clear();
  setHere(null);
  stop();
}

/** The open page's intro key for this person: a teammate's page is an agent's or a person's (never your own). */
function useIntroKey(me: string | undefined): string | null {
  const path = useLocation({ select: (l) => l.pathname });
  const raw = introKey(path);
  // the directory answers a slug, an actor id and a bare person id alike
  const found = useActor(raw === 'who' ? teamWho(path) : undefined);
  if (raw !== 'who') return raw;
  if (found?.kind === 'agent') return 'agent';
  return found?.kind === 'person' && found.id !== me ? 'teammate' : null;
}

export function PageIntro() {
  const me = useQuery(meQuery).data;
  const person = !!me && me.role !== 'agent' && me.id.startsWith('human:');
  const key = useIntroKey(me?.id);
  const setup = useLocation({ select: (l) => !!(l.search as { setup?: unknown }).setup });
  // `/` passes on to Tasks (and to the first run) only once its read is done: nothing plays on the way
  const settled = useRouterState({ select: (s) => s.status === 'idle' && s.resolvedLocation !== undefined });
  const config = useQuery({ ...configQuery, enabled: person });
  // the owner's setup is done only when the company's config says so: not before it is read
  const owed = me?.role === 'owner' && (!config.isSuccess || needsSetup(config.data));
  const seen = useQuery({ ...introsSeenQuery, enabled: person });
  const room = useQuery({ ...ticoRoomQuery, enabled: person }).data;
  const on = useQuery({ ...ticoPowerQuery, enabled: person }).data?.available !== false;
  const busy = useAnswering(on ? room : null);
  const shown = useShown();
  const replays = useReplays();
  const where = whereFor(useSheets(), on && !!room);
  const quiet = person && settled && !busy && !setup && !owed;

  useEffect(() => setHere(person ? key : null), [person, key]);

  // leaving the page puts its intro away (read from the store: this page's own may have started in this commit)
  useEffect(() => {
    const now = nowShown();
    if (now && now.key !== key) stop();
  }, [shown, key]);

  useEffect(() => setWhere(where), [where, shown]);

  // first visit: once the page and Tico are quiet
  const fresh = !!key && quiet && where !== 'none' && !!seen.data && !seen.data[key] && !played.has(key) && shown?.key !== key;
  useEffect(() => {
    if (!fresh || !key) return;
    play(key, false, where);
  }, [fresh, key, where]);

  // "Show me how this works": the open page's again, now
  const asked = useRef(replays);
  useEffect(() => {
    if (replays === asked.current) return;
    asked.current = replays;
    if (key && person) play(key, true, where);
  }, [replays, key, person, where]);

  if (!shown || shown.key !== key) return null;
  return (
    <>
      {!isPhone() && <Spotlight key={shown.at} page={shown.key} />}
      {shown.where === 'float' && createPortal(<FloatCard />, document.body)}
    </>
  );
}

interface Box {
  top: number;
  left: number;
  width: number;
  height: number;
}

/** Where an element shows: nothing inside a tucked sheet (`data-shut`, `inert`) or with no size. */
function boxOf(el: Element): Box | null {
  if (el.closest('[data-shut],[inert]')) return null;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 ? { top: r.top, left: r.left, width: r.width, height: r.height } : null;
}
/** The first match that shows. */
function find(sel: string): Element | null {
  for (const el of document.querySelectorAll(sel)) if (boxOf(el)) return el;
  return null;
}
const same = (a: Box | null, b: Box | null) => a === b || (!!a && !!b && a.top === b.top && a.left === b.left && a.width === b.width && a.height === b.height);

/** The spotlight on the beat now: a ring around its element with the beat's words, over the page, never in the way. */
function Spotlight({ page }: { page: string }) {
  const at = useBeat();
  const beat = at.n >= 0 ? INTROS[page]?.beats?.[at.n] : undefined;
  const [box, setBox] = useState<Box | null>(null);
  useEffect(() => {
    if (!beat) return;
    let el: Element | null = null;
    let next: ReturnType<typeof setTimeout> | undefined;
    const measure = () => {
      const b = el ? boxOf(el) : null;
      setBox((was) => (same(was, b) ? was : b));
    };
    const start = (found: Element) => {
      el = found;
      measure();
      next = setTimeout(nextBeat, SPOT_MS);
    };
    // drawn now, or within FIND_MS (a list still reading); else the beat is skipped
    const now = find(beat.sel);
    const mo = new MutationObserver(() => {
      const found = find(beat.sel);
      if (!found) return;
      mo.disconnect();
      clearTimeout(giveUp);
      start(found);
    });
    const giveUp = now ? undefined : setTimeout(() => (mo.disconnect(), nextBeat()), FIND_MS);
    if (now) start(now);
    else mo.observe(document.body, { subtree: true, childList: true, attributes: true });
    window.addEventListener('resize', measure);
    window.addEventListener('scroll', measure, true);
    return () => {
      mo.disconnect();
      clearTimeout(giveUp);
      clearTimeout(next);
      window.removeEventListener('resize', measure);
      window.removeEventListener('scroll', measure, true);
      setBox(null);
    };
  }, [beat, at]);
  if (!beat || !box) return null;
  const pad = 4;
  // the words stay on screen: inside the ring's foot when there is no room under it, flush right near the right edge
  const inside = box.top + box.height + 44 > window.innerHeight;
  const end = box.left + 280 > window.innerWidth;
  return createPortal(
    <div className="intro-spot" aria-hidden style={{ top: box.top - pad, left: box.left - pad, width: box.width + pad * 2, height: box.height + pad * 2 }}>
      <span className="intro-spot-label" data-in={inside || undefined} data-end={end || undefined}>
        {beat.label}
      </span>
    </div>,
    document.body,
  );
}

/** Over a phone's page foot: the page makes room for it (`--intro-room`), so nothing under it is out of reach. */
function FloatCard() {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    const root = document.documentElement.style;
    const room = () => root.setProperty('--intro-room', `${el?.offsetHeight ?? 0}px`);
    room();
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(room);
    if (el) ro?.observe(el);
    return () => {
      ro?.disconnect();
      root.removeProperty('--intro-room');
    };
  }, []);
  return (
    <div className="intro-float" ref={ref}>
      <IntroCard />
    </div>
  );
}

/** The page's owner agent, when it can run: a connected plan, and its own owner (not the General Manager standing in)
    set up and on the team. */
function useOwner(page: string, on: boolean): Actor | undefined {
  const subs = useQuery({ ...subscriptionsQuery, enabled: on, refetchInterval: false });
  const owners = useQuery({ ...pageOwnersQuery, enabled: on });
  const dir = useQuery({ ...directoryQuery, enabled: on });
  if (!on || subs.data?.subscriptions.some(providerReady) !== true) return undefined;
  const row = owners.data?.pages[page];
  const bot = row?.own && row.bot ? (row.bot.includes(':') ? row.bot : `bot:${row.bot}`) : undefined;
  const agent = bot ? dir.data?.byId[bot] : undefined;
  return live(agent) ? agent : undefined;
}

/** The intro as Tico says it: the pill, the line, the beats (each shows its spotlight again), chips, and Got it. */
export function IntroCard() {
  const shown = useShown();
  const at = useBeat();
  // the page as its owner and its messages name it (Goals is not Strategy's, though its intro is)
  const [path, search] = useLocation({ select: (l) => `${l.pathname}\n${l.searchStr}` }).split('\n');
  const here = pageAt(path ?? '/', search);
  const intro = shown ? INTROS[shown.key] : undefined;
  const owner = useOwner(here.page, !!intro?.owner);
  // drawn: this person has had it, and it does not play here again on its own
  const first = shown && !shown.replay ? shown.key : null;
  useEffect(() => {
    if (!first) return;
    played.add(first);
    void markIntroSeen(first).catch(() => undefined);
  }, [first]);
  if (!shown || !intro) return null;
  const phone = isPhone();
  const gotIt = () => {
    void markIntroSeen(shown.key).catch(() => undefined);
    stop();
  };
  return (
    <div className="intro" data-target="tico:intro">
      <Pill>{shown.replay ? intro.name : `First visit · ${intro.name}`}</Pill>
      <p className="intro-line">{intro.line}</p>
      {!!intro.beats?.length && (
        <ol className="intro-beats">
          {intro.beats.map((b, i) => (
            <li key={b.sel}>
              {phone ? (
                b.label
              ) : (
                <button type="button" aria-pressed={at.n === i} onClick={() => spot(i)}>
                  {b.label}
                </button>
              )}
            </li>
          ))}
        </ol>
      )}
      <div className="intro-acts">
        {intro.chips?.map((c) => (
          <button
            key={c.label}
            type="button"
            className="qchip"
            onClick={() => {
              gotIt();
              void run(c.action, c.input, { via: 'click' });
            }}
          >
            {c.label}
          </button>
        ))}
        {owner && (
          // the person's own press: the one way an intro writes to an agent
          <button
            type="button"
            className="qchip"
            onClick={() => {
              gotIt();
              const text = 'Propose one real addition to this page: one ask, with up to 3 choices.';
              void sendToDm(owner.id, text, fromPage(here)).catch(() => undefined);
            }}
          >
            {`Ask ${owner.name} for one idea`}
          </button>
        )}
        <Button onClick={gotIt}>Got it</Button>
      </div>
    </div>
  );
}

/** In Tico's sheet, over its box: the intro while it shows there (outside the chat's scrolling list). */
export function IntroPin() {
  const shown = useShown();
  if (shown?.where !== 'chat') return null;
  return (
    <div className="intro-pin">
      <IntroCard />
    </div>
  );
}
