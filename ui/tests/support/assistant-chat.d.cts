// Types for support/assistant-chat.cjs (the helper stays JavaScript: scripts require it by its .cjs path).
export declare const BOT: string;
export interface AssistantSnapshot {
  messages?: unknown[];
  next_before?: string | null;
  execution?: unknown;
}
export interface AssistantView {
  available: boolean;
  state: string;
  bot: string;
  name: string;
  can_turn_on: boolean;
  room_id: string;
  messages: unknown[];
  has_more: boolean;
  next_before: string | null;
  execution: unknown;
  actions: Record<string, unknown>;
  pending: unknown[];
}
export declare function assistantView(room: string, snapshot?: AssistantSnapshot): AssistantView;
