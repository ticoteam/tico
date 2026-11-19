#!/usr/bin/env node
// The page change (ui/app/router.ts routeChanged, ui/app/motion.ts motionPage).
// A move to another page draws it at once and fades #main alone, for 120ms: the sidebar is the same node, with nothing
// animating on it, and what is under the pointer is the page (no layer over it). A new page starts at its top; Back
// puts the reader where they were, in the pane the page scrolls in (the Tasks list has its own). The keyboard's focus is on the new page's title, with no ring when a pointer made
// the change and with one when a key did. Under reduced motion the page is drawn with no fade.
import type {Browser, Page} from 'playwright';
import type * as TasksPage from './tasks-page.cts';
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, task} = require('./tasks-page.cts') as TasksPage.Api;

/** The sidebar's nodes as they were before the first page change. */
interface Marks {side: Element | null; scroll: Element | null; link: Element | null}
declare const __nav: Marks;
const OTHER = '#/changelog';

/** Goes to `hash` and reads the page in the same task as its draw: the fade is read as it starts. */
const arrive = (page: Page, hash: string) => page.evaluate(async h => {
  await new Promise<void>(done => { addEventListener('hashchange', () => done(), {once: true}); location.hash = h; });
  const main = document.querySelector<HTMLElement>('#main')!, side = document.querySelector('#side')!;
  const title = main.querySelector<HTMLElement>('h1') ?? main, box = main.getBoundingClientRect();
  return {
    fade: main.getAnimations().filter(a => 'opacity' in ((a.effect as KeyframeEffect).getKeyframes()[0] ?? {})).map(a => a.effect?.getTiming().duration ?? 0),
    moves: main.getAnimations().filter(a => !(a instanceof CSSTransition)).length,
    side: side === __nav.side && side.querySelector('.side-scroll') === __nav.scroll && side.querySelector('.nav-link') === __nav.link,
    sideMoves: side.getAnimations().length, sideOpacity: getComputedStyle(side).opacity,
    under: !!document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2)?.closest('#main'),
    focus: document.activeElement === title, quiet: title.hasAttribute('data-quiet-focus'), titled: title !== main,
    top: main.scrollTop + (main.querySelector('#tasks-pane')?.scrollTop ?? 0)};
}, hash);

(async () => {
  const browser: Browser = await chromium.launch({channel: process.env['TICO_BROWSER_CHANNEL'] ?? 'chrome', headless: true});
  try {
    const iso = (minutes: number) => new Date(Date.now() - minutes * 60000).toISOString();
    const tasks = Array.from({length: 80}, (_, i) => task('t-' + (i + 1), 'Task ' + (i + 1), 'spec',
      {owner_id: 'human:ana', requester_id: 'human:ana', created_at: iso(200 - i)}));
    const {page, errors} = await open(browser, {tasks, needs: []});
    await page.emulateMedia({reducedMotion: 'no-preference'});
    await page.waitForFunction(() => document.querySelectorAll('#task-body [data-task]').length === 80);
    const place = await page.evaluate(async () => {
      const side = document.querySelector('#side');
      (window as unknown as {__nav: Marks}).__nav = {side, scroll: side?.querySelector('.side-scroll') ?? null, link: side?.querySelector('.nav-link') ?? null};
      const pane = document.querySelector<HTMLElement>('#tasks-pane')!;
      await new Promise<void>(done => { pane.addEventListener('scroll', () => done(), {once: true}); pane.scrollTop = 700; });
      return {top: pane.scrollTop, side: !!side};
    });
    assert.deepEqual(place, {top: 700, side: true}, 'the Tasks list is tall enough to scroll');

    // ---- on to another page: the sidebar stays, the page starts at its top
    const away = await arrive(page, OTHER);
    assert.equal(away.side, true, 'the sidebar is the same node across a page change');
    assert.deepEqual([away.sideMoves, away.sideOpacity], [0, '1'], 'nothing animates on the sidebar');
    assert.equal(away.under, true, 'what is under the pointer is the page');
    assert.equal(away.top, 0, 'a new page starts at its top');

    // ---- Back: the place the reader left
    await page.goBack();
    await page.waitForFunction(() => location.hash === '#/tasks' && Math.abs((document.querySelector('#tasks-pane')?.scrollTop ?? 0) - 700) <= 1);
    assert.equal(await page.evaluate(() => document.querySelector('#side') === __nav.side), true, 'Back keeps the sidebar too');

    // ---- a new visit: #main fades, the focus is on the title, quietly after a pointer
    await arrive(page, OTHER);
    const here = await arrive(page, '#/tasks');
    assert.deepEqual([here.fade, here.moves], [[120], 1], '#main alone fades in, opacity only, for 120ms');
    assert.deepEqual({side: here.side, sideMoves: here.sideMoves, under: here.under, top: here.top},
      {side: true, sideMoves: 0, under: true, top: 0}, 'the sidebar stays, nothing covers the page, and a new visit starts at the top');
    assert.deepEqual({titled: here.titled, focus: here.focus, quiet: here.quiet}, {titled: true, focus: true, quiet: true},
      "the focus is on the page's title, with no ring when no key was pressed");
    await page.waitForFunction(() => document.querySelectorAll('#task-body [data-task]').length === 80);
    assert.equal(await page.evaluate(() => document.activeElement === document.querySelector('#main h1')), true, 'the focus is still on the title once the rows are in');

    // ---- a key made the change: the title shows its ring
    await arrive(page, OTHER);
    await page.keyboard.press('Shift');
    const keyed = await arrive(page, '#/tasks');
    assert.deepEqual({focus: keyed.focus, quiet: keyed.quiet}, {focus: true, quiet: false}, 'after a key the title is focused with its ring');

    // ---- reduced motion: drawn at once, no fade
    await page.emulateMedia({reducedMotion: 'reduce'});
    await arrive(page, OTHER);
    const still = await arrive(page, '#/tasks');
    assert.deepEqual({fade: still.fade, focus: still.focus}, {fade: [], focus: true}, 'reduced motion: no fade, the focus still moves');
    assert.deepEqual(errors, [], 'no page errors');
    console.log('motion-page: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
