#!/usr/bin/env node
// Checks the page parts (ui/parts.json, backend/ui_bundle.py): code outside a part may use the part's names only
// through its entries, since the part is not loaded until a person goes to one of its pages.
//
//   npx --yes -p typescript@5.9.3 -c 'node scripts/check_ui_parts.cjs'     (scripts/check_ui_types.sh runs it)
//
// The files are the scripts index.html bundles, which share their top-level names. For every name a page part declares
// at its top level, each use from a file outside that part (the shell, or a part that does not list it in its deps)
// (its deps, theirs and so on, which load with it) must be one of the part's entries:
//   pages, calls   a call whose answer is not used (a statement, `void`, `await` as a statement, an arrow's body) or
//                  the function passed on as a value: the shell's stand-in loads the part and answers with a promise.
//                  An async function's answer may be used: it is a promise of the same answer either way;
//   idle           a call whose answer is not used: it does nothing until the part is loaded.
// `typeof NAME` is fine anywhere. `window.NAME?.()` is a call like any other: before its part is loaded it would do
// nothing at all, so it needs an entry too.
// Each entry must be a function declaration (or `window.NAME = function`) in its part: a stand-in is a function, and
// a `const` of the same name would refuse to load. A name a part uses as it runs (outside any function) must come from
// a part listed before it, since parts that load together run in ui/parts.json's order. It prints each use that breaks a rule, as file:line, and exits 1.
'use strict';
const fs = require('node:fs');
const path = require('node:path');

function typescript() {
  try { return require('typescript'); } catch { /* not installed here: the one npx put on PATH */ }
  for (const dir of (process.env.PATH || '').split(path.delimiter)) {
    const bin = path.join(dir, 'tsc');
    if (fs.existsSync(bin)) return require(path.join(path.dirname(fs.realpathSync(bin)), '..', 'lib', 'typescript.js'));
  }
  throw new Error('typescript is not installed: run this under npx -p typescript (see the top of this file)');
}

