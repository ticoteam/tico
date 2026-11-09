// The page the browser tests serve: ui/index.html as the server sends it, with its bundle regions replaced by
// the two bundle tags (backend/ui_bundle.py builds them, so the tests run the real bundler), and uiFile() for
// the files a test serves from disk. TICO_UI_BUNDLE=off serves the separate files as index.html lists them.
require('./browser.cjs');
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const ui = path.join(__dirname, '../..');
const bundled = !['off', '0', 'false', 'no'].includes((process.env.TICO_UI_BUNDLE || 'on').toLowerCase());
let out = null;
if (bundled) {
  out = fs.mkdtempSync(path.join(os.tmpdir(), 'tico-ui-bundle-'));
  execFileSync(process.env.TICO_PYTHON || 'python3', ['-m', 'backend.ui_bundle', ui, out], {cwd: path.join(ui, '..')});
  process.on('exit', () => { try { fs.rmSync(out, {recursive: true, force: true}); } catch {} });
}
exports.bundled = bundled;
// The harness catalog GET /v2/models sends, with each CLI's capabilities read from runner-rs/harnesses/*.toml, so a
// test's mock answers what the server would.
// Read on first use: most scripts never ask, and starting Python costs every one of them.
let harnesses;
Object.defineProperty(exports, 'harnesses', {enumerable: true, get: () => harnesses ||= JSON.parse(execFileSync(
  process.env.TICO_PYTHON || 'python3',
  ['-c', 'import json; from backend.harnesses import HARNESS_CATALOG; print(json.dumps(HARNESS_CATALOG))'],
  {cwd: path.join(ui, '..')}).toString())});
// The Beta list GET /v2/config sends (backend/beta.py), read the same way.
let beta;
Object.defineProperty(exports, 'beta', {enumerable: true, get: () => beta ||= JSON.parse(execFileSync(
  process.env.TICO_PYTHON || 'python3', ['-c', 'import json; from backend.beta import listing; print(json.dumps(listing()))'],
  {cwd: path.join(ui, '..')}).toString())});
exports.html = fs.readFileSync(path.join(out || ui, 'index.html'), 'utf8');
// A path under ui/ (what follows /tico/ui/ in a URL) to the file on disk; the bundles live in the temp folder, and a
// TypeScript file is served as its compiled JavaScript (npm run build:ui, backend/ui_build.py).
exports.uiFile = name => out && /^app\.bundle(?:\.\d+)?\.(?:js|css)$/.test(name) ? path.join(out, name)
  : name.endsWith('.ts') ? path.join(ui, '..', 'build', 'ui', name.replace(/\.ts$/, '.js')) : path.join(ui, name);
// The files a page loads from the hub's own paths, for a test's route handler. theme.ts shows the recovery card when
// any script fails to load, so a /tico/ui/ file the handler does not serve (a JSON or 404 answer) covers the page.
// Every file under /tico/ui/ is served from uiFile (a .ts file as its compiled script) and every file under /vendor/ from
// ui/vendor, each with its type from the extension. A .js script that is not on disk is an empty script, so a page's
// fallback stubs still load; any other missing file is a 404, which shows a .ts source that was not built.
const TYPES = {'.js': 'application/javascript', '.ts': 'application/javascript', '.css': 'text/css', '.html': 'text/html',
  '.json': 'application/json', '.webmanifest': 'application/manifest+json', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.woff2': 'font/woff2', '.map': 'application/json'};
exports.isUiPath = pathname => pathname.startsWith('/tico/ui/') || pathname.startsWith('/vendor/');
exports.serveUi = (route, pathname) => {
  const isVendor = pathname.startsWith('/vendor/');
  const name = pathname.slice(isVendor ? 1 : '/tico/ui/'.length);
  const type = TYPES[path.extname(name)] || 'application/octet-stream';
  if (name.split('/').includes('..')) return route.fulfill({status: 404, body: ''});
  const file = isVendor ? path.join(ui, name) : exports.uiFile(name);
  if (fs.existsSync(file)) return route.fulfill({contentType: type, body: fs.readFileSync(file)});
  if (type === 'application/javascript' && !name.endsWith('.ts')) return route.fulfill({contentType: type, body: ''});
  return route.fulfill({status: 404, body: ''});
};
