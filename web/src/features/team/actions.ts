import * as z from 'zod/mini';
import { defineAction, refusalFor, toldOf, type Ctx } from '@/actions';
import { ApiError } from '@/api/client';
import { directoryQuery } from '@/data';
import { toast } from '@/kit/Toast';
import {
  addAgent,
  addCloudAgent,
  archiveAgent,
  cancelCloudRequest,
  cancelLogin,
  cloudAgentsQuery,
  changeAgent,
  computersQuery,
  controlAgent,
  giveTask,
  groupsQuery,
  agentsQuery,
  instructionsQuery,
  invitePerson,
  isOpenRequest,
  makeCopy,
  makePassword,
  moveAgent,
  moveToTeam,
  newCredential,
  openBrowser,
  openScreenSession,
  removePerson,
  peopleRosterQuery,
  renamePerson,
  requestCloudAgent,
  restartComputer,
  seatAgentQuery,
  seatsQuery,
  sendBrowserInput,
  sendLoginCode,
  setBrowserControl,
  setAgentModel,
  setAgentRepos,
  setCompactAt,
  COMPACT_AT,
  setAgentStatus,
  setProfile,
  setScreenMode,
  setPersonFields,
  setPersonRole,
  setPersonTitle,
  setTeammateAccess,
  setWorksFor,
  answerDream,
  startDream,
  stopDream,
  wakeUp,
  startLogin,
  startLogout,
  saveTabs,
  taskTabsQuery,
  tellTico,
  TICO_TEXT_MAX,
  type TaskTab,
} from './queries';
import { instructionsChange, LIST_FIELDS, managerOf, rowOf, seatHref, type ListLine } from './model';

const slug = z.string().check(z.minLength(1), z.maxLength(100), z.describe("an agent's slug"));
const person = z.string().check(z.minLength(1), z.maxLength(100), z.describe("a person's id"));
const team = z.string().check(z.minLength(1), z.maxLength(100), z.describe("a team's id"));
const page = (who: string) => `/team/${encodeURIComponent(who)}`;
const onPage = (who: string) => ({ kind: 'page', id: page(who) });

/** A refusal's toast: the short words, then the server's reason when it gave a safe one. */
const reason = (e: unknown, fallback: string) => refusalFor(e, fallback, fallback);

/** Run a change; on refusal say so (the change on screen is already taken back) and pass the refusal on. */
async function said<T>(fallback: string, send: () => Promise<T>): Promise<T> {
  try {
    return await send();
  } catch (e) {
    toast(reason(e, fallback));
    throw toldOf(e);
  }
}

export const TABS = ['settings', 'instructions', 'model', 'computer', 'screen', 'slack'] as const;
export type Tab = (typeof TABS)[number];

export const openTeammate = defineAction({
  id: 'team.open',
  title: 'Open a teammate',
  kind: 'view',
  input: z.strictObject({ who: z.string().check(z.minLength(1), z.maxLength(120), z.describe("an agent's slug, a person's id or a team")), tab: z.optional(z.enum(TABS)) }),
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

export const ADD_STEPS = ['pick', 'new', 'template', 'person', 'cloud'] as const;
export type AddStep = (typeof ADD_STEPS)[number];

/** Add teammate: the dialog over any page (`?add=`), onto the team the address names (`?team=`, which the menu shows)
    or the one given. */
export const openAdd = defineAction({
  id: 'team.addTeammate',
  title: 'Add a teammate',
  kind: 'view',
  input: z.strictObject({ step: z.optional(z.enum(ADD_STEPS)), team: z.optional(team) }),
  run: ({ router }, { step, team: to }) =>
    router.navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, add: step ?? 'pick', ...(to ? { team: to } : {}) }) }),
  did: () => 'Opened Add teammate',
  target: () => ({ kind: 'team', id: 'add' }),
});

/* ---------------------------------------------------------------- agents */

export const rename = defineAction({
  id: 'agents.rename',
  title: 'Rename an agent',
  kind: 'change',
  input: z.strictObject({ slug, name: z.string().check(z.minLength(1), z.maxLength(100)) }),
  run: (_ctx, { slug, name }) => said("Couldn't rename it", () => changeAgent(slug, { display_name: name })),
  did: ({ name }) => `Renamed it ${name}`,
  target: ({ slug }) => onPage(slug),
});

export const setRole = defineAction({
  id: 'agents.setRole',
  title: "Change an agent's role",
  kind: 'change',
  input: z.strictObject({ slug, role: z.string().check(z.maxLength(2000)) }),
  run: (_ctx, { slug, role }) => said("Couldn't change the role", () => changeAgent(slug, { description: role })),
  did: () => 'Changed the role',
  target: ({ slug }) => onPage(slug),
});

export const setReportsTo = defineAction({
  id: 'agents.setReportsTo',
  title: 'Change who an agent reports to',
  kind: 'change',
  input: z.strictObject({ slug, to: z.string().check(z.maxLength(120), z.describe("a teammate's slug or person id; empty for nobody")) }),
  run: (_ctx, { slug, to }) => said("Couldn't change it", () => changeAgent(slug, { reports_to: to })),
  did: () => 'Changed who it reports to',
  target: ({ slug }) => onPage(slug),
});

