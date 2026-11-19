import type { AnyRouter } from '@tanstack/react-router';
import { defineAction, s, type Schema } from '@/actions';
import {
  addAccount,
  addApiTool,
  addTicoAddress,
  classifyPaste,
  connectMailbox,
  editApiTool,
  editInbox,
  editSource,
  editWebsite,
  orderAddress,
  pasteKeys,
  releaseInbox,
  removeSetting,
  requestNumber,
  revealSecret,
  saveSetting,
  sendTestEvent,
  setKeyExpiry,
  setWebhookOn,
  signInCode,
  addKey,
  addMcp,
  addNote,
  installSkill,
  importDoc,
  archiveDoc,
  addSource,
  addWebsite,
  approveProposal,
  cancelLetter,
  checkTool,
  createDoc,
  decideDevice,
  declineProposal,
  docsListQuery,
  probeMcp,
  refreshSource,
  removeKey,
  replaceKey,
  replaceLogin,
  restoreDoc,
  saveDoc,
  saveFact,
  setAccess,
  skillsQuery,
  startSignIn,
  toolsQuery,
  uploadFile,
  type DocRow,
  type SkillRow,
} from './queries';
import type { ToolIndex } from '@/api/schema';
import { viewing } from './viewing';

/* Every view and change on the Library pages: Docs, a doc, Files, Skills, Tools, Contacts. Anything that sets a secret,
   adds a file from the person's own disk or answers an approval is the person's own (`tico: false`). */

const id = s.string({ min: 1, max: 200 });
const text = (max: number) => s.string({ max });

/** Go to a Library page, keeping only the search words given (an empty one is dropped). */
const go = (router: AnyRouter, to: string, search: Record<string, string | number | undefined> = {}, replace = false) =>
  router.navigate({
    to,
    search: Object.fromEntries(Object.entries(search).filter(([, v]) => v !== undefined && v !== '')),
    replace,
  });

/** On the page now: change these words in its address, keep the rest. */
const set = (router: AnyRouter, change: Record<string, string | number | undefined>, replace = false) =>
  router.navigate({
    to: '.',
    replace,
    search: (prev: Record<string, unknown>) => {
      const next: Record<string, unknown> = { ...prev, ...change };
      for (const k of Object.keys(next)) if (next[k] === undefined || next[k] === '') delete next[k];
      return next;
    },
  });

/** Whether the person is on this Library page now (the app may sit under /next). */
const on = (router: AnyRouter, to: string) => router.state.location.pathname.replace(/^\/next(?=\/|$)/, '') === to;

/** A file from the person's disk: only a click or a drop has one. */
const file: Schema<File> = {
  parse: (v, p) => (typeof File !== 'undefined' && v instanceof File ? { ok: true, value: v } : { ok: false, error: `${p || 'input'}: expected a file` }),
  json: () => ({ type: 'object', description: 'A file the person chose' }),
  optional: false,
  '~standard': {
    version: 1,
    vendor: 'tico',
    validate: (v) => (typeof File !== 'undefined' && v instanceof File ? { value: v } : { issues: [{ message: 'expected a file' }] }),
  },
};

// ---------------------------------------------------------------- docs

export const docsOpen = defineAction({
  id: 'docs.open',
  title: 'Open a doc',
  kind: 'view',
  input: s.object({ doc: id, edit: s.optional(s.boolean()), version: s.optional(s.number({ int: true, min: 1 })) }),
  run: ({ router }, i) => go(router, `/library/${encodeURIComponent(i.doc)}`, { edit: i.edit ? 1 : undefined, v: i.version }),
  show: ({ router }, i) => go(router, `/library/${encodeURIComponent(i.doc)}`),
  target: (i) => ({ kind: 'page', id: `/library/${i.doc}` }),
  offer: ({ queryClient }) =>
    (queryClient.getQueryData<{ docs: DocRow[] }>(docsListQuery.queryKey)?.docs ?? []).slice(0, 50).map((d) => ({ input: { doc: d.id }, title: `Open ${d.title}` })),
});

export const docsList = defineAction({
  id: 'docs.list',
  title: 'Open Docs',
  kind: 'view',
  input: s.object({
    tab: s.optional(s.oneOf(['docs', 'sources'] as const)),
    q: s.optional(text(200)),
    source: s.optional(id),
    archived: s.optional(s.boolean()),
  }),
  run: ({ router }, i) =>
    i.tab === 'sources' || i.source
      ? go(router, '/library/docs', { tab: 'sources', panel: i.source })
      : go(router, '/library/docs', { q: i.q, archived: i.archived ? 1 : undefined }, true),
  target: (i) => ({ kind: 'page', id: i.tab === 'sources' || i.source ? '/library/docs?tab=sources' : '/library/docs' }),
});

