import * as z from 'zod/mini';
import { defineAction } from '@/actions';
import type { Ctx } from '@/actions';
import { run } from '@/actions/run';
import { meQuery } from '@/data';
import { desktopApp, tellDesktopSigningOut } from '@/desktop';
import { agentsQuery } from '@/features/team/routing';
import { setMotionChoice } from '@/motion';
import { setThemeChoice, type Theme } from '@/prefs';
import {
  addPerson,
  addSubscription,
  cancelSignIn,
  createToken,
  sendSignInCode,
  enrollComputer,
  labelComputer,
  refreshSubscription,
  removeComputer,
  removePerson,
  removeSubscription,
  setDefaultLimit,
  setCountPlanRuns,
  setGatewayFallback,
  setLimit,
  startSignIn,
  renamePerson,
  revokeToken,
  sendInvite,
  setMyNotice,
  setPageOwner,
  setRoadmap,
  addContactField,
  addProductUpdate,
  changeOwnerEmail,
  editSubscription,
  modelsQuery,
  purgeQueue,
  reconcileRun,
  restoreAgent,
  setBotAccess,
  setBotMail,
  setCoOwners,
  setDefaults,
  setSleep,
  startServer,
  transferOwner,
  contactTypeQuery,
  contactTypesQuery,
  FIELD_KINDS,
  setRole,
  setRule,
  setScreening,
  setSupport,
  setTaskModel,
  subscriptionsQuery,
  tokensQuery,
  newScimToken,
  previewDirectory,
  saveDirectory,
  syncDirectory,
  saveGateway,
  withDestination,
  limitsQuery,
  poolsOf,
  setPoolLimit,
  setTaskCap,
  taskCapsQuery,
} from './queries';
import {
  accessQuery,
  createRepo,
  forgetPerson,
  previewRepo,
  refreshRepos,
  restorePerson,
  revokeAnyToken,
  setAgentLimit,
  setAllowed,
  setNewAgentRepos,
  setRepo,
  setTeamIcon,
  startSupportAccess,
  stopSupportAccess,
  type AccessFull,
  type ProductRepoPreview,
} from './queries';
import {
  askQuery,
  healthQuery,
  markChangelogRead,
  resetAsk,
  roadmapQuery,
  saveAsk,
} from './queries';
import { renameCompany } from './queries';
import { askDestination } from './Destination';
import { SECTION_IDS } from './sections';

/* Every change on Settings and Usage. Appearance is this device's own (prefs); the rest goes to the server. Anything
   that sets or shows a secret, deletes the company or changes billing is the person's own press (tico: false). */

const id = (what: string) => z.string().check(z.minLength(1), z.maxLength(200), z.describe(what));
const setting = (key: string) => ({ kind: 'setting', id: key });
const bare = (actor: string) => actor.replace(/^human:/, '');

const MAX_SUBSCRIPTION_NAME = 80;
// The API and JSON Schema count code points. Zod's maxLength counts UTF-16 units.
const subscriptionName = z.string().check(
  z.trim(),
  z.refine((name) => [...name].length <= MAX_SUBSCRIPTION_NAME, { message: `must be at most ${MAX_SUBSCRIPTION_NAME} characters` }),
).register(z.globalRegistry, { maxLength: MAX_SUBSCRIPTION_NAME });
export const editSubscriptionName = subscriptionName.check(z.minLength(1));

export const openSection = defineAction({
  id: 'settings.open',
  title: 'Open a settings section',
  kind: 'view',
  input: z.strictObject({ section: z.enum(SECTION_IDS) }),
  run: ({ router }, { section }) => router.navigate({ to: '/settings/$section', params: { section } }),
  show: ({ router }, { section }) => router.navigate({ to: '/settings/$section', params: { section } }),
  did: ({ section }) => `Opened ${section}`,
  target: ({ section }) => ({ kind: 'page', id: `/settings/${section}` }),
});

// ---------------------------------------------------------------- appearance (this device)

export const setTheme = defineAction({
  id: 'settings.setTheme',
  title: 'Change the theme',
  kind: 'change',
  input: z.strictObject({ theme: z.enum(['system', 'light', 'dark'] as const) }),
  run: (_ctx, { theme }: { theme: Theme }) => setThemeChoice(theme),
  did: ({ theme }) => `Theme set to ${theme}`,
  target: () => setting('theme'),
});

export const setMotion = defineAction({
  id: 'settings.setMotion',
  title: 'Change the motion level',
  kind: 'change',
  input: z.strictObject({ motion: z.enum(['system', 'more', 'less', 'off'] as const) }),
  run: (_ctx, { motion }) => setMotionChoice(motion),
  did: ({ motion }) => `Motion set to ${motion}`,
  target: () => setting('motion'),
});

// ---------------------------------------------------------------- you

const myId = (ctx: { queryClient: { getQueryData: (k: readonly unknown[]) => unknown } }) => {
  const me = ctx.queryClient.getQueryData(meQuery.queryKey) as { id: string } | undefined;
  if (!me) throw new Error('not signed in');
  return bare(me.id);
};

export const setNotice = defineAction({
  id: 'settings.notify',
  title: 'Change a notification',
  kind: 'change',
  input: z.strictObject({ field: z.enum(['notify_browser', 'email_digest'] as const), on: z.boolean() }),
  run: (ctx, { field, on }) => setMyNotice(myId(ctx), field, on),
  did: ({ on }) => (on ? 'Turned on' : 'Turned off'),
  target: ({ field }) => setting(field),
});

export const signOut = defineAction({
  id: 'settings.signOut',
  title: 'Sign out',
  kind: 'change',
  input: z.strictObject({}),
  tico: false,
  // the desktop app hears first, so it drops the person's count and small call window before the page leaves
  run: async () => {
    await tellDesktopSigningOut();
    location.assign('/api/v2/logout');
  },
});

export const makeToken = defineAction({
  id: 'settings.createToken',
  title: 'Make an API token',
  kind: 'change',
  // `days` until it stops working (the server's 1 to 365); left out, 90.
  input: z.strictObject({ label: z.string().check(z.minLength(1), z.maxLength(80)), days: z.optional(z.int().check(z.minimum(1), z.maximum(365))) }),
  tico: false,
  run: async (ctx, { label, days }) => {
    const made = await createToken(label, days);
    void ctx.queryClient.invalidateQueries({ queryKey: tokensQuery.queryKey });
    // Shown once, on this screen: the result carries it to the press (never to Tico, which cannot run this).
    return { id: made.id, token: made.token };
  },
  did: () => 'Token made',
  target: () => setting('tokens'),
});

