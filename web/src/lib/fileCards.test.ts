import { describe, expect, it } from 'vitest';
import { csvRows } from './csv';
import { FRAME_POLICY, safeFrame } from './safeFrame';
import { decodeWords, parseEml, parseIcs } from './smallFiles';
import { zipEnd, zipEntries } from './zip';

/** A stored (unpacked) zip of `files`, with an archive comment, as a zip tool writes one. */
function zipOf(files: Record<string, string>, comment = '') {
  const enc = new TextEncoder();
  const local: number[] = [];
  const central: number[] = [];
  const u16 = (out: number[], n: number) => out.push(n & 0xff, (n >> 8) & 0xff);
  const u32 = (out: number[], n: number) => out.push(n & 0xff, (n >> 8) & 0xff, (n >> 16) & 0xff, (n >>> 24) & 0xff);
  const names = Object.keys(files);
  for (const name of names) {
    const n = enc.encode(name);
    const data = enc.encode(files[name]!);
    const at = local.length;
    u32(local, 0x04034b50);
    [20, 0, 0, 0, 0].forEach((v) => u16(local, v));
    u32(local, 0);
    u32(local, data.length);
    u32(local, data.length);
    u16(local, n.length);
    u16(local, 0);
    local.push(...n, ...data);
    u32(central, 0x02014b50);
    [20, 20, 0, 0, 0, 0].forEach((v) => u16(central, v));
    u32(central, 0);
    u32(central, data.length);
    u32(central, data.length);
    [n.length, 0, 0, 0, 0].forEach((v) => u16(central, v));
    u32(central, 0);
    u32(central, at);
    central.push(...n);
  }
  const end: number[] = [];
  const note = enc.encode(comment);
  u32(end, 0x06054b50);
  [0, 0, names.length, names.length].forEach((v) => u16(end, v));
  u32(end, central.length);
  u32(end, local.length);
  u16(end, note.length);
  end.push(...note);
  return { bytes: Uint8Array.from([...local, ...central, ...end]), dirAt: local.length, dirSize: central.length };
}

describe('zip names', () => {
  it('finds the end record and lists the directory', () => {
    const z = zipOf({ 'a.txt': 'hello', 'docs/': '', 'docs/b.md': '# b' });
    const end = zipEnd(z.bytes, z.bytes.length);
    expect(end).toEqual({ count: 3, at: z.dirAt, size: z.dirSize });
    if (!end || end === 'zip64') throw new Error('no end');
    const names = zipEntries(z.bytes.subarray(end.at, end.at + end.size));
    expect(names?.map((e) => [e.name, e.size])).toEqual([
      ['a.txt', 5],
      ['docs/', 0],
      ['docs/b.md', 3],
    ]);
  });

  it('finds the end record behind a comment, read from only the file’s tail', () => {
    // the comment holds what looks like an end record: only the one whose comment ends at the file's end counts
    const fake = 'PK\u0005\u0006' + 'x'.repeat(30);
    const z = zipOf({ 'a.txt': 'hello' }, `${'y'.repeat(2000)}${fake}`);
    const tail = z.bytes.subarray(z.bytes.length - 1500);
    expect(zipEnd(z.bytes, z.bytes.length)).toEqual({ count: 1, at: z.dirAt, size: z.dirSize });
    expect(zipEnd(tail, z.bytes.length)).toBeNull(); // the end record is before this tail: no false find in the comment
  });

  it('lists nothing for offsets that point past the end record', () => {
    const z = zipOf({ 'a.txt': 'hello' });
    const bad = z.bytes.slice();
    const at = bad.length - 22 + 16;
    new DataView(bad.buffer).setUint32(at, 0x7fff0000, true); // the directory "starts" far past the file
    expect(zipEnd(bad, bad.length)).toBeNull();
    // a directory whose first record is not one lists nothing
    expect(zipEntries(new Uint8Array(64))).toBeNull();
  });

  it('knows Zip64 only as such', () => {
    const z = zipOf({ 'a.txt': 'hello' });
    const big = z.bytes.slice();
    new DataView(big.buffer).setUint32(big.length - 22 + 16, 0xffffffff, true);
    expect(zipEnd(big, big.length)).toBe('zip64');
  });
});

describe('csvRows', () => {
  it('splits on the delimiter it is given', () => {
    expect(csvRows('a\tb,c\n1\t"2\t3"\n', '\t')).toEqual([
      ['a', 'b,c'],
      ['1', '2\t3'],
    ]);
  });

  it('drops a last record a partial read cut off, and keeps a whole file’s last line', () => {
    expect(csvRows('a,b\n1,2\n3,"long\nfield', ',', { partial: true })).toEqual([
      ['a', 'b'],
      ['1', '2'],
    ]);
    expect(csvRows('a,b\n1,2', ',')).toEqual([
      ['a', 'b'],
      ['1', '2'],
    ]);
  });
});

