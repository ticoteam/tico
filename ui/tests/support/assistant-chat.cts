// The Assistant page's chat for scripts that used to open a bot's Chat tab (gone: a bot page is Home and DM now).
// The Assistant (#/assistant) still runs the old chat engine (ui/app/chat.ts, pill.js) on its own room: the page asks
// GET /api/v2/assistant for the room and its newest messages, and sends to POST /api/v2/assistant/messages. A script
// routes those two itself and uses assistantView to build the first from a conversation snapshot. Its bot is 'assistant'.
const BOT = 'assistant';
// What a script passes as the conversation snapshot: its newest messages, the cursor for older ones, and an execution.
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
const assistantView = (room: string, snapshot: AssistantSnapshot = {}): AssistantView => ({available: true, state: 'active',
  bot: BOT, name: 'Assistant', can_turn_on: false, room_id: room, messages: snapshot.messages || [],
  has_more: !!snapshot.next_before, next_before: snapshot.next_before || null, execution: snapshot.execution || null,
  actions: {}, pending: []});
const api = {BOT, assistantView};
export type Api = typeof api;
module.exports = api;
