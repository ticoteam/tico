// Library on a real hub: its sidebar group folds behind a chevron (kept per browser, open again inside Library), and
// the Librarian's chat (ui/docs-ask.js) is the right rail on Docs, Meetings and Tools on a desktop, a sheet behind a
// button on a phone, with its sample questions on Docs only. An install without the Librarian shows none of it: no rail, no button, no settings link.
// Phone, then desktop.
const assert = require('node:assert/strict');
const {run, open, go, sideways, shot, t} = require('./support/redesign-e2e.cjs');

const LIBRARIAN = {name: 'librarian', display_name: 'Librarian', status: 'active'};
const rail = page => page.locator('[data-docs-ask]');
const libOpen = page => page.locator('#rd-lib').evaluate(el => !el.hidden && el.checkVisibility());
const at = (page, hash) => go(page, hash).then(() => page.waitForFunction(h => location.hash.split('?')[0] === h, hash.split('?')[0]));

run('redesign-library-rail', ['org', 'toured'], async ({hub, browser, phone, tag}) => {
  // ---- no Librarian on this hub: nothing of it on the Library pages
  let page = await open(browser, hub, 'dana', {phone, hash: '#/docs'});
  await page.locator('#docs-ask').waitFor({state: 'attached', timeout: t(10000)});
  assert.equal(await page.locator('#docs-ask').isVisible(), false, `${tag}: no Ask button`);
  assert.equal(await page.locator('#docs-librarian-settings').count(), 0, `${tag}: no settings link`);
  assert.equal(await rail(page).count(), 0, `${tag}: no rail`);
  await at(page, '#/meetings');
  await page.locator('#meet-ask').waitFor({state: 'attached', timeout: t(10000)});
  assert.equal(await page.locator('#meet-ask').isVisible(), false, `${tag}: no Ask on Meetings`);
  await page.context().close();

  // ---- with the Librarian (the hub's roster plus it)
  page = await open(browser, hub, 'dana', {phone});
  await page.context().route('**/api/employees', async r => {
    const res = await r.fetch();
    r.fulfill({response: res, json: [...await res.json(), LIBRARIAN]});
  });
  await page.reload();
  await page.waitForFunction(() => document.querySelector('#main')?.children.length > 0, null, {timeout: t(20000)});
  await at(page, '#/docs');
  await page.locator('.docs-actions #docs-librarian-settings').waitFor({timeout: t(10000)});
  assert.equal(await page.locator('#docs-librarian-settings').getAttribute('href'), '#/bot/librarian/more');
  assert.equal(await page.locator('#side .nav-librarian, #nav-strategy-librarian').count(), 0, `${tag}: no Librarian gear in the sidebar`);

  if (phone) {
    for (const [hash, button] of [['#/meetings', '#meet-ask'], ['#/library/tools', '#lc-ask']]) {
      await at(page, hash);
      await page.locator(button).waitFor({timeout: t(10000)});
      assert.equal(await rail(page).count(), 0, `${tag} ${hash}: no rail until asked`);
      await page.locator(button).click();
      assert.equal(await rail(page).getAttribute('aria-modal'), 'true', `${tag} ${hash}: a sheet`);
      assert.equal(await rail(page).locator('[data-hint]').count(), 0, `${tag} ${hash}: no doc questions off Docs`);
      assert.equal(await sideways(page), false);
      await shot(page, `library-rail-${tag}-${hash.split('/').pop()}`);
      await page.locator('.dask-close').click();
      assert.equal(await rail(page).count(), 0);
    }
  } else {
    // the rail stays on every Library page and leaves the others
    for (const hash of ['#/docs', '#/meetings', '#/library/tools']) {
      await at(page, hash);
      await rail(page).waitFor({timeout: t(10000)});
      assert.equal(await rail(page).getAttribute('role'), 'complementary', `${tag} ${hash}: a rail`);
      // The Librarian reads docs, so its sample questions are doc questions, offered on Docs only.
      if (hash === '#/docs') await rail(page).locator('[data-hint]').first().waitFor({timeout: t(5000)});
      else await page.waitForFunction(() => !document.querySelector('[data-docs-ask] [data-hint]'), null, {timeout: t(5000)});
      assert.deepEqual(await rail(page).locator('[data-hint]').allInnerTexts(),
        hash === '#/docs' ? ['How do we handle a refund?', 'Who helps new hires?', 'Where is the pricing?'] : [], `${tag} ${hash}: hints`);
      const main = await page.locator('#main').boundingBox(), side = await rail(page).boundingBox();
      assert.ok(main.x + main.width <= side.x + 1, `${tag} ${hash}: the page sits beside the rail`);
      assert.equal(await sideways(page), false);
      await shot(page, `library-rail-${tag}-${hash.split('/').pop()}`);
    }
    await at(page, '#/tasks');
    await page.waitForFunction(() => !document.querySelector('[data-docs-ask]'), null, {timeout: t(5000)});

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
    await shot(page, `library-rail-${tag}-nav`);
  }
  assert.deepEqual(page.errors, [], `${tag}: no page errors`);
  await page.context().close();
});
