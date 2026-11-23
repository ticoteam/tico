import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest';
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
  { id: 'bot:9f2c', kind: 'agent', name: 'Payroll' },
];

// by its label: the box is a combobox while its names are open, a text box otherwise
const box = () => screen.getByLabelText('Message Tico') as HTMLTextAreaElement;
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
    expect(names()).toEqual(['Payroll', 'QA', 'Release Owner', 'Rita Relish', 'Sam Lee']);
    expect(box().getAttribute('role')).toBe('combobox');
    expect(box().getAttribute('aria-expanded')).toBe('true');
    type('hi @rel');
    // a name or handle starting with it comes before a word inside one
    expect(names()).toEqual(['Release Owner', 'Rita Relish']);
    type('mail me at me@rel');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(screen.getByRole('textbox', { name: 'Message Tico' })).toBe(box());
  });

  it('an agent with no slug is written by its name, which the server also reads', () => {
    mount();
    type('ask @pay');
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(box().value).toBe('ask @Payroll ');
  });

  it('a list closed in one conversation does not stay closed in the next one the box shows', () => {
    const onSend = vi.fn();
    const at = (id: string) => (
      <QueryClientProvider client={queryClient}>
        <Composer id={id} me={ME} to="Tico" onSend={onSend} />
      </QueryClientProvider>
    );
    localStorage.clear();
    const { rerender } = render(at('c1'));
    type('hi @sa');
    fireEvent.keyDown(box(), { key: 'Escape' });
    rerender(at('c2'));
    type('yo @sa');
    // the @ at the same place in the other conversation is not the one closed in the first
    expect(names()).toEqual(['Sam Lee']);
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
    expect(box().value).toBe('@qa then more');
    expect(box().selectionStart).toBe(4);
    type('ping @release');
    fireEvent.click(screen.getByRole('option', { name: 'Release Owner' }));
    expect(box().value).toBe('ping @release-owner ');
  });

  it('a pick with the caret inside an @word replaces the whole word', () => {
    mount();
    type('Ask @release tomorrow');
    box().setSelectionRange(8, 8);
    fireEvent.select(box());
    fireEvent.click(screen.getByRole('option', { name: 'Rita Relish' }));
    expect(box().value).toBe('Ask @rita tomorrow');
  });

  it('a pick ends the replaced word at the first character no name has, never eating the next @mention', () => {
    mount();
    type('@release.@sam then');
    box().setSelectionRange(4, 4);
    fireEvent.select(box());
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(box().value).toBe('@release-owner @sam then');
    // a selection running past the caret goes too
    type('Ask @re!');
    box().setSelectionRange(6, 7);
    fireEvent.select(box());
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(box().value).toBe('Ask @release-owner !');
  });

  it('names only the given members where only they can be reached', () => {
    mount({ mentionOnly: ['bot:q1'] });
    type('@');
    expect(names()).toEqual(['QA']);
  });

  it('keeps the row under the arrow keys in view', () => {
    const seen = vi.fn();
    const own = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = seen;
    onTestFinished(() => {
      Element.prototype.scrollIntoView = own;
    });
    mount();
    type('@');
    fireEvent.keyDown(box(), { key: 'ArrowUp' });
    expect(seen.mock.contexts.at(-1)).toBe(screen.getByRole('option', { name: 'Sam Lee' }));
    expect(seen).toHaveBeenLastCalledWith({ block: 'nearest' });
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
    expect(names()).toEqual(['Sam Lee', 'QA', 'Payroll', 'Release Owner', 'Rita Relish']);
  });

  it('shows no list where mentions are off', () => {
    mount({ mentions: false });
    type('@sa');
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});
