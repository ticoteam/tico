// The Tools owner's chat and its key drop (ui/app/tools-key-drop.ts, secret-drop.ts; spec tools-chat-key-drop), on
// Tools over the mailboxes fake and the threads fake, with the real rule file (backend/secret_rules.json):
//  - AC-1 Chat opens the Tools Product Owner's direct chat beside Tools; its empty state names the tools in Problem;
//  - AC-2 a paste of a login and a GitHub token gives words in the box and two items (dots for the password, first 3 +
//    last 3 for the token); neither value is in the box, the DOM, local or session storage, or the console;
//  - AC-14 a name already in Credentials waits for Replace or Rename;
//  - AC-10 Send saves each value to Credentials (source tools-chat) and sends only names; the proof line says so;
//  - AC-12 a failed save sends nothing and says why; Send again saves only what failed, then sends once;
//  - AC-6 typed "token: <value>" is locked within a second, and no draft is kept;
//  - AC-7 a dropped file is refused with one line;
//  - AC-13 someone who is not a credential admin can't send keys, and is told who can;
//  - AC-8 Mark as secret turns the last word into an item, on a 360 px phone too, with no sideways scroll;
//  - AC-16, AC-18 the owner's proposal card: a row removed is not granted; Grant shows each key's check, a failure
//    with Paste a new key and the tool's settings.
// TICO_UI_SHOTS=<dir> keeps a screenshot of the chat with items at 1280 and 360.
// Run: node scripts/ui-tests.cts tools-chat-keys
import type {Page} from 'playwright';
import type * as MailboxesFake from './support/mailboxes-fake.cts';
import type * as GoogleFake from './support/google-fake.cts';
import type * as ThreadsFakeSupport from './support/threads-fake.cts';
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {mailboxesFake, open} = require('./support/mailboxes-fake.cts') as MailboxesFake.Api;
const {googleFake} = require('./support/google-fake.cts') as GoogleFake.Api;
const {threadsFake} = require('./support/threads-fake.cts') as ThreadsFakeSupport.Api;

const OWNER = 'tools-product-owner';
const RULES: unknown = JSON.parse(fs.readFileSync(path.join(__dirname, '../../backend/secret_rules.json'), 'utf8'));
// Made-up values, joined here so this file holds no key-shaped text.
const PASSWORD = ['Tr0ub', '4dor&3'].join('');
const TOKEN = ['gh', 'p_', 'aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3z'].join('');
const TYPED = ['abcd', '1234efgh'].join('');
const ROWS = [
  {id: 'api:jira', kind: 'api', name: 'Jira', detail: 'acme.atlassian.example', status: {tone: 'fail', text: 'Problem'}, who: [], who_total: 0, href: '#/tools/api/jira'},
  {id: 'api:posthog', kind: 'api', name: 'PostHog', detail: 'eu.posthog.example', status: {tone: 'fail', text: 'Problem'}, who: [], who_total: 0, href: '#/tools/api/posthog'},
  {id: 'github', kind: 'github', name: 'GitHub', detail: '', status: {tone: 'ok', text: 'Connected'}, who: [], who_total: 0, href: '#/tools/github'},
];
type Saved = {path: string; key: string; body: Record<string, unknown>};
type Sent = ThreadsFakeSupport.ThreadsPost;

const CARD = {id: 'kg-1', sender_id: 'bot:' + OWNER, kind: 'notice', body: 'Proposal: JIRA_LOGIN → Jira; POSTHOG_API_KEY → PostHog; NPM_TOKEN → npm',
  refs: {card: 'key_grant', key_grant: {id: 'g1', state: 'proposed', results: [], rows: [
    {key: 'JIRA_LOGIN', tool_id: 'jira', tool_name: 'Jira', level: 'read_write', bots: [{id: 'bot:support', name: 'Support'}, {id: 'bot:builder', name: 'Builder'}]},
    {key: 'POSTHOG_API_KEY', tool_id: 'posthog', tool_name: 'PostHog', level: 'read', bots: [{id: 'bot:analyst', name: 'Product Analyst'}],
      replaces: {key: 'PostHog read', keeps: ['Support', 'Builder'], loses: ['Every computer']}},
    {key: 'NPM_TOKEN', tool_id: 'npm', tool_name: 'npm', level: 'read', bots: [{id: 'bot:builder', name: 'Builder'}]}]}}};
