import { within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { FiguresStrip } from '@/pagekinds/_parts';
import { expectHeadAndTabs, showPage, stateTests, TABS } from '@/pagekinds/_parts/test-page';
import { Dashboard } from './Dashboard';

describe('Dashboard', () => {
  it('draws the figures strip, the chart and the widgets', async () => {
    const main = await showPage(
      <Dashboard
        title="Growth"
        path="/company/growth"
        tabs={TABS}
        figures={<FiguresStrip figures={[{ label: 'Visits', value: '1,200', target: 'figure:visits' }]} />}
        chart={<svg role="img" aria-label="Visits by week" />}
        widgets={<section aria-label="Funnel" />}
      />,
    );
    expectHeadAndTabs(main, 'Growth', '/company/growth');
    const figs = within(main).getByRole('group', { name: 'Figures' });
    expect(within(figs).getByText('1,200')).toBeTruthy();
    expect(within(main).getByRole('img', { name: 'Visits by week' })).toBeTruthy();
    expect(within(main).getByRole('region', { name: 'Funnel' })).toBeTruthy();
  });

  stateTests('Growth', (s) => <Dashboard title="Growth" path="/company/growth" {...s} />);
});
