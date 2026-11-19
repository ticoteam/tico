import { within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Button } from '@/kit/Button';
import { List } from '@/pagekinds/List';
import { ListAndItem } from '@/pagekinds/ListAndItem';
import type { StateProps } from '@/pagekinds/types';
import { showPage } from './test-page';

/* One filled button to a page: an empty page's action carries it in the empty state and the head's draws plain; a page
   with rows carries it in the head. */

afterEach(() => vi.restoreAllMocks());

const add = (
  <Button variant="primary" onClick={() => {}}>
    Add watch
  </Button>
);

const fills = (main: HTMLElement) => Array.from(main.querySelectorAll('.btn-fill'));

function list(s: StateProps) {
  return <List title="Listening" path="/listening" actions={add} empty={{ title: 'No watches yet', action: add }} sections={<p>A watch</p>} {...s} />;
}

describe('the one filled button', () => {
  it('sits in the empty state on an empty page, and the head draws its action plain', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const main = await showPage(list({ state: 'empty' }));
    expect(fills(main)).toHaveLength(1);
    expect(fills(main)[0]!.closest('.empty')).not.toBeNull();
    const head = main.querySelector('header')!;
    expect(within(head).getByRole('button', { name: 'Add watch' }).className).toBe('btn');
    expect(warn).not.toHaveBeenCalled();
  });

  it('sits in the head on a page with rows', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const main = await showPage(list({ state: 'ready' }));
    expect(fills(main)).toHaveLength(1);
    expect(fills(main)[0]!.closest('header')).not.toBeNull();
    expect(warn).not.toHaveBeenCalled();
  });

  it('is Try again on a page that did not load', async () => {
    const main = await showPage(list({ state: 'fault', onRetry: () => {} }));
    expect(fills(main).map((b) => b.textContent)).toEqual(['Try again']);
  });

  it('stays in the head of an empty page whose empty state has no action', async () => {
    const main = await showPage(<List title="Disputes" path="/disputes" actions={add} state="empty" empty={{ title: 'No disputes' }} sections={null} />);
    expect(fills(main)).toHaveLength(1);
    expect(fills(main)[0]!.closest('header')).not.toBeNull();
  });

  it('holds in the list-and-item kind too', async () => {
    const main = await showPage(
      <ListAndItem title="Threads" path="/threads" actions={add} state="empty" empty={{ title: 'No threads', action: add }} list={null} item={null} />,
    );
    expect(fills(main)).toHaveLength(1);
    expect(fills(main)[0]!.closest('.empty')).not.toBeNull();
  });
});
