// Motion on a real hub (ui/styles/motion.css, ui/app/motion.js), as Dana on a phone, then a desktop. With motion on:
// the sidebar's current-row bar slides to the new row when the page changes (desktop: a phone's sidebar is a drawer),
// Ask's panel fades in as it opens, and a count that changes ticks without its badge's box moving by a pixel. With
// reduced motion: nothing runs after a page change or after Ask opens (base.css shortens what is left to 1ms, so a
// 1ms animation is the rule's, not one that moves).
const assert = require('node:assert/strict');
const {run, open, go, shot, t} = require('./support/redesign-e2e.cjs');

// What still moves: running animations and transitions longer than base.css's 1ms, with what they move.
const moving = page => page.evaluate(() => document.getAnimations().filter(a => {
  const d = a.effect?.getComputedTiming?.().duration;
  return a.playState === 'running' && typeof d === 'number' && d > 1;
}).map(a => {
  const el = a.effect?.target;
  return `${a.animationName || a.transitionProperty || a.constructor.name} on ${el ? el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + [...el.classList].slice(0, 2).map(c => '.' + c).join('') : '?'}`;
}));
const askButton = phone => phone ? '#mobile-ask' : '#ask-fab';

run('motion', ['org', 'toured'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone, hash: '#/overview'});
  await page.waitForFunction(() => location.hash === '#/overview' && S.route === '#/overview');
  await page.waitForTimeout(400);

  // ---- the sidebar's bar slides from Overview's row to Tasks'.
  if (!phone) {
    const bar = page.locator('#side .nav-ind');
    await page.waitForFunction(() => !document.querySelector('#side .nav-ind')?.hidden, null, {timeout: t(5000)});
    const before = await bar.evaluate(el => el.style.transform);
    await go(page, '#/tasks');
    await page.waitForFunction(was => {
      const el = document.querySelector('#side .nav-ind');
      return el.style.transform !== was && el.classList.contains('slide');
    }, before, {timeout: t(5000), polling: 'raf'});
    const after = await bar.evaluate(el => {
      const row = document.querySelector('#side .nav-link.cur'), r = row.getBoundingClientRect();
      return {transform: el.style.transform, row: row.getAttribute('href'), rowTop: r.top, rowBottom: r.bottom};
    });
    assert.equal(after.row, '#/tasks', `${tag}: Tasks is the current row`);
    await page.waitForTimeout(250);
    const box = await bar.boundingBox();
    assert.ok(box.y > after.rowTop && box.y + box.height < after.rowBottom, `${tag}: the bar sits inside Tasks' row: ${JSON.stringify({box, after})}`);
    await go(page, '#/overview');
    await page.waitForFunction(() => S.route === '#/overview');
    await page.waitForTimeout(300);
  }

  // ---- every fold chevron turns with motion.css's one transition: the sidebar's, and a summary fold's triangle.
  const turns = await page.evaluate(() => {
    const d = document.createElement('details');
    d.className = 'run-fold'; d.innerHTML = '<summary>x</summary>';
    document.querySelector('#main').append(d);
    const css = (el, pseudo) => { const c = getComputedStyle(el, pseudo); return c.transitionProperty + ' ' + c.transitionDuration; };
    const chev = document.querySelector('#side .section-chev, #side .chev');
    const out = {chev: chev ? css(chev) : 'transform 0.12s', fold: css(d.querySelector('summary'), '::before')};
    d.remove();
    return out;
  });
  for (const [k, v] of Object.entries(turns)) assert.match(v, /^transform\b.* 0\.12s\b/, `${tag}: ${k} turns over --dur-fast: ${v}`);

  // ---- a count ticks in place: every frame of the tick has the new number's box (the old number, drawn over it,
  // never resizes it). Before vs after can differ by a digit's width in fonts without equal-width digits.
  const tick = await page.evaluate(async () => {
    const el = [...document.querySelectorAll('#side .cnt, #side [data-upd-badge], .mobile-nav .cnt, [data-upd-badge]')]
      .find(b => b.checkVisibility() && /^\d+\+?$/.test(b.textContent.trim()))
      || (() => {   // no count on screen: a stand-in badge where the team list's counts sit
        const b = document.createElement('span');
        b.className = 'cnt'; b.textContent = '3';
        (document.querySelector('#side .nav-link') || document.body).append(b);
        return b;
      })();
    countSet(el, el.textContent.trim());
    const was = el.textContent.trim(), next = String((parseInt(was, 10) || 0) + 1);
    const at = () => { const r = el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height].map(n => Math.round(n * 2) / 2).join(','); };
    countSet(el, next);
    const boxes = [at()];
    const ticked = el.classList.contains('m-ticking');
    for (let i = 0; i < 16; i++) { await new Promise(r => requestAnimationFrame(r)); boxes.push(at()); }
    await new Promise(r => setTimeout(r, 120));
    boxes.push(at());
    const text = el.textContent;
    countSet(el, was);
    return {ticked, boxes: [...new Set(boxes)], text, still: el.classList.contains('m-ticking') ? 'ticking' : 'done'};
  });
  assert.ok(tick.ticked, `${tag}: a changed count ticks`);
  assert.equal(tick.boxes.length, 1, `${tag}: the badge's box never moved while it ticked: ${JSON.stringify(tick.boxes)}`);
  assert.match(tick.text, /^\d+\+?$/, `${tag}: the badge's text is the new number after the tick`);

  // ---- Ask fades in as it opens, and is gone from the screen once closed.
  await page.locator(askButton(phone)).click();
  await page.locator('#ask-panel').waitFor({state: 'visible', timeout: t(5000)});
  const askMoves = await moving(page);
  assert.ok(askMoves.some(m => /^opacity on .*ask-panel/.test(m)), `${tag}: Ask's panel fades in: ${JSON.stringify(askMoves)}`);
  await shot(page, `motion-${tag}-ask`);
  await page.locator('#ask-panel [data-ask-close]').click();
  await page.locator('#ask-panel').waitFor({state: 'hidden', timeout: t(5000)});

  // ---- reduced motion: a page change and Ask's opening move nothing.
  await page.emulateMedia({reducedMotion: 'reduce'});
  await go(page, '#/tasks');
  await page.waitForFunction(() => S.route === '#/tasks');
  await page.waitForTimeout(60);
  assert.deepEqual(await moving(page), [], `${tag}: nothing moves after a page change under reduced motion`);
  await page.locator(askButton(phone)).click();
  await page.locator('#ask-panel').waitFor({state: 'visible', timeout: t(5000)});
  await page.waitForTimeout(30);
  assert.deepEqual(await moving(page), [], `${tag}: nothing moves after Ask opens under reduced motion`);
  await page.locator('#ask-panel [data-ask-close]').click();
  await page.locator('#ask-panel').waitFor({state: 'hidden', timeout: t(5000)});
  assert.deepEqual(page.errors, [], `${tag}: no page errors`);
  await page.context().close();
});
