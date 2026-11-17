// No browser: the Tools chat's key detector in the page (ui/app/secret-drop.ts) runs the shared case file and the rule
// file the server reads (backend/secret_rules.json), and agrees with backend/tests/test_secret_drop.py on every row:
// each item's tier, value, and the name, kind, service, username and type a case names (spec AC-2..5, 11, 17).
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const vm: typeof import('node:vm') = require('node:vm');
const path: typeof import('node:path') = require('node:path');

const root = path.join(__dirname, '../..');
// The page loads the SHA-256 fallback (ui/app/sha256.ts) before secret-drop.ts, so each context gets both, in that order.
const sha256 = fs.readFileSync(path.join(root, 'build/ui/app/sha256.js'), 'utf8');
const secretDrop = fs.readFileSync(path.join(root, 'build/ui/app/secret-drop.js'), 'utf8');
const exported = ';globalThis.sd = {sdScan, sdPreview, sdDigest};';
const context = vm.createContext({get: () => Promise.reject(new Error('no network in this test')), crypto: globalThis.crypto, TextEncoder});
vm.runInContext(sha256 + ';' + secretDrop + exported, context);
// A plain-http page: no crypto.subtle, so the digest comes from sha256Digest.
const plain = vm.createContext({get: () => Promise.reject(new Error('no network in this test')), TextEncoder, Uint8Array, Uint32Array, DataView, ArrayBuffer, Math});
vm.runInContext(sha256 + ';' + secretDrop + exported, plain);
type Item = {tier: string; value: string; name: string; [k: string]: string | number};
type SecretDrop = {sdScan(r: unknown, t: string): Item[]; sdPreview(i: {type: string; value: string}): string; sdDigest(r: unknown, v: string): Promise<string>};
const sd = (context as unknown as {sd: SecretDrop}).sd;
const plainSd = (plain as unknown as {sd: SecretDrop}).sd;
const rules: unknown = JSON.parse(fs.readFileSync(path.join(root, 'backend/secret_rules.json'), 'utf8'));
type Case = {id: string; text: string | string[]; expect: Record<string, string | string[]>[]};
const fx = JSON.parse(fs.readFileSync(path.join(root, 'backend/tests/fixtures/secret_drop_cases.json'), 'utf8')) as {cases: Case[]; ordinary: string[]};
const join = (v: string | string[]): string => Array.isArray(v) ? v.join('') : v;

for (const c of fx.cases) {
  const got = sd.sdScan(rules, join(c.text)).map(i => JSON.parse(JSON.stringify(i)) as Item);
  assert.equal(got.length, c.expect.length, `${c.id}: ${got.length} items, expected ${c.expect.length}`);
  c.expect.forEach((e, n) => {
    const g = got[n]!;
    for (const [k, v] of Object.entries(e)) assert.equal(g[k], join(v), `${c.id} item ${n}: ${k}`);
  });
}
// AC-11: 200 ordinary Tools sentences give no certain item and at most 2 suspects.
const ordinary = fx.ordinary.flatMap(s => sd.sdScan(rules, s));
assert.equal(ordinary.filter(i => i.tier === 'certain').length, 0, 'no certain item in ordinary sentences');
assert.ok(ordinary.filter(i => i.tier === 'suspect').length <= 2, 'at most 2 suspects in ordinary sentences');
// The preview follows the vault: first 3 + last 3 for a key of 12+, dots for a password, never more.
assert.equal(sd.sdPreview({type: 'token', value: 'abcdefghijklmnop'}), 'abc…nop');
assert.equal(sd.sdPreview({type: 'password', value: 'abcdefghijklmnop'}), '••••••');
assert.equal(sd.sdPreview({type: 'api_key', value: 'short1234'}), '••••••');
// The un-mark digest is the server's (secret_drop.digest: sha256 of the normalized value), on a secure page and on a
// plain-http one, which has no crypto.subtle. The pasted value carries a zero-width space, which the digest drops.
const expected = require('node:crypto').createHash('sha256').update('k3JdP9').digest('hex');
const pasted = 'k3J\u200bdP9';
void Promise.all([sd.sdDigest(rules, pasted), plainSd.sdDigest(rules, pasted)]).then(([secure, insecure]) => {
  assert.equal(secure, expected, 'digest with crypto.subtle');
  assert.equal(insecure, expected, 'digest without crypto.subtle (plain http)');
  console.log('secret-drop-unit: ok');
});
