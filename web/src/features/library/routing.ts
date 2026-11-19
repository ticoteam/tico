import type { QueryClient } from '@tanstack/react-query';
import { useSyncExternalStore } from 'react';
import {
  apiToolQuery,
  archivedDocsQuery,
  cardQuery,
  contactsQuery,
  docQuery,
  docsListQuery,
  externalDocAtQuery,
  externalDocQuery,
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
import type { ContactsSearch, DocsSearch, FilesSearch, SkillsSearch, ToolsSearch } from './search';
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

// What each page draws first, read before it draws: a read that fails is the router's fault page (no access on a 403).
// A read the first paint can do without, or one with its own fault in its place (an open tool, a card), only starts.

// Both Docs tabs show both counts.
export const loadDocs = (qc: QueryClient, s: Pick<DocsSearch, 'tab' | 'archived'>) => {
  if (s.tab === 'sources') {
    void qc.prefetchInfiniteQuery(docsListQuery);
    return qc.ensureQueryData(sourcesQuery);
  }
  void qc.prefetchQuery(sourcesQuery);
  return qc.ensureInfiniteQueryData(s.archived ? archivedDocsQuery : docsListQuery);
};
export const loadDoc = (qc: QueryClient, doc: string, revision: string | undefined) => {
  if (doc === 'new') return;
  if (!doc.startsWith('ext:')) return qc.ensureQueryData(docQuery(doc));
  const id = doc.slice(4);
  return qc.ensureQueryData(revision ? externalDocAtQuery(id, revision) : externalDocQuery(id));
};
export const loadFiles = (qc: QueryClient, s: Pick<FilesSearch, 'kind' | 'file'>) =>
  s.file ? qc.ensureQueryData(fileQuery(s.file)) : qc.ensureInfiniteQueryData(filesQuery(s.kind ?? ''));
export const loadSkills = (qc: QueryClient, s: Pick<SkillsSearch, 'skill' | 'proposal'>) => {
  if (s.proposal) return qc.ensureQueryData(proposalQuery(s.proposal));
  if (s.skill) return qc.ensureQueryData(skillQuery(s.skill));
  // Waiting changes decide the empty state, so they come in with the list (a failure leaves them out).
  return Promise.all([qc.ensureQueryData(skillsQuery), qc.prefetchQuery(proposalsQuery)]);
};
export const loadTools = (qc: QueryClient, s: Pick<ToolsSearch, 'tool'>) =>
  Promise.all([qc.ensureQueryData(toolsQuery), s.tool?.startsWith('api:') && qc.prefetchQuery(apiToolQuery(s.tool.slice(4)))]);
// The tabs and an open card come in after the list.
export const loadContacts = (qc: QueryClient, s: Pick<ContactsSearch, 'type' | 'q' | 'panel'> & { link?: string }) => {
  void qc.prefetchQuery(typesQuery);
  if (s.panel) void qc.prefetchQuery(cardQuery(s.panel));
  return qc.ensureInfiniteQueryData(contactsQuery(s.type ?? 'person', s.q ?? '', s.link ?? ''));
};
