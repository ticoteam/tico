/* Signing a PDF (backend/file_signings.py): the person's saved signature, their marks written into the PDF here with
   pdf-lib, and the signed bytes sent as the file's next version with the record of the signing. */

/** What the person adds to a page. Place and size are fractions of the page as it is shown (its rotation applied):
    x and y from its top left, w and h of its width and height. Text and a date are one line, as tall as `h`. */
export interface Mark {
  id: string;
  kind: 'signature' | 'text' | 'date';
  page: number;
  x: number;
  y: number;
  w: number;
  h: number;
  text: string;
}

/** A line's letters are this share of its box, and its baseline this far down it, here and in the PDF alike. */
export const TEXT_FILL = 0.7;
export const BASELINE = 0.74;

export interface Signing {
  id: string;
  file_id: string;
  task_id: string | null;
  signer: string;
  signer_name: string;
  before_version: number;
  after_version: number;
  created_at: string;
}

/** A version a signing made, as the file's versions say it. */
export interface Signed {
  by: string;
  by_name: string;
  at: string;
}

const stampFormat = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
const stampYearFormat = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' });

/** "Signed by Ana Example · Nov 20, 5:40 AM" (the year only when it is not this one). */
export function signedLine(s: Signed, now: Date = new Date()): string {
  const at = new Date(s.at);
  const when = (at.getFullYear() === now.getFullYear() ? stampFormat : stampYearFormat).format(at);
  return `Signed by ${s.by_name} · ${when}`;
}

/** Today, as a date mark first says it: "November 20, 2026". */
export const today = () => new Intl.DateTimeFormat('en-US', { month: 'long', day: 'numeric', year: 'numeric' }).format(new Date());

/** Characters the PDF's built-in Helvetica can write; anything else (an emoji, another script) is left out. */
const WIN_ANSI = /[^\x20-\x7E\xA0-\xFF‘’“”–—…•€]/g;

type Turn = 0 | 90 | 180 | 270;

/** A point on a page as it is shown (u across, v down, as fractions from its top left) in the page's own space, for a
    page whose /Rotate turns it clockwise by `turn` within its crop box. */
export function pagePoint(box: { x: number; y: number; width: number; height: number }, turn: Turn, u: number, v: number): [number, number] {
  if (turn === 90) return [box.x + v * box.width, box.y + u * box.height];
  if (turn === 180) return [box.x + (1 - u) * box.width, box.y + v * box.height];
  if (turn === 270) return [box.x + (1 - v) * box.width, box.y + (1 - u) * box.height];
  return [box.x + u * box.width, box.y + (1 - v) * box.height];
}

/** The PDF with the marks written into its pages for good. pdf-lib loads only here, when the person saves. */
export async function burnMarks(bytes: Uint8Array, marks: Mark[], signature: Uint8Array | null): Promise<Uint8Array> {
  const { PDFDocument, StandardFonts, degrees, rgb } = await import('pdf-lib');
  const pdf = await PDFDocument.load(bytes);
  const pages = pdf.getPages();
  const png = signature && marks.some((m) => m.kind === 'signature') ? await pdf.embedPng(signature) : null;
  const font = marks.some((m) => m.kind !== 'signature') ? await pdf.embedFont(StandardFonts.Helvetica) : null;
  for (const m of marks) {
    const page = pages[m.page - 1];
    if (!page) continue;
    const box = page.getCropBox();
    const turn = (((page.getRotation().angle % 360) + 360) % 360) as Turn;
    const sideways = turn === 90 || turn === 270;
    const at = (u: number, v: number) => pagePoint(box, turn, u, v);
    const shownWidth = sideways ? box.height : box.width;
    const shownHeight = sideways ? box.width : box.height;
    const rotate = degrees(turn);
    if (m.kind === 'signature') {
      if (!png) continue;
      const [x, y] = at(m.x, m.y + m.h);
      page.drawImage(png, { x, y, width: m.w * shownWidth, height: m.h * shownHeight, rotate });
    } else if (font) {
      const text = m.text.replace(WIN_ANSI, '').trim();
      if (!text) continue;
      const [x, y] = at(m.x, m.y + m.h * BASELINE);
      page.drawText(text, { x, y, size: m.h * shownHeight * TEXT_FILL, font, color: rgb(0, 0, 0), rotate });
    }
  }
  return pdf.save();
}
