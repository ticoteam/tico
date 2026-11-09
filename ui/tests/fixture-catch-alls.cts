// Offline check, no browser: a script's fake server ends its routes with unrouted(p) (support/routes.cts), never a
// bare {}. A {} for /api/issues, where the real server always sends a list, hung bot-needs.cts whenever it landed
// before /api/v2/status (v0.3.54 release check).
import type * as Routes from './support/routes.cts';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {unrouted} = require('./support/routes.cts') as Routes.Api;

assert.deepEqual(unrouted('/api/issues'), []);
assert.deepEqual(unrouted('/api/runs'), []);
assert.deepEqual(unrouted('/api/v2/bots'), []);
assert.deepEqual(unrouted('/api/v2/bots/ops/tools'), {});

const bare: string[] = [];
// Scripts are .cjs or .cts; declaration files (.d.cts) hold no route.
for (const name of fs.readdirSync(__dirname).filter((f: string) => /\.(cjs|cts)$/.test(f) && !f.endsWith('.d.cts')).sort()) {
  const lines: string[] = fs.readFileSync(path.join(__dirname, name), 'utf8').split('\n');
  lines.forEach((line: string, i: number) => {
    if (/^\s*return json\(\{\}\);\s*$/.test(line) && /^\s*\}\);/.test(lines[i + 1] || '')) bare.push(`${name}:${i + 1}`);
  });
}
assert.deepEqual(bare, [], 'end the route with json(unrouted(p)), not json({})');
console.log('fixture catch-alls ok');
