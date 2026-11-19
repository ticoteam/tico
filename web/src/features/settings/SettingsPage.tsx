import { useQuery, type UseQueryOptions } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import type { ReactNode } from 'react';
import { meQuery } from '@/data';
import { Empty } from '@/kit/Empty';
import { Settings } from '@/pagekinds';
import { Agents, Computers, Models } from './Agents';
import { Billing, Changelog, Health, History } from './Records';
import { Account, General, People, Security } from './Basics';
import { mainQuery } from './main';
import { isForbidden } from './parts';
import { NAV, type SectionId } from './sections';
import './settings.css';

/* Settings (kind: Settings). The section list on the left, the open section on the right; every control saves as it
   changes. The address holds the section and an opened row (?open=). */

const BODY: Record<SectionId, (p: { open?: string }) => ReactNode> = {
  general: () => <General />,
  people: ({ open }) => <People open={open} />,
  agents: ({ open }) => <Agents open={open} />,
  computers: ({ open }) => <Computers open={open} />,
  models: () => <Models />,
  account: () => <Account />,
  security: () => <Security />,
  billing: () => <Billing />,
  history: () => <History />,
  health: () => <Health />,
  changelog: () => <Changelog />,
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

/** `list`: the plain /settings address. A phone shows the section list there; a wide screen shows the first section. */
export function SettingsPage({ section, open, list = false }: { section: SectionId; open?: string; list?: boolean }) {
  const me = useQuery(meQuery);
  // The union of each section's options: one read, typed loosely here because only its state is used.
  const main = useQuery(mainQuery(section) as UseQueryOptions<unknown, Error, unknown, readonly unknown[]>);
  const forbidden = isForbidden(main.error);
  const state = me.isPending || main.isPending ? 'loading' : main.isError && !forbidden ? 'fault' : undefined;
  const Body = BODY[section];
  return (
    <Settings
      title="Settings"
      path={`/settings/${section}`}
      className="set-page"
      open={!list}
      nav={<Nav current={section} />}
      state={state}
      onRetry={() => void main.refetch()}
      body={forbidden ? <Empty icon="lock" title="Owners and admins only" /> : <Body open={open} />}
    />
  );
}
