import type { QueryClient } from '@tanstack/react-query';
import { useSyncExternalStore } from 'react';
import {
  apiToolQuery,
  cardQuery,
  contactsQuery,
  docQuery,
  docsListQuery,
  fileQuery,
  filesQuery,
  proposalQuery,
  proposalsQuery,
  skillQuery,
  skillsQuery,
  sourcesQuery,
  toolsQuery,
  typesQuery,
} from './queries';
import type { ContactsSearch, FilesSearch, SkillsSearch, ToolsSearch } from './search';
import { viewing } from './viewing';

// What the router and the routes use before a page's code loads: the address checks, the first reads and the skills
// helper's code. No components here: the first load imports this file, and a page comes through exports.ts.
export { takeHelperCode } from './helperCode';
export { webhookQuery } from './queries';
export {
  contactsSearch,
  docSearch,
  docsSearch,
  filesSearch,
  skillsSearch,
  toolsSearch,
  type ContactsSearch,
  type DocSearch,
  type DocsSearch,
  type FilesSearch,
  type SkillsSearch,
  type ToolsSearch,
} from './search';

/** A picture `files.view` opened is over the page: the root mounts FileViewerHost (and loads its code) from then on. */
export const useFileOpen = (): boolean => useSyncExternalStore(viewing.subscribe, () => viewing.get() !== null, () => false);

// What each page needs for its first paint. A read that fails shows the page's own fault state, so a loader never throws.
const quietly = (...reads: Promise<unknown>[]) =>
  Promise.all(reads.map((r) => r.catch(() => undefined))).then(() => undefined);

// Both Docs tabs show both counts.
export const loadDocs = (qc: QueryClient) => quietly(qc.ensureQueryData(docsListQuery), qc.ensureQueryData(sourcesQuery));
export const loadDoc = (qc: QueryClient, doc: string) => (doc === 'new' || doc.startsWith('ext:') ? quietly() : quietly(qc.ensureQueryData(docQuery(doc))));
export const loadFiles = (qc: QueryClient, s: Pick<FilesSearch, 'kind' | 'file'>) =>
  quietly(s.file ? qc.ensureQueryData(fileQuery(s.file)) : qc.ensureInfiniteQueryData(filesQuery(s.kind ?? '')));
export const loadSkills = (qc: QueryClient, s: Pick<SkillsSearch, 'skill' | 'proposal'>) =>
  quietly(
    s.proposal ? qc.ensureQueryData(proposalQuery(s.proposal)) : s.skill ? qc.ensureQueryData(skillQuery(s.skill)) : qc.ensureQueryData(skillsQuery),
    s.proposal || s.skill ? Promise.resolve() : qc.ensureQueryData(proposalsQuery),
  );
export const loadTools = (qc: QueryClient, s: Pick<ToolsSearch, 'tool'>) =>
  quietly(qc.ensureQueryData(toolsQuery), s.tool?.startsWith('api:') ? qc.ensureQueryData(apiToolQuery(s.tool.slice(4))) : Promise.resolve());
export const loadContacts = (qc: QueryClient, s: Pick<ContactsSearch, 'type' | 'q' | 'panel'> & { link?: string }) =>
  quietly(
    qc.ensureQueryData(typesQuery),
    qc.ensureInfiniteQueryData(contactsQuery(s.type ?? 'person', s.q ?? '', s.link ?? '')),
    s.panel ? qc.ensureQueryData(cardQuery(s.panel)) : Promise.resolve(),
  );
