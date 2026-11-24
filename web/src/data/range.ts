// Bounded reads of a file's bytes: a chat's card reads only the start of a file (or, for a zip, its end), never all
// of it. A read asks for a byte range and takes only a 206 whose Content-Range is the range asked for, so a server
// that sends the whole file instead is cut off at once; it never holds more than it asked for. A 0-byte file has no
// range (416) and reads as empty.
import { queryOptions } from '@tanstack/react-query';
import { ApiError } from '@/api/client';
import { ZIP_DIR_MAX, ZIP_TAIL, zipEnd, zipEntries, type ZipEntry } from '@/lib';

export interface Bytes {
  bytes: Uint8Array;
  /** The whole file's size. */
  total: number;
  /** The file goes on past what was read. */
  truncated: boolean;
}

export interface Text {
  text: string;
  total: number;
  truncated: boolean;
}

export interface RangeOptions {
  /** Where the read starts (a prefix read; 0 when left out). */
  from?: number;
  /** Read the file's last bytes instead. */
  suffix?: boolean;
  signal?: AbortSignal;
}

const refused = (status: number, why: string) => new ApiError(status, `range_${status}`, why, status >= 500);

/** Up to `bytes` bytes of the file at `url`. */
export async function rangeBytes(url: string, bytes: number, { from = 0, suffix = false, signal }: RangeOptions = {}): Promise<Bytes> {
  if (!(bytes > 0) || !Number.isSafeInteger(bytes) || from < 0) throw new RangeError('bad range');
  const res = await fetch(url, {
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { Range: suffix ? `bytes=-${bytes}` : `bytes=${from}-${from + bytes - 1}` },
    ...(signal && { signal }),
  });
  const range = res.headers.get('content-range') ?? '';
  if (res.status === 416) {
    void res.body?.cancel();
    if (/^bytes \*\/0$/.test(range)) return { bytes: new Uint8Array(0), total: 0, truncated: false };
    throw refused(416, 'range not satisfiable');
  }
  const m = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(range);
  const [start, end, total] = m ? [Number(m[1]), Number(m[2]), Number(m[3])] : [NaN, NaN, NaN];
  const length = end - start + 1;
  const asked = suffix ? end === total - 1 && start === Math.max(0, total - bytes) : start === from;
  if (res.status !== 206 || !m || !(start <= end && end < total) || length > bytes || !asked) {
    void res.body?.cancel();
    throw refused(res.status === 206 ? 502 : res.status, res.status === 206 ? 'bad content-range' : 'no range');
  }
  const out = new Uint8Array(length);
  let got = 0;
  const reader = res.body?.getReader();
  while (reader && got < length) {
    const { done, value } = await reader.read();
    if (done) break;
    const take = Math.min(value.length, length - got);
    out.set(value.subarray(0, take), got);
    got += take;
  }
  if (reader && got >= length) void reader.cancel();
  if (got < length) throw refused(502, 'short read');
  return { bytes: out, total, truncated: suffix ? start > 0 : end + 1 < total };
}

/** Bytes as text. When the file goes on past them, a character the read cut in half and the line it stopped in are
    left off, so the text ends on a whole line; a whole file is decoded as it is. */
export function textOf(read: Bytes): Text {
  if (!read.truncated) return { text: new TextDecoder().decode(read.bytes), total: read.total, truncated: false };
  const text = new TextDecoder().decode(read.bytes, { stream: true }); // stream: a cut-off last character is held back
  const cut = text.lastIndexOf('\n');
  return { text: cut >= 0 ? text.slice(0, cut + 1) : text, total: read.total, truncated: true };
}

export const rangeText = async (url: string, bytes: number, options?: RangeOptions) => textOf(await rangeBytes(url, bytes, options));

/** A file's first `bytes` bytes as text. Keyed by the size asked for, so a longer read never shows a shorter one. */
export const rangeTextQuery = (url: string, bytes: number) =>
  queryOptions({
    queryKey: ['files', 'range-text', url, bytes] as const,
    queryFn: ({ signal }) => rangeText(url, bytes, { signal }),
    staleTime: Infinity,
    gcTime: 5 * 60_000,
    retry: false,
  });

export interface ZipList {
  total: number;
  /** Entries in the archive; null for a Zip64 archive, which is known only by its size. */
  count: number | null;
  entries: ZipEntry[];
}

/** A zip file's names, up to `max`: its last 64 KB, then its central directory (at most 256 KB of it) when that is
    not already in them. Null when the file has no sound end record. */
export async function zipList(url: string, max: number, signal?: AbortSignal): Promise<ZipList | null> {
  const tail = await rangeBytes(url, ZIP_TAIL, { suffix: true, ...(signal && { signal }) });
  const end = zipEnd(tail.bytes, tail.total);
  if (end === 'zip64') return { total: tail.total, count: null, entries: [] };
  if (!end) return null;
  if (!end.count) return { total: tail.total, count: 0, entries: [] };
  const base = tail.total - tail.bytes.length;
  const want = Math.min(end.size, ZIP_DIR_MAX);
  const dir =
    end.at >= base ? tail.bytes.subarray(end.at - base, end.at - base + want) : (await rangeBytes(url, want, { from: end.at, ...(signal && { signal }) })).bytes;
  const entries = zipEntries(dir, max);
  return entries ? { total: tail.total, count: end.count, entries } : null;
}

/** The most names a zip's list reads; its card shows the first few. */
export const ZIP_NAMES = 1_000;

/** One read of a zip's names for its card and its full list alike. */
export const zipListQuery = (url: string) =>
  queryOptions({
    queryKey: ['files', 'zip-list', url] as const,
    queryFn: ({ signal }) => zipList(url, ZIP_NAMES, signal),
    staleTime: Infinity,
    gcTime: 5 * 60_000,
    retry: false,
  });