export const dropToken = defineAction({
  id: 'settings.revokeToken',
  title: 'Revoke an API token',
  kind: 'change',
  input: z.strictObject({ token: id('a token id') }),
  // Its scripts stop for good: the person's own press.
  tico: false,
  run: (_ctx, { token }) => revokeToken(token),
  did: () => 'Token revoked',
  target: ({ token }) => ({ kind: 'token', id: token }),
});

// ---------------------------------------------------------------- people

export const invite = defineAction({
  id: 'settings.invite',
  title: 'Add a person',
  kind: 'change',
  input: z.strictObject({ email: z.string().check(z.minLength(3), z.maxLength(320), z.regex(/^[^\s@]+@[^\s@]+$/), z.describe('an email address')), name: z.optional(z.string().check(z.maxLength(120))) }),
  // Inviting a member is Tico's too, as on Team (people.invite): setup is an agent's work.
  run: (_ctx, { email, name }) => addPerson(email, name ?? ''),
  did: ({ email }) => `Invited ${email}`,
  target: (_input, out) => (out ? { kind: 'person', id: out.person } : setting('people')),
});

export const resendInvite = defineAction({
  id: 'settings.resendInvite',
  title: 'Send an invite again',
  kind: 'change',
  input: z.strictObject({ person: id('a person id') }),
  run: (_ctx, { person }) => sendInvite(person),
  did: () => 'Invite sent',
  target: ({ person }) => ({ kind: 'person', id: person }),
});

export const changeRole = defineAction({
  id: 'settings.setRole',
  title: "Change a person's role",
  kind: 'change',
  input: z.strictObject({ person: id('a person id'), role: z.enum(['admin', 'member'] as const) }),
  // Making someone an admin widens their rights: the person's own press. Back to member stays Tico's.
  can: (ctx, { role }) => !(role === 'admin' && ctx.via === 'tico'),
  run: (_ctx, { person, role }) => setRole(person, role),
  did: ({ role }) => `Role set to ${role}`,
  target: ({ person }) => ({ kind: 'person', id: person }),
});

export const rename = defineAction({
  id: 'settings.rename',
  title: 'Rename a person',
  kind: 'change',
  input: z.strictObject({ person: id('a person id'), name: z.string().check(z.minLength(1), z.maxLength(120)) }),
  run: (_ctx, { person, name }) => renamePerson(person, name),
  did: ({ name }) => `Renamed to ${name}`,
  target: ({ person }) => ({ kind: 'person', id: person }),
});

export const remove = defineAction({
  id: 'settings.removePerson',
  title: 'Remove a person',
  kind: 'change',
  input: z.strictObject({ person: id('a person id') }),
  // Takes away someone's sign-in: the person's own press.
  tico: false,
  run: (_ctx, { person }) => removePerson(person),
  did: () => 'Removed',
  target: () => setting('people'),
});

export const makeOwner = defineAction({
  id: 'settings.makeOwner',
  title: 'Make someone the owner',
  kind: 'change',
  input: z.strictObject({ person: id('a person id'), keepAdmin: z.boolean() }),
  // Hands the whole company to someone else, and the owner's controls go with it.
  tico: false,
  run: (_ctx, { person, keepAdmin }) => transferOwner(person, keepAdmin),
  did: () => 'Owner changed',
  target: ({ person }) => ({ kind: 'person', id: person }),
});

const email = z.string().check(z.minLength(3), z.maxLength(320), z.regex(/^[^\s@]+@[^\s@]+\.[^\s@]+$/), z.describe('an email address'));

export const changeEmail = defineAction({
  id: 'settings.changeEmail',
  title: "Change the owner's sign-in email",
  kind: 'change',
  input: z.strictObject({ email, again: email }),
  // The old address stops signing in at once: a security change only the owner makes.
  tico: false,
  run: (_ctx, { email: to, again }) => changeOwnerEmail(to, again),
  did: () => 'Email changed',
  target: () => setting('people'),
});

// ---------------------------------------------------------------- directory sync (owner)

const lines = z.array(z.string().check(z.minLength(1), z.maxLength(200))).check(z.maxLength(50));

export const saveSync = defineAction({
  id: 'settings.saveDirectory',
  title: 'Change where people sync from',
  kind: 'change',
  input: z.strictObject({
    source: z.enum(['', 'google', 'entra', 'scim'] as const),
    // Minutes between syncs; 0 syncs only when a person presses Sync now.
    interval: z.int().check(z.minimum(0), z.maximum(10080)),
    massLeave: z.int().check(z.minimum(0), z.maximum(10000)),
    groups: lines,
    orgUnits: lines,
    domains: lines,
    credentials: z.optional(z.strictObject({
        service_account_json: z.optional(z.string().check(z.maxLength(20000))),
        admin_email: z.optional(z.string().check(z.maxLength(320))),
        tenant: z.optional(z.string().check(z.maxLength(200))),
        client_id: z.optional(z.string().check(z.maxLength(100))),
        client_secret: z.optional(z.string().check(z.maxLength(400))),
      })),
    replaceLostKey: z.optional(z.boolean()),
  }),
  // Who may sign in, and the directory's own keys: a security change only the owner makes.
  tico: false,
  run: (_ctx, { source, interval, massLeave, groups, orgUnits, domains, credentials, replaceLostKey }) =>
    saveDirectory({
      source,
      interval_minutes: interval,
      mass_leave_limit: massLeave,
      filter: { groups, org_units: orgUnits, domains },
      ...(credentials ? { credentials } : {}),
      ...(replaceLostKey ? { replace_lost_key: true } : {}),
    }),
  did: ({ source }) => (source ? 'Directory sync saved' : 'Stopped syncing'),
  target: () => setting('directory-sync'),
});

export const scimToken = defineAction({
  id: 'settings.scimToken',
  title: 'Make a SCIM token',
  kind: 'change',
  input: z.strictObject({}),
  // Shown once, to the press that made it; the one before stops at once.
  tico: false,
  run: async () => ({ token: await newScimToken() }),
  did: () => 'Token made',
  target: () => setting('directory-sync'),
});

export const previewSync = defineAction({
  id: 'settings.previewDirectory',
  title: 'See what a directory sync would change',
  kind: 'view',
  input: z.strictObject({}),
  run: () => previewDirectory(),
  did: () => 'Previewed',
  target: () => setting('directory-sync'),
});

export const applySync = defineAction({
  id: 'settings.syncDirectory',
  title: 'Sync people from the directory',
  kind: 'change',
  input: z.strictObject({
    plan: z.string().check(z.minLength(1), z.maxLength(64), z.describe('the hash of the previewed plan')),
    confirm: z.boolean(),
  }),
  // People join, change and leave: who may sign in changes at once.
  tico: false,
  run: (_ctx, { plan, confirm }) => syncDirectory(plan, confirm),
  did: (_input, out) => (out?.applied ? 'Synced' : out?.stale ? 'The directory changed. Preview again.' : 'Held for a check'),
  target: () => setting('directory-sync'),
});

