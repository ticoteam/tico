import { lazy } from 'react';

// One open task, everywhere it appears. The only file other code imports from this feature.
/** A task open over any page (`?task=`): drawn by the root, its code (the task's page, its chat, its files) loaded the
    first time one opens. */
export const TaskModal = lazy(() => import('./TaskModal').then((m) => ({ default: m.TaskModal })));
export { loadTask, taskRefQuery, taskDetailQuery, taskAddress } from './routing';
export { useApprovalCards, approvalCard, type ApprovalScope } from './ThreadCards';
export { AskCard } from './AskCard';
export { ChatQuestions } from './ChatQuestions';
export { SendMark, TaskSend } from './sending';
export { RequestCard } from './Requests';
export { QuestionCard } from './QuestionCard';
export { CardFix, lineFor, StatusLine } from './StatusLine';
