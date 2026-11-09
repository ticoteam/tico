#!/usr/bin/env node
// Changed pixels between screenshot pairs. Each PNG is decoded here with Node's built-in zlib, so no browser runs and
// CI's Python shards need none. A channel differing by no more than TOLERANCE counts as the same pixel, so
// anti-aliasing noise does not read as a change. scripts/release_shots.py calls it once per release:
//
//   node scripts/screenshots/compare.cts pairs.json results.json
//
// pairs.json: [{"key": "...", "before": "/abs/before.png", "after": "/abs/after.png"}]
// results.json: [{"key", "width", "height", "sizeMismatch", "changed", "total", "pct"}], pct is 0-100.
// Decodes 8-bit, non-interlaced PNGs of RGBA (6), RGB (2, opaque), gray (0) and gray+alpha (4), with all five scanline
// filters. Any other PNG (palette, 16-bit, interlaced, transparency chunks) is refused with an error naming the file.
const fs: typeof import('node:fs') = require('node:fs');
const zlib: typeof import('node:zlib') = require('node:zlib');

// One pair of screenshots to compare, as listed in pairs.json.
interface Pair {
  key: string;
  before: string;
  after: string;
}

// The decoded image: its size and its pixels as RGBA bytes.
interface Decoded {
  width: number;
  height: number;
  rgba: Uint8Array;
}

// The IHDR chunk's fields, as read from the file.
interface Header {
  width: number;
  height: number;
  bitDepth: number;
  colorType: number;
  compression: number;
  filter: number;
  interlace: number;
}

// One line of results.json.
interface Result {
  key: string;
  width: number;
  height: number;
  sizeMismatch: boolean;
  changed: number;
  total: number;
  pct: number;
}

const TOLERANCE = 2;
const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
// Samples per pixel by color type.
const CHANNELS: Record<number, number> = {0: 1, 2: 3, 4: 2, 6: 4};

function paeth(a: number, b: number, c: number): number {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  if (pa <= pb && pa <= pc) return a;
  return pb <= pc ? b : c;
}

// Reverses the scanline filters. Each row starts with its filter type byte; a is the same row's previous sample, b the
// row above at the same column and c the row above one sample back, all already reconstructed.
function unfilter(raw: Buffer, width: number, height: number, channels: number, file: string): Buffer {
  const stride = width * channels;
  const out = Buffer.alloc(stride * height);
  for (let y = 0; y < height; y++) {
    const type = raw[y * (stride + 1)]!;
    if (type > 4) throw new Error(`${file}: unknown scanline filter ${type}`);
    const src = y * (stride + 1) + 1;
    const row = y * stride;
    const up = row - stride;
    for (let i = 0; i < stride; i++) {
      const x = raw[src + i]!;
      const a = i >= channels ? out[row + i - channels]! : 0;
      const b = y > 0 ? out[up + i]! : 0;
      const c = y > 0 && i >= channels ? out[up + i - channels]! : 0;
      let value: number;
      switch (type) {
        case 0: value = x; break;
        case 1: value = x + a; break;
        case 2: value = x + b; break;
        case 3: value = x + ((a + b) >> 1); break;
        default: value = x + paeth(a, b, c);
      }
      out[row + i] = value & 0xff;
    }
  }
  return out;
}

// Gray is copied into all three color channels; a missing alpha is opaque.
function toRgba(samples: Buffer, pixels: number, colorType: number): Uint8Array {
  const channels = CHANNELS[colorType]!;
  const gray = colorType === 0 || colorType === 4;
  const hasAlpha = colorType === 4 || colorType === 6;
  const rgba = new Uint8Array(pixels * 4);
  for (let p = 0; p < pixels; p++) {
    const s = p * channels;
    const o = p * 4;
    rgba[o] = samples[s]!;
    rgba[o + 1] = gray ? samples[s]! : samples[s + 1]!;
    rgba[o + 2] = gray ? samples[s]! : samples[s + 2]!;
    rgba[o + 3] = hasAlpha ? samples[s + channels - 1]! : 255;
  }
  return rgba;
}