export const setModel = defineAction({
  id: 'agents.setModel',
  title: "Change an agent's model",
  kind: 'change',
  input: z.strictObject({
    slug,
    model: z.string().check(z.minLength(1), z.maxLength(120), z.describe('a model id from GET /api/v2/models')),
    effort: z.optional(z.string().check(z.maxLength(20), z.describe("one of the model's efforts; empty for its default"))),
    harness: z.optional(z.string().check(z.maxLength(40))),
  }),
  run: (_ctx, { slug, ...pick }) => said("Couldn't change the model", () => setAgentModel(slug, pick)),
  did: ({ model }) => `Moved it to ${model}`,
  target: ({ slug }) => onPage(slug),
});

export const setCompact = defineAction({
  id: 'agents.setCompact',
  title: "Change where an agent's conversation compacts",
  kind: 'change',
  input: z.strictObject({ slug, at: z.nullable(z.literal(COMPACT_AT)) }),
  run: (_ctx, { slug, at }) => said("Couldn't change it", () => setCompactAt(slug, at)),
  did: () => 'Changed where it compacts',
  target: ({ slug }) => onPage(slug),
});

export const pause = defineAction({
  id: 'agents.pause',
  title: 'Pause an agent',
  kind: 'change',
  input: z.strictObject({ slug }),
  run: (_ctx, { slug }) => said("Couldn't pause it", () => controlAgent(slug, 'pause')),
  did: () => 'Paused it',
  target: ({ slug }) => onPage(slug),
});

export const resume = defineAction({
  id: 'agents.resume',
  title: 'Resume an agent',
  kind: 'change',
  input: z.strictObject({ slug }),
  run: (_ctx, { slug }) => said("Couldn't resume it", () => controlAgent(slug, 'resume')),
  did: () => 'Resumed it',
  target: ({ slug }) => onPage(slug),
});

/** Archive: the page asks first, and who takes its open tasks (its operator unless another agent is picked). */
export const archive = defineAction({
  id: 'agents.archive',
  title: 'Archive an agent',
  kind: 'change',
  input: z.strictObject({ slug, successor: z.optional(z.string().check(z.maxLength(100), z.describe("the id of the agent that takes its open tasks; empty for the person who runs it"))) }),
  // The page asks first; Tico may archive without that question, so the did line says plainly what happened.
  run: async ({ router, queryClient }, { slug, successor }) => {
    const dir = queryClient.getQueryData(directoryQuery.queryKey);
    const name = dir?.byId[slug]?.name ?? slug;
    const roster = queryClient.getQueryData(agentsQuery.queryKey);
    const row = roster?.find((r) => r.slug === slug);
    const heir = successor
      ? (dir?.byId[`bot:${successor}`]?.name ?? roster?.find((r) => r.id === successor)?.display_name ?? 'the agent you picked')
      : row?.owner_id
        ? (dir?.byId[`human:${row.owner_id}`]?.name ?? 'the person who runs it')
        : 'the person who runs it';
    await said("Couldn't archive it", () => archiveAgent(slug, successor));
    await router.navigate({ to: '/' });
    return { name, heir };
  },
  did: ({ slug }, out) => `Archived ${out?.name ?? slug}. Its open tasks went to ${out?.heir ?? 'the person who runs it'}.`,
});

/** My copy of an agent that allows copies: on my computer, or planned until I add one. Opens it. */
export const copy = defineAction({
  id: 'agents.makeCopy',
  title: 'Make my copy of an agent',
  kind: 'change',
  input: z.strictObject({ slug, computer: z.optional(z.string().check(z.minLength(1), z.maxLength(100), z.describe("one of my computers' ids"))) }),
  run: async ({ router }, { slug, computer }) => {
    const made = await said("Couldn't make your copy", () => makeCopy(slug, computer));
    await router.navigate({ to: page(made.slug), search: { tab: 'settings' } });
    return made;
  },
  did: (_input, out) => (out?.status === 'planned' ? 'Made your copy. It starts once you add a computer.' : 'Made your copy'),
  target: (_input, out) => (out ? onPage(out.slug) : null),
});

export const status = defineAction({
  id: 'agents.setStatus',
  title: "Set a copy's status: setting up, active or paused",
  kind: 'change',
  input: z.strictObject({ slug, status: z.enum(['planned', 'active', 'paused'] as const) }),
  run: (_ctx, { slug, status }) => said("Couldn't change it", () => setAgentStatus(slug, status)),
  did: () => 'Changed its status',
  target: ({ slug }) => onPage(slug),
});

/** Leaving Custom deletes the wake rules it copied: refused unless `dropRules`, which only a person sends. */
export const profile = defineAction({
  id: 'agents.setProfile',
  title: 'Change how an agent takes work (its profile)',
  kind: 'change',
  input: z.strictObject({
    slug,
    profile: z.enum(['doer', 'reviewer', 'lead', 'manager', 'responder', 'monitor', 'custom'] as const),
    dropRules: z.optional(z.boolean()),
  }),
  can: (ctx, { dropRules }) => !(dropRules && ctx.via === 'tico'),
  run: async (_ctx, { slug, profile, dropRules }) => {
    try {
      return await setProfile(slug, profile, dropRules);
    } catch (e) {
      // Leaving Custom: the page asks, then sends it again with dropRules.
      if (e instanceof ApiError && e.code === 'conflict') throw e;
      toast(reason(e, "Couldn't change it"));
      throw toldOf(e);
    }
  },
  did: () => 'Changed its profile',
  target: ({ slug }) => onPage(slug),
});

export const moveAgentWithFeedback = (slug: string, computer: string) => said("Couldn't move it", () => moveAgent(slug, computer));

