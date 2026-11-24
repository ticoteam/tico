import * as z from 'zod/mini';
import { defineAction, run } from '@/actions';
import { configQuery, meQuery, queryClient } from '@/data';
import { tellDesktopSigningOut } from '@/desktop';
import { forgetSetup, goToGithub, handOff, pendingSetup, refused } from './handoff';
import {
  checkGithub,
  disconnectGithub,
  finishGithub,
  finishSetup,
  githubAuthorizeLink,
  githubSetup,
  inviteFromFile,
  invitees,
  needsSetup,
  replaceGithubKey,
  saveKey,
  setupQuery,
} from './queries';

const isOwner = () => queryClient.getQueryData(meQuery.queryKey)?.role === 'owner';
const TICO_SHEET = { kind: 'sheet', id: 'tico' } as const;

// What Tico hears when it takes the setup over: it runs its setup playbook in its own chat
// (templates/catalog/assistant/skills/set-up-the-company).
export const HAND_OFF = 'Set up my company.';

async function askTico(): Promise<boolean> {
  return (await run('tico.ask', { text: HAND_OFF }, { via: 'click' })).ok;
}

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

/* The first run lives in Tico's sheet (SetupChat): while it is owed this opens the sheet on it. Once the company is
   live, "Set up" hands the rest to Tico, which asks in its own chat. */
export const openSetup = defineAction({
  id: 'setup.open',
  title: 'Set up your company',
  kind: 'change',
  input: z.strictObject({}),
  can: () => isOwner(),
  run: async ({ via }) => {
    const config = await queryClient.ensureQueryData(configQuery);
    if (needsSetup(config)) {
      await run('shell.fold', { sheet: 'tico', folded: false }, { via });
      return;
    }
    // Tico choosing this is already doing the setup; it does not ask itself.
    if (via !== 'tico' && !(await askTico())) throw Object.assign(new Error('not sent'), { code: 'not_sent' });
  },
  did: () => 'Opened setup',
  target: () => TICO_SHEET,
});

export const pasteKey = defineAction({
  id: 'setup.pasteKey',
  title: 'Paste a model key',
  kind: 'change',
  tico: false,
  input: z.strictObject({ kind: z.enum(['anthropic_key', 'openai_key'] as const), secret: z.string().check(z.minLength(1), z.maxLength(20000)) }),
  run: (_ctx, { kind, secret }) => saveKey(kind, secret.trim()),
  target: () => TICO_SHEET,
});

/* Inviting someone sends mail outside the company: the owner's own press on Tico's card, never Tico's. */
export const invite = defineAction({
  id: 'setup.invite',
  title: 'Invite the people from the setup file',
  kind: 'change',
  tico: false,
  input: z.strictObject({}),
  can: () => isOwner(),
  run: async () => {
    for (const person of invitees(await queryClient.fetchQuery(setupQuery))) await inviteFromFile(person);
  },
  did: () => 'Invited them',
  target: () => TICO_SHEET,
});

/* The owner's finish, from their press in the first run. A company a setup file made goes live and lands on Tasks;
   one set up here gets its starting team, and Tico takes the setup over in its chat. Answers which: 'live',
   'handed', or 'tico_off' when Tico could not be asked (its sheet then says why). */
export const finish = defineAction({
  id: 'setup.finish',
  title: 'Go live',
  kind: 'change',
  tico: false,
  input: z.strictObject({}),
  can: () => isOwner(),
  run: async ({ router }) => {
    const { fromFile } = await finishSetup();
    if (fromFile) {
      await router.navigate({ to: '/tasks' });
      return 'live';
    }
    return (await askTico()) ? 'handed' : 'tico_off';
  },
  did: () => 'Went live',
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
