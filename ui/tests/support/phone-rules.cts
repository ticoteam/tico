// The phone rules behind ui/tests/phone-dead-ends-*.cts: a hosted company's first day, walked on a phone. A real hub
// (hosted_hub_server.py: the default company setup makes, hosted on Tico Cloud, HQ faked) in one state a person really
// starts from, at 400 px wide with touch and again at 560 px (the phone layout still applies there). Seven general
// rules run over every screen; a break prints the rule, the state, the screen and the element:
//   1 no disabled primary action without a way forward on the same screen
//   2 nothing drawn over an open modal, and nothing fixed over the focused field with the keyboard up
//   3 every needs / waiting / asleep state names what it waits for and has a link or control on its own line or card
//   4 no horizontal scroll
//   5 every modal can be closed and opened again, and keeps what was typed
//   6 no tap target under 32 px that is the only way forward
//   7 no fixed or floating control (the tab bar, the Assistant dot) over a primary action: as the screen opens,
//     scrolled to its end, and where the browser puts the action when it scrolls to it
// KNOWN lists the breaks reported and not yet fixed, each with the states it happens in: a script fails on a break
// that is not listed, and on a listed one that no longer happens in its state (delete the line with the fix).
//
//   node ui/tests/phone-dead-ends-no-computer.cts
//   PHONE_REPORT=1 node ...     print every break and exit 0 (for hunting)
//   PHONE_SHOTS=<folder> ...    also save a screenshot of every screen
import type {Browser, Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const {spawn} = require('node:child_process') as typeof import('node:child_process');
const path: typeof import('node:path') = require('node:path');
const readline: typeof import('node:readline') = require('node:readline');
const {t} = require('./load.cts') as import('./load.cts').Api;

const ROOT = path.join(__dirname, '..', '..', '..');
const PYTHON = process.env.TICO_PYTHON || 'python3';
const HEIGHT = 844, KEYBOARD = 420;   // the height left above a phone keyboard
const WIDTHS = (process.env.PHONE_WIDTHS || '400,560').split(',').map(Number);
let WIDTH = WIDTHS[0]!;
const PHONE = {
  isMobile: true, hasTouch: true,
  userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) ' +
    'Version/17.5 Mobile/15E148 Safari/604.1',
};

interface Break { rule: number; state: string; screen: string; element: string; why: string }
interface Hub { url: string; state: string; token: string; facts: Record<string, string>; stop: () => Promise<void> }

// "rule | element" (a prefix is enough) -> the states it happens in and what it is.
const ALL = ['no-computer', 'no-credit', 'setup-halfway', 'sign-off', 'bot-waiting'];
const KNOWN: Record<string, {states: string[]; what: string}> = {
  // Dead ends, reported to the lead on Nov 11 (rc.8 issue hunt, track F).
  '3 | span "Not connected"': {states: ALL,
    what: 'Settings > Models: a plan row says Not connected and names nothing (ui/app/subscriptions.ts, chris/signin-ui)'},
  '5 | dialog onb': {states: ['setup-halfway'],
    what: 'setup cannot be closed until it is finished (ui/app/welcome.ts reopens it): by design today'},
  // Small tap targets that are the only control of their row or card.
  '6 | a "BotOps"': {states: ['sign-off', 'bot-waiting'], what: 'Settings > Computers: an agent row\'s bot link is 16 px'},
};

function startHub(state: string): Promise<Hub> {
  const child = spawn(PYTHON, ['ui/tests/support/hosted_hub_server.py', '--state', state],
    {cwd: ROOT, env: process.env, stdio: ['ignore', 'pipe', 'pipe']});
  let err = '';
  child.stderr.on('data', (d: Buffer) => { err = (err + d).slice(-4000); });
  let stopping: Promise<void> | null = null;
  const stop = (): Promise<void> => stopping ??= new Promise<void>(resolve => {
    if (child.exitCode !== null || child.signalCode !== null) return resolve();
    const killer = setTimeout(() => child.kill('SIGKILL'), t(15000));
    child.once('exit', () => { clearTimeout(killer); resolve(); });
    child.kill('SIGTERM');
  });
  return new Promise<Hub>((resolve, reject) => {
    const timer = setTimeout(() => { void stop(); reject(new Error('hosted_hub_server.py did not start: ' + err)); }, t(120000));
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`hosted_hub_server.py exited ${code}: ${err}`)); });
    readline.createInterface({input: child.stdout!}).on('line', line => {
      if (!line.startsWith('{')) return;
      clearTimeout(timer);
      resolve({...(JSON.parse(line) as Omit<Hub, 'stop'>), stop});
    });
  });
}