export const moveComputer = defineAction({
  id: 'agents.move',
  title: 'Move an agent to another computer',
  kind: 'change',
  input: z.strictObject({ slug, computer: z.string().check(z.minLength(1), z.maxLength(100), z.describe("a computer's id")) }),
  run: async ({ queryClient }, { slug, computer }) => {
    const label = queryClient.getQueryData(computersQuery.queryKey)?.find((c) => c.id === computer)?.label ?? 'the computer';
    await moveAgentWithFeedback(slug, computer);
    void queryClient.invalidateQueries({ queryKey: computersQuery.queryKey });
    return { label };
  },
  did: (_input, out) => `Moved it to ${out?.label ?? 'the computer'}. It starts there after its turn ends.`,
  target: ({ slug }) => onPage(slug),
});

export const worksFor = defineAction({
  id: 'agents.setWorksFor',
  title: 'Change who an agent works for',
  kind: 'change',
  input: z.strictObject({ slug, people: z.array(person).check(z.maxLength(100)) }),
  // Who an agent works for is ordinary setup among teammates: Tico's too.
  run: (_ctx, { slug, people }) => said("Couldn't change it", () => setWorksFor(slug, people)),
  did: () => 'Changed who it works for',
  target: ({ slug }) => onPage(slug),
});

const repo = z.strictObject({ full_name: z.string().check(z.minLength(1), z.maxLength(200)), access: z.enum(['read', 'write'] as const) });

/** Its own repository only, every ticked one at one level, or chosen ones each at its level. */
export const setRepositories = defineAction({
  id: 'agents.setRepositories',
  title: 'Change which repositories an agent reaches',
  kind: 'change',
  input: z.strictObject({
    slug,
    mode: z.enum(['own', 'all', 'chosen'] as const),
    all_access: z.optional(z.enum(['read', 'write'] as const)),
    chosen: z.optional(z.array(repo).check(z.maxLength(500))),
  }),
  run: (_ctx, { slug, ...change }) => said("Couldn't change its repositories", () => setAgentRepos(slug, change)),
  did: () => 'Changed its repositories',
  target: ({ slug }) => onPage(slug),
});

/** Shown once, and the one before stops at once: only a person makes it. */
export const credential = defineAction({
  id: 'agents.newCredential',
  title: 'Make a new credential for an agent that runs elsewhere',
  kind: 'change',
  tico: false,
  input: z.strictObject({ slug }),
  run: (_ctx, { slug }) => said("Couldn't make a credential", () => newCredential(slug)),
  did: () => 'Made a new credential',
  target: ({ slug }) => onPage(slug),
});

/* ---------------------------------------------------------------- dream */

const dreamTask = z.string().check(z.minLength(1), z.maxLength(100), z.describe("the dream's task id"));
const onDream = (slug: string) => ({ kind: 'dream', id: slug });

export const dream = defineAction({
  id: 'agents.dream',
  title: 'Start a dream: an agent cleans up its own memory',
  kind: 'change',
  input: z.strictObject({ slug }),
  run: (_ctx, { slug }) => said("Couldn't start the dream", () => startDream(slug)),
  did: () => 'Started a dream',
  target: ({ slug }) => onDream(slug),
});

export const dreamAnswer = defineAction({
  id: 'agents.answerDream',
  title: "Answer a dreaming agent's questions",
  kind: 'change',
  input: z.strictObject({ slug, task: dreamTask, text: z.string().check(z.minLength(1), z.maxLength(8000)) }),
  run: (_ctx, { slug, task, text }) => said("Couldn't send the answers", () => answerDream(slug, task, text)),
  did: () => 'Sent the answers',
  target: ({ slug }) => onDream(slug),
});

export const dreamStop = defineAction({
  id: 'agents.stopDream',
  title: "Stop an agent's dream",
  kind: 'change',
  input: z.strictObject({ slug, task: dreamTask }),
  run: (_ctx, { slug, task }) => said("Couldn't stop the dream", () => stopDream(slug, task)),
  did: () => 'Stopped the dream',
  target: ({ slug }) => onDream(slug),
});

export const dreamWake = defineAction({
  id: 'agents.wakeUp',
  title: 'Put away a finished dream',
  kind: 'view',
  input: z.strictObject({ slug, task: dreamTask }),
  run: (_ctx, { slug, task }) => wakeUp(slug, task),
  did: () => 'Woke up',
  target: ({ slug }) => onPage(slug),
});

export const add = defineAction({
  id: 'agents.add',
  title: 'Add an agent',
  kind: 'change',
  input: z.strictObject({
    name: z.string().check(z.minLength(1), z.maxLength(100)),
    role: z.optional(z.string().check(z.maxLength(2000))),
    team: z.optional(team),
    template: z.optional(z.string().check(z.maxLength(100), z.describe("a template's slug"))),
    computer: z.optional(z.string().check(z.maxLength(100), z.describe("a computer's id"))),
  }),
  run: async ({ router }, input) => {
    const made = await said("Couldn't add the agent", () => addAgent(input));
    await router.navigate({ to: page(made.slug) });
    return made;
  },
  did: ({ name }) => `Added ${name}`,
  target: (_input, out) => (out ? onPage(out.slug) : null),
});

/* ---------------------------------------------------------------- cloud agents */

const cloudAgent = z.string().check(z.minLength(1), z.maxLength(40), z.describe("a cloud agent's id, as the cloud agents list names it (claude, codex, devin…)"));
const onCloud = (agent: string) => ({ kind: 'cloud', id: agent });
type Cache = Ctx['queryClient'];

