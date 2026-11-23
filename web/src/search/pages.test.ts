import { describe, expect, it } from 'vitest';
import { goTo } from '@/actions/base';
import { pages } from './pages';

/* Needs you is a page of the menu: search finds it by name, and opening it says its name. */

describe('the Pages group', () => {
  it('finds Needs you by name, with its mark, opening /needs-you', async () => {
    const hits = await pages.find('needs', new AbortController().signal);
    expect(hits[0]).toMatchObject({ title: 'Needs you', lead: { icon: 'inbox' }, run: { id: 'shell.goTo', input: { to: '/needs-you' } } });
    expect(goTo.did?.({ to: '/needs-you' }, undefined)).toBe('Opened Needs you');
  });
});
