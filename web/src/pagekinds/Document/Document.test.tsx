import { within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { expectHeadAndTabs, showPage, stateTests, TABS } from '@/pagekinds/_parts/test-page';
import { Document } from './Document';

describe('Document', () => {
  it('reads the body as an article, with the outline beside it and the decision under it', async () => {
    const main = await showPage(
      <Document
        title="Spec"
        path="/tasks/t1/spec"
        tabs={TABS}
        body={<p>Reading text</p>}
        side={<a href="#goals">Goals</a>}
        foot={<button type="button">Approve</button>}
      />,
    );
    expectHeadAndTabs(main, 'Spec', '/tasks/t1/spec');
    expect(within(within(main).getByRole('article')).getByText('Reading text')).toBeTruthy();
    expect(within(within(main).getByRole('complementary', { name: 'Outline' })).getByRole('link', { name: 'Goals' })).toBeTruthy();
    expect(within(main).getByRole('button', { name: 'Approve' })).toBeTruthy();
  });

  it('keeps a framed page as it is', async () => {
    const main = await showPage(<Document title="Report" path="/r" body={<iframe title="Report page" />} framed />);
    expect(within(main).queryByRole('article')).toBeNull();
    expect(within(main).getByTitle('Report page')).toBeTruthy();
  });

  stateTests('Spec', (s) => <Document title="Spec" path="/tasks/t1/spec" body={null} {...s} />);
});
