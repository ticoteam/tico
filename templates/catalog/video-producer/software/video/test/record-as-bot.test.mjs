import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { botHeaders, checkBotCanOpenApp, hubAuth, resolveStepUrl } from '../record.mjs';

const hub = { origin: 'https://hub.tico.team', token: 'bot-token-123' };

test('hubAuth reads the origin and the token; the origin ignores any path', () => {
  assert.deepEqual(hubAuth({ HUB_API_URL: 'https://hub.tico.team/', HUB_TOKEN: 'abc' }), {
    origin: 'https://hub.tico.team',
    token: 'abc',
  });
});

test('hubAuth reads a token from a tico-file: path and trims it', () => {
  const dir = mkdtempSync(join(tmpdir(), 'vp-record-test-'));
  try {
    const file = join(dir, 'token');
    writeFileSync(file, '  file-token\n');
    assert.deepEqual(hubAuth({ HUB_API_URL: 'https://hub.tico.team', HUB_TOKEN: `tico-file:${file}` }), {
      origin: 'https://hub.tico.team',
      token: 'file-token',
    });
    writeFileSync(file, '  \n');
    assert.throws(() => hubAuth({ HUB_API_URL: 'https://hub.tico.team', HUB_TOKEN: `tico-file:${file}` }), /is empty/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('hubAuth names the missing setting, and a token file that cannot be read', () => {
  assert.throws(() => hubAuth({ HUB_TOKEN: 'abc' }), /HUB_API_URL/);
  assert.throws(() => hubAuth({ HUB_API_URL: 'https://hub.tico.team' }), /HUB_TOKEN/);
  assert.throws(() => hubAuth({ HUB_API_URL: 'https://hub.tico.team', HUB_TOKEN: '   ' }), /HUB_TOKEN/);
  assert.throws(() => hubAuth({ HUB_API_URL: 'hub.tico.team', HUB_TOKEN: 'abc' }), /not a URL/);
  assert.throws(
    () => hubAuth({ HUB_API_URL: 'https://hub.tico.team', HUB_TOKEN: 'tico-file:/nonexistent/vp-token' }),
    /cannot read the bot token file/,
  );
});

test('the bot token goes only to the Tico origin', () => {
  const same = botHeaders('https://hub.tico.team/tasks?x=1', hub, { accept: 'text/html' });
  assert.deepEqual(same, { accept: 'text/html', authorization: 'Bearer bot-token-123' });

  assert.equal(botHeaders('https://hub.tico.team.evil.example/tasks', hub, {}), null);
  assert.equal(botHeaders('https://evil.example/?next=https://hub.tico.team', hub, {}), null);
  assert.equal(botHeaders('http://hub.tico.team/tasks', hub, {}), null);
  assert.equal(botHeaders('https://hub.tico.team:8443/tasks', hub, {}), null);
  assert.equal(botHeaders('not a url', hub, {}), null);
  assert.equal(botHeaders('https://hub.tico.team/tasks', null, {}), null);
});

test('without --as-bot a leading slash is still a file next to the shots file', () => {
  assert.equal(resolveStepUrl('site/index.html', '/work/demo'), pathToFileURL('/work/demo/site/index.html').href);
  assert.equal(resolveStepUrl('/tasks', '/work/demo'), pathToFileURL('/work/demo/tasks').href);
  assert.equal(resolveStepUrl('https://example.com/a', '/work/demo'), 'https://example.com/a');
  assert.equal(resolveStepUrl('file:///x/y.html', '/work/demo'), 'file:///x/y.html');
});

test('with --as-bot a leading slash resolves against the Tico origin', () => {
  assert.equal(resolveStepUrl('/tasks', '/work/demo', hub.origin), 'https://hub.tico.team/tasks');
  assert.equal(resolveStepUrl('/tasks/42?tab=log', '/work/demo', hub.origin), 'https://hub.tico.team/tasks/42?tab=log');
  assert.equal(resolveStepUrl('site/index.html', '/work/demo', hub.origin), pathToFileURL('/work/demo/site/index.html').href);
  assert.equal(resolveStepUrl('https://example.com/a', '/work/demo', hub.origin), 'https://example.com/a');
});

test('--as-bot stops before any browser when the web app will not open for a bot', async () => {
  const seen = [];
  const personOnly = async (url, init) => {
    seen.push([String(url), init.headers.authorization]);
    return { ok: false, status: 403 };
  };
  await assert.rejects(checkBotCanOpenApp(hub, personOnly), /needs a person/);
  assert.deepEqual(seen, [['https://hub.tico.team/api/me', 'Bearer bot-token-123']]);
});

test('--as-bot passes a bot that can open the web app, and names other failures', async () => {
  await checkBotCanOpenApp(hub, async () => ({ ok: true, status: 200 }));
  await assert.rejects(checkBotCanOpenApp(hub, async () => ({ ok: false, status: 401 })), /answered 401/);
  await assert.rejects(checkBotCanOpenApp(hub, async () => { throw new TypeError('fetch failed'); }), /cannot reach/);
});
