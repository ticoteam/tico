/* What a chat's small cards read out of a text file's first bytes: a calendar invite's title, time and place, a
   contact's name, email and phone, an email's sender, subject and date, a log without its colour codes. Hand-written and forgiving: whatever they cannot read is left out, and everything they return is plain text
   the card draws as text. */

const ESC = String.fromCharCode(27);
const BEL = String.fromCharCode(7);
/** A terminal's colour and cursor codes (CSI and OSC sequences, and the two-byte ones). */
const ANSI = new RegExp(`${ESC}\\[[0-?]*[ -/]*[@-~]|${ESC}\\][^${BEL}${ESC}]*(?:${BEL}|${ESC}\\\\)|${ESC}[@-Z\\\\-_]`, 'g');

export const stripAnsi = (text: string) => text.replace(ANSI, '');

/** A text file's first `n` lines. */
export const firstLines = (text: string, n: number) => text.split(/\r?\n/, n).join('\n');

/** Lines with their folded continuations joined (iCalendar, vCard and mail headers fold a long line onto the next
    with leading whitespace). `keepSpace`: mail keeps the fold's whitespace; iCalendar and vCard drop one. */
function unfold(text: string, keepSpace: boolean): string[] {
  return text.replace(/\r?\n[ \t]/g, keepSpace ? ' ' : '').split(/\r?\n/);
}

/** `NAME;PARAM=x:value` (a vCard's group prefix, `item1.EMAIL`, dropped); a quoted parameter may hold a colon. */
function property(line: string): { name: string; params: string; value: string } | null {
  let quoted = false;
  let i = 0;
  for (; i < line.length; i++) {
    if (line[i] === '"') quoted = !quoted;
    else if (line[i] === ':' && !quoted) break;
  }
  if (i >= line.length) return null;
  const m = /^(?:[A-Za-z0-9-]+\.)?([A-Za-z0-9-]+)(;.*)?$/.exec(line.slice(0, i));
  return m ? { name: m[1]!.toUpperCase(), params: m[2] ?? '', value: line.slice(i + 1) } : null;
}

const unescapeText = (v: string) => v.replace(/\\([nN,;\\])/g, (_, c: string) => (c === 'n' || c === 'N' ? ' ' : c)).trim();

export interface Invite {
  title: string;
  when: string;
  where: string;
}

interface Stamp {
  /** A day (an all-day event's) or a UTC time, as a Date; null for a wall-clock time. */
  date: Date | null;
  day: boolean;
  /** A zoned or floating time as written (`2026-12-01 09:00`), never turned into a Date: the reader's own zone
      would shift it. */
  wall: string;
  /** The zone a wall time is in (TZID), as written. */
  zone: string;
}

/** An iCalendar date or time (`20261201`, `20261201T100000`, `…Z`, with a TZID or none); null when unreadable. Only a
    UTC time is the reader's own; a zoned or floating one is the wall-clock time it names. */
function stamp(p: { params: string; value: string } | undefined): Stamp | null {
  const m = p && /^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?(Z)?)?$/.exec(p.value.trim());
  if (!p || !m) return null;
  const [y, mo, d, h, mi, s, z] = m.slice(1).map((x) => x ?? '');
  const day = !h || /(^|;)VALUE=DATE(;|$)/i.test(p.params);
  if (!day && !z) return { date: null, day, wall: `${y}-${mo}-${d} ${h}:${mi}`, zone: /(?:^|;)TZID=("?)([^";]*)\1/i.exec(p.params)?.[2] ?? '' };
  const date = day ? new Date(+y!, +mo! - 1, +d!) : new Date(Date.UTC(+y!, +mo! - 1, +d!, +h!, +mi!, +(s || 0)));
  return Number.isNaN(date.getTime()) ? null : { date, day, wall: '', zone: '' };
}

