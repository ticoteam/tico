import { describe, expect, it } from 'vitest';
import { goTo } from '@/actions/base';
import { pages } from './pages';

/* The menu's pages: search finds one by name, and opening it says its name. Needs you is the top of Tasks, no page of
   its own. */

describe('the Pages group', () => {
  it('finds Tasks by name, with its mark, opening /tasks; Needs you is no page of its own', async () => {
    const hits = await pages.find('tasks', new AbortController().signal);
    expect(hits[0]).toMatchObject({ title: 'Tasks', lead: { icon: 'tasks' }, run: { id: 'shell.goTo', input: { to: '/tasks' } } });
    expect(goTo.did?.({ to: '/tasks' }, undefined)).toBe('Opened Tasks');
    expect(await pages.find('needs', new AbortController().signal)).toEqual([]);
  });
});