/** Where an agent from the list lands: in the team, under its lead; with no team, under the person who adds it. */
function placeIn(qc: Cache, team: string | undefined) {
  const group = team ? qc.getQueryData(groupsQuery.queryKey)?.find((g) => g.id === team) : undefined;
  const lead = group ? managerOf(group, qc.getQueryData(agentsQuery.queryKey) ?? [], qc.getQueryData(peopleRosterQuery.queryKey)?.people ?? []) : null;
  return { team: team ?? '', reports_to: lead ?? '' };
}
const agentName = (qc: Cache, id: string) => qc.getQueryData(cloudAgentsQuery.queryKey)?.find((a) => a.id === id)?.name ?? id;

export const addCloud = defineAction({
  id: 'agents.addCloud',
  title: 'Add a cloud agent Tico connects (Claude Code, Codex) to a team',
  kind: 'change',
  input: z.strictObject({ agent: cloudAgent, team: z.optional(team) }),
  can: ({ queryClient }, { agent }) => queryClient.getQueryData(cloudAgentsQuery.queryKey)?.find((a) => a.id === agent)?.supported !== false,
  run: async ({ router, queryClient }, { agent, team }) => {
    const made = await said("Couldn't add the agent", () => addCloudAgent(agent, placeIn(queryClient, team)));
    await router.navigate({ to: page(made.slug) });
    return { ...made, name: agentName(queryClient, agent) };
  },
  did: ({ agent }, out) => `Added ${out?.name ?? agent}`,
  target: (_input, out) => (out ? onPage(out.slug) : null),
  offer: ({ queryClient }) =>
    (queryClient.getQueryData(cloudAgentsQuery.queryKey) ?? []).filter((a) => a.supported).map((a) => ({ input: { agent: a.id }, title: `Add ${a.name}` })),
});

export const requestCloud = defineAction({
  id: 'agents.requestCloud',
  title:
    "Request a cloud agent Tico does not connect yet (Devin, Jules…, or `other` with its name or website): a seat holds its place, its first task waits there, and Tico support builds it",
  kind: 'change',
  input: z.strictObject({
    agent: cloudAgent,
    team: z.optional(team),
    first_task: z.optional(z.string().check(z.maxLength(8000), z.describe('the first task, waiting on the seat until the agent connects'))),
    name: z.optional(z.string().check(z.maxLength(120), z.describe("for `other`: the agent's name or website"))),
  }),
  can: ({ queryClient }, { agent, name }) =>
    agent === 'other' ? !!name?.trim() : queryClient.getQueryData(cloudAgentsQuery.queryKey)?.find((a) => a.id === agent)?.supported !== true,
  run: async ({ router, queryClient }, { agent, team, first_task, name }) => {
    const body = { ...placeIn(queryClient, team), first_task: first_task?.trim() ?? '', ...(agent === 'other' ? { name: name?.trim() ?? '' } : {}) };
    const seat = await said("Couldn't send the request", () => requestCloudAgent(agent, body));
    await router.navigate({ to: seatHref(seat.id) });
    return seat;
  },
  did: ({ agent, name }, out) => `Requested ${out?.agent_name || name || agent}`,
  target: (_input, out) => (out ? { kind: 'page', id: seatHref(out.id) } : null),
});

export const cancelCloud = defineAction({
  id: 'agents.cancelCloudRequest',
  title: "Cancel a cloud agent request: Tico support hears of it and its seat closes",
  kind: 'change',
  input: z.strictObject({ seat: z.string().check(z.minLength(1), z.maxLength(200), z.describe("the request's seat id")) }),
  can: ({ queryClient }, { seat }) => {
    const seats = queryClient.getQueryData(seatsQuery.queryKey);
    return !seats || seats.some((x) => x.id === seat && isOpenRequest(x));
  },
  run: async ({ router, queryClient }, { seat }) => {
    const known = queryClient.getQueryData(seatsQuery.queryKey)?.find((x) => x.id === seat) ?? queryClient.getQueryData(seatAgentQuery(seat).queryKey)?.seat;
    const out = await said("Couldn't cancel the request", async () => ({ ...(await cancelCloudRequest(seat)), agent: known?.agent ?? '' }));
    // On the seat's own page, which is gone now: back home, its team shown in the menu.
    const group = queryClient.getQueryData(groupsQuery.queryKey)?.find((g) => g.id === known?.team);
    if (decodeURIComponent(router.state.location.pathname) === decodeURIComponent(seatHref(seat))) {
      if (group) await router.navigate({ to: '/', search: { team: group.id } as never });
      else router.history.back();
    }
    return out;
  },
  did: () => 'Canceled the request',
  target: (_input, out) => (out?.agent ? onCloud(out.agent) : null),
});

/* ---------------------------------------------------------------- its own lists */

