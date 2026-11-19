import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Empty } from './Empty';

describe('Empty', () => {
  it('shows the stacked bars and one line for an empty list', () => {
    const { container } = render(<Empty title="Nothing in Review" />);
    expect(screen.getByRole('heading', { name: 'Nothing in Review' })).toBeTruthy();
    expect(container.querySelector('.empty-art')?.getAttribute('aria-hidden')).toBe('true');
    expect(container.querySelector('.q-row')).toBeNull();
  });

  it('shows an icon, a sentence and the way out when something failed', () => {
    const { container } = render(
      <Empty title="Finance did not load" icon="alert" text="The bank did not answer.">
        <button type="button">Try again</button>
      </Empty>,
    );
    expect(container.querySelector('.empty-ico .ico')).not.toBeNull();
    expect(screen.getByText('The bank did not answer.').tagName).toBe('P');
    expect(screen.getByRole('button', { name: 'Try again' }).parentElement?.className).toBe('q-row');
  });
});
