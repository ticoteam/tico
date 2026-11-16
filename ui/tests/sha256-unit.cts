// No browser: the SHA-256 in ui/app/sha256.ts, for a page without crypto.subtle (a non-secure origin, such as a LAN hub on
// plain http), gives the digest Node's webcrypto gives: every length from 0 to 200 (the 55/56 and 64-byte block edges),
// a few larger ones up to a megabyte, the NIST message, and through sha256Bytes both without crypto and with it.
// Run: node scripts/ui-tests.cts sha256-unit   (the build runs first)
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {webcrypto}: typeof import('node:crypto') = require('node:crypto');

// The compiled script runs in its own context: it defines its functions there, and its only outside names are these.
const source = fs.readFileSync(path.join(__dirname, '../../build/ui/app/sha256.js'), 'utf8');
function load(extra: Record<string, unknown>) {
  const context = vm.createContext({Uint8Array, Uint32Array, DataView, ArrayBuffer, Math, ...extra});
  vm.runInContext(source, context);
  return {
    digest: vm.runInContext('sha256Digest', context) as (data: ArrayBuffer) => ArrayBuffer,
    bytes: vm.runInContext('sha256Bytes', context) as (data: ArrayBuffer) => Promise<ArrayBuffer>,
  };
}

// Bytes of a fixed pattern, so a failure reproduces.
function pattern(n: number): Uint8Array {
  const out = new Uint8Array(n);
  for (let i = 0; i < n; i++) out[i] = (i * 73 + n * 29 + (i >> 8) * 11) & 255;
  return out;
}
const text = (s: string) => new TextEncoder().encode(s);
const inputs: Uint8Array[] = [text(''), text('abc'), text('abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq')];
for (let n = 0; n <= 200; n++) inputs.push(pattern(n));
for (const n of [255, 256, 511, 512, 1000, 65536, 1_000_000, 1_000_003]) inputs.push(pattern(n));

const hex = (buffer: ArrayBuffer): string => Buffer.from(new Uint8Array(buffer)).toString('hex');

(async () => {
  const fallback = load({});
  const native = load({crypto: webcrypto});
  for (const input of inputs) {
    const expected = Buffer.from(await webcrypto.subtle.digest('SHA-256', input)).toString('hex');
    assert.equal(hex(fallback.digest(input.slice().buffer as ArrayBuffer)), expected, `sha256Digest, ${input.length} bytes`);
    assert.equal(hex(await fallback.bytes(input.slice().buffer as ArrayBuffer)), expected, `sha256Bytes without crypto, ${input.length} bytes`);
    assert.equal(hex(await native.bytes(input.slice().buffer as ArrayBuffer)), expected, `sha256Bytes with crypto, ${input.length} bytes`);
  }
  console.log(`PASS sha256 unit: ${inputs.length} inputs (lengths 0-200, the block edges, up to 1 MB) give crypto.subtle's SHA-256`);
})().catch(error => { console.error(error); process.exitCode = 1; });