// ---------------------------------------------------------------- general

export const pageOwner = defineAction({
  id: 'settings.setPageOwner',
  title: "Change a page's owner",
  kind: 'change',
  input: z.strictObject({ page: id('a page key'), owner: id("'default', 'none' or an agent id") }),
  run: (_ctx, { page, owner }) => setPageOwner(page, owner),
  did: () => 'Owner changed',
  target: ({ page }) => ({ kind: 'page-owner', id: page }),
});

export const reportsDirect = defineAction({
  id: 'settings.reportsDirect',
  title: 'Agents report to Tico support',
  kind: 'change',
  input: z.strictObject({ on: z.boolean() }),
  // Sending reports outside the company is the person's own press; turning it off stays Tico's.
  can: (ctx, { on }) => !(on && ctx.via === 'tico'),
  run: (_ctx, { on }) => setRule('bot_reports_direct', on),
  did: ({ on }) => (on ? 'Turned on' : 'Turned off'),
  target: () => setting('reports'),
});

// ---------------------------------------------------------------- security

export const screenMode = defineAction({
  id: 'settings.setScreening',
  title: 'Change how a door is screened',
  kind: 'change',
  input: z.strictObject({ door: z.enum(['webhook', 'mail', 'idea', 'watcher', 'document', 'slack'] as const), mode: z.enum(['off', 'warn', 'hold'] as const) }),
  // Holding is the strict side and stays Tico's; anything looser is the person's own press.
  can: (ctx, { mode }) => !(mode !== 'hold' && ctx.via === 'tico'),
  run: (_ctx, { door, mode }) => setScreening({ [door]: mode }),
  did: ({ mode }) => `Screening set to ${mode}`,
  target: ({ door }) => setting(`screen-${door}`),
});

export const detector = defineAction({
  id: 'settings.setDetector',
  title: 'Turn a screening detector on or off',
  kind: 'change',
  input: z.strictObject({ detector: z.enum(['patterns', 'model'] as const), on: z.boolean() }),
  // Turning a detector on is Tico's; turning one off loosens security.
  can: (ctx, { on }) => !(!on && ctx.via === 'tico'),
  run: (_ctx, { detector, on }) => setScreening({ [detector]: on }),
  did: ({ on }) => (on ? 'Turned on' : 'Turned off'),
  target: ({ detector }) => setting(`detector-${detector}`),
});

export const support = defineAction({
  id: 'settings.setSupport',
  title: 'Let Tico support reach the computers',
  kind: 'change',
  input: z.strictObject({ on: z.boolean() }),
  // Letting Tico support in widens who can reach the computers; shutting it out is Tico's.
  can: (ctx, { on }) => !(on && ctx.via === 'tico'),
  run: (_ctx, { on }) => setSupport(on),
  did: ({ on }) => (on ? 'Turned on' : 'Turned off'),
  target: () => setting('support'),
});

// ---- agents, computers and models (anchor: the Agents builder adds its actions below) ----

const sub = (id: string) => ({ kind: 'subscription', id });

export const connect = defineAction({
  id: 'settings.connect',
  title: 'Connect Claude or Codex',
  kind: 'change',
  input: z.strictObject({ kind: z.enum(['claude_login', 'codex_login'] as const) }),
  // The sign-in opens the provider's page in the person's own browser, signed in as them.
  tico: false,
  run: async (_ctx, { kind }) => {
    return (await startSignIn(kind)) ?? null;
  },
  did: ({ kind }) => (kind === 'codex_login' ? 'Codex sign-in started' : 'Claude sign-in started'),
  target: () => setting('subscriptions'),
});

export const signInCode = defineAction({
  id: 'settings.signInCode',
  title: 'Send the code from the sign-in page',
  kind: 'change',
  input: z.strictObject({ computer: id('a computer id'), login: id('a sign-in id'), code: z.string().check(z.minLength(1), z.maxLength(2000), z.describe('the code')) }),
  tico: false,
  run: (_ctx, { computer, login, code }) => sendSignInCode(computer, login, code),
  did: () => 'Code sent',
  target: () => setting('subscriptions'),
});

export const stopSignIn = defineAction({
  id: 'settings.cancelSignIn',
  title: 'Stop a sign-in',
  kind: 'change',
  input: z.strictObject({ computer: id('a computer id'), login: id('a sign-in id') }),
  run: (_ctx, { computer, login }) => cancelSignIn(computer, login),
  did: () => 'Stopped',
  target: () => setting('subscriptions'),
});

export const addKey = defineAction({
  id: 'settings.addKey',
  title: 'Add a model key',
  kind: 'change',
  input: z.strictObject({ kind: id('a subscription kind'), secret: z.string().check(z.minLength(8), z.maxLength(20000), z.describe('the key')), name: z.optional(subscriptionName) }),
  tico: false,
  run: async (ctx, { kind, secret, name }) => {
    const added = await addSubscription(kind, secret, name);
    void ctx.queryClient.invalidateQueries({ queryKey: subscriptionsQuery.queryKey });
    return added;
  },
  did: () => 'Key added',
  target: (_input, out) => (out ? sub(out.id) : setting('subscriptions')),
});

export const removeSub = defineAction({
  id: 'settings.removeSubscription',
  title: 'Remove a subscription',
  kind: 'change',
  input: z.strictObject({ subscription: id('a subscription id') }),
  // Its runs stop and the key is gone: the person's own press (the page asks first).
  tico: false,
  run: (_ctx, { subscription }) => removeSubscription(subscription),
  did: () => 'Removed',
  target: () => setting('subscriptions'),
});

export const refreshSub = defineAction({
  id: 'settings.refreshUsage',
  title: "Check a subscription's use again",
  kind: 'change',
  input: z.strictObject({ subscription: id('a subscription id') }),
  run: (_ctx, { subscription }) => refreshSubscription(subscription),
  did: () => 'Checked',
  target: ({ subscription }) => sub(subscription),
});

const comp = (rid: string) => ({ kind: 'computer', id: rid });

export const nameComputer = defineAction({
  id: 'settings.nameComputer',
  title: 'Rename a computer',
  kind: 'change',
  input: z.strictObject({ computer: id('a computer id'), name: z.string().check(z.minLength(1), z.maxLength(80)) }),
  run: (_ctx, { computer, name }) => labelComputer(computer, name),
  did: ({ name }) => `Renamed to ${name}`,
  target: ({ computer }) => comp(computer),
});

