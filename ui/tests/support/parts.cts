// Page parts on a real hub, for parts-first-visit.cts and parts-every-page.cts: a cold browser context that records the
// page's errors and the parts it downloads, a check that a page drew, and the parts table the page carries.
import type {Browser, Page} from 'playwright';
import type * as RealHubSupport from './real-hub.cts';
const assert = require('node:assert/strict');
const {DESKTOP} = require('./real-hub.cts') as RealHubSupport.Api;
const {t} = require('./load.cts') as {t: (ms: number) => number};

export interface PartsTable { build: string; parts: Record<string, {url: string; load: string[]}> }
// A page part's file (scripts/build-ui.cjs): app.bundle.<part>.<hash>.js; the shell's files have no part name.
const PART = /\/tico\/ui\/app\.bundle\.([a-z][a-z0-9-]*)\.[0-9a-f]{16}\.js/;

// A new context, nothing cached, signed in as Dana.
async function cold(browser: Browser, hub: RealHubSupport.Hub) {
  const context = await browser.newContext({...DESKTOP, serviceWorkers: 'block', extraHTTPHeaders: {Authorization: 'Bearer ' + hub.tokens.dana!}});
  const page = await context.newPage();
  const errors: string[] = [], parts: string[] = [];
  page.on('pageerror', e => { errors.push(String(e)); });
  page.on('console', m => { if (m.type() === 'error' && /is not defined|is not a function|before initialization/.test(m.text())) errors.push(m.text()); });
  page.on('request', r => { const m = PART.exec(r.url()); if (m) parts.push(m[1]!); });
  return {page, errors, parts, close: () => context.close()};
}

// The page drawn: something in the main area, no Retry, and a moment for what it loads next.
async function drawn(page: Page, route: string) {
  await page.waitForFunction(() => (document.querySelector('#main')?.children.length || 0) > 0, null, {timeout: t(20000)});
  await page.waitForLoadState('networkidle', {timeout: t(15000)}).catch(() => {});
  await page.waitForTimeout(500);
  assert.equal(await page.locator('#main [data-part-retry]').count(), 0, route + ': the page did not load its code');
}

const table = async (page: Page): Promise<PartsTable> => JSON.parse(await page.locator('#ui-parts').textContent() || '{}') as PartsTable;

const api = {PART, cold, drawn, table};
export type Api = typeof api;
module.exports = api;
