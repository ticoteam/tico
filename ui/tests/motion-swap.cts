#!/usr/bin/env node
// motionSwap (ui/app/motion.ts), the keyed list patch every live list draws with, and the Tasks list, the first list
// drawn with it (ui/app/tasks-page.ts).
// The patch: a first fill moves nothing; an unchanged row keeps its node and a changed one is patched in place; a new
// row comes in with .m-enter and a gone one stays, inert and without its key, while it fades (.m-leave), then goes; a
// moved row keeps its node and its focus and slides from where it was, and a second change mid-slide starts from where
// the row is on the screen; a row that moves to another group keeps its node (280ms), also when its old group goes;
// past opts.max changes, and under reduced motion, the list is patched with no motion; a burst of calls builds once
// at once and once more for the newest markup; opts.key names another attribute.
// The Tasks list: a live change to one row leaves every row its node, the hovered row its hover and the focused row
// its focus, and a burst of live changes never builds the list twice for one paint.
// It ends by printing what one changed row in a 300-row list costs drawn whole and drawn through motionSwap.
import type {Browser, Page} from 'playwright';
import type * as TasksPage from './tasks-page.cts';
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, task, brief} = require('./tasks-page.cts') as TasksPage.Api;

/** What the page keeps for these checks: the list under test and how to write and read its rows. */
interface Kit {
  root: HTMLElement;
  fresh(): HTMLElement;
  rows(keys: string[], texts?: Record<string, string>): string;
  group(key: string, rows: string): string;
  node(key: string): HTMLElement;
  kept: HTMLElement[];
  paints: number[];
}
declare const __ms: Kit;
declare function motionSwap(root: Element, html: string, opts?: {key?: string; max?: number}): void;
declare function motionStill(): boolean;
declare function keptWrite(page: string, view: string, value: unknown): void;
declare function keptRead<T>(page: string, view: string): T | null;
declare function tasksLiveApply(events: {id: string; task?: unknown; gone?: boolean}[]): void;
declare let MOTION_SWAP_BUILDS: number;
declare let MOTION_PAINT: number;
declare let MOTION_TICKING: boolean;

/** Every build so far has been drawn and counted: the next call for the same list builds at once. */
const drawn = (page: Page) => page.waitForFunction(() => !MOTION_TICKING);
const moving = (page: Page) => page.evaluate(() => __ms.root.getAnimations({subtree: true}).length);

