// Words people read: JSX text, aria-label/title/placeholder/alt, and the strings in a copy.ts. Fails on banned words,
// shouting, "!" endings and buttons longer than two words (design-fidelity.md 3.3).
import { globSync, readFileSync } from 'node:fs';
import ts from 'typescript';

const BANNED = [
  /\bbots?\b/i, /\bJev\b/, /\bkeeper\b/i, /\bRecurring\b/, /\bExtra high\b/i, /\bShow more\b/i, /\bLearn more\b/i,
  /\bOops\b/i, /\bsuccessfully\b/i, /\bplease\b/i, /\blorem\b/i, /\bthe Tico team\b/i, /\bclick here\b/i,
  /\bthe assistant\b/i, / · /,
];
const CAPS_OK = new Set(['AM', 'PM', 'QA', 'ZIP', 'CSV', 'PDF', 'API', 'URL', 'SSO', 'MCP', 'GPT', 'CPU', 'SMS']);
const ATTRS = new Set(['aria-label', 'title', 'placeholder', 'alt']);

let bad = 0;
function check(file: string, sf: ts.SourceFile, node: ts.Node, text: string, isButton = false) {
  const s = text.replace(/\s+/g, ' ').trim();
  if (!s) return;
  const where = `${file}:${sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1}`;
  const fail = (why: string) => {
    bad++;
    console.log(`${where}: ${why}: "${s}"`);
  };
  for (const re of BANNED) if (re.test(s)) fail(`banned ${re}`);
  for (const w of s.match(/\b[A-Z]{4,}\b/g) ?? []) if (!CAPS_OK.has(w)) fail(`all caps "${w}"`);
  if (s.endsWith('!')) fail('ends in "!"');
  if (isButton && s.split(' ').length > 2) fail('button label over two words');
}

const files = [...globSync('src/**/*.tsx'), ...globSync('src/**/copy.ts')].filter((f) => !/\.test\.tsx?$/.test(f));
for (const file of files) {
  const sf = ts.createSourceFile(file, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const isCopy = file.endsWith('copy.ts');
  const visit = (node: ts.Node) => {
    if (ts.isJsxText(node)) {
      const parent = node.parent;
      const tag = ts.isJsxElement(parent) ? parent.openingElement.tagName.getText(sf) : '';
      check(file, sf, node, node.text, tag === 'Button');
    } else if (ts.isJsxAttribute(node) && ATTRS.has(node.name.getText(sf)) && node.initializer) {
      const init = node.initializer;
      const lit = ts.isStringLiteral(init) ? init : ts.isJsxExpression(init) && init.expression && ts.isStringLiteralLike(init.expression) ? init.expression : null;
      if (lit) check(file, sf, lit, lit.text);
    } else if (isCopy && ts.isStringLiteralLike(node) && !ts.isImportDeclaration(node.parent)) {
      check(file, sf, node, node.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
}
process.exit(bad ? 1 : 0);
