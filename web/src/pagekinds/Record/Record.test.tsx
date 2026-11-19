import { within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { expectHeadAndTabs, showPage, stateTests, TABS } from '@/pagekinds/_parts/test-page';
import { Record } from './Record';

describe('Record', () => {
  it('draws the crumb back to its list, facts, the side column and sections', async () => {
    const main = await showPage(
      <Record
        title="Atlas"
        path="/records/products/atlas"
        crumb={[{ label: 'Products', to: '/records/products' }]}
        tabs={TABS}
        facts={<dl aria-label="Facts" />}
        side={<p>Owner</p>}
        sections={<section aria-label="Matters" />}
      />,
    );
    expectHeadAndTabs(main, 'Atlas', '/records/products/atlas');
    expect(within(within(main).getByRole('navigation', { name: 'Breadcrumb' })).getByRole('link', { name: 'Products' })).toBeTruthy();
    expect(within(main).getByRole('link', { name: 'Back to Products' })).toBeTruthy();
    expect(within(within(main).getByRole('complementary', { name: 'Details' })).getByText('Owner')).toBeTruthy();
    expect(within(main).getByRole('region', { name: 'Matters' })).toBeTruthy();
  });

  stateTests('Atlas', (s) => <Record title="Atlas" path="/records/products/atlas" {...s} />);
});
