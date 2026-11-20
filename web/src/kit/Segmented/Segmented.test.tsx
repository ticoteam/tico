import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Segmented } from './Segmented';

const RANGE = [
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
] as const;

describe('Segmented', () => {
  it('picks a choice and shows which is on', () => {
    const onValueChange = vi.fn();
    render(<Segmented label="Range" options={RANGE} value="week" onValueChange={onValueChange} />);
    expect(screen.getByRole('group', { name: 'Range' }).className).toBe('seg is-words');
    expect(screen.getByRole('button', { name: 'Week' }).getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Month' }));
    expect(onValueChange).toHaveBeenCalledWith('month');
  });

  it('never turns the chosen one off', () => {
    const onValueChange = vi.fn();
    render(<Segmented label="Range" options={RANGE} value="week" onValueChange={onValueChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Week' }));
    expect(onValueChange).not.toHaveBeenCalled();
  });

  it('names icon choices by their label', () => {
    render(
      <Segmented
        label="View"
        options={[{ value: 'list', label: 'List', icon: 'list' }, { value: 'columns', label: 'Columns', icon: 'columns' }]}
        value="list"
        onValueChange={() => {}}
      />,
    );
    expect(screen.getByRole('group', { name: 'View' }).className).toBe('seg');
    expect(screen.getByRole('button', { name: 'Columns' })).toBeTruthy();
  });

  it('does nothing while disabled', () => {
    const onValueChange = vi.fn();
    render(<Segmented label="Range" options={RANGE} value="week" disabled onValueChange={onValueChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Month' }));
    expect(onValueChange).not.toHaveBeenCalled();
  });

  describe('words that do not fit', () => {
    let room = 200;
    let fits: (() => void)[] = [];
    const width = (el: HTMLElement) => (el.getAttribute('role') === 'group' ? 340 : room);
    afterEach(() => {
      vi.unstubAllGlobals();
      vi.restoreAllMocks();
      fits = [];
    });
    const stub = () => {
      vi.stubGlobal(
        'ResizeObserver',
        class {
          constructor(fit: () => void) {
            fits.push(fit);
          }
          observe() {}
          disconnect() {}
        },
      );
      vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockImplementation(function (this: HTMLElement) {
        return width(this);
      });
      vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
        return width(this);
      });
    };

    it('become a select, and the words again once there is room', () => {
      stub();
      room = 200;
      const onValueChange = vi.fn();
      render(
        <div>
          <Segmented label="Effort" options={RANGE} value="week" onValueChange={onValueChange} />
        </div>,
      );
      expect(screen.queryByRole('group', { name: 'Effort' })).toBeNull();
      expect(screen.getByRole('combobox', { name: 'Effort' }).textContent).toContain('Week');
      room = 400;
      act(() => fits.at(-1)?.());
      expect(screen.getByRole('group', { name: 'Effort' })).toBeTruthy();
    });

    it('stay words while they fit', () => {
      stub();
      room = 400;
      render(
        <div>
          <Segmented label="Effort" options={RANGE} value="week" onValueChange={() => {}} />
        </div>,
      );
      expect(screen.getByRole('group', { name: 'Effort' })).toBeTruthy();
      expect(screen.queryByRole('combobox')).toBeNull();
    });
  });
});
