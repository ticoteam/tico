import { describe, expect, it } from 'vitest';
import { tabId } from './tab';

describe('tabId', () => {
  it('keeps one id per tab', () => {
    expect(tabId()).toBe(tabId());
    expect(sessionStorage.getItem('tico-next:tab')).toBe(tabId());
  });
});