const listName = z.string().check(z.minLength(1), z.maxLength(40));
const listId = z.string().check(z.regex(/^c\d{1,3}$/), z.describe("a list's key (c0, c1…) as its page names it"));
// A row's value in words: "open" or "open,done" (Status), a number of days, "overdue" or days (Due), "this_bot", "me",
// "others" or an actor id (Assigned to, Asked by), "any", "person", "bot", "me" or "not_me" (Waiting on), "you" (Needs).
const listRows = z.array(z.strictObject({ field: z.enum(LIST_FIELDS), value: z.string().check(z.minLength(1), z.maxLength(200)) })).check(z.maxLength(20));
const onList = (slug: string, list: string) => ({ kind: 'list', id: `${slug}:${list}` });
const own = (tabs: TaskTab[], list: string) => {
  const i = Number(list.slice(1));
  if (!tabs[i] || tabs[i].builtin) throw new Error(`No list ${list}`);
  return i;
};
const mayEdit = (qc: { getQueryData: (k: readonly unknown[]) => unknown }, slug: string) =>
  (qc.getQueryData(taskTabsQuery(slug).queryKey) as { can_edit?: boolean } | undefined)?.can_edit !== false;

export const addList = defineAction({
  id: 'agents.addList',
  title: "Add a list to an agent's page: tasks matching a saved filter",
  kind: 'change',
  input: z.strictObject({ slug, name: listName, conditions: listRows }),
  can: ({ queryClient }, { slug }) => mayEdit(queryClient, slug),
  run: async (_ctx, { slug, name, conditions }) => {
    let key = '';
    await said("Couldn't add the list", () =>
      saveTabs(slug, (tabs) => {
        key = `c${tabs.length}`;
        return [...tabs, { name, conditions: conditions.map((c) => rowOf(c as ListLine)) }];
      }),
    );
    return { list: key };
  },
  did: ({ name }) => `Added ${name}`,
  target: ({ slug }, out) => (out ? onList(slug, out.list) : onPage(slug)),
});

export const changeList = defineAction({
  id: 'agents.changeList',
  title: "Rename one of an agent's lists, or change its filter",
  kind: 'change',
  input: z.strictObject({ slug, list: listId, name: z.optional(listName), conditions: z.optional(listRows) }),
  can: ({ queryClient }, { slug }) => mayEdit(queryClient, slug),
  run: (_ctx, { slug, list, name, conditions }) =>
    said("Couldn't change the list", () =>
      saveTabs(slug, (tabs) => {
        const i = own(tabs, list);
        return tabs.map((t, j) =>
          j === i ? { ...t, ...(name ? { name } : {}), ...(conditions ? { conditions: conditions.map((c) => rowOf(c as ListLine)) } : {}) } : t,
        );
      }),
    ),
  did: ({ name }) => (name ? `Changed ${name}` : 'Changed the list'),
  target: ({ slug, list }) => onList(slug, list),
});

export const removeList = defineAction({
  id: 'agents.removeList',
  title: "Remove one of an agent's lists (its tasks stay)",
  kind: 'change',
  input: z.strictObject({ slug, list: listId }),
  can: ({ queryClient }, { slug }) => mayEdit(queryClient, slug),
  run: (_ctx, { slug, list }) =>
    said("Couldn't remove the list", () =>
      saveTabs(slug, (tabs) => {
        const i = own(tabs, list);
        return tabs.filter((_t, j) => j !== i);
      }),
    ),
  did: () => 'Removed the list',
  target: ({ slug }) => onPage(slug),
});

/* ---------------------------------------------------------------- its last run */

export const reviewStopped = defineAction({
  id: 'agents.reviewStopped',
  title: "Open an agent's stopped run, to try it again or let it go",
  kind: 'view',
  input: z.strictObject({ slug }),
  run: ({ router }, { slug }) => router.navigate({ to: '/settings/$section', params: { section: 'agents' }, search: { open: slug } }),
  show: ({ router }, { slug }) => router.navigate({ to: '/settings/$section', params: { section: 'agents' }, search: { open: slug } }),
  did: () => 'Opened the stopped run',
  target: () => ({ kind: 'page', id: '/settings/agents' }),
});

/* ---------------------------------------------------------------- teams and people */

export const move = defineAction({
  id: 'team.move',
  title: 'Move a teammate to a team',
  kind: 'change',
  input: z.strictObject({ kind: z.enum(['agent', 'person'] as const), id: z.string().check(z.minLength(1), z.maxLength(100), z.describe("an agent's slug or a person's id")), team }),
  run: (_ctx, { kind, id, team }) => said("Couldn't move them", () => moveToTeam(kind, id, team)),
  did: () => 'Moved them',
  target: ({ id }) => onPage(id),
});

export const invite = defineAction({
  id: 'people.invite',
  title: 'Invite a person',
  kind: 'change',
  input: z.strictObject({
    email: z.string().check(z.minLength(3), z.maxLength(200), z.regex(/^[^@\s]+@[^@\s]+$/), z.describe('an email address')),
    name: z.optional(z.string().check(z.maxLength(100))),
    team: z.optional(team),
    role: z.optional(z.enum(['member', 'admin'] as const)),
  }),
  // An admin's rights are the person's own to give; inviting a member stays Tico's.
  can: (ctx, { role }) => !(role === 'admin' && ctx.via === 'tico'),
  run: ({ router }, input) =>
    said("Couldn't send the invite", async () => {
      const out = await invitePerson(input);
      await router.navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, add: undefined, team: undefined }) });
      return out;
    }),
  did: ({ email }) => `Invited ${email}`,
  target: (_input, out) => (out ? onPage(out.person) : null),
});

export const setPermissions = defineAction({
  id: 'people.setRole',
  title: "Change a person's permissions",
  kind: 'change',
  input: z.strictObject({ person, role: z.enum(['member', 'admin'] as const) }),
  // Making someone an admin is the person's own press; back to member stays Tico's.
  can: (ctx, { role }) => !(role === 'admin' && ctx.via === 'tico'),
  run: (_ctx, { person, role }) => said("Couldn't change it", () => setPersonRole(person, role)),
  did: ({ role }) => (role === 'admin' ? 'Made them an admin' : 'Made them a member'),
  target: ({ person }) => onPage(person),
});

