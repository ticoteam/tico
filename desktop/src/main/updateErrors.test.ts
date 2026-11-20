import { describe, expect, it } from 'vitest';
import { feedMissing, stateAfterError } from './updateErrors';

// The shapes electron-updater throws: newError() sets `code`; a failed HTTP request is an HttpError with `statusCode`.
const coded = (code: string) => Object.assign(new Error(code), { code });
const http = (statusCode: number) => Object.assign(new Error(`HTTP ${statusCode}`), { statusCode });
const channelMissing = coded('ERR_UPDATER_CHANNEL_FILE_NOT_FOUND');

describe('updater errors', () => {
  it('treats a missing feed or file as no update, not a failure', () => {
    expect(feedMissing(channelMissing)).toBe(true);
    expect(feedMissing(http(404))).toBe(true);
    expect(feedMissing(new Error('Cannot download "https://github.com/x/Tico-1.2.0-arm64.zip", status 404: Not Found'))).toBe(true);
    expect(feedMissing(new Error('Cannot download "https://github.com/x/Tico-1.2.0-arm64.zip", status 500: Error'))).toBe(false);
    expect(feedMissing(http(500))).toBe(false);
    expect(feedMissing(coded('ERR_UPDATER_INVALID_SIGNATURE'))).toBe(false);
    expect(feedMissing(new Error('net::ERR_INTERNET_DISCONNECTED'))).toBe(false);
    expect(feedMissing(undefined)).toBe(false);
  });

  it('keeps the state when the latest release has no desktop files yet', () => {
    expect(stateAfterError({ state: 'none' }, channelMissing)).toEqual({ state: 'none' });
    expect(stateAfterError({ state: 'error' }, channelMissing)).toEqual({ state: 'error' });
    expect(stateAfterError({ state: 'downloading', version: '1.2.0' }, http(404))).toEqual({ state: 'none' });
  });

  it('shows a real failure, and never drops an update that is ready', () => {
    expect(stateAfterError({ state: 'none' }, http(500))).toEqual({ state: 'error' });
    expect(stateAfterError({ state: 'downloading', version: '1.2.0' }, new Error('checksum'))).toEqual({ state: 'error' });
    expect(stateAfterError({ state: 'ready', version: '1.2.0' }, http(500))).toEqual({ state: 'ready', version: '1.2.0' });
    expect(stateAfterError({ state: 'ready', version: '1.2.0' }, channelMissing)).toEqual({
      state: 'ready',
      version: '1.2.0',
    });
  });
});