// ------------------------------------------------------------------------------------------------ the rules, in the page
// Runs in the page. `rules` picks which of 1, 3, 4, 6 and the modal half of 2 to check on what is showing now.
function audit(where: string): {rule: number; element: string; why: string}[] {
  const out: {rule: number; element: string; why: string}[] = [];
  const W = innerWidth, H = innerHeight;
  const text = (el: Element) => ((el as HTMLElement).innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  const name = (el: Element) => {
    const cls = typeof el.className === 'string' && el.className ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.') : '';
    const label = el.getAttribute('aria-label') || text(el) || el.getAttribute('title') || el.getAttribute('placeholder') || '';
    return `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}${cls} "${label.slice(0, 50)}"`;
  };
  const shown = (el: Element) => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    if (r.width < 1 || r.height < 1 || s.visibility === 'hidden' || s.display === 'none' || Number(s.opacity) === 0) return false;
    if (!(el as HTMLElement).offsetParent && s.position !== 'fixed' && el.tagName !== 'DIALOG') return false;
    for (let p = el.parentElement; p; p = p.parentElement) if (p.hasAttribute('inert') || p.hasAttribute('hidden')) return false;
    return r.right > 0 && r.left < innerWidth;   // a closed drawer waits off screen
  };
  // A native modal makes the page inert even at opacity zero, the first frame of its opening fade.
  const dialogs = [...document.querySelectorAll('dialog[open]')].filter(d => d.matches(':modal') || shown(d));
  const top = dialogs[dialogs.length - 1] || null;
  // What a person can act on now: the top modal when one is open, else the page.
  const scope: Element = top || document.body;
  const within = (el: Element) => scope.contains(el);
  const ACT = 'a[href], button, [role="button"], [role="tab"], [role="link"], [onclick], summary, select, input:not([type="hidden"]), textarea, [contenteditable="true"]';
  const acts = [...scope.querySelectorAll(ACT)].filter(shown);
  const off = (el: Element) => (el as HTMLButtonElement).disabled === true || el.getAttribute('aria-disabled') === 'true';
  const LEAVE = /^(close|cancel|back|done|dismiss|not now|×|✕|x|ok)$/i;
  const box = (el: Element) =>
    el.closest('form, fieldset, li, tr, [role="listitem"], [role="row"], .card, .row, .tmodal-foot, .tmodal-body, section, article, dialog') || el.parentElement || document.body;

  // 1 a disabled primary action with nothing beside it that moves the person on
  for (const el of acts.filter(off)) {
    if (!el.matches('button, [role="button"], a')) continue;
    const primary = /primary|cta|submit/i.test(String(el.className)) || el.getAttribute('type') === 'submit';
    const home = box(el);
    const others = [...home.querySelectorAll(ACT)].filter(o => o !== el && shown(o));
    if (!primary && others.some(o => o.matches('button, [role="button"]') && !off(o) && !LEAVE.test(text(o)))) continue;
    // A form that waits for what the person has not typed or picked yet is not a dead end.
    const wants = others.some(o => !off(o) && (o.matches('textarea, [contenteditable="true"], select')
      || (o instanceof HTMLInputElement && (['checkbox', 'radio', 'file'].includes(o.type) ? !o.checked : o.value === ''))));
    if (wants) continue;
    const forward = others.some(o => !off(o) && !LEAVE.test(text(o) || o.getAttribute('aria-label') || '')
      && o.matches('a[href], button, [role="button"], [role="link"]'));
    if (!forward) out.push({rule: 1, element: name(el), why: 'disabled, with no link or control beside it that moves on: ' + text(home).slice(0, 120)});
  }

  // 2 something drawn over the open modal
  if (top) {
    const r = top.getBoundingClientRect(), seen = new Set<Element>();
    for (let i = 1; i < 8; i++) for (let j = 1; j < 12; j++) {
      const x = r.left + r.width * i / 8, y = r.top + r.height * j / 12;
      if (x < 0 || y < 0 || x >= W || y >= H) continue;
      const hit = document.elementFromPoint(x, y);
      if (!hit || top.contains(hit) || seen.has(hit)) continue;
      const cover = hit.closest('[id]') || hit;
      if (seen.has(cover)) continue;
      seen.add(hit); seen.add(cover);
      out.push({rule: 2, element: name(cover), why: `drawn over the open modal ${name(top)} at ${Math.round(x)},${Math.round(y)}`});
    }
  }

  // 3 a needs / waiting / asleep state that names nothing or leads nowhere
  const STATE = /\b(needs?( you| a| an| to)?|waiting|waits|asleep|sleeping|paused|offline|not connected|not signed in|no computer|out of credit|stopped|stuck|blocked|frozen|locked)\b/i;
  const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
  const said = new Set<Element>();
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const own = (node.nodeValue || '').trim(), el = node.parentElement;
    if (!el || !STATE.test(own) || own.length > 70 || !shown(el) || el.closest('script, style, option, [contenteditable], textarea')) continue;
    // A heading, tab, filter or menu item names a place, not a state; a control is its own way forward.
    if (el.closest('h1, h2, nav, [role="tab"], [role="tablist"], label, legend, a[href], button, [role="button"], summary, select')) continue;
    // Its own line or card: a link somewhere else on the page is not beside it.
    const home = el.closest('li, tr, [role="listitem"], [role="row"], .card, .row, .banner, .notice, p') || el.parentElement!;
    if (said.has(home)) continue;
    said.add(home);
    const controls = [...home.querySelectorAll('a[href], button, [role="button"], [role="link"], [onclick], summary')]
      .filter(o => shown(o) && !off(o) && !LEAVE.test(text(o) || o.getAttribute('aria-label') || ''));
    const clickable = !!home.closest('a[href], button, [role="button"], [onclick]') || (home as HTMLElement).onclick != null
      || getComputedStyle(home).cursor === 'pointer';
    const named = text(home).replace(STATE, '').replace(/[^\p{L}\p{N}]+/gu, ' ').trim().length >= 3;
    if (!named) out.push({rule: 3, element: name(el), why: 'the state names nothing it waits for: "' + text(home).slice(0, 80) + '"'});
    else if (!controls.length && !clickable) out.push({rule: 3, element: name(el), why: 'no link or control beside the state: "' + text(home).slice(0, 120) + '"'});
  }

  // 4 horizontal scroll
  const root = document.scrollingElement!;
  const scrolls = (el: Element) => {
    for (let p: Element | null = el; p && p !== document.body; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (/(auto|scroll|hidden|clip)/.test(s.overflowX) && p !== el) return true;
      if (s.position === 'fixed') return false;
    }
    return false;
  };
  const wide = (host: Element, limit: number, label: string) => {
    let worst: Element | null = null, edge = limit + 1;
    for (const el of host.querySelectorAll('*')) {
      if (!shown(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.right > edge && r.width > 0 && !scrolls(el)) { worst = el; edge = r.right; }
    }
    if (worst) out.push({rule: 4, element: name(worst), why: `${label} scrolls sideways: this reaches ${Math.round(edge)} px of ${Math.round(limit)}`});
    else out.push({rule: 4, element: name(host), why: `${label} scrolls sideways`});
  };
  if (root.scrollWidth > W + 1) wide(document.body, W, 'the page');
  if (top && top.scrollWidth > top.clientWidth + 1) wide(top, top.getBoundingClientRect().right, 'the modal');

  // 6 a small tap target that is the only way forward from where it is
  const tap = (el: Element) => {
    let r = el.getBoundingClientRect();
    const label = el.closest('label') || (el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null);
    if (label && shown(label)) r = label.getBoundingClientRect();
    return Math.min(r.width, r.height);
  };
  for (const el of acts) {
    if (off(el) || !within(el) || tap(el) >= 32) continue;
    if (!el.matches('a[href], button, [role="button"], [role="link"], summary, input[type="checkbox"], input[type="radio"]')) continue;
    const inline = el.matches('a') && getComputedStyle(el).display.startsWith('inline') && text(el.parentElement!).length > text(el).length + 12;
    const home = top && (el.closest('.tmodal-head, .tmodal-foot') || el.parentElement === top) ? top : box(el);
    const rest = [...home.querySelectorAll(ACT)].filter(o => o !== el && shown(o) && !off(o) && !el.contains(o) && !o.contains(el));
    const closes = /close|dismiss|×|✕/i.test(el.getAttribute('aria-label') || text(el));
    // The only close of a modal, or the only control of its row or card (peers of the same size are a style, not a dead end).
    const only = closes ? !rest.some(o => LEAVE.test(text(o) || o.getAttribute('aria-label') || '') && tap(o) >= 32)
      : !rest.length && !home.closest('a[href], button, [role="button"], [onclick]') && getComputedStyle(home).cursor !== 'pointer';
    if (only && !inline) out.push({rule: 6, element: name(el), why: `${Math.round(tap(el))} px, and the only ${closes ? 'way to close' : 'control'} in "${text(home).slice(0, 80)}"`});
  }
  // 7 a fixed or floating control over a primary action
  if (!top) for (const el of acts) {
    if (off(el) || !el.matches('.primary, button[type="submit"], input[type="submit"]')) continue;
    const r = el.getBoundingClientRect();
    if (r.bottom <= 0 || r.top >= H) continue;   // below the fold: a scroll brings it up, checked at the end too
    const x = r.left + r.width / 2, y = Math.min(H - 1, Math.max(0, r.top + r.height / 2));
    const hit = document.elementFromPoint(x, y);
    if (!hit || hit === el || el.contains(hit) || hit.contains(el)) continue;
    let fixed: Element | null = hit;
    while (fixed && !['fixed', 'sticky'].includes(getComputedStyle(fixed).position)) fixed = fixed.parentElement;
    if (fixed) out.push({rule: 7, element: name(el), why: `${name(hit.closest('[id]') || hit)} is drawn over it ${where} (the action at ${Math.round(r.left)},${Math.round(r.top)} ${Math.round(r.width)}x${Math.round(r.height)} of ${W}x${H})`});
  }
  // ... and where the browser itself puts it when it scrolls to it (focus, the keyboard's Next, a link to its card).
  if (!top && where === 'scrolled to the end') for (const el of acts) {
    if (off(el) || !el.matches('.primary, button[type="submit"], input[type="submit"]')) continue;
    scrollTo(0, 0);
    el.scrollIntoView({block: 'nearest'});
    const r = el.getBoundingClientRect();
    if (r.bottom <= 0 || r.top >= H) continue;   // inside a closed part of the page
    const hit = document.elementFromPoint(r.left + r.width / 2, Math.min(H - 1, r.top + r.height / 2));
    if (!hit || hit === el || el.contains(hit) || hit.contains(el)) continue;
    let fixed: Element | null = hit;
    while (fixed && !['fixed', 'sticky'].includes(getComputedStyle(fixed).position)) fixed = fixed.parentElement;
    if (fixed) out.push({rule: 7, element: name(el), why: `${name(hit.closest('[id]') || hit)} is drawn over it when the browser scrolls to it (the action at ${Math.round(r.left)},${Math.round(r.top)} ${Math.round(r.width)}x${Math.round(r.height)} of ${W}x${H})`});
  }
  return where === 'scrolled to the end' ? out.filter(b => b.rule === 7) : out;
}

