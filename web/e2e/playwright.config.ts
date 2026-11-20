// The web app's journeys (journeys.ts), each on its own fresh made-up company (hub/seeded.py). `npm run e2e` builds the
// app first; `npm run e2e:repeat` runs every journey three times, the bar a journey clears before it lands.
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: 'journeys.ts',
  // one server and one browser at a time: light on the machine, and nothing shared between journeys
  workers: 1,
  fullyParallel: false,
  // a flaky journey is a broken journey: no retries
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [['list'], ['github']] : 'list',
  outputDir: process.env.TICO_E2E_OUT || '../../build/web-e2e',
  use: {
    headless: true,
    // TICO_BROWSER_CHANNEL=chrome uses the installed Chrome; by default Playwright's own Chromium
    ...(process.env.TICO_BROWSER_CHANNEL ? { channel: process.env.TICO_BROWSER_CHANNEL } : {}),
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
