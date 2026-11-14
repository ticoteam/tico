// Library on a real hub: Tools › Keys has an Ask button that opens the Ask panel (ui/app/ask-panel.ts), which sees the
// page (Docs' and Meetings' Ask buttons are Chat now: page-chat-pages.cts); no Librarian rail, sidebar row or settings link. The Library and Company sidebar
// groups fold behind a chevron (kept per browser, open again on their own pages).
// Desktop only: the folds are the desktop sidebar's, and the phone's Ask is the same panel (ask-panel.cts).
import type {Page} from 'playwright';
import type * as RE from './support/redesign-e2e.cts';
const assert = require('node:assert/strict');
const {run, open, go, sideways, shot, t} = require('./support/redesign-e2e.cts') as RE.Api;

const libOpen = (page: Page) => page.locator('#rd-lib').evaluate(el => !(el as HTMLElement).hidden && el.checkVisibility());
const at = (page: Page, hash: string) => go(page, hash).then(() => page.waitForFunction(h => location.hash.split('?')[0] === h, hash.split('?')[0]!));

run('redesign-library-ask', ['org', 'toured'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone, hash: '#/docs'});
  for (const [hash, button] of [['#/tools/keys', '#lc-ask']] as const) {
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
    const toggle = page.locator('.rd-lib-toggle:not(.rd-rules-toggle):not(.rd-co-toggle)');
    // Library starts folded: open while on its pages above, folded again once this page is drawn.
    await page.waitForFunction(() => document.querySelector('[aria-controls="rd-lib"]')?.getAttribute('aria-expanded') === 'false', null, {timeout: t(5000)});
    assert.equal(await libOpen(page), false);
    await toggle.click();
    assert.equal(await page.evaluate(() => localStorage.getItem('tico.nav.library')), 'open');
    assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
    assert.equal(await libOpen(page), true);
    await toggle.click();
    assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
    assert.equal(await libOpen(page), false);
    assert.equal(await page.evaluate(() => localStorage.getItem('tico.nav.library')), 'closed');
    await page.reload();
    await page.waitForFunction(() => document.querySelector('[aria-controls="rd-lib"]')?.getAttribute('aria-expanded') === 'false', null, {timeout: t(20000)});
    assert.equal(await libOpen(page), false, 'kept across a reload');
    await page.locator('#side a.nav-link[data-nav="library"]').click();
    await page.waitForFunction(() => location.hash.startsWith('#/library') || location.hash.startsWith('#/docs'));
    await page.waitForFunction(() => !document.getElementById('rd-lib')!.hidden, null, {timeout: t(5000)});
    await page.locator('#rd-lib a.nav-link[data-nav="meetings"]').click();
    await page.waitForFunction(() => location.hash.startsWith('#/meetings'));
    assert.equal(await libOpen(page), true, 'open while inside Library');
    await at(page, '#/tasks');
    await page.waitForFunction(() => document.getElementById('rd-lib')!.hidden, null, {timeout: t(5000)});
    await toggle.click();
    assert.equal(await libOpen(page), true);
    assert.equal(await page.evaluate(() => localStorage.getItem('tico.nav.library')), 'open');
    await shot(page, `library-ask-${tag}-nav`);
    // ---- Company folds the same way: open on Strategy, Finance or Growth, folded again outside them; #/home lights its row
    const co = page.locator('#side .rd-co-toggle');
    const coOpen = () => page.locator('#rd-co').evaluate(el => !(el as HTMLElement).hidden && el.checkVisibility());
    assert.equal(await coOpen(), false, 'Company starts folded');
    await co.click();
    assert.equal(await coOpen(), true);
    await co.click();
    assert.equal(await coOpen(), false);
    assert.equal(await page.evaluate(() => localStorage.getItem('tico.nav.company')), 'closed');
    await at(page, '#/growth');
    await page.waitForFunction(() => !document.getElementById('rd-co')!.hidden, null, {timeout: t(5000)});
    await page.waitForFunction(() => document.querySelector('#rd-co a[data-nav="growth"]')?.getAttribute('aria-current') === 'page', null, {timeout: t(5000)});
    await page.locator('#side a[data-nav="overview"]').click();
    await page.waitForFunction(() => location.hash === '#/home');
    await page.waitForFunction(() => document.getElementById('rd-co')!.hidden, null, {timeout: t(5000)});
    await page.waitForFunction(() => document.querySelector('#side a[data-nav="overview"]')?.getAttribute('aria-current') === 'page', null, {timeout: t(5000)});
    await co.click();
    assert.equal(await coOpen(), true);
    assert.equal(await page.evaluate(() => localStorage.getItem('tico.nav.company')), 'open');
  }
  assert.deepEqual(page.errors, [], `${tag}: no page errors`);
  await page.context().close();
}, {only: 'desktop'});
