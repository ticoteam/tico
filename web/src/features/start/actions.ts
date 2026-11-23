import type { AnyRouter } from '@tanstack/react-router';
import * as z from 'zod/mini';
import { defineAction, run } from '@/actions';
import { meQuery, queryClient } from '@/data';
import { tellDesktopSigningOut } from '@/desktop';
import { providerReady, subscriptionsQuery } from '@/features/settings/routing';
import { forgetSetup, goToGithub, handOff, pendingSetup, refused } from './handoff';
import {
  checkGithub,
  disconnectGithub,
  finishGithub,
  finishSetup,
  githubAuthorizeLink,
  githubSetup,
  replaceGithubKey,
  saveCompanyName,
  saveKey,
  setupQuery,
} from './queries';
import { STEPS, resumeStep, stepBefore, type SetupStep } from './steps';

/** Open the first run at a step, or close it (null). Steps push, so Back walks them. */
const toStep = (router: AnyRouter, step: SetupStep | null) =>
  router.navigate({
    to: '.',
    search: ({ setup: _, ...rest }: Record<string, unknown>) => (step ? { ...rest, setup: step } : rest),
  } as never);

const isOwner = () => queryClient.getQueryData(meQuery.queryKey)?.role === 'owner';
const SETUP_TARGET = { kind: 'dialog', id: 'setup' } as const;

// What Tico hears when the person hands it the setup: it asks the rest in its own chat.
const HAND_OFF = 'Set up my company.';

/* Signing in and out is the person's own: the server's pages do it, never Tico. */
export const signOut = defineAction({
  id: 'start.signOut',
  title: 'Sign out',
  kind: 'change',
  tico: false,
  input: z.strictObject({}),
  // the desktop app hears first (its count and small call window go before the page leaves)
  run: async () => {
    await tellDesktopSigningOut();
    location.assign('/api/v2/logout');
  },
});

export const openSetup = defineAction({
  id: 'setup.open',
  title: 'Set up your company',
  kind: 'view',
  input: z.strictObject({ step: z.optional(z.enum(STEPS)) }),
  can: () => isOwner(),
  run: ({ router }, { step }) => toStep(router, step ?? 'path'),
  show: ({ router }, { step }) => toStep(router, step ?? 'path'),
  did: () => 'Opened setup',
  target: () => SETUP_TARGET,
});

export const closeSetup = defineAction({
  id: 'setup.close',
  title: 'Close setup',
  kind: 'view',
  input: z.strictObject({}),
  run: ({ router }) => toStep(router, null),
  did: () => 'Closed setup',
});

export const choosePath = defineAction({
  id: 'setup.choosePath',
  title: 'Choose who sets up the company',
  kind: 'change',
  input: z.strictObject({ path: z.enum(['agents', 'self'] as const) }),
  can: () => isOwner(),
  run: async ({ router, via }, { path }) => {
    if (path === 'agents') {
      // Tico choosing this is already doing the setup; it does not ask itself.
      if (via !== 'tico') {
        const asked = await run('tico.ask', { text: HAND_OFF }, { via: 'click' });
        if (!asked.ok) throw Object.assign(new Error(asked.error ?? 'not sent'), { code: 'not_sent' });
      }
      return toStep(router, null);
    }
    const [record, subs] = await Promise.all([
      queryClient.ensureQueryData(setupQuery),
      queryClient.ensureQueryData(subscriptionsQuery).catch(() => undefined),
    ]);
    return toStep(router, resumeStep(record, subs));
  },
  did: ({ path }) => (path === 'agents' ? 'Handed setup to Tico' : 'Setting up by hand'),
  target: ({ path }) => (path === 'agents' ? { kind: 'sheet', id: 'tico' } : SETUP_TARGET),
});

export const nextStep = defineAction({
  id: 'setup.next',
  title: 'Next setup step',
  kind: 'change',
  input: z.strictObject({ from: z.enum(['name', 'model'] as const), name: z.optional(z.string().check(z.minLength(1), z.maxLength(100))) }),
  can: () => isOwner(),
  run: async ({ router, queryClient }, { from, name }) => {
    if (from === 'name') {
      if (!name?.trim()) throw Object.assign(new Error('a name is needed'), { code: 'name_missing' });
      await saveCompanyName(name.trim());
      return toStep(router, 'model');
    }
    const providers = await queryClient.fetchQuery(subscriptionsQuery);
    if (!providers.subscriptions.some(providerReady)) throw Object.assign(new Error('Connect a provider first'), { code: 'provider_missing' });
    return toStep(router, 'team');
  },
  did: ({ from }) => (from === 'name' ? 'Named the company' : 'Next step'),
  target: () => SETUP_TARGET,
});

export const backStep = defineAction({
  id: 'setup.back',
  title: 'Previous setup step',
  kind: 'view',
  input: z.strictObject({ from: z.enum(STEPS) }),
  run: ({ router }, { from }) => toStep(router, stepBefore(from)),
  target: () => SETUP_TARGET,
});