export const dropComputer = defineAction({
  id: 'settings.removeComputer',
  title: 'Remove a computer',
  kind: 'change',
  input: z.strictObject({ computer: id('a computer id') }),
  // Its sign-in ends for good and its agents stop: the person's own press.
  tico: false,
  run: (_ctx, { computer }) => removeComputer(computer),
  did: () => 'Removed',
  target: () => setting('computers'),
});

export const fallback = defineAction({
  id: 'settings.gatewayFallback',
  title: 'Fall back to Tico Gateway',
  kind: 'change',
  input: z.strictObject({ computer: id('a computer id'), on: z.boolean() }),
  // Gateway runs are paid from the company's credit: a billing change.
  tico: false,
  run: (_ctx, { computer, on }) => setGatewayFallback(computer, on),
  did: ({ on }) => (on ? 'Fallback on' : 'Fallback off'),
  target: ({ computer }) => comp(computer),
});

export const addComputer = defineAction({
  id: 'settings.addComputer',
  title: 'Add a computer',
  kind: 'change',
  input: z.strictObject({}),
  // The one-time code lets a machine join the company: a secret, shown once.
  tico: false,
  run: async (ctx) => {
    const { code } = await enrollComputer(myId(ctx));
    return { code };
  },
  did: () => 'Code made',
  target: () => setting('computers'),
});

export const editSub = defineAction({
  id: 'settings.editSubscription',
  title: 'Rename a subscription or change who holds it',
  kind: 'change',
  input: z.strictObject({
    subscription: id('a subscription id'),
    name: editSubscriptionName,
    owner: z.optional(z.string().check(z.maxLength(200), z.describe('a person id, for a plan'))),
  }),
  // Who holds a plan's sign-in is the person's own press; a new name stays Tico's.
  can: (ctx, { owner }) => !(owner && ctx.via === 'tico'),
  run: (_ctx, { subscription, name, owner }) => editSubscription(subscription, name, owner || undefined),
  did: ({ name }) => `Saved ${name}`,
  target: ({ subscription }) => sub(subscription),
});

export const setSleepHours = defineAction({
  id: 'settings.setSleep',
  title: 'Sleep the server when idle',
  kind: 'change',
  input: z.strictObject({ hours: z.int().check(z.minimum(0), z.maximum(24)) }),
  run: (_ctx, { hours }) => setSleep({ hours }),
  did: ({ hours }) => (hours ? `Sleeps after ${hours} h` : 'Sleep off'),
  target: () => setting('sleep'),
});

export const sleepComputers = defineAction({
  id: 'settings.sleepComputers',
  title: "Let agents' computers sleep too",
  kind: 'change',
  input: z.strictObject({ on: z.boolean() }),
  run: (_ctx, { on }) => setSleep({ runners: on }),
  did: ({ on }) => (on ? 'Turned on' : 'Turned off'),
  target: () => setting('sleep'),
});

export const startHosted = defineAction({
  id: 'settings.startServer',
  title: 'Start a hosted server',
  kind: 'change',
  input: z.strictObject({ server: id('a hosted server id') }),
  // A hosted server runs on the company's credit: money, the person's own press.
  tico: false,
  run: async (ctx, { server }) => {
    const r = await startServer(server);
    if (r.state === 'started') void ctx.queryClient.invalidateQueries({ queryKey: ['settings', 'hosted'] });
    return r;
  },
  did: (_input, out) => (out?.state === 'started' ? 'Starting' : (out?.message ?? 'Not started')),
  target: () => setting('hosted'),
});

// ---------------------------------------------------------------- models and agents

export const setDefaultModel = defineAction({
  id: 'settings.defaultModel',
  title: 'Change the model a new agent uses',
  kind: 'change',
  input: z.strictObject({ model: z.string().check(z.minLength(1), z.maxLength(100), z.describe('a model id from GET /api/v2/models')) }),
  run: (_ctx, { model }) => setDefaults({ model }),
  did: () => 'Default changed',
  target: () => setting('default-model'),
});

export const setMeetingModel = defineAction({
  id: 'settings.meetingModel',
  title: 'Change the model meetings reply on',
  kind: 'change',
  input: z.strictObject({ model: z.string().check(z.maxLength(100), z.describe("a model id; empty for each agent's own")) }),
  run: (_ctx, { model }) => setDefaults({ meeting: model }),
  did: () => 'Meeting model changed',
  target: () => setting('meeting-model'),
});

export const changeModels = defineAction({
  id: 'settings.changeModels',
  title: 'Change the model for several agents',
  kind: 'change',
  input: z.strictObject({
    agents: z.array(z.string().check(z.minLength(1), z.maxLength(120), z.describe('an agent slug'))).check(z.maxLength(200)),
    model: z.string().check(z.minLength(1), z.maxLength(120), z.describe('a model id from GET /api/v2/models')),
    effort: z.optional(z.string().check(z.maxLength(20))),
    harness: z.optional(z.string().check(z.maxLength(40))),
  }),
  run: async (ctx, { agents, model, effort, harness }) => {
    if (!agents.length) throw new Error('Pick at least one agent');
    const [rows, catalog] = await Promise.all([ctx.queryClient.ensureQueryData(agentsQuery), ctx.queryClient.ensureQueryData(modelsQuery)]);
    const runs = catalog.models.find((m) => m.id === model)?.harnesses;
    // Each agent keeps its own harness when the model runs there; otherwise the one asked for, or the model's first.
    const pick = (slug: string) => {
      const own = rows.find((r) => r.slug === slug)?.harness;
      if (!runs?.length || (own && runs.includes(own))) return harness || own;
      return harness && runs.includes(harness) ? harness : runs[0];
    };
    const done = await Promise.all(
      agents.map((slug) => {
        const h = pick(slug);
        return run('agents.setModel', { slug, model, ...(effort ? { effort } : {}), ...(h ? { harness: h } : {}) }, { via: 'click' });
      }),
    );
    const failed = agents.filter((_slug, i) => !done[i]?.ok);
    if (failed.length === agents.length) throw new Error(done[0] && !done[0].ok ? done[0].error : "Couldn't change the model");
    return { changed: agents.length - failed.length, failed };
  },
  did: (_input, out) => (out ? `Changed ${out.changed}${out.failed.length ? `, ${out.failed.length} failed` : ''}` : 'Changed'),
  target: () => setting('agents'),
});

export const moveAgents = defineAction({
  id: 'settings.moveAgents',
  title: 'Move several agents to another computer',
  kind: 'change',
  input: z.strictObject({
    agents: z.array(z.string().check(z.minLength(1), z.maxLength(120), z.describe('an agent slug'))).check(z.maxLength(200)),
    computer: z.string().check(z.minLength(1), z.maxLength(100), z.describe("a computer's id")),
  }),
  run: async (_ctx, { agents, computer }) => {
    if (!agents.length) throw new Error('Pick at least one agent');
    const done = await Promise.all(agents.map((slug) => run('agents.move', { slug, computer }, { via: 'click' })));
    const failed = agents.filter((_slug, i) => !done[i]?.ok);
    if (failed.length === agents.length) throw new Error(done[0] && !done[0].ok ? done[0].error : "Couldn't move them");
    return { moved: agents.length - failed.length, failed };
  },
  did: (_input, out) => (out ? `Moved ${out.moved}${out.failed.length ? `, ${out.failed.length} failed` : ''}` : 'Moved'),
  target: () => setting('agents'),
});

