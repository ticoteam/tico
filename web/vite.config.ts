import { fileURLToPath } from 'node:url';
import babel from '@rolldown/plugin-babel';
import { tanstackRouter } from '@tanstack/router-plugin/vite';
import react, { reactCompilerPreset } from '@vitejs/plugin-react';
import type { Plugin } from 'vite';
import { defineConfig } from 'vitest/config';

const server = 'http://127.0.0.1:8765';
// The server refuses writes from another origin; through the proxy the page's writes come from the server's own.
const toServer = { target: server, changeOrigin: false, headers: { origin: server } };

/* What the page preloads. The page itself: only the entry's own imports; the browser finds the next level as those
   arrive. A later page's code: only what the first load did not already bring. Preloading more gained nothing, and
   WebKit then reports each extra one as "preloaded but not used" on every later visit (run 3). */
const firstLoad = new Set<string>();
const entryImports = new Set<string>();
const preloads: Plugin = {
  name: 'tico:preloads',
  apply: 'build',
  generateBundle: {
    order: 'pre',
    handler(_options, bundle) {
      firstLoad.clear();
      entryImports.clear();
      const chunks = Object.values(bundle).flatMap((c) => (c.type === 'chunk' ? [c] : []));
      const entry = chunks.find((c) => c.isEntry);
      for (const f of entry?.imports ?? []) entryImports.add(f);
      const walk = (file: string) => {
        if (firstLoad.has(file)) return;
        firstLoad.add(file);
        for (const f of chunks.find((c) => c.fileName === file)?.imports ?? []) walk(f);
      };
      if (entry) walk(entry.fileName);
    },
  },
  transformIndexHtml: {
    order: 'post',
    handler: (html) =>
      entryImports.size
        ? html.replace(/[ \t]*<link rel="modulepreload"[^>]*href="\/next\/([^"]+)"[^>]*>\n?/g, (tag, file: string) => (entryImports.has(file) ? tag : ''))
        : html,
  },
};

export default defineConfig({
  // Assets stay under /next/ even after the switch; only the router's basepath changes (transition.md 1.3).
  base: '/next/',
  plugins: [
    tanstackRouter({ target: 'react', autoCodeSplitting: true }),
    react(),
    babel({ presets: [reactCompilerPreset()] }),
    preloads,
  ],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: {
    outDir: fileURLToPath(new URL('../build/web', import.meta.url)),
    emptyOutDir: true,
    manifest: true,
    modulePreload: { resolveDependencies: (_file, deps, { hostType }) => (hostType === 'js' ? deps.filter((d) => !firstLoad.has(d)) : deps) },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      // The live stream is long-lived: no timeouts, and nothing in between may hold frames back.
      '/api/v2/events': {
        ...toServer,
        timeout: 0,
        proxyTimeout: 0,
        configure: (proxy) => {
          proxy.on('proxyRes', (res) => {
            res.headers['cache-control'] = 'no-cache, no-transform';
            res.headers['x-accel-buffering'] = 'no';
          });
        },
      },
      '/api': toServer,
      '/auth': toServer,
      '/_tico': toServer,
      '/visuals': toServer,
      '/download': toServer,
      '/public': toServer,
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test/setup.ts'],
    pool: 'threads',
    maxWorkers: 1,
  },
});
