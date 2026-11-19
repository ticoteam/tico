// Threads, the second chat, Mail, Messaging and Updates. The only file other code imports from this feature.
export { ThreadsPage, type ThreadsPageProps } from './ThreadsPage';
export { MailPage, MessagingPage, type MailPageProps, type MessagingPageProps } from './MailPage';
export { UpdatesPage, type UpdatesPageProps } from './UpdatesPage';
export { ChatSheet, type ChatSheetProps } from './ChatSheet';
export {
  threadListQuery,
  threadQuery,
  catchUpQuery,
  chatQuery,
  mailboxesQuery,
  textListQuery,
  updateListQuery,
  updateQuery,
  type UpdateTab,
} from './queries';

/** Words the thread address keeps for pages of its own: `/threads/new`, `/threads/catch-up`, the Mail and Messaging tabs. */
export const THREAD_PAGES = ['new', 'catch-up', 'mail', 'messaging'] as const;
