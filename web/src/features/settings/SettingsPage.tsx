import { useQuery, type UseQueryOptions } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { meQuery } from '@/data';
import { AddAgentButton } from '@/features/team/exports';
import { Empty } from '@/kit/Empty';
import { Search } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { Link as To } from '@/kit/Link';
import { Section, Settings } from '@/pagekinds';
import { Agents, Computers, Models } from './Agents';
import { Billing, Changelog, CheckNow, Health, History } from './Records';
import { Account, General, People, Security } from './Basics';
import { Contacts, Tasks } from './Catalog';
import { mainQuery } from './main';
import { isForbidden } from './parts';
import { accessQuery, settingsCatalogQuery, type SettingHit } from './queries';
import { NAV, type SectionId } from './sections';
import './settings.css';

/* Settings (kind: Settings). The section list on the left, the open section on the right; every control saves as it
   changes. The address holds the section, an opened row (?open=) and History's filter (?who=). */

const BODY: Record<SectionId, (p: { open?: string; who?: string }) => ReactNode> = {
  general: () => <General />,
  people: ({ open }) => <People open={open} />,
  agents: ({ open }) => <Agents open={open} />,
  computers: ({ open }) => <Computers open={open} />,
  models: () => <Models />,
  tasks: () => <Tasks />,
  contacts: ({ open }) => <Contacts open={open} />,
  account: () => <Account />,
  security: () => <Security />,
  billing: () => <Billing />,
  history: ({ who }) => <History who={who} />,
  health: () => <Health />,
  changelog: ({ open }) => <Changelog open={open} />,
};

function Nav({ current }: { current: SectionId }) {
  return (
    <>
      {NAV.map((n, i) =>
        n ? (
          <Link
            key={n[0]}
            to="/settings/$section"
            params={{ section: n[0] }}
            className="row set-link"
            aria-current={n[0] === current ? 'page' : undefined}
          >
            <span className="row-title">{n[1]}</span>
          </Link>
        ) : (
          <div key={`gap-${i}`} className="set-gap" role="separator" />
        ),
      )}
      {/* The style guide: a reference page, open to everyone, after the sections. */}
      <div className="set-gap" role="separator" />
      <Link to="/settings/style-guide" className="row set-link">
        <span className="row-title">Style guide</span>
        <span className="set-out">
          <Icon name="arrow" />
        </span>
      </Link>
    </>
  );
}

// The catalog still names the old app's tabs: each one's place here, or none (Inboxes and Releases live elsewhere).
const TAB: Record<string, SectionId> = {
  general: 'general',
  people: 'people',
  bots: 'agents',
  devices: 'computers',
  computers: 'computers',
  models: 'models',
  tasks: 'tasks',
  security: 'security',
  billing: 'billing',
  history: 'history',
  health: 'health',
};

/* Old cards ('#/settings/<tab>/<card>') that have a place here: the section, and the part a hit lands on. */
const CARD: Record<string, [SectionId, string]> = {
  'settings-roadmaps': ['tasks', 'setting:roadmap'],
  'set-screening-card': ['security', 'setting:screening'],
  'settings-sleep': ['computers', 'setting:sleep'],
  'settings-tokens': ['account', 'setting:tokens'],
  'settings-subs': ['agents', 'setting:subscriptions'],
  'settings-gateway-card': ['models', 'setting:gateway'],
  'wh-gateway': ['models', 'setting:gateway'],
  'settings-defaults': ['models', 'setting:defaults'],
  'settings-company': ['general', 'setting:company'],
  'settings-team': ['general', 'setting:reports'],
  'set-repos': ['general', 'setting:repositories'],
};

const hashOf = (card = '') => card.slice(Math.max(0, card.indexOf('#')));
const cardOf = (card = '') => /^#\/settings\/[a-z-]+\/([a-z0-9-]+)/.exec(hashOf(card))?.[1] ?? '';