export const renameOne = defineAction({
  id: 'people.rename',
  title: 'Rename a person',
  kind: 'change',
  input: z.strictObject({ person, name: z.string().check(z.minLength(1), z.maxLength(120)) }),
  run: (_ctx, { person, name }) => said("Couldn't rename them", () => renamePerson(person, name)),
  did: ({ name }) => `Renamed them ${name}`,
  target: ({ person }) => onPage(person),
});

export const setTitle = defineAction({
  id: 'people.setTitle',
  title: "Change a person's role",
  kind: 'change',
  input: z.strictObject({ person, title: z.string().check(z.maxLength(200)) }),
  run: (_ctx, { person, title }) => said("Couldn't change the role", () => setPersonTitle(person, title)),
  did: () => 'Changed the role',
  target: ({ person }) => onPage(person),
});

/** A new boss manages them from then on: only a person gives that. */
export const setPersonReportsTo = defineAction({
  id: 'people.setReportsTo',
  title: 'Change who a person reports to',
  kind: 'change',
  tico: false,
  input: z.strictObject({ person, boss: person }),
  run: (_ctx, { person, boss }) => said("Couldn't change it", () => setPersonFields(person, { reports_to: boss })),
  did: () => 'Changed who they report to',
  target: ({ person }) => onPage(person),
});

export const setGithub = defineAction({
  id: 'people.setGithub',
  title: "Change a person's GitHub login",
  kind: 'change',
  input: z.strictObject({ person, login: z.string().check(z.maxLength(100)) }),
  run: (_ctx, { person, login }) => said("Couldn't change it", () => setPersonFields(person, { github: login })),
  did: () => 'Changed the GitHub login',
  target: ({ person }) => onPage(person),
});

export const giveATask = defineAction({
  id: 'people.giveTask',
  title: 'Give a person a task',
  kind: 'change',
  input: z.strictObject({ person, title: z.string().check(z.minLength(1), z.maxLength(300)), message: z.optional(z.string().check(z.maxLength(20000))) }),
  run: (_ctx, { person, title, message }) => said("Couldn't make the task", () => giveTask(`human:${person}`, title, message || undefined)),
  did: ({ title }) => `Gave them "${title}"`,
  target: ({ person }) => onPage(person),
});

const audience = z.strictObject({
  everyone: z.boolean(),
  people: z.array(z.string().check(z.minLength(1), z.maxLength(200))).check(z.maxLength(500)),
  teams: z.array(z.string().check(z.minLength(1), z.maxLength(200))).check(z.maxLength(200)),
  bots: z.array(z.string().check(z.minLength(1), z.maxLength(200))).check(z.maxLength(500)),
});

/** Who may see, read and write to a person or a team: a permission change, so only a person makes it. */
export const setAccess = defineAction({
  id: 'team.setAccess',
  title: 'Change who can see, read and write to a person or a team',
  kind: 'change',
  tico: false,
  input: z.strictObject({
    kind: z.enum(['person', 'team'] as const),
    id: z.string().check(z.minLength(1), z.maxLength(100), z.describe("a person's id or a team's id")),
    see: audience,
    read: audience,
    write: audience,
  }),
  run: (_ctx, { kind, id, ...levels }) => said("Couldn't change who has access", () => setTeammateAccess(kind, id, levels)),
  did: () => 'Changed who has access',
  target: ({ kind, id }) => (kind === 'person' ? onPage(id) : { kind: 'team', id }),
});

/** A password answered once, kept nowhere: a sign-in secret, so only a person makes it. */
export const password = defineAction({
  id: 'people.makePassword',
  title: 'Make a Tico account with a password for a person',
  kind: 'change',
  tico: false,
  input: z.strictObject({ person }),
  run: (_ctx, { person }) => said("Couldn't make the account", () => makePassword(person)),
  did: () => 'Made an account',
  target: ({ person }) => onPage(person),
});

/** Their sign-in and API tokens end; their history stays. Only a person does this, and the page asks first. */
export const remove = defineAction({
  id: 'people.remove',
  title: 'Remove a person',
  kind: 'change',
  tico: false,
  input: z.strictObject({ person }),
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
  input: z.strictObject({ id: z.string().check(z.minLength(1), z.maxLength(100), z.describe("a computer's id")) }),
  // The page asks first; Tico may restart without that question, so the did line says plainly what happened.
  run: async ({ queryClient }, { id }) => {
    const label = queryClient.getQueryData(computersQuery.queryKey)?.find((c) => c.id === id)?.label ?? 'the computer';
    await said("Couldn't restart it", () => restartComputer(id));
    return { label };
  },
  did: (_input, out) => `Restarted ${out?.label ?? 'the computer'}. Its agents stopped their turns and start again in about a minute.`,
  target: () => ({ kind: 'team', id: 'computer' }),
});

const computer = z.string().check(z.minLength(1), z.maxLength(100), z.describe("a computer's id"));
const runtime = z.string().check(z.minLength(1), z.maxLength(40), z.describe("an AI tool's runtime id, as the computer reports it"));
const login = z.string().check(z.minLength(1), z.maxLength(100), z.describe('a sign-in id'));

