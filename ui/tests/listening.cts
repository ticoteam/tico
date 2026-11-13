// Listening (ui/app/listening.ts) against the fake hub in support/automations-fake.cts: its own sidebar row and list
// (a dot, the name, the limits, when it last sent; a search and a +; no filters), with nothing of it left on the
// Automations page. One open: Sources, Rules and Rate limits lead the flow and are edited in the trigger's editor,
// saved as one PATCH {name, trigger}; a new push source shows its address once, with a copy button. Its Runs tab opens
// the newest send on its counts and the text that went out. A Paths step splits what was sent By tag or Each item
// and offers the Keep rules' tags as paths. A phone first, then a computer.
import type * as AutomationsFakeSupport from './support/automations-fake.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {openAutomations, PHONE, DESKTOP, sideways, shot} = require('./support/automations-fake.cts') as AutomationsFakeSupport.Api;

type Trigger = {type: string; at?: string; tz?: string; sources: {id: string; kind: string; site?: string; target?: string}[];
  rules: {do: string; tag?: string; text: string}[]; limits: {count: number; per: string}[]};
type Body = {name?: string; trigger?: Trigger; steps?: {by?: string; paths?: {name: string; when: unknown[]; steps?: unknown[]}[]}[]};

(async () => {
  const browser = await chromium.launch();
  try {
    // A phone: the list, then one open and its editor as a sheet.
    {
      const tag = 'phone';
      const {page, errors} = await openAutomations(browser, {viewport: PHONE, hash: '#/listening'});
      await page.waitForSelector('.lst-row');
      assert.deepEqual(await page.locator('.lst-row .lst-nm').allInnerTexts(), ['Market listening', 'Pricing page watch'], tag + ': the listenings and nothing else');
      assert.equal(await page.locator('.lst-row .lst-lim').first().isVisible(), false, tag + ': the limits leave the row on a phone');
      assert.equal(await page.locator('.au-q').isVisible(), true, tag + ': a search');
      assert.equal(await page.locator('.au-range, .au-fbtn, .au-tabs').evaluateAll(els => els.some(el => (el as HTMLElement).checkVisibility())), false, tag + ': no range, filters or tabs');
      assert.equal(await sideways(page), false, tag + ': no sideways scroll');
      await shot(page, 's1-list-phone');
      await page.locator('.lst-row[data-lst="au_market"]').click();
      await page.waitForSelector('.lst-blk');
      assert.equal(await page.evaluate(() => location.hash), '#/listening/au_market');
      assert.equal(await sideways(page), false, tag + ': one open, no sideways scroll');
      await shot(page, 's2-open-phone');
      await page.locator('.lst-blk').click();
      await page.waitForSelector('.au-ed [data-b="trigger.sources.0.site"]');
      assert.equal(await sideways(page), false, tag + ': the editor, no sideways scroll');
      await shot(page, 's2-edit-phone');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    // A computer: the Automations page has no listening; Listening has its own row and list.
    {
      const tag = 'desktop';
      const {page, errors, writes} = await openAutomations(browser, {viewport: DESKTOP, hash: '#/automations'});
      await page.waitForSelector('tr.au-row');
      assert.equal(await page.locator('tr.au-row[data-row="au_market"], tr.au-row[data-row="au_pricing"]').count(), 0, tag + ': no listening in Automations');
      assert.equal(await page.locator('[data-ftrig="listening"]').count(), 0, tag + ': Listening left the kinds');
      await page.locator('.au-new').click();
      assert.ok(!(await page.locator('.au-newm').innerText()).includes('Listening'), tag + ': and the New menu');
      await page.keyboard.press('Escape');
      // An old Automations address of a listening opens it on its own page.
      await page.evaluate(() => { location.hash = '#/automations/au_market'; });
      await page.waitForFunction(() => location.hash === '#/listening/au_market');
      await page.waitForSelector('.lst-blk');

      await page.locator('#side a[data-nav="listening"]').click();
      await page.waitForSelector('.lst-row');
      assert.equal(await page.locator('#side a[data-nav="listening"].cur').count(), 1, tag + ': Listening is current');
      assert.equal(await page.locator('.au-ph h1').innerText(), 'Listening');
      const row = page.locator('.lst-row[data-lst="au_market"]');
      assert.match((await row.innerText()).replace(/\s+/g, ' '), /^Market listening 3 an hour · 10 a day 20m$/, tag + ': the name, the limits and when it last sent');
      // The dot is the server's: hollow with a blocked source, and hollow with posts waiting unscored though its source read well.
      assert.equal(await page.locator('.lst-row .au-dot.lst-no').count(), 2, tag + ': hollow dots');
      assert.equal(await page.locator('.lst-row.off .lst-nm').innerText(), 'Pricing page watch', tag + ': an off one is dimmed');
      await page.fill('.au-q', 'northwind');
      assert.deepEqual(await page.locator('.lst-row .lst-nm').allInnerTexts(), ['Market listening'], tag + ': the search reads the sources too');
      await page.fill('.au-q', 'zzz');
      assert.equal((await page.locator('.au-lst .muted', {hasText: 'No matches'}).innerText()).trim(), 'No matches', tag + ': a search that hides every row says so');
      assert.equal(await page.locator('.lst-empty, [data-lst-new]').count(), 0, tag + ': with no button');
      await page.fill('.au-q', '');
      await shot(page, 's1-list-desktop');

      // One open: the three parts, then the steps.
      await row.click();
      await page.waitForSelector('.lst-blk');
      assert.match(await page.locator('.au-crumb').innerText(), /Listening/);
      assert.equal(await page.locator('.au-ph :is(.au-q,.au-new)').evaluateAll(els => els.some(el => (el as HTMLElement).checkVisibility())), false, tag + ': the search and the + are the list\'s');
      const secs = page.locator('.lst-blk .lst-sec');
      assert.deepEqual(await secs.locator('.au-lb').allInnerTexts().then(t => t.map(x => x.toLowerCase())), ['sources', 'rules', 'rate limits']);
      assert.equal(await secs.nth(0).locator('.lst-r').count(), 5);
      assert.equal(await secs.nth(0).locator('.au-dot.ok').count(), 4, tag + ': four sources read well');
      assert.equal(await secs.nth(0).locator('.au-dot.lst-no').getAttribute('title'), 'Blocked', tag + ': and one is blocked');
      assert.match(await secs.nth(1).locator('.lst-r').first().innerText(), /Remove duplicates/, tag + ': Remove duplicates is first');
      assert.equal(await secs.nth(1).locator('.lst-r').count(), 6);
      assert.deepEqual(await secs.nth(2).locator('.lst-r').allInnerTexts().then(t => t.map(x => x.trim())), ['Up to 3 every hour', 'Up to 10 every day']);
      assert.equal(await page.locator('.au-paths .au-pth').count(), 3, tag + ': the steps underneath');
      await shot(page, 's2-open-desktop');

      // The editor: Remove duplicates is locked; a limit takes a unit no other limit has; no time with an hourly limit.
      await page.locator('.lst-blk [data-lst-add="lsrc"]').click();
      await page.waitForSelector('.au-ed [data-b="trigger.sources.5.kind"]');
      assert.equal(await page.locator('.au-ed .lst-dup').count(), 1);
      assert.equal(await page.locator('.au-ed .lst-dup :is(input,select,button)').count(), 0, tag + ': Remove duplicates has no controls');
      assert.equal(await page.locator('.au-ed [data-b="trigger.at"]').count(), 0, tag + ': no time while a limit is hourly');
      assert.deepEqual(await page.locator('.au-ed [data-b="trigger.limits.0.per"] option').allInnerTexts(), ['hour', 'week', 'month'], tag + ': one limit per unit');
      await page.selectOption('[data-b="trigger.sources.5.kind"]', 'push');
      assert.equal(await page.locator('.au-ed [data-b="trigger.sources.5.target"]').count(), 0, tag + ': a push source has nothing to read');
      await page.fill('[data-b="trigger.sources.5.site"]', 'Partner feed');
      // A Keep rule needs a tag no other rule has.
      await page.locator('.au-ed [data-op="add"][data-tpl="lrule"]').click();
      await page.fill('[data-b="trigger.rules.5.tag"]', 'buyer');
      await page.fill('[data-b="trigger.rules.5.text"]', 'Someone asking for a price');
      await page.locator('.au-save').click();
      await page.locator('.toast.err', {hasText: 'Each tag is used once'}).waitFor();
      assert.equal(writes.length, 0, tag + ': a repeated tag is not sent');
      await shot(page, 's2-refused-desktop');
      await page.fill('[data-b="trigger.rules.5.tag"]', 'price');
      await page.fill('[data-b="name"]', 'Market watch');
      await shot(page, 's2-edit-desktop');
      await page.locator('.au-save').click();
      await page.waitForFunction(() => document.querySelector('.au-ver')?.textContent === 'v4');
      assert.equal(await page.locator('.toast.err').count(), 0, tag + ': a save that goes through takes the refusal away');
      const sent = writes.at(-1)!;
      assert.equal(sent.method + ' ' + sent.path, 'PATCH /api/v2/automations/au_market');
      const body = sent.body as Body, t = body.trigger!;
      assert.deepEqual(Object.keys(body).sort(), ['name', 'trigger']);
      assert.equal(body.name, 'Market watch');
      assert.equal(t.type, 'listening');
      assert.equal(t.sources.length, 6);
      assert.deepEqual({...t.sources[5]!, id: ''}, {id: '', kind: 'push', site: 'Partner feed'});
      assert.deepEqual(t.rules[5], {do: 'keep', tag: 'price', text: 'Someone asking for a price'});
      assert.deepEqual(t.rules[3], {do: 'drop', text: 'Vendors promoting themselves'});
      assert.deepEqual(t.limits, [{count: 3, per: 'hour'}, {count: 10, per: 'day'}]);
      assert.equal(t.at, '08:00');
      // The new push source's address, once, with a copy button.
      const addr = page.locator('.lst-blk .lst-addr');
      await addr.waitFor();
      assert.equal(await addr.locator('code').innerText(), 'https://tico.example/api/v2/listening/in/tok_' + t.sources[5]!.id);
      assert.equal(await addr.locator('[data-lst-copy]').getAttribute('data-lst-copy'), 'https://tico.example/api/v2/listening/in/tok_' + t.sources[5]!.id);
      assert.equal(await page.locator('.au-hd .au-name').innerText(), 'Market watch');
      await shot(page, 's2-push-address-desktop');
      await page.locator('.au-crumb a').click();
      await page.waitForSelector('.lst-row');
      await page.locator('.lst-row[data-lst="au_market"]').click();
      await page.waitForSelector('.lst-blk');
      assert.equal(await page.locator('.lst-addr').count(), 0, tag + ': the address is not shown again');

      // Without an hourly limit, the time it sends.
      await page.locator('.lst-blk').click();
      await page.locator('.au-ed [data-op="rm"][data-at="trigger.limits"]').first().click();
      await page.fill('[data-b="trigger.at"]', '09:30');
      await page.locator('.au-save').click();
      await page.waitForFunction(() => document.querySelector('.au-ver')?.textContent === 'v5');
      const again = (writes.at(-1)!.body as Body).trigger!;
      assert.deepEqual(again.limits, [{count: 10, per: 'day'}]);
      assert.equal(again.at, '09:30');
      assert.match(await page.locator('.lst-blk .lst-sec').nth(2).innerText(), /Up to 10 every day · 09:30/);

      // S4: the Paths step in a listening. One line for the trigger while a step is edited.
      await page.locator('.au-card[data-open="lp1"]').click();
      await page.waitForSelector('.au-ed .au-bseg');
      assert.match(await page.locator('.au-trig').innerText(), /6 sources · 6 rules · 10 a day/, tag + ': the trigger folds to one line');
      assert.match(await page.locator('.au-pcard').innerText(), /By tag/);
      assert.deepEqual(await page.locator('.au-pth .au-cond').allInnerTexts(), ['competitor', 'question'], tag + ': a path reads as its tag');
      assert.equal(await page.locator('.au-ed [data-op="lst-by"][aria-pressed="true"]').innerText(), 'By tag');
      assert.deepEqual(await page.locator('.au-ed [data-op="lst-tag"]').allInnerTexts(), ['+ buyer', '+ price'], tag + ': the Keep tags no path takes');
      assert.deepEqual(await page.locator('#au-fields option').evaluateAll(o => o.map(x => (x as HTMLOptionElement).value)), ['item.tag', 'item.title', 'item.site', 'item.score']);
      // A tag path is one field: no condition row, no minimum. The tag typed is its name and its condition.
      assert.equal(await page.locator('.au-ed [data-lst-tagpath]').count(), 2);
      assert.equal(await page.locator('.au-ed .au-cond-row').count(), 0, tag + ': no condition rows for tag paths');
      await page.locator('.au-ed [data-op="lst-tag"][data-v="buyer"]').click();
      await page.fill('.au-ed [data-b="step.paths.1.name"]', 'ask');
      // + Condition: the path then shows its rows, the field on its own line.
      await page.locator('.au-ed [data-path-ed="0"] [data-op="add"][data-tpl="cond"]').click();
      assert.equal(await page.locator('.au-ed [data-path-ed="0"] .au-cond-row').count(), 2);
      await page.fill('.au-ed [data-b="step.paths.0.when.1.field"]', 'item.site');
      await page.fill('.au-ed [data-b="step.paths.0.when.1.value"]', 'northwind.com');
      const fw = await page.locator('.au-ed [data-b="step.paths.0.when.1.field"]').evaluate(el => el.getBoundingClientRect().width);
      assert.ok(fw > 200, tag + ': a condition field is wide enough to read (' + fw + ')');
      const vw = await page.locator('.au-ed [data-b="step.paths.0.when.1.value"]').evaluate(el => el.getBoundingClientRect().width);
      assert.ok(vw > 140, tag + ': and its value (' + vw + ')');
      assert.equal(await page.locator('.au-ed [data-path-ed] .au-pct').evaluateAll(els => els.some(el => (el as HTMLElement).checkVisibility())), false, tag + ': no minimum');
      await page.locator('.au-ed [data-op="lst-by"][data-v="item"]').click();
      assert.deepEqual(await page.locator('.au-ed [data-op="lst-tag"]').allInnerTexts(), ['+ question', '+ price']);
      await shot(page, 's4-paths-desktop');
      await page.locator('.au-save').click();
      await page.waitForFunction(() => document.querySelector('.au-ver')?.textContent === 'v6');
      const step = (writes.at(-1)!.body as Body).steps![0]!;
      assert.equal(step.by, 'item');
      assert.deepEqual(step.paths![1], {name: 'ask', when: [{field: 'item.tag', op: 'is', value: 'ask'}], steps: step.paths![1]!.steps});
      assert.equal(step.paths![0]!.when.length, 2);
      assert.deepEqual(step.paths![2], {name: 'buyer', when: [{field: 'item.tag', op: 'is', value: 'buyer'}], steps: []});
      assert.match(await page.locator('.au-pcard').innerText(), /Each item/);

      // S3: the Runs tab opens the newest send on its counts and the text that went out.
      await page.locator('.au-hd .au-seg a', {hasText: 'Runs'}).click();
      await page.waitForSelector('.lst-run');
      assert.equal(await page.locator('.lst-run').count(), 1, tag + ': the newest send is open');
      assert.deepEqual(await page.locator('.lst-cr').allInnerTexts().then(t => t.map(x => x.replace(/\s+/g, ' ').trim())),
        ['Read 212', 'Duplicates removed 41', 'Dropped by rules 136', 'Could not score 2', 'Kept 33', 'Sent 3', 'Waiting 27']);
      assert.match(await page.locator('.lst-sent pre').innerText(), /^Market listening · Tue Nov 10, 14:00 · 3 of 30\n\n1\. competitor {2}Northwind Team plan is now \$15 a seat/);
      assert.equal(await page.locator('.au-rx .au-sl:not(.muted)').count(), 4, tag + ': then what the steps did');
      await shot(page, 's3-run-desktop');
      // A send the spend cap partly stopped reads Partly delivered: Sent is what a step got, the rest went back to waiting.
      const partly = page.locator('.au-rr').nth(1);
      assert.equal(await partly.locator('.au-dot').getAttribute('aria-label'), 'Partly delivered');
      assert.equal(await partly.locator('.au-ro').innerText(), 'Partly delivered · 1 of 3 not delivered (spend cap)');
      await partly.click();
      await page.waitForFunction(() => document.querySelectorAll('.lst-run').length === 2);
      assert.deepEqual(await page.locator('.lst-run').nth(1).locator('.lst-cr').allInnerTexts().then(t => t.map(x => x.replace(/\s+/g, ' ').trim())),
        ['Read 212', 'Duplicates removed 41', 'Dropped by rules 136', 'Could not score 2', 'Kept 33', 'Sent 2', 'Back to waiting 1', 'Waiting 27']);
      const missedLines = (await page.locator('.au-rx').nth(1).innerText()).split('spend cap · 1 not delivered, back to waiting').length - 1;
      assert.equal(missedLines, 2, tag + ': each stopped step names what it missed, a capped choose step too');
      assert.ok(!(await page.locator('.au-rx').nth(1).innerText()).includes('undefined'), tag + ': a step no longer in the flow names no one');

      // +: a new one is the viewer's, off, 10 a day, with one step that posts what was sent.
      await page.locator('.au-crumb a').click();
      await page.waitForSelector('.lst-row');
      await page.locator('.au-new').click();
      await page.waitForFunction(() => /^#\/listening\/au_\d+$/.test(location.hash));
      await page.waitForSelector('.lst-blk');
      const made = writes.at(-1)!, mb = made.body as {name: string; owner: string; enabled: boolean; trigger: Trigger; steps: {type: string; do: string}[]};
      assert.equal(made.method + ' ' + made.path, 'POST /api/v2/automations');
      assert.equal(mb.name, 'New listening');
      assert.equal(mb.owner, 'person:dana');
      assert.equal(mb.enabled, false);
      assert.deepEqual({...mb.trigger, tz: ''}, {type: 'listening', sources: [], rules: [], limits: [{count: 10, per: 'day'}], at: '08:00', tz: ''});
      assert.deepEqual(mb.steps.map(s => [s.type, s.do]), [['action', 'post_update']]);
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    // None yet: one line on what Listening does and one green New listening; the + hides until one exists. A double
    // press makes one listening.
    {
      const tag = 'none';
      let posts = 0;
      const {page, errors} = await openAutomations(browser, {viewport: DESKTOP, hash: '#/listening',
        tweak: fx => { fx.automations = fx.automations.filter(a => (a.trigger as {type?: string}).type !== 'listening'); },
        routes: async p => {
          await p.route('**/api/v2/automations', async route => {
            if (route.request().method() === 'POST') { posts++; await new Promise(r => setTimeout(r, 300)); }
            await route.fallback();
          });
        }});
      const empty = page.locator('.au-lst .empty-state.lst-empty');
      await empty.waitFor();
      assert.equal((await empty.locator('p').innerText()).trim(), 'Watches the sites and searches you pick, and posts what matters to Updates.', tag + ': what it does');
      assert.equal(await empty.locator('.ea-art').count(), 1, tag + ': its little picture');
      assert.deepEqual(await page.locator('.au-lst :is(button,a).primary:visible').allInnerTexts(), ['New listening'], tag + ': one green action');
      assert.equal(await page.locator('.au-lst .au-new').count(), 1, tag + ': the + stays in the page');
      assert.equal(await page.locator('.au-lst .au-new').isVisible(), false, tag + ': but hides');
      await empty.locator('[data-lst-new]').dblclick();
      await page.waitForFunction(() => /^#\/listening\/au_\d+$/.test(location.hash));
      await page.waitForSelector('.lst-blk');
      assert.equal(posts, 1, tag + ': a double press makes one listening');
      await page.locator('.au-crumb a').click();
      await page.waitForSelector('.lst-row');
      assert.equal(await page.locator('.au-lst .au-new').isVisible(), true, tag + ': the + is back once one exists');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    // Someone who may not change it reads the three parts and has nothing to add to.
    {
      const tag = 'reader';
      const {page, errors} = await openAutomations(browser, {viewport: DESKTOP, hash: '#/listening/au_pricing'});
      await page.waitForSelector('.lst-blk');
      assert.equal(await page.locator('.lst-blk [data-lst-add]').count(), 0, tag + ': no + Source, + Rule or + Limit');
      await page.locator('.lst-blk').click();
      await page.waitForSelector('.au-ed [data-b="trigger.sources.0.site"]');
      assert.equal(await page.locator('.au-ed :is(input,select,textarea):not([disabled])').count(), 0, tag + ': every field is off');
      assert.equal(await page.locator('.au-ed .au-save').count(), 0, tag + ': no Save');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
