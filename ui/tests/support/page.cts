// The page the browser tests serve: the server's own, build/ui/index.html (scripts/build-ui.cjs, which
// scripts/ui-tests.cts runs first), and uiFile() for the files a test serves from disk.
require('./browser.cts');
const {execFileSync} = require('node:child_process') as typeof import('node:child_process');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const ui = path.join(__dirname, '../..');
const built = path.join(ui, '..', 'build', 'ui');
const pageHtml = fs.readFileSync(path.join(built, 'index.html'), 'utf8');
// The harness catalog GET /v2/models sends, with each CLI's capabilities read from runner-rs/harnesses/*.toml, so a
// test's mock answers what the server would.
// Read on first use: most scripts never ask, and starting Python costs every one of them.
export interface Harness {
  id: string;
  label: string;
  runtime: string;
  name: string;
  capabilities: Record<string, unknown>;
}
// One Beta feature: its id and name, its manual pages (`page.md#heading`), and the bots that are the feature.
export interface BetaFeature {
  id: string;
  name: string;
  docs: string[];
  [bot: string]: unknown;
}
let harnesses: Harness[] | undefined;
// The Beta list GET /v2/config sends (backend/beta.py), read the same way.
let beta: BetaFeature[] | undefined;
const TYPES: Record<string, string> = {'.js': 'application/javascript', '.ts': 'application/javascript', '.css': 'text/css', '.html': 'text/html',
  '.json': 'application/json', '.webmanifest': 'application/manifest+json', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.woff2': 'font/woff2', '.map': 'application/json'};
// A route as the browser tests use it: only fulfil is needed here.
export interface FulfilRoute {
  fulfill(options: {status?: number; contentType?: string; body?: string | Buffer; path?: string}): Promise<void>;
}
const api = {
  // The shell's bundle scripts, in the order the page runs them (names under /tico/ui/).
  shell: [...pageHtml.matchAll(/<script src="\/tico\/ui\/(app\.bundle\.[0-9a-f]{16}\.js)"/g)].map(m => m[1]!),
  get harnesses(): Harness[] {
    return harnesses ||= JSON.parse(execFileSync(
      process.env.TICO_PYTHON || 'python3',
      ['-c', 'import json; from backend.harnesses import HARNESS_CATALOG; print(json.dumps(HARNESS_CATALOG))'],
      {cwd: path.join(ui, '..')}).toString()) as Harness[];
  },
  get beta(): BetaFeature[] {
    return beta ||= JSON.parse(execFileSync(
      process.env.TICO_PYTHON || 'python3', ['-c', 'import json; from backend.beta import listing; print(json.dumps(listing()))'],
      {cwd: path.join(ui, '..')}).toString()) as BetaFeature[];
  },
  html: pageHtml,
  // A path under ui/ (what follows /tico/ui/ in a URL) to the file on disk: a bundle file is in build/ui/bundle, and a
  // TypeScript file is served as its compiled JavaScript in build/ui (npm run build:ui).
  uiFile: (name: string): string => name.startsWith('app.bundle.') ? path.join(built, 'bundle', name)
    : name.endsWith('.ts') ? path.join(built, name.replace(/\.ts$/, '.js')) : path.join(ui, name),
  // The files a page loads from the hub's own paths, for a test's route handler. theme.ts shows the recovery card when
  // any script fails to load, so a /tico/ui/ file the handler does not serve (a JSON or 404 answer) covers the page.
  // Every file under /tico/ui/ is served from uiFile (a .ts file as its compiled script) and every file under /vendor/ from
  // ui/vendor, each with its type from the extension. A .js script that is not on disk is an empty script, so a page's
  // fallback stubs still load; any other missing file is a 404, which shows a .ts source that was not built.
  isUiPath: (pathname: string): boolean => pathname.startsWith('/tico/ui/') || pathname.startsWith('/vendor/'),
  serveUi: async (route: FulfilRoute, pathname: string): Promise<void> => {
    const isVendor = pathname.startsWith('/vendor/');
    const name = pathname.slice(isVendor ? 1 : '/tico/ui/'.length);
    const type = TYPES[path.extname(name)] || 'application/octet-stream';
    if (name.split('/').includes('..')) return route.fulfill({status: 404, body: ''});
    const file = isVendor ? path.join(ui, name) : api.uiFile(name);
    if (fs.existsSync(file)) return route.fulfill({contentType: type, body: fs.readFileSync(file)});
    if (type === 'application/javascript' && !name.endsWith('.ts')) return route.fulfill({contentType: type, body: ''});
    return route.fulfill({status: 404, body: ''});
  },
};
export type Api = typeof api;
module.exports = api;
