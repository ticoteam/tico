import { defineAction, s } from '@/actions';
import { ApiError } from '@/api/client';
import { directoryQuery } from '@/data';
import { toast } from '@/kit/Toast';
import {
  addAgent,
  archiveAgent,
  changeAgent,
  computersQuery,
  controlAgent,
  invitePerson,
  moveToTeam,
  removePerson,
  renamePerson,
  restartComputer,
  setAgentModel,
  setPersonRole,
  setPersonTitle,
} from './queries';

const slug = s.string({ min: 1, max: 100, what: "an agent's slug" });
const person = s.string({ min: 1, max: 100, what: "a person's id" });
const team = s.string({ min: 1, max: 100, what: "a team's id" });
const page = (who: string) => `/team/${encodeURIComponent(who)}`;
const onPage = (who: string) => ({ kind: 'page', id: page(who) });

/** The server's words for a refusal, else a plain line. */
const reason = (e: unknown, fallback: string) => (e instanceof ApiError && e.status !== 0 && e.message ? e.message : fallback);

/** Run a change; on refusal say so (the change on screen is already taken back) and pass the refusal on. */
async function said<T>(fallback: string, send: () => Promise<T>): Promise<T> {
  try {
    return await send();
  } catch (e) {
    toast({ text: reason(e, fallback), kind: 'failed' });
    throw e;
  }
}

export const TABS = ['settings', 'instructions', 'model', 'computer', 'screen'] as const;
export type Tab = (typeof TABS)[number];

export const openTeammate = defineAction({
  id: 'team.open',
  title: 'Open a teammate',
  kind: 'view',
  input: s.object({ who: s.string({ min: 1, max: 120, what: "an agent's slug, a person's id or a team" }), tab: s.optional(s.oneOf(TABS)) }),
  run: ({ router }, { who, tab }) => router.navigate({ to: page(who), search: tab ? { tab } : {} }),
  show: ({ router }, { who, tab }) => router.navigate({ to: page(who), search: tab ? { tab } : {} }),
  did: ({ who }) => `Opened ${who}`,
  target: ({ who }) => onPage(who),
  offer: ({ queryClient }) =>
    (queryClient.getQueryData(directoryQuery.queryKey)?.actors ?? []).map((a) => ({
      input: { who: a.kind === 'agent' && a.slug ? a.slug : a.id.slice(a.id.indexOf(':') + 1) },
      title: `Open ${a.name}`,
    })),
});

export const ADD_STEPS = ['pick', 'new', 'template', 'person'] as const;
export type AddStep = (typeof ADD_STEPS)[number];

/** Add teammate: the dialog over the team page on screen (`?add=`). */
export const openAdd = defineAction({
  id: 'team.addTeammate',
  title: 'Add a teammate',
  kind: 'view',
  input: s.object({ step: s.optional(s.oneOf(ADD_STEPS)) }),
  can: ({ router }) => router.state.location.pathname.startsWith('/team/'),
  run: ({ router }, { step }) =>
    router.navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, add: step ?? 'pick' }) }),
  did: () => 'Opened Add teammate',
  target: () => ({ kind: 'team', id: 'add' }),
});

/* ---------------------------------------------------------------- agents */

export const rename = defineAction({
  id: 'agents.rename',
  title: 'Rename an agent',
  kind: 'change',
  input: s.object({ slug, name: s.string({ min: 1, max: 100 }) }),
  run: (_ctx, { slug, name }) => said("Couldn't rename it", () => changeAgent(slug, { display_name: name })),
  did: ({ name }) => `Renamed it ${name}`,
  target: ({ slug }) => onPage(slug),
});

export const setRole = defineAction({
  id: 'agents.setRole',
  title: "Change an agent's role",
  kind: 'change',
  input: s.object({ slug, role: s.string({ max: 2000 }) }),
  run: (_ctx, { slug, role }) => said("Couldn't change the role", () => changeAgent(slug, { description: role })),
  did: () => 'Changed the role',
  target: ({ slug }) => onPage(slug),
});

export const setReportsTo = defineAction({
  id: 'agents.setReportsTo',
  title: 'Change who an agent reports to',
  kind: 'change',
  input: s.object({ slug, to: s.string({ max: 120, what: "a teammate's slug or person id; empty for nobody" }) }),
  run: (_ctx, { slug, to }) => said("Couldn't change it", () => changeAgent(slug, { reports_to: to })),
  did: () => 'Changed who it reports to',
  target: ({ slug }) => onPage(slug),
});

export const setModel = defineAction({
  id: 'agents.setModel',
  title: "Change an agent's model",
  kind: 'change',
  input: s.object({
    slug,
    model: s.string({ min: 1, max: 120, what: 'a model id from GET /api/v2/models' }),
    effort: s.optional(s.string({ max: 20, what: "one of the model's efforts; empty for its default" })),
    harness: s.optional(s.string({ max: 40 })),
  }),
  run: (_ctx, { slug, ...pick }) => said("Couldn't change the model", () => setAgentModel(slug, pick)),
  did: ({ model }) => `Moved it to ${model}`,
  target: ({ slug }) => onPage(slug),
});

export const pause = defineAction({
  id: 'agents.pause',
  title: 'Pause an agent',
  kind: 'change',
  input: s.object({ slug }),
  run: (_ctx, { slug }) => said("Couldn't pause it", () => controlAgent(slug, 'pause')),
  did: () => 'Paused it',
  target: ({ slug }) => onPage(slug),
});

