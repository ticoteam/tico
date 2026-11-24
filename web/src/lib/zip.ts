/* A zip file's list of names, read from its end without unpacking anything: the end record (EOCD) says where the
   central directory is, and the directory names every entry. Each offset and length is checked against the bytes
   that are really there, so a damaged or hostile file lists nothing rather than reading past its end. Zip64 is
   known only as such. */

/** The most an end record can sit from the file's end: its 22 bytes and a comment of up to 65,535. */
export const ZIP_TAIL = 65_557;
/** The most of a central directory read. */
export const ZIP_DIR_MAX = 256 * 1024;

export interface ZipEnd {
  /** Entries in the whole archive. */
  count: number;
  /** Where the central directory starts in the file, and its length. */
  at: number;
  size: number;
}

export interface ZipEntry {
  name: string;
  /** Unpacked and packed bytes. */
  size: number;
  packed: number;
  method: number;
  /** Where its local header starts in the file. */
  offset: number;
}

const EOCD = 0x06054b50;
const LOCATOR64 = 0x07064b50;
const CENTRAL = 0x02014b50;

const view = (b: Uint8Array) => new DataView(b.buffer, b.byteOffset, b.byteLength);

/**
 * The end record in `tail`, the last bytes of a file `total` bytes long, scanning back from the end: the first
 * candidate whose comment ends exactly at the file's end and whose directory lies wholly before it. `zip64` when the
 * archive needs Zip64's fields; null when there is no sound end record.
 */
export function zipEnd(tail: Uint8Array, total: number): ZipEnd | 'zip64' | null {
  const d = view(tail);
  const base = total - tail.length;
  if (base < 0) return null;
  for (let i = tail.length - 22; i >= 0; i--) {
    if (d.getUint32(i, true) !== EOCD) continue;
    if (i + 22 + d.getUint16(i + 20, true) !== tail.length) continue;
    const disk = d.getUint16(i + 4, true);
    const dirDisk = d.getUint16(i + 6, true);
    const here = d.getUint16(i + 8, true);
    const count = d.getUint16(i + 10, true);
    const size = d.getUint32(i + 12, true);
    const at = d.getUint32(i + 16, true);
    if (count === 0xffff || here === 0xffff || size === 0xffffffff || at === 0xffffffff) return 'zip64';
    if (i >= 20 && d.getUint32(i - 20, true) === LOCATOR64) return 'zip64';
    if (disk !== 0 || dirDisk !== 0 || here !== count) return null; // a split archive
    if (at + size > base + i) return null;
    if (count > 0 && size < 46 * count) return null;
    return { count, at, size };
  }
  return null;
}

/** The high half of the DOS code page (CP437), which zip tools used for names before UTF-8. */
const CP437 = 'ÇüéâäàåçêëèïîìÄÅÉæÆôöòûùÿÖÜ¢£¥₧ƒáíóúñÑªº¿⌐¬½¼¡«»░▒▓│┤╡╢╖╕╣║╗╝╜╛┐└┴┬├─┼╞╟╚╔╩╦╠═╬╧╨╤╥╙╘╒╓╫╪┘┌█▄▌▐▀αßΓπΣσµτΦΘΩδ∞φε∩≡±≥≤⌠⌡÷≈°∙·√ⁿ²■ ';
const strict = new TextDecoder('utf-8', { fatal: true });

/** An entry's name. Flagged UTF-8 (bit 11), or an Info-ZIP Unicode Path field (0x7075), is read as UTF-8. An
    unflagged name is read as UTF-8 too when it is valid UTF-8, since many tools write UTF-8 without the flag;
    otherwise as CP437, as older Windows and DOS tools wrote it. */
function nameOf(raw: Uint8Array, flags: number, extra: Uint8Array): string {
  const x = view(extra);
  for (let j = 0; j + 4 <= extra.length; ) {
    const id = x.getUint16(j, true);
    const len = x.getUint16(j + 2, true);
    if (j + 4 + len > extra.length) break;
    if (id === 0x7075 && len > 5 && extra[j + 4] === 1) return new TextDecoder().decode(extra.subarray(j + 9, j + 4 + len));
    j += 4 + len;
  }
  if (flags & 0x800) return new TextDecoder().decode(raw);
  try {
    return strict.decode(raw);
  } catch {
    return Array.from(raw, (b) => (b < 0x80 ? String.fromCharCode(b) : CP437[b - 0x80])).join('');
  }
}

/** Up to `max` entries of a central directory (or its first part); null when its first record is not one. */
export function zipEntries(dir: Uint8Array, max = Infinity): ZipEntry[] | null {
  const d = view(dir);
  const out: ZipEntry[] = [];
  let i = 0;
  while (out.length < max && i + 46 <= dir.length) {
    if (d.getUint32(i, true) !== CENTRAL) return out.length ? out : null;
    const nameLen = d.getUint16(i + 28, true);
    const extraLen = d.getUint16(i + 30, true);
    const noteLen = d.getUint16(i + 32, true);
    if (i + 46 + nameLen > dir.length) break; // cut off where the read stopped
    const extraAt = i + 46 + nameLen;
    out.push({
      name: nameOf(dir.subarray(i + 46, extraAt), d.getUint16(i + 8, true), dir.subarray(extraAt, Math.min(dir.length, extraAt + extraLen))),
      method: d.getUint16(i + 10, true),
      packed: d.getUint32(i + 20, true),
      size: d.getUint32(i + 24, true),
      offset: d.getUint32(i + 42, true),
    });
    i += 46 + nameLen + extraLen + noteLen;
  }
  return out;
}

export const isFolder = (e: ZipEntry) => e.name.endsWith('/');