export const docsNew = defineAction({
  id: 'docs.new',
  title: 'Start a new doc',
  kind: 'view',
  input: s.object({}),
  run: ({ router }) => go(router, '/library/new'),
  target: () => ({ kind: 'page', id: '/library/new' }),
});

export const docsCreate = defineAction({
  id: 'docs.create',
  title: 'Make a doc',
  kind: 'change',
  input: s.object({ title: s.string({ min: 1, max: 300 }), body: text(200_000), path: s.optional(text(300)) }),
  run: async ({ router }, i) => {
    const r = await createDoc({ title: i.title.trim(), body: i.body, path: i.path?.trim() || undefined });
    await go(router, `/library/${encodeURIComponent(r.doc.id)}`, {}, true);
    return r.doc.id;
  },
  did: (i) => `Made ${i.title}`,
  target: (_i, out) => (out ? { kind: 'page', id: `/library/${out}` } : null),
});

export const docsSave = defineAction({
  id: 'docs.save',
  title: 'Save a doc',
  kind: 'change',
  input: s.object({ doc: id, version: s.number({ int: true, min: 1 }), title: s.optional(s.string({ min: 1, max: 300 })), body: s.optional(text(200_000)) }),
  run: async ({ router }, i) => {
    await saveDoc(i.doc, { version: i.version, title: i.title, body: i.body });
    await set(router, { edit: undefined }, true);
  },
  did: () => 'Saved',
  target: (i) => ({ kind: 'page', id: `/library/${i.doc}` }),
});

export const docsRestore = defineAction({
  id: 'docs.restore',
  title: 'Bring back an old version of a doc',
  kind: 'change',
  input: s.object({ doc: id, version: s.number({ int: true, min: 1 }) }),
  run: async ({ router }, i) => {
    await restoreDoc(i.doc, i.version);
    await set(router, { v: undefined }, true);
  },
  did: (i) => `Brought back version ${i.version}`,
  target: (i) => ({ kind: 'page', id: `/library/${i.doc}` }),
});

/** Archive a doc (it leaves the list and search), or bring an archived one back. */
export const docsArchive = defineAction({
  id: 'docs.archive',
  title: 'Archive a doc, or bring one back',
  kind: 'change',
  input: s.object({ doc: id, version: s.number({ int: true, min: 1 }), archived: s.boolean() }),
  run: async ({ router }, i) => {
    await archiveDoc(i.doc, i.version, i.archived);
    if (i.archived && on(router, `/library/${i.doc}`)) await go(router, '/library/docs', {}, true);
  },
  did: (i) => (i.archived ? 'Archived' : 'Restored'),
  target: () => ({ kind: 'page', id: '/library/docs' }),
});

/** A file the person chose becomes a new doc, and opens. */
export const docsImport = defineAction({
  id: 'docs.import',
  title: 'Import a file as a doc',
  kind: 'change',
  // the file is one a person picks on their computer: there is nothing for Tico to hand over
  tico: false,
  input: s.object({ file, title: s.optional(s.string({ max: 300 })), path: s.optional(s.string({ max: 300 })) }),
  run: async ({ router }, i) => {
    const doc = await importDoc(i.file, { title: i.title, path: i.path });
    await go(router, `/library/${encodeURIComponent(doc.id)}`);
  },
  did: (i) => `Imported ${i.file.name}`,
  target: () => ({ kind: 'page', id: '/library/docs' }),
});

export const docsRefreshSource = defineAction({
  id: 'docs.refreshSource',
  title: 'Read an outside source again',
  kind: 'change',
  input: s.object({ source: id }),
  run: (_ctx, i) => refreshSource(i.source),
  did: () => 'Reading it again',
  target: (i) => ({ kind: 'source', id: i.source }),
});