async function patch(page: Page) {
  await page.evaluate(() => {
    const kit: Kit = {
      root: document.createElement('div'), kept: [], paints: [],
      fresh() {
        kit.root.remove();
        kit.root = document.createElement('div');
        kit.root.style.cssText = 'position:fixed;left:0;top:0;width:320px;z-index:2147483647;background:#fff;color:#000';
        document.body.appendChild(kit.root);
        return kit.root;
      },
      rows: (keys, texts = {}) => keys.map(k => `<div class="r" data-key="${k}" tabindex="0" style="height:24px">${texts[k] ?? k}</div>`).join(''),
      group: (key, rows) => `<section data-key="g:${key}"><h2 style="margin:0;height:20px">${key}</h2>${rows}</section>`,
      node: key => kit.root.querySelector<HTMLElement>(`[data-key="${key}"]`)!,
    };
    (window as unknown as {__ms: Kit}).__ms = kit;
    kit.fresh();
  });

  // ---- a first fill
  const first = await page.evaluate(() => {
    const was = MOTION_SWAP_BUILDS;
    motionSwap(__ms.root, __ms.rows(['a', 'b', 'c']));
    return {builds: MOTION_SWAP_BUILDS - was, rows: __ms.root.children.length, enter: __ms.root.querySelectorAll('.m-enter').length};
  });
  assert.deepEqual(first, {builds: 1, rows: 3, enter: 0}, 'a first fill is drawn at once and nothing arrives');
  assert.equal(await moving(page), 0, 'a first fill moves nothing');

  // ---- kept, patched, new and gone
  await drawn(page);
  const second = await page.evaluate(async () => {
    const {root} = __ms, a = __ms.node('a'), b = __ms.node('b'), c = __ms.node('c');
    motionSwap(root, __ms.rows(['a', 'b', 'd'], {b: 'B2'}));
    await Promise.resolve();
    const d = __ms.node('d');
    return {a: __ms.node('a') === a, b: __ms.node('b') === b, text: b.textContent, enter: d.classList.contains('m-enter'),
      kept: a.classList.contains('m-enter') || b.classList.contains('m-enter'),
      leaving: c.isConnected && c.classList.contains('m-leave') && c.hasAttribute('inert') && c.getAttribute('aria-hidden') === 'true',
      keyed: c.hasAttribute('data-key'), list: root.classList.contains('m-list'),
      order: [...root.children].map(el => el.textContent)};
  });
  assert.deepEqual(second, {a: true, b: true, text: 'B2', enter: true, kept: false, leaving: true, keyed: false, list: true,
    order: ['a', 'B2', 'c', 'd']}, 'kept rows keep their nodes, the changed one is patched, the new one arrives, the gone one fades in place');
  await page.waitForFunction(() => !__ms.root.querySelector('.m-leave, .m-enter'));
  assert.deepEqual(await page.evaluate(() => [...__ms.root.children].map(el => el.getAttribute('data-key'))), ['a', 'b', 'd'],
    'the gone row is out once its fade ends, and the arrival is over');

  // ---- a move: the same node, its focus, and a slide from where it was
  await drawn(page);
  const moved = await page.evaluate(async () => {
    const {root} = __ms, a = __ms.node('a'), d = __ms.node('d');
    d.focus();
    const was = d.getBoundingClientRect().top;
    motionSwap(root, __ms.rows(['d', 'a', 'b'], {b: 'B2'}));
    await Promise.resolve();
    const slide = d.getAnimations()[0];
    return {same: __ms.node('d') === d && __ms.node('a') === a, top: root.firstElementChild === d, focus: document.activeElement === d,
      from: Math.abs(Math.round(d.getBoundingClientRect().top - was)), duration: slide?.effect?.getTiming().duration ?? 0,
      sliding: [d, a, __ms.node('b')].map(el => el.getAnimations().length)};
  });
  assert.deepEqual(moved, {same: true, top: true, focus: true, from: 0, duration: 180, sliding: [1, 1, 1]},
    'a moved row keeps its node and focus and starts its slide where it was; the rows it passed slide too');

  // ---- a second change mid-slide starts from where the row is
  await drawn(page);
  const cut = await page.evaluate(async () => {
    const d = __ms.node('d');
    for (const slide of d.getAnimations()) { slide.pause(); slide.currentTime = 90; }
    const was = d.getBoundingClientRect().top;
    motionSwap(__ms.root, __ms.rows(['a', 'b', 'd'], {b: 'B2'}));
    await Promise.resolve();
    return {jump: Math.abs(Math.round(d.getBoundingClientRect().top - was)), last: __ms.root.lastElementChild === d, slides: d.getAnimations().length};
  });
  assert.deepEqual(cut, {jump: 0, last: true, slides: 1}, 'a change mid-slide does not jump');

  // ---- groups: a row that changes group keeps its node, also when its old group goes
  await page.evaluate(() => { motionSwap(__ms.fresh(), __ms.group('one', __ms.rows(['a', 'b'])) + __ms.group('two', __ms.rows(['c']))); });
  await drawn(page);
  const regrouped = await page.evaluate(async () => {
    const b = __ms.node('b'), one = __ms.node('g:one'), a = __ms.node('a');
    motionSwap(__ms.root, __ms.group('one', __ms.rows(['a'])) + __ms.group('two', __ms.rows(['c', 'b'])));
    await Promise.resolve();
    return {same: __ms.node('b') === b && __ms.node('a') === a && __ms.node('g:one') === one, parent: b.parentElement === __ms.node('g:two'),
      duration: b.getAnimations()[0]?.effect?.getTiming().duration ?? 0, enter: __ms.root.querySelectorAll('.m-enter').length};
  });
  assert.deepEqual(regrouped, {same: true, parent: true, duration: 280, enter: 0}, 'a row moved to another group is the same node and slides for 280ms');
  await drawn(page);
  const emptied = await page.evaluate(async () => {
    const a = __ms.node('a'), one = __ms.node('g:one');
    motionSwap(__ms.root, __ms.group('two', __ms.rows(['c', 'b', 'a'])));
    await Promise.resolve();
    return {same: __ms.node('a') === a, parent: a.parentElement === __ms.node('g:two'), leaving: one.isConnected && one.classList.contains('m-leave')};
  });
  assert.deepEqual(emptied, {same: true, parent: true, leaving: true}, 'the last row of a group that goes keeps its node; the group fades');
  await page.waitForFunction(() => __ms.root.children.length === 1);

  // ---- past opts.max changes the list is patched with no motion
  await page.evaluate(() => { motionSwap(__ms.fresh(), __ms.rows(['a', 'b', 'c'])); });
  await drawn(page);
  const many = await page.evaluate(async () => {
    const a = __ms.node('a');
    motionSwap(__ms.root, __ms.rows(['x', 'y', 'z', 'a', 'b']), {max: 2});
    await Promise.resolve();
    return {same: __ms.node('a') === a, rows: __ms.root.children.length, marked: __ms.root.querySelectorAll('.m-enter, .m-leave').length};
  });
  assert.deepEqual(many, {same: true, rows: 5, marked: 0}, 'four changes past a max of two: patched, nothing arrives or fades');
  assert.equal(await moving(page), 0, 'and nothing slides');

  // ---- reduced motion: the setting on <html>, and the system's
  for (const how of ['html', 'system']) {
    await drawn(page);
    if (how === 'html') await page.evaluate(() => { document.documentElement.dataset['motion'] = 'reduce'; });
    else await page.emulateMedia({reducedMotion: 'reduce'});
    const still = await page.evaluate(async keys => {
      motionSwap(__ms.root, __ms.rows(keys));
      await Promise.resolve();
      return {still: motionStill(), rows: [...__ms.root.children].map(el => el.getAttribute('data-key')), marked: __ms.root.querySelectorAll('.m-enter, .m-leave').length,
        moving: __ms.root.getAnimations({subtree: true}).length};
    }, how === 'html' ? ['b', 'a', 'n'] : ['n', 'm', 'a']);
    assert.deepEqual(still, {still: true, rows: how === 'html' ? ['b', 'a', 'n'] : ['n', 'm', 'a'], marked: 0, moving: 0}, `reduced motion (${how}): patched, with no motion`);
    if (how === 'html') await page.evaluate(() => { delete document.documentElement.dataset['motion']; });
    else await page.emulateMedia({reducedMotion: 'no-preference'});
  }

  // ---- a burst builds once now and once more, for the newest markup
  await drawn(page);
  const burst = await page.evaluate(() => {
    const was = MOTION_SWAP_BUILDS;
    for (let i = 0; i < 50; i++) motionSwap(__ms.root, __ms.rows(['a', 'k' + i]));
    return {was, now: MOTION_SWAP_BUILDS - was, shown: !!__ms.root.querySelector('[data-key="k0"]')};
  });
  assert.deepEqual({now: burst.now, shown: burst.shown}, {now: 1, shown: true}, 'the first call of a burst is drawn before it returns; the rest wait');
  await page.waitForFunction(() => __ms.root.querySelector('[data-key="k49"]'));
  assert.equal(await page.evaluate(was => MOTION_SWAP_BUILDS - was, burst.was), 2, 'fifty calls in one frame build the list twice: the first and the newest');

  // ---- another key attribute
  await page.evaluate(() => { motionSwap(__ms.fresh(), '<p data-id="1">one</p><p data-id="2">two</p>', {key: 'data-id'}); });
  await drawn(page);
  assert.deepEqual(await page.evaluate(() => {
    const one = __ms.root.querySelector('[data-id="1"]');
    motionSwap(__ms.root, '<p data-id="2">two</p><p data-id="1">one</p>', {key: 'data-id'});
    return {same: __ms.root.querySelector('[data-id="1"]') === one, last: __ms.root.lastElementChild === one};
  }), {same: true, last: true}, 'opts.key names the attribute the rows are matched by');

  // ---- the kept view's two helpers (ui/app/core.ts)
  assert.deepEqual(await page.evaluate(() => { keptWrite('checks', 'mine', [1, 2]); return [keptRead('checks', 'mine'), keptRead('checks', 'all')]; }),
    [[1, 2], null], 'a kept read is given back for the view it was read for, and no other');
}