// With a field focused and the keyboard up, the field is on screen and nothing fixed is drawn over it.
function keyboardAudit(): {rule: number; element: string; why: string}[] {
  const out: {rule: number; element: string; why: string}[] = [];
  const el = document.activeElement;
  if (!el || el === document.body) return out;
  el.scrollIntoView({block: 'nearest'});   // as a phone does for the focused field when its keyboard opens
  const r = el.getBoundingClientRect();
  const label = `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''} "${(el.getAttribute('placeholder') || el.getAttribute('aria-label') || '').slice(0, 40)}"`;
  const x = Math.min(innerWidth - 2, Math.max(1, r.left + r.width / 2)), y = r.top + Math.min(r.height / 2, 16);
  if (y < 0 || y > innerHeight - 4) { out.push({rule: 2, element: label, why: `with the keyboard up the focused field is off screen (top ${Math.round(r.top)} of ${innerHeight})`}); return out; }
  const hit = document.elementFromPoint(x, y);
  if (hit && hit !== el && !el.contains(hit) && !hit.contains(el)) {
    const cover = hit.closest('[id]') || hit;
    out.push({rule: 2, element: `${cover.tagName.toLowerCase()}${cover.id ? '#' + cover.id : ''}.${String(cover.className).split(/\s+/).slice(0, 2).join('.')}`,
      why: `drawn over the focused field ${label} with the keyboard up`});
  }
  return out;
}

