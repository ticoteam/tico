import { Link, useRouter, type LinkProps } from '@tanstack/react-router';
import { Fragment, createContext, use, useEffect, type MouseEvent } from 'react';
import { Icon } from '@/kit/Icon';
import { stepBackIs, watchSteps } from '@/lib';

/* The trail over every page: the company, the section, any page in between, then the open page. Read from the address
   alone, plus the names it needs; the open page's own name (its h1, tico/pageName) ends it when the address does not
   already name it (a doc, a task, a file opened with `?file=`). Every part but the last is a link. */

export interface TrailPart {
  label: string;
  /** Where it goes; none for the open page, and for Team, which has no page of its own. */
  to?: string;
}

export interface TrailNames {
  company: string;
  pipelines?: readonly { id: string; name: string }[];
  /** The teammate `/team/<who>` names. */
  who?: string;
  /** The open page's name, its h1. */
  page: string;
}

/** "catch-up" → "Catch up": a fixed part of an address, as the menu names it. */
const words = (slug: string) => {
  const s = slug.replace(/-/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
};

const decode = (s: string) => {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
};

/* the fixed pages under a section; anything else there is one thing by its id (a doc, a thread) */
const LIBRARY = new Set(['docs', 'files', 'tools', 'skills', 'contacts']);
const THREADS = new Set(['mail', 'messaging', 'catch-up']);

export function trailOf(path: string, n: TrailNames): TrailPart[] {
  const [top, a, b] = path
    .replace(/^\/next(?=\/|$)/, '')
    .split('/')
    .filter(Boolean)
    .map(decode);
  const parts: TrailPart[] = [{ label: n.company, to: '/' }];
  const add = (label: string, to?: string) => parts.push({ label, to });
  const at = (...s: string[]) => `/${s.map(encodeURIComponent).join('/')}`;
  let page = n.page;
  switch (top) {
    case undefined:
    case 'call':
      break;
    case 'tasks':
    case 'task':
      add('Tasks', '/tasks');
      // `all` holds the tasks with no pipeline of their own; `<pipeline>/<n>/<tab>`'s tab is the page's own tab row
      if (top === 'tasks' && a && a !== 'all') add(n.pipelines?.find((p) => p.id === a)?.name ?? '', at('tasks', a));
      if (top === 'tasks' && b === 'edit') add('Edit', at('tasks', a!, 'edit'));
      break;
    case 'flow':
      add('Flow', '/flow');
      // Flow's own page is Tasks with Flow pressed: its h1 says Tasks
      if (!a) page = '';
      break;
    case 'roadmap':
      add('Tasks', '/tasks');
      add('Roadmap', '/roadmap');
      break;
    case 'company':
      add('Company', '/company');
      if (a) add(words(a), at('company', a));
      // Risk's disputes is a page under it; other company pages' second part is a tab
      if (a === 'risk' && b) add(words(b), at('company', a, b));
      break;
    case 'library':
      add('Library', '/library');
      if (a) add(LIBRARY.has(a) ? words(a) : 'Docs', at('library', LIBRARY.has(a) ? a : 'docs'));
      break;
    case 'team':
      add('Team');
      if (a) add(n.who ?? '', at('team', a));
      break;
    case 'threads':
      add('Threads', '/threads');
      if (a && THREADS.has(a)) add(words(a), at('threads', a));
      break;
    case 'updates':
      add('Updates', '/updates');
      // daily and weekly are its tabs
      if (a === 'archive') add('Archive', at('updates', a));
      break;
    case 'settings':
      add('Settings', '/settings');
      if (a) add(words(a), at('settings', a));
      break;
    default:
      add(words(top), at(top));
  }
  const named = parts.filter((p) => p.label);
  if (page && !named.some((p) => p.label === page)) named.push({ label: page });
  const last = named.at(-1);
  if (last) named[named.length - 1] = { label: last.label };
  return named;
}

/* addresses that only pass on to another page: nothing to go back up to */
const HOPS = new Set(['/', '/tasks', '/library']);

/** Where a phone's Back goes: up the trail one step, unless that step is a section that only passes on, or Team. */
export function upOf(parts: readonly TrailPart[]): Required<TrailPart> | undefined {
  const up = parts.at(-2);
  return up?.to && !HOPS.has(up.to) ? { label: up.label, to: up.to } : undefined;
}

/** The trail the frame draws, for a page's head (a phone's Back). Empty outside the frame. */
export const TrailContext = createContext<readonly TrailPart[]>([]);
export const useTrail = () => use(TrailContext);

/* The trail on the frame's ground, over the sheets (on a phone, over the page): "TIDY › Tasks › Legal › <task>".
   A part goes where the menu would; when the step before in history is that very page, it goes back to it instead,
   so a board comes back with its filters and place. */
export function Trail({ parts }: { parts: readonly TrailPart[] }) {
  const router = useRouter();
  useEffect(() => watchSteps(router), [router]);
  const back = (e: MouseEvent, to: string) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0 || !stepBackIs(router, to)) return;
    e.preventDefault();
    router.history.back();
  };
  return (
    <nav className="shell-trail" aria-label="Breadcrumb">
      {parts.map((p, i) => (
        <Fragment key={i}>
          {i > 0 && <Icon name="chevron" />}
          {p.to ? (
            <Link to={p.to as LinkProps['to']} title={p.label} onClick={(e) => back(e, p.to!)}>
              {p.label}
            </Link>
          ) : (
            <span title={p.label} aria-current={i === parts.length - 1 ? 'page' : undefined}>
              {p.label}
            </span>
          )}
        </Fragment>
      ))}
    </nav>
  );
}
