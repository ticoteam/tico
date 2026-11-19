import { describe, expect, it } from 'vitest';
import { registry } from '@/actions';
import { settingsSearch } from './search';

const sig = () => new AbortController().signal;

describe('settings search', () => {
  it('finds a section by its name and opens it', async () => {
    const hits = await settingsSearch.find('billing', sig());
    expect(hits.map((h) => [h.title, h.run])).toEqual([['Settings, Billing', { id: 'settings.open', input: { section: 'billing' } }]]);
    expect(registry.get('settings.open')?.input.parse(hits[0]!.run.input).ok).toBe(true);
  });

  it('lists sections for the word settings, a few at most, and nothing for no words', async () => {
    expect(await settingsSearch.find('settings', sig())).toHaveLength(8);
    expect(await settingsSearch.find('', sig())).toEqual([]);
  });
});