// ------------------------------------------------------------------------------------------------ the walk
const settle = async (page: Page) => {
  await page.waitForFunction(() => (document.querySelector('#main')?.children.length ?? 0) > 0, null, {timeout: t(20000)}).catch(() => {});
  await page.waitForTimeout(t(450));
};
const go = async (page: Page, hash: string) => {
  await page.evaluate(h => { location.hash = h; }, hash);
  await settle(page);
};
// Settings > Computers draws its External agents table only when the settings read answers, which can take longer
// than settle's fixed wait on a loaded computer: wait for the agent row the hub seeded, or the audit misses it.
const computers = async (page: Page, hub: Hub) => {
  await go(page, '#/settings/computers');
  if (!hub.facts.agent) return;
  const row = `[data-agent-row="${hub.facts.agent}"]`;
  await page.locator(row).first().waitFor({state: 'attached', timeout: t(20000)}).catch(() => {
    throw new Error(`Settings > Computers never drew ${row} for the agent the hub seeded`);
  });
};
const openDialogs = (page: Page) => page.evaluate(() => [...document.querySelectorAll('dialog[open]')].map(d => d.id || d.className));

interface Screen { name: string; open: (page: Page, hub: Hub) => Promise<boolean | void>; modalFrom?: string }
// The first match among selectors that is on screen, tapped. False when none is.
async function tapFirst(page: Page, selectors: string[]): Promise<boolean> {
  for (const selector of selectors) {
    const target = page.locator(selector).first();
    if (await target.isVisible().catch(() => false)) { await target.click({timeout: t(5000)}).catch(() => {}); await page.waitForTimeout(t(400)); return true; }
  }
  return false;
}
const firstHref = (page: Page, pattern: string) => page.evaluate(p => {
  const re = new RegExp(p);
  const link = [...document.querySelectorAll<HTMLAnchorElement>('a[href]')].find(a => re.test(a.getAttribute('href') || '') && a.offsetParent);
  return link ? link.getAttribute('href') : null;
}, pattern);

