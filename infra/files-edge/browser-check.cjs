// Optional builder check: synthetic local HTTP fixture, no AWS or team state.
// Requires Playwright and Chromium installed (same setup as the UI checks).
// The app is 127.0.0.1 and the files host is localhost: two sites, as the app and `<workspace>-files.<FilesDomain>` are.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const http = require('node:http');
const vm = require('node:vm');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const template = JSON.parse(execFileSync('python3', [path.join(__dirname, 'template.py')]));
const context = vm.createContext({});
vm.runInContext(template.Resources.VisualsResponse.Properties.FunctionCode, context);
const attack = `let readDenied = false, writeDenied = false, parentDenied = false, sawApp = false, stored = false;
try { sawApp = document.cookie.includes('app='); } catch (e) { readDenied = e.name === 'SecurityError'; }
try { document.cookie = 'script=bad; Path=/'; } catch (e) { writeDenied = e.name === 'SecurityError'; }
try { parent.document.body; } catch (e) { parentDenied = e.name === 'SecurityError'; }
try { localStorage.setItem('k', 'v'); stored = localStorage.getItem('k') === 'v'; } catch (e) {}
parent.postMessage({readDenied, writeDenied, parentDenied, sawApp, stored}, '*');`;
const hash = "'sha256-" + crypto.createHash('sha256').update(attack).digest('base64') + "'";

(async () => {
    let browser;
    const server = http.createServer((req, res) => {
        const port = server.address().port;
        if (req.url.startsWith('/app')) {
            res.setHeader('Content-Type', 'text/html');
            res.setHeader('Set-Cookie', 'app=secret; Path=/');
            const version = req.url.slice(-1);
            res.end(`<script>window.addEventListener('message', e => window.receipt = {...e.data, origin: e.origin})</script>` +
                `<iframe src="http://localhost:${port}/visual${version}"></iframe>`);
            return;
        }
        const out = context.handler({response: {statusCode: 200, cookies: {evil: {value: 'bad'}}, headers: {
            'content-type': {value: 'text/html'},
            'content-security-policy': {value: 'sandbox allow-same-origin allow-scripts allow-top-navigation'},
            'set-cookie': {value: 'header=bad; Path=/'},
            'x-amz-meta-tico-visual-policy-version': {value: req.url.slice(-1)},
            'x-amz-meta-tico-visual-ancestors': {value: 'http://127.0.0.1:' + port},
            'x-amz-meta-tico-visual-script': {value: hash},
            'x-amz-meta-tico-visual-csp': {value: 'sandbox allow-same-origin allow-top-navigation'},
        }}});
        for (const [name, header] of Object.entries(out.headers)) res.setHeader(name, header.value);
        res.writeHead(out.statusCode);
        res.end('<meta http-equiv="Content-Security-Policy" content="sandbox allow-same-origin allow-scripts">' +
            '<meta http-equiv="Set-Cookie" content="meta=bad; Path=/">' + '<script>' + attack + '</script>');
    });
    try {
        await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
        const port = server.address().port;
        browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
        const receipt = async version => {
            const page = await browser.newPage();
            await page.goto(`http://127.0.0.1:${port}/app${version}`);
            await page.waitForFunction(() => window.receipt, undefined, {timeout: 10000});
            const got = await page.evaluate(() => window.receipt);
            const cookies = (await page.context().cookies('http://127.0.0.1:' + port)).map(c => c.name);
            await page.context().close();
            return {got, cookies};
        };
        // Version 1: an opaque origin, no cookies or storage at all.
        const closed = await receipt('1');
        assert.deepEqual(closed.got, {readDenied: true, writeDenied: true, parentDenied: true, sawApp: false,
            stored: false, origin: 'null'});
        assert.deepEqual(closed.cookies, ['app']);
        // Version 2: the page has its own origin and storage, never the app's cookies or window.
        const open = await receipt('2');
        assert.deepEqual(open.got, {readDenied: false, writeDenied: false, parentDenied: true, sawApp: false,
            stored: true, origin: 'http://localhost:' + port});
        assert.deepEqual(open.cookies, ['app']);
        console.log('PASS: v1 opaque, cookie read/write and parent access denied; v2 own origin and storage, ' +
            "never the app's cookies or window; header/meta/metadata cookies never set");
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
