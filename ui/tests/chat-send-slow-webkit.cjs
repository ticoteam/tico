// chat-send-slow.cjs on WebKit, the engine of mobile Safari, where the stalled send was seen. A script of its own so
// each engine's run fits the browser runner's time per script. TICO_UI_ENGINES=chromium runs Chromium instead where
// WebKit's system libraries are missing.
process.env.TICO_UI_ENGINES = process.env.TICO_UI_ENGINES || 'webkit';
require('./chat-send-slow.cjs');
