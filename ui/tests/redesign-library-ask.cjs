// Library on a real hub: Docs, Meetings and Tools each have an Ask button that opens the Ask panel
// (ui/app/ask-panel.js), which sees the page; no Librarian rail, sidebar row or settings link. The Library sidebar
// group folds behind a chevron (kept per browser, open again inside Library). Phone, then desktop.
const assert = require('node:assert/strict');
const {run, open, go, sideways, shot, t} = require('./support/redesign-e2e.cjs');

const libOpen = page => page.locator('#rd-lib').evaluate(el => !el.hidden && el.checkVisibility());
const at = (page, hash) => go(page, hash).then(() => page.waitForFunction(h => location.hash.split('?')[0] === h, hash.split('?')[0]));

run('redesign-library-ask', ['org', 'toured'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone, hash: '#/docs'});
  for (const [hash, button] of [['#/docs', '#docs-ask'], ['#/meetings', '#meet-ask'], ['#/library/tools', '#lc-ask']]) {
    await at(page, hash);
    const ask = page.locator(button), panel = page.locator('#ask-panel');
    await ask.waitFor({timeout: t(10000)});
    assert.equal(await ask.getAttribute('aria-label'), 'Ask', `${tag} ${hash}: Ask`);
    await ask.click();
    await panel.waitFor({timeout: t(5000)});
    // It sees the page whether or not this person's Assistant is on yet (the hub's may still be off).
    await panel.locator('[data-ask-sees-name]').waitFor({timeout: t(10000)});
    assert.equal(await panel.locator('[data-ask-sees-name]').innerText(), await page.locator('#main h1').first().innerText(), `${tag} ${hash}: it sees the page`);
    assert.equal(await sideways(page), false);
    await shot(page, `library-ask-${tag}-${hash.split('/').pop()}`);
    await panel.locator('[data-ask-close]').first().click();
    await panel.waitFor({state: 'hidden', timeout: t(5000)});
  }
  assert.equal(await page.locator('[data-docs-ask], #docs-librarian-settings, #side .nav-librarian, #nav-strategy-librarian').count(), 0,
    `${tag}: nothing of the Librarian`);

  if (!phone) {
    await at(page, '#/tasks');   // outside Library, where the fold is the person's own choice
    // ---- Library folds: kept across a reload, open again inside Library, closed again outside it
    const toggle = page.locator('.rd-lib-toggle');
    assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
    assert.equal(await libOpen(page), true);
    await toggle.click();
    assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
    assert.equal(await libOpen(page), false);
    assert.equal(await page.evaluate(() => localStorage.getItem('tico.nav.library')), 'closed');
    await page.reload();
    await page.waitForFunction(() => document.querySelector('.rd-lib-toggle')?.getAttribute('aria-expanded') === 'false', null, {timeout: t(20000)});
    assert.equal(await libOpen(page), false, 'kept across a reload');
    await page.locator('#side a.nav-link[data-nav="library"]').click();
    await page.waitForFunction(() => location.hash.startsWith('#/library') || location.hash.startsWith('#/docs'));
    await page.waitForFunction(() => !document.getElementById('rd-lib').hidden, null, {timeout: t(5000)});
    await page.locator('#rd-lib a.nav-link[data-nav="meetings"]').click();
    await page.waitForFunction(() => location.hash.startsWith('#/meetings'));
    assert.equal(await libOpen(page), true, 'open while inside Library');
    await at(page, '#/tasks');
    await page.waitForFunction(() => document.getElementById('rd-lib').hidden, null, {timeout: t(5000)});
    await toggle.click();
    assert.equal(await libOpen(page), true);
    assert.equal(await page.evaluate(() => localStorage.getItem('tico.nav.library')), 'open');
    await shot(page, `library-ask-${tag}-nav`);
  }
  assert.deepEqual(page.errors, [], `${tag}: no page errors`);
  await page.context().close();
});
