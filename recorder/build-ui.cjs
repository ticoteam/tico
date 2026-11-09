#!/usr/bin/env node
// Builds the Recorder window into recorder/dist (gitignored): ui/app -> dist/app, which tauri.conf.json embeds
// (frontendDist), and ui/preview -> dist/preview, the mock-backed page the screenshots and browser specs open.
// Each .ts file becomes a .js file at the same path, with the same esbuild transform as scripts/build-ui.cjs: types
// are stripped, nothing is bundled, and the file stays a classic script, so index.html lists it as the .js file it
// was. Every other file (HTML, CSS, fonts) is copied as it is. Declaration files and Node scripts (.mts, .cts) are
// left out. Anything else under dist/app and dist/preview is removed, and an output is rewritten only when its bytes
// change.
// Last it writes dist/build-manifest.txt: each source and output with its FNV-1a 64 hash, and the hashes of the
// recipe (this file, package.json, package-lock.json). build.rs checks dist/app against it by content, not by time.
// Usage: node recorder/build-ui.cjs [--watch]   (needs the repository's npm ci for esbuild; cargo tauri build runs
// it, and cargo tauri dev runs it with --watch: one full build, then a rebuild on each change under ui/app or
// ui/preview, until it is stopped. While it watches, dist/.watching holds its process id, so build.rs waits for it.)
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const RECORDER = __dirname;
const UI = path.join(RECORDER, 'ui');
const DIST = path.join(RECORDER, 'dist');
const PARTS = ['app', 'preview'];
const TYPES_ONLY = /\.d\.[cm]?ts$/;
const NODE_SCRIPT = /\.[cm]ts$/;
const MANIFEST = path.join(DIST, 'build-manifest.txt');
const WATCHING = path.join(DIST, '.watching');
// The recipe: a change to any of them can change the output, so build.rs counts them (paths relative to recorder/).
const INPUTS = ['build-ui.cjs', '../package.json', '../package-lock.json'];

let esbuild;
try {
  esbuild = require('esbuild');
} catch {
  console.error('recorder ui build: esbuild is missing. Run `npm ci` at the repository root first.');
  process.exit(1);
}

// FNV-1a 64 as 16 hex digits: build.rs (std only) computes the same hash, so no hashing crate is needed there.
function fnv(bytes) {
  let hash = 0xcbf29ce484222325n;
  for (const byte of bytes) hash = ((hash ^ BigInt(byte)) * 0x100000001b3n) & 0xffffffffffffffffn;
  return hash.toString(16).padStart(16, '0');
}

function hashOf(file) {
  return fs.existsSync(file) ? fnv(fs.readFileSync(file)) : 'missing';
}

// Every file under dir, as a path relative to it, sorted.
function files(dir, rel = '') {
  const found = [];
  for (const entry of fs.readdirSync(path.join(dir, rel), {withFileTypes: true})) {
    const name = rel ? `${rel}/${entry.name}` : entry.name;
    if (entry.isDirectory()) found.push(...files(dir, name));
    else if (entry.isFile()) found.push(name);
  }
  return found.sort();
}

function writeAtomic(file, data) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  const part = `${file}.${process.pid}.part`;
  fs.writeFileSync(part, data);
  fs.renameSync(part, file);
}

const fromRecorder = file => path.relative(RECORDER, file).split(path.sep).join('/');

function build() {
  // A build that stops halfway leaves no manifest, so build.rs reads it as not built.
  fs.rmSync(MANIFEST, {force: true});
  const written = new Set();
  const entries = [];
  let compiled = 0;
  let changed = 0;
  for (const part of PARTS) {
    const from = path.join(UI, part);
    for (const name of files(from)) {
      if (TYPES_ONLY.test(name) || NODE_SCRIPT.test(name)) continue;
      const source = fs.readFileSync(path.join(from, name));
      let out = path.join(DIST, part, name);
      let data = source;
      if (name.endsWith('.ts')) {
        out = out.replace(/\.ts$/, '.js');
        // The options scripts/build-ui.cjs gives esbuild, per file and without a bundle.
        data = Buffer.from(esbuild.transformSync(source.toString('utf8'), {
          loader: 'ts', target: 'esnext', charset: 'utf8', sourcefile: name, logLevel: 'warning',
        }).code);
        compiled++;
      }
      if (!fs.existsSync(out) || !fs.readFileSync(out).equals(data)) {
        writeAtomic(out, data);
        changed++;
      }
      written.add(out);
      entries.push(`file\t${fromRecorder(path.join(from, name))}\t${fnv(source)}\t${fromRecorder(out)}\t${fnv(data)}`);
    }
  }
  // An output whose source is gone would still be embedded; remove it, and any folder left empty.
  const prune = dir => {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) { prune(file); if (!fs.readdirSync(file).length) fs.rmdirSync(file); }
      else if (!written.has(file)) { fs.rmSync(file); changed++; }
    }
  };
  for (const part of PARTS) prune(path.join(DIST, part));
  const manifest = [
    '# Written by recorder/build-ui.cjs; build.rs checks dist/app against it. FNV-1a 64 hashes; paths from recorder/.',
    `esbuild\t${esbuild.version}`,
    ...INPUTS.map(input => `input\t${input}\t${hashOf(path.join(RECORDER, input))}`),
    ...entries,
  ];
  writeAtomic(MANIFEST, `${manifest.join('\n')}\n`);
  return {compiled, changed, total: written.size};
}

function run() {
  const started = Date.now();
  const {compiled, changed, total} = build();
  console.log(`recorder ui build: ${compiled} TypeScript file${compiled === 1 ? '' : 's'}, ${total} files, ${changed} changed -> recorder/dist (${Date.now() - started} ms)`);
}

function watch() {
  run();
  fs.writeFileSync(WATCHING, `${process.pid}\n`);
  process.on('exit', () => fs.rmSync(WATCHING, {force: true}));
  for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(signal, () => process.exit(0));
  // A save is often several events; rebuild once they settle. A failed rebuild (a syntax error) is reported and the
  // watch goes on: the manifest is gone until the next good build, so cargo stops rather than embed the old window.
  let timer;
  const again = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      try { run(); } catch (error) { console.error(`recorder ui build failed: ${error.message}`); }
    }, 50);
  };
  for (const part of PARTS) fs.watch(path.join(UI, part), {recursive: true}, again);
  console.log('recorder ui build: watching ui/app and ui/preview (Ctrl-C stops)');
}

try {
  if (process.argv.includes('--watch')) watch();
  else run();
} catch (error) {
  console.error(`recorder ui build failed: ${error.message}`);
  process.exit(1);
}
