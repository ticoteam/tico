import { afterEach, describe, expect, it, vi } from 'vitest';
import { rangeBytes, rangeText } from './range';

/** A server holding `file` that answers ranges as backend/file_delivery.py does, or `bad` for one that does not. */
function serve(file: Uint8Array<ArrayBuffer>, bad?: 'whole' | 'wrong-range') {
  const seen: string[] = [];
  vi.stubGlobal('fetch', async (_url: string, init?: RequestInit) => {
    const range = new Headers(init?.headers).get('range') ?? '';
    seen.push(range);
    if (bad === 'whole') return new Response(file, { status: 200 });
    if (!file.length) return new Response(null, { status: 416, headers: { 'Content-Range': 'bytes */0' } });
    const m = /^bytes=(\d*)-(\d*)$/.exec(range)!;
    const [start, end] = m[1] ? [Number(m[1]), Math.min(Number(m[2] || file.length - 1), file.length - 1)] : [Math.max(0, file.length - Number(m[2])), file.length - 1];
    const shownStart = bad === 'wrong-range' ? start + 1 : start;
    return new Response(file.slice(start, end + 1), { status: 206, headers: { 'Content-Range': `bytes ${shownStart}-${end}/${file.length}` } });
  });
  return seen;
}

const enc = (s: string) => new TextEncoder().encode(s);

afterEach(() => vi.unstubAllGlobals());

describe('rangeBytes', () => {
  it('asks for a range and takes only a 206 for that range', async () => {
    const seen = serve(enc('hello world'));
    const read = await rangeBytes('/f', 5);
    expect(seen).toEqual(['bytes=0-4']);
    expect(new TextDecoder().decode(read.bytes)).toBe('hello');
    expect(read).toMatchObject({ total: 11, truncated: true });
  });

  it('refuses a server that sends the whole file instead of the range', async () => {
    serve(enc('hello world'), 'whole');
    await expect(rangeBytes('/f', 5)).rejects.toThrow();
  });

  it('refuses a Content-Range that is not the range asked for', async () => {
    serve(enc('hello world'), 'wrong-range');
    await expect(rangeBytes('/f', 5)).rejects.toThrow();
  });

  it('reads a 0-byte file as empty', async () => {
    serve(new Uint8Array(0));
    const read = await rangeBytes('/f', 5);
    expect([read.bytes.length, read.total, read.truncated]).toEqual([0, 0, false]);
  });

  it('reads a suffix, cut only when the file is longer', async () => {
    serve(enc('0123456789'));
    const tail = await rangeBytes('/f', 4, { suffix: true });
    expect([new TextDecoder().decode(tail.bytes), tail.total, tail.truncated]).toEqual(['6789', 10, true]);
    const all = await rangeBytes('/f', 40, { suffix: true });
    expect([new TextDecoder().decode(all.bytes), all.total, all.truncated]).toEqual(['0123456789', 10, false]);
  });
});

describe('rangeText', () => {
  it('ends a cut read on a whole line and a whole character', async () => {
    // "é" is two bytes; the read stops between them, inside the third line
    serve(enc('one\ntwo\nthré\nfour\n'));
    const read = await rangeText('/f', 12);
    expect(read).toMatchObject({ text: 'one\ntwo\n', truncated: true });
  });

  it('never ends a cut read in half a character', async () => {
    serve(enc('abcé and more'));
    expect((await rangeText('/f', 4)).text).toBe('abc');
  });

  it('leaves a whole file as it is, last line and all', async () => {
    serve(enc('one\ntwo'));
    expect(await rangeText('/f', 100)).toMatchObject({ text: 'one\ntwo', truncated: false, total: 7 });
  });
});
