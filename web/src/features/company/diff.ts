/* What changed between two versions, a line at a time: the lines both keep, the lines added, the lines taken out, in
   reading order (the longest common run of lines kept; what is left is added or taken out). */

export type Mark = '=' | '+' | '-';

export function lineDiff(was: readonly string[], now: readonly string[]): [Mark, string][] {
  const m = was.length;
  const n = now.length;
  // keep[i][j]: how many lines was[i..] and now[j..] keep in common
  const keep = Array.from({ length: m + 1 }, () => new Array<number>(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      keep[i]![j] = was[i] === now[j] ? keep[i + 1]![j + 1]! + 1 : Math.max(keep[i + 1]![j]!, keep[i]![j + 1]!);
    }
  }
  const out: [Mark, string][] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (was[i] === now[j]) {
      out.push(['=', now[j]!]);
      i++;
      j++;
    } else if (keep[i + 1]![j]! >= keep[i]![j + 1]!) out.push(['-', was[i++]!]);
    else out.push(['+', now[j++]!]);
  }
  while (i < m) out.push(['-', was[i++]!]);
  while (j < n) out.push(['+', now[j++]!]);
  return out;
}

/** Text in paragraphs, as written: one per blank line, a heading's marks dropped. */
export const paragraphs = (text: unknown): string[] =>
  typeof text === 'string'
    ? text
        .split(/\n\s*\n/)
        .map((p) => p.trim().replace(/^#+\s*/, ''))
        .filter(Boolean)
    : [];
