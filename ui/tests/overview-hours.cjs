// Overview "Bot hours vs human hours": the headline ratio for the last 7 days, the weekly line with a dashed Parity
// line and the current week dotted, a week's numbers on hover or tap, the weekly table on a click, a phone width
// without sideways scrolling, and the three empty cases (no people, no runs, under a week of runs). Fixtures only;
// the 3D renderer is replaced by a stand-in.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');

const SCENE = `export function createBuilding() {
  document.body.dataset.sceneReady = 'true'; document.querySelector('#loading').hidden = true;
  return {update() {}, link() { return true; }, clearLinks() {}, dispose() {}};
}`;
const week = (day, bot, people, so_far = false) => ({week: day, bot_hours: bot, people, human_hours: people * 40,
  ratio: people ? Math.round(bot / (people * 40) * 100) / 100 : null, so_far});
const answer = (over = {}) => ({zone: 'America/Los_Angeles', now: '2026-10-14T19:00:00.000000Z', first_run: '2026-09-29T16:00:00.000000Z',
  people: 1, hours_per_person: 40, gap_minutes: 30, last_7_days: {bot_hours: 243, human_hours: 40, ratio: 6.08, full: true},
  weeks: [week('2026-09-28', 20, 1), week('2026-10-05', 80, 1), week('2026-10-12', 30, 1, true)], ...over});

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 860}, serviceWorkers: 'block'});
    const errors = [];
    let hours = answer();
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url()), p = url.pathname;
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/tico/ui/overview/building.js') return route.fulfill({contentType: 'application/javascript', body: SCENE});
      if (p.startsWith('/tico/ui/')) {
        const name = p.slice('/tico/ui/'.length), file = uiFile(name);
        if (fs.existsSync(file)) return route.fulfill({body: fs.readFileSync(file), contentType: name.endsWith('.html') ? 'text/html'
          : name.endsWith('.css') ? 'text/css' : name.endsWith('.js') ? 'application/javascript' : 'application/octet-stream'});
      }
      if (p === '/api/v2/overview/bot-hours') return json(hours);
      if (p === '/api/employees') return json([]);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], org_groups: []});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, config: {company_name: 'Acme Studio'}});
      return json({});
    });
    await page.goto('http://tico-ui.test/');
    await page.waitForFunction(() => location.hash === '#/updates');
    await page.evaluate(() => location.hash = '#/overview');
    const card = page.locator('#overview-hours');
    await card.locator('.ovh-plot').waitFor();

    // The headline, one decimal below 10; the Parity line dashed at 1×; the current week dotted, a hollow point.
    assert.equal(await card.locator('.ovh-head').innerText(), '6.1× bot hours per human hour · last 7 days');
    assert.match(await card.innerText(), /Bot hours = time bots spent working \(pauses over 30 min left out\)\. Human hours =\s+people in your org chart × 40 h a week\./);
    assert.notEqual(await card.locator('.ovh-parity').evaluate(l => getComputedStyle(l).strokeDasharray), 'none');
    assert.equal(await card.locator('.ovh-parity-label').innerText(), 'Parity 1×');
    assert.equal(await card.locator('.ovh-line').count(), 2, 'finished weeks solid, then the week so far');
    assert.notEqual(await card.locator('.ovh-so-far').evaluate(l => getComputedStyle(l).strokeDasharray), 'none');
    assert.equal(await card.locator('.ovh-dot').count(), 3);
    assert.equal(await card.locator('.ovh-dot-so-far').count(), 1);
    // A 80-hour week with one person: 2.0×; points sit on their week, above or below Parity.
    const tops = await card.locator('.ovh-dot').evaluateAll(ds => ds.map(d => parseFloat(d.style.top)));
    const parity = parseFloat(await card.locator('.ovh-parity-label').evaluate(l => l.style.top));
    assert.ok(tops[0] > parity && tops[1] < parity, 'a 0.5× week below the parity line, a 2.0× week above it');

    // Hover a week: its numbers. Leaving hides them.
    const plot = card.locator('.ovh-plot'), box = await plot.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    assert.equal(await card.locator('.ovh-tip').innerText(), 'Week of Oct 5: 80 bot hours · 40 human hours · 2.0×');
    await page.mouse.move(box.x + box.width - 2, box.y + box.height / 2);
    assert.equal(await card.locator('.ovh-tip').innerText(), 'Week of Oct 12 (so far): 30 bot hours · 40 human hours · 0.8×');
    await page.mouse.move(5, 5);
    assert.equal(await card.locator('.ovh-tip').isHidden(), true);
    // A click on the chart shows the week rather than opening the table.
    await plot.click({position: {x: 3, y: box.height / 2}});
    assert.equal(await card.locator('.ovh-tip').innerText(), 'Week of Sep 28: 20 bot hours · 40 human hours · 0.5×');
    assert.equal(await page.locator('#overview-hours-dialog[open]').count(), 0);

    // A click on the card: the weekly table, newest week first.
    await card.locator('.ovh-head').click();
    const dialog = page.locator('#overview-hours-dialog[open]');
    await dialog.waitFor();
    assert.deepEqual(await dialog.locator('thead th').allTextContents(), ['Week', 'Bot hours', 'People', 'Human hours', 'Ratio']);
    const rows = await dialog.locator('tbody tr').evaluateAll(trs => trs.map(tr => [...tr.cells].map(c => c.innerText.trim())));
    assert.deepEqual(rows, [['Oct 12 so far', '30', '1', '40', '0.8×'], ['Oct 5', '80', '1', '40', '2.0×'], ['Sep 28', '20', '1', '40', '0.5×']]);
    await dialog.locator('[data-close]').click();
    // The keyboard opens it too.
    await card.focus(); await page.keyboard.press('Enter');
    await dialog.waitFor();
    await page.keyboard.press('Escape');

    // Three people: 120 human hours a week; ten and over reads whole.
    hours = answer({people: 3, last_7_days: {bot_hours: 1300, human_hours: 120, ratio: 10.83, full: true},
      weeks: [week('2026-10-05', 300, 3), week('2026-10-12', 100, 3, true)]});
    await page.evaluate(() => overviewHoursLoad());
    await card.locator('.ovh-head', {hasText: '11×'}).waitFor();
    await card.locator('.ovh-head').click();
    assert.deepEqual(await dialog.locator('tbody tr').first().locator('td').allInnerTexts(), ['100', '3', '120', '0.8×']);
    await page.keyboard.press('Escape');

    // On a phone the card is readable and the chart fits without scrolling sideways; a tap shows a week.
    await page.setViewportSize({width: 375, height: 740});
    const fit = await card.evaluate(el => ({card: el.scrollWidth <= el.clientWidth, doc: document.documentElement.scrollWidth <= innerWidth,
      plot: el.querySelector('.ovh-plot').getBoundingClientRect().right <= innerWidth}));
    assert.deepEqual(fit, {card: true, doc: true, plot: true});
    const phone = await plot.boundingBox();
    await plot.tap({position: {x: phone.width - 2, y: phone.height / 2}}).catch(() => plot.click({position: {x: phone.width - 2, y: phone.height / 2}}));
    assert.match(await card.locator('.ovh-tip').innerText(), /^Week of Oct 12 \(so far\): 100 bot hours · 120 human hours · 0\.8×$/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'the shown week fits too');
    await page.setViewportSize({width: 1280, height: 860});

    // No people on the org chart: bot hours only.
    hours = answer({people: 0, last_7_days: {bot_hours: 12.34, human_hours: 0, ratio: null, full: true},
      weeks: [week('2026-10-05', 10, 0), week('2026-10-12', 2.3, 0, true)]});
    await page.evaluate(() => overviewHoursLoad());
    await card.locator('.ovh-note', {hasText: 'Add people to the org chart to compare'}).waitFor();
    assert.equal(await card.locator('.ovh-head').innerText(), '12 bot hours · last 7 days');
    assert.equal(await card.locator('.ovh-plot').count(), 0);

    // Under a week of runs: the headline so far, and the chart waits.
    hours = answer({first_run: '2026-10-12T16:00:00.000000Z', last_7_days: {bot_hours: 6.4, human_hours: 40, ratio: 0.16, full: false},
      weeks: [week('2026-10-12', 6, 1, true)]});
    await page.evaluate(() => overviewHoursLoad());
    await card.locator('.ovh-note', {hasText: 'Chart fills in as weeks pass'}).waitFor();
    assert.equal(await card.locator('.ovh-head').innerText(), '0.2× bot hours per human hour · last 7 days');

    // No bot runs yet: a link to Bots, which opens Settings on its Bots tab rather than the table.
    hours = answer({first_run: null, last_7_days: {bot_hours: 0, human_hours: 40, ratio: 0, full: false}, weeks: []});
    await page.evaluate(() => overviewHoursLoad());
    await card.locator('.ovh-head', {hasText: 'No bot work yet'}).waitFor();
    const bots = card.getByRole('link', {name: 'Bots'});
    assert.equal(await bots.getAttribute('href'), '#/settings');
    await bots.click();
    await page.waitForFunction(() => location.hash === '#/settings' && SETTINGS_TAB === 'bots');
    assert.equal(await page.locator('#overview-hours-dialog[open]').count(), 0);
    assert.deepEqual(errors, []);
    console.log('overview-hours: ok');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
