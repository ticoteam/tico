import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateShots } from '../record.mjs';

const example = JSON.parse(readFileSync(new URL('../examples/shots.json', import.meta.url), 'utf8'));

test('the shipped shots file is valid', () => {
  assert.deepEqual(validateShots(example), []);
});

test('bad steps are reported with their position', () => {
  const errors = validateShots({
    steps: [
      { do: 'goto' },
      { do: 'dance' },
      { do: 'zoom', selector: '#x', scale: 9 },
      { do: 'wait' },
      { do: 'type', selector: '#name' },
    ],
  });
  assert.ok(errors.some((e) => e.startsWith('steps[0].url')));
  assert.ok(errors.some((e) => e.startsWith('steps[1].do')));
  assert.ok(errors.some((e) => e.startsWith('steps[2].scale')));
  assert.ok(errors.some((e) => e.startsWith('steps[3]')));
  assert.ok(errors.some((e) => e.startsWith('steps[4].text')));
});

test('zoom reset needs no selector; empty step lists are refused', () => {
  assert.deepEqual(validateShots({ steps: [{ do: 'zoom', reset: true }] }), []);
  assert.deepEqual(validateShots({ steps: [] }), ['steps must be a non-empty array']);
  assert.match(validateShots({ viewport: 'big', steps: [{ do: 'pause', ms: 1 }] })[0], /viewport/);
});
