import type { AnyRouter } from '@tanstack/react-router';
import * as z from 'zod/mini';
import { defineAction } from '@/actions';
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
  addContact,
  addNote,
  installSkill,
  importDoc,
  archiveDoc,
  lockDoc,
  addSource,
  addWebsite,
  apiToolQuery,
  approveProposal,
  cancelLetter,
  checkTool,
  createDoc,
  decideDevice,
  declineProposal,
  docRows,
  docsListQuery,
  fileQuery,
  probeMcp,
  refreshSource,
  removeKey,
  replaceKey,
  replaceLogin,
  restoreDoc,
  saveDoc,
  saveFact,
  setAccess,
  proposeSkill,
  skillQuery,
  skillTextQuery,
  withMain,
  SKILL_NAME,
  skillsQuery,
  startSignIn,
  toolsQuery,
  uploadFile,
  websiteToolQuery,
  type SkillRow,
  acceptQuote,
  agentSendingQuery,
  cancelNumberRequest,
  checkGoogle,
  checkLetters,
  connectMyGoogle,
  disconnectGoogle,
  disconnectMyGoogle,
  googleQuery,
  googleSetupCommand,
  lettersSettingsQuery,
  sendingQuery,
  setAgentSending,
  setIntake,
  setLegalMail,
  setLetterGrant,
  setLettersOut,
  setMailMay,
  setSending,
  sharedInbox,
  uploadGoogleKey,
  type MailMay,
  type SendPolicy,
} from './queries';
import type { PolicyUpdate, ToolIndex } from '@/api/schema';
import { viewing } from './viewing';
import { removeInstall, rescreenProposal, withdrawProposal } from './queries';
import {
  addMailboxAccess,
  checkInboxes,
  checkMailbox,
  deleteKey,
  deleteWebsite,
  grantKey,
  removeAccount,
  removeMailboxAccess,
  revokeKey,
  rotateKey,
  setAccountAccess,
  setInboxReaders,
  setMailboxLevel,
  shareMailbox,
  stopMailbox,
  storeKey,
  was,
} from './queries';

/* Every view and change on the Library pages: Docs, a doc, Files, Skills, Tools, Contacts. Anything that sets a secret,
   adds a file from the person's own disk or answers an approval is the person's own (`tico: false`). */

const id = z.string().check(z.minLength(1), z.maxLength(200));
const text = (max: number) => z.string().check(z.maxLength(max));

/* Where a stored key or login is sent, and who may use one, are the person's own press. From Tico a change that sends a
   stored credential somewhere new or widens who uses it is refused; narrowing stays Tico's. A tool not read yet is
   refused too: the screen cannot tell, and the person can do it themselves. */
const LEVEL = { none: 0, read: 1, read_write: 2 } as const;
const adds = (next: readonly string[] | undefined, had: readonly string[]) => !!next?.some((x) => !had.includes(x));

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
const file = z.custom<File>((v) => typeof File !== 'undefined' && v instanceof File, 'expected a file');

// ---------------------------------------------------------------- docs

export const docsOpen = defineAction({
  id: 'docs.open',
  title: 'Open a doc',
  kind: 'view',
  input: z.strictObject({ doc: id, version: z.optional(z.int().check(z.minimum(1))) }),
  run: ({ router }, i) => go(router, `/library/${encodeURIComponent(i.doc)}`, { v: i.version }),
  show: ({ router }, i) => go(router, `/library/${encodeURIComponent(i.doc)}`),
  target: (i) => ({ kind: 'page', id: `/library/${i.doc}` }),
  offer: ({ queryClient }) =>
    docRows(queryClient.getQueryData(docsListQuery.queryKey)).slice(0, 50).map((d) => ({ input: { doc: d.id }, title: `Open ${d.title}` })),
});

