// One conversation for Tico's sheet, a second chat, a thread, a task's chat and an agent's page.
import './conversation.css';

export { Conversation, type ConversationProps } from './Conversation';
export { Composer, type ComposerProps } from './Composer';
export { Message, DidLine, LiveRow, type MessageProps, type DidLineProps } from './Message';
export { Outline, jumpTo, type OutlineProps } from './Outline';
export { timelineQuery, ticoRoomQuery, sendMessage, markRead } from './queries';
export type { Variant, ChatMessage, PageRef, LiveRun } from './types';
