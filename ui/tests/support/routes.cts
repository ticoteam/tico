// The answer a script's fake server gives a path it does not route itself: [] where the real server always sends a
// list, {} for the rest. A catch-all {} for /api/issues crashed the sidebar draw whenever it landed before
// /api/v2/status, and the bot page never opened (bot-needs.cjs, v0.3.54 release check).
// Lists: GET /api/issues and /api/runs (backend/views.py legacy_issues, runs), GET /api/v2/bots (backend/app.py bots).
const LISTS = new Set<string>(['/api/issues', '/api/runs', '/api/v2/bots']);
// Pages that draw a fixed shape: Settings opens on General, which draws the owner's Storage card
// (backend/company_storage.py), so a script that routes neither still opens without a crash.
const SHAPES: Record<string, () => Record<string, unknown>> = {
  '/api/v2/system/storage': () => ({active: null, earlier: [], server: {mode: 'local', location: ''},
    copy: {done: 0, total: 0, failed: 0, running: false}}),
};
const unrouted = (p: string): unknown[] | Record<string, unknown> => LISTS.has(p) ? [] : SHAPES[p] ? SHAPES[p]() : {};
const api = {LISTS, unrouted};
export type Api = typeof api;
module.exports = api;
