import { createCssVariablesTheme, createHighlighterCore, type HighlighterCore, type LanguageRegistration } from 'shiki/core';
import { createJavaScriptRegexEngine } from 'shiki/engine/javascript';

/* Code colouring for a chat's code blocks, loaded the first time a settled block in view asks (kit/Markdown/code.tsx).
   Shiki's core with its JavaScript regex engine (no WebAssembly), one grammar per language fetched when first used,
   and one theme whose colours are CSS variables: the token kinds take the app's own greys in light and dark
   (Markdown.css, `.hl-*`). */

type Grammar = () => Promise<{ default: LanguageRegistration[] }>;

const GRAMMARS: Record<string, Grammar> = {
  typescript: () => import('shiki/langs/typescript.mjs'),
  tsx: () => import('shiki/langs/tsx.mjs'),
  javascript: () => import('shiki/langs/javascript.mjs'),
  jsx: () => import('shiki/langs/jsx.mjs'),
  json: () => import('shiki/langs/json.mjs'),
  python: () => import('shiki/langs/python.mjs'),
  shellscript: () => import('shiki/langs/shellscript.mjs'),
  sql: () => import('shiki/langs/sql.mjs'),
  yaml: () => import('shiki/langs/yaml.mjs'),
  html: () => import('shiki/langs/html.mjs'),
  css: () => import('shiki/langs/css.mjs'),
  go: () => import('shiki/langs/go.mjs'),
  rust: () => import('shiki/langs/rust.mjs'),
};

const NAMES: Record<string, string> = {
  ts: 'typescript', typescript: 'typescript', mts: 'typescript', cts: 'typescript',
  tsx: 'tsx',
  js: 'javascript', javascript: 'javascript', mjs: 'javascript', cjs: 'javascript',
  jsx: 'jsx',
  json: 'json', jsonc: 'json', json5: 'json',
  py: 'python', python: 'python',
  sh: 'shellscript', bash: 'shellscript', shell: 'shellscript', zsh: 'shellscript', shellscript: 'shellscript',
  sql: 'sql',
  yaml: 'yaml', yml: 'yaml',
  html: 'html', htm: 'html', xml: 'html', svg: 'html',
  css: 'css',
  go: 'go', golang: 'go',
  rust: 'rust', rs: 'rust',
};

/** Past this a block shows as plain text: colouring it would hold the page up. */
const MAX_CHARS = 100_000;

export interface Piece {
  text: string;
  /** The token's kind (`keyword`, `string`, …), or none for plain text. */
  kind?: string;
}

const THEME = 'tico';
let core: Promise<HighlighterCore> | undefined;
const loading = new Map<string, Promise<void>>();

function load(h: HighlighterCore, lang: string) {
  let p = loading.get(lang);
  if (!p) {
    p = GRAMMARS[lang]!().then((m) => h.loadLanguage(...m.default));
    p.catch(() => loading.delete(lang)); // a chunk that failed to arrive is asked again next time
    loading.set(lang, p);
  }
  return p;
}

/** The block's lines as coloured pieces, or null for a language it has no grammar for. */
export async function highlight(code: string, lang: string): Promise<Piece[][] | null> {
  const id = NAMES[lang.toLowerCase()];
  if (!id || code.length > MAX_CHARS) return null;
  core ??= createHighlighterCore({
    themes: [createCssVariablesTheme({ name: THEME, variablePrefix: '--hl-', fontStyle: false })],
    langs: [],
    engine: createJavaScriptRegexEngine(),
  });
  const h = await core;
  await load(h, id);
  return h.codeToTokensBase(code, { lang: id, theme: THEME }).map((line) =>
    line.map((t) => ({ text: t.content, kind: /--hl-token-([a-z-]+)/.exec(t.color ?? '')?.[1] })),
  );
}
