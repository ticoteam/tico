import { lazy } from 'react';

// One open task, everywhere it appears. The only file other code imports from this feature.
export { TaskPage, TaskById, type TaskPageProps, type TaskChatProps } from './TaskPage';
export const TaskChat = lazy(() => import('./TaskPage').then((m) => ({ default: m.TaskChat })));
export { loadTask, taskRefQuery, taskDetailQuery, taskAddress, taskSearch, type TaskSearch } from './routing';
export { useApprovalCards, approvalCard, type ApprovalScope } from './ThreadCards';
export { AskCard } from './AskCard';
export { SendMark, TaskSend } from './sending';
export { RequestCard } from './Requests';
