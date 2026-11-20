import { cleanup, configure } from '@testing-library/react';
import { afterAll, afterEach, beforeEach, vi } from 'vitest';

// findBy/waitFor wait up to 5s: a whole-suite run on a busy machine can take over the default 1s to draw a lazy page.
// Nothing waits that long when the code is right; it only stops false failures under load.
configure({ asyncUtilTimeout: 5000 });

// A laptop's window. At jsdom's own 1024 sheets tuck to their edges (shell/layout.ts, Layers), out of reach of a test
// that reads them; a test that wants another width resizes.
beforeEach(() => {
  Object.defineProperty(window, 'innerWidth', { value: 1440, configurable: true });
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

/* TanStack Query hands its observers their news in a batch on the next timer (notifyManager's setTimeout 0). A read
   that answered as a file's last test ended leaves one pending: under a whole-suite load it ran after the file's jsdom
   was taken down, and React, told of the change, read `window` ("window is not defined", seen in Usage and Lists).
   One timer turn at the end of each file lets it run while the window is still there. */
afterAll(async () => {
  if (!vi.isFakeTimers()) await new Promise((done) => setTimeout(done, 0));
});
