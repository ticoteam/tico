import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Section, SectionHead } from './SectionHead';

describe('SectionHead', () => {
  it('folds a section from its name, with the count before the chevron', () => {
    render(
      <Section name="In review" count={3}>
        <p>Row one</p>
      </Section>,
    );
    const fold = screen.getByRole('button', { name: /In review/ });
    expect(fold.getAttribute('aria-expanded')).toBe('true');
    expect(Array.from(fold.children).map((c) => c.getAttribute('class'))).toEqual([null, 'stage-n', 'ico']);
    expect(screen.getByText('Row one')).toBeTruthy();

    fireEvent.click(fold);
    expect(fold.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByText('Row one')).toBeNull();
    expect(fold.closest('section')?.className).toBe('stage is-folded');
  });

  it('names a section in an h2 and a group one step down in an h3', () => {
    render(
      <>
        <SectionHead name="Today" count={4} />
        <SectionHead name="Morning" level={2} />
      </>,
    );
    expect(screen.getByRole('heading', { level: 2, name: /Today/ }).className).toBe('sec-name');
    expect(screen.getByRole('heading', { level: 3, name: 'Morning' }).className).toBe('sec-name is-2');
  });
});