export const pasteKey = defineAction({
  id: 'setup.pasteKey',
  title: 'Paste a model key',
  kind: 'change',
  tico: false,
  input: z.strictObject({ kind: z.enum(['anthropic_key', 'openai_key'] as const), secret: z.string().check(z.minLength(1), z.maxLength(20000)) }),
  run: async ({ router }, { kind, secret }) => {
    await saveKey(kind, secret.trim());
    return toStep(router, 'model');
  },
  target: () => SETUP_TARGET,
});

export const finish = defineAction({
  id: 'setup.finish',
  title: 'Create the team',
  kind: 'change',
  input: z.strictObject({ job: z.string().check(z.maxLength(40), z.regex(/^[a-z]*$/), z.describe('a first job key, or empty for the whole chart')) }),
  can: () => isOwner(),
  run: async ({ router }, { job }) => {
    await finishSetup(job);
    return router.navigate({ to: '/tasks' });
  },
  did: () => 'Created the team',
  target: () => ({ kind: 'page', id: '/tasks' }),
});

/* ---------------------------------------------------------------- GitHub: the App the agents' repositories live under */

const GITHUB_TARGET = { kind: 'section', id: 'github' } as const;

/** A refusal keeps the server's reason ("code: reason"), so the press can say it. */
async function withReason<T>(send: () => Promise<T>, onRefused?: (code: string) => void): Promise<T> {
  try {
    return await send();
  } catch (e) {
    const err = e as { status?: number; code?: string; message?: string };
    if (err.status === 401 || err.status === 403 || !err.code) throw e;
    onRefused?.(err.code);
    throw Object.assign(new Error(err.message), { status: err.status, code: `${err.code}: ${err.message ?? ''}` });
  }
}

const where = z.strictObject({
  org: z.optional(z.string().check(z.minLength(1), z.maxLength(39), z.regex(/^[A-Za-z0-9-]+$/), z.describe("an organization's GitHub login, or none for your own account"))),
  administration: z.boolean(),
});

/* Making the App is the owner's own click on GitHub's page: the browser goes there. */
export const githubConnect = defineAction({
  id: 'github.connect',
  title: 'Connect GitHub',
  kind: 'change',
  tico: false,
  input: where,
  can: () => isOwner(),
  run: async (_ctx, { org, administration }) => handOff(await withReason(() => githubSetup(org ?? '', administration))),
  target: () => GITHUB_TARGET,
});

/* The key that opens the saved App is lost: replacing it removes the App and the directory sync keys, so only the
   owner's own press after the question does it. Then the setup goes on as Connect does. */
export const githubReplaceKey = defineAction({
  id: 'github.replaceKey',
  title: 'Replace the lost GitHub key',
  kind: 'change',
  tico: false,
  input: where,
  can: () => isOwner(),
  run: async (_ctx, { org, administration }) => {
    await withReason(replaceGithubKey);
    handOff(await withReason(() => githubSetup(org ?? '', administration)));
  },
  target: () => GITHUB_TARGET,
});

/* GitHub came back with no session: the setup this tab started is finished here, and the browser goes on to install. */
export const githubFinish = defineAction({
  id: 'github.finish',
  title: 'Finish connecting GitHub',
  kind: 'change',
  tico: false,
  input: z.strictObject({}),
  can: () => isOwner(),
  run: async () => {
    const state = pendingSetup();
    if (!state) throw refused('github_state', 'Connect again');
    // A used, old or another person's state never works again.
    const done = await withReason(() => finishGithub(state), (code) => code === 'github_state' && forgetSetup());
    forgetSetup();
    goToGithub(done.install_url);
  },
  target: () => GITHUB_TARGET,
});

export const githubCheck = defineAction({
  id: 'github.checkPermissions',
  title: 'Check GitHub permissions again',
  kind: 'change',
  input: z.strictObject({}),
  can: () => isOwner(),
  run: () => withReason(checkGithub),
  did: () => 'Checked GitHub',
  target: () => GITHUB_TARGET,
});

/* A personal account: the owner lets Tico create repositories there, on GitHub's page. */
export const githubAuthorize = defineAction({
  id: 'github.authorize',
  title: 'Authorize Tico on GitHub',
  kind: 'change',
  tico: false,
  input: z.strictObject({}),
  can: () => isOwner(),
  run: async () => goToGithub((await withReason(githubAuthorizeLink)).url),
  target: () => GITHUB_TARGET,
});

export const githubDisconnect = defineAction({
  id: 'github.disconnect',
  title: 'Disconnect GitHub',
  kind: 'change',
  tico: false,
  input: z.strictObject({}),
  can: () => isOwner(),
  run: async () => {
    await withReason(disconnectGithub);
  },
  did: () => 'Disconnected GitHub',
  target: () => GITHUB_TARGET,
});
