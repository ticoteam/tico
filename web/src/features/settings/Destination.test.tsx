import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { ApiError } from '@/api/client';
import { useDestination } from './Destination';
import { withDestination, type DestinationChoice } from './queries';

const held = () =>
  new ApiError(409, 'destination_change', 'Held', false, null, {
    integration: 'webhook',
    name: 'Sample hook',
    old: { address: 'https://old.example.test' },
    new: { address: 'https://new.example.test' },
    credentials: ['Signing secret'],
    revision: 2,
  });

function Editor({ answers }: { answers: (DestinationChoice | undefined)[] }) {
  const { ask, dialog } = useDestination();
  const [said, setSaid] = useState('');
  const save = () =>
    withDestination(
      async (credentials) => {
        answers.push(credentials);
        if (!credentials) throw held();
        return credentials;
      },
      ask,
    ).then(
      (c) => setSaid(`saved ${c}`),
      (e: Error) => setSaid(e.message),
    );
  return (
    <>
      <button type="button" onClick={() => void save()}>
        Save
      </button>
      <span>{said}</span>
      {dialog}
    </>
  );
}

describe('A held change of address', () => {
  it('asks what happens to the saved credentials and sends again with the answer', async () => {
    const answers: (DestinationChoice | undefined)[] = [];
    render(<Editor answers={answers} />);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Change where Sample hook sends?')).toBeTruthy();
    expect(screen.getByText(/Signing secret was saved for https:\/\/old.example.test/)).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Keep' })));
    expect(await screen.findByText('saved keep')).toBeTruthy();
    expect(answers).toEqual([undefined, 'keep']);
  });

  it('saves nothing on Cancel', async () => {
    const answers: (DestinationChoice | undefined)[] = [];
    render(<Editor answers={answers} />);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    const cancel = await screen.findByRole('button', { name: 'Cancel' });
    await act(async () => fireEvent.click(cancel));
    expect(await screen.findByText('Nothing was saved')).toBeTruthy();
    expect(answers).toEqual([undefined]);
  });
});
