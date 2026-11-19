// Library: Docs, a doc, Files, Skills, Tools and Contacts. The only file other code imports from this feature.
export { ContactsPage } from './ContactsPage';
export { DocPage } from './DocPage';
export { DocsPage } from './DocsPage';
export { FilesPage } from './FilesPage';
// A file anywhere: `files.view` shows a picture over the page (FileViewerHost, mounted once at the root) and opens any
// other file as its page; FilePreview draws one in place (a task's Details).
export { FilePreview, FileViewerHost } from './FileViewer';
export { SkillsPage } from './SkillsPage';
// The skills helper's code out of the address at startup (router.tsx), held for the Skills page.
export { takeHelperCode } from './helperCode';
export { loadContacts, loadDoc, loadDocs, loadFiles, loadSkills, loadTools } from './routing';
// An outgoing webhook's Tools page, read again after Routines changes it.
export { webhookQuery } from './queries';
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
