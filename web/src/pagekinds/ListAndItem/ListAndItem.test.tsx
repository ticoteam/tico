import { within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { expectHeadAndTabs, showPage, stateTests, TABS } from '@/pagekinds/_parts/test-page';
import { ItemHead, ListAndItem } from './ListAndItem';

describe('ListAndItem', () => {
  it('puts the head, search, tabs and rows in the list column, and the item in its own', async () => {
    const main = await showPage(
      <ListAndItem
        title="Threads"
        path="/threads"
        tabs={TABS}
        search={<input aria-label="Search threads" />}
        list={<a href="/threads/1" data-target="thread:1">Launch</a>}
        itemHead={<ItemHead title="Launch" target="thread:1" back={{ to: '/threads', label: 'Threads' }} />}
        item={<p>The thread</p>}
        foot={<textarea aria-label="Message" />}
        itemLabel="Thread"
        open
      />,
    );
    expectHeadAndTabs(main, 'Threads', '/threads');
    const list = within(main).getByRole('region', { name: 'Threads' });
    expect(within(list).getByRole('textbox', { name: 'Search threads' })).toBeTruthy();
    expect(within(list).getByRole('link', { name: 'Launch' }).getAttribute('data-target')).toBe('thread:1');
    const item = within(main).getByRole('region', { name: 'Thread' });
    expect(within(item).getByRole('heading', { level: 2, name: 'Launch' })).toBeTruthy();
    expect(within(item).getByRole('link', { name: 'Back to Threads' })).toBeTruthy();
    expect(within(item).getByText('The thread')).toBeTruthy();
    expect(within(item).getByRole('textbox', { name: 'Message' })).toBeTruthy();
  });

  stateTests('Threads', (s) => <ListAndItem title="Threads" path="/threads" list={null} {...s} />);
});
