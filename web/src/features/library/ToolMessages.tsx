import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Ago } from '@/kit/Ago';
import { Empty } from '@/kit/Empty';
import { Search } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { FilterRow, type FilterChip } from '@/pagekinds';
import { More } from './parts';
import { inboxesQuery } from './queries';
import { MessageDialog } from './ToolDialogs';
import { Shell, stateOf } from './ToolShell';
import { allMessagesQuery, type InMessage, type MessageFilter } from './toolMail';

/* Messages (`?tool=messages`, old inboxes-texts.ts inAllDraw): what came in to every Tico address and number, or the
   ones picked, narrowed by channel, sender, words, a code or spam. A message opens as plain text in the reader, which
   reveals its code (s932). Filters live on the page while it is open. */

const CHANNEL: Record<'email' | 'sms', string> = { email: 'Email', sms: 'Texts' };

/** Typed words, sent after a short pause so each key is not a read. */
function useSettled(value: string) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), 300);
    return () => clearTimeout(t);
  }, [value]);
  return settled;
}

export function MessagesView() {
  const boxes = useQuery(inboxesQuery);
  const [pick, setPick] = useState<Omit<MessageFilter, 'q' | 'from'>>({});
  const [words, setWords] = useState('');
  const [from, setFrom] = useState('');
  const q = useSettled(words.trim());
  const sender = useSettled(from.trim());
  const list = useInfiniteQuery(allMessagesQuery({ ...pick, q: q || undefined, from: sender || undefined }));
  const [reading, setReading] = useState<InMessage | null>(null);
  const inboxes = boxes.data ?? [];
  const picked = pick.inboxes ?? [];
  const nameOf = (id: string) => inboxes.find((b) => b.inbox_id === id)?.name || inboxes.find((b) => b.inbox_id === id)?.address || id;
  const set = (change: Partial<typeof pick>) => setPick({ ...pick, ...change });
  const toggleInbox = (id: string) => set({ inboxes: picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id] });
  const chips: FilterChip[] = [
    ...picked.map((id) => ({ label: nameOf(id), onClear: () => toggleInbox(id) })),
    ...(pick.kind ? [{ label: CHANNEL[pick.kind], onClear: () => set({ kind: undefined }) }] : []),
    ...(pick.has_code ? [{ label: 'Has a code', onClear: () => set({ has_code: undefined }) }] : []),
    ...(pick.spam ? [{ label: 'Includes spam', onClear: () => set({ spam: undefined }) }] : []),
  ];
  const rows = list.data?.pages.flatMap((p) => p.messages) ?? [];
  const filter = (
    <Menu trigger={<IconButton icon="filter" label="Filter messages" on={chips.length > 0} data-phone />}>
      {inboxes.length > 0 && (
        <MenuSub label="Inbox">
          <MenuItem label="Every inbox" on={!picked.length} onSelect={() => set({ inboxes: undefined })} />
          <MenuRule />
          {inboxes.map((b) => (
            <MenuItem key={b.inbox_id} label={b.name || b.address} meta={b.state === 'released' ? 'Not in use' : undefined} on={picked.includes(b.inbox_id)} onSelect={() => toggleInbox(b.inbox_id)} />
          ))}
        </MenuSub>
      )}
      <MenuSub label="Channel">
        <MenuItem label="Every channel" on={!pick.kind} onSelect={() => set({ kind: undefined })} />
        <MenuItem label="Email" on={pick.kind === 'email'} onSelect={() => set({ kind: 'email' })} />
        <MenuItem label="Texts" on={pick.kind === 'sms'} onSelect={() => set({ kind: 'sms' })} />
      </MenuSub>
      <MenuRule />
      <MenuItem label="Has a code" on={!!pick.has_code} onSelect={() => set({ has_code: !pick.has_code || undefined })} />
      <MenuItem label="Include spam" on={!!pick.spam} onSelect={() => set({ spam: !pick.spam || undefined })} />
    </Menu>
  );
  return (
    <Shell name="Messages" kind="mailbox" fill={filter} state={stateOf(list)} onRetry={() => void list.refetch()} target="tool:messages">
      <div className="msg-find">
        <Search label="Find messages" value={words} onValueChange={setWords} placeholder="Words" />
        <Search label="From" value={from} onValueChange={setFrom} placeholder="From" />
      </div>
      <FilterRow filters={chips} onClearAll={() => setPick({})} />
      {!rows.length && <Empty title={chips.length || q || sender ? 'Nothing matches' : 'No messages yet'} />}
      {rows.map((m) => {
        const what = m.subject || m.preview || (m.retention_removed ? 'Removed after its time' : 'Message');
        return (
          <button key={m.message_id} type="button" className="row urow mrow" title={what} onClick={() => setReading(m)}>
            <span className="row-title">{what}</span>
            {m.has_code && <Pill>Code</Pill>}
            {m.spam && <Pill>Spam</Pill>}
            <span className="row-ctx">{m.from ?? ''}</span>
            <Ago at={new Date(m.received_at * 1000)} className="row-age" />
          </button>
        );
      })}
      {list.hasNextPage && !list.isFetchingNextPage && <More onClick={() => void list.fetchNextPage()}>Older messages</More>}
      {reading && (
        <MessageDialog
          message={reading.message_id}
          title={reading.subject || reading.preview || 'Message'}
          hasCode={!!reading.has_code}
          onClose={() => setReading(null)}
        />
      )}
    </Shell>
  );
}