async function tasksList(page: Page, rows: ReturnType<typeof brief>[]) {
  await page.waitForFunction(n => document.querySelectorAll('#task-body [data-task]').length === n, rows.length);
  assert.deepEqual(await page.evaluate(() => ({rows: document.querySelectorAll('#task-body .tk-row[data-key^="t:"]').length,
    groups: [...document.querySelectorAll('#task-body .tk-section')].map(el => el.getAttribute('data-key'))})), {rows: rows.length, groups: ['g:Do']},
  'the list is keyed: its rows and its group');
  await page.hover('#task-body [data-task="t-3"]');
  await page.evaluate(changed => {
    __ms.kept = [...document.querySelectorAll<HTMLElement>('#task-body .tk-section, #task-body [data-task]')];
    document.querySelector<HTMLElement>('#task-body [data-task="t-5"]')!.focus();
    tasksLiveApply([{id: 't-7', task: changed}]);
  }, {...rows[6]!, title: 'Renamed', version: 4});
  await page.waitForFunction(() => document.querySelector('#task-body [data-task="t-7"] .tk-title')?.textContent === 'Renamed');
  assert.deepEqual(await page.evaluate(() => {
    const now = [...document.querySelectorAll<HTMLElement>('#task-body .tk-section, #task-body [data-task]')];
    return {same: now.length === __ms.kept.length && now.every((el, i) => el === __ms.kept[i]),
      hover: !!document.querySelector('#task-body [data-task="t-3"]:hover'), focus: document.activeElement?.getAttribute('data-task')};
  }), {same: true, hover: true, focus: 't-5'}, 'a live change to one row: every row keeps its node, the hovered row its hover, the focused row its focus');

  // A burst of live changes: each build is seen as it changes the list, with the paint it was made for.
  const burst = await page.evaluate(async list => {
    __ms.paints = [];
    new MutationObserver(() => { __ms.paints.push(MOTION_PAINT); })
      .observe(document.querySelector('#task-body')!, {childList: true, subtree: true, characterData: true});
    const was = MOTION_SWAP_BUILDS;
    let frames = 0;
    for (let i = 0; i < 40; i++) {
      const row = list[i % list.length]!;
      tasksLiveApply([{id: row.id, task: {...row, title: 'Burst ' + i, version: 10 + i}}]);
      if (i % 4 === 3) { await new Promise(requestAnimationFrame); frames++; }
    }
    return {was, frames};
  }, rows);
  await page.waitForFunction(() => [...document.querySelectorAll('#task-body .tk-title')].some(el => el.textContent === 'Burst 39'));
  const built = await page.evaluate(was => ({builds: MOTION_SWAP_BUILDS - was, paints: __ms.paints}), burst.was);
  assert.ok(built.builds >= 1 && built.builds <= burst.frames + 2, `forty live changes over ${burst.frames} frames built the list ${built.builds} times`);
  assert.equal(new Set(built.paints).size, built.paints.length, `no two builds for one paint (${built.paints.join(', ')})`);
  return built.builds;
}

