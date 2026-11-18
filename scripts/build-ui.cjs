#!/usr/bin/env node
// Builds the web UI into build/ui/, which is all the server serves of it (backend/ui_bundle_routes.py):
//   app/a.js, ...       each TypeScript file under ui/ compiled on its own at the same path (ui/app/a.ts ->
//                       build/ui/app/a.js), types stripped, with its source map beside it. The service worker is served from
//                       here, and the unit tests read these files.
//   bundle/             the bundle: the files index.html lists between its bundle markers, each minified, in that order,
//                       cut between files into parts of at most PART_BYTES (WebKit's disk cache keeps nothing larger than
//                       an eighth of its size); the page parts (ui/parts.json) cut out as parts of their own; theme.ts and
//                       marked, which the page loads on their own. Every file is named by its content
//                       (app.bundle.<hash>.js), so the server keeps it for a year, and has an index source map (each
//                       file's own map at the line the file starts on) and a gzip copy beside it.
//   index.html          ui/index.html with each bundle region replaced by the parts' tags, and the page parts' list (a
//                       JSON block, #ui-parts, which ui/app/parts.ts reads) holding `build`, the id of this build.
//   manifest.json       the build id and the sha256 of every file the build read, written last. backend/ui_build.py
//                       compares it with ui/ to tell a stale build.
// The scripts stay classic scripts sharing the page's globals: nothing is bundled as modules, and the load order is
// index.html's.
// Usage: node scripts/build-ui.cjs [--watch]   (npm run build:ui, npm run watch:ui)
'use strict';
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const esbuild = require('esbuild');

const ROOT = path.join(__dirname, '..');
const UI = path.join(ROOT, 'ui');
const OUT = path.join(ROOT, 'build', 'ui');
const BUNDLE = path.join(OUT, 'bundle');
const MANIFEST = path.join(OUT, 'manifest.json');
const SKIP = new Set(['node_modules', 'vendor']);
const PREFIX = '/tico/ui/';
// Every bundle file's URL starts with this (backend/ui_bundle_routes.py serves them).
const NAME = 'app.bundle.';
// The largest part (decoded): a WebKit cache of 4 MB or more keeps it. Files are cut between, never inside.
const PART_BYTES = 512 * 1024;
const REGION = /[ \t]*<!-- bundle:(js|css):start -->\n([\s\S]*?)[ \t]*<!-- bundle:\1:end -->\n?/g;
const REF = /(?:src|href)="([^"]+)"/g;
// The scripts the page loads on their own, by the URL index.html names them with: the file each one is built from.
const STANDALONE = {'/tico/ui/theme.ts': ['theme', 'theme.ts'], '/vendor/marked.min.js': ['marked', 'vendor/marked.min.js']};
const STANDALONE_STEMS = Object.fromEntries(Object.values(STANDALONE).map(([stem]) => [stem, true]));
// Where the boot timing starts counting the bundle's own run (ui/app/boot-timing.ts).
const BUNDLE_MARK = "try { performance.mark('tico:bundle-start'); } catch { /* no User Timing */ }\n";
const PART_NAME = /^[a-z][a-z0-9-]*$/;
const ENTRY_NAME = /^[A-Za-z_$][\w$]*$/;
const ENTRY_KINDS = ['pages', 'calls', 'idle'];

class BuildError extends Error {}
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

// The files index.html bundles, as paths relative to ui/, in its order: {js: [...], css: [...]}.
function listed(html) {
  const files = {js: [], css: []};
  for (const region of html.matchAll(REGION)) {
    for (const ref of region[2].matchAll(REF)) {
      const url = ref[1];
      if (!url.startsWith(PREFIX) || url.split('/').includes('..')) throw new BuildError(`${url}: only files under ${PREFIX} can be bundled`);
      const name = url.slice(PREFIX.length);
      if (!fs.existsSync(path.join(UI, name))) throw new BuildError(`${url}: no such file`);
      files[region[1]].push(name);
    }
  }
  if (!files.js.length || !files.css.length) throw new BuildError('index.html has no bundle regions');
  return files;
}