export const taskModel = defineAction({
  id: 'settings.taskModel',
  title: "Change the model an agent's tasks run on",
  kind: 'change',
  input: z.strictObject({
    agent: z.string().check(z.minLength(1), z.maxLength(120), z.describe('an agent slug')),
    model: z.string().check(z.maxLength(120), z.describe("a model id from GET /api/v2/models; empty for the agent's own")),
    effort: z.optional(z.string().check(z.maxLength(20))),
  }),
  run: async (ctx, { agent: slug, model, effort }) => {
    const [rows, catalog] = await Promise.all([ctx.queryClient.ensureQueryData(agentsQuery), ctx.queryClient.ensureQueryData(modelsQuery)]);
    const row = rows.find((r) => r.slug === slug);
    if (!row) throw new Error('No such agent');
    const runs = catalog.models.find((m) => m.id === model)?.harnesses;
    const harness = !runs?.length || (row.harness && runs.includes(row.harness)) ? row.harness : runs[0];
    const out = await setTaskModel(row.id, model ? { model, ...(harness ? { harness } : {}), ...(effort ? { effort } : {}) } : null, row.revision ?? 1);
    void ctx.queryClient.invalidateQueries({ queryKey: agentsQuery.queryKey });
    return { warning: out?.warning };
  },
  did: ({ model }, out) => out?.warning || (model ? 'Tasks moved' : "Tasks run on the agent's own model"),
  target: ({ agent: slug }) => ({ kind: 'agent', id: slug }),
});

const agent = (bot: string) => ({ kind: 'agent', id: bot });

export const restore = defineAction({
  id: 'settings.restoreAgent',
  title: 'Bring back a retired agent',
  kind: 'change',
  input: z.strictObject({ agent: id('an agent id or slug') }),
  run: async (ctx, { agent: bot }) => {
    await restoreAgent(bot);
    void ctx.queryClient.invalidateQueries({ queryKey: agentsQuery.queryKey });
  },
  did: () => 'Restored',
  target: ({ agent: bot }) => agent(bot),
});

export const owners = defineAction({
  id: 'settings.agentOwners',
  title: "Change who owns an agent",
  kind: 'change',
  input: z.strictObject({
    agent: id('an agent id or slug'),
    add: z.array(z.string().check(z.minLength(1), z.maxLength(200))).check(z.maxLength(100)),
    remove: z.array(z.string().check(z.minLength(1), z.maxLength(200))).check(z.maxLength(100)),
  }),
  // Owners manage the agent and its access: a permission change.
  tico: false,
  run: (_ctx, { agent: bot, add, remove: drop }) => setCoOwners(bot, add, drop),
  did: () => 'Owners changed',
  target: ({ agent: bot }) => agent(bot),
});

const audience = z.strictObject({
  everyone: z.boolean(),
  people: z.array(z.string().check(z.minLength(1), z.maxLength(200))).check(z.maxLength(500)),
  teams: z.array(z.string().check(z.minLength(1), z.maxLength(200))).check(z.maxLength(200)),
  bots: z.array(z.string().check(z.minLength(1), z.maxLength(200))).check(z.maxLength(500)),
});

export const access = defineAction({
  id: 'settings.agentAccess',
  title: 'Change who can see, read and write to an agent',
  kind: 'change',
  input: z.strictObject({ agent: id('an agent id or slug'), see: audience, read: audience, write: audience }),
  // Who reaches an agent's work is a permission change.
  tico: false,
  run: (_ctx, { agent: bot, ...levels }) => setBotAccess(bot, levels),
  did: () => 'Access changed',
  target: ({ agent: bot }) => agent(bot),
});

export const mail = defineAction({
  id: 'settings.agentMail',
  title: 'Let an agent send mail without asking',
  kind: 'change',
  input: z.strictObject({ agent: id('an agent id or slug'), on: z.boolean() }),
  // Mail that leaves the company with no one's OK cannot be called back: Tico may only turn it off (asks first).
  can: (ctx, { on }) => !(on && ctx.via === 'tico'),
  run: (_ctx, { agent: bot, on }) => setBotMail(bot, on),
  did: ({ on }) => (on ? 'Sends on its own' : 'Asks first'),
  target: ({ agent: bot }) => agent(bot),
});

export const settleRun = defineAction({
  id: 'settings.stoppedRun',
  title: 'Try a stopped run again, or let it go',
  kind: 'change',
  input: z.strictObject({
    agent: id('an agent id or slug'),
    job: id('a job id'),
    run: id('a run id'),
    decision: z.enum(['resume', 'dismiss'] as const),
    detail: z.optional(z.string().check(z.maxLength(3000))),
  }),
  // A retry may repeat work that already reached the outside world.
  tico: false,
  run: (_ctx, { agent: bot, job, run: rid, decision, detail }) => reconcileRun(bot, job, rid, decision, detail),
  did: ({ decision }, out) =>
    out?.deferred ? 'Saved for when its turn ends' : decision === 'resume' ? 'Queued to try again' : "Dismissed; it won't retry",
  target: ({ agent: bot }) => agent(bot),
});

// ---------------------------------------------------------------- tasks and contacts

export const roadmapLabels = defineAction({
  id: 'settings.roadmapLabels',
  title: 'Change the labels on the public roadmap',
  kind: 'change',
  input: z.strictObject({ labels: z.array(z.string().check(z.minLength(1), z.maxLength(200))).check(z.maxLength(50)) }),
  // What the public sees: Tico may only take a label off (shows less); putting one on is the person's own press.
  can: (ctx, { labels }) => ctx.via !== 'tico' || labels.every((l) => ctx.queryClient.getQueryData(roadmapQuery.queryKey)?.labels.includes(l)),
  run: (_ctx, { labels }) => setRoadmap({ labels }),
  did: () => 'Roadmap changed',
  target: () => setting('roadmap'),
});

export const roadmapApproval = defineAction({
  id: 'settings.roadmapApproval',
  title: "Ask a person before an agent's words go public",
  kind: 'change',
  input: z.strictObject({ on: z.boolean() }),
  // Asking first is Tico's; dropping the ask lets an agent's words leave the company unread.
  can: (ctx, { on }) => !(!on && ctx.via === 'tico'),
  run: (_ctx, { on }) => setRoadmap({ bot_public_approval: on }),
  did: ({ on }) => (on ? 'Turned on' : 'Turned off'),
  target: () => setting('roadmap-approval'),
});

