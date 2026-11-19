import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Button } from './Button';

afterEach(() => vi.restoreAllMocks());

describe('Button', () => {
  it('presses, and the filled variant is primary', () => {
    const onClick = vi.fn();
    render(<Button variant="primary" onClick={onClick}>Delete task</Button>);
    const b = screen.getByRole('button', { name: 'Delete task' });
    expect(b.className).toBe('btn btn-fill');
    fireEvent.click(b);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('does nothing while disabled', () => {
    const onClick = vi.fn();
    render(<Button disabled onClick={onClick}>Save</Button>);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(onClick).not.toHaveBeenCalled();
  });

  it('says it is busy without a spinner', () => {
    render(<Button busy>Save</Button>);
    const b = screen.getByRole('button', { name: 'Save' });
    expect(b.getAttribute('aria-busy')).toBe('true');
    expect(b.className).not.toContain('is-busy');
  });

  it('warns once when a sheet holds two filled buttons', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(
      <div className="pane">
        <Button variant="primary">Save</Button>
        <Button variant="primary">Send</Button>
      </div>,
    );
    expect(warn).toHaveBeenCalledOnce();
  });

  it('stays quiet with one filled button to a sheet', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(
      <>
        <div className="pane"><Button variant="primary">Save</Button><Button>Cancel</Button></div>
        <div className="pane"><Button variant="primary">Send</Button></div>
      </>,
    );
    expect(warn).not.toHaveBeenCalled();
  });
});
