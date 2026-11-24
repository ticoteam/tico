import { useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { run } from '@/actions';
import { sendToDm, ticoRoomQuery, type ChatMessage } from '@/conversation';
import { directoryQuery, meQuery, pageOwnersQuery, useActor } from '@/data';
import { providerReady, subscriptionsQuery } from '@/features/settings/routing';
import { Button } from '@/kit/Button';
import { Pill } from '@/kit/Pill';
import { fromPage, pageAt } from '@/shell/pageOwner';
import { useAnswering } from './Chips';
import { INTROS, introKey, isPhone, nextBeat, nowPlaying, nowProposal, play, propose, replayCount, spot, stop, subscribe, teamWho, type Playing } from './intros';
import { introsSeenQuery, markIntroSeen, ticoPowerQuery } from './queries';
import './intro.css';

/* A page's first-visit intro (intros.ts), mounted once beside the frame. It waits for a quiet moment (Tico not
   answering, the first run not open), plays the page's line in Tico's chat (`useIntroMessage`, drawn by `IntroCard`)
   with its spotlights over the page, and marks the page seen for this person. On a phone, or while Tico is off, the
   line is a card over the bottom of the page instead, with the beats as words and no spotlights. Never a dialog: the
   page stays usable, and "Got it" puts it away. Agents never get one. */

/** How long a spotlight stays on its element. */
export const SPOT_MS = 1800;

const usePlaying = () => useSyncExternalStore(subscribe, nowPlaying, nowPlaying);
const useReplays = () => useSyncExternalStore(subscribe, replayCount, replayCount);

/** An agent that is set up and on the team: never a planned, paused or archived one. */
const live = (lifecycle: string | undefined) => lifecycle !== 'planned' && lifecycle !== 'paused' && lifecycle !== 'archived';

/** Pages played this session: a seen mark the server refused never plays one again and again. */
const played = new Set<string>();

/** For tests: a fresh session. */
export function resetIntros(): void {
  played.clear();
  propose(null);
  stop();
}

/** The open page's intro key for this person: a teammate's page is an agent's or a person's (never your own). */
function useIntroKey(me: string | undefined): { key: string | null; path: string; who?: string } {
  const path = useLocation({ select: (l) => l.pathname });
  const raw = introKey(path);
  const who = raw === 'who' ? teamWho(path) : undefined;
  // the directory answers a slug, an actor id and a bare person id alike
  const found = useActor(who);
  if (raw !== 'who') return { key: raw, path };
  if (found?.kind === 'agent') return { key: 'agent', path, who: found.id };
  if (found?.kind === 'person' && found.id !== me) return { key: 'teammate', path };
  return { key: null, path };
}

/** The first-visit starter for the owner: after the beats, once, only with a connected plan and the agent on the team. */
function useOwnerProposal() {
  const pending = useSyncExternalStore(subscribe, nowProposal, nowProposal);
  const on = !!pending;
  const subs = useQuery({ ...subscriptionsQuery, enabled: on, refetchInterval: false });
  const owners = useQuery({ ...pageOwnersQuery, enabled: on && !pending?.agent });
  const dir = useQuery({ ...directoryQuery, enabled: on });
  const playing = usePlaying();
  const beatsOver = !playing || playing.at !== pending?.at || playing.beat < 0;
  const settled = !subs.isPending && (!!pending?.agent || !owners.isPending) && !dir.isPending;
  useEffect(() => {
    if (!pending || !beatsOver || !settled) return;
    propose(null);
    const plan = subs.data?.subscriptions.some(providerReady) === true;
    const row = owners.data?.pages[pending.key];
    const named = pending.agent ?? (row?.own && row.bot ? (row.bot.includes(':') ? row.bot : `bot:${row.bot}`) : undefined);
    const agent = named ? dir.data?.byId[named] : undefined;
    if (!plan || !agent || agent.kind !== 'agent' || !live(agent.lifecycle)) return;
    const intro = INTROS[pending.key]!;
    const page = pending.agent ? 'your page' : intro.name;
    const text = `I just opened ${page} for the first time. Propose one real addition to it: one ask, with up to 3 choices.`;
    void sendToDm(agent.id, text, fromPage(pageAt(pending.path))).catch(() => undefined);
  }, [pending, beatsOver, settled, subs.data, owners.data, dir.data]);
}

export function PageIntro() {
  const me = useQuery(meQuery).data;
  const person = !!me && me.role !== 'agent' && me.id.startsWith('human:');
  const { key, path, who } = useIntroKey(me?.id);
  const setup = useLocation({ select: (l) => !!(l.search as { setup?: unknown }).setup });
  const seen = useQuery({ ...introsSeenQuery, enabled: person });
  const room = useQuery({ ...ticoRoomQuery, enabled: person }).data;
  const power = useQuery({ ...ticoPowerQuery, enabled: person }).data;
  const busy = useAnswering(room);
  const playing = usePlaying();
  const replays = useReplays();
  const float = isPhone() || power?.available === false || !room;
  const quiet = person && !busy && !setup;

  // leaving the page puts its intro away (read from the store: this page's own may have started in this commit)
  useEffect(() => {
    const now = nowPlaying();
    if (now && now.key !== key) stop();
  }, [playing, key]);

  // first visit: once the page and Tico are quiet
  const fresh = !!key && quiet && !!seen.data && !seen.data[key] && !played.has(key) && playing?.key !== key;
  useEffect(() => {
    if (!fresh || !key) return;
    played.add(key);
    play(key, false, float);
    void markIntroSeen(key).catch(() => undefined);
    const at = nowPlaying()?.at ?? '';
    if (INTROS[key]?.owner) propose({ key, at, path, ...(who && { agent: who }) });
  }, [fresh, key, float, path, who]);

  // "Show me how this works": the open page's again, now, with no starter for its owner
  const asked = useRef(replays);
  useEffect(() => {
    if (replays === asked.current) return;
    asked.current = replays;
    if (key && person) play(key, true, float);
  }, [replays, key, person, float]);

  useOwnerProposal();

  if (!playing || playing.key !== key) return null;
  const phone = isPhone();
  return (
    <>
      {!phone && <Spotlight playing={playing} />}
      {playing.float &&
        createPortal(
          <div className="intro-float">
            <IntroCard />
          </div>,
          document.body,
        )}
    </>
  );
}

/** The spotlight on the beat now: a ring around its element with the beat's words, over the page, never in the way. */
function Spotlight({ playing }: { playing: Playing }) {
  const beat = playing.beat >= 0 ? INTROS[playing.key]?.beats?.[playing.beat] : undefined;
  const [box, setBox] = useState<DOMRect | null>(null);
  useLayoutEffect(() => {
    if (!beat) return;
    const el = document.querySelector(beat.sel);
    const measure = () => {
      const r = el?.getBoundingClientRect();
      setBox(r && r.width > 0 && r.height > 0 ? r : null);
    };
    measure();
    // an element that is not on the page is skipped
    const next = setTimeout(nextBeat, el ? SPOT_MS : 0);
    window.addEventListener('resize', measure);
    window.addEventListener('scroll', measure, true);
    return () => {
      clearTimeout(next);
      window.removeEventListener('resize', measure);
      window.removeEventListener('scroll', measure, true);
    };
  }, [beat, playing.beat, playing.solo]);
  if (!beat || !box) return null;
  const pad = 4;
  return createPortal(
    <div className="intro-spot" aria-hidden style={{ top: box.top - pad, left: box.left - pad, width: box.width + pad * 2, height: box.height + pad * 2 }}>
      <span className="intro-spot-label">{beat.label}</span>
    </div>,
    document.body,
  );
}

/** The intro as Tico says it: the pill, the line, the beats (each shows its spotlight again), chips, and Got it. */
export function IntroCard() {
  const playing = usePlaying();
  if (!playing) return null;
  const intro = INTROS[playing.key];
  if (!intro) return null;
  const phone = isPhone();
  const gotIt = () => {
    void markIntroSeen(playing.key).catch(() => undefined);
    stop();
  };
  return (
    <div className="intro" data-target="tico:intro">
      <Pill>{playing.replay ? intro.name : `First visit · ${intro.name}`}</Pill>
      <p className="intro-line">{intro.line}</p>
      {!!intro.beats?.length && (
        <ol className="intro-beats">
          {intro.beats.map((b, i) => (
            <li key={b.sel}>
              {phone ? (
                b.label
              ) : (
                <button type="button" aria-pressed={playing.beat === i} onClick={() => spot(i)}>
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
        <Button onClick={gotIt}>Got it</Button>
      </div>
    </div>
  );
}

/** For Tico's chat: the intro playing there as a message of Tico's, drawn by `IntroCard` (`introCard`). */
export function useIntroMessage(bot: string | undefined): ChatMessage[] | undefined {
  const playing = usePlaying();
  if (!playing || playing.float) return undefined;
  return [{ id: `intro:${playing.key}:${playing.at}`, kind: INTRO_KIND, sender_id: `bot:${bot ?? 'assistant'}`, sender_name: 'Tico', created_at: playing.at, body: INTROS[playing.key]?.line ?? '' }];
}

const INTRO_KIND = 'intro';
export const introCard = (m: ChatMessage) => (m.kind === INTRO_KIND ? <IntroCard /> : null);
