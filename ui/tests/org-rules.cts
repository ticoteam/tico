// The Rules page (ui/app/org-rules.ts) on a real hub seeded with the scenario org and its groups: Dana adds an internal
// rule from the Engineering Lead's Reach panel (Who already the Engineering Lead) with a With, and its panel opens (its panel says who follows it with whom, and If broken reads Stop / Ask
// first / Warn), a rule naming a teammate as @slug shows a mention chip that opens them, edits it in the side panel, a stale save is refused and the panel
// reloads, a due date is added, marked satisfied, its repeat changed and a routine set, a second date shows beside
// it, the Engineering Lead's Rules tab shows the rule and its settings, a teammate who manages nothing reads the rule
// without Save or Retire, and the Engineering Lead's suggestions are added all at once. With RULES_SHOTS=<dir>, it also
// saves each screen at 1440 and 390, light and dark, into that directory.
import type * as RealHubSupport from './support/real-hub.cts';
import type {Browser, Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {startHub, pageAs} = require('./support/real-hub.cts') as RealHubSupport.Api;
const {t} = require('./support/load.cts') as {t: (ms: number) => number};

interface Source { id: string; name: string; kind: string }
interface Rule {
  id: string; status: string; version: number; description?: string; text?: string;
  who: {bots: {id: string}[]; outside_editions?: boolean}; with: {groups: {id: string}[]};
  where: unknown[]; if_broken: string; reviewer: {id: string}; can_edit: boolean;
}
interface Due {
  id: string; status: string; due_on: string; repeats: string; replaces: string; task_days: number; routine_id: string;
  history: {satisfied_by: string}[];
}
// The page's globals this spec reads (app/ scripts loaded by the Rules page).
interface PageWin { ticoTheme: {set: (theme: string) => void} }
// A write as the page's own teammate, the way the app sends one.
const api = (page: Page, method: string, p: string, body?: unknown) => page.evaluate(async ([method, p, body]: [string, string, unknown]) => {
  const r = await fetch('/api' + p, {method, cache: 'no-store', headers: {'content-type': 'application/json', 'Idempotency-Key': crypto.randomUUID()},
                                     body: body === undefined ? undefined : JSON.stringify(body)} as RequestInit);
  const json = await r.json().catch(() => ({}));
  return {status: r.status, json};
}, [method, p, body] as [string, string, unknown]);
const ok = async <T extends unknown = Record<string, unknown>>(page: Page, method: string, p: string, body?: unknown): Promise<T> => {
  const {status, json} = await api(page, method, p, body);
  assert.ok(status < 300, `${method} ${p}: ${status} ${JSON.stringify(json)}`);
  return json as T;
};
const ready = (page: Page) => page.waitForFunction(() => {
  const who = document.querySelector('#account .account-email')?.textContent || '';
  return who !== '' && who !== 'Signing in…' && (document.querySelector('#main')?.children.length as number) > 0;
}, null, {timeout: t(20000)});
const go = async (page: Page, hash: string) => { await page.evaluate(h => { location.hash = h; }, hash); };
const hashParam = async (page: Page, k: string) => new URLSearchParams((await page.evaluate(() => location.hash)).split('?')[1]).get(k)!;
const LEAD = '#/rules?party=' + encodeURIComponent('bot:engineering-manager');
const day = (n: number) => { const d = new Date(Date.now() + n * 86400000); return d.toISOString().slice(0, 10); };

(async () => {
  const hub = await startHub({seeds: ['org', 'groups', 'toured']});
  let browser: Browser | undefined;
  try {
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);

    // The Engineering Lead suggests three rules from a vendor: a bot's rule always waits in Suggested.
    const bot = await pageAs(browser, hub, 'engineering-manager');
    const source = (await ok<{source: Source}>(bot, 'POST', '/v2/rule-sources', {name: 'Northwind Supply', kind: 'vendor'})).source;
    for (const text of ['Send invoices within 30 days', 'Keep delivery records for 2 years', 'Report outages within 24 hours']) {
      const made = await ok<Rule>(bot, 'POST', '/v2/rules', {kind: 'external', text, who: {bots: ['engineering-manager']}, source_id: source.id});
      assert.equal(made.status, 'suggested');
    }

    const page = await pageAs(browser, hub, 'dana');
    page.on('dialog', d => d.accept());
    await ready(page);

    // Add an internal rule from the Engineering Lead's Reach panel: Who is the Engineering Lead already; With Engineering.
    // Saving it opens its panel over the party's.
    await go(page, LEAD);
    await page.locator('.rr-panel[data-party="bot:engineering-manager"] [data-rr-add-rule]').click({timeout: t(15000)});
    const modal = page.locator('#ru-add');
    await modal.locator('textarea[name=text]').fill('Ask before a production deploy');
    await modal.locator('[data-ru-adv]').click();
    assert.match(await modal.locator(`[data-scope="who"] .ru-chip`).innerText(), /engineering.manager/i);
    await modal.locator('[data-scope="with"] [data-seg="groups"]').click();
    await modal.locator('[data-scope="with"] .ru-addsel').selectOption({label: 'Engineering'});
    await modal.locator('button[type=submit]').click();
    await page.waitForFunction(() => /[?&]rule=/.test(location.hash) && /party=bot%3Aengineering-manager/.test(location.hash), null, {timeout: t(10000)});
    const ruleId = await hashParam(page, 'rule');
    let rule = await ok<Rule>(page, 'GET', '/v2/rules/' + ruleId);
    assert.equal(rule.status, 'active');
    assert.deepEqual(rule.who.bots.map(b => b.id), ['engineering-manager']);
    assert.equal(rule.with.groups.length, 1);
    assert.ok(rule.can_edit);
    // Every kind of rule has a tab, in the page's order, each with its count (Stage rules spec, The Rules page).
    const tabs = (await page.locator('.ru-tabs a').allInnerTexts()).map(x => x.replace(/\s+\d+$/, ''));
    assert.deepEqual(tabs, ['Company', 'Outside', 'Pipelines and stages', 'Due', 'Wakes', 'Automations on tasks', 'Built in', 'Suggested']);
    assert.equal(await page.locator('.ru-tabs a .ru-n').count(), 8, 'each tab carries its count');
    // Its panel: one sentence from Who and With, the If broken labels say what happens, and the reviewer shows only
    // under Ask first.
    await go(page, '#/rules?rule=' + ruleId);
    const follow = page.locator(`#ru-side form[data-rule="${ruleId}"] .ru-follow`);
    await follow.waitFor({timeout: t(10000)});
    assert.match((await follow.textContent())!, /^\S.* follows this with Engineering\.$/);
    assert.deepEqual(await page.locator('#ru-side [data-ru-seg="if_broken"] button').allInnerTexts(), ['Stop', 'Ask first', 'Warn']);
    assert.equal(await page.locator('#ru-side .ru-reviewer').isHidden(), true);
    await page.locator('#ru-side [data-ru-seg="if_broken"] [data-v="escalate"]').click();
    assert.equal(await page.locator('#ru-side .ru-reviewer').isVisible(), true);
    assert.equal(await page.locator('#ru-side [data-ru-hint]').textContent(), 'Held until the reviewer OKs it.');
    await go(page, '#/rules');

    // A teammate named @slug in a rule's text is a mention chip; in a party's rules (all bots' rules, folded until
    // opened) it opens the teammate, not the rule.
    const named = await ok<Rule>(page, 'POST', '/v2/rules', {kind: 'internal', text: 'Ask @engineering-manager before you merge.', who: {all_bots: true}});
    await go(page, '#/overview');
    await page.locator('#ru-root').waitFor({state: 'detached', timeout: t(10000)});   // so Rules reads them again
    await go(page, LEAD);
    await page.locator('.rr-panel [data-rr-inh="all_bots"][aria-expanded="true"]').waitFor({timeout: t(15000)});
    const chip = page.locator(`.rr-panel a.rr-rule[data-rule="${named.id}"] .ru-at`);
    await chip.waitFor({timeout: t(10000)});
    assert.equal(await chip.getAttribute('data-href'), '#/bot/engineering-manager');
    assert.ok((await chip.textContent())!.startsWith('@'));
    await chip.click();
    await page.waitForFunction(() => location.hash.startsWith('#/bot/engineering-manager'), null, {timeout: t(10000)});
    await go(page, '#/rules?rule=' + named.id);
    await page.locator('#ru-side .ru-read a.ru-at[href="#/bot/engineering-manager"]').waitFor({timeout: t(10000)});
    await ok(page, 'POST', `/v2/rules/${named.id}/retire`, {version: named.version});
    await go(page, '#/rules');

    // Add rule from a bot's panel: no Where (rules cover every internal and external action).
    await go(page, LEAD);
    await page.locator('.rr-panel[data-party="bot:engineering-manager"] [data-rr-add-rule]').click({timeout: t(15000)});
    const threaded = page.locator('#ru-add');
    await threaded.locator('textarea[name=text]').fill('Post deploy notes in the thread');
    await threaded.locator('[data-ru-adv]').click();
    assert.equal(await threaded.locator('input[name=where_kind]').count(), 0);
    // If broken in Add rule: Ask first with a reviewer.
    await threaded.locator('[data-ru-seg="if_broken"] [data-v="escalate"]').click();
    await threaded.locator('select[name=reviewer]').selectOption('dana');
    await threaded.locator('button[type=submit]').click();
    await page.waitForFunction(id => { const r = new URLSearchParams(location.hash.split('?')[1]).get('rule'); return r && r !== id; }, ruleId, {timeout: t(10000)});
    const threadRule = await ok<Rule>(page, 'GET', '/v2/rules/' + await hashParam(page, 'rule'));
    assert.deepEqual(threadRule.where, []);
    assert.equal(threadRule.if_broken, 'escalate');
    assert.equal(threadRule.reviewer.id, 'dana');
    // Outside editions travels with the rule: shown in its Who, and still set after a save that doesn't touch Who.
    let outside = await ok<Rule>(page, 'POST', '/v2/rules', {kind: 'internal', text: 'Never name a customer outside the company', who: {outside_editions: true}});
    assert.equal(outside.who.outside_editions, true);
    outside = await ok<Rule>(page, 'POST', '/v2/rules/' + outside.id, {version: outside.version, text: 'Never name a customer outside Acme'});
    assert.equal(outside.who.outside_editions, true);
    await go(page, '#/rules?rule=' + outside.id);
    await page.waitForFunction(() => document.querySelector('#ru-side [data-unchip="outside_editions"]'), null, {timeout: t(10000)});
    await page.locator('#ru-side form:not([data-old]) textarea[name=description]').fill('Editions go through this.');
    await page.locator('#ru-side button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector<HTMLTextAreaElement>('#ru-side textarea[name=description]')?.value === 'Editions go through this.', null, {timeout: t(10000)});
    assert.equal((await ok<Rule>(page, 'GET', '/v2/rules/' + outside.id)).who.outside_editions, true, 'a panel save keeps it');
    await go(page, '#/rules?rule=' + ruleId);

    // Edit it in the side panel.
    const side = page.locator('#ru-side');
    await page.waitForFunction(id => document.querySelector(`#ru-side form[data-rule="${id}"]`), ruleId, {timeout: t(10000)});
    await side.locator('textarea[name=description]').fill('Production means the live site.');
    await side.locator('[data-ru-seg="if_broken"] [data-v="warn"]').click();
    await page.evaluate(() => { document.querySelector<HTMLFormElement>('#ru-side form')!.dataset.old = '1'; });   // the panel redraws after a save
    await side.locator('button[type=submit]').click();
    await page.waitForFunction(async id => {
      const r = (await (await fetch('/api/v2/rules/' + id, {cache: 'no-store'})).json()) as {if_broken: string; version: number};
      return r.if_broken === 'warn' && r.version === 2;
    }, ruleId, {timeout: t(10000)});
    rule = await ok<Rule>(page, 'GET', '/v2/rules/' + ruleId);
    assert.equal(rule.description, 'Production means the live site.');

    // A save on a version someone else has moved past is refused; the panel reloads what is stored now.
    await side.locator(`form[data-rule="${ruleId}"]:not([data-old]) textarea[name=description]`).waitFor({timeout: t(10000)});
    await ok(page, 'POST', '/v2/rules/' + ruleId, {version: rule.version, text: 'Ask Omar before a production deploy'});
    await side.locator('textarea[name=description]').fill('A stale edit');
    await side.locator('button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector<HTMLTextAreaElement>('#ru-side textarea[name=text]')?.value === 'Ask Omar before a production deploy'
      && document.querySelector('#ru-side .ru-status')?.textContent!.trim() !== '', null, {timeout: t(10000)});
    rule = await ok<Rule>(page, 'GET', '/v2/rules/' + ruleId);
    assert.equal(rule.version, 3);
    assert.equal(rule.description, 'Production means the live site.', 'the stale save wrote nothing');

    // Add a due date from the rule's panel, then mark it satisfied.
    await side.locator('.ru-adddue').click();
    await page.waitForFunction(() => document.querySelector('#ru-side input[name=due]'), null, {timeout: t(10000)});
    await side.locator('input[name=due]').fill(day(5));
    await side.locator('select[name=repeats]').selectOption('yearly');
    await side.locator('select[name=owner]').selectOption('human:dana');
    await side.locator('button[type=submit]').click();
    const satisfy = side.locator('[data-ru-satisfy]');
    await satisfy.waitFor({timeout: t(10000)});
    const dueId = new URLSearchParams((await page.evaluate(() => location.hash)).split('?')[1]).get('due');
    assert.ok(dueId);
    await satisfy.click();
    await page.locator('#ru-side .ru-mark').waitFor({timeout: t(10000)});   // its History has the mark
    const due = (await ok<{due: Due[]}>(page, 'GET', '/v2/rules/due?next=400d')).due.find(d => d.id === dueId)!;
    assert.equal(due.status, 'upcoming', 'a yearly due date moves on to next year');
    assert.equal(due.history[0]!.satisfied_by, 'human:dana');

    // A change to Repeats starts again from the date shown (next year's), not the first date ever set, so the
    // satisfied one stays behind instead of coming back overdue.
    await page.waitForFunction(d => document.querySelector<HTMLInputElement>('#ru-side input[name=due]')?.value === d, due.due_on, {timeout: t(10000)});
    await side.locator('select[name=repeats]').selectOption('once');
    await side.locator('button[type=submit]').click();
    await page.waitForFunction(id => { const q = new URLSearchParams(location.hash.split('?')[1]); return q.get('due') && q.get('due') !== id; },
      dueId, {timeout: t(10000)});
    const liveId = new URLSearchParams((await page.evaluate(() => location.hash)).split('?')[1]).get('due')!;
    const live = await ok<Due>(page, 'GET', '/v2/rule-due/' + liveId);
    assert.equal(live.due_on, due.due_on);
    assert.equal(live.repeats, 'once');
    assert.equal(live.status, 'upcoming');
    assert.equal(live.replaces, dueId);
    // The ended row's address opens the live one. Open it only once the live row's panel has finished drawing:
    // a draw still in flight when the hash moves back makes the ended row's open give way and never redirect.
    await page.waitForFunction(id => document.querySelector<HTMLElement>('#ru-side')?.dataset.open?.startsWith('due:' + id + ':')
      && document.querySelector<HTMLSelectElement>('#ru-side select[name=repeats]')?.value === 'once', liveId, {timeout: t(10000)});
    await go(page, `#/rules/due?due=${dueId}`);
    await page.waitForFunction(id => location.hash.includes('due=' + id), liveId, {timeout: t(10000)});

    // Routine: a task 10 days before. The automation it makes is linked from the panel and the list shows the lead.
    await side.locator('select[name=routine]').selectOption('task');
    await side.locator('input[name=task_days]').fill('10');
    await side.locator('button[type=submit]').click();
    await side.locator('.ru-routine').waitFor({timeout: t(10000)});
    const linked = await ok<Due>(page, 'GET', '/v2/rule-due/' + liveId);
    assert.equal(linked.task_days, 10);
    assert.equal(await side.locator('.ru-routine').getAttribute('href'), '#/automations/' + linked.routine_id);
    assert.equal((await ok<{trigger: {rule_due_id: string}}>(page, 'GET', '/v2/automations/' + linked.routine_id)).trigger.rule_due_id, liveId);
    await go(page, '#/rules/due');
    await page.waitForFunction(id => /10 days before/.test(document.querySelector(`a.ru-row[data-due="${id}"] [data-l=Routine]`)?.textContent || ''),
      liveId, {timeout: t(10000)});

    // A second, earlier date on the same rule (the hub's clock decides overdue or due soon): the panel lists both,
    // the most urgent first.
    const sooner = await ok<Due>(page, 'POST', `/v2/rules/${ruleId}/due`, {first_due: day(-3), repeats: 'once', owner: 'human:dana'});
    await go(page, `#/rules?rule=${ruleId}`);
    await page.waitForFunction(() => document.querySelectorAll('#ru-side .ru-duebox').length === 2, null, {timeout: t(10000)});
    assert.equal(await side.locator('.ru-duebox').first().getAttribute('data-due'), sooner.id);
    assert.equal(await side.locator('.ru-adddue[href*="due=new"]').count(), 1, 'more dates can still be added');

    // The Engineering Lead's Rules tab: the rule, then its settings as sentences, each opening its own editor; the
    // hub's fixed limits have none.
    await go(page, '#/bot/engineering-manager/rules');
    await page.locator(`#bot-rules .ru-row[data-rule="${ruleId}"]`).waitFor({timeout: t(15000)});
    assert.ok(await page.locator('#bot-rules .ru-setting').count() > 0, 'its settings are listed');
    assert.equal(await page.locator('#bot-rules [data-ru-setting="mail"]').getAttribute('href'), '#/bot/engineering-manager/settings');
    assert.ok(await page.locator('#bot-rules .ru-setting > .ru-c:last-child > .ru-none').count() > 0, 'a fixed limit has no link');

    // Its reach, as the Rules panel has it: a cap it sets has no source, a default is shown as its value with one, and
    // an exception it sends or receives is listed; each row's link opens the Rules panel.
    await ok(page, 'PUT', '/v2/reach/settings/' + encodeURIComponent('bot:engineering-manager'), {chats_per_day: 40});
    await ok(page, 'POST', '/v2/reach/lines', {sender: 'bot:engineering-manager', receiver: 'human:priya', per_day: 3});
    await go(page, '#/overview');
    await go(page, '#/bot/engineering-manager/rules');
    const reachRow = (text: string) => page.locator('#bot-rules .ru-setting', {has: page.locator('.ru-txt', {hasText: text})});
    await reachRow('Up to 40 chats a day').locator('.ru-txt').waitFor({timeout: t(15000)});
    assert.doesNotMatch(await reachRow('Up to 40 chats a day').locator('.ru-txt').innerText(), /default/, 'its own cap has no source');
    assert.match(await reachRow('Up to 999 tasks a day').locator('.ru-txt').innerText(), /Up to 999 tasks a day.*default/s);
    assert.match(await reachRow('Reach:').locator('.ru-txt').innerText(), /^Reach: (anyone|nearby|only who's listed)/);
    assert.match(await reachRow('3 a day').locator('.ru-txt').innerText(), /^To .+ · 3 a day/);
    assert.equal(await reachRow('Up to 40 chats a day').locator('a.ru-set').getAttribute('href'), '#/rules?party=bot%3Aengineering-manager');

    // Priya manages nothing about the Engineering Lead: the panel is read only, and the bot's Rules tab opens by link.
    const priya = await pageAs(browser, hub, 'priya');
    await ready(priya);
    await go(priya, `#/rules?rule=${ruleId}`);
    await priya.locator(`#ru-side form[data-rule="${ruleId}"]`).waitFor({timeout: t(10000)});
    assert.equal(await priya.locator('#ru-side button[type=submit]').count(), 0);
    assert.equal(await priya.locator('#ru-side [data-ru-retire]').count(), 0);
    assert.equal(await priya.locator('#ru-side .ru-duebox').count(), 2);
    await go(priya, '#/bot/engineering-manager/rules');
    await priya.locator(`#bot-rules .ru-row[data-rule="${ruleId}"]`).waitFor({timeout: t(15000)});
    assert.equal(await priya.locator('[data-bot-detail-tab=rules]').getAttribute('aria-selected'), 'true');
    await priya.context().close();

    // Suggested: add all three at once.
    await go(page, '#/rules/suggested');
    const group = page.locator(`.ru-sg[data-group="${source.id}"]`);
    await group.locator('[data-ru-add-all]').click();
    await group.waitFor({state: 'detached', timeout: t(15000)});
    const counts = await ok<{suggested: number; external: number}>(page, 'GET', '/v2/rules/counts');
    assert.equal(counts.suggested, 0);
    assert.equal(counts.external, 3);
    await page.context().close();

    if (process.env.RULES_SHOTS) await shots(browser, hub, bot, {ruleId, dueId: liveId});
    await bot.context().close();
    console.log('org-rules: ok');
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(error => { console.error(error); process.exit(1); });

// Each screen at 1440 and 390, light and dark: Internal with a party's panel and with the rule's panel, External, Due with its panel,
// Suggested, Add rule and the bot's Rules tab.
async function shots(browser: Browser, hub: RealHubSupport.Hub, bot: Page, {ruleId, dueId}: {ruleId: string; dueId: string}) {
  const dir = process.env.RULES_SHOTS!;
  fs.mkdirSync(dir, {recursive: true});
  // More to look at: a second internal rule, and suggestions from a customer waiting in Suggested.
  const dana = await pageAs(browser, hub, 'dana');
  await ok(dana, 'POST', '/v2/rules', {kind: 'internal', text: 'Reply to customers within one business day', who: {groups: [(await ok<Rule>(dana, 'GET', '/v2/rules/' + ruleId)).with.groups[0]!.id]}});
  await ok(dana, 'POST', '/v2/rules', {kind: 'internal', text: 'Log every refund in the ledger', who: {all_bots: true}, if_broken: 'escalate'});
  await dana.context().close();
  const customer = (await ok<{source: Source}>(bot, 'POST', '/v2/rule-sources', {name: 'Contoso Retail', kind: 'customer'})).source;
  for (const text of ['Ship orders within 2 business days', 'Use the customer portal for returns'])
    await ok(bot, 'POST', '/v2/rules', {kind: 'external', text, who: {all_bots: true}, source_id: customer.id});
  const screens: [string, string, string, ((page: Page) => Promise<void>)?][] = [
    ['reach', LEAD, '.rr-panel[data-party="bot:engineering-manager"]'],
    ['internal', `${LEAD}&rule=${ruleId}`, `#ru-side form[data-rule="${ruleId}"]`],
    ['external', '#/rules/external', '.ru-ext a.ru-row'],
    ['due', `#/rules/due?due=${dueId}`, '#ru-side .ru-mark'],
    ['suggested', '#/rules/suggested', '.ru-sg .ru-prop'],
    ['add-rule', LEAD, '#ru-add[open]', async page => { await page.locator('.rr-panel [data-rr-add-rule]').click(); }],
    ['bot-rules', '#/bot/engineering-manager/rules', '#bot-rules .ru-setting'],
  ];
  for (const theme of ['light', 'dark']) {
    for (const phone of [false, true]) {
      const page = await pageAs(browser, hub, 'dana', {phone});
      try {
        await ready(page);
        await page.evaluate(v => (window as unknown as PageWin).ticoTheme.set(v), theme);
        for (const [name, hash, wait, act] of screens) {
          await go(page, '#/overview');
          await go(page, hash);
          if (act) { await page.locator('.rr-panel [data-rr-add-rule]').waitFor({timeout: t(10000)}); await act(page); }
          await page.locator(wait).first().waitFor({timeout: t(15000)});
          await page.waitForTimeout(400);
          await page.screenshot({path: path.join(dir, `${name}-${phone ? 390 : 1440}-${theme}.png`)});
          await page.keyboard.press('Escape');
        }
      } finally {
        await page.context().close();
      }
    }
  }
}
