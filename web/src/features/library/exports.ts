// Library: Docs, a doc, Files, Skills, Tools and Contacts. The only file other code imports from this feature.
export { ContactsPage } from './ContactsPage';
export { DocPage } from './DocPage';
export { DocsPage } from './DocsPage';
export { FilesPage } from './FilesPage';
// A file anywhere: `files.view` shows a picture over the page (FileViewerHost, mounted once at the root) and opens any
// other file as its page; FilePreview draws one in place (a task's Details), FileFrame one whose type is known (a
// task's file at a version, an email's attachment).
export { FileFrame, FilePreview, FileViewerHost } from './FileViewer';
// A file's record by its id (its name for a task's tab when the file is another task's), read under the file's own rule.
export { fileQuery } from './queries';
// "Signed by Ana Example · Nov 20, 5:40 AM", for a version a person's signing made (a task's file bar).
export { signedLine } from './signing';
export { size as fileSize } from './parts';
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