// ui/parts.json, checked against the scripts index.html bundles: {part: {files, deps, events, pages, calls, idle, load}}.
// `load` is what a part needs before its entries run: its deps, theirs and so on (two parts may need each other), in the
// order ui/parts.json lists the parts, itself included. Scripts run in that order (ui/app/parts.ts).
function readParts(jsNames) {
  const file = path.join(UI, 'parts.json');
  if (!fs.existsSync(file)) return {parts: {}, owner: {}};
  let raw;
  try { raw = JSON.parse(fs.readFileSync(file, 'utf8')); } catch (error) { throw new BuildError(`ui/parts.json: ${error.message}`); }
  const parts = {}, owner = {}, entries = {};
  for (const [name, spec] of Object.entries(raw)) {
    if (name.startsWith('//')) continue;  // a comment
    if (!PART_NAME.test(name) || name in STANDALONE_STEMS || /^[0-9a-f]{16}$/.test(name)) throw new BuildError(`ui/parts.json: ${JSON.stringify(name)} is not a part name (a-z, 0-9, -)`);
    const part = Object.fromEntries(['files', 'deps', 'events', ...ENTRY_KINDS].map(k => [k, [...(spec[k] || [])]]));
    if (!part.events.every(e => typeof e === 'string' && e.trim())) throw new BuildError(`ui/parts.json: ${name}: events are CSS selectors`);
    for (const f of part.files) {
      if (!jsNames.includes(f)) throw new BuildError(`ui/parts.json: ${f} is not a script index.html bundles`);
      if (f in owner) throw new BuildError(`ui/parts.json: ${f} is in two parts (${owner[f]}, ${name})`);
      owner[f] = name;
    }
    for (const kind of ENTRY_KINDS) for (const e of part[kind]) {
      if (typeof e !== 'string' || !ENTRY_NAME.test(e)) throw new BuildError(`ui/parts.json: ${name}: ${JSON.stringify(e)} is not a name`);
      if (e in entries) throw new BuildError(`ui/parts.json: ${e} is an entry of two parts (${entries[e]}, ${name})`);
      entries[e] = name;
    }
    if (!part.files.length) throw new BuildError(`ui/parts.json: ${name} has no files`);
    parts[name] = part;
  }
  const names = Object.keys(parts);
  for (const [name, part] of Object.entries(parts)) {
    for (const dep of part.deps) if (!(dep in parts)) throw new BuildError(`ui/parts.json: ${name} depends on ${dep}, which is not a part`);
  }
  for (const [name, part] of Object.entries(parts)) {
    const need = new Set([name]), todo = [name];
    while (todo.length) for (const dep of parts[todo.pop()].deps) if (!need.has(dep)) { need.add(dep); todo.push(dep); }
    part.load = names.filter(n => need.has(n));
  }
  // So a part's scripts never run before a part it needs (only two that need each other share one order): a part's
  // functions replace their stand-ins the moment its script runs, and a caller then would find its needs missing.
  for (const [name, part] of Object.entries(parts)) {
    for (const dep of part.load) {
      if (names.indexOf(dep) > names.indexOf(name) && !parts[dep].load.includes(name)) throw new BuildError(`ui/parts.json: ${name} needs ${dep}, so ${dep} must be listed before it`);
    }
  }
  return {parts, owner};
}

// The shell's stand-ins for the page parts' entries (ui/app/parts.ts uiPartCall), declared before any file runs.
function stubs(parts) {
  const out = [], called = [];
  for (const [name, part] of Object.entries(parts)) for (const kind of ENTRY_KINDS) for (const e of part[kind]) {
    if (kind === 'idle') { out.push(`function ${e}() {}\n`); continue; }
    out.push(`function ${e}(...a) { return uiPartCall("${name}", "${e}", a, ${kind === 'pages'}); }\n`);
    called.push(e);
  }
  // So a call that finds a stand-in still there after its part loaded fails, rather than calling itself.
  if (called.length) out.push(`const UI_PART_STUBS = new Set([${called.join(', ')}]);\n`);
  return out.join('');
}

// One file minified, with its map to the source (named by its path under ui/, with its text): {code, map}.
async function minify(name, text) {
  const loader = name.endsWith('.css') ? 'css' : name.endsWith('.ts') ? 'ts' : 'js';
  const result = await esbuild.transform(text, {
    loader, minify: true, target: 'esnext', charset: 'utf8', logLevel: 'warning',
    sourcemap: 'external', sourcefile: name, sourcesContent: true, legalComments: 'none',
  });
  const map = JSON.parse(result.map);
  map.sources = [name];
  return {code: result.code, map};
}