describe('safeFrame', () => {
  const html = '<html><head><meta http-equiv="refresh" content="0;url=https://evil.example"><link rel="preload" href="https://evil.example/x"><title>T</title></head><body><img src="https://evil.example/p.png"><p>Hi</p></body></html>';

  it('puts its policy first in the head and drops refreshes and resource hints', () => {
    const doc = new DOMParser().parseFromString(safeFrame(html)!, 'text/html');
    const first = doc.head.firstElementChild;
    expect(first?.getAttribute('http-equiv')).toBe('Content-Security-Policy');
    expect(first?.getAttribute('content')).toBe(FRAME_POLICY);
    expect(doc.querySelector('meta[http-equiv="refresh"]')).toBeNull();
    expect(doc.querySelector('link[rel="preload"]')).toBeNull();
    expect(doc.body.textContent).toBe('Hi');
  });

  it('allows pictures only from data: addresses, so an outside picture never loads', () => {
    const img = FRAME_POLICY.split(';').map((d) => d.trim()).find((d) => d.startsWith('img-src'));
    expect(img).toBe('img-src data:');
    expect(FRAME_POLICY).toMatch(/default-src 'none'/);
    // the policy comes before the picture, so the browser reads it first
    const out = safeFrame(html)!;
    expect(out.indexOf('Content-Security-Policy')).toBeLessThan(out.indexOf('evil.example/p.png'));
  });
});

describe('safeFrame links and mutations', () => {
  it('opens links in a new tab, to the web, mail or the page only', () => {
    const doc = new DOMParser().parseFromString(safeFrame('<a href="https://acme.example">a</a><a href="javascript:alert(1)">b</a>')!, 'text/html');
    const [web, script] = [...doc.querySelectorAll('a')];
    expect([web?.getAttribute('href'), web?.getAttribute('target'), web?.getAttribute('rel')]).toEqual(['https://acme.example', '_blank', 'noopener noreferrer']);
    expect(script?.hasAttribute('href')).toBe(false);
  });

  it('applies the same rules to image-map areas, and keeps a fragment link inside the srcdoc page', () => {
    const html = '<map name="m"><area href="javascript:alert(1)"><area href="mailto:ana@acme.example"></map><a href="#top" target="_top" rel="opener">top</a>';
    const doc = new DOMParser().parseFromString(safeFrame(html)!, 'text/html');
    const [bad, mail] = [...doc.querySelectorAll('area')];
    expect(bad?.hasAttribute('href')).toBe(false);
    expect([mail?.getAttribute('target'), mail?.getAttribute('rel')]).toEqual(['_blank', 'noopener noreferrer']);
    const here = doc.querySelector('a');
    expect([here?.getAttribute('href'), here?.hasAttribute('target'), here?.hasAttribute('rel')]).toEqual(['about:srcdoc#top', false, false]);
  });

  it('refuses markup that grows a <link> back when parsed a second time', () => {
    // inert on the first parse (text in a <style>), a real <link> once serialized and parsed again
    const mutating = '<form><math><mtext></form><form><mglyph><style></math><link rel=stylesheet href=//evil.example/x>';
    expect(new DOMParser().parseFromString(mutating, 'text/html').querySelector('link')).toBeNull();
    expect(safeFrame(mutating)).toBeNull();
  });
});

describe('calendar invites', () => {
  it('shows an all-day event by its days, its last one the day before DTEND, alarms skipped', () => {
    const ics = (end: string) =>
      `BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Offsite\r\nDTSTART;VALUE=DATE:20261201\r\nDTEND;VALUE=DATE:${end}\r\nBEGIN:VALARM\r\nSUMMARY:Alarm\r\nLOCATION:nowhere\r\nEND:VALARM\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n`;
    const day = (d: number) => new Date(2026, 11, d).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
    expect(parseIcs(ics('20261202'))).toEqual({ title: 'Offsite', when: day(1), where: '' });
    expect(parseIcs(ics('20261204'))?.when).toBe(`${day(1)} – ${day(3)}`);
  });
});

describe('calendar times', () => {
  it('shows wall-clock times as written, each beside its own zone when the zones differ', () => {
    const ics = (start: string, end: string) => `BEGIN:VEVENT\r\nSUMMARY:Call\r\n${start}\r\n${end}\r\nEND:VEVENT\r\n`;
    expect(parseIcs(ics('DTSTART;TZID=America/New_York:20261201T090000', 'DTEND;TZID=Europe/London:20261201T150000'))?.when).toBe(
      '2026-12-01 09:00 America/New_York – 2026-12-01 15:00 Europe/London',
    );
    expect(parseIcs(ics('DTSTART;TZID=America/New_York:20261201T090000', 'DTEND;TZID=America/New_York:20261201T100000'))?.when).toBe(
      '2026-12-01 09:00 – 2026-12-01 10:00 America/New_York',
    );
  });
});

describe('email headers', () => {
  it('decodes encoded words as plain text, joining adjacent ones and unfolding headers', () => {
    expect(decodeWords('=?UTF-8?B?Q2Fmw6k=?= =?utf-8?Q?_ouvert?=')).toBe('Café ouvert');
    expect(decodeWords('=?iso-8859-1?q?r=E9sum=E9?= is <b>here</b>')).toBe('résumé is <b>here</b>');
    const mail = parseEml('From: =?utf-8?Q?Ana_Exampl=C3=A9?= <ana@acme.example>\r\nSubject: Quarterly\r\n plan\r\nDate: not a date\r\n\r\nBody: not a header\r\n');
    expect(mail).toEqual({ from: 'Ana Examplé <ana@acme.example>', subject: 'Quarterly plan', date: 'not a date' });
  });
});
