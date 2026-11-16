/* ui/app/sha256.ts — SHA-256 in plain script, for a page without crypto.subtle. crypto.subtle exists only on a secure
   origin (https, or localhost), so a self-hosted hub on a LAN over plain http has none. An upload's idempotency digest
   is the same 32 bytes either way (sha256Bytes), so a retry's key does not depend on the origin. The check against
   Node's webcrypto is ui/tests/sha256-unit.cts. */
'use strict';

// The first 32 bits of the fractional parts of the cube roots of the first 64 primes (FIPS 180-4, 4.2.2).
const SHA256_ROUND = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

// The eight 32-bit words of the hash state.
type Sha256State = [number, number, number, number, number, number, number, number];
const SHA256_INITIAL: Sha256State = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];

// Rotate right. The result may be negative; the uint32 sums in sha256Block wrap it back.
const sha256Rotr = (x: number, n: number): number => (x >>> n) | (x << (32 - n));

// One 64-byte block of `view` at `at`, folded into the state, which comes back as a new one. `w` is scratch for the schedule.
function sha256Block(state: Sha256State, view: DataView, at: number, w: Uint32Array): Sha256State {
  for (let i = 0; i < 16; i++) w[i] = view.getUint32(at + i * 4);
  for (let i = 16; i < 64; i++) {
    const x = w[i - 15]!, y = w[i - 2]!;
    const s0 = sha256Rotr(x, 7) ^ sha256Rotr(x, 18) ^ (x >>> 3);
    const s1 = sha256Rotr(y, 17) ^ sha256Rotr(y, 19) ^ (y >>> 10);
    w[i] = (w[i - 16]! + s0 + w[i - 7]! + s1) >>> 0;
  }
  let [a, b, c, d, e, f, g, k] = state;
  for (let i = 0; i < 64; i++) {
    const t1 = (k + (sha256Rotr(e, 6) ^ sha256Rotr(e, 11) ^ sha256Rotr(e, 25)) + ((e & f) ^ (~e & g)) + SHA256_ROUND[i]! + w[i]!) >>> 0;
    const t2 = ((sha256Rotr(a, 2) ^ sha256Rotr(a, 13) ^ sha256Rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
    k = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
  }
  return [(state[0] + a) >>> 0, (state[1] + b) >>> 0, (state[2] + c) >>> 0, (state[3] + d) >>> 0,
    (state[4] + e) >>> 0, (state[5] + f) >>> 0, (state[6] + g) >>> 0, (state[7] + k) >>> 0];
}

// The SHA-256 of the bytes, as crypto.subtle.digest('SHA-256', bytes) answers it. The message is read in place, block by
// block; only its last one or two blocks (the padding and the length) are copied.
function sha256Digest(data: ArrayBuffer): ArrayBuffer {
  let state = SHA256_INITIAL;
  const w = new Uint32Array(64);
  const whole = Math.floor(data.byteLength / 64) * 64;
  const view = new DataView(data);
  for (let at = 0; at < whole; at += 64) state = sha256Block(state, view, at, w);
  // What is left, then a 0x80 byte, zeros and the length in bits as 64 bits: one block if that fits in 64 bytes, else two.
  const rest = data.byteLength - whole;
  const tail = new Uint8Array(rest < 56 ? 64 : 128);
  tail.set(new Uint8Array(data, whole, rest));
  tail[rest] = 0x80;
  const bits = data.byteLength * 8;
  const tailView = new DataView(tail.buffer);
  tailView.setUint32(tail.length - 8, Math.floor(bits / 0x100000000));
  tailView.setUint32(tail.length - 4, bits >>> 0);
  for (let at = 0; at < tail.length; at += 64) state = sha256Block(state, tailView, at, w);
  const out = new ArrayBuffer(32);
  const outView = new DataView(out);
  state.forEach((word, i) => outView.setUint32(i * 4, word));
  return out;
}

// The digest an upload's idempotency signature carries: crypto.subtle where the page has it, else sha256Digest.
async function sha256Bytes(data: ArrayBuffer): Promise<ArrayBuffer> {
  return typeof crypto !== 'undefined' && crypto.subtle ? crypto.subtle.digest('SHA-256', data) : sha256Digest(data);
}
