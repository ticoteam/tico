import { fireEvent, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { FilterRow, Section, SidePanel } from '@/pagekinds/_parts';
import { expectHeadAndTabs, showPage, stateTests, TABS } from '@/pagekinds/_parts/test-page';
import { List, pendingTitle } from './List';

describe('List', () => {
  it('while reading, titles a page by its own name where the address gives it', () => {
    expect(pendingTitle('/library/docs')).toBe('Docs');
    expect(pendingTitle('/company/risk/disputes')).toBe('Risk');
    expect(pendingTitle('/library/d-123')).toBe('Library');
    expect(pendingTitle('/settings/people')).toBe('Settings');
    expect(pendingTitle('/')).toBe('Tico');
  });

  it('draws the head, the tabs as links, the filter row, sections and the panel', async () => {
    const main = await showPage(
      <List
        title="Products"
        path="/records/products"
        tabs={TABS}
        filter={<FilterRow filters={[{ label: 'Late', onClear: () => {} }]} />}
        sections={
          <Section name="Active" count={1}>
            <a href="/x" data-target="product:1">
              First product
            </a>
          </Section>
        }
        panel={
          <SidePanel title="First product" onClose={() => {}}>
            Facts
          </SidePanel>
        }
      />,
    );
    expectHeadAndTabs(main, 'Products', '/records/products');
    expect(within(main).getByRole('group', { name: 'Filters' })).toBeTruthy();
    const section = within(main).getByRole('region', { name: 'Active' });
    expect(within(section).getByRole('link', { name: 'First product' }).getAttribute('data-target')).toBe('product:1');
    expect(within(main).getByRole('complementary', { name: 'First product' })).toBeTruthy();
  });

  it('opens its panel on an empty list, the empty state still in the body', async () => {
    const main = await showPage(
      <List
        title="Routines"
        path="/routines"
        state="empty"
        empty={{ title: 'No due dates' }}
        sections={null}
        panel={
          <SidePanel title="New due date" onClose={() => {}}>
            Form
          </SidePanel>
        }
      />,
    );
    expect(within(main).getByRole('heading', { name: 'No due dates' })).toBeTruthy();
    expect(within(main).getByRole('complementary', { name: 'New due date' })).toBeTruthy();
  });

  it('folds a section from its name', async () => {
    const main = await showPage(
      <List title="Tasks" path="/tasks" sections={<Section name="Review">Row</Section>} />,
    );
    fireEvent.click(within(main).getByRole('button', { name: /Review/ }));
    expect(await within(main).findByRole('button', { name: /Review/, expanded: false })).toBeTruthy();
  });

  stateTests('Products', (s) => <List title="Products" path="/records/products" sections={null} {...s} />);
});
