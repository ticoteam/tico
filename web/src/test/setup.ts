import { cleanup, configure } from '@testing-library/react';
import { afterEach, beforeEach } from 'vitest';

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
