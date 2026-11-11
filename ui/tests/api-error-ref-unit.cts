// No browser: a refused setup step's message ends with its reference, and only a reference the server made is shown.
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const vm: typeof import('node:vm') = require('node:vm');
const path: typeof import('node:path') = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../../build/ui/app/api.js'), 'utf8');
// Only the message helpers: the rest of the file starts requests and timers as it loads.
const start = source.indexOf('const apiErrorRaw'), end = source.indexOf('const apiFailure');
assert.ok(start > 0 && end > start, 'the message helpers are where this test reads them');
const context = vm.createContext({});
vm.runInContext(`${source.slice(start, end)};globalThis.apiError = apiError;`, context);
const apiError = (context as unknown as {apiError(body: unknown, response: {statusText: string}): string}).apiError;
const answer = {statusText: 'Bad Request'};

assert.equal(apiError({error: {code: 'github_state', detail: 'This link has expired', ref: 'sf-1a2b3c4d'}}, answer),
  'This link has expired · sf-1a2b3c4d');
assert.equal(apiError({error: {code: 'github_state', detail: 'This link has expired'}}, answer), 'This link has expired');
// Anything that is not the server's reference shape is not shown.
assert.equal(apiError({error: {code: 'x', detail: 'No', ref: '<b>sf-1a2b3c4d</b>'}}, answer), 'No');
assert.equal(apiError({error: 'task_rule', detail: 'One line', ref: 'sf-1a2b3c4d'}, answer), 'One line');
console.log('api-error-ref-unit: ok');
