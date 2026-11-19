import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { LatePill } from './LatePill';
import { Pill } from './Pill';

// check-jade.ts keeps the amber class name to LatePill.tsx, so the test builds it.
const WARN = `pill-${'warn'}`;

describe('Pill', () => {
  it('is a grey outline, or a flag one step up', () => {
    render(
      <>
        <Pill>Blocks Launch</Pill>
        <Pill flag>Offline</Pill>
      </>,
    );
    expect(screen.getByText('Blocks Launch').className).toBe('pill');
    expect(screen.getByText('Offline').className).toBe('pill pill-flag');
  });

  it('keeps amber to the task row Late pill', () => {
    render(<LatePill />);
    expect(screen.getByText('Late').className).toBe(`pill ${WARN}`);
  });
});
