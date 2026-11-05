// The Assistant page's chat for scripts that used to open a bot's Chat tab (gone: a bot page is Home and DM now).
// The Assistant (#/assistant) still runs the old chat engine (ui/app/chat.js, pill.js) on its own room: the page asks
// GET /api/v2/assistant for the room and its newest messages, and sends to POST /api/v2/assistant/messages. A script
// routes those two itself and uses assistantView to build the first from a conversation snapshot. Its bot is 'coo'.
exports.BOT = 'coo';
exports.assistantView = (room, snapshot = {}) => ({available: true, state: 'active', bot: exports.BOT, name: 'Assistant',
  can_turn_on: false, room_id: room, messages: snapshot.messages || [], has_more: !!snapshot.next_before,
  next_before: snapshot.next_before || null, execution: snapshot.execution || null, actions: {}, pending: []});
