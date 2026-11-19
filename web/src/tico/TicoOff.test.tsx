import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
import { dot } from '@/tico/dot';
import { useTicoPower } from './power';
import { ticoPowerQuery, type TicoPower } from './queries';
import { TicoOff } from './TicoOff';

const release = vi.fn();
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => release), clear: vi.fn(), el: () => null } }));
vi.mock('@/actions', async (orig) => ({ ...(await orig<object>()), run: vi.fn() }));

afterEach(() => vi.clearAllMocks());

describe('Tico is off @s216', () => {
  it('gives the owner one Turn on, and says when Tico still needs a computer', async () => {
    vi.mocked(run).mockResolvedValue({ ok: true, out: 'planned' });
    render(<TicoOff canTurnOn />);
    expect(screen.getByRole('heading', { name: 'Tico is off' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Turn on' }));
    expect(vi.mocked(run)).toHaveBeenCalledWith('tico.turnOn', {}, { via: 'click' });
    expect(await screen.findByText('Tico needs a computer before it can answer.')).toBeTruthy();
  });

  it('shows everyone else the plain state, with no button', () => {
    render(<TicoOff canTurnOn={false} />);
    expect(screen.getByText('Ask the owner to turn it on.')).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('holds the Dot off while Tico is off, once, and lets go when it is back', async () => {
    const client = new QueryClient();
    const set = (p: TicoPower) => act(() => void client.setQueryData(ticoPowerQuery.queryKey, p));
    client.setQueryData(ticoPowerQuery.queryKey, { available: false, can_turn_on: true, state: 'archived' });
    const Probe = () => (useTicoPower(), null);
    render(
      <QueryClientProvider client={client}>
        <Probe />
        <Probe />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(vi.mocked(dot.set)).toHaveBeenCalledWith('off'));
    expect(vi.mocked(dot.set)).toHaveBeenCalledTimes(1);
    set({ available: true, can_turn_on: false, state: 'active' });
    await waitFor(() => expect(release).toHaveBeenCalledTimes(1));
  });
});