const sourceBody = {
  url: s.optional(s.string({ min: 1, max: 2000, pattern: /^https?:\/\// })),
  title: s.optional(s.string({ max: 300 })),
  description: s.optional(s.string({ max: 300 })),
  reference: s.optional(s.string({ max: 256 })),
};

export const docsAddSource = defineAction({
  id: 'docs.addSource',
  title: 'Link an outside source of docs',
  kind: 'change',
  input: s.object({ ...sourceBody, url: s.string({ min: 1, max: 2000, pattern: /^https?:\/\// }) }),
  run: async ({ router }, i) => {
    const made = await addSource(i);
    await go(router, '/library/docs', { tab: 'sources', panel: made.id });
  },
  did: (i) => `Linked ${i.title || i.url}`,
  target: () => ({ kind: 'page', id: '/library/docs?tab=sources' }),
});

export const docsEditSource = defineAction({
  id: 'docs.editSource',
  title: 'Change or remove an outside source',
  kind: 'change',
  input: s.object({ source: id, ...sourceBody, archived: s.optional(s.boolean()) }),
  run: async ({ router }, { source, ...change }) => {
    await editSource(source, change);
    if (change.archived) await go(router, '/library/docs', { tab: 'sources' });
  },
  did: (i) => (i.archived ? 'Removed the source' : 'Saved'),
  target: (i) => ({ kind: 'source', id: i.source }),
});

// ---------------------------------------------------------------- files

export const filesList = defineAction({
  id: 'files.list',
  title: 'Open Files',
  kind: 'view',
  input: s.object({ kind: s.optional(text(40)), q: s.optional(text(200)) }),
  run: ({ router }, i) => go(router, '/library/files', { kind: i.kind, q: i.q }, true),
  target: () => ({ kind: 'page', id: '/library/files' }),
});

export const filesOpen = defineAction({
  id: 'files.open',
  title: 'Open a file',
  kind: 'view',
  input: s.object({ file: id, version: s.optional(s.number({ int: true, min: 1 })) }),
  // On Files it keeps the page's filters; from anywhere else it goes to the file.
  run: ({ router }, i) => {
    const change = { file: i.file, v: i.version };
    return on(router, '/library/files') ? set(router, change) : go(router, '/library/files', change);
  },
  target: (i) => ({ kind: 'file', id: i.file }),
});

/** A file over whatever page is open (a task, a chat): a picture full size, anything else in a dialog. */
export const filesView = defineAction({
  id: 'files.view',
  title: 'Show a file',
  kind: 'view',
  input: s.object({ file: s.nullable(id), version: s.optional(s.number({ int: true, min: 1 })) }),
  run: (_ctx, i) => viewing.set(i.file ? { file: i.file, version: i.version } : null),
  target: () => null,
});

export const filesUpload = defineAction({
  id: 'files.upload',
  title: 'Add a file to a task',
  kind: 'change',
  tico: false,
  input: s.object({ task: id, file }),
  run: (_ctx, i) => uploadFile(i.task, i.file),
  did: (i) => `Added ${i.file.name}`,
  target: () => ({ kind: 'page', id: '/library/files' }),
});

// ---------------------------------------------------------------- skills

export const skillsList = defineAction({
  id: 'skills.list',
  title: 'Open Skills',
  kind: 'view',
  input: s.object({ q: s.optional(text(200)) }),
  run: ({ router }, i) => go(router, '/library/skills', { q: i.q }, true),
  target: () => ({ kind: 'page', id: '/library/skills' }),
});

export const skillsOpen = defineAction({
  id: 'skills.open',
  title: 'Open a skill',
  kind: 'view',
  input: s.object({ skill: id }),
  run: ({ router }, i) => go(router, '/library/skills', { skill: i.skill }),
  target: (i) => ({ kind: 'skill', id: i.skill }),
  offer: ({ queryClient }) =>
    (queryClient.getQueryData<SkillRow[]>(skillsQuery.queryKey) ?? []).slice(0, 50).map((k) => ({ input: { skill: k.name }, title: `Open ${k.name}` })),
});

export const skillsOpenProposal = defineAction({
  id: 'skills.openProposal',
  title: 'Open a proposed skill change',
  kind: 'view',
  input: s.object({ proposal: id }),
  run: ({ router }, i) => go(router, '/library/skills', { proposal: i.proposal }),
  target: (i) => ({ kind: 'proposal', id: i.proposal }),
});

/** The setup steps, or (with the code the helper shows) approving that computer. */
export const skillsConnect = defineAction({
  id: 'skills.connect',
  title: 'Connect Claude Code to skills',
  kind: 'view',
  input: s.object({ code: s.optional(s.string({ min: 1, max: 40 })) }),
  run: ({ router }, i) => set(router, { connect: i.code ?? 'setup' }),
  target: () => ({ kind: 'skills', id: 'connect' }),
});

export const skillsDecideDevice = defineAction({
  id: 'skills.decideDevice',
  title: 'Approve or deny Claude Code on a computer',
  kind: 'change',
  tico: false,
  input: s.object({ code: s.string({ min: 1, max: 40 }), approve: s.boolean(), share: s.optional(s.boolean()) }),
  run: (_ctx, i) => decideDevice(i.code, i.approve, !!i.share),
  did: (i) => (i.approve ? 'Connected' : 'Denied'),
  target: () => ({ kind: 'skills', id: 'connect' }),
});

export const skillsApprove = defineAction({
  id: 'skills.approve',
  title: 'Approve a skill change',
  kind: 'change',
  tico: false,
  input: s.object({ proposal: id, acknowledged: s.optional(s.boolean()) }),
  run: async ({ router }, i) => {
    await approveProposal(i.proposal, i.acknowledged);
    await go(router, '/library/skills', {}, true);
  },
  did: () => 'Approved',
  target: () => ({ kind: 'page', id: '/library/skills' }),
});

/** An approved skill for one teammate or a team's members; the latest version unless one is pinned. */
export const skillsInstall = defineAction({
  id: 'skills.install',
  title: 'Install a skill',
  kind: 'change',
  input: s.object({
    skill: s.string({ min: 1, max: 120 }),
    targets: s.array(s.object({ target_type: s.oneOf(['Bot', 'Human'] as const), target_id: id }), { max: 200 }),
    version: s.optional(s.number({ int: true, min: 1 })),
  }),
  run: (_ctx, i) => (i.targets.length ? installSkill(i.skill, i.targets, i.version) : Promise.reject(new Error('no_targets'))),
  did: (i) => `Installed ${i.skill}${i.targets.length > 1 ? ` for ${i.targets.length}` : ''}`,
  target: (i) => ({ kind: 'page', id: `/library/skills?skill=${i.skill}` }),
});

export const skillsDecline = defineAction({
  id: 'skills.decline',
  title: 'Send back a skill change',
  kind: 'change',
  tico: false,
  input: s.object({ proposal: id, reason: s.string({ min: 1, max: 300 }) }),
  run: async ({ router }, i) => {
    await declineProposal(i.proposal, i.reason.trim());
    await go(router, '/library/skills', {}, true);
  },
  did: () => 'Sent back',
  target: () => ({ kind: 'page', id: '/library/skills' }),
});

// ---------------------------------------------------------------- tools

export const toolsOpen = defineAction({
  id: 'tools.open',
  title: 'Open a tool',
  kind: 'view',
  input: s.object({ tool: s.optional(id), q: s.optional(text(200)) }),
  run: ({ router }, i) => go(router, '/library/tools', { tool: i.tool, q: i.q }, !i.tool),
  target: (i) => (i.tool ? { kind: 'tool', id: i.tool } : { kind: 'page', id: '/library/tools' }),
  offer: ({ queryClient }) =>
    (queryClient.getQueryData<ToolIndex>(toolsQuery.queryKey)?.rows ?? []).slice(0, 50).map((t) => ({ input: { tool: t.id }, title: `Open ${t.name}` })),
});

/** Narrow the list to one kind of tool, or to the tools that need someone; nothing given shows them all. */
export const toolsFilter = defineAction({
  id: 'tools.filter',
  title: 'Filter tools',
  kind: 'view',
  input: s.object({ kind: s.optional(text(40)), needs: s.optional(s.boolean()) }),
  run: ({ router }, i) => {
    const change = { kind: i.kind, needs: i.needs ? 1 : undefined };
    return on(router, '/library/tools') ? set(router, change, true) : go(router, '/library/tools', change);
  },
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

/** One tap: the provider's sign-in page, which comes back to the tool. */
export const toolsConnect = defineAction({
  id: 'tools.connect',
  title: 'Connect a tool',
  kind: 'change',
  tico: false,
  input: s.object({ tool: id }),
  run: async (_ctx, i) => {
    const r = await startSignIn(i.tool);
    window.location.assign(r.authorize_url);
  },
  target: (i) => ({ kind: 'tool', id: i.tool }),
});

/** GitHub's install page for the App; the server sends the browser there and GitHub brings it back to Tools. */
export const toolsInstallGithub = defineAction({
  id: 'tools.installGithub',
  title: 'Install the GitHub App',
  kind: 'change',
  tico: false,
  input: s.object({}),
  run: () => window.location.assign('/api/v2/github/app/install'),
  target: () => ({ kind: 'tool', id: 'github' }),
});

/** Add a key, or replace one. The value goes to the server once and is never shown again. */
export const toolsSetKey = defineAction({
  id: 'tools.setKey',
  title: 'Add or replace a key',
  kind: 'change',
  tico: false,
  input: s.object({ tool: id, key: s.optional(id), name: s.optional(text(120)), value: s.string({ min: 1, max: 20_000 }) }),
  run: (_ctx, i) => (i.key ? replaceKey(i.tool, i.key, i.value) : addKey(i.tool, i.name?.trim() || 'Key', i.value)),
  did: (i) => (i.key ? 'Replaced the key' : 'Added a key'),
  target: (i) => ({ kind: 'tool-keys', id: i.tool }),
});

export const toolsRemoveKey = defineAction({
  id: 'tools.removeKey',
  title: 'Remove a key',
  kind: 'change',
  input: s.object({ tool: id, key: id }),
  run: (_ctx, i) => removeKey(i.tool, i.key),
  did: () => 'Removed the key',
  target: (i) => ({ kind: 'tool-keys', id: i.tool }),
});

export const toolsCheck = defineAction({
  id: 'tools.check',
  title: 'Check a tool works',
  kind: 'change',
  input: s.object({ tool: id }),
  run: (_ctx, i) => checkTool(i.tool),
  did: () => 'Checked',
  target: (i) => ({ kind: 'tool-connect', id: i.tool }),
});

export const toolsSetAccess = defineAction({
  id: 'tools.setAccess',
  title: 'Change who may use a tool',
  kind: 'change',
  input: s.object({ tool: id, subject: id, level: s.oneOf(['read', 'read_write', 'none'] as const) }),
  run: (_ctx, i) => setAccess(i.tool, i.subject, i.level),
  did: (i) => (i.level === 'none' ? 'Removed' : 'Changed'),
  target: (i) => ({ kind: 'tool-who', id: i.tool }),
});

/* Adding a tool. Tico may add one when nothing secret is typed (an API's address, an MCP server it signs in to); a
   token, a client secret or a password is the person's own. Each add opens the new tool. */

const opened = (router: AnyRouter, tool: string) => go(router, '/library/tools', { tool });

export const toolsAddApi = defineAction({
  id: 'tools.addApi',
  title: 'Add an API',
  kind: 'change',
  input: s.object({
    name: s.string({ min: 1, max: 120 }),
    service: s.optional(text(80)),
    base_url: s.optional(text(500)),
    auth_scheme: s.string({ min: 1, max: 80, pattern: /^(bearer|basic_user|basic_password|(header|query):[\w-]+)$/ }),
    check_path: s.optional(text(500)),
  }),
  run: async ({ router }, i) => opened(router, `api:${(await addApiTool(i)).id}`),
  did: (i) => `Added ${i.name}`,
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

/** An MCP server with its own sign-in: Tico may add it; signing in is the person's (`tools.connect`). */
export const toolsAddMcp = defineAction({
  id: 'tools.addMcp',
  title: 'Add an MCP server',
  kind: 'change',
  input: s.object({ name: s.string({ min: 1, max: 80 }), url: s.string({ min: 1, max: 500 }), client_id: s.optional(text(500)) }),
  run: async ({ router }, i) => {
    const f = await probeMcp(i.url);
    if (!f.oauth) throw new Error('This server takes a token');
    if (!f.dcr && !i.client_id) throw new Error('This server needs a client id');
    await opened(router, `api:${(await addMcp({ ...i, auth: 'oauth' })).id}`);
  },
  did: (i) => `Added ${i.name}`,
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

/** An MCP server with a token or a client secret the person types. Sign-in goes on to the provider's page. */
export const toolsAddMcpWithKey = defineAction({
  id: 'tools.addMcpWithKey',
  title: 'Add an MCP server with a key',
  kind: 'change',
  tico: false,
  input: s.object({
    name: s.string({ min: 1, max: 80 }),
    url: s.string({ min: 1, max: 500 }),
    auth: s.oneOf(['token', 'oauth'] as const),
    token: s.optional(s.string({ max: 100_000 })),
    header: s.optional(text(60)),
    client_id: s.optional(text(500)),
    client_secret: s.optional(s.string({ max: 2000 })),
  }),
  run: async ({ router }, i) => {
    const r = await addMcp(i);
    if (i.auth === 'oauth') window.location.assign((await startSignIn(r.id)).authorize_url);
    else await opened(router, `api:${r.id}`);
  },
  did: (i) => `Added ${i.name}`,
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

const login = {
  username: s.optional(text(250)),
  password: s.optional(s.string({ max: 2000 })),
  totp: s.optional(s.string({ max: 2000 })),
  holder: s.optional(id),
  all_bots: s.optional(s.boolean()),
};

/** Only the site: Tico may add it. A login on it is the person's. */
export const toolsAddWebsite = defineAction({
  id: 'tools.addWebsite',
  title: 'Add a website',
  kind: 'change',
  input: s.object({ site: s.string({ min: 1, max: 300 }), central_only: s.optional(s.boolean()) }),
  run: async ({ router }, i) => opened(router, `website:${(await addWebsite(i.site, i.central_only ?? false)).id}`),
  did: (i) => `Added ${i.site}`,
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

export const toolsAddWebsiteWithLogin = defineAction({
  id: 'tools.addWebsiteWithLogin',
  title: 'Add a website with a login',
  kind: 'change',
  tico: false,
  input: s.object({ site: s.string({ min: 1, max: 300 }), central_only: s.optional(s.boolean()), ...login }),
  run: async ({ router }, { site, central_only, ...l }) => {
    const tool = (await addWebsite(site, central_only ?? false)).id;
    // A refused login keeps the site: adding the account again is all that is left.
    await opened(router, `website:${tool}`);
    await addAccount(tool, l);
  },
  did: (i) => `Added ${i.site}`,
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

export const toolsAddAccount = defineAction({
  id: 'tools.addAccount',
  title: 'Add an account to a website',
  kind: 'change',
  tico: false,
  input: s.object({ tool: id, ...login }),
  run: async (_ctx, { tool, ...l }) => {
    await addAccount(tool, l);
  },
  did: () => 'Added the account',
  target: (i) => ({ kind: 'tool', id: `website:${i.tool}` }),
});

/** Only the fields typed are sent. A stored password or authenticator key is never read back. */
export const toolsReplaceLogin = defineAction({
  id: 'tools.replaceLogin',
  title: 'Replace a website login',
  kind: 'change',
  tico: false,
  input: s.object({
    tool: id,
    account: id,
    username: s.optional(text(250)),
    password: s.optional(s.string({ min: 1, max: 2000 })),
    totp: s.optional(s.string({ min: 1, max: 2000 })),
    remove_totp: s.optional(s.boolean()),
  }),
  run: async (_ctx, { tool, account, remove_totp, ...change }) => {
    await replaceLogin(tool, account, remove_totp ? { ...change, totp: null } : change);
  },
  did: () => 'Replaced the login',
  target: (i) => ({ kind: 'tool', id: `website:${i.tool}` }),
});

export const toolsCancelLetter = defineAction({
  id: 'tools.cancelLetter',
  title: 'Stop a letter before it is mailed',
  kind: 'change',
  input: s.object({ letter: id }),
  run: (_ctx, i) => cancelLetter(i.letter),
  did: () => 'Stopped the letter',
  target: (i) => ({ kind: 'letter', id: i.letter }),
});

/* Changing a tool. Tico may rename it, move its address, set a key's end date or a run setting: nothing secret is
   typed. Reading a stored secret, a sign-in code, a paste of keys, or anything paid or for good is the person's own. */

const api = (tool: string) => ({ kind: 'tool', id: `api:${tool}` });
const site = (tool: string) => ({ kind: 'tool', id: `website:${tool}` });
const day = s.string({ max: 10, pattern: /^$|^\d{4}-\d{2}-\d{2}$/ });

export const toolsEditApi = defineAction({
  id: 'tools.editApi',
  title: 'Change an API tool',
  kind: 'change',
  input: s.object({
    tool: id,
    name: s.optional(s.string({ min: 1, max: 120 })),
    service: s.optional(text(80)),
    base_url: s.optional(text(500)),
    auth_scheme: s.optional(s.string({ min: 1, max: 80, pattern: /^(bearer|basic_user|basic_password|(header|query):[\w-]+)$/ })),
    check_path: s.optional(text(500)),
  }),
  run: (_ctx, { tool, ...change }) => editApiTool(tool, change),
  did: () => 'Saved',
  target: (i) => api(i.tool),
});

export const toolsSetKeyExpiry = defineAction({
  id: 'tools.setKeyExpiry',
  title: 'Set when a key ends',
  kind: 'change',
  input: s.object({ tool: id, key: id, expires_on: day }),
  run: (_ctx, i) => setKeyExpiry(i.tool, i.key, i.expires_on),
  did: (i) => (i.expires_on ? `Ends ${i.expires_on}` : 'No end date'),
  target: (i) => ({ kind: 'tool-keys', id: i.tool }),
});

export const toolsSaveSetting = defineAction({
  id: 'tools.saveSetting',
  title: 'Add or change a run setting',
  kind: 'change',
  input: s.object({ tool: id, setting: s.optional(id), name: s.string({ min: 1, max: 80, pattern: /^[A-Z_][A-Z0-9_]*$/ }), value: s.string({ min: 1, max: 2000 }) }),
  run: (_ctx, i) => saveSetting(i.tool, { id: i.setting, name: i.name, value: i.value }),
  did: (i) => `Saved ${i.name}`,
  target: (i) => ({ kind: 'tool-about', id: i.tool }),
});

export const toolsRemoveSetting = defineAction({
  id: 'tools.removeSetting',
  title: 'Remove a run setting',
  kind: 'change',
  input: s.object({ tool: id, setting: id }),
  run: (_ctx, i) => removeSetting(i.tool, i.setting),
  did: () => 'Removed',
  target: (i) => ({ kind: 'tool-about', id: i.tool }),
});

/** A stored key or website password, shown to the person who asked. The value is the result's `out` only. */
export const toolsReveal = defineAction({
  id: 'tools.reveal',
  title: 'Show a stored key or password',
  kind: 'change',
  tico: false,
  input: s.object({ credential: id }),
  run: (_ctx, i) => revealSecret(i.credential),
  did: () => 'Shown',
  target: () => null,
});

/** Look at a paste first: what it holds, by name, with nothing stored. */
export const toolsCheckPaste = defineAction({
  id: 'tools.checkPaste',
  title: 'See what a paste of keys holds',
  kind: 'view',
  tico: false,
  input: s.object({ text: s.string({ min: 1, max: 100_000 }) }),
  run: (_ctx, i) => classifyPaste(i.text),
  target: () => null,
});

export const toolsPasteKeys = defineAction({
  id: 'tools.pasteKeys',
  title: 'Add many keys at once',
  kind: 'change',
  tico: false,
  input: s.object({ tool: id, text: s.string({ min: 1, max: 100_000 }) }),
  run: (_ctx, i) => pasteKeys(i.tool, i.text),
  did: (_i, out) => (out ? `Added ${out.keys} ${out.keys === 1 ? 'key' : 'keys'} and ${out.settings} ${out.settings === 1 ? 'setting' : 'settings'}` : 'Added'),
  target: (i) => ({ kind: 'tool-keys', id: i.tool }),
});

export const toolsEditWebsite = defineAction({
  id: 'tools.editWebsite',
  title: 'Rename a website tool or change its sites',
  kind: 'change',
  input: s.object({
    tool: id,
    name: s.optional(s.string({ min: 1, max: 80 })),
    sites: s.optional(s.array(s.string({ min: 1, max: 300 }), { max: 50 })),
    central_only: s.optional(s.boolean()),
  }),
  run: (_ctx, { tool, ...change }) => editWebsite(tool, change),
  did: () => 'Saved',
  target: (i) => site(i.tool),
});

/** The sign-in code the account's authenticator shows now: the person's own, like a password. */
export const toolsSignInCode = defineAction({
  id: 'tools.signInCode',
  title: 'Show a sign-in code',
  kind: 'change',
  tico: false,
  input: s.object({ tool: id, account: id }),
  run: (_ctx, i) => signInCode(i.tool, i.account),
  did: () => 'Shown',
  target: () => null,
});

export const toolsAddTicoAddress = defineAction({
  id: 'tools.addTicoAddress',
  title: 'Make a Tico address',
  kind: 'change',
  input: s.object({ name: s.string({ min: 1, max: 80 }) }),
  run: async ({ router }, i) => {
    const made = await addTicoAddress(i.name.trim());
    if (made) await opened(router, `tico:${made}`);
  },
  did: (i) => `Made ${i.name}`,
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

/** A monthly number has a price: asking for one is the person's own. */
export const toolsRequestNumber = defineAction({
  id: 'tools.requestNumber',
  title: 'Ask for a US mobile number',
  kind: 'change',
  tico: false,
  input: s.object({ name: s.string({ min: 1, max: 80 }) }),
  run: (_ctx, i) => requestNumber(i.name.trim()),
  did: () => 'Asked for a number',
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

export const toolsEditInbox = defineAction({
  id: 'tools.editInbox',
  title: 'Rename an address or number',
  kind: 'change',
  input: s.object({ inbox: id, name: s.string({ min: 1, max: 80 }), used_for: s.optional(s.nullable(text(1000))) }),
  run: (_ctx, i) => editInbox(i.inbox, { name: i.name.trim(), used_for: i.used_for === undefined ? undefined : i.used_for?.trim() || null }),
  did: () => 'Saved',
  target: (i) => ({ kind: 'tool', id: `tico:${i.inbox}` }),
});

/** For good: an address is retired, a number is not renewed. */
export const toolsReleaseInbox = defineAction({
  id: 'tools.releaseInbox',
  title: 'Stop using an address or number',
  kind: 'change',
  tico: false,
  input: s.object({ inbox: id, confirm: s.optional(text(160)) }),
  run: async ({ router }, i) => {
    await releaseInbox(i.inbox, i.confirm ?? '');
    await go(router, '/library/tools', {}, true);
  },
  did: () => 'Stopped',
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

/** Gives Tico a company mailbox: the server takes it only from a person signed in as themselves. */
export const toolsConnectMailbox = defineAction({
  id: 'tools.connectMailbox',
  title: 'Connect a mailbox',
  kind: 'change',
  tico: false,
  input: s.object({ address: s.string({ min: 3, max: 320 }), shared: s.boolean() }),
  run: async ({ router }, i) => opened(router, `mailbox:${(await connectMailbox(i.address.trim().toLowerCase(), i.shared)).id}`),
  did: (i) => `Connected ${i.address}`,
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

/** Paid monthly: the person's own. */
export const toolsOrderAddress = defineAction({
  id: 'tools.orderAddress',
  title: 'Get a mailing address',
  kind: 'change',
  tico: false,
  input: s.object({ kind: s.oneOf(['shared', 'private'] as const), state: s.oneOf(['DE', 'CA', 'TX', 'FL', 'NV'] as const), company_name: s.string({ min: 1, max: 200 }) }),
  run: (_ctx, i) => orderAddress({ ...i, company_name: i.company_name.trim() }),
  did: () => 'Asked for a mailing address',
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

export const toolsPauseWebhook = defineAction({
  id: 'tools.pauseWebhook',
  title: 'Pause or restart an outgoing webhook',
  kind: 'change',
  input: s.object({ webhook: id, on: s.boolean() }),
  run: (_ctx, i) => setWebhookOn(i.webhook, i.on),
  did: (i) => (i.on ? 'Sending again' : 'Paused'),
  target: (i) => ({ kind: 'tool', id: `webhook:${i.webhook}` }),
});

export const toolsTestWebhook = defineAction({
  id: 'tools.testWebhook',
  title: 'Send a test to an outgoing webhook',
  kind: 'change',
  input: s.object({ webhook: id }),
  run: (_ctx, i) => sendTestEvent(i.webhook),
  did: () => 'Sent a test',
  target: (i) => ({ kind: 'tool', id: `webhook:${i.webhook}` }),
});

// ---------------------------------------------------------------- contacts

export const contactsList = defineAction({
  id: 'contacts.list',
  title: 'Open Contacts',
  kind: 'view',
  input: s.object({ type: s.optional(text(60)), q: s.optional(text(200)) }),
  run: ({ router }, i) => go(router, '/library/contacts', { type: i.type, q: i.q }, true),
  target: () => ({ kind: 'page', id: '/library/contacts' }),
});

export const contactsOpen = defineAction({
  id: 'contacts.open',
  title: 'Open a contact',
  kind: 'view',
  // `type` when the contact is not on the tab shown: its own tab opens behind it (People is the page with none).
  input: s.object({ contact: s.nullable(id), type: s.optional(text(60)) }),
  run: ({ router }, i) => {
    const change = { panel: i.contact ?? undefined, ...(i.type ? { type: i.type === 'person' ? undefined : i.type, q: undefined } : {}) };
    return on(router, '/library/contacts') ? set(router, change) : go(router, '/library/contacts', change);
  },
  target: (i) => (i.contact ? { kind: 'contact', id: i.contact } : null),
});

export const contactsUpdate = defineAction({
  id: 'contacts.update',
  title: 'Change a fact about a contact',
  kind: 'change',
  input: s.object({ contact: id, type: s.string({ min: 1, max: 60 }), key: s.string({ min: 1, max: 80 }), value: text(4000) }),
  run: (_ctx, i) => saveFact(i.contact, i.type, i.key, i.value.trim()),
  did: () => 'Saved',
  target: (i) => ({ kind: 'contact-fact', id: `${i.contact}:${i.key}` }),
});

export const contactsNote = defineAction({
  id: 'contacts.note',
  title: 'Add a note to a contact',
  kind: 'change',
  input: s.object({ contact: id, body: s.string({ min: 1, max: 4000 }), audience: s.optional(s.oneOf(['company', 'me'] as const)) }),
  run: (_ctx, i) => addNote(i.contact, i.body.trim(), i.audience ?? 'company'),
  did: () => 'Added a note',
  target: (i) => ({ kind: 'contact', id: i.contact }),
});
