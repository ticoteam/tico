// Journey: sign in, as Dana. A teammate's token opens the app with the teammate's account drawn and the first page
// rendered; a token the hub does not know is refused (401) and the app does not open. Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, expectFailure, expectError, assertClean, t, watch} = require('./support/e2e.cjs');
const {DESKTOP, PHONE} = require('./support/real-hub.cjs');

journey('e2e-sign-in', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';
  const page = await open(browser, hub, 'dana', {phone});
  const email = (await page.locator('#account .account-email').innerText()).trim();
  assert.match(email, /dana@acme\.example/, `${where}: the account names the signed-in teammate`);
  assert.ok(await page.locator('#main').evaluate(el => el.children.length > 0), `${where}: the first page is drawn`);
  assertClean(page, `${where}: signed in`);
  await page.context().close();

  // A token the hub does not know: the hub answers 401 and the app does not open as anyone.
  const context = await browser.newContext({...(phone ? PHONE : DESKTOP), serviceWorkers: 'block',
    extraHTTPHeaders: {Authorization: 'Bearer not-a-real-token'}});
  const bad = watch(await context.newPage());
  expectFailure(bad, 401, '^/api/v2/', 'GET');
  expectError(bad, /status of 401/, 'the refused token is logged by the browser as a failed load');
  await bad.goto(hub.url + '/');
  await bad.waitForTimeout(t(3000));
  const shown = (await bad.locator('#account .account-email').innerText().catch(() => '')).trim();
  assert.ok(!/dana@acme\.example/.test(shown), `${where}: an unknown token does not open the account`);
  assertClean(bad, `${where}: unknown token`);
  await context.close();
});