export const addField = defineAction({
  id: 'settings.addField',
  title: 'Add a contact field',
  kind: 'change',
  input: z.strictObject({
    type: id('a contact type'),
    name: z.string().check(z.minLength(1), z.maxLength(80)),
    kind: z.enum(FIELD_KINDS),
    description: z.string().check(z.minLength(1), z.maxLength(500), z.describe('what the field holds')),
  }),
  run: async (ctx, { type, name, kind, description }) => {
    const made = await addContactField(type, name, kind, description);
    void ctx.queryClient.invalidateQueries({ queryKey: contactTypeQuery(type).queryKey });
    void ctx.queryClient.invalidateQueries({ queryKey: contactTypesQuery.queryKey });
    return made;
  },
  did: ({ name }) => `Added ${name}`,
  target: ({ type }) => ({ kind: 'contact-type', id: type }),
});

export const productUpdate = defineAction({
  id: 'settings.productUpdate',
  title: 'Add a product update',
  kind: 'change',
  input: z.strictObject({
    title: z.string().check(z.minLength(1), z.maxLength(90)),
    bullets: z.array(z.string().check(z.minLength(1), z.maxLength(500))).check(z.maxLength(12)),
  }),
  // Published to everyone, and the server has no way to take an update back.
  tico: false,
  run: (_ctx, { title, bullets }) => {
    if (!bullets.length) throw new Error('Add at least one bullet');
    return addProductUpdate(title, bullets);
  },
  did: () => 'Published',
  target: () => ({ kind: 'page', id: '/settings/changelog' }),
});

// ---- usage (anchor: the Usage builder adds its actions below) ----

const money = z.optional(z.number().check(z.minimum(0), z.maximum(1_000_000)));

export const openUsage = defineAction({
  id: 'usage.open',
  title: 'Open usage',
  kind: 'view',
  input: z.strictObject({
    view: z.optional(z.enum(['limits'] as const)),
    range: z.optional(z.enum(['today', '7d', '30d', 'month'] as const)),
    by: z.optional(z.enum(['model'] as const)),
    open: z.optional(z.string().check(z.maxLength(120))),
  }),
  run: ({ router }, search) => router.navigate({ to: '/company/usage', search }),
  show: ({ router }, search) => router.navigate({ to: '/company/usage', search }),
  did: () => 'Opened usage',
  target: () => ({ kind: 'page', id: '/company/usage' }),
});

export const limit = defineAction({
  id: 'usage.setLimit',
  title: "Change an agent's monthly limit",
  kind: 'change',
  input: z.strictObject({ agent: id('an agent slug'), monthly: money }),
  // Raising or removing a spending limit is budget, the person's own press; Tico may only lower one.
  can: (ctx, { agent, monthly }) => {
    if (ctx.via !== 'tico') return true;
    const now = ctx.queryClient.getQueryData(limitsQuery.queryKey)?.bots[agent];
    return lowers(now ? now.monthly_usd : undefined, monthly);
  },
  run: (_ctx, { agent, monthly }) => setLimit(agent, monthly ?? null),
  did: ({ monthly }) => (monthly == null ? 'Limit removed' : `Limit set to $${monthly}`),
  target: ({ agent }) => ({ kind: 'limit', id: agent }),
});

export const defaultLimit = defineAction({
  id: 'usage.setDefaultLimit',
  title: 'Change the monthly limit for a new agent',
  kind: 'change',
  input: z.strictObject({ monthly: money }),
  // Raising or removing the default is budget, the person's own press; Tico may only lower it.
  can: (ctx, { monthly }) => ctx.via !== 'tico' || lowers(ctx.queryClient.getQueryData(limitsQuery.queryKey)?.default.monthly_usd, monthly),
  run: (_ctx, { monthly }) => setDefaultLimit(monthly ?? null),
  did: ({ monthly }) => (monthly == null ? 'Limit removed' : `Limit set to $${monthly}`),
  target: () => ({ kind: 'limit', id: 'default' }),
});

export const countPlanRuns = defineAction({
  id: 'usage.countPlanRuns',
  title: 'Count plan runs against the limits',
  kind: 'change',
  input: z.strictObject({ on: z.boolean() }),
  // Counting less spend raises every limit: budget, the person's own press. Tico may only turn it on.
  can: (ctx, { on }) => ctx.via !== 'tico' || on,
  run: (_ctx, { on }) => setCountPlanRuns(on),
  did: ({ on }) => (on ? 'Plan runs count' : 'Plan runs no longer count'),
  target: () => ({ kind: 'limit', id: 'default' }),
});

export const clearQueue = defineAction({
  id: 'usage.clearQueue',
  title: 'Clear the automatic queue',
  kind: 'change',
  input: z.strictObject({ scope: z.optional(z.string().check(z.maxLength(200), z.describe("'company' or 'group:<id>'"))) }),
  // Cancelled wakes do not come back.
  tico: false,
  run: (ctx, { scope }) => {
    const done = purgeQueue(scope || 'company');
    void done.then(() => ctx.queryClient.invalidateQueries({ queryKey: ['usage'] }));
    return done;
  },
  did: (_input, out) => (out ? `Cleared ${out.cancelled} queued, ${out.kept_people} kept, ${out.still_running} running` : 'Cleared'),
  target: () => ({ kind: 'limit', id: 'default' }),
});

// ---------------------------------------------------------------- Tico Gateway (owner)

export const gatewaySave = defineAction({
  id: 'settings.saveGateway',
  title: 'Change the Tico Gateway key or address',
  kind: 'change',
  input: z.strictObject({
    url: z.string().check(z.maxLength(300), z.describe("the Gateway's address; empty for Tico's")),
    key: z.optional(z.string().check(z.minLength(1), z.maxLength(500))),
  }),
  // Sets a secret and where it goes: the owner's own press. A move with a key saved asks first (Keep or Clear).
  tico: false,
  run: (_ctx, input) => withDestination((credentials) => saveGateway({ ...input, ...(credentials ? { credentials } : {}) }), askDestination),
  did: () => 'Saved',
  target: () => setting('gateway'),
});

// ---------------------------------------------------------------- the desktop app (this person's own computer)

const onDesktop = () => desktopApp() !== null;
const theApp = () => {
  const a = desktopApp();
  if (!a) throw new Error('Only in the desktop app');
  return a;
};

export const restartToUpdate = defineAction({
  id: 'settings.restartToUpdate',
  title: 'Restart the desktop app to update',
  kind: 'change',
  input: z.strictObject({}),
  tico: false,
  can: onDesktop,
  // The app refuses while a call is live.
  run: () => theApp().restartToUpdate(),
  did: () => 'Restarting',
  target: () => setting('updates'),
});