export const resume = defineAction({
  id: 'agents.resume',
  title: 'Resume an agent',
  kind: 'change',
  input: s.object({ slug }),
  run: (_ctx, { slug }) => said("Couldn't resume it", () => controlAgent(slug, 'resume')),
  did: () => 'Resumed it',
  target: ({ slug }) => onPage(slug),
});

/** Archive: the page asks first. Its open tasks stay where they are. */
export const archive = defineAction({
  id: 'agents.archive',
  title: 'Archive an agent',
  kind: 'change',
  input: s.object({ slug }),
  // The page asks first; Tico may archive without that question, so the did line says plainly what happened.
  run: async ({ router, queryClient }, { slug }) => {
    const name = queryClient.getQueryData(directoryQuery.queryKey)?.byId[slug]?.name ?? slug;
    await said("Couldn't archive it", () => archiveAgent(slug));
    await router.navigate({ to: '/' });
    return { name };
  },
  did: ({ slug }, out) => `Archived ${out?.name ?? slug}. It takes no more work; its tasks and history stay.`,
});

export const add = defineAction({
  id: 'agents.add',
  title: 'Add an agent',
  kind: 'change',
  input: s.object({
    name: s.string({ min: 1, max: 100 }),
    role: s.optional(s.string({ max: 2000 })),
    team: s.optional(team),
    template: s.optional(s.string({ max: 100, what: "a template's slug" })),
    computer: s.optional(s.string({ max: 100, what: "a computer's id" })),
  }),
  run: async ({ router }, input) => {
    const made = await said("Couldn't add the agent", () => addAgent(input));
    await router.navigate({ to: page(made.slug) });
    return made;
  },
  did: ({ name }) => `Added ${name}`,
  target: (_input, out) => (out ? onPage(out.slug) : null),
});

/* ---------------------------------------------------------------- teams and people */

export const move = defineAction({
  id: 'team.move',
  title: 'Move a teammate to a team',
  kind: 'change',
  input: s.object({ kind: s.oneOf(['agent', 'person'] as const), id: s.string({ min: 1, max: 100, what: "an agent's slug or a person's id" }), team }),
  run: (_ctx, { kind, id, team }) => said("Couldn't move them", () => moveToTeam(kind, id, team)),
  did: () => 'Moved them',
  target: ({ id }) => onPage(id),
});

export const invite = defineAction({
  id: 'people.invite',
  title: 'Invite a person',
  kind: 'change',
  input: s.object({
    email: s.string({ min: 3, max: 200, pattern: /^[^@\s]+@[^@\s]+$/,what: 'an email address' }),
    name: s.optional(s.string({ max: 100 })),
    team: s.optional(team),
    role: s.optional(s.oneOf(['member', 'admin'] as const)),
  }),
  run: ({ router }, input) =>
    said("Couldn't send the invite", async () => {
      const out = await invitePerson(input);
      await router.navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, add: undefined }) });
      return out;
    }),
  did: ({ email }) => `Invited ${email}`,
  target: (_input, out) => (out ? onPage(out.person) : null),
});

export const setPermissions = defineAction({
  id: 'people.setRole',
  title: "Change a person's permissions",
  kind: 'change',
  input: s.object({ person, role: s.oneOf(['member', 'admin'] as const) }),
  run: (_ctx, { person, role }) => said("Couldn't change it", () => setPersonRole(person, role)),
  did: ({ role }) => (role === 'admin' ? 'Made them an admin' : 'Made them a member'),
  target: ({ person }) => onPage(person),
});

export const renameOne = defineAction({
  id: 'people.rename',
  title: 'Rename a person',
  kind: 'change',
  input: s.object({ person, name: s.string({ min: 1, max: 120 }) }),
  run: (_ctx, { person, name }) => said("Couldn't rename them", () => renamePerson(person, name)),
  did: ({ name }) => `Renamed them ${name}`,
  target: ({ person }) => onPage(person),
});

export const setTitle = defineAction({
  id: 'people.setTitle',
  title: "Change a person's role",
  kind: 'change',
  input: s.object({ person, title: s.string({ max: 200 }) }),
  run: (_ctx, { person, title }) => said("Couldn't change the role", () => setPersonTitle(person, title)),
  did: () => 'Changed the role',
  target: ({ person }) => onPage(person),
});

/** Their sign-in and API tokens end; their history stays. Only a person does this, and the page asks first. */
export const remove = defineAction({
  id: 'people.remove',
  title: 'Remove a person',
  kind: 'change',
  tico: false,
  input: s.object({ person }),
  run: async ({ router }, { person }) => {
    await said("Couldn't remove them", () => removePerson(person));
    await router.navigate({ to: '/' });
  },
  did: () => 'Removed them',
});

/* ---------------------------------------------------------------- computers */

/** The page asks first: the agents on it stop their turns. */
export const restart = defineAction({
  id: 'computers.restart',
  title: 'Restart a computer',
  kind: 'change',
  input: s.object({ id: s.string({ min: 1, max: 100, what: "a computer's id" }) }),
  // The page asks first; Tico may restart without that question, so the did line says plainly what happened.
  run: async ({ queryClient }, { id }) => {
    const label = queryClient.getQueryData(computersQuery.queryKey)?.find((c) => c.id === id)?.label ?? 'the computer';
    await said("Couldn't restart it", () => restartComputer(id));
    return { label };
  },
  did: (_input, out) => `Restarted ${out?.label ?? 'the computer'}. Its agents stopped their turns and start again in about a minute.`,
  target: () => ({ kind: 'team', id: 'computer' }),
});