// The chunks (each one file's text, in order) packed into parts of at most `limit` bytes; a larger file is a part alone.
function pack(chunks, limit = PART_BYTES) {
  const parts = [[]];
  let size = 0;
  for (const chunk of chunks) {
    if (parts.at(-1).length && size + chunk.bytes > limit) { parts.push([]); size = 0; }
    parts.at(-1).push(chunk);
    size += chunk.bytes;
  }
  return parts;
}

// One bundle file: its text (a head, then each chunk), an index map placing each chunk's map at its first line, and its
// name from both, so a file and its map change name together. Written with a gzip copy of each.
function emit(written, stem, kind, head, chunks) {
  let line = head.split('\n').length - 1, body = head;
  const sections = [];
  for (const chunk of chunks) {
    if (chunk.map) sections.push({offset: {line: line + chunk.before, column: 0}, map: chunk.map});
    line += chunk.text.split('\n').length - 1;
    body += chunk.text;
  }
  const map = sections.length ? JSON.stringify({version: 3, sections}) : '';
  const name = `${NAME}${stem ? stem + '.' : ''}${sha256(body + map).slice(0, 16)}.${kind}`;
  if (map) body += kind === 'css' ? `/*# sourceMappingURL=${name}.map */\n` : `//# sourceMappingURL=${name}.map\n`;
  const files = [[name, body], ...(map ? [[`${name}.map`, map]] : [])];
  for (const [file, data] of files) {
    writeAtomic(path.join(BUNDLE, file), data);
    writeAtomic(path.join(BUNDLE, `${file}.gz`), zlib.gzipSync(data, {level: 9}));
    written.add(path.join(BUNDLE, file)).add(path.join(BUNDLE, `${file}.gz`));
  }
  return name;
}

// Each TypeScript file compiled on its own to build/ui at its own path, with its map beside it.
async function compileEach(written, names) {
  const result = names.length ? await esbuild.build({
    entryPoints: names.map(name => path.join(UI, name)), outdir: OUT, outbase: UI, write: false,
    bundle: false, target: 'esnext', charset: 'utf8', logLevel: 'warning', sourcemap: 'linked',
  }) : {outputFiles: []};
  for (const out of result.outputFiles) {
    let contents = out.contents;
    if (out.path.endsWith('.js.map')) {
      // esbuild names the source by its path from build/ui; the browser resolves it from the map's URL
      // (/tico/ui/app/a.js.map), where the source is a.ts beside it. The map carries the source text too.
      const map = JSON.parse(Buffer.from(contents).toString('utf8'));
      map.sources = [path.basename(out.path).replace(/\.js\.map$/, '.ts')];
      contents = Buffer.from(JSON.stringify(map));
    }
    writeAtomic(out.path, contents);
    written.add(out.path);
  }
}

