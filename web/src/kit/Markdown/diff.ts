/* A unified diff read line by line for a ```diff block. Inside a hunk whose header gives its line counts, every line is
   the hunk's until those run out, so a removed line that reads "--- x" stays a removed line; outside one, the file
   headers (`diff --git`, `---`, `+++`, `index …`) are headers. Anything else (prose an agent wrote between hunks) is
   shown as written. */

export type DiffKind = 'add' | 'del' | 'same' | 'hunk' | 'file' | 'note';

export interface DiffLine {
  kind: DiffKind;
  /** The gutter: +, − or nothing. */
  sign: string;
  text: string;
}

const HUNK = /^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@/;
const FILE = /^(diff --git |diff -|index [0-9a-f]+\.\.|--- |\+\+\+ |new file mode |deleted file mode |old mode |new mode |similarity index |dissimilarity index |rename (from|to) |copy (from|to) |Binary files )/;

export function parseDiff(text: string): DiffLine[] {
  const out: DiffLine[] = [];
  let olds = 0;
  let news = 0;
  for (const line of text.split('\n')) {
    const hunk = HUNK.exec(line);
    if (hunk || line.startsWith('@@')) {
      // "@@ -3 +3 @@" holds one line on each side; a bare "@@" (hand-written) holds no count
      olds = hunk ? Number(hunk[1] ?? 1) : 0;
      news = hunk ? Number(hunk[2] ?? 1) : 0;
      out.push({ kind: 'hunk', sign: '', text: line });
      continue;
    }
    const counted = olds > 0 || news > 0;
    if (!counted && FILE.test(line)) {
      out.push({ kind: 'file', sign: '', text: line });
      continue;
    }
    const mark = line[0];
    if (mark === '\\') out.push({ kind: 'note', sign: '', text: line });
    else if (mark === '+') {
      news = Math.max(0, news - 1);
      out.push({ kind: 'add', sign: '+', text: line.slice(1) });
    } else if (mark === '-') {
      olds = Math.max(0, olds - 1);
      out.push({ kind: 'del', sign: '−', text: line.slice(1) });
    } else {
      // a context line; an empty one is a context line whose space was trimmed
      olds = Math.max(0, olds - 1);
      news = Math.max(0, news - 1);
      out.push({ kind: 'same', sign: '', text: mark === ' ' ? line.slice(1) : line });
    }
  }
  return out;
}
