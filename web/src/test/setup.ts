import { cleanup, configure } from '@testing-library/react';
import { afterEach } from 'vitest';

// findBy/waitFor wait up to 5s: a whole-suite run on a busy machine can take over the default 1s to draw a lazy page.
// Nothing waits that long when the code is right; it only stops false failures under load.
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  localStorage.clear();
});