function main() {
  const ts = typescript();
  const root = path.join(__dirname, '..');
  const ui = process.argv.slice(2).find(a => !a.startsWith('--')) || path.join(root, 'ui');
  const html = fs.readFileSync(path.join(ui, 'index.html'), 'utf8');
  const region = /<!-- bundle:js:start -->\n([\s\S]*?)<!-- bundle:js:end -->/.exec(html);
  if (!region) throw new Error('index.html has no bundle:js region');
  const order = [...region[1].matchAll(/src="\/tico\/ui\/([^"]+)"/g)].map(m => m[1]);
  const raw = JSON.parse(fs.readFileSync(path.join(ui, 'parts.json'), 'utf8'));
  const parts = Object.fromEntries(Object.entries(raw).filter(([name]) => !name.startsWith('//')));
  const partOf = new Map();
  const entry = new Map();   // name -> {part, kind}
  for (const [name, spec] of Object.entries(parts)) {
    for (const f of spec.files || []) partOf.set(f, name);
    for (const kind of ['pages', 'calls', 'idle']) for (const e of spec[kind] || []) entry.set(e, {part: name, kind});
  }
  // A part's deps, transitively: their names are loaded before the part runs.
  const deps = name => { const seen = new Set(); const walk = n => { for (const d of parts[n]?.deps || []) if (!seen.has(d)) { seen.add(d); walk(d); } }; walk(name); return seen; };
  const depsOf = new Map(Object.keys(parts).map(n => [n, deps(n)]));
  const rank = new Map(Object.keys(parts).map((n, i) => [n, i]));

  const sources = new Map();
  const owner = new Map();   // top-level name -> file
  const declared = new Map();   // name -> 'function' | 'window' | 'other'
  const bind = (n, acc) => {
    if (ts.isIdentifier(n)) acc.push(n.text);
    else if (ts.isObjectBindingPattern(n) || ts.isArrayBindingPattern(n)) for (const e of n.elements) if (!ts.isOmittedExpression(e)) bind(e.name, acc);
  };
  for (const file of order) {
    const sf = ts.createSourceFile(file, fs.readFileSync(path.join(ui, file), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
    sources.set(file, sf);
    const add = (name, how) => { if (!owner.has(name) || how !== 'other') { owner.set(name, file); declared.set(name, how); } };
    for (const st of sf.statements) {
      const isAsync = n => !!n?.modifiers?.some(m => m.kind === ts.SyntaxKind.AsyncKeyword) || /^Promise</.test(n?.type?.getText() || '');
      if (ts.isFunctionDeclaration(st) && st.name && st.body) add(st.name.text, isAsync(st) ? 'async' : 'function');
      else if (ts.isClassDeclaration(st) && st.name) add(st.name.text, 'other');
      else if (ts.isEnumDeclaration(st)) add(st.name.text, 'other');
      else if (ts.isVariableStatement(st) && !st.modifiers?.some(m => m.kind === ts.SyntaxKind.DeclareKeyword)) {
        for (const d of st.declarationList.declarations) { const names = []; bind(d.name, names); for (const n of names) add(n, 'other'); }
      } else if (ts.isExpressionStatement(st) && ts.isBinaryExpression(st.expression) && st.expression.operatorToken.kind === ts.SyntaxKind.EqualsToken
        && ts.isPropertyAccessExpression(st.expression.left) && st.expression.left.expression.getText() === 'window') {
        add(st.expression.left.name.text, isAsync(st.expression.right) ? 'async' : 'window');
      }
    }
  }

  const problems = [];
  const uses = process.argv.includes('--uses') ? [] : null;
  for (const [name, {part}] of entry) {
    const file = owner.get(name);
    if (!file || partOf.get(file) !== part) problems.push(`ui/parts.json: ${part} lists ${name}, which none of its files declares`);
    else if (declared.get(name) === 'other') problems.push(`ui/${file}: ${name} is an entry of ${part}, so it must be a function declaration`);
  }
  // Not a use of a global: a property name, a declaration's own name, a type.
  const notUse = n => {
    const p = n.parent;
    if (!p) return true;
    if (ts.isPropertyAccessExpression(p) && p.name === n) return p.expression.getText() !== 'window';
    if ((ts.isPropertyAssignment(p) || ts.isMethodDeclaration(p) || ts.isPropertyDeclaration(p) || ts.isPropertySignature(p) || ts.isMethodSignature(p)
      || ts.isGetAccessor(p) || ts.isSetAccessor(p) || ts.isEnumMember(p) || ts.isParameter(p) || ts.isVariableDeclaration(p) || ts.isFunctionDeclaration(p)
      || ts.isFunctionExpression(p) || ts.isClassDeclaration(p) || ts.isClassExpression(p) || ts.isBindingElement(p)) && p.name === n) return true;
    if (ts.isBindingElement(p) && p.propertyName === n) return true;
    return ts.isQualifiedName(p) || ts.isTypeReferenceNode(p) || ts.isTypeQueryNode(p) || ts.isLabeledStatement(p) || ts.isBreakOrContinueStatement(p)
      || ts.isImportTypeNode(p) || ts.isExpressionWithTypeArguments(p) && ts.isHeritageClause(p.parent) && false;
  };
  for (const [file, sf] of sources) {
    const mine = partOf.get(file);
    // Names a function or block of this file declares itself shadow the globals: a rough scope, by name.
    const visit = (n, shadowed, inFn) => {
      if (ts.isInterfaceDeclaration(n) || ts.isTypeAliasDeclaration(n) || ts.isModuleDeclaration(n)) return;
      if (ts.isTypeNode(n) && !ts.isExpressionWithTypeArguments(n) && !ts.isTypeQueryNode(n)) return;
      let inner = shadowed;
      if (n !== sf && (ts.isFunctionLike(n) || ts.isBlock(n) || ts.isForStatement(n) || ts.isForOfStatement(n) || ts.isForInStatement(n) || ts.isCatchClause(n))) {
        const local = [];
        if (ts.isFunctionLike(n)) for (const p of n.parameters || []) bind(p.name, local);
        if (ts.isCatchClause(n) && n.variableDeclaration) bind(n.variableDeclaration.name, local);
        const body = ts.isBlock(n) ? n.statements : ts.isFunctionLike(n) && n.body && ts.isBlock(n.body) ? n.body.statements : [];
        for (const st of body) {
          if (ts.isVariableStatement(st)) for (const d of st.declarationList.declarations) bind(d.name, local);
          else if ((ts.isFunctionDeclaration(st) || ts.isClassDeclaration(st)) && st.name) local.push(st.name.text);
        }
        if ((ts.isForStatement(n) || ts.isForOfStatement(n) || ts.isForInStatement(n)) && n.initializer && ts.isVariableDeclarationList(n.initializer)) {
          for (const d of n.initializer.declarations) bind(d.name, local);
        }
        if (local.length) inner = new Set([...shadowed, ...local]);
      }
      if (ts.isIdentifier(n) && !notUse(n) && !shadowed.has(n.text)) check(n, inFn);
      const nested = inFn || (n !== sf && (ts.isFunctionLike(n) || ts.isClassLike(n)) && !(n.parent && ts.isCallExpression(n.parent) && n.parent.expression === n));
      ts.forEachChild(n, c => visit(c, inner, nested));
    };
    const check = (n, inFn) => {
      const name = n.text, home = owner.get(name);
      const theirs = home && partOf.get(home);
      const at = `ui/${file}:${sf.getLineAndCharacterOfPosition(n.getStart()).line + 1}`;
      if (!theirs || theirs === mine) return;
      if (mine && depsOf.get(mine).has(theirs)) {
        // Loaded with this part, but a name used as the part runs must come from a part that runs before it.
        if (!inFn && rank.get(theirs) > rank.get(mine)) problems.push(`${at}: ${name} (${theirs} part) is used as the ${mine} part runs, but ${theirs} runs after it: list ${theirs} first in ui/parts.json`);
        return;
      }
      // `typeof NAME` only asks whether it is there (a stand-in is a function).
      const around = ts.isPropertyAccessExpression(n.parent) && n.parent.name === n ? n.parent : n;
      if (ts.isTypeOfExpression(around.parent)) return;
      const e = entry.get(name);
      if (uses) {
        let u = ts.isPropertyAccessExpression(n.parent) && n.parent.name === n ? n.parent : n;
        while (ts.isNonNullExpression(u.parent) || ts.isParenthesizedExpression(u.parent)) u = u.parent;
        let how = 'value';
        if (ts.isCallExpression(u.parent) && u.parent.expression === u) {
          let a = u.parent;
          while (ts.isAwaitExpression(a.parent) || ts.isVoidExpression(a.parent) || ts.isParenthesizedExpression(a.parent)) a = a.parent;
          how = ts.isExpressionStatement(a.parent) || (ts.isArrowFunction(a.parent) && a.parent.body === a) ? 'call' : 'call-used';
        }
        uses.push({file, line: sf.getLineAndCharacterOfPosition(n.getStart()).line + 1, name, part: theirs, decl: declared.get(name), how, entry: e?.kind || ''});
      }
      if (!e) { problems.push(`${at}: ${name} is in the ${theirs} part, which is not loaded here: move it out of the part, or make it an entry`); return; }
      let used = ts.isPropertyAccessExpression(n.parent) && n.parent.name === n ? n.parent : n;
      while (ts.isNonNullExpression(used.parent) || ts.isParenthesizedExpression(used.parent)) used = used.parent;
      const call = used.parent;
      if (ts.isCallExpression(call) && call.expression === used) {
        let answer = call;
        while (ts.isAwaitExpression(answer.parent) || ts.isVoidExpression(answer.parent) || ts.isParenthesizedExpression(answer.parent)) answer = answer.parent;
        const unused = ts.isExpressionStatement(answer.parent) || (ts.isArrowFunction(answer.parent) && answer.parent.body === answer && e.kind !== 'idle');
        // An async function answers with a promise anyway: its stand-in's promise of the same answer is used alike.
        if (!unused && declared.get(name) !== 'async') problems.push(`${at}: ${name} (${theirs} part) answers with a promise until its part is loaded: its answer cannot be used here`);
        return;
      }
      if (e.kind === 'idle') problems.push(`${at}: ${name} (${theirs} part) is idle until its part is loaded: only call it`);
    };
    visit(sf, new Set(), false);
  }
  if (uses) { console.log(JSON.stringify(uses)); return; }
  for (const p of problems) console.log(p);
  if (problems.length) {
    console.log(`${problems.length} use(s) of a page part's names from outside it (ui/parts.json, scripts/check_ui_parts.cjs).`);
    process.exit(1);
  }
  console.log(`ui parts: ${Object.keys(parts).length} parts, ${partOf.size} files, ${entry.size} entries: every use from outside goes through an entry.`);
}

main();