export const docsList = defineAction({
  id: 'docs.list',
  title: 'Open Docs',
  kind: 'view',
  input: z.strictObject({
    tab: z.optional(z.enum(['docs', 'sources'] as const)),
    q: z.optional(text(200)),
    source: z.optional(id),
    archived: z.optional(z.boolean()),
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
  input: z.strictObject({}),
  run: ({ router }) => go(router, '/library/new'),
  target: () => ({ kind: 'page', id: '/library/new' }),
});

export const docsCreate = defineAction({
  id: 'docs.create',
  title: 'Make a doc',
  kind: 'change',
  input: z.strictObject({ title: z.string().check(z.minLength(1), z.maxLength(300)), body: text(200_000), path: z.optional(text(300)) }),
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
  // `path`: where the doc lives (its folders and file name); a new one moves it.
  input: z.strictObject({
    doc: id,
    version: z.int().check(z.minimum(1)),
    title: z.optional(z.string().check(z.minLength(1), z.maxLength(300))),
    body: z.optional(text(200_000)),
    path: z.optional(z.string().check(z.minLength(1), z.maxLength(300))),
  }),
  run: (_ctx, i) => saveDoc(i.doc, { version: i.version, title: i.title, body: i.body, path: i.path }),
  did: () => 'Saved',
  target: (i) => ({ kind: 'page', id: `/library/${i.doc}` }),
});

export const docsRestore = defineAction({
  id: 'docs.restore',
  title: 'Bring back an old version of a doc',
  kind: 'change',
  input: z.strictObject({ doc: id, version: z.int().check(z.minimum(1)) }),
  run: async ({ router }, i) => {
    await restoreDoc(i.doc, i.version);
    await set(router, { v: undefined }, true);
  },
  did: (i) => `Brought back version ${i.version}`,
  target: (i) => ({ kind: 'page', id: `/library/${i.doc}` }),
});

/** Lock a doc (only owners and agent administrators may change it), or unlock it. Unlocking lets everyone change it
   again, so it is the person's own press; Tico may only lock. */
export const docsLock = defineAction({
  id: 'docs.lock',
  title: 'Lock or unlock a doc',
  kind: 'change',
  input: z.strictObject({ doc: id, version: z.int().check(z.minimum(1)), locked: z.boolean() }),
  can: (ctx, i) => ctx.via !== 'tico' || i.locked,
  run: (_ctx, i) => lockDoc(i.doc, i.version, i.locked),
  did: (i) => (i.locked ? 'Locked' : 'Unlocked'),
  target: (i) => ({ kind: 'page', id: `/library/${i.doc}` }),
});

/** Archive a doc (it leaves the list and search), or bring an archived one back. */
export const docsArchive = defineAction({
  id: 'docs.archive',
  title: 'Archive a doc, or bring one back',
  kind: 'change',
  input: z.strictObject({ doc: id, version: z.int().check(z.minimum(1)), archived: z.boolean() }),
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
  input: z.strictObject({ file, title: z.optional(z.string().check(z.maxLength(300))), path: z.optional(z.string().check(z.maxLength(300))) }),
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
  input: z.strictObject({ source: id }),
  run: (_ctx, i) => refreshSource(i.source),
  did: () => 'Reading it again',
  target: (i) => ({ kind: 'source', id: i.source }),
});

const sourceBody = {
  url: z.optional(z.string().check(z.minLength(1), z.maxLength(2000), z.regex(/^https?:\/\//))),
  title: z.optional(z.string().check(z.maxLength(300))),
  description: z.optional(z.string().check(z.maxLength(300))),
  reference: z.optional(z.string().check(z.maxLength(256))),
};

export const docsAddSource = defineAction({
  id: 'docs.addSource',
  title: 'Link an outside source of docs',
  kind: 'change',
  input: z.strictObject({ ...sourceBody, url: z.string().check(z.minLength(1), z.maxLength(2000), z.regex(/^https?:\/\//)) }),
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
  input: z.strictObject({ source: id, ...sourceBody, archived: z.optional(z.boolean()) }),
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
  input: z.strictObject({ kind: z.optional(text(40)), q: z.optional(text(200)) }),
  run: ({ router }, i) => go(router, '/library/files', { kind: i.kind, q: i.q }, true),
  target: () => ({ kind: 'page', id: '/library/files' }),
});

export const filesOpen = defineAction({
  id: 'files.open',
  title: 'Open a file',
  kind: 'view',
  input: z.strictObject({ file: id, version: z.optional(z.int().check(z.minimum(1))) }),
  // On Files it keeps the page's filters; from anywhere else it goes to the file.
  run: ({ router }, i) => {
    const change = { file: i.file, v: i.version };
    return on(router, '/library/files') ? set(router, change) : go(router, '/library/files', change);
  },
  target: (i) => ({ kind: 'file', id: i.file }),
});

/** A file from wherever it shows (a task, a chat): a picture full size over the page, anything else its page in Files. */
export const filesView = defineAction({
  id: 'files.view',
  title: 'Show a file',
  kind: 'view',
  input: z.strictObject({ file: z.nullable(id), version: z.optional(z.int().check(z.minimum(1))) }),
  run: async ({ router, queryClient }, i) => {
    if (!i.file) return viewing.set(null);
    const f = await queryClient.ensureQueryData(fileQuery(i.file)).catch(() => null);
    if (f && (f.mime?.startsWith('image/') || f.library_kind === 'image')) return viewing.set({ file: i.file, version: i.version, name: f.title });
    await go(router, '/library/files', { file: i.file, v: i.version });
  },
  target: () => null,
});

/** A file from a chat, full screen over the page whatever it is (a page, a PDF, a picture, text): the message's own file
    by its address, or one of the library's by its id. Closed with `file: null`. */
export const filesPreview = defineAction({
  id: 'files.preview',
  title: 'Preview a file',
  kind: 'view',
  tico: false,
  input: z.strictObject({
    file: z.nullable(id),
    version: z.optional(z.int().check(z.minimum(1))),
    held: z.optional(
      z.strictObject({
        url: z.string().check(z.regex(/^\/api\/v2\/files\/[^/?#]+(\/versions\/\d+)?(\?v=\d+)?$/)),
        mime: text(200),
        name: text(500),
        size: z.optional(z.number()),
      }),
    ),
  }),
  run: async ({ queryClient }, i) => {
    if (!i.file) return viewing.set(null);
    if (i.held) return viewing.set({ file: i.file, name: i.held.name, full: { mime: i.held.mime, size: i.held.size, url: i.held.url } });
    const f = await queryClient.ensureQueryData(fileQuery(i.file)).catch(() => null);
    viewing.set({ file: i.file, version: i.version, name: f?.title || f?.name || 'File', full: { mime: f?.mime ?? '', kind: f?.library_kind, size: f?.size } });
  },
  target: () => null,
});

export const filesUpload = defineAction({
  id: 'files.upload',
  title: 'Add a file to a task',
  kind: 'change',
  tico: false,
  input: z.strictObject({ task: id, file }),
  run: (_ctx, i) => uploadFile(i.task, i.file),
  did: (i) => `Added ${i.file.name}`,
  target: () => ({ kind: 'page', id: '/library/files' }),
});

// ---------------------------------------------------------------- skills

export const skillsList = defineAction({
  id: 'skills.list',
  title: 'Open Skills',
  kind: 'view',
  input: z.strictObject({ q: z.optional(text(200)), waiting: z.optional(z.boolean()), function: z.optional(text(64)) }),
  run: ({ router }, i) => go(router, '/library/skills', { q: i.q, waiting: i.waiting ? 1 : undefined, function: i.function }, true),
  target: () => ({ kind: 'page', id: '/library/skills' }),
});

export const skillsOpen = defineAction({
  id: 'skills.open',
  title: 'Open a skill',
  kind: 'view',
  input: z.strictObject({ skill: id }),
  run: ({ router }, i) => go(router, '/library/skills', { skill: i.skill }),
  target: (i) => ({ kind: 'skill', id: i.skill }),
  offer: ({ queryClient }) =>
    (queryClient.getQueryData<SkillRow[]>(skillsQuery.queryKey) ?? []).slice(0, 50).map((k) => ({ input: { skill: k.name }, title: `Open ${k.name}` })),
});

export const skillsOpenProposal = defineAction({
  id: 'skills.openProposal',
  title: 'Open a proposed skill change',
  kind: 'view',
  input: z.strictObject({ proposal: id }),
  run: ({ router }, i) => go(router, '/library/skills', { proposal: i.proposal }),
  target: (i) => ({ kind: 'proposal', id: i.proposal }),
});

/** A new skill as its SKILL.md. Open to Tico: it only waits for an admin's OK after screening (backend/skills.py
   propose), and changes nothing until then. A name already in the list is refused: proposing it would replace that
   skill's whole folder with this one file. Lands on the proposal. */
export const skillsPropose = defineAction({
  id: 'skills.propose',
  title: 'Propose a new skill',
  kind: 'change',
  input: z.strictObject({
    name: z.string().check(z.minLength(1), z.maxLength(80), z.regex(SKILL_NAME), z.describe('a skill name in lowercase letters, digits, dots and hyphens')),
    text: z.string().check(z.minLength(1), z.maxLength(90_000)),
    note: z.optional(text(200)),
    task: z.optional(id),
  }),
  run: async ({ router, queryClient }, i) => {
    const skills = await queryClient.ensureQueryData(skillsQuery);
    if (skills.some((k) => k.name === i.name)) throw Object.assign(new Error(`${i.name} is already a skill`), { code: 'exists' });
    const p = await proposeSkill({ name: i.name, text: i.text, note: i.note?.trim() || undefined, task: i.task });
    await go(router, '/library/skills', { proposal: p.id }, true);
    return p.id;
  },
  did: (i) => `Proposed ${i.name}`,
  target: (_i, out) => (out ? { kind: 'page', id: `/library/skills?proposal=${out}` } : null),
});

/** A change to a skill: its SKILL.md as edited, the rest of its folder as it is, from its latest version (or the
   files Tico ships it with). Open to Tico like a new skill: it waits for an admin's OK after screening, and an
   unchanged text is refused there. Lands on the proposal. */
export const skillsEdit = defineAction({
  id: 'skills.edit',
  title: "Propose a change to a skill's text",
  kind: 'change',
  input: z.strictObject({
    name: z.string().check(z.minLength(1), z.maxLength(80), z.regex(SKILL_NAME)),
    text: z.string().check(z.minLength(1), z.maxLength(90_000)),
    note: z.optional(text(200)),
    task: z.optional(id),
  }),
  run: async ({ router, queryClient }, i) => {
    const d = await queryClient.ensureQueryData(skillQuery(i.name));
    const base = d.skill.latest?.number;
    const files = base ? (await queryClient.ensureQueryData(skillTextQuery(i.name, base))).files : (d.shipped?.files ?? []);
    const p = await proposeSkill({ name: i.name, text: i.text, files: withMain(files, i.text), base, note: i.note?.trim() || undefined, task: i.task });
    await go(router, '/library/skills', { proposal: p.id }, true);
    return p.id;
  },
  did: (i) => `Proposed a change to ${i.name}`,
  target: (_i, out) => (out ? { kind: 'page', id: `/library/skills?proposal=${out}` } : null),
});

/** The setup steps. A helper's approval code is never put in the address: the page holds one from an old link itself. */
export const skillsConnect = defineAction({
  id: 'skills.connect',
  title: 'Connect Claude Code to skills',
  kind: 'view',
  input: z.strictObject({}),
  run: ({ router }) => set(router, { connect: 'setup' }),
  target: () => ({ kind: 'skills', id: 'connect' }),
});

export const skillsDecideDevice = defineAction({
  id: 'skills.decideDevice',
  title: 'Approve or deny Claude Code on a computer',
  kind: 'change',
  tico: false,
  input: z.strictObject({ code: z.string().check(z.minLength(1), z.maxLength(40)), approve: z.boolean(), share: z.optional(z.boolean()) }),
  run: (_ctx, i) => decideDevice(i.code, i.approve, !!i.share),
  did: (i) => (i.approve ? 'Connected' : 'Denied'),
  target: () => ({ kind: 'skills', id: 'connect' }),
});

export const skillsApprove = defineAction({
  id: 'skills.approve',
  title: 'Approve a skill change',
  kind: 'change',
  tico: false,
  input: z.strictObject({ proposal: id, acknowledged: z.optional(z.boolean()) }),
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
  input: z.strictObject({
    skill: z.string().check(z.minLength(1), z.maxLength(120)),
    targets: z.array(z.strictObject({ target_type: z.enum(['Bot', 'Human'] as const), target_id: id })).check(z.maxLength(200)),
    version: z.optional(z.int().check(z.minimum(1))),
  }),
  run: (_ctx, i) => (i.targets.length ? installSkill(i.skill, i.targets, i.version) : Promise.reject(new Error('no_targets'))),
  did: (i) => `Installed ${i.skill}${i.targets.length > 1 ? ` for ${i.targets.length}` : ''}`,
  target: (i) => ({ kind: 'page', id: `/library/skills?skill=${i.skill}` }),
});

export const skillsDecline = defineAction({
  id: 'skills.decline',
  title: 'Send back a skill change',
  kind: 'change',
  // Open to Tico: sending a change back only keeps the skill as it is (the server still asks for an admin).
  input: z.strictObject({ proposal: id, reason: z.string().check(z.minLength(1), z.maxLength(300)) }),
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
  input: z.strictObject({ tool: z.optional(id), q: z.optional(text(200)) }),
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
  input: z.strictObject({ kind: z.optional(text(40)), needs: z.optional(z.boolean()) }),
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
  input: z.strictObject({ tool: id }),
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
  input: z.strictObject({}),
  run: () => window.location.assign('/api/v2/github/app/install'),
  target: () => ({ kind: 'tool', id: 'github' }),
});

/** Add a key, or replace one. The value goes to the server once and is never shown again. */
export const toolsSetKey = defineAction({
  id: 'tools.setKey',
  title: 'Add or replace a key',
  kind: 'change',
  tico: false,
  input: z.strictObject({ tool: id, key: z.optional(id), name: z.optional(text(120)), value: z.string().check(z.minLength(1), z.maxLength(20_000)) }),
  run: (_ctx, i) => (i.key ? replaceKey(i.tool, i.key, i.value) : addKey(i.tool, i.name?.trim() || 'Key', i.value)),
  did: (i) => (i.key ? 'Replaced the key' : 'Added a key'),
  target: (i) => ({ kind: 'tool-connect', id: `api:${i.tool}` }),
});

export const toolsRemoveKey = defineAction({
  id: 'tools.removeKey',
  title: 'Remove a key',
  kind: 'change',
  input: z.strictObject({ tool: id, key: id }),
  // For good, and the agents using it stop: the person's own press.
  tico: false,
  run: (_ctx, i) => removeKey(i.tool, i.key),
  did: () => 'Removed the key',
  target: (i) => ({ kind: 'tool-connect', id: `api:${i.tool}` }),
});

export const toolsCheck = defineAction({
  id: 'tools.check',
  title: 'Check a tool works',
  kind: 'change',
  input: z.strictObject({ tool: id }),
  run: (_ctx, i) => checkTool(i.tool),
  did: () => 'Checked',
  target: (i) => ({ kind: 'tool-connect', id: `api:${i.tool}` }),
});

export const toolsSetAccess = defineAction({
  id: 'tools.setAccess',
  title: 'Change who may use a tool',
  kind: 'change',
  input: z.strictObject({ tool: id, subject: id, level: z.enum(['read', 'read_write', 'none'] as const) }),
  can: (ctx, i) =>
    ctx.via !== 'tico' ||
    LEVEL[i.level] <= LEVEL[ctx.queryClient.getQueryData(apiToolQuery(i.tool).queryKey)?.access.find((a) => a.subject === i.subject)?.level ?? 'none'],
  run: (_ctx, i) => setAccess(i.tool, i.subject, i.level),
  did: (i) => (i.level === 'none' ? 'Removed' : 'Changed'),
  target: (i) => ({ kind: 'tool-who', id: `api:${i.tool}` }),
});

/* Adding a tool. Tico may add one when nothing secret is typed (an API's address, an MCP server it signs in to); a
   token, a client secret or a password is the person's own. Each add opens the new tool. */

const opened = (router: AnyRouter, tool: string) => go(router, '/library/tools', { tool });

export const toolsAddApi = defineAction({
  id: 'tools.addApi',
  title: 'Add an API',
  kind: 'change',
  input: z.strictObject({
    name: z.string().check(z.minLength(1), z.maxLength(120)),
    service: z.optional(text(80)),
    base_url: z.optional(text(500)),
    auth_scheme: z.string().check(z.minLength(1), z.maxLength(80), z.regex(/^(bearer|basic_user|basic_password|(header|query):[\w-]+)$/)),
    check_path: z.optional(text(500)),
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
  input: z.strictObject({ name: z.string().check(z.minLength(1), z.maxLength(80)), url: z.string().check(z.minLength(1), z.maxLength(500)), client_id: z.optional(text(500)) }),
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
  input: z.strictObject({
    name: z.string().check(z.minLength(1), z.maxLength(80)),
    url: z.string().check(z.minLength(1), z.maxLength(500)),
    auth: z.enum(['token', 'oauth'] as const),
    token: z.optional(z.string().check(z.maxLength(100_000))),
    header: z.optional(text(60)),
    client_id: z.optional(text(500)),
    client_secret: z.optional(z.string().check(z.maxLength(2000))),
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
  username: z.optional(text(250)),
  password: z.optional(z.string().check(z.maxLength(2000))),
  totp: z.optional(z.string().check(z.maxLength(2000))),
  holder: z.optional(id),
  all_bots: z.optional(z.boolean()),
};

/** Only the site: Tico may add it. A login on it is the person's. */
export const toolsAddWebsite = defineAction({
  id: 'tools.addWebsite',
  title: 'Add a website',
  kind: 'change',
  input: z.strictObject({ site: z.string().check(z.minLength(1), z.maxLength(300)) }),
  run: async ({ router }, i) => opened(router, `website:${(await addWebsite(i.site)).id}`),
  did: (i) => `Added ${i.site}`,
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

export const toolsAddWebsiteWithLogin = defineAction({
  id: 'tools.addWebsiteWithLogin',
  title: 'Add a website with a login',
  kind: 'change',
  tico: false,
  input: z.strictObject({ site: z.string().check(z.minLength(1), z.maxLength(300)), ...login }),
  run: async ({ router }, { site, ...l }) => {
    const tool = (await addWebsite(site)).id;
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
  input: z.strictObject({ tool: id, ...login }),
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
  input: z.strictObject({
    tool: id,
    account: id,
    username: z.optional(text(250)),
    password: z.optional(z.string().check(z.minLength(1), z.maxLength(2000))),
    totp: z.optional(z.string().check(z.minLength(1), z.maxLength(2000))),
    remove_totp: z.optional(z.boolean()),
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
  input: z.strictObject({ letter: id }),
  run: (_ctx, i) => cancelLetter(i.letter),
  did: () => 'Stopped the letter',
  target: (i) => ({ kind: 'letter', id: i.letter }),
});

/* Changing a tool. Tico may rename it, move the address of a tool with no stored key, set a key's end date or a run
   setting: nothing secret is typed. Reading a stored secret, a sign-in code, a paste of keys, or anything paid or for good is the person's own. */

const api = (tool: string) => ({ kind: 'tool', id: `api:${tool}` });
const site = (tool: string) => ({ kind: 'tool', id: `website:${tool}` });
const day = z.string().check(z.maxLength(10), z.regex(/^$|^\d{4}-\d{2}-\d{2}$/));

export const toolsEditApi = defineAction({
  id: 'tools.editApi',
  title: 'Change an API tool',
  kind: 'change',
  input: z.strictObject({
    tool: id,
    name: z.optional(z.string().check(z.minLength(1), z.maxLength(120))),
    service: z.optional(text(80)),
    base_url: z.optional(text(500)),
    auth_scheme: z.optional(z.string().check(z.minLength(1), z.maxLength(80), z.regex(/^(bearer|basic_user|basic_password|(header|query):[\w-]+)$/))),
    check_path: z.optional(text(500)),
  }),
  can: (ctx, i) => {
    if (ctx.via !== 'tico' || (i.base_url === undefined && i.auth_scheme === undefined)) return true;
    const t = ctx.queryClient.getQueryData(apiToolQuery(i.tool).queryKey);
    if (!t) return false;
    const moved = (i.base_url !== undefined && i.base_url !== t.tool?.base_url) || (i.auth_scheme !== undefined && i.auth_scheme !== t.tool?.auth_scheme);
    return !moved || t.keys.length === 0;
  },
  run: (_ctx, { tool, ...change }) => editApiTool(tool, change),
  did: () => 'Saved',
  target: (i) => api(i.tool),
});

export const toolsSetKeyExpiry = defineAction({
  id: 'tools.setKeyExpiry',
  title: 'Set when a key ends',
  kind: 'change',
  input: z.strictObject({ tool: id, key: id, expires_on: day }),
  run: (_ctx, i) => setKeyExpiry(i.tool, i.key, i.expires_on),
  did: (i) => (i.expires_on ? `Ends ${i.expires_on}` : 'No end date'),
  target: (i) => ({ kind: 'tool-connect', id: `api:${i.tool}` }),
});

export const toolsSaveSetting = defineAction({
  id: 'tools.saveSetting',
  title: 'Add or change a run setting',
  kind: 'change',
  input: z.strictObject({ tool: id, setting: z.optional(id), name: z.string().check(z.minLength(1), z.maxLength(80), z.regex(/^[A-Z_][A-Z0-9_]*$/)), value: z.string().check(z.minLength(1), z.maxLength(2000)) }),
  run: (_ctx, i) => saveSetting(i.tool, { id: i.setting, name: i.name, value: i.value }),
  did: (i) => `Saved ${i.name}`,
  target: (i) => ({ kind: 'tool-about', id: `api:${i.tool}` }),
});

export const toolsRemoveSetting = defineAction({
  id: 'tools.removeSetting',
  title: 'Remove a run setting',
  kind: 'change',
  input: z.strictObject({ tool: id, setting: id }),
  run: (_ctx, i) => removeSetting(i.tool, i.setting),
  did: () => 'Removed',
  target: (i) => ({ kind: 'tool-about', id: `api:${i.tool}` }),
});

/** A stored key or website password, shown to the person who asked. The value is the result's `out` only. */
export const toolsReveal = defineAction({
  id: 'tools.reveal',
  title: 'Show a stored key or password',
  kind: 'change',
  tico: false,
  input: z.strictObject({ credential: id }),
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
  input: z.strictObject({ text: z.string().check(z.minLength(1), z.maxLength(100_000)) }),
  run: (_ctx, i) => classifyPaste(i.text),
  target: () => null,
});

export const toolsPasteKeys = defineAction({
  id: 'tools.pasteKeys',
  title: 'Add many keys at once',
  kind: 'change',
  tico: false,
  input: z.strictObject({ tool: id, text: z.string().check(z.minLength(1), z.maxLength(100_000)) }),
  run: (_ctx, i) => pasteKeys(i.tool, i.text),
  did: (_i, out) => (out ? `Added ${out.keys} ${out.keys === 1 ? 'key' : 'keys'} and ${out.settings} ${out.settings === 1 ? 'setting' : 'settings'}` : 'Added'),
  target: (i) => ({ kind: 'tool-connect', id: `api:${i.tool}` }),
});

export const toolsEditWebsite = defineAction({
  id: 'tools.editWebsite',
  title: 'Rename a website tool or change its sites',
  kind: 'change',
  input: z.strictObject({
    tool: id,
    name: z.optional(z.string().check(z.minLength(1), z.maxLength(80))),
    sites: z.optional(z.array(z.string().check(z.minLength(1), z.maxLength(300))).check(z.maxLength(50))),
  }),
  can: (ctx, i) => {
    if (ctx.via !== 'tico' || i.sites === undefined) return true;
    const t = ctx.queryClient.getQueryData(websiteToolQuery(i.tool).queryKey);
    if (!t) return false;
    return !adds(i.sites, t.tool.sites) || t.accounts.length === 0;
  },
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
  input: z.strictObject({ tool: id, account: id }),
  run: (_ctx, i) => signInCode(i.tool, i.account),
  did: () => 'Shown',
  target: () => null,
});

export const toolsAddTicoAddress = defineAction({
  id: 'tools.addTicoAddress',
  title: 'Make a Tico address',
  kind: 'change',
  input: z.strictObject({ name: z.string().check(z.minLength(1), z.maxLength(80)) }),
  run: async ({ router }, i) => {
    const made = await addTicoAddress(i.name.trim());
    if (made) await opened(router, `tico:${made}`);
  },
  did: (i) => `Made ${i.name}`,
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

/** Asking buys nothing: a price comes back, and accepting it (`tools.acceptQuote`) is the person's own. */
export const toolsRequestNumber = defineAction({
  id: 'tools.requestNumber',
  title: 'Ask for a US mobile number',
  kind: 'change',
  input: z.strictObject({ name: z.string().check(z.minLength(1), z.maxLength(80)) }),
  run: (_ctx, i) => requestNumber(i.name.trim()),
  did: () => 'Asked for a number',
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

export const toolsEditInbox = defineAction({
  id: 'tools.editInbox',
  title: 'Rename an address or number',
  kind: 'change',
  input: z.strictObject({ inbox: id, name: z.string().check(z.minLength(1), z.maxLength(80)), used_for: z.optional(z.nullable(text(1000))) }),
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
  input: z.strictObject({ inbox: id, confirm: z.optional(text(160)) }),
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
  input: z.strictObject({ address: z.string().check(z.minLength(3), z.maxLength(320)), shared: z.boolean() }),
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
  input: z.strictObject({ kind: z.enum(['shared', 'private'] as const), state: z.enum(['DE', 'CA', 'TX', 'FL', 'NV'] as const), company_name: z.string().check(z.minLength(1), z.maxLength(200)) }),
  run: (_ctx, i) => orderAddress({ ...i, company_name: i.company_name.trim() }),
  did: () => 'Asked for a mailing address',
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

export const toolsPauseWebhook = defineAction({
  id: 'tools.pauseWebhook',
  title: 'Pause or restart an outgoing webhook',
  kind: 'change',
  input: z.strictObject({ webhook: id, on: z.boolean() }),
  // Sending again sends company events outside again: Tico may pause, never restart.
  can: (ctx, i) => ctx.via !== 'tico' || !i.on,
  run: (_ctx, i) => setWebhookOn(i.webhook, i.on),
  did: (i) => (i.on ? 'Sending again' : 'Paused'),
  target: (i) => ({ kind: 'tool', id: `webhook:${i.webhook}` }),
});

export const toolsTestWebhook = defineAction({
  id: 'tools.testWebhook',
  title: 'Send a test to an outgoing webhook',
  kind: 'change',
  input: z.strictObject({ webhook: id }),
  run: (_ctx, i) => sendTestEvent(i.webhook),
  did: () => 'Sent a test',
  target: (i) => ({ kind: 'tool', id: `webhook:${i.webhook}` }),
});

// ---------------------------------------------------------------- contacts

export const contactsList = defineAction({
  id: 'contacts.list',
  title: 'Open Contacts',
  kind: 'view',
  // `link`: only the contacts with that link to us (customer_of, vendor_to...), the Relationship filter.
  input: z.strictObject({ type: z.optional(text(60)), q: z.optional(text(200)), link: z.optional(text(60)) }),
  run: ({ router }, i) => go(router, '/library/contacts', { type: i.type, q: i.q, link: i.link }, true),
  target: () => ({ kind: 'page', id: '/library/contacts' }),
});

export const contactsOpen = defineAction({
  id: 'contacts.open',
  title: 'Open a contact',
  kind: 'view',
  // `type` when the contact is not on the tab shown: its own tab opens behind it (People is the page with none).
  input: z.strictObject({ contact: z.nullable(id), type: z.optional(text(60)) }),
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
  input: z.strictObject({ contact: id, type: z.string().check(z.minLength(1), z.maxLength(60)), key: z.string().check(z.minLength(1), z.maxLength(80)), value: text(4000) }),
  run: (_ctx, i) => saveFact(i.contact, i.type, i.key, i.value.trim()),
  did: () => 'Saved',
  target: (i) => ({ kind: 'contact-fact', id: `${i.contact}:${i.key}` }),
});

export const contactsAdd = defineAction({
  id: 'contacts.add',
  title: 'Add a contact',
  kind: 'change',
  input: z.strictObject({ type: z.string().check(z.minLength(1), z.maxLength(60)), name: z.string().check(z.minLength(1), z.maxLength(300)) }),
  run: async ({ router }, i) => {
    const r = await addContact(i.type, i.name.trim());
    await go(router, '/library/contacts', { type: i.type === 'person' ? undefined : i.type, panel: r.id });
    return r.id;
  },
  did: (i) => `Added ${i.name.trim()}`,
  target: (_i, out) => (out ? { kind: 'contact', id: out } : null),
});

export const contactsNote = defineAction({
  id: 'contacts.note',
  title: 'Add a note to a contact',
  kind: 'change',
  input: z.strictObject({ contact: id, body: z.string().check(z.minLength(1), z.maxLength(4000)), audience: z.optional(z.enum(['company', 'me'] as const)) }),
  run: (_ctx, i) => addNote(i.contact, i.body.trim(), i.audience ?? 'company'),
  did: () => 'Added a note',
  target: (i) => ({ kind: 'contact', id: i.contact }),
});

/* ---------------------------------------------------------------- company settings for Email, Texts and Letters */

/* The old Tools › Settings. The person's own press (`tico: false`): anything paid (a number's quote), anything that
   reveals or stores a secret (a Workspace key, a setup code, a Google sign-in, a connection code) and joining another
   company's shared Inbox, which sends this company's mail there. Rules on what agents may send, and the letters'
   sending rule and cap, Tico may only tighten: from Tico a change that lets more out is refused, as is one on rules not
   read yet. Everything else is Tico's too. */

const RANK: Record<string, number> = { never: 0, ask_first: 1, on_their_own: 2, replies_only: 0, replies_and_new: 1, internal: 0, known_addresses: 1, anyone: 2 };
const up = (next: string | null | undefined, was: string | null | undefined) =>
  next !== undefined && (next === null || (RANK[next] ?? 9) > (RANK[was ?? ''] ?? -1));
const CAPS = ['daily_cap', 'mailbox_daily_cap', 'company_daily_cap', 'max_outside_recipients'] as const;
const ASKS = ['ask_new_address', 'ask_legal_money', 'ask_attachments', 'ask_many_recipients', 'ask_outside_cc'] as const;

/** Whether a change to the sending rules lets agents send more, sooner or to more people than `was`. */
export function sendsMore(was: SendPolicy | undefined, next: PolicyUpdate): boolean {
  if (!was || next.inherit) return true;
  if (up(next.mode, was.mode) || up(next.what, was.what) || up(next.to, was.to)) return true;
  if (CAPS.some((k) => next[k] != null && (next[k] as number) > (was[k] ?? 0))) return true;
  if (next.undo_seconds != null && next.undo_seconds < (was.undo_seconds ?? 0)) return true;
  if (ASKS.some((k) => next[k] === false && was[k])) return true;
  return !!next.blocked_recipients && (was.blocked_recipients ?? []).some((x) => !next.blocked_recipients?.includes(x));
}

const mayIn = z.strictObject({ organize_mail: z.boolean(), send_mail: z.boolean(), change_calendar: z.boolean() });

export const toolsSetMailMay = defineAction({
  id: 'tools.setMailMay',
  title: 'Change what agents may do in Google',
  kind: 'change',
  input: mayIn,
  can: (ctx, i) => {
    if (ctx.via !== 'tico') return true;
    const was = ctx.queryClient.getQueryData(googleQuery.queryKey)?.permissions;
    return !!was && (Object.keys(i) as (keyof MailMay)[]).every((k) => !i[k] || was[k]);
  },
  run: (_ctx, i) => setMailMay(i),
  did: () => 'Saved',
  target: () => ({ kind: 'tool-can', id: 'mailbox' }),
});

export const toolsCheckGoogle = defineAction({
  id: 'tools.checkGoogle',
  title: 'Check the Google Workspace connection',
  kind: 'change',
  input: z.strictObject({}),
  run: () => checkGoogle(),
  did: (_i, out) => ((out as { ok?: boolean } | undefined)?.ok === false ? 'Google did not accept it' : 'Google accepts it'),
  target: () => ({ kind: 'tool-connect', id: 'mailbox' }),
});

/** A setup command for an agent or a terminal to run: it carries a one-time code, so it is the person's own. */
export const toolsGoogleSetup = defineAction({
  id: 'tools.googleSetup',
  title: 'Make a Google Workspace setup command',
  kind: 'change',
  tico: false,
  input: z.strictObject({ domain: z.string().check(z.minLength(3), z.maxLength(253)) }),
  run: (_ctx, i) => googleSetupCommand(i.domain.trim().toLowerCase()).then((r) => r.command),
  did: () => 'Made a setup command',
  target: () => null,
});

export const toolsUploadGoogleKey = defineAction({
  id: 'tools.uploadGoogleKey',
  title: 'Upload a Google Workspace key',
  kind: 'change',
  tico: false,
  input: z.strictObject({ domain: z.string().check(z.minLength(3), z.maxLength(253)), key: z.string().check(z.minLength(2), z.maxLength(20000)) }),
  run: (_ctx, i) => uploadGoogleKey(i.domain.trim().toLowerCase(), JSON.parse(i.key) as Record<string, unknown>),
  did: () => 'Key uploaded',
  target: () => ({ kind: 'tool-connect', id: 'mailbox' }),
});

export const toolsDisconnectGoogle = defineAction({
  id: 'tools.disconnectGoogle',
  title: 'Disconnect the Google Workspace',
  kind: 'change',
  tico: false,
  input: z.strictObject({}),
  run: () => disconnectGoogle(),
  did: () => 'Disconnected',
  target: () => ({ kind: 'tool-connect', id: 'mailbox' }),
});

/** The person's own Google sign-in: off to Google's page, back to Tico when done. */
export const toolsConnectMyGoogle = defineAction({
  id: 'tools.connectMyGoogle',
  title: 'Connect your Google account',
  kind: 'change',
  tico: false,
  input: z.strictObject({}),
  run: async () => {
    window.location.assign((await connectMyGoogle()).auth_url);
  },
  did: () => 'Off to Google',
  target: () => null,
});

export const toolsDisconnectMyGoogle = defineAction({
  id: 'tools.disconnectMyGoogle',
  title: 'Disconnect your Google account',
  kind: 'change',
  tico: false,
  input: z.strictObject({}),
  run: () => disconnectMyGoogle(),
  did: () => 'Disconnected',
  target: () => ({ kind: 'tool-connect', id: 'mailbox' }),
});

const capIn = z.optional(z.int().check(z.minimum(0), z.maximum(1_000_000)));

export const toolsSetSending = defineAction({
  id: 'tools.setSending',
  title: 'Change what agents may send by email',
  kind: 'change',
  input: z.strictObject({
    mode: z.optional(z.enum(['ask_first', 'on_their_own', 'never'] as const)),
    what: z.optional(z.enum(['replies_only', 'replies_and_new'] as const)),
    to: z.optional(z.enum(['anyone', 'known_addresses', 'internal'] as const)),
    daily_cap: capIn,
    mailbox_daily_cap: capIn,
    company_daily_cap: capIn,
    max_outside_recipients: z.optional(z.int().check(z.minimum(0), z.maximum(100))),
    undo_seconds: z.optional(z.literal([0, 30, 120, 600] as const)),
    ask_new_address: z.optional(z.boolean()),
    ask_legal_money: z.optional(z.boolean()),
    ask_attachments: z.optional(z.boolean()),
    ask_many_recipients: z.optional(z.boolean()),
    ask_outside_cc: z.optional(z.boolean()),
    blocked_recipients: z.optional(z.array(z.string().check(z.minLength(1), z.maxLength(320))).check(z.maxLength(500))),
  }),
  can: (ctx, i) => ctx.via !== 'tico' || !sendsMore(ctx.queryClient.getQueryData(sendingQuery.queryKey)?.effective, i),
  run: (_ctx, i) => setSending(i),
  did: () => 'Saved',
  target: () => ({ kind: 'tool-can', id: 'mailbox' }),
});

export const toolsSetAgentSending = defineAction({
  id: 'tools.setAgentSending',
  title: 'Change what one agent may send by email',
  kind: 'change',
  input: z.strictObject({ bot: id, mode: z.nullable(z.enum(['ask_first', 'on_their_own', 'never'] as const)) }),
  can: (ctx, i) => {
    if (ctx.via !== 'tico') return true;
    const own = ctx.queryClient.getQueryData(agentSendingQuery(i.bot).queryKey);
    const company = ctx.queryClient.getQueryData(sendingQuery.queryKey)?.effective.mode;
    return !!own && company !== undefined && !up(i.mode, own.mode ?? company);
  },
  run: (_ctx, i) => setAgentSending(i.bot, i.mode),
  did: (i) => (i.mode ? 'Saved' : 'Uses the company’s rule'),
  target: () => ({ kind: 'tool-can', id: 'mailbox' }),
});

/** Joining the company's shared Inbox: this workspace's mail goes there and the company's comes here. */
export const toolsJoinSharedInbox = defineAction({
  id: 'tools.joinSharedInbox',
  title: 'Join the company’s shared Inbox',
  kind: 'change',
  tico: false,
  input: z.strictObject({ code: z.string().check(z.minLength(43), z.maxLength(43)) }),
  run: (_ctx, i) => sharedInbox('connect', i.code).then(() => undefined),
  did: () => 'Joined',
  target: () => null,
});

/** Accepting a number's quote buys it for 30 days: paid, so the person's own. */
export const toolsAcceptQuote = defineAction({
  id: 'tools.acceptQuote',
  title: 'Accept a phone number’s quote',
  kind: 'change',
  tico: false,
  input: z.strictObject({ request: id, price_micros: z.int().check(z.minimum(0)) }),
  run: (_ctx, i) => acceptQuote(i.request, i.price_micros),
  did: () => 'Quote accepted',
  target: () => ({ kind: 'tool-connect', id: 'number' }),
});

export const toolsCancelNumberRequest = defineAction({
  id: 'tools.cancelNumberRequest',
  title: 'Cancel a phone number request',
  kind: 'change',
  input: z.strictObject({ request: id }),
  run: (_ctx, i) => cancelNumberRequest(i.request),
  did: () => 'Request canceled',
  target: () => ({ kind: 'tool-connect', id: 'number' }),
});

export const toolsSetIntake = defineAction({
  id: 'tools.setIntake',
  title: 'Choose where emailed letters come in',
  kind: 'change',
  input: z.strictObject({ inbox: z.nullable(id), senders: z.array(z.string().check(z.minLength(1), z.maxLength(320))).check(z.maxLength(100)) }),
  // Tico may stop it, never set where letters come in or who may send them.
  can: (ctx, i) => (i.inbox ? ctx.via !== 'tico' && i.senders.length > 0 : true),
  run: (_ctx, i) => setIntake(i.inbox, i.inbox ? i.senders.map((x) => x.trim().toLowerCase()) : []),
  did: (i) => (i.inbox ? 'Saved' : 'Stopped'),
  target: () => ({ kind: 'tool-can', id: 'address' }),
});

export const toolsSetLetterAccess = defineAction({
  id: 'tools.setLetterAccess',
  title: 'Change who may read every letter',
  kind: 'change',
  input: z.strictObject({ type: z.enum(['person', 'bot'] as const), who: id, read: z.boolean() }),
  // Tico may take the read away, never give it.
  can: (ctx, i) => ctx.via !== 'tico' || !i.read,
  run: (_ctx, i) => setLetterGrant(i.type, i.who, i.read),
  did: (i) => (i.read ? 'Can read letters' : 'Removed'),
  target: () => ({ kind: 'tool-who', id: 'address' }),
});

export const toolsSetLegalMail = defineAction({
  id: 'tools.setLegalMail',
  title: 'Choose who gets legal letters',
  kind: 'change',
  input: z.strictObject({ bot: z.nullable(id) }),
  // Tico may hand legal letters back to the owner, never to an agent.
  can: (ctx, i) => ctx.via !== 'tico' || i.bot === null,
  run: (_ctx, i) => setLegalMail(i.bot),
  did: () => 'Saved',
  target: () => ({ kind: 'tool-can', id: 'address' }),
});

const addressIn = z.strictObject({
  name: z.string().check(z.minLength(1), z.maxLength(100)),
  line1: z.string().check(z.minLength(1), z.maxLength(100)),
  line2: z.optional(z.string().check(z.maxLength(100))),
  city: z.string().check(z.minLength(1), z.maxLength(100)),
  state: z.string().check(z.regex(/^[A-Z]{2}$/), z.describe('a two-letter state')),
  postal_code: z.string().check(z.regex(/^\d{5}(-\d{4})?$/), z.describe('a ZIP code')),
});

/** Letters out: who decides a letter goes (each one is paid), the month's cap and the return address. */
export const toolsSetLettersOut = defineAction({
  id: 'tools.setLettersOut',
  title: 'Change how letters go out',
  kind: 'change',
  input: z.strictObject({
    mode: z.optional(z.enum(['ask_first', 'on_their_own', 'never'] as const)),
    monthly_cap_micros: z.optional(z.nullable(z.int().check(z.minimum(0), z.maximum(1_000_000_000_000)))),
    return_address: z.optional(z.nullable(addressIn)),
  }),
  can: (ctx, i) => {
    if (ctx.via !== 'tico') return true;
    const was = ctx.queryClient.getQueryData(lettersSettingsQuery.queryKey);
    if (!was) return false;
    const cap = i.monthly_cap_micros;
    return !up(i.mode, was.mode) && (cap === undefined || (cap !== null && (was.monthly_cap_micros === null || cap <= was.monthly_cap_micros)));
  },
  run: (_ctx, i) => setLettersOut(i),
  did: () => 'Saved',
  target: () => ({ kind: 'tool-can', id: 'address' }),
});

export const toolsCheckLetters = defineAction({
  id: 'tools.checkLetters',
  title: 'Check letters can go out',
  kind: 'change',
  input: z.strictObject({}),
  run: () => checkLetters(),
  did: () => 'Checked',
  target: () => ({ kind: 'tool-can', id: 'address' }),
});

// ---------------------------------------------------------------- tools: stored keys, mailboxes, who reads and sees a tool

/* The Tools gap rows (s787 s788 s865 s866 s867 s908 s909 s926). Tightening stays Tico's (taking access away, a lower
   level, a check, stopping); storing or showing a secret, giving access or a higher level, opening a tool wider, and
   anything that cannot be undone are the person's own. */

const subjectIn = z.string().check(z.minLength(1), z.maxLength(220));

/** Tools narrowed to who has access: a team (`group:<id>`) or a teammate (`human:<id>`, `bot:<id>`); none shows all. */
export const toolsFilterWho = defineAction({
  id: 'tools.filterWho',
  title: 'Filter tools by who has access',
  kind: 'view',
  input: z.strictObject({ who: z.optional(subjectIn) }),
  run: ({ router }, i) => (on(router, '/library/tools') ? set(router, { who: i.who }, true) : go(router, '/library/tools', { who: i.who })),
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

/** A new value for a stored key no tool holds. The value is only sent. */
export const toolsRotateKey = defineAction({
  id: 'tools.rotateKey',
  title: 'Rotate a stored key',
  kind: 'change',
  // Storing a secret is the person's own.
  tico: false,
  input: z.strictObject({ credential: id, value: z.string().check(z.minLength(1), z.maxLength(100_000)) }),
  run: (_ctx, i) => {
    const key = was.vault()?.credentials.find((k) => k.id === i.credential);
    if (!key) throw new Error('That key is not open');
    return rotateKey(key, i.value);
  },
  did: () => 'Rotated',
  target: (i) => ({ kind: 'tool', id: `key:${i.credential}` }),
});

/** A key a declared tool names that the vault does not hold yet. */
export const toolsStoreKey = defineAction({
  id: 'tools.storeKey',
  title: 'Store a key',
  kind: 'change',
  // Storing a secret is the person's own.
  tico: false,
  input: z.strictObject({
    name: z.string().check(z.minLength(1), z.maxLength(150)),
    env: z.string().check(z.maxLength(100), z.regex(/^$|^[A-Z_][A-Z0-9_]*$/), z.describe('a variable name')),
    source: text(500),
    kind: z.enum(['api_key', 'token'] as const),
    value: z.string().check(z.minLength(1), z.maxLength(100_000)),
  }),
  run: (_ctx, { value, ...key }) => storeKey({ ...key, secret: value }),
  did: (i) => `Stored ${i.name}`,
  target: () => null,
});

export const toolsGrantKey = defineAction({
  id: 'tools.grantKey',
  title: 'Give someone a stored key',
  kind: 'change',
  // Giving someone a stored key is a right only the person gives.
  tico: false,
  input: z.strictObject({ credential: id, subject: subjectIn }),
  run: (_ctx, i) => grantKey(i.credential, i.subject),
  did: () => 'Added',
  target: (i) => ({ kind: 'tool-who', id: `key:${i.credential}` }),
});

/** Taking a key away: Tico may. */
export const toolsRevokeKey = defineAction({
  id: 'tools.revokeKey',
  title: 'Take a stored key away from someone',
  kind: 'change',
  input: z.strictObject({ credential: id, grant: id }),
  run: (_ctx, i) => revokeKey(i.credential, i.grant),
  did: () => 'Removed',
  target: (i) => ({ kind: 'tool-who', id: `key:${i.credential}` }),
});

export const toolsDeleteKey = defineAction({
  id: 'tools.deleteKey',
  title: 'Delete a stored key',
  kind: 'change',
  // The key and every grant of it go for good: it cannot be undone.
  tico: false,
  input: z.strictObject({ credential: id }),
  run: async ({ router }, i) => {
    await deleteKey(i.credential);
    await go(router, '/library/tools', {}, true);
  },
  did: () => 'Deleted',
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

/* A mailbox. */

const levelIn = z.enum(['read', 'read_write'] as const);

export const toolsAddMailboxAccess = defineAction({
  id: 'tools.addMailboxAccess',
  title: 'Give someone a mailbox',
  kind: 'change',
  // Giving someone a mailbox is a right only the person gives.
  tico: false,
  input: z.strictObject({ mailbox: id, type: z.enum(['person', 'bot'] as const), who: id, level: levelIn }),
  run: (_ctx, i) => addMailboxAccess(i.mailbox, i.type, i.who, i.level),
  did: () => 'Added',
  target: (i) => ({ kind: 'tool-who', id: `mailbox:${i.mailbox}` }),
});

export const toolsSetMailboxAccess = defineAction({
  id: 'tools.setMailboxAccess',
  title: "Change someone's level on a mailbox",
  kind: 'change',
  input: z.strictObject({ mailbox: id, access: id, level: z.enum(['read', 'read_write', 'none'] as const) }),
  // Tico may lower a level or take it away, never raise one.
  can: (ctx, i) =>
    ctx.via !== 'tico' ||
    LEVEL[i.level] <= LEVEL[was.mailbox(i.mailbox)?.accesses.find((a) => a.id === i.access)?.level ?? 'none'],
  run: (_ctx, i) => (i.level === 'none' ? removeMailboxAccess(i.mailbox, i.access) : setMailboxLevel(i.mailbox, i.access, i.level)),
  did: (i) => (i.level === 'none' ? 'Removed' : 'Changed'),
  target: (i) => ({ kind: 'tool-who', id: `mailbox:${i.mailbox}` }),
});

/** Google asked again, and the mailbox read again. */
export const toolsCheckMailbox = defineAction({
  id: 'tools.checkMailbox',
  title: 'Check a mailbox',
  kind: 'change',
  input: z.strictObject({ mailbox: id }),
  run: (_ctx, i) => checkMailbox(i.mailbox),
  did: (_i, out) => out?.text ?? 'Checked',
  target: (i) => ({ kind: 'tool-connect', id: `mailbox:${i.mailbox}` }),
});

export const toolsShareMailbox = defineAction({
  id: 'tools.shareMailbox',
  title: 'Make a mailbox a shared inbox',
  kind: 'change',
  // A shared inbox is opened to the company: a right only the person gives.
  tico: false,
  input: z.strictObject({ mailbox: id }),
  run: (_ctx, i) => shareMailbox(i.mailbox),
  did: () => 'Shared',
  target: (i) => ({ kind: 'tool', id: `mailbox:${i.mailbox}` }),
});

/** Tico stops reading and sending from it. */
export const toolsStopMailbox = defineAction({
  id: 'tools.stopMailbox',
  title: 'Stop using a mailbox',
  kind: 'change',
  input: z.strictObject({ mailbox: id }),
  run: async ({ router }, i) => {
    await stopMailbox(i.mailbox);
    await go(router, '/library/tools', {}, true);
  },
  did: () => 'Stopped',
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

/* A Tico address or number. */

export const toolsSetInboxReaders = defineAction({
  id: 'tools.setInboxReaders',
  title: 'Choose who reads an address or number',
  kind: 'change',
  input: z.strictObject({ inbox: id, audience: z.enum(['company', 'team'] as const), group: z.optional(id) }),
  // Tico may narrow it from everyone to one team, never open it wider.
  can: (ctx, i) => ctx.via !== 'tico' || (i.audience === 'team' && was.inbox(i.inbox)?.audience === 'company'),
  run: (_ctx, i) => setInboxReaders(i.inbox, i.audience, i.group ?? null),
  did: () => 'Saved',
  target: (i) => ({ kind: 'tool', id: `tico:${i.inbox}` }),
});

/** The inbound connection read again. */
export const toolsCheckInbox = defineAction({
  id: 'tools.checkInbox',
  title: 'Check the connection for addresses and numbers',
  kind: 'change',
  input: z.strictObject({}),
  run: () => checkInboxes(),
  did: (_i, out) => out?.text ?? 'Checked',
  target: () => null,
});

/* A website. */

export const toolsRemoveAccount = defineAction({
  id: 'tools.removeAccount',
  title: 'Remove an account from a website',
  kind: 'change',
  // The login and its grants go for good: it cannot be undone.
  tico: false,
  input: z.strictObject({ tool: id, account: id }),
  run: (_ctx, i) => removeAccount(i.tool, i.account),
  did: () => 'Removed',
  target: (i) => site(i.tool),
});

export const toolsDeleteWebsite = defineAction({
  id: 'tools.deleteWebsite',
  title: 'Delete a website tool',
  kind: 'change',
  // The website, its logins and their grants go for good: it cannot be undone.
  tico: false,
  input: z.strictObject({ tool: id }),
  run: async ({ router }, i) => {
    await deleteWebsite(i.tool);
    await go(router, '/library/tools', {}, true);
  },
  did: () => 'Deleted',
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

export const toolsSetAccountAccess = defineAction({
  id: 'tools.setAccountAccess',
  title: 'Change who may use a website account',
  kind: 'change',
  input: z.strictObject({ tool: id, account: id, subject: subjectIn, on: z.boolean() }),
  // Tico may take a login away, never give one.
  can: (ctx, i) => ctx.via !== 'tico' || !i.on,
  run: (_ctx, i) => setAccountAccess(i.tool, i.account, i.subject, i.on),
  did: (i) => (i.on ? 'Added' : 'Removed'),
  target: (i) => site(i.tool),
});

// ---------------------------------------------------------------- tier-2 gaps: skills (s781 s782 s783 s804)

/* All open to Tico: each one only narrows the list, takes a skill off (installing it again puts it back), ends or
   re-screens a proposal, or answers the maintenance run's suggestion. The server checks the rights of each. */

export const skillsRemoveInstall = defineAction({
  id: 'skills.removeInstall',
  title: 'Take a skill off a teammate',
  kind: 'change',
  input: z.strictObject({ skill: z.string().check(z.minLength(1), z.maxLength(120)), install: id }),
  run: (_ctx, i) => removeInstall(i.skill, i.install),
  did: () => 'Removed',
  target: (i) => ({ kind: 'page', id: `/library/skills?skill=${i.skill}` }),
});

/** The proposer's own: it leaves the list; proposing it again starts a new one. */
export const skillsWithdraw = defineAction({
  id: 'skills.withdraw',
  title: 'Withdraw a skill change',
  kind: 'change',
  input: z.strictObject({ proposal: id }),
  run: async ({ router }, i) => {
    await withdrawProposal(i.proposal);
    await go(router, '/library/skills', {}, true);
  },
  did: () => 'Withdrawn',
  target: () => ({ kind: 'page', id: '/library/skills' }),
});

export const skillsRescreen = defineAction({
  id: 'skills.rescreen',
  title: 'Screen a skill change again',
  kind: 'change',
  input: z.strictObject({ proposal: id }),
  run: (_ctx, i) => rescreenProposal(i.proposal),
  did: () => 'Screening',
  target: (i) => ({ kind: 'proposal', id: i.proposal }),
});

// ---------------------------------------------------------------- tools: tier-2 gaps (webhooks, letters, messages)

/* s515 s868 s869 s870 s932. The person's own: deleting an endpoint (cannot be undone), a new signing secret and a
   message's code (each shows a secret). Giving someone Read on an address is a right only the person gives; Tico may
   take it away. A send retried, a letter retried and an address checked are ordinary work: open to Tico. */

import { addWebhook, checkAddresses, deleteWebhook, editWebhook, readCode, webhookQuery, retryLetterIn, retryWebhookSend, rotateWebhookSecret, setAddressGrant } from './queries';

const hookAt = (webhook: string) => ({ kind: 'tool', id: `webhook:${webhook}` });

export const toolsDeleteWebhook = defineAction({
  id: 'tools.deleteWebhook',
  title: 'Delete an outgoing webhook',
  kind: 'change',
  // Nothing more goes to it and what waits is canceled: it cannot be undone.
  tico: false,
  input: z.strictObject({ webhook: id }),
  run: async ({ router }, i) => {
    await deleteWebhook(i.webhook);
    await go(router, '/library/tools', {}, true);
  },
  did: () => 'Deleted',
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

/** A new signing secret. The value is the result's `out` only, shown once in the dialog that asked. */
export const toolsNewWebhookSecret = defineAction({
  id: 'tools.newWebhookSecret',
  title: 'Make a new signing secret for an outgoing webhook',
  kind: 'change',
  // It shows a secret.
  tico: false,
  input: z.strictObject({ webhook: id }),
  run: (_ctx, i) => rotateWebhookSecret(i.webhook),
  did: () => 'Made a new secret',
  target: () => null,
});

export const toolsRetrySend = defineAction({
  id: 'tools.retrySend',
  title: 'Send one webhook send again',
  kind: 'change',
  input: z.strictObject({ webhook: id, send: id }),
  run: (_ctx, i) => retryWebhookSend(i.webhook, i.send),
  did: () => 'Retrying',
  target: (i) => hookAt(i.webhook),
});

/* A mailing address. */

export const toolsSetAddressAccess = defineAction({
  id: 'tools.setAddressAccess',
  title: 'Change who may read one address’s letters',
  kind: 'change',
  input: z.strictObject({ address: id, type: z.enum(['person', 'bot'] as const), who: id, read: z.boolean() }),
  // Read takes in private letters and scans: Tico may take it away, never give it.
  can: (ctx, i) => ctx.via !== 'tico' || !i.read,
  run: (_ctx, i) => setAddressGrant(i.address, i.type, i.who, i.read),
  did: (i) => (i.read ? 'Can read its letters' : 'Removed'),
  target: (i) => ({ kind: 'tool', id: `address:${i.address}` }),
});

export const toolsRetryLetter = defineAction({
  id: 'tools.retryLetter',
  title: 'Try a failed letter again',
  kind: 'change',
  input: z.strictObject({ letter: id }),
  run: (_ctx, i) => retryLetterIn(i.letter),
  did: () => 'Retrying',
  target: () => null,
});

const ADDRESS_WORD: Record<string, string> = { pending_1583: 'Waiting for Form 1583', active: 'Connected', closed: 'Closed' };

/** The address as the Gateway has it now: its state, or why it could not be read. */
export const toolsCheckAddress = defineAction({
  id: 'tools.checkAddress',
  title: 'Check a mailing address',
  kind: 'change',
  input: z.strictObject({ address: id }),
  run: async (_ctx, i) => {
    const r = await checkAddresses();
    const a = (r.addresses ?? []).find((x) => String(x.assignment_id || x.id || '') === i.address || x.address === i.address);
    return a ? { ok: a.status === 'active', text: ADDRESS_WORD[a.status] ?? a.status } : { ok: false, text: 'Not found' };
  },
  did: (_i, out) => out?.text ?? 'Checked',
  target: (i) => ({ kind: 'tool', id: `address:${i.address}` }),
});

/* A message that came in to a Tico address or number. */

/** The code a message carries. The value is the result's `out` only, shown in the reader that asked. */
export const toolsRevealCode = defineAction({
  id: 'tools.revealCode',
  title: 'Show the code in a message',
  kind: 'change',
  // A code is a secret: the person's own.
  tico: false,
  input: z.strictObject({ message: id }),
  run: (_ctx, i) => readCode(i.message),
  did: () => 'Shown',
  target: () => null,
});

/* An outgoing webhook's own add and edit (Tools is its only home). */

const hookFields = {
  name: z.string().check(z.minLength(1), z.maxLength(120)),
  url: z.string().check(z.minLength(1), z.maxLength(2000), z.regex(/^https?:\/\//)),
  events: z.array(z.string().check(z.minLength(1), z.maxLength(120))).check(z.maxLength(200)),
};

export const toolsAddWebhook = defineAction({
  id: 'tools.addWebhook',
  title: 'Add an outgoing webhook',
  kind: 'change',
  // Its signing secret is made now and shown once: the person's own press.
  tico: false,
  input: z.strictObject(hookFields),
  run: (_ctx, i) => {
    if (!i.events.length) throw Object.assign(new Error('Pick at least one event'), { code: 'bad_input: Pick at least one event' });
    return addWebhook({ name: i.name.trim(), url: i.url.trim(), events: i.events, allow_http: /^http:/i.test(i.url.trim()) });
  },
  did: (_i, out) => (out?.approval ? 'Sent for approval' : 'Added'),
  target: () => ({ kind: 'page', id: '/library/tools' }),
});

export const toolsEditWebhook = defineAction({
  id: 'tools.editWebhook',
  title: 'Change an outgoing webhook',
  kind: 'change',
  input: z.strictObject({ webhook: id, name: z.optional(hookFields.name), url: z.optional(hookFields.url), events: z.optional(hookFields.events) }),
  // Tico may rename it or change its events; where the company's events are sent is the person's own.
  can: (ctx, i) => ctx.via !== 'tico' || !i.url || ctx.queryClient.getQueryData(webhookQuery(i.webhook).queryKey)?.webhook.url === i.url.trim(),
  run: (_ctx, i) => editWebhook(i.webhook, { name: i.name?.trim(), url: i.url?.trim(), events: i.events, ...(i.url ? { allow_http: /^http:/i.test(i.url.trim()) } : {}) }),
  did: () => 'Saved',
  target: (i) => hookAt(i.webhook),
});