// Decodes one PNG to RGBA bytes, or throws an error that names the file when the PNG is one this does not handle.
function decodePng(file: string): Decoded {
  const data = fs.readFileSync(file);
  const refuse: (reason: string) => never = (reason) => {
    throw new Error(`${file}: ${reason}`);
  };
  if (data.length < 8 || !data.subarray(0, 8).equals(SIGNATURE)) refuse('not a PNG file');
  let header: Header | null = null;
  const idat: Buffer[] = [];
  for (let offset = 8; ;) {
    if (offset + 12 > data.length) refuse('truncated PNG: no IEND chunk');
    const length = data.readUInt32BE(offset);
    const kind = data.toString('latin1', offset + 4, offset + 8);
    const start = offset + 8;
    const end = start + length;
    if (end + 4 > data.length) refuse(`truncated PNG: the ${kind} chunk runs past the end of the file`);
    if (kind === 'IHDR') {
      if (length !== 13) refuse('bad IHDR chunk');
      header = {
        width: data.readUInt32BE(start),
        height: data.readUInt32BE(start + 4),
        bitDepth: data[start + 8]!,
        colorType: data[start + 9]!,
        compression: data[start + 10]!,
        filter: data[start + 11]!,
        interlace: data[start + 12]!,
      };
    } else if (kind === 'IDAT') {
      if (!header) refuse('IDAT before IHDR');
      idat.push(data.subarray(start, end));
    } else if (kind === 'tRNS') {
      refuse('transparency chunks (tRNS) are not supported');
    } else if (kind === 'IEND') {
      break;
    }
    offset = end + 4;
  }
  if (!header) refuse('no IHDR chunk');
  const {width, height, bitDepth, colorType, compression, filter, interlace} = header;
  if (colorType === 3) refuse('palette PNGs are not supported (need RGBA, RGB, gray or gray+alpha)');
  if (!(colorType in CHANNELS)) refuse(`unknown color type ${colorType}`);
  if (bitDepth !== 8) refuse(`${bitDepth}-bit PNGs are not supported (need 8-bit)`);
  if (interlace !== 0) refuse('interlaced PNGs are not supported');
  if (compression !== 0 || filter !== 0) refuse('unknown compression or filter method');
  if (!width || !height) refuse('the image has no pixels');
  if (!idat.length) refuse('no image data (IDAT)');
  let raw: Buffer;
  try {
    raw = zlib.inflateSync(Buffer.concat(idat));
  } catch (error) {
    refuse(`corrupt image data: ${(error as Error).message}`);
  }
  const stride = width * CHANNELS[colorType]!;
  if (raw.length !== height * (stride + 1)) {
    refuse(`image data is ${raw.length} bytes, expected ${height * (stride + 1)}`);
  }
  const samples = unfilter(raw, width, height, CHANNELS[colorType]!, file);
  return {width, height, rgba: toRgba(samples, width * height, colorType)};
}

// A pixel is changed when any of its four channels differs by more than TOLERANCE.
function compareFiles(beforeFile: string, afterFile: string): Omit<Result, 'key'> {
  const a = decodePng(beforeFile);
  const b = decodePng(afterFile);
  const {width, height} = a;
  const total = width * height;
  if (width !== b.width || height !== b.height) {
    return {width, height, sizeMismatch: true, changed: total, total, pct: 100};
  }
  const da = a.rgba;
  const db = b.rgba;
  let changed = 0;
  for (let i = 0; i < da.length; i += 4) {
    if (Math.abs(da[i]! - db[i]!) > TOLERANCE || Math.abs(da[i + 1]! - db[i + 1]!) > TOLERANCE
      || Math.abs(da[i + 2]! - db[i + 2]!) > TOLERANCE || Math.abs(da[i + 3]! - db[i + 3]!) > TOLERANCE) {
      changed++;
    }
  }
  return {width, height, sizeMismatch: false, changed, total, pct: total ? (100 * changed) / total : 0};
}

function main(): void {
  const [pairsFile, resultsFile] = process.argv.slice(2);
  if (!pairsFile || !resultsFile) throw new Error('usage: compare.js pairs.json results.json');
  const pairs: Pair[] = JSON.parse(fs.readFileSync(pairsFile, 'utf8'));
  const results: Result[] = pairs.map(pair => ({key: pair.key, ...compareFiles(pair.before, pair.after)}));
  fs.writeFileSync(resultsFile, JSON.stringify(results, null, 2) + '\n');
}

try {
  main();
} catch (error) {
  console.error(error);
  process.exit(1);
}
