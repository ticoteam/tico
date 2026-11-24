/* What a file is, from its type and else its name: the one map a chat's cards, the file viewer and the actions that
   open a file all read, so a picture in one is a picture in all. A kind with no face of its own yet is still named
   here, so the viewer maps every kind on purpose. */

export type FileKind =
  | 'image'
  | 'video'
  | 'audio'
  | 'pdf'
  | 'html'
  | 'markdown'
  | 'csv'
  | 'tsv'
  | 'text'
  | 'diff'
  | 'ics'
  | 'vcf'
  | 'eml'
  | 'zip'
  | 'docx'
  | 'xlsx'
  | 'office'
  | 'none';

const DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
const OFFICE = /^application\/(msword|rtf|vnd\.(openxmlformats-officedocument|ms-excel|ms-powerpoint|ms-word|oasis\.opendocument))/;
const TEXT = /^(text\/|application\/(json|xml|csv|x-yaml|yaml|javascript))/;

/** Types that say exactly what a file is, whatever its name. */
const BY_TYPE: Record<string, FileKind> = {
  'application/pdf': 'pdf',
  'text/html': 'html',
  'text/markdown': 'markdown',
  'text/x-markdown': 'markdown',
  'text/csv': 'csv',
  'application/csv': 'csv',
  'text/tab-separated-values': 'tsv',
  'text/x-diff': 'diff',
  'text/x-patch': 'diff',
  'text/calendar': 'ics',
  'text/vcard': 'vcf',
  'text/x-vcard': 'vcf',
  'message/rfc822': 'eml',
  'application/zip': 'zip',
  'application/x-zip-compressed': 'zip',
  [DOCX]: 'docx',
  [XLSX]: 'xlsx',
};

/** By its name, when the type says nothing more (`application/octet-stream`, or `text/plain` for `notes.md`). */
const BY_NAME: [RegExp, FileKind][] = [
  [/\.(png|jpe?g|gif|webp|avif|svg)$/i, 'image'],
  [/\.(mp4|webm|mov|m4v)$/i, 'video'],
  [/\.(mp3|m4a|wav|ogg|oga|opus|aac|flac)$/i, 'audio'],
  [/\.pdf$/i, 'pdf'],
  [/\.html?$/i, 'html'],
  [/\.(md|markdown)$/i, 'markdown'],
  [/\.csv$/i, 'csv'],
  [/\.tsv$/i, 'tsv'],
  [/\.(diff|patch)$/i, 'diff'],
  [/\.ics$/i, 'ics'],
  [/\.vcf$/i, 'vcf'],
  [/\.eml$/i, 'eml'],
  [/\.zip$/i, 'zip'],
  [/\.docx$/i, 'docx'],
  [/\.xlsx$/i, 'xlsx'],
  [/\.(doc|xls|pptx?|od[tsp]|rtf)$/i, 'office'],
  [/\.(txt|log|json|ya?ml|xml)$/i, 'text'],
];

/** `libraryKind` is the server's word for a Files entry (`image`, `page`), when there is one. */
export function fileKind(mime: string | null | undefined, name?: string | null, libraryKind?: string | null): FileKind {
  const m = (mime ?? '').toLowerCase().split(';')[0]?.trim() ?? '';
  if (m.startsWith('image/') || libraryKind === 'image') return 'image';
  if (m.startsWith('video/')) return 'video';
  if (m.startsWith('audio/')) return 'audio';
  if (libraryKind === 'page') return 'html';
  const typed = BY_TYPE[m];
  if (typed) return typed;
  if (m.includes('csv')) return 'csv';
  if (OFFICE.test(m)) return 'office';
  const named = BY_NAME.find(([re]) => re.test(name ?? ''))?.[1];
  return named ?? (TEXT.test(m) ? 'text' : 'none');
}
