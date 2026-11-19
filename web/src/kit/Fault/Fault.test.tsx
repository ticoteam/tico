import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Pill } from '@/kit/Pill';
import { Fault } from './Fault';

describe('Fault', () => {
  it('is one named mark with a solid shape and a cut-out stroke', () => {
    render(<Fault label="Failed" />);
    const mark = screen.getByRole('img', { name: 'Failed' });
    expect(mark.className).toBe('fmark');
    expect(mark.querySelector('.fm-a')).not.toBeNull();
    expect(mark.querySelector('.fm-b')).not.toBeNull();
  });

  it('sits in a flag pill at 12px', () => {
    render(
      <Pill flag mark={<Fault label="Stopped" size={12} />}>
        Stopped
      </Pill>,
    );
    const mark = screen.getByRole('img', { name: 'Stopped' });
    expect(mark.style.getPropertyValue('--f')).toBe('12px');
    expect(mark.parentElement?.className).toBe('pill pill-flag');
  });
});
