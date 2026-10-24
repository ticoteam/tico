// Preloaded by the runner: scripts use Playwright directly, including ones that do not serve page.cjs.
// Wrap its public creation methods so defaults are set before the script starts using a page.
const {applyTimeouts, t} = require('./load.cjs');
const playwright = require('playwright');

// Playwright gives newPage and page.close no timeout. Under load either one waited forever while another page in the
// same context reloaded or navigated (people-access.cjs, Oct 22), and the browser job hung until CI cancelled it at
// 30 minutes. Bound both by the default timeout, so a stall fails and names the call.
function bounded(target, name, label) {
  const call = target[name];
  target[name] = function (...args) {
    let timer;
    const stalled = new Promise((_, reject) => {
      timer = setTimeout(() => {
        const error = new Error(`${label} did not finish in ${t(30000) / 1000}s`);
        console.error(error.message);   // now: a browser.close() that also stalls would keep the thrown error unseen
        reject(error);
      }, t(30000));
    });
    return Promise.race([call.apply(this, args), stalled]).finally(() => clearTimeout(timer));
  };
}

function pageTimeouts(page) {
  applyTimeouts(page);
  bounded(page, 'close', 'page.close');
}

function contextTimeouts(context) {
  applyTimeouts(context);
  bounded(context, 'newPage', 'browserContext.newPage');
  context.on('page', pageTimeouts);
  return context;
}

for (const engine of [playwright.chromium, playwright.firefox, playwright.webkit]) {
  const launch = engine.launch;
  engine.launch = async function (...args) {
    const browser = await launch.apply(this, args);
    const newContext = browser.newContext;
    browser.newContext = async function (...options) {
      return contextTimeouts(await newContext.apply(this, options));
    };
    return browser;
  };
  const persistent = engine.launchPersistentContext;
  engine.launchPersistentContext = async function (...args) {
    return contextTimeouts(await persistent.apply(this, args));
  };
}