// One changed row in a list of 300, drawn whole (as the list was) and through motionSwap: the median of 15, each with
// the layout it causes. Printed, not held: a time is this machine's.
async function cost(page: Page) {
  return page.evaluate(async () => {
    const keys = Array.from({length: 300}, (_, i) => 'r' + i);
    const html = (n: number) => __ms.rows(keys, {r150: 'changed ' + n});
    const median = (ms: number[]) => [...ms].sort((a, b) => a - b)[ms.length >> 1]!;
    const whole: number[] = [], swapped: number[] = [];
    const box = __ms.fresh();
    box.innerHTML = html(0);
    for (let n = 1; n <= 15; n++) { const at = performance.now(); box.innerHTML = html(n); void box.offsetHeight; whole.push(performance.now() - at); }
    const root = __ms.fresh();
    motionSwap(root, html(0));
    for (let n = 1; n <= 15; n++) {
      for (const was = MOTION_PAINT; MOTION_PAINT === was;) await new Promise(requestAnimationFrame);
      const at = performance.now();
      motionSwap(root, html(n));
      await Promise.resolve();
      void root.offsetHeight;
      swapped.push(performance.now() - at);
    }
    return {whole: median(whole), swapped: median(swapped)};
  });
}

(async () => {
  const browser: Browser = await chromium.launch({channel: process.env['TICO_BROWSER_CHANNEL'] ?? 'chrome', headless: true});
  try {
    const iso = (minutes: number) => new Date(Date.now() - minutes * 60000).toISOString();
    const tasks = Array.from({length: 12}, (_, i) => task('t-' + (i + 1), 'Task ' + (i + 1), 'spec',
      {owner_id: 'human:ana', requester_id: 'human:ana', created_at: iso(100 - i)}));
    const {page, errors} = await open(browser, {tasks, needs: []});
    await page.emulateMedia({reducedMotion: 'no-preference'});
    await patch(page);
    const builds = await tasksList(page, tasks.map(brief));
    const ms = await cost(page);
    assert.deepEqual(errors, [], 'no page errors');
    console.log(`motion-swap: ok (forty live changes: ${builds} builds; one changed row of 300: drawn whole ${ms.whole.toFixed(2)} ms, motionSwap ${ms.swapped.toFixed(2)} ms)`);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
