import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Avatar } from '@/kit/Avatar';
import { DidLine } from './DidLine';

describe('DidLine', () => {
  it('says what was done beside the done tick, or the icon given', () => {
    const { container } = render(
      <>
        <DidLine text="Opened Finance" />
        <DidLine text="Moved to Friday" icon="calendar" />
      </>,
    );
    const [plain, dated] = [...container.querySelectorAll('.did')];
    expect(plain!.textContent).toBe('Opened Finance');
    expect(plain!.querySelector('.did-ico .ico')).toBeTruthy();
    expect(dated!.querySelector('.did-ico .ico')?.innerHTML).not.toBe(plain!.querySelector('.did-ico .ico')?.innerHTML);
  });

  it('with a face, the face stands in for the mark', () => {
    const { container } = render(<DidLine text="Asked Finance Owner" face={<Avatar kind="agent" name="Finance Owner" size={22} />} />);
    expect(container.querySelector('.did-ico')).toBeNull();
    expect(screen.getByRole('img', { name: 'Finance Owner' }).className).toContain('av-22');
  });

  it('a line that could not be done carries the fault mark, named', () => {
    const { container } = render(<DidLine text="Turn on Two-step" fault />);
    expect(container.querySelector('.did')?.className).toBe('did is-fault');
    expect(screen.getByRole('img', { name: 'Could not do it' })).toBeTruthy();
  });
});