const SCREENS: Screen[] = [
  {name: 'Home', open: page => go(page, '#/home')},
  {name: 'Needs you', open: page => go(page, '#/tasks?view=foryou')},
  {name: 'Tasks', open: page => go(page, '#/tasks')},
  {name: 'One task', open: async (page, hub) => {
    await go(page, '#/tasks');
    const id = hub.facts.task || await page.evaluate(async () => {
      const reply = await (await fetch('/api/v2/tasks', {cache: 'no-store'})).json();
      return (reply.tasks || [])[0]?.id || '';
    });
    if (!id) return false;
    await go(page, '#/task/' + encodeURIComponent(id));
  }},
  // New task is in Tasks' ⋯ (spec "Pipeline board in one row")
  {name: 'New task', open: async page => { await go(page, '#/tasks'); return await tapFirst(page, ['#task-menu']) && tapFirst(page, ['#task-filter-pop [data-tm="new"]']); }},
  {name: 'Pipeline', open: async page => {
    await go(page, '#/tasks');
    const id = await page.evaluate(async () => {
      const reply = await (await fetch('/api/v2/pipelines', {cache: 'no-store'})).json();
      const all: {id: string; name: string}[] = reply.pipelines || [];
      return (all.find(p => p.name === 'Engineering') || all.find(p => p.id !== 'general') || all[0])?.id || '';
    });
    if (!id) return false;
    await go(page, '#/tasks?pipeline=' + encodeURIComponent(id) + '&layout=columns');
  }},
  {name: 'Team', open: async page => {
    await go(page, '#/home');
    const href = await firstHref(page, '^#/team/');
    await go(page, href || '#/strategy/org');
  }},
  {name: 'One bot', open: page => go(page, '#/bot/builder')},
  {name: 'Tools', open: page => go(page, '#/tools')},
  {name: 'Tools > GitHub', open: page => go(page, '#/tools/github')},
  {name: 'Settings > Computers', open: computers},
  {name: 'Add computer', open: async (page, hub) => { await computers(page, hub); return tapFirst(page, ['button:has-text("Add computer")', '[data-machine-add]']); }},
  {name: 'Settings > Models', open: page => go(page, '#/settings/models')},
  {name: 'Settings > Billing', open: page => go(page, '#/settings/billing')},
  {name: 'Finance', open: page => go(page, '#/finance')},
  {name: 'Assistant', open: page => go(page, '#/assistant')},
  {name: 'Menu', open: async page => { await go(page, '#/home'); return tapFirst(page, ['#mobile-more']); }},
];

