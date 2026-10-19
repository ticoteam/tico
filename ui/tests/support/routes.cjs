// The answer a script's fake server gives a path it does not route itself: [] where the real server always sends a
// list, {} for the rest. A catch-all {} for /api/issues crashed the sidebar draw whenever it landed before
// /api/v2/status, and the bot page never opened (bot-needs.cjs, v0.3.54 release check).
// Lists: GET /api/issues and /api/runs (backend/views.py legacy_issues, runs), GET /api/v2/bots (backend/app.py bots).
const LISTS = new Set(['/api/issues', '/api/runs', '/api/v2/bots']);
exports.LISTS = LISTS;
exports.unrouted = p => LISTS.has(p) ? [] : {};
