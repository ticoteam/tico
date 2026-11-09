// Types for support/recorder-pause.cjs (the helper stays JavaScript: live-meetings.cts requires it by its .cjs path).
import type {Browser} from 'playwright';
declare function recorderPause(browser: Browser): Promise<void>;
export = recorderPause;
