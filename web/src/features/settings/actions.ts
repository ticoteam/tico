import { defineAction, s } from '@/actions';
import { meQuery } from '@/data';
import { setMotionChoice } from '@/motion';
import { removePref, writePref } from '@/prefs';
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
  setGatewayFallback,
  setLimit,
  startSignIn,
  renamePerson,
  revokeToken,
  sendInvite,
  setMyNotice,
  setPageOwner,
  setRole,
  setRule,
  setScreening,
  setSupport,
  subscriptionsQuery,
  tokensQuery,
} from './queries';
import { handOver } from './handover';
import { SECTION_IDS } from './sections';

/* Every change on Settings and Usage. Appearance is this device's own (prefs); the rest goes to the server. Anything
   that sets or shows a secret, deletes the company or changes billing is the person's own press (tico: false). */

const id = (what: string) => s.string({ min: 1, max: 200, what });
const setting = (key: string) => ({ kind: 'setting', id: key });
const bare = (actor: string) => actor.replace(/^human:/, '');

export const openSection = defineAction({
  id: 'settings.open',
  title: 'Open a settings section',
  kind: 'view',
  input: s.object({ section: s.oneOf(SECTION_IDS) }),
  run: ({ router }, { section }) => router.navigate({ to: '/settings/$section', params: { section } }),
  show: ({ router }, { section }) => router.navigate({ to: '/settings/$section', params: { section } }),
  did: ({ section }) => `Opened ${section}`,
  target: ({ section }) => ({ kind: 'page', id: `/settings/${section}` }),
});

// ---------------------------------------------------------------- appearance (this device)

export type Theme = 'system' | 'light' | 'dark';
export const THEME_KEY = 'theme.v1';

/** The theme boot.js sets before paint, set again now: system follows the device's light or dark. */
export function applyTheme(theme: Theme): void {
  const light = theme === 'light' || (theme === 'system' && matchMedia('(prefers-color-scheme: light)').matches);
  document.documentElement.setAttribute('data-theme', light ? 'light' : 'dark');
}

export const setTheme = defineAction({
  id: 'settings.setTheme',
  title: 'Change the theme',
  kind: 'change',
  input: s.object({ theme: s.oneOf(['system', 'light', 'dark'] as const) }),
  run: (_ctx, { theme }) => {
    writePref(THEME_KEY, theme);
    applyTheme(theme);
  },
  did: ({ theme }) => `Theme set to ${theme}`,
  target: () => setting('theme'),
});

export const setMotion = defineAction({
  id: 'settings.setMotion',
  title: 'Change the motion level',
  kind: 'change',
  input: s.object({ motion: s.oneOf(['system', 'more', 'less', 'off'] as const) }),
  run: (_ctx, { motion }) => setMotionChoice(motion),
  did: ({ motion }) => `Motion set to ${motion}`,
  target: () => setting('motion'),
});

export const resetSizes = defineAction({
  id: 'settings.resetSizes',
  title: 'Reset sheet sizes',
  kind: 'change',
  input: s.object({}),
  // shell/memory.ts resetSheetSizes, which features may not import: the same key, forgotten.
  run: () => removePref('sheets.v1'),
  did: () => 'Sheet sizes reset',
  target: () => setting('sizes'),
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
  input: s.object({ field: s.oneOf(['notify_browser', 'email_digest'] as const), on: s.boolean() }),
  run: (ctx, { field, on }) => setMyNotice(myId(ctx), field, on),
  did: ({ on }) => (on ? 'Turned on' : 'Turned off'),
  target: ({ field }) => setting(field),
});

export const signOut = defineAction({
  id: 'settings.signOut',
  title: 'Sign out',
  kind: 'change',
  input: s.object({}),
  tico: false,
  run: () => window.location.assign('/api/v2/logout'),
});

export const makeToken = defineAction({
  id: 'settings.createToken',
  title: 'Make an API token',
  kind: 'change',
  input: s.object({ label: s.string({ min: 1, max: 80 }) }),
  tico: false,
  run: async (ctx, { label }) => {
    const made = await createToken(label);
    handOver('token', made.token);
    void ctx.queryClient.invalidateQueries({ queryKey: tokensQuery.queryKey });
    return { id: made.id };
  },
  did: () => 'Token made',
  target: () => setting('tokens'),
});

export const dropToken = defineAction({
  id: 'settings.revokeToken',
  title: 'Revoke an API token',
  kind: 'change',
  input: s.object({ token: id('a token id') }),
  run: (_ctx, { token }) => revokeToken(token),
  did: () => 'Token revoked',
  target: ({ token }) => ({ kind: 'token', id: token }),
});

