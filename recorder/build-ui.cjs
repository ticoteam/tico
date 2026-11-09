#!/usr/bin/env node
// Builds the Recorder window into recorder/dist (gitignored): ui/app -> dist/app, which tauri.conf.json embeds
// (frontendDist), and ui/preview -> dist/preview, the mock-backed page the screenshots and browser specs open.
// Each .ts file becomes a .js file at the same path, with the same esbuild transform as scripts/build-ui.cjs: types
// are stripped, nothing is bundled, and the file stays a classic script, so index.html lists it as the .js file it
// was. Every other file (HTML, CSS, fonts) is copied as it is. Declaration files and Node scripts (.mts, .cts) are
// left out. Every output is rewritten on each run and anything else under dist/app and dist/preview is removed, so
// build.rs can tell a stale or missing output by its time alone.
// Usage: node recorder/build-ui.cjs   (needs the repository's npm ci for esbuild; cargo tauri build/dev run it)
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const RECORDER = __dirname;
const UI = path.join(RECORDER, 'ui');
const DIST = path.join(RECORDER, 'dist');
const PARTS = ['app', 'preview'];
const TYPES_ONLY = /\.d\.[cm]?ts$/;
const NODE_SCRIPT = /\.[cm]ts$/;

let esbuild;
try {
  esbuild = require('esbuild');
} catch {
  console.error('recorder ui build: esbuild is missing. Run `npm ci` at the repository root first.');
  process.exit(1);
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

function build() {
  const written = new Set();
  let compiled = 0;
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
        data = esbuild.transformSync(source.toString('utf8'), {
          loader: 'ts', target: 'esnext', charset: 'utf8', sourcefile: name, logLevel: 'warning',
        }).code;
        compiled++;
      }
      writeAtomic(out, data);
      written.add(out);
    }
  }
  // An output whose source is gone would still be embedded; remove it, and any folder left empty.
  const prune = dir => {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) { prune(file); if (!fs.readdirSync(file).length) fs.rmdirSync(file); }
      else if (!written.has(file)) fs.rmSync(file);
    }
  };
  for (const part of PARTS) prune(path.join(DIST, part));
  return {compiled, total: written.size};
}

const started = Date.now();
try {
  const {compiled, total} = build();
  console.log(`recorder ui build: ${compiled} TypeScript file${compiled === 1 ? '' : 's'}, ${total} files -> recorder/dist (${Date.now() - started} ms)`);
} catch (error) {
  console.error(`recorder ui build failed: ${error.message}`);
  process.exit(1);
}