export const openAtLogin = defineAction({
  id: 'settings.openAtLogin',
  title: 'Open the desktop app at login',
  kind: 'change',
  input: z.strictObject({ on: z.boolean() }),
  tico: false,
  can: onDesktop,
  run: (_ctx, { on }) => theApp().openAtLogin(on),
  did: ({ on }) => (on ? 'Opens at login' : 'Stopped opening at login'),
  target: () => setting('open-at-login'),
});

// ---- gap wave (the Usage builder): pools, task caps, Gateway checks ----

/** True when `next` lowers (or keeps) the limit in force now (null: none). Unread (undefined) or removed: false. */
function lowers(now: number | null | undefined, next: number | null | undefined): boolean {
  if (next == null || now === undefined) return false;
  return now === null || next <= now;
}

export const poolLimit = defineAction({
  id: 'usage.setPoolLimit',
  title: "Change the company's or a team's monthly limit",
  kind: 'change',
  input: z.strictObject({ scope: id("'company' or a team's group id"), monthly: money }),
  // Raising or removing a combined limit is budget, the person's own press; Tico may only lower one.
  can: (ctx, { scope, monthly }) => {
    if (ctx.via !== 'tico') return true;
    const p = poolsOf(ctx.queryClient.getQueryData(limitsQuery.queryKey));
    const now = scope === 'company' ? p?.company : p?.groups[scope];
    return lowers(now ? now.monthly_usd : undefined, monthly);
  },
  run: (_ctx, { scope, monthly }) => setPoolLimit(scope, monthly ?? null),
  did: ({ monthly }) => (monthly == null ? 'Limit removed' : `Limit set to $${monthly}`),
  target: ({ scope }) => ({ kind: 'limit', id: scope === 'company' ? 'company' : `team:${scope}` }),
});

export const taskCap = defineAction({
  id: 'usage.setTaskCap',
  title: 'Cap the tasks one teammate sends another a day',
  kind: 'change',
  input: z.strictObject({
    sender: id("'bot:<slug>' or 'person:<id>'"),
    receiver: id("a manager above the sender, 'bot:<slug>' or 'person:<id>'"),
    perDay: z.optional(z.number().check(z.minimum(0), z.maximum(10_000))),
  }),
  // A cap is a limit: Tico may add or lower one, never raise or remove it.
  can: (ctx, { sender, receiver, perDay }) => {
    if (ctx.via !== 'tico') return true;
    const caps = ctx.queryClient.getQueryData(taskCapsQuery.queryKey)?.caps;
    if (!caps) return false;
    // An agent may be named by its slug or its id: either names the same cap.
    const roster = ctx.queryClient.getQueryData(agentsQuery.queryKey) ?? [];
    const same = (ref: string) => {
      const a = roster.find((r) => `bot:${r.slug}` === ref || `bot:${r.id}` === ref);
      return a ? [`bot:${a.slug}`, `bot:${a.id}`] : [ref];
    };
    const senders = same(sender);
    const receivers = same(receiver);
    const now = caps.find((c) => senders.includes(c.sender) && receivers.includes(c.receiver));
    return lowers(now ? now.per_day : null, perDay);
  },
  run: (_ctx, { sender, receiver, perDay }) => setTaskCap(sender, receiver, perDay ?? null),
  did: ({ perDay }) => (perDay == null ? 'Cap removed' : perDay === 0 ? 'Blocked' : `Capped at ${perDay} a day`),
  target: ({ sender }) => ({ kind: 'limit', id: sender.replace(/^bot:/, '') }),
});

// ---- company gaps (anchor: General, People, tokens, Security and Tico support add their actions below) ----

const accessNow = (ctx: Ctx) => ctx.queryClient.getQueryData<AccessFull>(accessQuery.queryKey);
const person = (p: string) => ({ kind: 'person', id: p });
const addresses = z.array(z.string().check(z.minLength(1), z.maxLength(320))).check(z.maxLength(500));

/** A file from the person's disk: only a click has one (as files.upload). */
const picture = z.custom<File>((v) => typeof File !== 'undefined' && v instanceof File, 'expected a file');

export const joining = defineAction({
  id: 'settings.setJoining',
  title: 'Change who can join by signing in',
  kind: 'change',
  input: z.strictObject({ allowed: addresses, domains: z.array(z.string().check(z.minLength(1), z.maxLength(253))).check(z.maxLength(100)) }),
  // Letting more people in is the person's own press; taking an address or a domain off stays Tico's.
  can: (ctx, { allowed, domains }) => {
    if (ctx.via !== 'tico') return true;
    const v = accessNow(ctx);
    return !!v && allowed.every((x) => v.allowed.includes(x)) && domains.every((d) => v.allowed_domains.includes(d));
  },
  run: (_ctx, { allowed, domains }) => setAllowed(allowed, domains),
  did: () => 'Joining changed',
  target: () => setting('joining'),
});

export const agentLimit = defineAction({
  id: 'settings.agentLimit',
  title: 'Change how many agents a person may have',
  kind: 'change',
  input: z.strictObject({ limit: z.int().check(z.minimum(0), z.maximum(1000)) }),
  // Raising a limit is the person's own press; lowering it stays Tico's.
  can: (ctx, { limit }) => ctx.via !== 'tico' || limit <= (accessNow(ctx)?.member_bot_limit ?? 0),
  run: (_ctx, { limit }) => setAgentLimit(limit),
  did: ({ limit }) => `Limit set to ${limit}`,
  target: () => setting('agent-limit'),
});

export const restoreLeft = defineAction({
  id: 'settings.restorePerson',
  title: 'Bring back a person who left',
  kind: 'change',
  input: z.strictObject({ person: id('a person id') }),
  // Gives someone their sign-in back.
  tico: false,
  run: (_ctx, { person: p }) => restorePerson(p),
  did: () => 'Restored',
  target: ({ person: p }) => person(p),
});

export const forget = defineAction({
  id: 'settings.forgetPerson',
  title: 'Remove a person who left, for good',
  kind: 'change',
  input: z.strictObject({ person: id('a person id') }),
  // Cannot be undone.
  tico: false,
  run: (_ctx, { person: p }) => forgetPerson(p),
  did: () => 'Removed',
  target: () => setting('people'),
});

export const revokeAny = defineAction({
  id: 'settings.revokeAnyToken',
  title: "Revoke someone's API token",
  kind: 'change',
  input: z.strictObject({ token: id('a token id') }),
  // Its scripts stop for good: cannot be undone.
  tico: false,
  run: (_ctx, { token }) => revokeAnyToken(token),
  did: () => 'Token revoked',
  target: ({ token }) => ({ kind: 'token', id: token }),
});