const DAY: Intl.DateTimeFormatOptions = { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' };
const sayDay = (d: Date) => d.toLocaleDateString(undefined, DAY);
const sayTime = (s: Stamp) => (s.date ? s.date.toLocaleString(undefined, { ...DAY, hour: 'numeric', minute: '2-digit' }) : s.wall);

/** When an event is: an all-day one by its days (its end is the day after its last), a timed one by its times, a
    wall-clock time as written with its zone's name (each its own when start and end are in different zones). */
function when(start: Stamp | null, end: Stamp | null): string {
  if (!start) return '';
  if (start.day && start.date) {
    const last = end?.day && end.date ? new Date(end.date.getFullYear(), end.date.getMonth(), end.date.getDate() - 1) : null;
    return last && last > start.date ? `${sayDay(start.date)} – ${sayDay(last)}` : sayDay(start.date);
  }
  const zoned = (t: Stamp) => (t.zone ? `${sayTime(t)} ${t.zone}` : sayTime(t));
  const b = end && !end.day ? end : null;
  if (!b || sayTime(b) === sayTime(start)) return zoned(start);
  // one zone said once at the end; two zones each beside its own time
  return b.zone !== start.zone ? `${zoned(start)} – ${zoned(b)}` : `${sayTime(start)} – ${zoned(b)}`;
}

/** The first event of a calendar file (its alarms and other parts inside it skipped); null when it has nothing to
    show. */
export function parseIcs(text: string): Invite | null {
  let inEvent = false;
  let inner = 0;
  const got: Record<string, { params: string; value: string }> = {};
  for (const line of unfold(text, false)) {
    if (!inEvent) {
      inEvent = /^BEGIN:VEVENT$/i.test(line);
      continue;
    }
    if (/^BEGIN:/i.test(line)) inner++;
    else if (/^END:/i.test(line)) {
      if (!inner) break;
      inner--;
    } else if (!inner) {
      const p = property(line);
      if (p && !got[p.name]) got[p.name] = p;
    }
  }
  const invite = { title: unescapeText(got.SUMMARY?.value ?? ''), when: when(stamp(got.DTSTART), stamp(got.DTEND)), where: unescapeText(got.LOCATION?.value ?? '') };
  return invite.title || invite.when || invite.where ? invite : null;
}

export interface Contact {
  name: string;
  email: string;
  phone: string;
}

/** The first contact of a vCard file; null when it has nothing to show. */
export function parseVcf(text: string): Contact | null {
  const lines = unfold(text, false);
  const at = lines.findIndex((l) => /^BEGIN:VCARD$/i.test(l));
  if (at < 0) return null;
  const got: Record<string, string> = {};
  for (const line of lines.slice(at + 1)) {
    if (/^END:VCARD$/i.test(line)) break;
    const p = property(line);
    if (p && !(p.name in got)) got[p.name] = p.value;
  }
  const structured = (got.N ?? '').split(';').map(unescapeText);
  const name = unescapeText(got.FN ?? '') || [structured[1], structured[0]].filter(Boolean).join(' ');
  const c = { name, email: unescapeText(got.EMAIL ?? ''), phone: unescapeText((got.TEL ?? '').replace(/^tel:/i, '')) };
  return c.name || c.email || c.phone ? c : null;
}

function decodeBytes(bytes: Uint8Array, charset: string): string {
  try {
    return new TextDecoder(charset.trim().toLowerCase() || 'utf-8').decode(bytes);
  } catch {
    return new TextDecoder().decode(bytes); // a charset the browser does not know
  }
}

/** One RFC 2047 encoded word's text (`=?utf-8?B?…?=`, `=?iso-8859-1?Q?…?=`). */
function decodeWord(charset: string, how: string, data: string): string {
  if (how.toUpperCase() === 'B') {
    try {
      const raw = atob(data.replace(/[^A-Za-z0-9+/=]/g, ''));
      return decodeBytes(Uint8Array.from(raw, (c) => c.charCodeAt(0)), charset);
    } catch {
      return data;
    }
  }
  const bytes: number[] = [];
  for (let i = 0; i < data.length; i++) {
    const c = data[i]!;
    if (c === '_') bytes.push(0x20);
    else if (c === '=' && /^[0-9A-Fa-f]{2}$/.test(data.slice(i + 1, i + 3))) {
      bytes.push(parseInt(data.slice(i + 1, i + 3), 16));
      i += 2;
    } else bytes.push(c.charCodeAt(0) & 0xff);
  }
  return decodeBytes(Uint8Array.from(bytes), charset);
}

/** A mail header's text with its encoded words decoded; the space between two encoded words is dropped, as the
    RFC says. Plain text only: the result is never read as markup. */
export function decodeWords(value: string): string {
  return value
    .replace(/(=\?[^?\s]+\?[BbQq]\?[^?\s]*\?=)\s+(?==\?[^?\s]+\?[BbQq]\?[^?\s]*\?=)/g, '$1')
    .replace(/=\?([^?\s]+)\?([BbQq])\?([^?\s]*)\?=/g, (_, charset: string, how: string, data: string) => decodeWord(charset.split('*')[0] ?? '', how, data));
}

export interface Mail {
  from: string;
  subject: string;
  date: string;
}

/** An email's sender, subject and date, from its headers (up to the first blank line); null when it has none. */
export function parseEml(text: string): Mail | null {
  const head = text.split(/\r?\n\r?\n/, 1)[0] ?? '';
  const got: Record<string, string> = {};
  for (const line of unfold(head, true)) {
    const m = /^([A-Za-z0-9-]+):\s*(.*)$/.exec(line);
    if (m && !(m[1]!.toLowerCase() in got)) got[m[1]!.toLowerCase()] = m[2]!.trim();
  }
  if (!got.from && !got.subject && !got.date) return null;
  const when = got.date ? new Date(got.date) : null;
  return {
    from: decodeWords(got.from ?? ''),
    subject: decodeWords(got.subject ?? ''),
    date: when && !Number.isNaN(when.getTime()) ? when.toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }) : (got.date ?? ''),
  };
}
