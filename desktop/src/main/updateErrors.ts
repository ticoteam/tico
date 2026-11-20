// What an updater error means for the state the app shows. Pure (no Electron), so it is tested on its own.
// The feed is the latest GitHub release's latest-*.yml. It is missing for the few minutes between a release being
// published and its desktop files arriving, and on a release that has none; a file can also vanish mid-download when
// a newer release becomes the latest. Those are not failures: the state stays as it was and the next check tries again.
import type { UpdateState } from '../bridge';

/** True when the error is a 404 for the feed or one of its files, rather than a real failure. */
export function feedMissing(error: unknown): boolean {
  if (typeof error !== 'object' || error === null) return false;
  const { code, statusCode, message } = error as { code?: unknown; statusCode?: unknown; message?: unknown };
  if (code === 'ERR_UPDATER_CHANNEL_FILE_NOT_FOUND' || statusCode === 404) return true;
  // An installer download that 404s is a plain Error: 'Cannot download "<url>", status 404: Not Found'.
  return typeof message === 'string' && /^Cannot download ".*", status 404\b/.test(message);
}

/** The state after an updater error: a ready update is kept, a missing feed changes nothing (a download it cut short
 * goes back to 'none'), anything else is 'error'. */
export function stateAfterError(current: UpdateState, error: unknown): UpdateState {
  if (current.state === 'ready') return current;
  if (feedMissing(error)) return current.state === 'downloading' ? { state: 'none' } : current;
  return { state: 'error' };
}