async function build() {
  const inputs = {};
  const read = name => {
    const data = fs.readFileSync(path.join(UI, name));
    inputs[name] = sha256(data);
    return data.toString('utf8');
  };
  const written = new Set([MANIFEST]);
  const tsFiles = sources();
  tsFiles.forEach(read);
  await compileEach(written, tsFiles);

  let html = read('index.html');
  const files = listed(html);
  if (fs.existsSync(path.join(UI, 'parts.json'))) read('parts.json');
  const {parts, owner} = readParts(files.js);
  const minified = Object.fromEntries(await Promise.all([...files.js, ...files.css].map(async name => [name, await minify(name, read(name))])));

  // One script is one scope for 'use strict': a directive only counts at the top of a script, so each part opens with it
  // once, and every file must already be strict. esbuild writes the directive with double quotes.
  const shell = [], paged = Object.fromEntries(Object.keys(parts).map(name => [name, []]));
  for (const name of files.js) {
    const {code, map} = minified[name];
    if (!/^(['"])use strict\1;/m.test(code)) throw new BuildError(`ui/${name}: not strict, so it cannot share a script`);
    const text = `// file: ${name}\n${code.trimEnd()}\n\n`;
    (owner[name] ? paged[owner[name]] : shell).push({text, bytes: Buffer.byteLength(text), before: 1, map});
  }
  const css = files.css.map(name => {
    const text = `/* file: ${name} */\n${minified[name].code.trimEnd()}\n\n`;
    return {text, bytes: Buffer.byteLength(text), before: 1, map: minified[name].map};
  });
  const head = `'use strict';\n${BUNDLE_MARK}${stubs(parts)}\n`;
  const shellJs = pack(shell).map((chunks, n) => emit(written, '', 'js', n === 0 ? head : "'use strict';\n\n", chunks));
  const shellCss = pack(css).map(chunks => emit(written, '', 'css', '', chunks));
  const partNames = {};
  for (const [name, chunks] of Object.entries(paged)) {
    if (chunks.reduce((n, c) => n + c.bytes, 0) > PART_BYTES) throw new BuildError(`ui/parts.json: ${name} is over ${PART_BYTES} bytes, more than WebKit's cache keeps: split it`);
    partNames[name] = emit(written, name, 'js', "'use strict';\n\n", chunks);
  }
  // theme.ts (in the head, before the first paint) and marked load on their own, each named by its content too.
  const standalone = {};
  for (const [url, [stem, name]] of Object.entries(STANDALONE)) {
    if (!html.includes(`src="${url}"`) || !fs.existsSync(path.join(UI, name))) continue;
    const text = read(name);
    const chunk = name.endsWith('.ts') ? await minify(name, text) : {code: text, map: null};
    const body = `${chunk.code.trimEnd()}\n`;
    standalone[url] = emit(written, stem, 'js', '', [{text: body, bytes: Buffer.byteLength(body), before: 0, map: chunk.map}]);
  }

  const every = [...shellJs, ...shellCss, ...Object.values(partNames), ...Object.values(standalone)];
  const buildId = sha256(every.join('\n')).slice(0, 16);
  const table = {build: buildId, parts: Object.fromEntries(Object.entries(parts).map(([name, part]) => [name, {
    url: PREFIX + partNames[name], load: part.load, ...(part.events.length ? {events: part.events} : {}),
  }]))};
  html = html.replace(REGION, (_, kind) => kind === 'css'
    ? shellCss.map(name => `<link rel="stylesheet" href="${PREFIX}${name}">\n`).join('')
    // Data, not code: a JSON block runs nothing, so the page's script-src stays 'self'.
    : `<script type="application/json" id="ui-parts">${JSON.stringify(table)}</script>\n`
      + shellJs.map(name => `<script src="${PREFIX}${name}"></script>\n`).join(''));
  for (const [url, name] of Object.entries(standalone)) html = html.replace(`src="${url}"`, `src="${PREFIX}${name}"`);
  writeAtomic(path.join(OUT, 'index.html'), html);
  written.add(path.join(OUT, 'index.html'));

  // The output of a source that is gone, or an older build's bundle file, would otherwise stay.
  const prune = dir => {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) { prune(file); if (!fs.readdirSync(file).length) fs.rmdirSync(file); }
      else if (!written.has(file)) fs.rmSync(file);
    }
  };
  // Written last: until it names the new inputs, backend/ui_build.py counts the build as stale.
  const sorted = Object.fromEntries(Object.entries(inputs).sort(([a], [b]) => a < b ? -1 : 1));
  writeAtomic(MANIFEST, JSON.stringify({build: buildId, shell: [...shellJs, ...Object.values(standalone)], inputs: sorted}, null, 2) + '\n');
  prune(OUT);
  return {count: tsFiles.length, parts: every.length};
}

// Every input under ui/ the build reads: a change to one rebuilds (tests are not inputs).
const watched = name => !name.startsWith('tests/') && !name.split('/').includes('node_modules') && /\.(?:ts|css|js|html|json)$/.test(name);

async function main() {
  const started = Date.now();
  const report = ({count, parts}) => `${count} TypeScript file${count === 1 ? '' : 's'}, ${parts} bundle files`;
  console.log(`ui build: ${report(await build())} -> build/ui (${Date.now() - started} ms)`);
  if (!process.argv.includes('--watch')) return;
  // Every change rebuilds everything: a new file is picked up too, and the whole set takes about a second.
  let timer = null, running = Promise.resolve();
  fs.watch(UI, {recursive: true}, (_, name) => {
    if (!name || !watched(String(name).split(path.sep).join('/'))) return;
    clearTimeout(timer);
    timer = setTimeout(() => {
      running = running.then(build).then(
        result => console.log(`ui build: ${report(result)} at ${new Date().toLocaleTimeString()}`),
        error => console.error(`ui build failed: ${error.message}`));
    }, 50);
  });
  console.log('ui build: watching ui/ (reload the page after a rebuild)');
}

main().catch(error => { console.error(`ui build failed: ${error.message}`); process.exit(1); });
