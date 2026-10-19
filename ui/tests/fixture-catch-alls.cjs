// Offline check, no browser: a script's fake server ends its routes with unrouted(p) (support/routes.cjs), never a
// bare {}. A {} for /api/issues, where the real server always sends a list, hung bot-needs.cjs whenever it landed
// before /api/v2/status (v0.3.54 release check).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {unrouted} = require('./support/routes.cjs');

assert.deepEqual(unrouted('/api/issues'), []);
assert.deepEqual(unrouted('/api/runs'), []);
assert.deepEqual(unrouted('/api/v2/bots'), []);
assert.deepEqual(unrouted('/api/v2/bots/ops/tools'), {});

const bare = [];
for (const name of fs.readdirSync(__dirname).filter(f => f.endsWith('.cjs')).sort()) {
  const lines = fs.readFileSync(path.join(__dirname, name), 'utf8').split('\n');
  lines.forEach((line, i) => {
    if (/^\s*return json\(\{\}\);\s*$/.test(line) && /^\s*\}\);/.test(lines[i + 1] || '')) bare.push(`${name}:${i + 1}`);
  });
}
assert.deepEqual(bare, [], 'end the route with json(unrouted(p)), not json({})');
console.log('fixture catch-alls ok');