/** The provider's page opens in the person's own browser, signed in as them: only a person does this. */
export const signIn = defineAction({
  id: 'computers.signIn',
  title: 'Sign in to an AI tool on a computer',
  kind: 'change',
  tico: false,
  input: z.strictObject({ computer, runtime }),
  run: (_ctx, { computer, runtime }) => said("Couldn't start the sign-in", () => startLogin(computer, runtime)),
  did: () => 'Sign-in started',
  target: () => ({ kind: 'team', id: 'computer' }),
});

export const signInCode = defineAction({
  id: 'computers.signInCode',
  title: 'Send the code from the sign-in page',
  kind: 'change',
  tico: false,
  input: z.strictObject({ computer, login, code: z.string().check(z.minLength(1), z.maxLength(700), z.describe('the code')) }),
  run: (_ctx, { computer, login, code }) => said("Couldn't send the code", () => sendLoginCode(computer, login, code)),
  did: () => 'Code sent',
  target: () => ({ kind: 'team', id: 'computer' }),
});

export const stopSignIn = defineAction({
  id: 'computers.cancelSignIn',
  title: 'Stop a sign-in on a computer',
  kind: 'change',
  input: z.strictObject({ computer, login }),
  run: (_ctx, { computer, login }) => said("Couldn't stop it", () => cancelLogin(computer, login)),
  did: () => 'Stopped the sign-in',
  target: () => ({ kind: 'team', id: 'computer' }),
});

/** The page names the agents it stops first. They keep the sign-in and start again once someone signs in. */
export const signOut = defineAction({
  id: 'computers.signOut',
  title: 'Sign out of an AI tool on a computer',
  kind: 'change',
  // Only a person can sign in again, so signing out is the person's own press too.
  tico: false,
  input: z.strictObject({ computer, runtime }),
  run: (_ctx, { computer, runtime }) => said("Couldn't sign out", () => startLogout(computer, runtime)),
  did: () => 'Signing out. The agents that use it stop until someone signs in again.',
  target: () => ({ kind: 'team', id: 'computer' }),
});

/* ---------------------------------------------------------------- a computer's screen */

/* A session's ticket comes back as the action's output, which only the person's own press receives: the Screen tab
   holds it in component state and connects. The server refuses the Assistant too ("Open the screen yourself"). */

/** Keeps the refusal's code (screen_off, forbidden…): a 403 would otherwise read only as not_allowed. */
const session = (computer: string, control: boolean) =>
  openScreenSession(computer, control).catch((e: unknown) => {
    throw e instanceof ApiError && e.status === 403 ? Object.assign(new Error(e.message), { code: e.code }) : e;
  });

export const watchScreen = defineAction({
  id: 'computers.watchScreen',
  title: "Watch a computer's screen",
  kind: 'view',
  tico: false,
  input: z.strictObject({ computer }),
  run: (_ctx, { computer }) => session(computer, false),
  did: () => 'Watching the screen',
  target: ({ computer }) => ({ kind: 'screen', id: computer }),
});

export const controlScreen = defineAction({
  id: 'computers.controlScreen',
  title: "Take control of a computer's screen",
  kind: 'change',
  tico: false,
  input: z.strictObject({ computer }),
  run: (_ctx, { computer }) => session(computer, true),
  did: () => 'You have control',
  target: ({ computer }) => ({ kind: 'screen', id: computer }),
});

/** Who may open the screen from the app. Its operator or an admin. */
export const setScreen = defineAction({
  id: 'computers.setScreen',
  title: "Turn a computer's screen off, view only or on",
  kind: 'change',
  input: z.strictObject({ computer, mode: z.enum(['off', 'view', 'control'] as const) }),
  // Off and view only only take access away, so Tico may; turning control on gives it, so that is the person's own press.
  can: (ctx, { mode }) => !(mode === 'control' && ctx.via === 'tico'),
  run: (_ctx, { computer, mode }) => said("Couldn't change it", () => setScreenMode(computer, mode)),
  did: ({ mode }) => (mode === 'off' ? 'Turned the screen off' : mode === 'view' ? 'The screen is view only' : 'Turned the screen on'),
  target: ({ computer }) => ({ kind: 'screen', id: computer }),
});

/** Needs the press itself (a browser grants full screen only to one). */
export const fullScreen = defineAction({
  id: 'computers.fullScreen',
  title: "Show a computer's screen full screen",
  kind: 'view',
  tico: false,
  input: z.strictObject({ computer }),
  run: (_ctx, { computer }) => document.querySelector(`[data-target="screen:${CSS.escape(computer)}"]`)?.requestFullscreen(),
  did: () => 'Full screen',
  target: ({ computer }) => ({ kind: 'screen', id: computer }),
});

/* ---------------------------------------------------------------- an account's browser (Open browser) */

const tool = z.string().check(z.minLength(1), z.maxLength(100), z.describe("a website tool's id"));
const account = z.string().check(z.minLength(1), z.maxLength(100), z.describe("one of its accounts' ids"));
const onBrowser = (account: string) => ({ kind: 'browser', id: account });

/** Opens, or wakes, the account's browser so a person can sign in or finish a step by hand. */
export const browserOpen = defineAction({
  id: 'browser.open',
  title: "Open a website account's browser",
  kind: 'change',
  tico: false,
  input: z.strictObject({ tool, account }),
  run: (_ctx, { tool, account }) => said("The browser didn't open", () => openBrowser(tool, account)),
  did: () => 'Opened the browser',
  target: ({ account }) => onBrowser(account),
});

