// QA pass for org switching and adding at phone (390x844), compact (760x900) and desktop (1240x820) sizes: the web
// app's account menu (Switch organization and Add organization, with the native bridge mocked as native-orgs.cjs does),
// the desktop shell's pages loaded as plain files (the setup window, and the org rail at its minimum height), and the
// HQ org picker's HTML (portal/views.py desktop_orgs, rendered to ui/tests/support/qa-org-picker-pages.json).
// Each check that should hold is recorded; every one that does not is printed as a FINDING with its place and detail,
// and the run exits 1 when there is any. Run on RunsOn: node scripts/ui-tests.cjs qa-org-sizes
const {webkit} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const SHELL = path.join(__dirname, '../../app/shell');
const PICKER = JSON.parse(fs.readFileSync(path.join(__dirname, 'support/qa-org-picker-pages.json'), 'utf8'));
const SIZES = [
  {name: 'phone', width: 390, height: 844},
  {name: 'compact', width: 760, height: 900},
  {name: 'desktop', width: 1240, height: 820},
];
const TYPES = {'.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css'};
const json = value => JSON.stringify(value);

const results = [];
function record(where, label, ok, detail = '') {
  results.push({where, label, ok: !!ok, detail: ok ? '' : String(detail)});
}
// A scenario that throws is itself a finding (a control that cannot be reached, a page that never loads).
async function guard(where, fn) {
  try {
    await fn();
  } catch (error) {
    record(where, 'scenario ran to the end', false, String(error && error.message || error).split('\n').slice(0, 3).join(' | '));
  }
}

