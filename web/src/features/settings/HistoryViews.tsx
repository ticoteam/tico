import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { run } from '@/actions/run';
import { directoryQuery } from '@/data';
import { Ago } from '@/kit/Ago';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Select } from '@/kit/Select';
import { Section } from '@/pagekinds';
import { Foot, Verb, isForbidden } from './parts';
import { historyPage, historyParams, type HistoryItem } from './queries';

/* Settings › History (old app ui/app/settings-history.ts), one list as the mock draws it: what happened, by whom,
   filtered by who (in the address), a page at a time (Show older follows the server's cursor), with its CSV. */

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

const EVERYONE = '';

/** A read that failed: no access says so; anything else offers Try again. */
function Failed({ error, what, retry }: { error: unknown; what: string; retry: () => void }) {
  if (isForbidden(error)) return <Empty icon="lock" title="Owners and admins only" />;
  return (
    <Empty icon="alert" title={`${what} did not load`}>
      <Button onClick={retry}>Try again</Button>
    </Empty>
  );
}

function LogRows({ items, names }: { items: HistoryItem[]; names: Record<string, string> }) {
  const dir = useQuery(directoryQuery);
  return items.map((it) => {
    const actor = dir.data?.byId[it.actor];
    const name = actor?.name ?? names[it.actor] ?? it.actor;
    const title = [it.subject, it.result].filter(Boolean).join(': ') || (KIND[it.kind] ?? it.kind);
    return (
      <div key={it.id} className="row g-chg" title={`${name}, ${title}`}>
        <Avatar kind={actor?.kind ?? (it.actor.startsWith('bot:') ? 'agent' : 'person')} name={name} hue={actor?.hue} decorative />
        <span className="row-title">
          {title}
          <span className="row-sub"> {KIND[it.kind] ?? it.kind}</span>
        </span>
        <Ago at={it.at} className="row-age" />
      </div>
    );
  });
}

function Older({ onClick }: { onClick: () => void }) {
  return (
    <Foot open>
      <Verb onClick={onClick}>Show older</Verb>
    </Foot>
  );
}

/** A page after the first, by the cursor the page before gave. */
function LogPage({ who, before, last, onMore }: { who?: string; before: string; last: boolean; onMore: (next: string) => void }) {
  const p = useQuery(historyPage(who, before));
  if (p.isError) return <Failed error={p.error} what="History" retry={() => void p.refetch()} />;
  if (!p.data) return null;
  const next = p.data.next;
  return (
    <>
      <LogRows items={p.data.items ?? []} names={p.data.names ?? {}} />
      {last && next && <Older onClick={() => onMore(next)} />}
    </>
  );
}

/** `who` (the address's `?who=`): one person's or agent's history. */
export function History({ who }: { who?: string }) {
  return <Log key={who ?? ''} who={who} />;
}

function Log({ who }: { who?: string }) {
  const first = useQuery(historyPage(who));
  const dir = useQuery(directoryQuery);
  const [older, setOlder] = useState<string[]>([]);
  const h = first.data;
  const people = [
    { value: EVERYONE, label: 'Everyone' },
    ...(dir.data?.actors ?? []).map((a) => ({ value: a.id, label: a.name, meta: a.kind === 'agent' ? 'Agent' : undefined })),
  ];
  const more = (next: string) => setOlder((o) => (o.includes(next) ? o : [...o, next]));
  return (
    <Section name="History" count={h?.items.length} target="setting:history">
      <div className="u-tools">
        <Select
          label="Who"
          labelHidden
          look="quiet"
          value={who ?? EVERYONE}
          options={people}
          onValueChange={(v) => void run('page.filter', { search: { who: v || null } }, { via: 'click' })}
        />
      </div>
      {first.isError ? (
        <Failed error={first.error} what="History" retry={() => void first.refetch()} />
      ) : !h ? null : h.items.length === 0 ? (
        <Empty title="Nothing yet" />
      ) : (
        <>
          <LogRows items={h.items} names={h.names ?? {}} />
          {older.length === 0 && h.next && <Older onClick={() => more(h.next ?? '')} />}
          {older.map((before, i) => (
            <LogPage key={before} who={who} before={before} last={i === older.length - 1} onMore={more} />
          ))}
        </>
      )}
      <Foot>
        <a className="verb" href={`/api/v2/history.csv${historyParams(who)}`} download>
          Download CSV
        </a>
      </Foot>
    </Section>
  );
}
