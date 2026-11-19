import { within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { showPage } from '@/pagekinds/_parts/test-page';
import { CenteredCard } from './CenteredCard';

describe('CenteredCard', () => {
  it('draws one line, its field and button, and the foot', async () => {
    const main = await showPage(
      <CenteredCard
        title="Sign in to Tico"
        path="/signin"
        body={<input aria-label="Email" />}
        actions={<button type="button">Continue</button>}
        foot={<a href="/support">Tico support</a>}
      />,
    );
    expect(within(main).getByRole('heading', { level: 1, name: 'Sign in to Tico' }).getAttribute('data-target')).toBe('page:/signin');
    expect(within(main).getByRole('textbox', { name: 'Email' })).toBeTruthy();
    expect(within(main).getByRole('button', { name: 'Continue' })).toBeTruthy();
    expect(within(main).getByRole('link', { name: 'Tico support' })).toBeTruthy();
  });

  it('leads a fault title with its icon', async () => {
    const main = await showPage(<CenteredCard title="Can't reach Tico" path="/down" fault="alert" />);
    expect(within(main).getByRole('heading', { level: 1, name: "Can't reach Tico" }).className).toBe('is-fault');
  });
});
