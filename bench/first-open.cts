// First open of Home in a browser, on a real hub seeded with the scenario org (ui/tests/support/real-hub.cts):
//
//     node bench/first-open.cts            (needs the backend requirements in python3 or $TICO_PYTHON, and playwright's Chrome)
//
// For each network (unthrottled, fast 4G 9 Mbps / 60 ms, slow 4G 1.6 Mbps / 150 ms, by CDP emulateNetworkConditions) it
// opens Home cold (a new context, empty cache) and warm (the same context, reloaded) and prints the bytes transferred by
// type (JS, CSS, other: CDP encodedDataLength), the API calls (/api/...) and the time of the `tico:paint-fresh` mark.
// Unthrottled, it also lists every /api URL requested more than once, counted until Home shows its data plus 5 s, and
// then opens a task from Home and lists the URLs repeated there (counted until 5 s after the task's dialog shows).
import type {Browser, CDPSession, Page} from 'playwright';
import type * as RealHubSupport from '../ui/tests/support/real-hub.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const {startHub, DESKTOP} = require('../ui/tests/support/real-hub.cts') as RealHubSupport.Api;
const {must} = require('../ui/tests/support/e2e.cts') as {must: (hub: RealHubSupport.Hub, who: string, method: string, path: string, body?: unknown) => Promise<unknown>};

const NETWORKS = [
  {name: 'unthrottled', down: -1, latency: 0},
  {name: 'fast 4G', down: 9e6, latency: 60},
  {name: 'slow 4G', down: 1.6e6, latency: 150},
];
const SETTLE_MS = 5000;

// Every response the page gets, with its bytes on the wire, and every /api request it makes.
function record(cdp: CDPSession) {
  const types = new Map<string, string>();
  const bytes = {js: 0, css: 0, other: 0};
  const api: string[] = [];
  cdp.on('Network.requestWillBeSent', e => {
    const url = new URL(e.request.url);
    if (url.pathname.startsWith('/api/')) api.push(e.request.method + ' ' + url.pathname + url.search);
  });
  cdp.on('Network.responseReceived', e => { types.set(e.requestId, e.type); });
  cdp.on('Network.loadingFinished', e => {
    const type = types.get(e.requestId);
    bytes[type === 'Script' ? 'js' : type === 'Stylesheet' ? 'css' : 'other'] += e.encodedDataLength;
  });
  return {bytes, api, reset() { bytes.js = bytes.css = bytes.other = 0; api.length = 0; }};
}

// The /api URLs requested more than once, and the page's live stream (GET /api/v2/events, whatever its topics; a task's
// own stream names its `place=`) opened more than once with different URLs.
function repeats(calls: string[]): string[] {
  const counts = new Map<string, number>();
  for (const c of calls) counts.set(c, (counts.get(c) || 0) + 1);
  const out = [...counts].filter(([, n]) => n > 1).map(([c, n]) => `${n}x ${c}`);
  const streams = calls.filter(c => c.startsWith('GET /api/v2/events?') && !c.includes('place='));
  if (new Set(streams).size > 1) out.push(`${streams.length}x GET /api/v2/events (the live stream, opened again with other topics or scope)`);
  return out;
}

const kb = (n: number) => (n / 1024).toFixed(0).padStart(6) + ' KB';

async function paintFresh(page: Page): Promise<number> {
  const handle = await page.waitForFunction(() => performance.getEntriesByName('tico:paint-fresh')[0]?.startTime,
    null, {timeout: 120000});
  return Math.round(await handle.jsonValue() as number);
}

async function run(browser: Browser, hub: RealHubSupport.Hub, net: typeof NETWORKS[number], detail: boolean) {
  const context = await browser.newContext({...DESKTOP, serviceWorkers: 'block',
    extraHTTPHeaders: {Authorization: 'Bearer ' + hub.tokens.dana}});
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.emulateNetworkConditions', {offline: false, latency: net.latency,
    downloadThroughput: net.down > 0 ? net.down / 8 : -1, uploadThroughput: net.down > 0 ? net.down / 8 : -1});
  const rec = record(cdp);
  const line = (when: string, ms: number) => console.log(`${net.name.padEnd(12)} ${when.padEnd(5)} JS ${kb(rec.bytes.js)}  ` +
    `CSS ${kb(rec.bytes.css)}  other ${kb(rec.bytes.other)}  API calls ${String(rec.api.length).padStart(3)}  paint-fresh ${ms} ms`);
  try {
    await page.goto(hub.url + '/');
    const cold = await paintFresh(page);
    await page.waitForTimeout(SETTLE_MS);
    line('cold', cold);
    const homeRepeats = repeats(rec.api);
    rec.reset();
    await page.reload();
    const warm = await paintFresh(page);
    await page.waitForTimeout(SETTLE_MS);
    line('warm', warm);
    if (!detail) return;
    console.log('\nRepeated on a cold open of Home:\n  ' + (homeRepeats.join('\n  ') || 'none'));
    console.log('Repeated on a warm open of Home:\n  ' + (repeats(rec.api).join('\n  ') || 'none'));
    rec.reset();
    await page.locator('#main a[href^="#/task/"]').first().click();
    await page.locator('dialog[open]').first().waitFor({timeout: 30000});
    await page.waitForTimeout(SETTLE_MS);
    console.log('Repeated on opening a task from Home:\n  ' + (repeats(rec.api).join('\n  ') || 'none'));
    console.log('  (of ' + rec.api.length + ' API calls: ' + [...new Set(rec.api)].join(', ') + ')\n');
  } finally {
    await context.close();
  }
}

(async () => {
  const hub = await startHub({seeds: ['org', 'toured']})   // toured: the first-visit tour seen, so it covers nothing;
  let browser;
  try {
    // Dana's Home lists her open tasks: one to open.
    await must(hub, 'dana', 'POST', 'tasks', {title: 'Check the first-open numbers', body: 'A task to open from Home.', owner: 'dana'});
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
    for (const net of NETWORKS) await run(browser, hub, net, net === NETWORKS[0]);
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(error => { console.error(error); process.exit(1); });
