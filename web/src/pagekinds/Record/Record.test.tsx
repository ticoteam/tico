import { act, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { expectHeadAndTabs, showPage, stateTests, TABS } from '@/pagekinds/_parts/test-page';
import { TrailContext } from '@/shell';
import { Record, RecordPanel } from './Record';

describe('Record', () => {
  it('draws a phone’s back up the trail to its list, facts, the side column and sections; no trail of its own', async () => {
    const main = await showPage(
      <TrailContext value={[{ label: 'Products', to: '/records/products' }, { label: 'Atlas' }]}>
        <Record
          title="Atlas"
          path="/records/products/atlas"
          tabs={TABS}
          facts={<dl aria-label="Facts" />}
          side={<p>Owner</p>}
          sections={<section aria-label="Matters" />}
        />
      </TrailContext>,
    );
    expectHeadAndTabs(main, 'Atlas', '/records/products/atlas');
    expect(within(main).queryByRole('navigation', { name: 'Breadcrumb' })).toBeNull();
    expect(within(main).getByRole('link', { name: 'Back to Products' }).getAttribute('href')).toBe('/records/products');
    expect(within(within(main).getByRole('complementary', { name: 'Details' })).getByText('Owner')).toBeTruthy();
    expect(within(main).getByRole('region', { name: 'Matters' })).toBeTruthy();
  });

  stateTests('Atlas', (s) => <Record title="Atlas" path="/records/products/atlas" {...s} />);

  it('a panel still reading draws bars in its own shape, after a beat, and none of its slots', () => {
    vi.useFakeTimers();
    try {
      render(<RecordPanel title="Atlas" onClose={() => undefined} loading facts={<p>Kind</p>} />);
      const panel = screen.getByRole('complementary', { name: 'Atlas' });
      const bars = within(panel).getByRole('status', { name: 'Loading' });
      expect(bars.querySelector('.pk-sk-fact')).toBeNull();
      act(() => void vi.advanceTimersByTime(200));
      expect(bars.querySelectorAll('.pk-sk-fact').length).toBeGreaterThan(0);
      expect(within(panel).queryByText('Kind')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
