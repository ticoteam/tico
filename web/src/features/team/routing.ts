import type { QueryClient } from '@tanstack/react-query';
import { redirect } from '@tanstack/react-router';
import { directoryQuery } from '@/data';
import { ADD_STEPS, TABS, type AddStep, type Tab } from './actions';
import { bareId, resolveWho, SEAT_PREFIX } from './model';
import { agentsQuery, computersQuery, groupsQuery, instructionsQuery, openTasksQuery, peopleQuery, seatAgentQuery, slackQuery } from './queries';

// What the routes, the shell and other features use before a teammate's page loads: the address check, the reads and
// a teammate's address. No components here: the first load imports this file, and a page comes through exports.ts.
export { agentsQuery, groupsQuery, computersQuery, teammateAccessQuery, type AgentRow, type Computer } from './queries';
export { moveAgentWithFeedback } from './actions';
export { teamSlug, whoHref, modelName, effortLabel } from './model';

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

/** Where an old team link goes: there are no team pages; the menu shows the team (`?team=`), and Add teammate opens
    onto it when the link asked for it. */
export const teamAddress = (team: string, add?: AddStep) => ({ to: '/' as const, search: { team, ...(add ? { add } : {}) } });

/** `/team/<who>`'s first paint: who it is, then the one read the page it opens on draws. A failed read is the router's
    fault page (no access on a 403, as a person's Slack is for anyone but them and the owner). A team's address goes
    home, with the team shown in the menu. */
export async function loadWho(qc: QueryClient, who: string, tab: Tab | undefined, add?: AddStep): Promise<unknown> {
  // A requested seat's page draws its own states (a closed request is its empty page, not the router's fault page).
  if (who.startsWith(SEAT_PREFIX)) return qc.prefetchQuery(seatAgentQuery(who.slice(SEAT_PREFIX.length)));
  const [dir, groups] = await Promise.all([qc.ensureQueryData(directoryQuery), qc.ensureQueryData(groupsQuery)]);
  const found = resolveWho(who, dir, groups);
  if (found?.kind === 'team') throw redirect({ ...teamAddress(found.group.id, add), replace: true } as never);
  if (found?.kind === 'agent') {
    if (tab === 'computer' || tab === 'screen') return qc.ensureQueryData(computersQuery);
    if (tab === 'instructions') return qc.ensureQueryData(instructionsQuery(found.actor.slug ?? found.actor.id.slice(4)));
    if (tab === 'settings' || tab === 'model') return qc.ensureQueryData(agentsQuery);
  }
  if (found?.kind === 'person' && tab === 'settings') return qc.ensureQueryData(peopleQuery);
  if (found?.kind === 'person' && tab === 'slack') return qc.ensureQueryData(slackQuery(bareId(found.actor.id)));
  if (found) return qc.ensureQueryData(openTasksQuery(found.actor.id));
}