// ----------------------------------------------------------------------------- web app: account menu
async function openWeb(browser, size, many) {
  const page = await browser.newPage({serviceWorkers: 'block', viewport: {width: size.width, height: size.height},
    userAgent: 'Mozilla/5.0 AppleWebKit/605.1.15 TicoHub'});
  const pageErrors = [];
  const consoleErrors = [];
  page.on('pageerror', e => pageErrors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  await page.addInitScript(many => {
    window.EventSource = undefined;
    Object.defineProperty(navigator, 'platform', {get: () => 'MacIntel'});
    window.__posted = [];
    window.ticoNative = name => ({postMessage: body => {
      window.__posted.push([name, body]);
      return Promise.resolve(name === 'multiOrg' ? many : null);
    }});
    window.ticoNativeFeatures = ['openWindow', 'checkForUpdates', 'showLog', 'orgInfo', 'multiOrg', 'showOrgMenu', 'addOrg'];
  }, many);
  await page.route('**/*', async route => {
    const p = new URL(route.request().url()).pathname;
    const reply = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p.startsWith('/tico/ui/')) {
      const file = uiFile(p.slice('/tico/ui/'.length));
      if (fs.existsSync(file)) return route.fulfill({path: file});
    }
    if (p === '/api/me') return reply({id: 'ana', email: 'ana@acme.example', role: 'owner', cloud: true, config: {company_name: 'Acme Lights'}});
    if (p === '/api/status') return reply({cloud: true, keeper_alive: true, health_issues: []});
    if (p === '/api/employees' || p === '/api/issues') return reply([]);
    return reply(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/settings', {waitUntil: 'domcontentloaded'});
  await page.locator('#account .account-email', {hasText: 'ana@acme.example'}).waitFor();
  return {page, pageErrors, consoleErrors};
}

const posted = (page, name) => page.evaluate(n => window.__posted.filter(([k]) => k === n).map(([, body]) => body), name);

// Where an item sits, and which element the screen shows at its center (a covered item reports the cover).
const measure = (page, sel) => page.evaluate(sel => {
  const el = document.querySelector(sel);
  const r = el.getBoundingClientRect();
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  const name = node => node ? (node.id ? '#' + node.id : node.tagName.toLowerCase()) : 'none';
  return {shown: r.width > 0 && r.height > 0 && !el.closest('[hidden]'), l: Math.round(r.left), t: Math.round(r.top),
    r: Math.round(r.right), b: Math.round(r.bottom), vw: innerWidth, vh: innerHeight, hitOwn: !!hit && el.contains(hit), hit: name(hit)};
}, sel);

async function webCase(browser, size, orgs) {
  const where = `web ${size.name} ${size.width}x${size.height}, ${orgs} org${orgs === 1 ? '' : 's'}`;
  await guard(where, async () => {
    const many = orgs >= 2;
    const phone = size.width <= 760;
    const {page, pageErrors, consoleErrors} = await openWeb(browser, size, many);
    try {
      await page.waitForFunction(() => window.__posted.some(([n]) => n === 'multiOrg'));
      if (phone) {
        await page.locator('#mobile-more').click();
        await page.waitForFunction(() => document.body.classList.contains('drawer'));
        const inBar = await page.locator('#mobile-nav #org-switch, #mobile-nav #org-add').count();
        record(where, 'phone: the org items are not on the bottom bar (reached through More, then the account button)', inBar === 0, `${inBar} in the bar`);
      }
      await page.locator('#account').click();
      await page.locator('#account-menu').waitFor({state: 'visible'});
      await page.waitForFunction(show => document.querySelector('#org-switch').hidden === !show, many);
      if (!many) record(where, '1 org: Switch organization stays hidden', await page.locator('#org-switch').isHidden());
      for (const sel of many ? ['#org-switch', '#org-add'] : ['#org-add']) {
        const m = await measure(page, sel);
        record(where, `${sel} is shown inside the screen`, m.shown && m.l >= 0 && m.t >= 0 && m.r <= m.vw && m.b <= m.vh, json(m));
        record(where, `${sel} is not covered by another element`, m.hitOwn, json(m));
      }
      const sideways = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
      record(where, 'the page does not scroll sideways with the menu open', sideways <= 0, `${sideways}px wider than the screen`);
      if (many) {
        await page.locator('#org-switch').click();
        const shown = await posted(page, 'showOrgMenu');
        record(where, 'Switch organization posts showOrgMenu once', json(shown) === '[null]', json(shown));
        await page.locator('#account').click();
        await page.locator('#account-menu').waitFor({state: 'visible'});
      }
      await page.locator('#org-add').click();
      const added = await posted(page, 'addOrg');
      record(where, 'Add organization posts addOrg once', json(added) === '[null]', json(added));
      record(where, 'no page errors', pageErrors.length === 0, pageErrors.join(' | '));
      record(where, 'no console errors', consoleErrors.length === 0, consoleErrors.join(' | '));
    } finally {
      await page.close();
    }
  });
}

// ----------------------------------------------------------------------------- desktop shell: plain files
// The shell's pages, served from app/shell under a fake origin, with window.__TAURI__ mocked: each invoke is recorded
// in __calls and answered from `answers` (a value of {__reject: message} rejects).
async function openShell(browser, file, viewport, answers) {
  const page = await browser.newPage({serviceWorkers: 'block', viewport});
  const pageErrors = [];
  const consoleErrors = [];
  page.on('pageerror', e => pageErrors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  await page.addInitScript(answers => {
    window.__answers = answers;
    window.__calls = [];
    window.__listeners = {};
    window.__TAURI__ = {
      core: {invoke: (cmd, args) => {
        window.__calls.push([cmd, args === undefined ? null : args]);
        const answer = window.__answers[cmd];
        if (answer && answer.__reject !== undefined) return Promise.reject(answer.__reject);
        return Promise.resolve(answer === undefined ? null : answer);
      }},
      event: {listen: (name, fn) => { window.__listeners[name] = fn; return Promise.resolve(() => {}); }},
    };
  }, answers);
  await page.route('https://tico-shell.test/**', route => {
    const p = new URL(route.request().url()).pathname;
    const name = p === '/' ? 'index.html' : p.slice(1);
    const full = path.join(SHELL, name);
    if (!name.includes('..') && fs.existsSync(full) && fs.statSync(full).isFile()) {
      return route.fulfill({path: full, contentType: TYPES[path.extname(full)] || 'application/octet-stream'});
    }
    return route.abort();
  });
  await page.goto('https://tico-shell.test/' + file, {waitUntil: 'domcontentloaded'});
  return {page, pageErrors, consoleErrors};
}

const boxes = (page, sels) => page.evaluate(sels => sels.map(sel => {
  const el = document.querySelector(sel);
  if (!el || el.closest('[hidden]')) return {sel, shown: false};
  const r = el.getBoundingClientRect();
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return {sel, shown: r.width > 0 && r.height > 0, l: Math.round(r.left), t: Math.round(r.top), r: Math.round(r.right),
    b: Math.round(r.bottom), hitOwn: !!hit && el.contains(hit), vw: innerWidth, vh: innerHeight};
}), sels);
const inside = b => b.shown && b.l >= 0 && b.t >= 0 && b.r <= b.vw && b.b <= b.vh;
const calls = page => page.evaluate(() => window.__calls);
const sideways = page => page.evaluate(() => ({x: document.documentElement.scrollWidth - innerWidth, y: document.documentElement.scrollHeight - innerHeight}));

async function setupCase(browser, label, viewport, answers, check) {
  const where = `setup ${label} ${viewport.width}x${viewport.height}`;
  await guard(where, async () => {
    const {page, pageErrors, consoleErrors} = await openShell(browser, 'index.html', viewport, answers);
    try {
      await page.waitForFunction(() => !document.querySelector('#signin').hidden || !document.querySelector('form').hidden);
      await check(page, where);
      const over = await sideways(page);
      record(where, 'the window does not scroll', over.x <= 0 && over.y <= 0, json(over));
      record(where, 'no page errors', pageErrors.length === 0, pageErrors.join(' | '));
      record(where, 'no console errors', consoleErrors.length === 0, consoleErrors.join(' | '));
    } finally {
      await page.close();
    }
  });
}

async function railCase(browser, label, viewport, orgs, check) {
  const where = `rail ${label} ${viewport.width}x${viewport.height}`;
  await guard(where, async () => {
    const {page, pageErrors, consoleErrors} = await openShell(browser, 'rail.html', viewport, {rail_orgs: orgs});
    try {
      await page.waitForFunction(() => document.querySelectorAll('#orgs li').length > 0);
      await check(page, where);
      record(where, 'no page errors', pageErrors.length === 0, pageErrors.join(' | '));
      record(where, 'no console errors', consoleErrors.length === 0, consoleErrors.join(' | '));
    } finally {
      await page.close();
    }
  });
}

const ORG = (n, fields) => ({id: `o_0000000${n}`, url: `https://org${n}.tico.dev`, name: '', email: '', active: false, ...fields});
const EIGHT = [
  ORG(1, {name: 'Acme Lights', email: 'ana@acme.example', active: true}),
  ORG(2, {name: 'TIDY', email: 'chris@tidy.example'}),
  // A pictograph first: the first code unit of a surrogate pair is not a character on its own.
  ORG(3, {name: '\u{1F33F} Garden Co', email: 'x@garden.example'}),
  ORG(4, {name: 'A company with an extraordinarily long display name that keeps going well past any sensible width',
    email: 'an.unbreakable.very.long.email.address.for.testing@subdomain.example.international'}),
  ORG(5, {}),
  ORG(6, {name: 'Six'}),
  ORG(7, {name: 'Seven'}),
  ORG(8, {name: 'Eight'}),
];
const TWO = [ORG(1, {name: 'Acme Lights', email: 'ana@acme.example', active: true}), ORG(2, {name: 'TIDY', email: 'chris@tidy.example'})];

// ----------------------------------------------------------------------------- HQ org picker at 390
async function pickerCase(browser, name) {
  const where = `hq picker ${name} 390x844`;
  await guard(where, async () => {
    const page = await browser.newPage({serviceWorkers: 'block', viewport: {width: 390, height: 844}});
    const pageErrors = [];
    page.on('pageerror', e => pageErrors.push(e.message));
    await page.route('**/*', route => route.fulfill({contentType: 'text/html', body: PICKER[name]}));
    await page.goto('https://portal.test/desktop/orgs', {waitUntil: 'domcontentloaded'});
    try {
      const wide = await page.evaluate(() => [...document.body.querySelectorAll('*')]
        .filter(el => el.getBoundingClientRect().right > innerWidth + 0.5).slice(0, 6)
        .map(el => `${el.tagName.toLowerCase()}.${String(el.className).split(' ').join('.')} right=${Math.round(el.getBoundingClientRect().right)}`));
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
      record(where, 'the page does not scroll sideways', overflow <= 0, `${overflow}px wider than 390; ${json(wide)}`);
      const form = await page.locator('form[action="/desktop/orgs/done"]').count();
      const checked = await page.locator('input[name="org"]:checked').count();
      const boxesCount = await page.locator('input[name="org"]').count();
      if (name === 'zero') {
        record(where, 'zero orgs: no Add form, the message and Use another account show', form === 0 && boxesCount === 0
          && (await page.getByText('No organizations for').count()) === 1 && (await page.getByRole('button', {name: 'Use another account'}).count()) === 1,
          `form=${form} boxes=${boxesCount}`);
      } else {
        const valid = await page.locator('form[action="/desktop/orgs/done"]').evaluate(f => f.checkValidity());
        if (boxesCount > 1) {
          record(where, 'several orgs: Add does not submit with none ticked', !(valid && checked === 0),
            `${checked} ticked, form valid: ${valid}; the app then closes the sign-in window with no message (signin.rs finish)`);
        }
        const add = await page.getByRole('button', {name: 'Add', exact: true}).boundingBox();
        record(where, 'Add is inside the 390px screen', add && add.x >= 0 && add.x + add.width <= 390, json(add));
      }
      record(where, 'no page errors', pageErrors.length === 0, pageErrors.join(' | '));
    } finally {
      await page.close();
    }
  });
}

(async () => {
  const browser = await webkit.launch({headless: true});
  for (const size of SIZES) {
    for (const orgs of [1, 2]) await webCase(browser, size, orgs);
  }

  // First run: sign in is the default, the address form stays behind a link, and there is no heading or Cancel.
  await setupCase(browser, 'first run, sign in', {width: 360, height: 400},
    {setup_info: {hasOrgs: false, signIn: true}, server_address: 'https://tico.example.com'}, async (page, where) => {
      const [signin, use, heading, cancel, form] = await boxes(page, ['#signin-button', '#use-address', '#heading', '#cancel', 'form']);
      record(where, 'Sign in to Tico is shown inside the window', inside(signin), json(signin));
      record(where, 'the address link is shown and inside the window', inside(use), json(use));
      record(where, 'no heading or Cancel on a first run', !heading.shown && !cancel.shown, json([heading, cancel]));
      record(where, 'the address form waits behind the link', !form.shown, json(form));
    });

  // Adding an org with one open: heading, Cancel, the failure message, the address form and Connect.
  for (const viewport of [{width: 360, height: 400}, {width: 1240, height: 820}]) {
    await setupCase(browser, 'adding, sign in', viewport,
      {setup_info: {hasOrgs: true, signIn: true}, server_address: 'https://tico.example.com', tico_signin: {__reject: 'Sign-in could not start'}},
      async (page, where) => {
        const [heading, cancel, signin] = await boxes(page, ['#heading', '#cancel', '#signin-button']);
        record(where, 'heading and Cancel shown inside the window', inside(heading) && inside(cancel), json([heading, cancel]));
        record(where, 'window title is Add organization', (await page.title()) === 'Add organization', await page.title());
        record(where, 'Sign in to Tico shown inside the window', inside(signin), json(signin));
        await page.locator('#signin-button').click();
        const message = await page.locator('#signin-error').textContent();
        record(where, 'a failed sign-in start shows its message', message === 'Sign-in could not start', message);
        record(where, 'Sign in is enabled again after a failure', !(await page.locator('#signin-button').isDisabled()));
        await page.locator('#use-address').click();
        const [form, connect] = await boxes(page, ['form', 'form button[type=submit]']);
        record(where, 'Use a server address shows the form, with Connect inside the window', form.shown && inside(connect), json([form, connect]));
        await page.locator('#address').fill('https://acme.tico.dev');
        await page.locator('form button[type=submit]').click();
        const log = await calls(page);
        record(where, 'Connect posts connect_server with the typed address', log.some(([c, a]) => c === 'connect_server' && a?.address === 'https://acme.tico.dev'), json(log));
        await page.locator('#cancel').click();
        const after = await calls(page);
        record(where, 'Cancel posts cancel_setup', after.some(([c]) => c === 'cancel_setup'), json(after));
      });
  }

  // No sign-in in this build: the address form is the page.
  await setupCase(browser, 'address only', {width: 360, height: 400},
    {setup_info: {hasOrgs: false, signIn: false}, server_address: 'https://tico.example.com'}, async (page, where) => {
      const [signin, use, connect] = await boxes(page, ['#signin', '#use-address', 'form button[type=submit]']);
      record(where, 'the form is shown, with Connect inside the window', connect.shown && inside(connect), json(connect));
      await page.waitForFunction(() => document.querySelector('#address').value !== '');
      record(where, 'the address is filled from the app', (await page.inputValue('#address')) === 'https://tico.example.com');
      record(where, 'Sign in and its link stay hidden', !signin.shown && !use.shown, json([signin, use]));
    });

  // The org rail, as plain file, at the minimum window height (400) with eight orgs, and at desktop height with two.
  await railCase(browser, '8 orgs', {width: 60, height: 400}, EIGHT, async (page, where) => {
    const nav = await page.evaluate(() => {
      const n = document.querySelector('nav');
      return {sh: n.scrollHeight, ch: n.clientHeight, sw: n.scrollWidth, cw: n.clientWidth, overflowY: getComputedStyle(n).overflowY};
    });
    record(where, 'eight orgs: the list scrolls inside the rail (nav overflow-y auto)', nav.sh > nav.ch && nav.overflowY === 'auto', json(nav));
    const addFirst = (await boxes(page, ['#add']))[0];
    record(where, '+ is in view without scrolling at this height', inside(addFirst), json(addFirst));
    await page.locator('#add').scrollIntoViewIfNeeded();
    const addAfter = (await boxes(page, ['#add']))[0];
    record(where, '+ is reachable by scrolling, and not covered', inside(addAfter) && addAfter.hitOwn, json(addAfter));
    const sizes = await page.evaluate(() => [...document.querySelectorAll('.org')].map(b => b.getBoundingClientRect().width));
    record(where, 'every org button keeps its 36px size', sizes.every(w => w === 36), json(sizes));
    record(where, 'long names and emails do not widen the rail', nav.sw <= nav.cw, json(nav));
    // Checked in the page and reported as hex code points: a lone surrogate may not survive the trip back as a string.
    const initials = await page.evaluate(() => [...document.querySelectorAll('.org')].map(b => {
      const t = b.textContent;
      const lone = [...t].some(ch => ch.length === 1 && ch.charCodeAt(0) >= 0xD800 && ch.charCodeAt(0) <= 0xDFFF);
      return {lone, codes: [...t].map(ch => ch.codePointAt(0).toString(16)).join(' ')};
    }));
    const broken = initials.filter(b => b.lone);
    record(where, 'initials are whole characters (an emoji name leaves a lone surrogate)', broken.length === 0, json(broken));
    const title = await page.locator('.org').nth(3).getAttribute('title');
    record(where, 'the tooltip carries the long name and email', title.includes('unbreakable') && title.includes('\n'), json(title));
    await page.locator('.org').nth(1).click();
    await page.locator('.org').nth(0).click();
    await page.locator('.org').nth(3).click({button: 'right'});
    await page.locator('#add').click();
    const log = await calls(page);
    record(where, 'click an open org: no switch sent', !log.some(([c, a]) => c === 'rail_switch' && a.id === 'o_00000001'), json(log));
    record(where, 'click another org: rail_switch with its id', log.some(([c, a]) => c === 'rail_switch' && a.id === 'o_00000002'), json(log));
    record(where, 'right-click an org: rail_menu with its id', log.some(([c, a]) => c === 'rail_menu' && a.id === 'o_00000004'), json(log));
    record(where, '+ posts rail_add', log.some(([c]) => c === 'rail_add'), json(log));
  });

  await railCase(browser, '2 orgs', {width: 60, height: 820}, TWO, async (page, where) => {
    const [add] = await boxes(page, ['#add']);
    record(where, '+ is in view at desktop height', inside(add), json(add));
    const current = await page.evaluate(() => [...document.querySelectorAll('.org')].map(b => b.getAttribute('aria-current')));
    record(where, 'exactly the open org is marked current', json(current) === '["true","false"]', json(current));
    await page.evaluate(() => {
      window.__answers.rail_orgs = [window.__answers.rail_orgs[1]];
      window.__listeners['orgs-changed']();
    });
    await page.waitForFunction(() => document.querySelectorAll('#orgs li').length === 1);
    const left = await page.evaluate(() => [...document.querySelectorAll('.org')].map(b => b.title));
    record(where, 'removing an org reaches the rail through orgs-changed', left.length === 1 && left[0].includes('TIDY'), json(left));
  });

  // HQ picker HTML (portal/views.py) at phone width: long emails and names, several orgs, and none.
  for (const name of ['one_org', 'many_long', 'zero']) await pickerCase(browser, name);

  await browser.close();
  const failed = results.filter(r => !r.ok);
  for (const r of results.filter(r => r.ok)) console.log(`ok      ${r.where} :: ${r.label}`);
  for (const r of failed) console.log(`FINDING ${r.where} :: ${r.label} :: ${r.detail}`);
  console.log(`qa-org-sizes: ${results.length - failed.length} checks pass, ${failed.length} findings`);
  if (failed.length) process.exitCode = 1;
})().catch(error => { console.error(error); process.exit(1); });
