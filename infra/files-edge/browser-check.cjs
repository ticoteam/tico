// Optional builder check: synthetic local HTTP fixture, no AWS or team state.
// Requires Playwright and Chromium installed (same setup as the UI checks).
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
const attack = `let readDenied = false, writeDenied = false, parentDenied = false;
try { document.cookie; } catch (e) { readDenied = e.name === 'SecurityError'; }
try { document.cookie = 'script=bad; Path=/'; } catch (e) { writeDenied = e.name === 'SecurityError'; }
try { parent.document.body; } catch (e) { parentDenied = e.name === 'SecurityError'; }
parent.postMessage({readDenied, writeDenied, parentDenied}, '*');`;
const hash = "'sha256-" + crypto.createHash('sha256').update(attack).digest('base64') + "'";

(async () => {
    let browser;
    const server = http.createServer((req, res) => {
        if (req.url === '/') {
            res.setHeader('Content-Type', 'text/html');
            res.end(`<script>window.addEventListener('message', e => window.receipt = {...e.data, origin: e.origin})</script><iframe src="/visual"></iframe>`);
            return;
        }
        const out = context.handler({response: {statusCode: 200, cookies: {evil: {value: 'bad'}}, headers: {
            'content-type': {value: 'text/html'},
            'content-security-policy': {value: 'sandbox allow-same-origin allow-scripts'},
            'set-cookie': {value: 'header=bad; Path=/'},
            'x-amz-meta-tico-visual-policy-version': {value: '1'},
            'x-amz-meta-tico-visual-ancestors': {value: 'http://127.0.0.1:' + server.address().port},
            'x-amz-meta-tico-visual-script': {value: hash},
            'x-amz-meta-tico-visual-csp': {value: 'sandbox allow-same-origin'},
        }}});
        for (const [name, header] of Object.entries(out.headers)) res.setHeader(name, header.value);
        res.writeHead(out.statusCode);
        res.end('<meta http-equiv="Content-Security-Policy" content="sandbox allow-same-origin allow-scripts">' +
            '<meta http-equiv="Set-Cookie" content="meta=bad; Path=/">' + '<script>' + attack + '</script>');
    });
    try {
        await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
        browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
        const page = await browser.newPage();
        await page.goto('http://127.0.0.1:' + server.address().port);
        await page.waitForFunction(() => window.receipt, undefined, {timeout: 10000});
        const receipt = await page.evaluate(() => window.receipt);
        assert.deepEqual(receipt, {readDenied: true, writeDenied: true, parentDenied: true, origin: 'null'});
        assert.deepEqual(await page.context().cookies(), []);
        console.log('PASS: hostile body/meta/header/metadata remains opaque; cookie read/write and parent access denied; zero cookies');
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