export const letSupportIn = defineAction({
  id: 'settings.letSupportIn',
  title: 'Let Tico support sign in for a while',
  kind: 'change',
  input: z.strictObject({ ticket: id('a support request id'), tier: z.enum(['app', 'full'] as const), hours: z.int().check(z.minimum(1), z.maximum(8760)) }),
  // Lets someone from outside the company in: the person's own press.
  tico: false,
  run: (_ctx, { ticket, tier, hours }) => startSupportAccess(ticket, tier, hours),
  did: () => 'Tico support let in',
  target: () => setting('support-access'),
});

export const stopSupport = defineAction({
  id: 'settings.stopSupport',
  title: "End Tico support's sign-in now",
  kind: 'change',
  input: z.strictObject({ session: id('a support session id') }),
  run: (_ctx, { session }) => stopSupportAccess(session),
  did: () => 'Tico support stopped',
  target: () => setting('support-access'),
});

export const teamIcon = defineAction({
  id: 'settings.teamIcon',
  title: "Change the company's icon",
  kind: 'change',
  input: z.strictObject({ file: picture }),
  // Only a press has a file to send.
  tico: false,
  run: (_ctx, { file }) => setTeamIcon(file),
  did: () => 'Icon changed',
  target: () => setting('icon'),
});

const repo = z.string().check(z.minLength(3), z.maxLength(200), z.regex(/^[^/\s]+\/[^/\s]+$/), z.describe('owner/name'));

export const useRepo = defineAction({
  id: 'settings.useRepo',
  title: 'Put a repository in use or take it out',
  kind: 'change',
  input: z.strictObject({ repo, on: z.boolean() }),
  run: (_ctx, { repo: name, on }) => setRepo(name, { enabled: on }),
  did: ({ on }) => (on ? 'In use' : 'Not in use'),
  target: ({ repo: name }) => ({ kind: 'repo', id: name }),
});

export const repoSetup = defineAction({
  id: 'settings.repoSetup',
  title: "Change a repository's setup command",
  kind: 'change',
  input: z.strictObject({ repo, command: z.string().check(z.maxLength(2000)) }),
  run: (_ctx, { repo: name, command }) => setRepo(name, { setup_command: command.trim() }),
  did: () => 'Setup command saved',
  target: ({ repo: name }) => ({ kind: 'repo', id: name }),
});

export const newAgentRepos = defineAction({
  id: 'settings.newAgentRepos',
  title: 'Choose the repositories new agents get',
  kind: 'change',
  input: z.strictObject({ value: z.enum(['own', 'read', 'all'] as const) }),
  run: (_ctx, { value }) => setNewAgentRepos(value),
  did: () => 'Saved',
  target: () => setting('repositories'),
});

export const reposRefresh = defineAction({
  id: 'settings.refreshRepos',
  title: 'Read the repositories from GitHub again',
  kind: 'change',
  input: z.strictObject({}),
  run: () => refreshRepos(),
  did: () => 'Refreshed',
  target: () => setting('repositories'),
});

const repoName = z.string().check(z.minLength(1), z.maxLength(100), z.regex(/^[A-Za-z0-9._-]+$/), z.describe('a repository name'));

export const repoPreview = defineAction({
  id: 'settings.previewRepo',
  title: 'See what a new repository would be',
  kind: 'view',
  input: z.strictObject({ name: repoName }),
  run: (_ctx, { name }): Promise<ProductRepoPreview> => previewRepo(name),
  did: () => 'Previewed',
  target: () => setting('repositories'),
});

export const repoCreate = defineAction({
  id: 'settings.createRepo',
  title: 'Make a new private repository on GitHub',
  kind: 'change',
  input: z.strictObject({ name: repoName }),
  // Makes a repository outside Tico that Tico cannot take back: the owner's own press.
  tico: false,
  run: async (ctx, { name }) => {
    const p = await previewRepo(name);
    if (p.capability !== 'available') throw new Error(p.capability_detail || 'GitHub cannot make it now');
    const made = await createRepo(p);
    void ctx.queryClient.invalidateQueries({ queryKey: ['settings', 'repositories'] });
    return made;
  },
  did: (_input, out) => (out ? `Made ${out.repository}` : 'Made'),
  target: () => setting('repositories'),
});

// ---- tier 2 gaps (Tasks, Health, Changelog, Contacts): this builder's actions below ----

const askInput = {
  mode: z.enum(['off', 'on'] as const),
  budget: z.int().check(z.minimum(1), z.maximum(200)),
  criteria: z.array(z.string().check(z.minLength(1), z.maxLength(200))).check(z.maxLength(10)),
};

export const askRules = defineAction({
  id: 'settings.askRules',
  title: 'Change what agents may ask a person',
  kind: 'change',
  input: z.strictObject({ ...askInput, team: z.optional(z.boolean()) }),
  run: (_ctx, { team, ...rules }) => saveAsk(rules, !!team),
  did: () => 'Saved; the next ask uses it',
  target: () => setting('ask'),
});

export const askReset = defineAction({
  id: 'settings.askReset',
  title: 'Use the team default for what agents may ask',
  kind: 'change',
  input: z.strictObject({}),
  can: (ctx) => !!ctx.queryClient.getQueryData(askQuery.queryKey)?.settings.own,
  run: () => resetAsk(),
  did: () => 'Using the team default',
  target: () => setting('ask'),
});

export const healthCheck = defineAction({
  id: 'settings.checkHealth',
  title: 'Check health now',
  kind: 'view',
  input: z.strictObject({}),
  run: async (ctx) => {
    await ctx.queryClient.refetchQueries({ queryKey: healthQuery.queryKey, exact: true });
  },
  did: () => 'Checked',
  target: () => setting('health'),
});

export const changelogRead = defineAction({
  id: 'settings.changelogRead',
  title: 'Mark product updates read',
  kind: 'change',
  input: z.strictObject({ ids: z.array(id('an entry id')).check(z.maxLength(250)) }),
  run: (_ctx, { ids }) => markChangelogRead(ids),
  did: () => 'Marked read',
  target: () => ({ kind: 'page', id: '/settings/changelog' }),
});

// ---------------------------------------------------------------- company name

// Renaming is ordinary work: open to Tico. The server lets only the owner save setup.
export const companyRename = defineAction({
  id: 'settings.renameCompany',
  title: 'Rename the company',
  kind: 'change',
  input: z.strictObject({ name: z.string().check(z.minLength(1), z.maxLength(100), z.describe('the company name')) }),
  run: (_ctx, { name }) => renameCompany(name),
  did: ({ name }) => `Renamed to ${name}`,
  target: () => setting('companyName'),
});