// ---------------------------------------------------------------- people

export const invite = defineAction({
  id: 'settings.invite',
  title: 'Add a person',
  kind: 'change',
  input: s.object({ email: s.string({ min: 3, max: 320, pattern: /^[^\s@]+@[^\s@]+$/, what: 'an email address' }), name: s.optional(s.string({ max: 120 })) }),
  run: (_ctx, { email, name }) => addPerson(email, name ?? ''),
  did: ({ email }) => `Invited ${email}`,
  target: (_input, out) => (out ? { kind: 'person', id: out.person } : setting('people')),
});

export const resendInvite = defineAction({
  id: 'settings.resendInvite',
  title: 'Send an invite again',
  kind: 'change',
  input: s.object({ person: id('a person id') }),
  run: (_ctx, { person }) => sendInvite(person),
  did: () => 'Invite sent',
  target: ({ person }) => ({ kind: 'person', id: person }),
});

export const changeRole = defineAction({
  id: 'settings.setRole',
  title: "Change a person's role",
  kind: 'change',
  input: s.object({ person: id('a person id'), role: s.oneOf(['admin', 'member'] as const) }),
  run: (_ctx, { person, role }) => setRole(person, role),
  did: ({ role }) => `Role set to ${role}`,
  target: ({ person }) => ({ kind: 'person', id: person }),
});

export const rename = defineAction({
  id: 'settings.rename',
  title: 'Rename a person',
  kind: 'change',
  input: s.object({ person: id('a person id'), name: s.string({ min: 1, max: 120 }) }),
  run: (_ctx, { person, name }) => renamePerson(person, name),
  did: ({ name }) => `Renamed to ${name}`,
  target: ({ person }) => ({ kind: 'person', id: person }),
});

export const remove = defineAction({
  id: 'settings.removePerson',
  title: 'Remove a person',
  kind: 'change',
  input: s.object({ person: id('a person id') }),
  run: (_ctx, { person }) => removePerson(person),
  did: () => 'Removed',
  target: () => setting('people'),
});

// ---------------------------------------------------------------- general

export const pageOwner = defineAction({
  id: 'settings.setPageOwner',
  title: "Change a page's owner",
  kind: 'change',
  input: s.object({ page: id('a page key'), owner: id("'default', 'none' or an agent id") }),
  run: (_ctx, { page, owner }) => setPageOwner(page, owner),
  did: () => 'Owner changed',
  target: ({ page }) => ({ kind: 'page-owner', id: page }),
});

export const reportsDirect = defineAction({
  id: 'settings.reportsDirect',
  title: 'Agents report to Tico support',
  kind: 'change',
  input: s.object({ on: s.boolean() }),
  run: (_ctx, { on }) => setRule('bot_reports_direct', on),
  did: ({ on }) => (on ? 'Turned on' : 'Turned off'),
  target: () => setting('reports'),
});

// ---------------------------------------------------------------- security

export const screenMode = defineAction({
  id: 'settings.setScreening',
  title: 'Change how a door is screened',
  kind: 'change',
  input: s.object({ door: s.oneOf(['webhook', 'mail', 'idea', 'watcher', 'document', 'slack'] as const), mode: s.oneOf(['off', 'warn', 'hold'] as const) }),
  tico: false,
  run: (_ctx, { door, mode }) => setScreening({ [door]: mode }),
  did: ({ mode }) => `Screening set to ${mode}`,
  target: ({ door }) => setting(`screen-${door}`),
});

export const detector = defineAction({
  id: 'settings.setDetector',
  title: 'Turn a screening detector on or off',
  kind: 'change',
  input: s.object({ detector: s.oneOf(['patterns', 'model'] as const), on: s.boolean() }),
  tico: false,
  run: (_ctx, { detector, on }) => setScreening({ [detector]: on }),
  did: ({ on }) => (on ? 'Turned on' : 'Turned off'),
  target: ({ detector }) => setting(`detector-${detector}`),
});

export const support = defineAction({
  id: 'settings.setSupport',
  title: 'Let Tico support reach the computers',
  kind: 'change',
  input: s.object({ on: s.boolean() }),
  tico: false,
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
  input: s.object({ kind: s.oneOf(['claude_login', 'codex_login'] as const) }),
  // The sign-in opens the provider's page in the person's own browser, signed in as them.
  tico: false,
  run: async (_ctx, { kind }) => {
    const login = await startSignIn(kind);
    handOver('sign-in', login);
    return login?.id ? { id: login.id } : null;
  },
  did: ({ kind }) => (kind === 'codex_login' ? 'Codex sign-in started' : 'Claude sign-in started'),
  target: () => setting('subscriptions'),
});

