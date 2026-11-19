import { defineAction, run, s } from '@/actions';
import { directoryQuery, meQuery } from '@/data';
import { teamSlug } from '@/features/team/routing';
import { companiesBridge } from './companies';
import { desktop, openCallWindow } from '@/desktop';

/* What the menu's own controls do, as actions so Tico can do them too: add a teammate from the Team section's plus,
   and, in the desktop app, switch to or add a company. The account menu's rows are other parts' actions
   (team.open, settings.open, settings.setTheme, settings.signOut). */

const here = (path: string) => path.replace(/^\/next(?=\/|$)/, '') || '/';

/** Add teammate opens over a team's page: this one when a team's page is open, else my team's, else the first. */
export const addTeammate = defineAction({
  id: 'shell.addTeammate',
  title: 'Add a teammate',
  kind: 'view',
  input: s.object({}),
  run: async ({ router, queryClient, via }) => {
    const dir = queryClient.getQueryData(directoryQuery.queryKey);
    const me = queryClient.getQueryData(meQuery.queryKey);
    const [, top, who] = here(router.state.location.pathname).split('/');
    const onTeam = top === 'team' && !!who && !!dir && !dir.byId[decodeURIComponent(who)] && !dir.byId[`human:${decodeURIComponent(who)}`];
    if (!onTeam) {
      const team = (me && dir?.byId[me.id]?.team) || dir?.actors.find((a) => a.team)?.team;
      if (!team) throw new Error('no team to add to yet');
      await router.navigate({ to: `/team/${encodeURIComponent(teamSlug(team))}` });
    }
    const r = await run('team.addTeammate', {}, { via });
    if (!r.ok) throw new Error(r.error);
  },
  did: () => 'Opened Add teammate',
});

/** Desktop only: the window shows that company. */
export const switchCompany = defineAction({
  id: 'shell.switchCompany',
  title: 'Switch to another company',
  kind: 'view',
  input: s.object({ id: s.string({ min: 1, max: 200, what: "a company's id from the desktop app's list" }) }),
  can: () => !!companiesBridge(),
  run: async (_ctx, { id }) => {
    const b = companiesBridge();
    if (!b) throw new Error('only the desktop app holds other companies');
    await b.switchTo(id);
  },
  did: () => 'Switched company',
});

/** Desktop only: its Add company page. Signing in to another company is the person's own (tico: false). */
export const addCompany = defineAction({
  id: 'shell.addCompany',
  title: 'Add a company',
  kind: 'view',
  tico: false,
  input: s.object({}),
  can: () => !!companiesBridge(),
  run: async () => {
    const b = companiesBridge();
    if (!b) throw new Error('only the desktop app adds companies');
    await b.add();
  },
  did: () => 'Opened Add company',
});

/** Desktop only: the call moves to its own small window. The room lives in one window at a time, so this one leaves
    it first and the small window joins (about a second, platforms.md 4.6); this window goes back to the start. */
export const openCallInWindow = defineAction({
  id: 'shell.openCallWindow',
  title: 'Open the call in its own window',
  kind: 'view',
  tico: false,
  input: s.object({ id: s.string({ min: 1, max: 200, what: 'a call id' }) }),
  can: () => !!desktop(),
  run: async ({ router, via }, { id }) => {
    await run('calls.leave', {}, { via });
    await openCallWindow(id);
    if (/^\/call\//.test(here(router.state.location.pathname))) await router.navigate({ to: '/' });
  },
  did: () => 'Opened the call in its own window',
});
