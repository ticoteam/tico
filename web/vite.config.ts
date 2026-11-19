import { fileURLToPath } from 'node:url';
import babel from '@rolldown/plugin-babel';
import { tanstackRouter } from '@tanstack/router-plugin/vite';
import react, { reactCompilerPreset } from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const server = 'http://127.0.0.1:8765';
const toServer = { target: server, changeOrigin: false };

export default defineConfig({
  // Assets stay under /next/ even after the switch; only the router's basepath changes (transition.md 1.3).
  base: '/next/',
  plugins: [
    tanstackRouter({ target: 'react', autoCodeSplitting: true }),
    react(),
    babel({ presets: [reactCompilerPreset()] }),
  ],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: {
    outDir: fileURLToPath(new URL('../build/web', import.meta.url)),
    emptyOutDir: true,
    manifest: true,
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
