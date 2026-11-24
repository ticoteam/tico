import type { MarkedOptions, Tokens } from 'marked';

/* Display math in a chat: a `$$ … $$` block on lines of its own, or `$$ … $$` filling one line. Never inline `$`: a
   message about money ("$5 and $10", "$$5 to $$10") stays text. An unclosed block stays text too. */

/** What KaTeX is given at most; longer is shown as the code it is. */
export const TEX_MAX = 4000;

const BLOCK = new RegExp(String.raw`^ {0,3}\$\$[ \t]*\n([\s\S]{1,${TEX_MAX}}?)\n {0,3}\$\$[ \t]*(?:\n+|$)`);
const LINE = /^ {0,3}\$\$(?!\$)((?:(?!\$\$)[^\n])+)\$\$[ \t]*(?:\n+|$)/;

export interface MathToken extends Tokens.Generic {
  type: 'math';
  raw: string;
  text: string;
}

export function mathBlock(src: string): MathToken | undefined {
  const m = BLOCK.exec(src) ?? LINE.exec(src);
  const text = m?.[1]?.trim();
  return m && text ? { type: 'math', raw: m[0], text } : undefined;
}

/** Where a paragraph stops for a block below it: a line that starts one that closes. Only the first few lines that
    start with `$$` are tried, so a long message of them costs little. */
export function mathStart(src: string): number | undefined {
  const at = /\n {0,3}\$\$/g;
  for (let tries = 0, m = at.exec(src); m && tries < 20; tries++, m = at.exec(src)) {
    const line = m.index + 1;
    if (mathBlock(src.slice(line))) return line;
  }
  return undefined;
}

/** The marked options part that reads it (lexer options take the extension lists as marked.use builds them). */
export const MATH: NonNullable<MarkedOptions['extensions']> = { renderers: {}, childTokens: {}, block: [mathBlock], startBlock: [mathStart] };
