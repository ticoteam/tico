// Preloaded by the runner: scripts use Playwright directly, including ones that do not serve page.cts.
// Wrap its public creation methods so defaults are set before the script starts using a page.
import type {Browser, BrowserContext, BrowserType, Page} from 'playwright';
const {applyTimeouts, t} = require('./load.cts') as import('./load.cts').Api;
const playwright = require('playwright') as typeof import('playwright');

// Playwright gives newPage and page.close no timeout. Under load either one waited forever while another page in the
// same context reloaded or navigated (people-access.cts, Oct 22), and the browser job hung until CI canceled it at
// 30 minutes. Bound both by the default timeout, so a stall fails and names the call.
type Method = (...args: unknown[]) => Promise<unknown>;
function bounded(target: object, name: string, label: string): void {
  const record = target as Record<string, Method>;
  const call = record[name]!;
  record[name] = function (this: unknown, ...args: unknown[]): Promise<unknown> {
    let timer: NodeJS.Timeout | undefined;
    const stalled = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        const error = new Error(`${label} did not finish in ${t(30000) / 1000}s`);
        console.error(error.message);   // now: a browser.close() that also stalls would keep the thrown error unseen
        reject(error);
      }, t(30000));
    });
    return Promise.race([call.apply(this, args), stalled]).finally(() => clearTimeout(timer));
  };
}

function pageTimeouts(page: Page): void {
  applyTimeouts(page);
  bounded(page, 'close', 'page.close');
}

function contextTimeouts(context: BrowserContext): BrowserContext {
  applyTimeouts(context);
  bounded(context, 'newPage', 'browserContext.newPage');
  context.on('page', pageTimeouts);
  return context;
}

for (const engine of [playwright.chromium, playwright.firefox, playwright.webkit]) {
  const launch = engine.launch;
  engine.launch = async function (this: BrowserType, ...args: Parameters<BrowserType['launch']>): Promise<Browser> {
    const browser = await launch.apply(this, args);
    const newContext = browser.newContext;
    browser.newContext = async function (this: Browser, ...options: Parameters<Browser['newContext']>): Promise<BrowserContext> {
      return contextTimeouts(await newContext.apply(this, options));
    };
    return browser;
  };
  const persistent = engine.launchPersistentContext;
  engine.launchPersistentContext = async function (this: BrowserType, ...args: Parameters<BrowserType['launchPersistentContext']>): Promise<BrowserContext> {
    return contextTimeouts(await persistent.apply(this, args));
  };
}
