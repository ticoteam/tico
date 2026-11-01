// Journey 4 on a real hub (redesign TESTING.md §L3): Dana's "Route mail" forks on its Decide answer. On a phone the
// path cards swipe, a dot per path following along; a path's name opens the Paths sheet, where a new path gets a
// condition on the Decide answer (d1.bucket is reply, at least 90%). Saved, the hub's flow has it, and a test run takes
// the Reply path (the steady judge answers reply at 82%, short of 90%). Phone, then desktop.
const assert = require('node:assert/strict');
const {run, open, ok, automationNamed, sideways, shot, t} = require('./support/redesign-e2e.cjs');

const selectedDot = page => page.evaluate(() => [...document.querySelectorAll('.au-dots [data-dot]')]
  .findIndex(d => d.getAttribute('aria-selected') === 'true'));

run('redesign-e2e-paths', ['org', 'toured', 'automations'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone, hash: '#/automations'});
  const route = await automationNamed(page, 'Route mail');
  await page.locator(`.au-row[data-row="${route.id}"] a`).click();
  await page.locator('.au-hd .au-name:text("Route mail")').waitFor();
  const strip = page.locator('.au-paths');
  await strip.waitFor();
  assert.deepEqual(await page.locator('.au-pth .au-pname').allInnerTexts(), ['Reply', 'Archive', 'Otherwise']);

  if (phone) {
    assert.equal(await page.locator('.au-dots [data-dot]').count(), 3);
    assert.equal(await selectedDot(page), 0);
    // A swipe left: the next card comes in and its dot lights.
    await strip.evaluate(el => el.scrollBy({left: el.clientWidth * 0.8}));
    await page.waitForFunction(() => document.querySelectorAll('.au-dots [data-dot]')[1].getAttribute('aria-selected') === 'true',
      null, {timeout: t(5000)});
    await shot(page, `${tag}-01-swiped`);
    await page.locator('.au-dots [data-dot="other"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.au-dots [data-dot]')[2].getAttribute('aria-selected') === 'true');
  } else {
    assert.equal(await page.locator('.au-dots').isVisible(), false);
    const tops = await page.locator('.au-pth').evaluateAll(cols => cols.map(c => Math.round(c.getBoundingClientRect().top)));
    assert.equal(new Set(tops).size, 1, 'the paths sit side by side');
    await shot(page, `${tag}-01-paths`);
  }
  assert.equal(await sideways(page), false);

  // A new path with a condition on the Decide answer.
  await page.locator('.au-pname[data-path="0"]').click();
  await page.locator('.au-ed [data-b="step.paths.0.name"]').waitFor();
  await page.locator('[data-op="add"][data-at="step.paths"]').click();
  await page.fill('[data-b="step.paths.2.name"]', 'Sure reply');
  await page.locator('[data-op="add"][data-at="step.paths.2.when"]').click();
  await page.fill('[data-b="step.paths.2.when.0.field"]', 'd1.bucket');
  await page.selectOption('[data-b="step.paths.2.when.0.op"]', 'is');
  await page.fill('[data-b="step.paths.2.when.0.value"]', 'reply');
  await page.fill('[data-b="step.paths.2.when.0.min"]', '90');
  assert.equal(await sideways(page), false);
  await shot(page, `${tag}-02-path-sheet`);
  await page.locator('.au-ed .au-save').click();
  await page.waitForFunction(() => document.querySelector('.au-ver')?.textContent === 'v2', null, {timeout: t(10000)});

  const fork = (await automationNamed(page, 'Route mail')).steps.find(s => s.type === 'paths');
  assert.deepEqual(fork.paths.map(p => p.name), ['Reply', 'Archive', 'Sure reply'], `${tag}: the hub's paths`);
  assert.deepEqual(fork.paths[2], {name: 'Sure reply', when: [{field: 'd1.bucket', op: 'is', value: 'reply', min: 0.9}], steps: []});
  assert.equal(fork.paths[0].steps.length, 1, 'the other paths keep their steps');
  assert.deepEqual(fork.otherwise.steps.map(s => s.id), ['o']);
  assert.match(await page.locator('.au-pth[data-col="2"]').innerText(), /Sure reply[\s\S]*bucket = reply ≥ 90%/);
  if (phone) assert.equal(await page.locator('.au-dots [data-dot]').count(), 4);
  assert.equal(await sideways(page), false);
  await shot(page, `${tag}-03-saved`);

  // A test run: reply at 82% takes Reply, not Sure reply.
  const out = await ok(page, 'POST', `automations/${route.id}/run`,
    {input: {message: {from: 'sam@acme.example', subject: 'Lunch?', body: 'Free Friday?'}}});
  const went = out.run.steps.find(s => s.type === 'paths');
  assert.equal(went.outputs.path, 'Reply', `${tag}: the Reply path: ${JSON.stringify(went.outputs)}`);
  assert.deepEqual(page.errors, [], `${tag}: no page errors`);
  await page.context().close();
});
