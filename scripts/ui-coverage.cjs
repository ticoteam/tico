// Records which UI source files each browser script runs, for scripts/ui_test_map.py. Off unless TICO_UI_COVERAGE
// names a folder, and it acts only in a browser script (ui/tests/<name>.cjs), so the runner, the Playwright driver and
// the other node processes are left alone. The measuring workflow preloads it with NODE_OPTIONS
// (.github/workflows/ui-test-map.yml); a normal run never loads it.
//
// Each script writes <folder>/<name>.json:
//   {"script": "chat", "pages": 3, "files": {"ui/app/chat.js": {"functions": 120, "ran": 31}}}
// "functions" counts the functions a file defines, other than its top-level code; "ran" counts those that ran at least
// once in any page the script used. The top-level code runs at every page load, so it does not count.
// Chromium's precise coverage is read from each page every FLUSH_MS, after each navigation, and before a page, context
// or browser closes, so a script that fails or is stopped still leaves what it ran.
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const FLUSH_MS = 2000;
// The UI scripts a page loads, by their path under /tico/ui/. The bundles have no source file of their own.
const SERVED = /^\/tico\/ui\/((?!app\.bundle)[^?#]+\.(?:js|ts))$/;

// Adds one reading (the scripts of Profiler.takePreciseCoverage) to files, which maps "ui/app/chat.js" to the keys of
// its functions and of those that ran. A key is the function's source range.
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
    if (!files.has(file)) files.set(file, {functions: new Set(), ran: new Set()});
    const seen = files.get(file);
    for (const fn of functions) {
      const first = (fn.ranges || [])[0];
      if (!first || (first.startOffset === 0 && first.endOffset === end)) continue;
      const key = `${first.startOffset}-${first.endOffset}`;
      seen.functions.add(key);
      if (first.count > 0) seen.ran.add(key);
    }
  }
  return files;
}

function summary(files) {
  const out = {};
  for (const [file, seen] of files) out[file] = {functions: seen.functions.size, ran: seen.ran.size};
  return out;
}

function install() {
  const folder = process.env.TICO_UI_COVERAGE;
  const {chromium} = require('playwright');
  const name = path.basename(process.argv[1], '.cjs');
  const files = new Map();
  const pages = new Map(); // page -> {session, ready, queue}
  let attached = 0;
  let timer = null;

  // A failed write keeps the previous record. Nothing here may throw into the script under test.
  function write() {
    try {
      fs.mkdirSync(folder, {recursive: true});
      const target = path.join(folder, `${name}.json`);
      const record = {script: name, pages: attached, files: summary(files)};
      fs.writeFileSync(`${target}.tmp`, `${JSON.stringify(record, null, 1)}\n`);
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
if (folder && /[\\/]ui[\\/]tests[\\/][^\\/]+\.cjs$/.test(path.resolve(process.argv[1] || ''))) install();

module.exports = {fold, summary, SERVED, FLUSH_MS};
