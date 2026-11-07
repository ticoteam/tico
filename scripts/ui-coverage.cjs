// Records what each browser script runs in the UI, for scripts/ui_test_map.py. Off unless TICO_UI_COVERAGE names a folder,
// and it acts only in a browser script (ui/tests/<name>.cjs) or the boot baseline (scripts/ui-boot-baseline.cjs), so the
// runner, the Playwright driver and the other node processes are left alone. The measuring workflow preloads it with
// NODE_OPTIONS (.github/workflows/ui-test-map.yml); a normal run never loads it.
//
// Each script writes <folder>/<name>.json:
//   {"script": "chat", "pages": 3, "loads": 4, "files": {"ui/app/chat.js": {"120-398": 31, "402-611": 2}}}
// "files" maps each UI file to the functions that ran, by their source range ("start-end"), with how many times each ran
// across all of the script's pages. The top-level code of a file runs at every load and is left out. "loads" counts the
// page loads (the load event) of those pages: the boot baseline loads the app once, so counts are compared per load.
// Chromium's precise coverage is read from each page every FLUSH_MS, after each navigation, and before a page, context or
// browser closes, so a script that fails or is stopped still leaves what it ran.
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const FLUSH_MS = 2000;
// The UI scripts a page loads, by their path under /tico/ui/. The bundles have no source file of their own.
const SERVED = /^\/tico\/ui\/((?!app\.bundle)[^?#]+\.(?:js|ts))$/;
// The files this hook measures when it is preloaded: a browser script, or the boot baseline.
const MEASURED = /[\\/](?:ui[\\/]tests[\\/][^\\/]+\.cjs|scripts[\\/]ui-boot-baseline\.cjs)$/;

// Adds one reading (the scripts of Profiler.takePreciseCoverage) to files: a Map from "ui/app/chat.js" to a Map from each
// function's key to the calls it has had. A key is the function's source range, the same in every page of the file.
function fold(files, scripts) {
  for (const entry of scripts) {
    let pathname;
    try { pathname = new URL(entry.url).pathname; } catch { continue; } // an eval or an inline script has no URL
    const match = SERVED.exec(pathname);
    if (!match) continue;
    const functions = entry.functions || [];
    // The top-level code is the function whose range covers the whole script.
    let end = 0;
    for (const fn of functions) for (const range of fn.ranges || []) end = Math.max(end, range.endOffset);
    const file = `ui/${match[1]}`;
    for (const fn of functions) {
      const first = (fn.ranges || [])[0];
      if (!first || !(first.count > 0) || (first.startOffset === 0 && first.endOffset === end)) continue;
      if (!files.has(file)) files.set(file, new Map());
      const counts = files.get(file);
      const key = `${first.startOffset}-${first.endOffset}`;
      counts.set(key, (counts.get(key) || 0) + first.count);
    }
  }
  return files;
}

// The record a script writes: every UI file it ran code in, with the calls of each function that ran.
function record(name, files, pages, loads) {
  const out = {};
  for (const [file, counts] of files) out[file] = Object.fromEntries(counts);
  return {script: name, pages, loads, files: out};
}

function install() {
  const folder = process.env.TICO_UI_COVERAGE;
  const {chromium} = require('playwright');
  const name = path.basename(process.argv[1], '.cjs');
  const files = new Map();
  const pages = new Map(); // page -> {session, ready, queue}
  let attached = 0;
  let loads = 0;
  let timer = null;

  // A failed write keeps the previous record. Nothing here may throw into the script under test.
  function write() {
    try {
      fs.mkdirSync(folder, {recursive: true});
      const target = path.join(folder, `${name}.json`);
      fs.writeFileSync(`${target}.tmp`, `${JSON.stringify(record(name, files, attached, loads), null, 1)}\n`);
      fs.renameSync(`${target}.tmp`, target);
    } catch { /* the build reports a script with no record */ }
  }

  // Reads one page's counters, which start again at zero, and starts them again in case V8 stopped them. Reads of one
  // page run in order. A page that has gone leaves nothing to read: what it ran was read before it went.
  function take(page) {
    const state = pages.get(page);
    if (!state) return Promise.resolve();
    state.queue = state.queue.then(async () => {
      await state.ready;
      try {
        const {result} = await state.session.send('Profiler.takePreciseCoverage');
        fold(files, result);
      } catch { /* the page has gone */ }
      try {
        await state.session.send('Profiler.startPreciseCoverage', {callCount: true, detailed: true});
      } catch { /* the page has gone */ }
      write();
    }).catch(() => {});
    return state.queue;
  }

  // The last read of a page, before it closes; then it is no longer tracked.
  async function finish(page) {
    const state = pages.get(page);
    if (!state) return;
    await take(page);
    pages.delete(page);
    if (state.session) state.session.detach().catch(() => {});
  }

  function attach(page) {
    if (pages.has(page)) return pages.get(page).ready;
    const state = {session: null, ready: null, queue: Promise.resolve()};
    pages.set(page, state);
    attached += 1;
    page.on('load', () => { loads += 1; });
    const close = page.close;
    page.close = async function (...args) {
      await finish(page);
      return close.apply(this, args);
    };
    state.ready = (async () => {
      try {
        state.session = await page.context().newCDPSession(page);
        await state.session.send('Profiler.enable');
        await state.session.send('Profiler.startPreciseCoverage', {callCount: true, detailed: true});
      } catch {
        pages.delete(page); // coverage cannot start on this page, so it is not measured
        return;
      }
      page.on('framenavigated', frame => { if (frame === page.mainFrame()) take(page); });
      if (!timer) {
        timer = setInterval(() => { for (const tracked of pages.keys()) take(tracked); }, FLUSH_MS);
        timer.unref();
      }
      write();
    })();
    return state.ready;
  }

  function attachContext(context) {
    for (const page of context.pages()) attach(page);
    context.on('page', page => { attach(page); });
    // A page made here is attached before the caller gets it, so its first navigation is measured.
    const newPage = context.newPage;
    context.newPage = async function (...options) {
      const page = await newPage.apply(this, options);
      await attach(page);
      return page;
    };
    const close = context.close;
    context.close = async function (...args) {
      await Promise.all([...pages.keys()].filter(page => page.context() === context).map(finish));
      return close.apply(this, args);
    };
    return context;
  }

  function attachBrowser(browser) {
    const newContext = browser.newContext;
    browser.newContext = async function (...options) { return attachContext(await newContext.apply(this, options)); };
    const newPage = browser.newPage;
    browser.newPage = async function (...options) {
      const page = await newPage.apply(this, options);
      await attach(page);
      return page;
    };
    const close = browser.close;
    browser.close = async function (...args) {
      await Promise.all([...pages.keys()].map(finish));
      return close.apply(this, args);
    };
    return browser;
  }

  const launch = chromium.launch;
  chromium.launch = async function (...args) { return attachBrowser(await launch.apply(this, args)); };
  const persistent = chromium.launchPersistentContext;
  chromium.launchPersistentContext = async function (...args) { return attachContext(await persistent.apply(this, args)); };
  write();
}

const folder = process.env.TICO_UI_COVERAGE;
if (folder && MEASURED.test(path.resolve(process.argv[1] || ''))) install();

module.exports = {fold, record, SERVED, FLUSH_MS, MEASURED};