// Rule 5 on whatever modal is open now: type, close, open it again the same way, and the text is still there.
async function closeAndReopen(page: Page, screen: Screen, hub: Hub): Promise<{rule: number; element: string; why: string}[]> {
  const before = await openDialogs(page);
  if (!before.length) return [];
  const id = before[before.length - 1]!;
  const FIELDS = 'textarea:visible, input[type="text"]:visible, input:not([type]):visible';
  const fields = page.locator('dialog[open]').last().locator(FIELDS);
  // Each of the modal's first few text fields gets its own words; a field is known by its label and its place.
  const typed: {at: number; label: string; text: string}[] = [];
  for (let i = 0; i < Math.min(await fields.count(), 4); i++) {
    const field = fields.nth(i);
    if (!await field.isEditable().catch(() => false)) continue;
    const label = await field.evaluate(el => `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''} "${el.getAttribute('placeholder') || el.getAttribute('aria-label') || ''}"`);
    const text = 'kept across a close ' + i;
    if (await field.fill(text).then(() => true, () => false)) typed.push({at: i, label, text});
  }
  const close = page.locator('dialog[open]').last().locator('[data-modal-close], .tmodal-x, .tmodal-head [aria-label="Close"], [aria-label="Close"], button:has-text("Cancel"), button:has-text("Not now")').first();   // never a button that says Close: on a task that closes the task
  if (await close.isVisible().catch(() => false)) await close.click({timeout: t(3000)}).catch(() => {});
  else await page.keyboard.press('Escape');
  await page.waitForTimeout(t(500));
  if ((await openDialogs(page)).includes(id)) {
    await page.keyboard.press('Escape');
    await page.waitForTimeout(t(400));
    if ((await openDialogs(page)).includes(id)) return [{rule: 5, element: 'dialog ' + id, why: 'the modal cannot be closed: no close control worked and Escape did nothing'}];
    return [{rule: 5, element: 'dialog ' + id, why: 'the modal has no close control a finger can reach (only Escape closes it)'}];
  }
  if (await screen.open(page, hub) === false || !(await openDialogs(page)).includes(id)) {
    return [{rule: 5, element: 'dialog ' + id, why: 'once closed, the same steps do not open the modal again'}];
  }
  const lost: {rule: number; element: string; why: string}[] = [];
  const again = page.locator('dialog[open]').last().locator(FIELDS);
  for (const {at, label, text} of typed) {
    const now = await again.nth(at).inputValue().catch(() => '');
    if (now !== text) lost.push({rule: 5, element: `dialog ${id} ${label}`, why: `what was typed is gone after closing and opening again (the field now holds "${now.slice(0, 40)}")`});
  }
  return lost;
}