export const signInCode = defineAction({
  id: 'settings.signInCode',
  title: 'Send the code from the sign-in page',
  kind: 'change',
  input: s.object({ computer: id('a computer id'), login: id('a sign-in id'), code: s.string({ min: 1, max: 2000, what: 'the code' }) }),
  tico: false,
  run: (_ctx, { computer, login, code }) => sendSignInCode(computer, login, code),
  did: () => 'Code sent',
  target: () => setting('subscriptions'),
});

export const stopSignIn = defineAction({
  id: 'settings.cancelSignIn',
  title: 'Stop a sign-in',
  kind: 'change',
  input: s.object({ computer: id('a computer id'), login: id('a sign-in id') }),
  run: (_ctx, { computer, login }) => cancelSignIn(computer, login),
  did: () => 'Stopped',
  target: () => setting('subscriptions'),
});

export const addKey = defineAction({
  id: 'settings.addKey',
  title: 'Add a model key',
  kind: 'change',
  input: s.object({ kind: id('a subscription kind'), secret: s.string({ min: 8, max: 20000, what: 'the key' }), name: s.optional(s.string({ max: 80 })) }),
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
  input: s.object({ subscription: id('a subscription id') }),
  run: (_ctx, { subscription }) => removeSubscription(subscription),
  did: () => 'Removed',
  target: () => setting('subscriptions'),
});

export const refreshSub = defineAction({
  id: 'settings.refreshUsage',
  title: "Check a subscription's use again",
  kind: 'change',
  input: s.object({ subscription: id('a subscription id') }),
  run: (_ctx, { subscription }) => refreshSubscription(subscription),
  did: () => 'Checked',
  target: ({ subscription }) => sub(subscription),
});

const comp = (rid: string) => ({ kind: 'computer', id: rid });

export const nameComputer = defineAction({
  id: 'settings.nameComputer',
  title: 'Rename a computer',
  kind: 'change',
  input: s.object({ computer: id('a computer id'), name: s.string({ min: 1, max: 80 }) }),
  run: (_ctx, { computer, name }) => labelComputer(computer, name),
  did: ({ name }) => `Renamed to ${name}`,
  target: ({ computer }) => comp(computer),
});

export const dropComputer = defineAction({
  id: 'settings.removeComputer',
  title: 'Remove a computer',
  kind: 'change',
  input: s.object({ computer: id('a computer id') }),
  run: (_ctx, { computer }) => removeComputer(computer),
  did: () => 'Removed',
  target: () => setting('computers'),
});

export const fallback = defineAction({
  id: 'settings.gatewayFallback',
  title: 'Fall back to Tico Gateway',
  kind: 'change',
  input: s.object({ computer: id('a computer id'), on: s.boolean() }),
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
  input: s.object({}),
  // The one-time code lets a machine join the company: a secret, shown once.
  tico: false,
  run: async (ctx) => {
    const { code } = await enrollComputer(myId(ctx));
    handOver('enroll', code);
  },
  did: () => 'Code made',
  target: () => setting('computers'),
});

// ---- usage (anchor: the Usage builder adds its actions below) ----

const money = s.optional(s.number({ min: 0, max: 1_000_000 }));

export const openUsage = defineAction({
  id: 'usage.open',
  title: 'Open usage',
  kind: 'view',
  input: s.object({
    view: s.optional(s.oneOf(['limits'] as const)),
    range: s.optional(s.oneOf(['today', '7d', '30d', 'month'] as const)),
    by: s.optional(s.oneOf(['model'] as const)),
    open: s.optional(s.string({ max: 120 })),
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
  input: s.object({ agent: id('an agent slug'), monthly: money }),
  run: (_ctx, { agent, monthly }) => setLimit(agent, monthly ?? null),
  did: ({ monthly }) => (monthly == null ? 'Limit removed' : `Limit set to $${monthly}`),
  target: ({ agent }) => ({ kind: 'limit', id: agent }),
});

export const defaultLimit = defineAction({
  id: 'usage.setDefaultLimit',
  title: 'Change the monthly limit for a new agent',
  kind: 'change',
  input: s.object({ monthly: money }),
  run: (_ctx, { monthly }) => setDefaultLimit(monthly ?? null),
  did: ({ monthly }) => (monthly == null ? 'Limit removed' : `Limit set to $${monthly}`),
  target: () => ({ kind: 'limit', id: 'default' }),
});
