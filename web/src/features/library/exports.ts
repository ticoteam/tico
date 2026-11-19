// Library: Docs, a doc, Files, Skills, Tools and Contacts. The only file other code imports from this feature.
import type { QueryClient } from '@tanstack/react-query';
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

export { ContactsPage } from './ContactsPage';
export { DocPage } from './DocPage';
export { DocsPage } from './DocsPage';
export { FilesPage } from './FilesPage';
// A file anywhere: `files.view` opens it over the page (FileViewerHost mounted once at the root shows it);
// FilePreview draws one in place (a task's Details).
export { FilePreview, FileViewer, FileViewerHost } from './FileViewer';
export { SkillsPage } from './SkillsPage';
export { ToolsPage } from './ToolsPage';
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
export const loadContacts = (qc: QueryClient, s: Pick<ContactsSearch, 'type' | 'q' | 'panel'>) =>
  quietly(
    qc.ensureQueryData(typesQuery),
    qc.ensureInfiniteQueryData(contactsQuery(s.type ?? 'person', s.q ?? '')),
    s.panel ? qc.ensureQueryData(cardQuery(s.panel)) : Promise.resolve(),
  );
