import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { refusal } from '@/actions';
import { run } from '@/actions/run';
import { directoryQuery, meQuery } from '@/data';
import { Ago } from '@/kit/Ago';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { Section } from '@/pagekinds';
import { Foot, Verb, isForbidden } from './parts';
import { changesQuery, historyPage, historyParams, runsQuery, type HistoryItem, type RunRow, type SettingsChange } from './queries';

/* Settings › History (old app ui/app/settings-history.ts): three views. Log: what happened, by whom, filtered by who
   and kind, a page at a time (Show older follows the server's cursor), with its CSV. Runs: the newest runs, or the
   day's failed ones, each with its log. Changes (the owner's): each change to an agent's settings, with Undo. The view
   and the kind are the section's own; `who` is in the address. */

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

const KINDS = [
  { value: '', label: 'Everything' },
  { value: 'run', label: 'Runs' },
  { value: 'task', label: 'Tasks' },
  { value: 'approval.asked', label: 'Approvals asked' },
  { value: 'approval.granted', label: 'Approvals granted' },
  { value: 'approval.denied', label: 'Approvals denied' },
  { value: 'outbound.blocked', label: 'Outbound blocked' },
  { value: 'credential.revealed', label: 'Credential reveals' },
  { value: 'permission.changed', label: 'Permission changes' },
  { value: 'decision', label: 'Decisions' },
];

type View = 'log' | 'runs' | 'changes';
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

/** `who` (the address's `?who=`): one person's or agent's history. */
export function History({ who }: { who?: string }) {
  const me = useQuery(meQuery);
  const owner = me.data?.role === 'owner';
  const [view, setView] = useState<View>('log');
  const [kind, setKind] = useState('');
  const shown: View = view === 'changes' && !owner ? 'log' : view;
  const pick = (
    <Segmented
      label="View"
      options={[{ value: 'log', label: 'Log' }, { value: 'runs', label: 'Runs' }, ...(owner ? [{ value: 'changes', label: 'Changes' }] : [])]}
      value={shown}
      onValueChange={(v) => setView(v as View)}
    />
  );
  if (shown === 'runs') return <Runs pick={pick} />;
  if (shown === 'changes') return <Changes pick={pick} />;
  return <Log key={`${who ?? ''}|${kind}`} who={who} kind={kind} onKind={setKind} pick={pick} />;
}

/* ---------------------------------------------------------------- Log */

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
function LogPage({ who, kind, before, last, onMore }: { who?: string; kind: string; before: string; last: boolean; onMore: (next: string) => void }) {
  const p = useQuery(historyPage(who, kind, before));
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

function Log({ who, kind, onKind, pick }: { who?: string; kind: string; onKind: (k: string) => void; pick: ReactNode }) {
  const first = useQuery(historyPage(who, kind));
  const dir = useQuery(directoryQuery);
  const [older, setOlder] = useState<string[]>([]);
  const h = first.data;
  const people = [
    { value: EVERYONE, label: 'Everyone' },
    ...(dir.data?.actors ?? []).map((a) => ({ value: a.id, label: a.name, meta: a.kind === 'agent' ? 'Agent' : undefined })),
  ];
  const more = (next: string) => setOlder((o) => (o.includes(next) ? o : [...o, next]));
  return (
    <Section name="History" count={h?.items.length} actions={pick} target="setting:history">
      <div className="u-tools">
        <Select
          label="Who"
          labelHidden
          look="quiet"
          value={who ?? EVERYONE}
          options={people}
          onValueChange={(v) => void run('page.filter', { search: { who: v || null } }, { via: 'click' })}
        />
        <Select label="Kind" labelHidden look="quiet" value={kind} options={KINDS} onValueChange={onKind} />
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
            <LogPage key={before} who={who} kind={kind} before={before} last={i === older.length - 1} onMore={more} />
          ))}
        </>
      )}
      <Foot>
        <a className="verb" href={`/api/v2/history.csv${historyParams(who, kind)}`} download>
          Download CSV
        </a>
      </Foot>
    </Section>
  );
}

/* ---------------------------------------------------------------- Runs */

