// Builds dist/: the main process and each preload bundled into one file apiece with esbuild, so the packaged app ships
// no node_modules (electron-updater and its dependencies are inside main.js). Types are checked by `npm run typecheck`
// (tsc --noEmit); esbuild only strips them. dist/ is cleared first so nothing stale is ever packaged.
// - dist/main/main.js: the main process (Node in Electron 45). __dirname stays dist/main, which contents.ts and
//   shell.ts use to find the preloads and the local pages.
// - dist/preload.js, dist/localPreload.js: preloads run sandboxed, where only `electron` can be required. They are
//   built for the browser, so an import of a Node module or package fails the build instead of the page.
// Not minified, and names kept, so a stack trace in a log reads as the source does.
import { rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
rmSync(dist, { recursive: true, force: true });

const common = { bundle: true, format: 'cjs', external: ['electron'], keepNames: true, logLevel: 'warning', absWorkingDir: root };
await Promise.all([
  build({ ...common, entryPoints: ['src/main/main.ts'], outfile: 'dist/main/main.js', platform: 'node', target: 'node24' }),
  build({
    ...common,
    entryPoints: ['src/preload.ts', 'src/localPreload.ts'],
    outdir: 'dist',
    platform: 'browser',
    target: 'chrome156',
  }),
]);
