import { QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest';
import { directoryQuery, queryClient, type Actor, type Directory } from '@/data';
import { Toasts } from '@/kit/Toast';
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

  it('a list closed in one message does not stay closed in the next', () => {
    const onSend = mount();
    type('hi @sa');
    fireEvent.keyDown(box(), { key: 'Escape' });
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('hi @sa', []);
    // the next message's @ sits where the closed one did
    type('yo @sa');
    expect(names()).toEqual(['Sam Lee']);
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
    expect(box().value).toBe('Ask @release-owner!');
  });

  it('a pick keeps the punctuation after the @word and adds no space before it', () => {
    mount();
    type('Ask @sa, then go');
    box().setSelectionRange(7, 7);
    fireEvent.select(box());
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(box().value).toBe('Ask @sam, then go');
    expect(box().selectionStart).toBe(8);
    type('Thanks @sa.');
    box().setSelectionRange(10, 10);
    fireEvent.select(box());
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(box().value).toBe('Thanks @sam.');
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

describe('files', () => {
  const made: string[] = [];
  const freed: string[] = [];
  beforeEach(() => {
    made.length = 0;
    freed.length = 0;
    Object.assign(URL, {
      createObjectURL: () => {
        made.push(`blob:${made.length}`);
        return made.at(-1);
      },
      revokeObjectURL: (u: string) => freed.push(u),
    });
  });
  const file = (name: string, type: string, size = 3) => {
    const f = new File(['abc'], name, { type });
    if (size !== 3) Object.defineProperty(f, 'size', { value: size });
    return f;
  };
  const paste = (files: File[]) => fireEvent.paste(box(), { clipboardData: { files, types: ['Files'] } });
  const drop = (files: File[]) => fireEvent.drop(box(), { dataTransfer: { files, types: ['Files'] } });
  const tray = () => screen.queryAllByRole('button', { name: /^Remove / }).map((b) => b.getAttribute('aria-label'));

  it('takes pasted and dropped files into the tray, a picture as itself, and sends them', () => {
    const onSend = mount();
    render(<Toasts />);
    const shot = file('image.png', 'image/png');
    paste([shot]);
    const pdf = file('brief.pdf', 'application/pdf');
    drop([pdf]);
    // a drop on the tray is the box's too
    const notes = file('notes.txt', 'text/plain');
    fireEvent.drop(screen.getByRole('button', { name: 'Remove brief.pdf' }), { dataTransfer: { files: [notes], types: ['Files'] } });
    fireEvent.click(screen.getByRole('button', { name: 'Remove notes.txt' }));
    expect(tray()).toEqual(['Remove image.png', 'Remove brief.pdf']);
    expect(document.querySelector('.composer-thumb img')?.getAttribute('src')).toBe('blob:0');
    // a press removes it, and frees its picture
    fireEvent.click(screen.getByRole('button', { name: 'Remove image.png' }));
    expect(freed).toEqual(['blob:0']);
    paste([shot]);
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('', [pdf, shot]);
    expect(tray()).toEqual([]);
    expect(freed).toEqual(['blob:0', 'blob:1']);
  });

  it('keeps to the server caps and says which one left a file out', async () => {
    mount();
    render(<Toasts />);
    drop([file('huge.mov', 'video/quicktime', 10_000_001)]);
    expect(await screen.findByText('Files up to 10 MB')).toBeTruthy();
    expect(tray()).toEqual([]);
    paste(Array.from({ length: 12 }, (_, i) => file(`${i}.txt`, 'text/plain')));
    expect(await screen.findByText('Up to 10 files')).toBeTruthy();
    expect(tray()).toHaveLength(10);
    cleanup();
    mount();
    render(<Toasts />);
    // two files of 9.9 MB pass the per-file cap but not the request's
    drop([file('a.bin', '', 9_900_000), file('b.bin', '', 9_900_000)]);
    expect(await screen.findByText('Up to 19 MB a message')).toBeTruthy();
    expect(tray()).toEqual(['Remove a.bin']);
  });

  it('sends files only with words where the box needs them (the Assistant)', () => {
    mount({ needsText: true });
    paste([file('image.png', 'image/png')]);
    const send = screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    type('What is this?');
    expect(send.disabled).toBe(false);
  });

  it('frees the pictures when the box goes', () => {
    mount();
    paste([file('a.png', 'image/png'), file('b.webp', 'image/webp')]);
    cleanup();
    expect(freed.sort()).toEqual(['blob:0', 'blob:1']);
  });

  it('takes no files where it attaches none: a paste stays the browser\'s', () => {
    mount({ attach: false });
    const pasted = paste([file('image.png', 'image/png')]);
    drop([file('brief.pdf', 'application/pdf')]);
    expect(tray()).toEqual([]);
    // not prevented: the browser pastes what it can as words
    expect(pasted).toBe(true);
  });
});
