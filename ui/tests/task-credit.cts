// A task held for credit uses the top strip's Add credit action, at desktop and phone widths. Fixtures only.
import type {Task} from './tasks-page.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, task} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
declare function cloudAccessApply(cloud: {access: string; billing_url: string | null}): void;

(async () => {
  const channel = process.env.TICO_BROWSER_CHANNEL;
  const browser = await chromium.launch({headless: true, ...(channel ? {channel} : {})});
  try {
    for (const width of [390, 560, 1440]) {
      const billing = 'https://billing.acme.example/companies/acme';
      let line: string | null = 'Out of credit: Tico-funded work is paused';
      const held: Task = task('t-credit', 'Ship the checkout', 'spec');
      const {page, errors} = await open(browser, {
        viewport: {width, height: 844}, hash: '#/task/t-credit', tasks: [held], needs: [],
        beforeGoto: async page => {
          await page.context().route('https://billing.acme.example/**', route => route.fulfill({contentType: 'text/html', body: '<title>Add credit</title>'}));
          await page.route('**/api/status', route => route.fulfill({json: {
            cloud: true, keeper_alive: true, active: [], queued: [], health_issues: [],
            cloud_access: {access: 'frozen', reason: 'out_of_credit', billing_url: billing},
          }}));
          await page.route('**/api/v2/tasks/t-credit', route => route.fulfill({json: {
            // The turn carries the cause's page (backend/task_wakes.py CAUSE_PAGES); the queued run says it in words.
            task: {...held, turn: line ? {state: 'bot', who: 'bot:pm', requirement_id: null, since: null, until: null, ask: false,
              ...(/^out of credit/i.test(line) ? {line: 'PM Tico Cloud is out of credit', href: '#/settings/billing'}
                : {line: 'PM has no computer', href: '#/settings/computers'})} : null},
            waiting_for: line, children: [], comments: [], events: [],
          }}));
        },
      });
      try {
        const banner = page.locator('#cloud-banner a');
        await banner.waitFor();
        const rows = page.locator('#task-modal[open] .task-wait');
        const links = rows.getByRole('link', {name: 'Add credit', exact: true});
        await page.locator('#task-modal .task-comments .task-wait').waitFor();
        assert.equal(await links.count(), 2, 'both the turn and the queued-run status offer Add credit');
        for (const link of await links.all()) {
          assert.equal(await link.getAttribute('href'), await banner.getAttribute('href'), 'same destination as the top strip');
          assert.equal(await link.getAttribute('target'), '_blank');
          assert.match((await link.getAttribute('rel'))!, /noopener/);
          await link.scrollIntoViewIfNeeded();
          const box = (await link.boundingBox())!;
          assert.ok(box.x >= 0 && box.x + box.width <= width, 'the action fits the viewport');
          const opened = page.waitForEvent('popup');
          await link.click();
          const popup = await opened;
          await popup.waitForLoadState('domcontentloaded');
          assert.equal(popup.url(), billing, 'activating either task status opens the credit flow');
          assert.equal(await popup.evaluate(() => window.opener), null);
          await popup.close();
          assert.ok(page.url().endsWith('#/task/t-credit'), 'the task stays open');
        }
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no horizontal page overflow');
        assert.ok(await page.locator('#task-modal').evaluate(el => el.scrollWidth <= el.clientWidth), 'no horizontal task overflow');
        // A cloud update and its missing-URL fallback apply to existing task links as well as the strip.
        for (const url of ['https://billing.acme.example/updated', null]) {
          await page.evaluate(billing_url => cloudAccessApply({access: 'frozen', billing_url}), url);
          for (const link of await links.all()) assert.equal(await link.getAttribute('href'), await banner.getAttribute('href'));
        }
        // A plain status has the action too; unrelated and absent waits never acquire it.
        for (line of ['Out of credit', 'Waiting on a computer', null]) {
          await page.reload();
          await page.locator('#task-modal[open] .tmodal-title').waitFor();
          if (line) await page.locator('#task-modal .task-comments .task-wait').waitFor();
          assert.equal(await links.count(), line === 'Out of credit' ? 2 : 0);
        }
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }
    console.log('task-credit: ok');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