export const browserTake = defineAction({
  id: 'browser.takeControl',
  title: "Take control of a website account's browser",
  kind: 'change',
  tico: false,
  input: z.strictObject({ tool, account }),
  run: (_ctx, { tool, account }) => said("Couldn't take control", () => setBrowserControl(tool, account, true)),
  did: () => 'You have control',
  target: ({ account }) => onBrowser(account),
});

export const browserGiveBack = defineAction({
  id: 'browser.giveBack',
  title: "Give back control of a website account's browser",
  kind: 'change',
  tico: false,
  input: z.strictObject({ tool, account }),
  run: (_ctx, { tool, account }) => said("Couldn't give it back", () => setBrowserControl(tool, account, false)),
  did: () => 'Gave back control',
  target: ({ account }) => onBrowser(account),
});

/** A click in page pixels, typed text, a key or a scroll. Only while the person holds control. */
export const browserInput = defineAction({
  id: 'browser.input',
  title: "Click, type or scroll in a website account's browser",
  kind: 'change',
  tico: false,
  input: z.strictObject({
    tool,
    account,
    kind: z.enum(['click', 'type', 'key', 'scroll'] as const),
    x: z.optional(z.int().check(z.minimum(0))),
    y: z.optional(z.int().check(z.minimum(0))),
    text: z.optional(z.string().check(z.minLength(1), z.maxLength(2000))),
    key: z.optional(z.enum(['Enter', 'Tab', 'Backspace'] as const)),
    dy: z.optional(z.int()),
  }),
  run: (_ctx, { tool, account, ...input }) => said("It didn't reach the browser", () => sendBrowserInput(tool, account, input)),
  did: () => 'Sent',
  target: ({ account }) => onBrowser(account),
});

/* ---------------------------------------------------------------- its rules and instructions */

const rosterRow = (qc: { getQueryData: (k: readonly unknown[]) => unknown }, slug: string) =>
  (qc.getQueryData(agentsQuery.queryKey) as { id: string; slug: string; display_name?: string; can_manage?: boolean }[] | undefined)?.find(
    (r) => r.slug === slug,
  );

/** A new rule for one agent: the Rules page's editor, with that agent's rules showing (the rule editor is Rules'). */
export const newAgentRule = defineAction({
  id: 'agents.newRule',
  title: 'Start a new rule for an agent',
  kind: 'view',
  input: z.strictObject({ slug }),
  run: ({ router, queryClient }, { slug }) => {
    const actor = queryClient.getQueryData(directoryQuery.queryKey)?.actors.find((a) => a.kind === 'agent' && a.slug === slug);
    const id = actor?.id ?? (rosterRow(queryClient, slug) ? `bot:${rosterRow(queryClient, slug)!.id}` : undefined);
    return router.navigate({ to: '/company/rules', search: { ...(id ? { who: id } : {}), panel: 'org:new' } });
  },
  did: () => 'Opened a new rule',
  target: () => ({ kind: 'panel', id: 'rule:new' }),
});

const tooLong = () => {
  toast({ text: 'Too long to send at once', kind: 'failed' });
  return new Error('too long');
};

/** No route writes AGENTS.md: the change goes to Tico as a message, and Tico makes it on the agent's computer. */
export const editInstructions = defineAction({
  id: 'agents.editInstructions',
  title: "Change an agent's instructions",
  kind: 'change',
  // A message to Tico itself: Tico changes the file on the computer directly instead.
  tico: false,
  input: z.strictObject({ slug, text: z.string().check(z.maxLength(100_000), z.describe('the whole new instructions')) }),
  can: ({ queryClient }, { slug }) => rosterRow(queryClient, slug)?.can_manage !== false,
  run: async ({ queryClient }, { slug, text }) => {
    const before = (await queryClient.ensureQueryData(instructionsQuery(slug))).content ?? '';
    const change = instructionsChange(before, text);
    if (!change) return { sent: false };
    const name = rosterRow(queryClient, slug)?.display_name ?? slug;
    const msg = `Update the Instructions (AGENTS.md) for ${name} [${slug}]. Make this change and verify the file on its computer:\n\n${change}`;
    if (msg.length > TICO_TEXT_MAX) throw tooLong();
    await said("Couldn't send the change", () => tellTico(msg));
    return { sent: true };
  },
  did: (_input, out) => (out?.sent === false ? 'Nothing changed' : 'Sent the change to Tico. It updates the file on the computer.'),
  target: ({ slug }) => onPage(slug),
});

/** For someone who may not manage the agent: Tico takes the request to whoever does. */
export const requestInstructions = defineAction({
  id: 'agents.requestInstructions',
  title: "Ask for a change to an agent's instructions",
  kind: 'change',
  // A message to Tico itself: Tico asks the agent's manager directly instead.
  tico: false,
  input: z.strictObject({ slug, changes: z.string().check(z.minLength(1), z.maxLength(6000), z.describe('what should change')) }),
  run: async ({ queryClient }, { slug, changes }) => {
    const name = rosterRow(queryClient, slug)?.display_name ?? slug;
    const msg = `Ask whoever manages ${name} [${slug}] to change its Instructions (AGENTS.md). What should change:\n\n${changes.trim()}`;
    if (msg.length > TICO_TEXT_MAX) throw tooLong();
    await said("Couldn't send the request", () => tellTico(msg));
  },
  did: () => 'Sent your request to Tico',
  target: ({ slug }) => onPage(slug),
});
