// Threads, the menu's Threads list, a team's thread, the second chat, Mail, Messaging and Updates. The only file other
// code imports from this feature.
export { ThreadsPage, type ThreadsPageProps } from './ThreadsPage';
export { ThreadsIndex } from './ThreadPane';
export { MailPage, MessagingPage, type MailPageProps, type MessagingPageProps } from './MailPage';
export { UpdatesPage, type UpdatesPageProps } from './UpdatesPage';
export type { ChatSheetProps } from './ChatSheet';
export { LearnedPage } from './LearnedPage';
export { loadThreads, updatesSearch, loadUpdates, loadLearned, type UpdatesSearch } from './routing';
export type { MenuThreadsProps } from './MenuThreads';
export type { MessagePickProps } from './MessagePick';

export const ChatSheet = lazy(() => import('./ChatSheet').then((m) => ({ default: m.ChatSheet })));
export const MenuThreads = lazy(() => import('./MenuThreads').then((m) => ({ default: m.MenuThreads })));
export const MessagePick = lazy(() => import('./MessagePick').then((m) => ({ default: m.MessagePick })));
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
import { lazy } from 'react';
