import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { IconButton } from '@/kit/IconButton';
import { Toasts } from '@/kit/Toast';
import { Button, FillContext } from './Button';

/** A promise the test settles. */
function later<T>() {
  let settle!: (v: T) => void;
  let fail!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    settle = res;
    fail = rej;
  });
  return { promise, settle, fail };
}

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

  it('takes no press while busy', () => {
    const onClick = vi.fn();
    render(<Button busy onClick={onClick}>Save</Button>);
    const b = screen.getByRole('button', { name: 'Save' });
    expect(b).toHaveProperty('disabled', true);
    fireEvent.click(b);
    expect(onClick).not.toHaveBeenCalled();
  });

  it.each([
    ['Button', (onClick: () => Promise<unknown>) => <Button onClick={onClick}>Approve</Button>],
    ['IconButton', (onClick: () => Promise<unknown>) => <IconButton icon="close" label="Approve" onClick={onClick} />],
  ])('%s stays busy from the press until the work it started is answered, and is not pressed twice', async (_kind, draw) => {
    const work = later<unknown>();
    const onClick = vi.fn(() => work.promise);
    render(draw(onClick));
    const b = screen.getByRole('button', { name: 'Approve' });
    fireEvent.click(b);
    expect(b.getAttribute('aria-busy')).toBe('true');
    expect(b).toHaveProperty('disabled', true);
    fireEvent.click(b);
    expect(onClick).toHaveBeenCalledOnce();
    await act(async () => work.settle({ ok: true }));
    expect(b.getAttribute('aria-busy')).toBeNull();
    expect(b).toHaveProperty('disabled', false);
  });

  it('says a refusal the press hands back, and a thrown failure, never neither', async () => {
    const refused = later<unknown>();
    const thrown = later<unknown>();
    render(
      <>
        <Toasts />
        <Button onClick={() => refused.promise}>Remove</Button>
        <Button onClick={() => thrown.promise}>Send</Button>
      </>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    await act(async () => refused.settle({ ok: false, error: 'conflict', why: 'It changed meanwhile' }));
    expect(await screen.findByText('It changed meanwhile')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await act(async () => thrown.fail(new Error('boom')));
    expect(await screen.findByText('That did not work')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Send' })).toHaveProperty('disabled', false);
  });

  it('says nothing more when the action said it, or the press answered for itself', async () => {
    render(
      <>
        <Toasts />
        <Button onClick={() => Promise.resolve({ ok: false, error: 'conflict', told: true })}>Remove</Button>
        <Button onClick={() => Promise.resolve(false)}>Save</Button>
      </>,
    );
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Remove' })));
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Save' })));
    expect(screen.queryByRole('button', { name: 'Remove' })?.getAttribute('aria-busy')).toBeNull();
    expect(document.querySelector('.kit-toast')).toBeNull();
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

  it('draws a primary button plain where the filled button stands elsewhere', () => {
    render(
      <FillContext value={false}>
        <Button variant="primary">Add watch</Button>
      </FillContext>,
    );
    expect(screen.getByRole('button', { name: 'Add watch' }).className).toBe('btn');
  });

  it('counts a part marked as a sheet of its own apart from the page around it', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(
      <div className="pane">
        <Button variant="primary">Approve</Button>
        <figure data-sheet>
          <Button variant="primary">Try again</Button>
        </figure>
      </div>,
    );
    expect(warn).not.toHaveBeenCalled();
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
