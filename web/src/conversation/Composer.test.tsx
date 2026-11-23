import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { directoryQuery, queryClient, type Actor, type Directory } from '@/data';
import { Composer } from './Composer';

const ME = 'human:me';
const actors: Actor[] = [
  { id: 'human:me', kind: 'person', name: 'Mia Example' },
  { id: 'human:sam', kind: 'person', name: 'Sam Lee' },
  { id: 'human:rita', kind: 'person', name: 'Rita Relish' },
  { id: 'bot:r1', kind: 'agent', name: 'Release Owner', slug: 'release-owner' },
  { id: 'bot:q1', kind: 'agent', name: 'QA', slug: 'qa' },
  { id: 'bot:old', kind: 'agent', name: 'Retired Relay', slug: 'relay', lifecycle: 'archived' },
];

const box = () => screen.getByRole('textbox', { name: 'Message Tico' }) as HTMLTextAreaElement;
// each row's name, without the avatar's initials
const names = () => screen.queryAllByRole('option').map((o) => o.querySelector(':scope > span:not(.av)')?.textContent);
const type = (value: string) => fireEvent.change(box(), { target: { value } });

function mount(props: Partial<Parameters<typeof Composer>[0]> = {}) {
  const onSend = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <Composer id="c1" me={ME} to="Tico" onSend={onSend} {...props} />
    </QueryClientProvider>,
  );
  return onSend;
}

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData<Directory>(directoryQuery.queryKey, { actors, byId: {} });
});

describe('names after @', () => {
  it('lists the matching teammates as an @word is typed, never the writer or a retired agent', () => {
    mount();
    type('hi there');
    expect(screen.queryByRole('listbox')).toBeNull();
    type('hi @');
    expect(names()).toEqual(['QA', 'Release Owner', 'Rita Relish', 'Sam Lee']);
    type('hi @rel');
    // a name or handle starting with it comes before a word inside one
    expect(names()).toEqual(['Release Owner', 'Rita Relish']);
    type('mail me at me@rel');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('Enter picks the highlighted name in the form the server reads as a mention, and does not send', () => {
    const onSend = mount();
    type('ask @re');
    fireEvent.keyDown(box(), { key: 'ArrowDown' });
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onSend).not.toHaveBeenCalled();
    expect(box().value).toBe('ask @rita ');
    expect(screen.queryByRole('listbox')).toBeNull();
    // with the list gone, Enter sends again
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('ask @rita', []);
  });

  it('Tab or a tap picks an agent by its slug, in the middle of the text', () => {
    mount();
    type('@qa then more');
    box().setSelectionRange(3, 3);
    fireEvent.select(box());
    fireEvent.keyDown(box(), { key: 'Tab' });
    expect(box().value).toBe('@qa  then more');
    type('ping @release');
    fireEvent.click(screen.getByRole('option', { name: 'Release Owner' }));
    expect(box().value).toBe('ping @release-owner ');
  });

  it('Escape closes the list and leaves the text; Enter then sends it', () => {
    const onSend = mount();
    type('hi @sa');
    expect(names()).toEqual(['Sam Lee']);
    fireEvent.keyDown(box(), { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    type('hi @sam');
    expect(screen.queryByRole('listbox')).toBeNull();
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('hi @sam', []);
  });

  it('puts the given people and agents first', () => {
    mount({ mentionFirst: ['human:sam', 'bot:q1'] });
    type('@');
    expect(names()).toEqual(['Sam Lee', 'QA', 'Release Owner', 'Rita Relish']);
  });

  it('shows no list where mentions are off', () => {
    mount({ mentions: false });
    type('@sa');
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});