async function start(browser: import('playwright').Browser, {width = 1280, admin = true, failSaves = 0, card = false} = {}) {
  const saved: Saved[] = [], sent: Sent[] = [], grants: unknown[] = [];
  let failures = failSaves;
  const threads = threadsFake({bot: OWNER, me: 'human:ana', sent, messages: card ? [CARD] : []});
  const got = await open(browser, mailboxesFake(), googleFake(), {hash: '#/tools', width, height: width < 500 ? 780 : 860, ready: '.tt-row, [data-tl-empty], .tt-table',
    extra: (p, method, body, url) => {
      if (p === '/api/v2/tool-index') return {rows: ROWS, counts: null};
      if (p === '/api/v2/page-owners') return {pages: {tools: {bot: 'bot:' + OWNER, name: 'Tools'}}};
      if (p === '/api/v2/secret-rules') return RULES;
      if (p === '/api/v2/credentials' && method === 'GET') {
        return admin ? {credentials: [{id: 'c-gh', name: 'GITHUB_TOKEN', env: 'GITHUB_TOKEN', revision: 3}], can_manage: true}
          : {status: 403, body: {error: {code: 'forbidden', detail: 'Only a credential administrator can share credentials. Ask Ana', retryable: false}}};
      }
      if (p === '/api/v2/tools-chat/proposals/kg-1/grant') {
        grants.push(body);
        return {...CARD.refs.key_grant, state: 'granted', rows: CARD.refs.key_grant.rows.slice(0, 2), results: [
          {key: 'JIRA_LOGIN', tool_id: 'jira', ok: true, detail: 'Connected to Jira'},
          {key: 'POSTHOG_API_KEY', tool_id: 'posthog', ok: false, detail: "Didn't work: Key rejected"}]};
      }
      if (p.startsWith('/api/v2/credentials') && method === 'POST') {
        const b = body as Record<string, unknown>;
        if (failures > 0 && b.name === 'JIRA_LOGIN') { failures--; return {status: 422, body: {error: {code: 'credential', detail: 'The vault is busy', retryable: false}}}; }
        saved.push({path: p, key: '', body: b});
        return {id: 'c-' + saved.length, name: b.name, revision: 1};
      }
      return threads(p, url, method, body);
    }});
  return {...got, saved, sent, grants};
}

