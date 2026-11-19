import { defineAction, s } from '@/actions';
import { ApiError } from '@/api/client';
import { directoryQuery } from '@/data';
import { toast } from '@/kit/Toast';
import {
  addAgent,
  archiveAgent,
  cancelLogin,
  changeAgent,
  computersQuery,
  controlAgent,
  giveTask,
  invitePerson,
  makeCopy,
  makePassword,
  moveAgent,
  moveToTeam,
  newCredential,
  openBrowser,
  openScreenSession,
  removePerson,
  renamePerson,
  restartComputer,
  sendBrowserInput,
  sendLoginCode,
  setBrowserControl,
  setAgentModel,
  setAgentStatus,
  setProfile,
  setScreenMode,
  setPersonRole,
  setPersonTitle,
  setTeammateAccess,
  setWorksFor,
  startLogin,
  startLogout,
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

/** My copy of an agent that allows copies: on my computer, or planned until I add one. Opens it. */
export const copy = defineAction({
  id: 'agents.makeCopy',
  title: 'Make my copy of an agent',
  kind: 'change',
  input: s.object({ slug, computer: s.optional(s.string({ min: 1, max: 100, what: "one of my computers' ids" })) }),
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
  input: s.object({ slug, status: s.oneOf(['planned', 'active', 'paused'] as const) }),
  run: (_ctx, { slug, status }) => said("Couldn't change it", () => setAgentStatus(slug, status)),
  did: () => 'Changed its status',
  target: ({ slug }) => onPage(slug),
});

/** Leaving Custom deletes the wake rules it copied: refused unless `dropRules`, which only a person sends. */
export const profile = defineAction({
  id: 'agents.setProfile',
  title: 'Change how an agent takes work (its profile)',
  kind: 'change',
  input: s.object({
    slug,
    profile: s.oneOf(['doer', 'reviewer', 'lead', 'manager', 'responder', 'monitor', 'custom'] as const),
    dropRules: s.optional(s.boolean()),
  }),
  can: (ctx, { dropRules }) => !(dropRules && ctx.via === 'tico'),
  run: async (_ctx, { slug, profile, dropRules }) => {
    try {
      return await setProfile(slug, profile, dropRules);
    } catch (e) {
      // Leaving Custom: the page asks, then sends it again with dropRules.
      if (!(e instanceof ApiError && e.code === 'conflict')) toast({ text: reason(e, "Couldn't change it"), kind: 'failed' });
      throw e;
    }
  },
  did: () => 'Changed its profile',
  target: ({ slug }) => onPage(slug),
});

export const moveComputer = defineAction({
  id: 'agents.move',
  title: 'Move an agent to another computer',
  kind: 'change',
  input: s.object({ slug, computer: s.string({ min: 1, max: 100, what: "a computer's id" }) }),
  run: async ({ queryClient }, { slug, computer }) => {
    const label = queryClient.getQueryData(computersQuery.queryKey)?.find((c) => c.id === computer)?.label ?? 'the computer';
    await said("Couldn't move it", () => moveAgent(slug, computer));
    return { label };
  },
  did: (_input, out) => `Moved it to ${out?.label ?? 'the computer'}. It starts there after its turn ends.`,
  target: ({ slug }) => onPage(slug),
});

export const worksFor = defineAction({
  id: 'agents.setWorksFor',
  title: 'Change who an agent works for',
  kind: 'change',
  input: s.object({ slug, people: s.array(person, { max: 100 }) }),
  run: (_ctx, { slug, people }) => said("Couldn't change it", () => setWorksFor(slug, people)),
  did: () => 'Changed who it works for',
  target: ({ slug }) => onPage(slug),
});

/** Shown once, and the one before stops at once: only a person makes it. */
export const credential = defineAction({
  id: 'agents.newCredential',
  title: 'Make a new credential for an agent that runs elsewhere',
  kind: 'change',
  tico: false,
  input: s.object({ slug }),
  run: (_ctx, { slug }) => said("Couldn't make a credential", () => newCredential(slug)),
  did: () => 'Made a new credential',
  target: ({ slug }) => onPage(slug),
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

export const giveATask = defineAction({
  id: 'people.giveTask',
  title: 'Give a person a task',
  kind: 'change',
  input: s.object({ person, title: s.string({ min: 1, max: 300 }), message: s.optional(s.string({ max: 20000 })) }),
  run: (_ctx, { person, title, message }) => said("Couldn't make the task", () => giveTask(`human:${person}`, title, message || undefined)),
  did: ({ title }) => `Gave them "${title}"`,
  target: ({ person }) => onPage(person),
});

const audience = s.object({
  everyone: s.boolean(),
  people: s.array(s.string({ min: 1, max: 200 }), { max: 500 }),
  teams: s.array(s.string({ min: 1, max: 200 }), { max: 200 }),
  bots: s.array(s.string({ min: 1, max: 200 }), { max: 500 }),
});

/** Who may see, read and write to a person or a team: a permission change, so only a person makes it. */
export const setAccess = defineAction({
  id: 'team.setAccess',
  title: 'Change who can see, read and write to a person or a team',
  kind: 'change',
  tico: false,
  input: s.object({
    kind: s.oneOf(['person', 'team'] as const),
    id: s.string({ min: 1, max: 100, what: "a person's id or a team's id" }),
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
  input: s.object({ person }),
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

const computer = s.string({ min: 1, max: 100, what: "a computer's id" });
const runtime = s.string({ min: 1, max: 40, what: "an AI tool's runtime id, as the computer reports it" });
const login = s.string({ min: 1, max: 100, what: 'a sign-in id' });

/** The provider's page opens in the person's own browser, signed in as them: only a person does this. */
export const signIn = defineAction({
  id: 'computers.signIn',
  title: 'Sign in to an AI tool on a computer',
  kind: 'change',
  tico: false,
  input: s.object({ computer, runtime }),
  run: (_ctx, { computer, runtime }) => said("Couldn't start the sign-in", () => startLogin(computer, runtime)),
  did: () => 'Sign-in started',
  target: () => ({ kind: 'team', id: 'computer' }),
});

export const signInCode = defineAction({
  id: 'computers.signInCode',
  title: 'Send the code from the sign-in page',
  kind: 'change',
  tico: false,
  input: s.object({ computer, login, code: s.string({ min: 1, max: 700, what: 'the code' }) }),
  run: (_ctx, { computer, login, code }) => said("Couldn't send the code", () => sendLoginCode(computer, login, code)),
  did: () => 'Code sent',
  target: () => ({ kind: 'team', id: 'computer' }),
});

export const stopSignIn = defineAction({
  id: 'computers.cancelSignIn',
  title: 'Stop a sign-in on a computer',
  kind: 'change',
  input: s.object({ computer, login }),
  run: (_ctx, { computer, login }) => said("Couldn't stop it", () => cancelLogin(computer, login)),
  did: () => 'Stopped the sign-in',
  target: () => ({ kind: 'team', id: 'computer' }),
});

/** The page names the agents it stops first. They keep the sign-in and start again once someone signs in. */
export const signOut = defineAction({
  id: 'computers.signOut',
  title: 'Sign out of an AI tool on a computer',
  kind: 'change',
  input: s.object({ computer, runtime }),
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
  input: s.object({ computer }),
  run: (_ctx, { computer }) => session(computer, false),
  did: () => 'Watching the screen',
  target: ({ computer }) => ({ kind: 'screen', id: computer }),
});

export const controlScreen = defineAction({
  id: 'computers.controlScreen',
  title: "Take control of a computer's screen",
  kind: 'change',
  tico: false,
  input: s.object({ computer }),
  run: (_ctx, { computer }) => session(computer, true),
  did: () => 'You have control',
  target: ({ computer }) => ({ kind: 'screen', id: computer }),
});

/** Who may open the screen from the app. Its operator or an admin, in person. */
export const setScreen = defineAction({
  id: 'computers.setScreen',
  title: "Turn a computer's screen off, view only or on",
  kind: 'change',
  tico: false,
  input: s.object({ computer, mode: s.oneOf(['off', 'view', 'control'] as const) }),
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
  input: s.object({ computer }),
  run: (_ctx, { computer }) => document.querySelector(`[data-target="screen:${CSS.escape(computer)}"]`)?.requestFullscreen(),
  did: () => 'Full screen',
  target: ({ computer }) => ({ kind: 'screen', id: computer }),
});

/* ---------------------------------------------------------------- an account's browser (Open browser) */

const tool = s.string({ min: 1, max: 100, what: "a website tool's id" });
const account = s.string({ min: 1, max: 100, what: "one of its accounts' ids" });
const onBrowser = (account: string) => ({ kind: 'browser', id: account });

/** Opens, or wakes, the account's browser so a person can sign in or finish a step by hand. */
export const browserOpen = defineAction({
  id: 'browser.open',
  title: "Open a website account's browser",
  kind: 'change',
  tico: false,
  input: s.object({ tool, account }),
  run: (_ctx, { tool, account }) => said("The browser didn't open", () => openBrowser(tool, account)),
  did: () => 'Opened the browser',
  target: ({ account }) => onBrowser(account),
});

export const browserTake = defineAction({
  id: 'browser.takeControl',
  title: "Take control of a website account's browser",
  kind: 'change',
  tico: false,
  input: s.object({ tool, account }),
  run: (_ctx, { tool, account }) => said("Couldn't take control", () => setBrowserControl(tool, account, true)),
  did: () => 'You have control',
  target: ({ account }) => onBrowser(account),
});

export const browserGiveBack = defineAction({
  id: 'browser.giveBack',
  title: "Give back control of a website account's browser",
  kind: 'change',
  tico: false,
  input: s.object({ tool, account }),
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
  input: s.object({
    tool,
    account,
    kind: s.oneOf(['click', 'type', 'key', 'scroll'] as const),
    x: s.optional(s.number({ int: true, min: 0 })),
    y: s.optional(s.number({ int: true, min: 0 })),
    text: s.optional(s.string({ min: 1, max: 2000 })),
    key: s.optional(s.oneOf(['Enter', 'Tab', 'Backspace'] as const)),
    dy: s.optional(s.number({ int: true })),
  }),
  run: (_ctx, { tool, account, ...input }) => said("It didn't reach the browser", () => sendBrowserInput(tool, account, input)),
  did: () => 'Sent',
  target: ({ account }) => onBrowser(account),
});