// `full` also checks the keyboard and each modal's close and reopen; the second width only looks.
async function walk(browser: Browser, hub: Hub, full: boolean): Promise<Break[]> {
  const breaks: Break[] = [];
  const context = await browser.newContext({...PHONE, viewport: {width: WIDTH, height: HEIGHT}, serviceWorkers: 'block', extraHTTPHeaders: {Authorization: 'Bearer ' + hub.token}});
  const page = await context.newPage();
  const add = (screen: string, found: {rule: number; element: string; why: string}[]) =>
    found.forEach(f => breaks.push({...f, state: hub.state, screen}));
  try {
    await page.goto(hub.url + '/');
    await settle(page);
    // What the app opens by itself on this state (the setup modal, a tour, a notice) is the first screen.
    add('On opening', await page.evaluate(audit, 'as the screen opens'));
    if (process.env.PHONE_SHOTS) await page.screenshot({path: path.join(process.env.PHONE_SHOTS, `${hub.state}-On-opening.png`)});
    if ((await openDialogs(page)).length) {
      const opening: Screen = {name: 'On opening', open: async p => { await p.reload(); await settle(p); }};
      if (full) await keyboard(page, 'On opening', add);
      if (full) add('On opening', await closeAndReopen(page, opening, hub));
      await dismissAll(page);
    }
    // The first-visit tour is a card over the whole app with its own Skip: one tap must end it for good.
    if (await page.locator('.gs-tour').first().isVisible().catch(() => false)) {
      await tapFirst(page, ['.gs-tour button:has-text("Skip")']);
      await page.reload();
      await settle(page);
      if (await page.locator('.gs-tour').first().isVisible().catch(() => false)) {
        add('On opening', [{rule: 5, element: 'div.gs-tour', why: 'the first-visit tour is back after Skip and a reload'}]);
        await page.evaluate(() => document.querySelectorAll('.gs-tour').forEach(el => el.remove()));
      }
    }
    for (const screen of SCREENS) {
      if (await screen.open(page, hub) === false) { console.log(`  (${hub.state}: no "${screen.name}" to open)`); continue; }
      add(screen.name, await page.evaluate(audit, 'as the screen opens'));
      if (!(await openDialogs(page)).length) {
        // The end of the page, as a person scrolls there: the window, or the page's own scroller.
        await page.evaluate(() => {
          scrollTo(0, document.documentElement.scrollHeight);
          for (const el of document.querySelectorAll('#main, #main *')) if (el.scrollHeight > el.clientHeight + 4 && /(auto|scroll)/.test(getComputedStyle(el).overflowY)) el.scrollTop = el.scrollHeight;
        });
        await page.waitForTimeout(t(150));
        add(screen.name, await page.evaluate(audit, 'scrolled to the end'));
        await page.evaluate(() => scrollTo(0, 0));
      }
      if (process.env.PHONE_SHOTS) await page.screenshot({path: path.join(process.env.PHONE_SHOTS, `${WIDTH}-${hub.state}-${screen.name.replace(/[^a-z]+/gi, '-')}.png`)});
      if (full) await keyboard(page, screen.name, add);
      if (full && (await openDialogs(page)).length) add(screen.name, await closeAndReopen(page, screen, hub));
      await dismissAll(page);
    }
  } finally {
    await context.close();
  }
  return breaks;
}