const paste = (page: Page, text: string) => page.locator('.tt-pgc .p-text').evaluate((box, t) => {
  const dt = new DataTransfer();
  dt.setData('text/plain', t);
  box.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
}, text);
// Everything the page could keep or show: the DOM (attributes too), both storages, the box.
const kept = (page: Page) => page.evaluate(() => {
  const store = (s: Storage) => Array.from({length: s.length}, (_, i) => s.key(i) + '=' + s.getItem(s.key(i) || '')).join('\n');
  return [document.documentElement.outerHTML, store(localStorage), store(sessionStorage),
    (document.querySelector('.tt-pgc .p-text') as HTMLTextAreaElement | null)?.value || '', location.href].join('\n');
});
// "Sent names only" shows as the saves finish, just before the message posts: wait for the post itself.
async function messageSent(sent: Sent[], n = 1): Promise<void> {
  for (let i = 0; i < 100 && sent.filter(s => typeof s.text === 'string').length < n; i++) await new Promise(r => setTimeout(r, 50));
}
const shot = async (page: Page, name: string) => {
  const dir = process.env.TICO_UI_SHOTS;
  if (dir) { fs.mkdirSync(dir, {recursive: true}); await page.screenshot({path: path.join(dir, `tools-chat-keys-${name}.png`)}); }
};
async function openChat(page: Page) {
  await page.locator('.tt-chat [data-pgc-open]').click();
  await page.locator('.tt-pgc .p-text').waitFor();
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    {
      const {page, seen, saved, sent} = await start(browser);
      const logs: string[] = [];
      page.on('console', m => logs.push(m.text()));
      await openChat(page);
      // AC-1
      const empty = await page.locator('.tt-pgc .th-empty').innerText();
      assert.match(empty, /Jira and PostHog need keys/, 'the empty state names the tools in Problem');
      assert.equal(await page.locator('.tt-pgc .thv-attach-wrap').isHidden(), true, 'no attachments in this chat');
      assert.equal(await page.locator('.tt-pgc .p-text').getAttribute('spellcheck'), 'false');
      // AC-2
      await paste(page, `for jira user chris / ${PASSWORD} and ${TOKEN}`);
      await page.locator('.tt-pgc [data-kd-item]').nth(1).waitFor();
      const items = await page.locator('.tt-pgc [data-kd-item]').evaluateAll(els => els.map(el => ({
        kind: el.querySelector('.kd-kind')?.textContent?.trim(), name: (el.querySelector('[data-kd-name]') as HTMLInputElement).value,
        preview: el.querySelector('.kd-preview')?.textContent})));
      assert.deepEqual(items, [
        {kind: '🔒 Jira login · chris', name: 'JIRA_LOGIN', preview: '••••••'},
        {kind: '🔒 GitHub token', name: 'GITHUB_TOKEN', preview: TOKEN.slice(0, 3) + '…' + TOKEN.slice(-3)},
      ]);
      assert.equal(await page.locator('.tt-pgc .p-text').inputValue(), 'for jira user chris / [key:JIRA_LOGIN] and [key:GITHUB_TOKEN]');
      let all = await kept(page);
      assert.ok(!all.includes(PASSWORD) && !all.includes(TOKEN), 'no value in the box, the DOM, storage or the address');
      assert.match(await page.locator('.tt-pgc [data-kd-proof]').innerText(), /2 keys locked\. They go to Credentials, encrypted/);
      // AC-14: GITHUB_TOKEN exists; Send waits until it is replaced or renamed.
      assert.match(await page.locator('.tt-pgc .kd-replace').innerText(), /Replaces the existing GITHUB_TOKEN/);
      assert.equal(await page.locator('.tt-pgc .p-send').isDisabled(), true, 'Send waits for Replace or a new name');
      await shot(page, '1280');
      const name = page.locator('.tt-pgc [data-kd-name]').nth(1);
      await name.fill('GH_DEPLOY_TOKEN');
      await name.press('Tab');
      await page.waitForFunction(() => !document.querySelector('.tt-pgc .kd-replace'));
      assert.equal(await page.locator('.tt-pgc .p-text').inputValue(), 'for jira user chris / [key:JIRA_LOGIN] and [key:GH_DEPLOY_TOKEN]');
      // AC-10
      await page.locator('.tt-pgc .p-send').click();
      await page.waitForFunction(() => /Sent names only/.test(document.querySelector('.tt-pgc [data-kd-proof]')?.textContent || ''));
      assert.deepEqual(saved.map(s => [s.path, s.body.name, s.body.kind, s.body.username, s.body.source]), [
        ['/api/v2/credentials', 'JIRA_LOGIN', 'password', 'chris', 'tools-chat'],
        ['/api/v2/credentials', 'GH_DEPLOY_TOKEN', 'token', '', 'tools-chat'],
      ]);
      assert.deepEqual([saved[0]?.body.secret, saved[1]?.body.secret], [PASSWORD, TOKEN], 'each value went to Credentials, and only there');
      await messageSent(sent);
      const message = sent.find(s => typeof s.text === 'string');
      assert.equal(message?.text, 'for jira user chris / [saved JIRA_LOGIN for Jira] and [saved GH_DEPLOY_TOKEN for GitHub]');
      assert.equal(await page.locator('.tt-pgc [data-kd-proof]').innerText(), 'Sent names only: JIRA_LOGIN, GH_DEPLOY_TOKEN.');
      all = await kept(page);
      assert.ok(!all.includes(PASSWORD) && !all.includes(TOKEN), 'after Send no value is anywhere on the page');
      assert.ok(!logs.some(l => l.includes(PASSWORD) || l.includes(TOKEN)), 'no value in the console');
      // AC-6: typed, locked within a second, no draft kept.
      const box = page.locator('.tt-pgc .p-text');
      await box.click();
      await box.pressSequentially('the token: ' + TYPED, {delay: 5});
      await page.waitForFunction(() => /\[key:TOKEN\]/.test((document.querySelector('.tt-pgc .p-text') as HTMLTextAreaElement).value), null, {timeout: 1000});
      assert.ok(!(await kept(page)).includes(TYPED), 'the typed value left the box and no draft holds it');
      // AC-8: Mark as secret on the last word.
      await box.pressSequentially(' and pw hunter', {delay: 5});
      await page.locator('.tt-pgc [data-kd-mark]').click();
      assert.match(await box.inputValue(), /and pw \[key:SECRET\]$/);
      assert.deepEqual(seen.errors, []);
      await page.close();
    }
    {
      // AC-12: one save fails: nothing is sent; Send again saves only the failed one and sends once.
      const {page, saved, sent} = await start(browser, {failSaves: 1});
      await openChat(page);
      await paste(page, `posthog ${['ph', 'x_', 'k3JdP9xQa8LmZ2vR7tYbW4nE6'].join('')} then jira user chris / ${PASSWORD}`);
      await page.locator('.tt-pgc [data-kd-item]').nth(1).waitFor();
      await page.locator('.tt-pgc .p-send').click();
      await page.locator('.tt-pgc .kd-bad').waitFor();
      assert.match(await page.locator('.tt-pgc .kd-bad').innerText(), /The vault is busy/);
      assert.equal(sent.filter(s => typeof s.text === 'string').length, 0, 'no message while a save failed');
      assert.equal(saved.length, 1);
      await page.locator('.tt-pgc .p-send').click();
      await page.waitForFunction(() => /Sent names only/.test(document.querySelector('.tt-pgc [data-kd-proof]')?.textContent || ''));
      assert.deepEqual(saved.map(s => s.body.name), ['POSTHOG_API_KEY', 'JIRA_LOGIN'], 'the saved one is not saved twice');
      await messageSent(sent);
      assert.equal(sent.filter(s => typeof s.text === 'string').length, 1, 'the message goes once');
      // AC-7: a dropped file is refused with one line.
      await page.locator('.tt-pgc .thv-ask').evaluate(el => {
        const dt = new DataTransfer();
        dt.items.add(new File(['x'], 'keys.txt', {type: 'text/plain'}));
        el.dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true}));
      });
      assert.match(await page.locator('.tt-pgc [data-kd-proof]').innerText(), /Files can’t go in this chat/);
      await page.close();
    }
    {
      // AC-13: not a credential admin.
      const {page, saved} = await start(browser, {admin: false});
      await openChat(page);
      await paste(page, 'for github ' + TOKEN);
      await page.locator('.tt-pgc .kd-denied').waitFor();
      assert.match(await page.locator('.tt-pgc .kd-denied').innerText(), /Ask Ana.*Request a key/s);
      assert.equal(await page.locator('.tt-pgc .p-send').isDisabled(), true);
      await page.locator('.tt-pgc .p-text').press('Enter');
      assert.equal(saved.length, 0, 'no value leaves the page');
      await page.close();
    }
    {
      // AC-8 on a phone, 360 px: full screen, no sideways scroll, Mark as secret in the bar.
      const {page} = await start(browser, {width: 360});
      await page.locator('.tt-chat [data-pgc-open]').click();
      await page.locator('.tt-pgc.pgc-full .p-text').waitFor();
      await paste(page, `jira user chris / ${PASSWORD}, posthog ${['ph', 'x_', 'k3JdP9xQa8LmZ2vR7tYbW4nE6'].join('')}, more ${TOKEN}`);
      await page.locator('.tt-pgc .kd-wide').waitFor();
      assert.equal(await page.locator('.tt-pgc [data-kd-for]').count(), 3, 'three items open the tray with For');
      assert.equal(await page.locator('.tt-pgc [data-kd-for]').nth(1).inputValue(), 'PostHog', 'For is pre-filled from the vendor');
      const wide = await page.evaluate(() => [document.documentElement, document.querySelector('.tt-pgc')].some(el => el && el.scrollWidth > el.clientWidth + 1));
      assert.equal(wide, false, 'no sideways scroll at 360 px');
      await shot(page, '360');
      const box = page.locator('.tt-pgc .p-text');
      await box.evaluate(b => { const t = b as HTMLTextAreaElement; t.focus(); t.setSelectionRange(t.value.length, t.value.length); });
      await box.pressSequentially(' and also secondpw', {delay: 5});
      await page.locator('.tt-pgc [data-kd-mark]').click();
      assert.match(await box.inputValue(), /and also \[key:[A-Z_]+\]$/);
      assert.equal(await page.locator('.tt-pgc [data-kd-item]').count(), 4, 'the marked word is a fourth item');
      await page.close();
    }
    {
      // AC-16, AC-18: the card's rows; one removed; Grant; each key's result, a failure with its fixes.
      const {page, grants, seen} = await start(browser, {card: true});
      await openChat(page);
      const card = page.locator('.tt-pgc [data-kg="kg-1"]');
      await card.waitFor();
      assert.equal(await card.locator('[data-kg-row]').count(), 3);
      const words = async (l: import('playwright').Locator) => ((await l.textContent()) || '').replace(/\s+/g, ' ').trim();
      assert.match(await words(card.locator('[data-kg-row]').first()), /JIRA_LOGIN → Jira · Read and write · Support, Builder/);
      // Review 1: a key that replaces the tool's key names it, who keeps access and who loses it, before Grant.
      assert.match(await words(card.locator('[data-kg-row="1"] .kg-replaces')),
        /Replaces PostHog's Read key PostHog read\. Support, Builder keep access on the new key\. Every computer loses access\./);
      await card.locator('[data-kg-remove="2"]').click();
      assert.equal(await card.locator('[data-kg-grant]').innerText(), 'Grant 2');
      await card.locator('[data-kg-grant]').click();
      await page.locator('.tt-pgc .kg-result.kg-bad').waitFor();
      assert.deepEqual(grants, [{keep: [0, 1]}], 'exactly the rows kept are granted');
      assert.match(await words(page.locator('.tt-pgc .kg-ok')), /✓ JIRA_LOGIN Connected to Jira/);
      const bad = page.locator('.tt-pgc .kg-bad');
      assert.match(await words(bad), /POSTHOG_API_KEY Didn't work: Key rejected/);
      assert.equal(await bad.locator('[data-kg-paste]').innerText(), 'Paste a new key');
      assert.equal(await bad.locator('a').getAttribute('href'), '#/tools/api/posthog');
      assert.deepEqual(seen.errors, []);
      await page.close();
    }
    console.log('tools-chat-keys: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
