import { useQueries } from '@tanstack/react-query';
import { useState } from 'react';
import { run } from '@/actions/run';
import { Ago } from '@/kit/Ago';
import { Avatar } from '@/kit/Avatar';
import { Empty } from '@/kit/Empty';
import { Segmented } from '@/kit/Segmented';
import { VirtualList } from '@/kit/VirtualList';
import { fmtDate } from '@/lib';
import { useFaces } from './parts';
import { historyPage, type HistoryItem } from './queries';

/* Settings › History (t-settings-history): one list, newest first, Everyone | People | Agents (in the address as
   ?who=), the next page read when the list reaches its end. The first page is the page's read: its loading, fault
   and no access are the page's. */

export type HistoryWho = 'people' | 'agents';

const KIND: Record<string, string> = {
  run: 'Run',
  task: 'Task',
  'approval.asked': 'Approval asked',
  'approval.granted': 'Approval granted',
  'approval.denied': 'Approval denied',
  'outbound.blocked': 'Outbound blocked',
  'credential.revealed': 'Credential revealed',
  'permission.changed': 'Permission change',
  decision: 'Decision',
};

const WHO = [
  { value: 'everyone', label: 'Everyone' },
  { value: 'people', label: 'People' },
  { value: 'agents', label: 'Agents' },
] as const;

export function History({ who }: { who?: HistoryWho }) {
  const faces = useFaces();
  // The cursors of the pages after the first, each its own read.
  const [cursors, setCursors] = useState<string[]>([]);
  const pages = useQueries({ queries: ['', ...cursors].map((before) => historyPage(before)) });
  const names: Record<string, string> = Object.assign({}, ...pages.map((p) => p.data?.names ?? {}));
  const all = pages.flatMap((p) => p.data?.items ?? []);
  const items = who ? all.filter((it) => (who === 'agents') === it.actor.startsWith('bot:')) : all;
  const last = pages.at(-1);
  const next = last?.data?.next;
  const more = () => {
    if (!next || last?.isFetching) return;
    if (last?.isError) void last.refetch();
    else setCursors((c) => (c.includes(next) ? c : [...c, next]));
  };
  const row = (it: HistoryItem) => {
    const actor = faces[it.actor];
    const name = actor?.name ?? names[it.actor] ?? it.actor;
    const title = [it.subject, it.result].filter(Boolean).join(': ') || (KIND[it.kind] ?? it.kind);
    return (
      <div className="row g-chg" title={`${name}, ${title}`}>
        <Avatar kind={actor?.kind ?? (it.actor.startsWith('bot:') ? 'agent' : 'person')} name={name} hue={actor?.hue} decorative />
        <span className="row-title">
          <b>{name}</b> {title}
        </span>
        <Ago at={it.at} className="row-age" />
      </div>
    );
  };
  return (
    <>
      <div className="u-tools" data-target="setting:history">
        <Segmented
          label="Who"
          options={WHO}
          value={who ?? 'everyone'}
          onValueChange={(v) => void run('page.filter', { search: { who: v === 'everyone' ? null : v } }, { via: 'click' })}
        />
        {all.length > 0 && <span className="u-since">Since {fmtDate(all.at(-1)!.at)}</span>}
      </div>
      {items.length === 0 ? (
        all.length > 0 || pages[0]?.data ? <Empty title="Nothing yet" /> : null
      ) : (
        <VirtualList className="set-vl" label="History" items={items} getKey={(it) => it.id} estimateSize={34} renderItem={row} onReachEnd={more} />
      )}
    </>
  );
}
