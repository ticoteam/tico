import { isCancelledError, type QueryClient } from '@tanstack/react-query';
import { directoryQuery } from '@/data';
import { ADD_STEPS, TABS, type AddStep, type Tab } from './actions';
import { bareId, resolveWho } from './model';
import { agentsQuery, computersQuery, groupsQuery, instructionsQuery, openTasksQuery, peopleQuery, slackQuery } from './queries';

// What the routes, the shell and other features use before a teammate's page loads: the address check, the reads and
// a teammate's address. No components here: the first load imports this file, and a page comes through exports.ts.
export { agentsQuery, groupsQuery, computersQuery, type AgentRow } from './queries';
export { teamSlug, whoHref } from './model';

export interface TeamSearch {
  tab?: Tab;
  add?: AddStep;
}

/** `?tab=settings|instructions|model|computer|screen|slack&add=pick|new|template|person`; anything else is dropped. */
export function validateTeamSearch(raw: Record<string, unknown>): TeamSearch {
  const out: TeamSearch = {};
  if (TABS.includes(raw.tab as Tab)) out.tab = raw.tab as Tab;
  if (ADD_STEPS.includes(raw.add as AddStep)) out.add = raw.add as AddStep;
  return out;
}

/** `/team/<who>`'s first paint: who it is, then the one read the page it opens on draws. A failed read is the router's
    fault page (no access on a 403, as a person's Slack is for anyone but them and the owner). */
export async function loadWho(qc: QueryClient, who: string, tab: Tab | undefined): Promise<unknown> {
  // The shell's observers (the menu, a team's thread) can detach during mount while the route still waits on a read.
  const again = <T,>(read: () => Promise<T>) => read().catch((error: unknown) => (isCancelledError(error) ? read() : Promise.reject(error)));
  const [dir, groups] = await Promise.all([again(() => qc.ensureQueryData(directoryQuery)), again(() => qc.ensureQueryData(groupsQuery))]);
  const found = resolveWho(who, dir, groups);
  if (found?.kind === 'agent') {
    if (tab === 'computer' || tab === 'screen') return qc.ensureQueryData(computersQuery);
    if (tab === 'instructions') return qc.ensureQueryData(instructionsQuery(found.actor.slug ?? found.actor.id.slice(4)));
    if (tab === 'settings' || tab === 'model') return qc.ensureQueryData(agentsQuery);
  }
  if (found?.kind === 'person' && tab === 'settings') return qc.ensureQueryData(peopleQuery);
  if (found?.kind === 'person' && tab === 'slack') return qc.ensureQueryData(slackQuery(bareId(found.actor.id)));
  if (found && found.kind !== 'team') return qc.ensureQueryData(openTasksQuery(found.actor.id));
}