const took = (s?: number | null) => (s == null ? '' : s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`);

/** A run's result in a word or two: Running, Done, Didn't start, or its outcome. */
export function runResult(r: RunRow): string {
  if (!r.finished_at && r.exit == null && !r.outcome && !r.didnt_start) return 'Running';
  if (r.exit === 0 && !r.didnt_start) return 'Done';
  if (r.didnt_start) return "Didn't start";
  return r.outcome || (r.exit != null ? `Exit ${r.exit}` : r.state);
}

function Runs({ pick }: { pick: ReactNode }) {
  const [failed, setFailed] = useState(false);
  const runs = useQuery(runsQuery(failed));
  const dir = useQuery(directoryQuery);
  const list = Array.isArray(runs.data) ? runs.data : [];
  return (
    <Section name="Runs" count={runs.data ? list.length : undefined} actions={pick} target="setting:runs">
      <div className="u-tools">
        <Select
          label="Show"
          labelHidden
          look="quiet"
          value={failed ? 'failed' : 'all'}
          options={[
            { value: 'all', label: 'All runs' },
            { value: 'failed', label: 'Failed today' },
          ]}
          onValueChange={(v) => setFailed(v === 'failed')}
        />
      </div>
      {runs.isError ? (
        <Failed error={runs.error} what="Runs" retry={() => void runs.refetch()} />
      ) : !runs.data ? null : list.length === 0 ? (
        <Empty title={failed ? 'No failed runs today' : 'No runs yet'} />
      ) : (
        list.map((r, i) => {
          const who = dir.data?.byId[`bot:${r.employee}`] ?? dir.data?.byId[r.employee];
          const name = who?.name ?? r.employee;
          const result = runResult(r);
          const model = r.ran_on?.segments?.at(-1)?.model ?? '';
          const cost = r.cost_usd != null ? `$${r.cost_usd.toFixed(2)}` : '';
          return (
            <div key={r.id ?? i} className={result === 'Done' || result === 'Running' ? 'row g-run' : 'row g-run is-bad'} title={`${name}, ${result}`}>
              <Avatar kind="agent" name={name} hue={who?.hue} decorative />
              <span className="row-title">
                {name}
                <span className="row-sub"> {result}</span>
              </span>
              <span className="row-cell r-model">{model}</span>
              <span className="row-fig r-took">{took(r.duration_s)}</span>
              <span className="row-fig r-cost">{cost}</span>
              {r.finished_at ? <Ago at={r.finished_at} className="row-age" /> : <span />}
              {r.id ? (
                <a className="verb" href={`/api/runs/${encodeURIComponent(r.id)}/log`} target="_blank" rel="noreferrer">
                  Log
                </a>
              ) : (
                <span />
              )}
            </div>
          );
        })
      )}
    </Section>
  );
}

/* ---------------------------------------------------------------- Changes (the owner's) */

const FIELD: Record<string, string> = {
  owners: 'who it works for',
  co_owners: 'co-owners',
  access: 'access',
  model: 'model',
  placement: 'computer',
  fallback: 'fallback',
  compact_at: 'compact at',
  output_style: 'output style',
  verbosity: 'verbosity',
  profile: 'profile',
  turns_at_once: 'turns at once',
  definition: 'setup',
  task_model: 'task model',
  identity: 'name and description',
  manages_reports: 'manages its reports',
  lifecycle: 'archive or restore',
};

function ChangeRows({ changes }: { changes: SettingsChange[] }) {
  const dir = useQuery(directoryQuery);
  const [busy, setBusy] = useState('');
  const undo = async (c: SettingsChange) => {
    setBusy(c.id);
    const r = await run('settings.undoChange', { change: c.id }, { via: 'click' });
    setBusy('');
    toast(r.ok ? { text: r.did ?? 'Undone', kind: 'done' } : refusal(r, "Couldn't undo"));
  };
  return changes.map((c) => {
    const agent = dir.data?.byId[`bot:${c.bot}`] ?? dir.data?.byId[c.bot];
    const name = agent?.name ?? c.bot;
    const by = dir.data?.byId[c.actor]?.name ?? c.actor.replace(/^(human|bot):/, '');
    const title = `${name} · ${FIELD[c.field] ?? c.field}`;
    return (
      <div key={c.id} className="row g-set" title={title}>
        <Avatar kind="agent" name={name} hue={agent?.hue} decorative />
        <span className="row-title">
          {title}
          <span className="row-sub">
            {' '}
            {c.via === 'report_manager' ? `${by}, as its manager` : by}
            {c.undone_at ? ', undone' : ''}
          </span>
        </span>
        <span className="row-act">{c.can_undo && !c.undone_at && busy !== c.id && <Verb onClick={() => void undo(c)}>Undo</Verb>}</span>
        <Ago at={c.created_at} className="row-age" />
      </div>
    );
  });
}

function ChangesPage({ before, last, onMore }: { before: string; last: boolean; onMore: (next: string) => void }) {
  const p = useQuery(changesQuery(before));
  if (p.isError) return <Failed error={p.error} what="Changes" retry={() => void p.refetch()} />;
  if (!p.data) return null;
  const next = p.data.next_before;
  return (
    <>
      <ChangeRows changes={p.data.changes ?? []} />
      {last && next && <Older onClick={() => onMore(next)} />}
    </>
  );
}

function Changes({ pick }: { pick: ReactNode }) {
  const first = useQuery(changesQuery());
  const [older, setOlder] = useState<string[]>([]);
  const c = first.data;
  const more = (next: string) => setOlder((o) => (o.includes(next) ? o : [...o, next]));
  return (
    <Section name="Changes" count={c?.changes?.length} actions={pick} target="setting:changes">
      {first.isError ? (
        <Failed error={first.error} what="Changes" retry={() => void first.refetch()} />
      ) : !c ? null : (c.changes ?? []).length === 0 ? (
        <Empty title="No changes yet" />
      ) : (
        <>
          <ChangeRows changes={c.changes} />
          {older.length === 0 && c.next_before && <Older onClick={() => more(c.next_before ?? '')} />}
          {older.map((before, i) => (
            <ChangesPage key={before} before={before} last={i === older.length - 1} onMore={more} />
          ))}
        </>
      )}
    </Section>
  );
}
