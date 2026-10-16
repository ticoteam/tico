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
// The harness catalog GET /v2/models sends, with each CLI's capabilities read from runner/harnesses/*.toml, so a
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
// A path under ui/ (what follows /tico/ui/ in a URL) to the file on disk; the bundles live in the temp folder.
exports.uiFile = name => out && (name === 'app.bundle.js' || name === 'app.bundle.css') ? path.join(out, name) : path.join(ui, name);
