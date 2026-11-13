// No browser: a bot page's Runs card reads that bot's own runs (GET /api/runs?bot=<slug>, backend/views.py runs), not
// the company's newest 200 filtered in the page. On Nov 12 a busy company's newer runs pushed every failed start of
// Performance Owner off its card. Its cached list is per bot, so opening another bot within the minute reads again.
// Run: node ui/tests/bot-runs-unit.cts
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {stripTypeScriptTypes} = require('node:module') as {stripTypeScriptTypes: (code: string) => string};

const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'bot-page.ts'), 'utf8');
const from = source.indexOf('interface BotRunsCache'), to = source.indexOf('// -----', from);
assert.ok(from > 0 && to > from, 'bot-page.ts keeps its runs cache before the bot page section');

const asked: string[] = [];
const ctx = vm.createContext({
  Date,
  get: async (url: string) => { asked.push(url); return [{id: 'r-' + asked.length, employee: 'ops'}]; },
});
vm.runInContext(stripTypeScriptTypes(source.slice(from, to)), ctx, {filename: 'bot-page.ts'});
const recentRuns = vm.runInContext('recentRuns', ctx) as (slug: string) => Promise<{id: string}[]>;

(async () => {
  const first = await recentRuns('performance owner');
  assert.deepEqual(asked, ['/runs?bot=performance%20owner'], 'the page asks for its own bot\'s runs');
  assert.equal((await recentRuns('performance owner'))[0]!.id, first[0]!.id, 'the same bot within the minute is cached');
  await recentRuns('ops');
  assert.deepEqual(asked.slice(1), ['/runs?bot=ops'], 'another bot reads its own runs, not the first bot\'s cache');
  console.log('bot-runs-unit: ok');
})().catch(e => { console.error(e); process.exit(1); });
