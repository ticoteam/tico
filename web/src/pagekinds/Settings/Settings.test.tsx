import { within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { showPage, stateTests } from '@/pagekinds/_parts/test-page';
import { Settings } from './Settings';

describe('Settings', () => {
  it('puts the section list in its own navigation and the section beside it', async () => {
    const main = await showPage(
      <Settings
        title="Settings"
        path="/settings/general"
        nav={
          <a href="/settings/general" className="row set-link" aria-current="page">
            General
          </a>
        }
        body={<label>Company name<input /></label>}
        foot={<button type="button">Delete company</button>}
        open
      />,
    );
    expect(within(main).getByRole('heading', { level: 1, name: 'Settings' }).getAttribute('data-target')).toBe('page:/settings/general');
    const nav = within(main).getByRole('navigation', { name: 'Sections' });
    expect(within(nav).getByRole('link', { name: 'General' }).getAttribute('aria-current')).toBe('page');
    expect(within(main).getByRole('textbox', { name: 'Company name' })).toBeTruthy();
    expect(within(main).getByRole('button', { name: 'Delete company' })).toBeTruthy();
  });

  stateTests('Settings', (s) => <Settings title="Settings" path="/settings" nav={null} body={null} {...s} />);
});
