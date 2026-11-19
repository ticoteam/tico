import { useQuery, type UseQueryOptions } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useEffect, useState, type ReactNode } from 'react';
import { meQuery } from '@/data';
import { Empty } from '@/kit/Empty';
import { Search } from '@/kit/Field';
import { Link as To } from '@/kit/Link';
import { Section, Settings } from '@/pagekinds';
import { Agents, Computers, Models } from './Agents';
import { Billing, Changelog, Health, History } from './Records';
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

/** Where a hit opens in this app, from its old address ('#/settings/<tab>/<card>', '#/usage', '#/bot/<slug>/settings'). */
export function hitHref(card = ''): string | null {
  const hash = card.slice(Math.max(0, card.indexOf('#')));
  const tab = /^#\/settings\/([a-z-]+)/.exec(hash)?.[1];
  if (tab) return TAB[tab] ? `/settings/${TAB[tab]}` : null;
  if (/^#\/usage\b/.test(hash)) return '/company/usage';
  const bot = /^#\/bot\/([^/]+)/.exec(hash)?.[1];
  return bot ? `/team/${bot}?tab=settings` : null;
}

function Results({ q, onPick }: { q: string; onPick: () => void }) {
  const me = useQuery(meQuery);
  const access = useQuery({ ...accessQuery, retry: false });
  const found = useQuery(settingsCatalogQuery(q));
  const owner = me.data?.role === 'owner';
  const admin = owner || access.data?.you === 'owner' || access.data?.you === 'admin';
  // A setting the reader may not change is not offered.
  const may = (h: SettingHit) => (h.who === 'owner' ? owner : h.who === 'admin' ? admin : true);
  const hits = (found.data ?? []).filter(may).flatMap((h) => {
    const href = hitHref(h.card);
    return href ? [{ ...h, href }] : [];
  });
  if (!found.data) return null;
  if (hits.length === 0) return <Empty icon="search" title="No settings found" />;
  return (
    <Section name="Results" count={hits.length}>
      {hits.map((h) => (
        <To key={h.key} href={h.href} className="row set-link" title={h.where} onClick={onPick}>
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
  return (
    <Settings
      title="Settings"
      path={`/settings/${section}`}
      className="set-page"
      open={!list || searching}
      nav={<Nav current={section} />}
      actions={<Search label="Search settings" placeholder="Search settings" value={q} onValueChange={setQ} />}
      state={state}
      onRetry={() => void main.refetch()}
      body={
        searching ? (
          asked ? (
            <Results q={asked} onPick={() => setQ('')} />
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