/** Where a hit opens in this app, from its old address ('#/settings/<tab>/<card>', '#/usage', '#/bot/<slug>/settings'). */
export function hitHref(card = ''): string | null {
  const hash = hashOf(card);
  const known = CARD[cardOf(card)];
  if (known) return `/settings/${known[0]}`;
  if (cardOf(card) === 'settings-limits') return '/company/usage?view=limits';
  const tab = /^#\/settings\/([a-z-]+)/.exec(hash)?.[1];
  if (tab) return TAB[tab] ? `/settings/${TAB[tab]}` : null;
  if (/^#\/usage\b/.test(hash)) return '/company/usage';
  const bot = /^#\/bot\/([^/]+)/.exec(hash)?.[1];
  return bot ? `/team/${bot}?tab=settings` : null;
}

/** The part of the section a hit lands on: scrolled to, and its first control focused. */
export const hitTarget = (card = ''): string | undefined => CARD[cardOf(card)]?.[1];

// The part to land on once the picked section has drawn it (a section reads its own data, so it may come later).
let landing: string | null = null;

function land(): boolean {
  if (!landing) return true;
  const el = document.querySelector<HTMLElement>(`[data-target="${landing}"]`);
  if (!el) return false;
  landing = null;
  el.scrollIntoView?.({ block: 'start' });
  const control = el.querySelector<HTMLElement>('input, select, textarea, button, [role="switch"], a[href]');
  (control ?? el).focus({ preventScroll: true });
  return true;
}

/** Land on the picked part when it is drawn; give up after a few seconds (it may not be shown to this reader). */
function useLanding(section: SectionId, ready: boolean) {
  useEffect(() => {
    if (!ready || land()) return;
    const watch = new MutationObserver(() => land() && watch.disconnect());
    watch.observe(document.body, { childList: true, subtree: true });
    const stop = setTimeout(() => {
      watch.disconnect();
      landing = null;
    }, 4000);
    return () => {
      watch.disconnect();
      clearTimeout(stop);
    };
  }, [section, ready]);
}

type Hit = SettingHit & { href: string };

function useHits(q: string): { hits: Hit[]; loaded: boolean } {
  const me = useQuery(meQuery);
  const access = useQuery({ ...accessQuery, retry: false });
  const found = useQuery({ ...settingsCatalogQuery(q), enabled: !!q });
  const owner = me.data?.role === 'owner';
  const admin = owner || access.data?.you === 'owner' || access.data?.you === 'admin';
  // A setting the reader may not change is not offered.
  const may = (h: SettingHit) => (h.who === 'owner' ? owner : h.who === 'admin' ? admin : true);
  const hits = (found.data ?? []).filter(may).flatMap((h) => {
    const href = hitHref(h.card);
    return href ? [{ ...h, href }] : [];
  });
  return { hits, loaded: !!found.data };
}

function Results({ hits, at, onPick }: { hits: Hit[]; at: number; onPick: (h: Hit) => void }) {
  if (hits.length === 0) return <Empty icon="search" title="No settings found" />;
  return (
    <Section name="Results" count={hits.length}>
      {hits.map((h, i) => (
        <To
          key={h.key}
          href={h.href}
          className={i === at ? 'row set-link is-selected' : 'row set-link'}
          aria-current={i === at ? 'true' : undefined}
          title={h.where}
          data-hit={i}
          onClick={() => onPick(h)}
        >
          <span className="row-title">{h.name}</span>
        </To>
      ))}
    </Section>
  );
}

/** A filter on the settings themselves: what is typed is asked for after a short pause. */
function useSettled(value: string, ms = 200): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return settled;
}

/** `list`: the plain /settings address. A phone shows the section list there; a wide screen shows the first section. */
export function SettingsPage({ section, open, who, list = false }: { section: SectionId; open?: string; who?: string; list?: boolean }) {
  const me = useQuery(meQuery);
  // The union of each section's options: one read, typed loosely here because only its state is used.
  const main = useQuery(mainQuery(section) as UseQueryOptions<unknown, Error, unknown, readonly unknown[]>);
  const forbidden = isForbidden(main.error);
  const state = me.isPending || main.isPending ? 'loading' : main.isError && !forbidden ? 'fault' : undefined;
  const Body = BODY[section];
  const [q, setQ] = useState('');
  const asked = useSettled(q.trim());
  const searching = !!q.trim();
  const { hits, loaded } = useHits(asked);
  // The result the arrow keys are on; Enter opens it (the first when none is).
  const [at, setAt] = useState(-1);
  const results = useRef<HTMLDivElement>(null);
  const pick = (h: Hit) => {
    landing = hitTarget(h.card) ?? null;
    setQ('');
    setAt(-1);
  };
  const keys = (e: KeyboardEvent) => {
    if (!searching || hits.length === 0) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const step = e.key === 'ArrowDown' ? 1 : -1;
      setAt((i) => (i < 0 ? (step > 0 ? 0 : hits.length - 1) : (i + step + hits.length) % hits.length));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      results.current?.querySelector<HTMLElement>(`[data-hit="${Math.max(0, Math.min(at, hits.length - 1))}"]`)?.click();
    }
  };
  useLanding(section, !searching && !state);
  return (
    <Settings
      title="Settings"
      path={`/settings/${section}`}
      className="set-page"
      open={!list || searching}
      nav={<Nav current={section} />}
      actions={!searching && (section === 'agents' ? <AddAgentButton /> : section === 'health' && !forbidden && <CheckNow />)}
      search={
        <div onKeyDown={keys}>
          <Search
            label="Search settings"
            placeholder="Search settings"
            value={q}
            onValueChange={(v) => {
              setQ(v);
              setAt(-1);
            }}
          />
        </div>
      }
      state={state}
      onRetry={() => void main.refetch()}
      body={
        searching ? (
          asked && loaded ? (
            <div ref={results}>
              <Results hits={hits} at={at} onPick={pick} />
            </div>
          ) : null
        ) : forbidden ? (
          <Empty icon="lock" title="Owners and admins only" />
        ) : (
          <Body open={open} who={who} />
        )
      }
    />
  );
}