// The keyboard half of rule 2: each of the first few fields showing, focused with the keyboard's height taken away.
async function keyboard(page: Page, screen: string, add: (screen: string, found: {rule: number; element: string; why: string}[]) => void) {
  const scope = (await openDialogs(page)).length ? 'dialog[open] ' : '#main ';
  const fields = page.locator(`${scope}textarea:visible, ${scope}input[type="text"]:visible, ${scope}input[type="search"]:visible, ${scope}input:not([type]):visible, ${scope}[contenteditable="true"]:visible`);
  const count = Math.min(await fields.count(), 2);
  for (let i = 0; i < count; i++) {
    const field = fields.nth(i);
    if (!await field.isEditable().catch(() => false)) continue;
    await field.focus().catch(() => {});
    await page.setViewportSize({width: WIDTH, height: KEYBOARD});
    await page.waitForTimeout(t(200));
    add(screen, await page.evaluate(keyboardAudit));
    await page.setViewportSize({width: WIDTH, height: HEIGHT});
    await page.waitForTimeout(t(80));
  }
}

async function dismissAll(page: Page) {
  for (let i = 0; i < 4 && (await openDialogs(page)).length; i++) {
    await page.evaluate(() => { const all = document.querySelectorAll<HTMLDialogElement>('dialog[open]'); all[all.length - 1]?.close(); });
    await page.waitForTimeout(t(250));
  }
}

const keyOf = (b: Break) => `${b.rule} | ${b.element}`;
const knownFor = (b: Break) => Object.keys(KNOWN).find(k => keyOf(b).startsWith(k) && KNOWN[k]!.states.includes(b.state));
const RULES = ['', 'disabled action, no way forward', 'drawn over a modal or the focused field', 'a waiting state that leads nowhere',
  'horizontal scroll', 'modal close and reopen', 'small tap target, the only way forward', 'a floating control over a primary action'];

// Walks one state and exits 1 on a break KNOWN does not list, or on one it lists for this state that no longer happens.
async function run(state: string) {
  let browser: Browser | undefined;
  const all: Break[] = [];
  try {
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
    const hub = await startHub(state);
    try {
      for (const width of WIDTHS) { WIDTH = width; all.push(...await walk(browser, hub, width === WIDTHS[0])); }
    } finally { await hub.stop(); }
  } finally {
    await browser?.close();
  }
  const grouped = new Map<string, Break & {screens: string[]}>();
  for (const b of all) {
    const found = grouped.get(keyOf(b));
    if (!found) grouped.set(keyOf(b), {...b, screens: [b.screen]});
    else if (!found.screens.includes(b.screen)) found.screens.push(b.screen);
  }
  const fresh = [...grouped.values()].filter(b => !knownFor(b));
  const gone = Object.keys(KNOWN).filter(k => KNOWN[k]!.states.includes(state) && !all.some(b => knownFor(b) === k));
  const report = process.env.PHONE_REPORT === '1';
  for (const b of report ? [...grouped.values()] : fresh) {
    console.log(`rule ${b.rule} (${RULES[b.rule]}) · ${state} · ${b.screens.slice(0, 4).join(', ')}${b.screens.length > 4 ? ' +' + (b.screens.length - 4) : ''}\n    ${keyOf(b)}\n    ${b.why}`);
  }
  if (report) { console.log(`phone-dead-ends ${state}: ${grouped.size} breaks (${fresh.length} not in KNOWN)`); return; }
  for (const k of gone) console.log(`KNOWN no longer happens in ${state}; delete its line, or take ${state} off it: ${k}`);
  if (fresh.length || gone.length) {
    console.error(`phone-dead-ends ${state}: ${fresh.length} new break(s) on a phone, ${gone.length} fixed one(s) still listed`);
    process.exit(1);
  }
  console.log(`phone-dead-ends ${state}: ok`);
}

const api = {audit, run: (state: string) => run(state).catch(error => { console.error(error); process.exit(1); })};
export type Api = typeof api;
module.exports = api;
