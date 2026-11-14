#!/usr/bin/env node
// Compiles each TypeScript file under ui/ to JavaScript at the same path under build/ui/ (ui/app/a.ts ->
// build/ui/app/a.js), with esbuild's per-file transform: types are stripped, nothing is bundled, and the file stays a
// classic script, so it runs where index.html lists it as the .js file it replaced did. Beside each output is its source
// map (a.js.map, named by the output's last line), so a stack trace from the separately served file points at a.ts.
// build/ui/manifest.json records the sha256 of each source, output and map; the server (backend/ui_build.py) refuses a
// .ts file the manifest does not match.
// Each file index.html bundles (its bundle:js and bundle:css regions) is also minified on its own, to
// build/ui/min/<path> (a .ts file as .js), with a map to its source beside it (a.js.map, a.css.map), recorded under the
// manifest's "min". Names at a file's top level are kept, since the UI's classic scripts share them. The bundle
// (backend/ui_bundle.py) uses a file's minified copy while the manifest matches its source, and the file as it is
// otherwise: a .js or .css edited after a build is served whole until the next build, never as old code.
// Usage: node scripts/build-ui.cjs [--watch]   (npm run build:ui, npm run watch:ui)
'use strict';
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const esbuild = require('esbuild');

const ROOT = path.join(__dirname, '..');
const UI = path.join(ROOT, 'ui');
const OUT = path.join(ROOT, 'build', 'ui');
const MANIFEST = path.join(OUT, 'manifest.json');
// The same folders backend/ui_build.py SKIP leaves out.
const SKIP = new Set(['node_modules', 'vendor']);
const MIN = path.join(OUT, 'min');
// The files the page bundles: the same regions and URL prefix as backend/ui_bundle.py REGION and PREFIX.
const REGION = /[ \t]*<!-- bundle:(js|css):start -->\n([\s\S]*?)[ \t]*<!-- bundle:\1:end -->/g;
const PREFIX = '/tico/ui/';

const sha256 = data => crypto.createHash('sha256').update(data).digest('hex');

// Every .ts file under ui/ (declaration files hold types only), as a path relative to ui/, sorted.
function sources(dir = UI, rel = '') {
  const found = [];
  for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
    const name = rel ? `${rel}/${entry.name}` : entry.name;
    if (entry.isDirectory()) { if (!SKIP.has(entry.name)) found.push(...sources(path.join(dir, entry.name), name)); }
    else if (entry.isFile() && entry.name.endsWith('.ts') && !entry.name.endsWith('.d.ts')) found.push(name);
  }
  return found.sort();
}

function writeAtomic(file, data) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  const part = `${file}.${process.pid}.part`;
  fs.writeFileSync(part, data);
  fs.renameSync(part, file);
}

// The files index.html bundles, as paths relative to ui/, in its order (js first, then css, as listed).
function bundled() {
  let html;
  try { html = fs.readFileSync(path.join(UI, 'index.html'), 'utf8'); } catch { return []; }
  const found = [];
  for (const region of html.matchAll(REGION)) {
    for (const ref of region[2].matchAll(/(?:src|href)="([^"]+)"/g)) {
      const url = ref[1];
      if (!url.startsWith(PREFIX) || url.split('/').includes('..')) continue;  // the server refuses these itself
      const name = url.slice(PREFIX.length);
      if (fs.existsSync(path.join(UI, name))) found.push(name);
    }
  }
  return [...new Set(found)];
}

// Each bundled file minified on its own, with its map: {name: {source, output, map}} for the manifest.
async function minify(written) {
  const out = {};
  await Promise.all(bundled().map(async name => {
    const text = fs.readFileSync(path.join(UI, name));
    const css = name.endsWith('.css');
    const loader = css ? 'css' : name.endsWith('.ts') ? 'ts' : 'js';
    // The map names the source by its URL from the bundle's map (/tico/ui/app.bundle.js.map), with its text.
    const result = await esbuild.transform(text.toString('utf8'), {
      loader, minify: true, target: 'esnext', charset: 'utf8', logLevel: 'warning',
      sourcemap: 'external', sourcefile: name, sourcesContent: true, legalComments: 'none',
    });
    const file = path.join(MIN, css ? name : name.replace(/\.ts$/, '.js'));
    const map = JSON.parse(result.map);
    map.sources = [name];
    const code = Buffer.from(result.code), mapText = Buffer.from(JSON.stringify(map));
    writeAtomic(file, code);
    writeAtomic(`${file}.map`, mapText);
    written.add(file).add(`${file}.map`);
    out[name] = {source: sha256(text), output: sha256(code), map: sha256(mapText)};
  }));
  return Object.fromEntries(Object.entries(out).sort());
}

async function build() {
  const files = sources();
  const texts = new Map(files.map(name => [name, fs.readFileSync(path.join(UI, name))]));
  const result = files.length ? await esbuild.build({
    entryPoints: files.map(name => path.join(UI, name)), outdir: OUT, outbase: UI, write: false,
    bundle: false, target: 'esnext', charset: 'utf8', logLevel: 'warning', sourcemap: 'linked',
  }) : {outputFiles: []};
  const manifest = {files: {}};
  const written = new Set([MANIFEST]);
  const entry = name => manifest.files[name] ??= {source: sha256(texts.get(name))};
  for (const out of result.outputFiles) {
    const rel = path.relative(OUT, out.path).split(path.sep).join('/');
    let contents = out.contents;
    if (rel.endsWith('.js.map')) {
      // esbuild names the source by its path from build/ui; the browser resolves it from the map's URL
      // (/tico/ui/app/a.js.map), where the source is a.ts beside it. The map carries the source text too.
      const name = rel.replace(/\.js\.map$/, '.ts');
      const map = JSON.parse(Buffer.from(contents).toString('utf8'));
      map.sources = [path.posix.basename(name)];
      contents = Buffer.from(JSON.stringify(map));
      entry(name).map = sha256(contents);
    } else {
      entry(rel.replace(/\.js$/, '.ts')).output = sha256(contents);
    }
    writeAtomic(out.path, contents);
    written.add(out.path);
  }
  manifest.min = await minify(written);
  // The output of a source that is gone would otherwise stay; nothing names it, but nothing should find it either.
  const prune = dir => {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) { prune(file); if (!fs.readdirSync(file).length) fs.rmdirSync(file); }
      else if (!written.has(file)) fs.rmSync(file);
    }
  };
  prune(OUT);
  // Written last: until it names a file's new hashes, the server treats that file as not yet compiled.
  writeAtomic(MANIFEST, JSON.stringify(manifest, null, 2) + '\n');
  return files.length;
}

async function main() {
  const started = Date.now();
  const count = await build();
  console.log(`ui build: ${count} TypeScript file${count === 1 ? '' : 's'} -> build/ui, bundled files minified (${Date.now() - started} ms)`);
  if (!process.argv.includes('--watch')) return;
  // Every change rebuilds everything: a new file is picked up too, and the whole set takes milliseconds.
  let timer = null;
  fs.watch(UI, {recursive: true}, (_, name) => {
    if (!name || !String(name).endsWith('.ts')) return;
    clearTimeout(timer);
    timer = setTimeout(() => build().then(
      n => console.log(`ui build: ${n} file${n === 1 ? '' : 's'} at ${new Date().toLocaleTimeString()}`),
      error => console.error(`ui build failed: ${error.message}`)), 50);
  });
  console.log('ui build: watching ui/ for .ts changes');
}

main().catch(error => { console.error(`ui build failed: ${error.message}`); process.exit(1); });
