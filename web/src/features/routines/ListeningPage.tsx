import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Link } from '@/kit/Link';
import { Pill } from '@/kit/Pill';
import { Row } from '@/kit/Row';
import { Segmented } from '@/kit/Segmented';
import { fmtDate } from '@/lib';
import { List, PageMore, Record as RecordPage, Section, type PageTab } from '@/pagekinds';
import { LISTENING } from './actions';
import { Face, HeadSwitch, press, RemoveItem, RuleRow, useConfirmRemove, Word } from './parts';
import { matchesQuery, runsQuery, sourcesQuery, watchesQuery, watchQuery } from './queries';
import { runLink } from './RoutinePanel';
import type { ListenSource, Match, Routine, WatchSource } from './types';
import { agentWords, cap, limitsWords } from './words';
import './routines.css';

/* Listening (rules.js listeningScreen, watchScreen): the watches as a List; one watch as a Record with two tabs, its
   matches by day and its sources with what it keeps. A match opens in place (`&match=`). */

export interface ListeningSearch {
  watch?: string;
  tab?: 'sources';
  match?: string;
}

const q = (search: Record<string, string | undefined>) => {
  const s = new URLSearchParams(Object.entries(search).filter((e): e is [string, string] => !!e[1])).toString();
  return s ? `${LISTENING}?${s}` : LISTENING;
};
/** `/company/listening?…`: the address a row opens. */
export const listeningHref = (search: ListeningSearch) => q({ ...search });

export function ListeningPage({ search }: { search: ListeningSearch }) {
  return search.watch ? <WatchPage key={search.watch} id={search.watch} tab={search.tab} match={search.match} /> : <Watches />;
}

const isOn = (w: Routine) => w.enabled !== false && !w.paused;
const sourceCount = (n: number) => `${n} ${n === 1 ? 'source' : 'sources'}`;

// ---------------------------------------------------------------- the watches

function Watches() {
  const list = useQuery(watchesQuery);
  const [adding, setAdding] = useState(false);
  const watches = list.data ?? [];
  const add = (
    <Button variant="primary" onClick={() => setAdding(true)}>
      Add watch
    </Button>
  );
  return (
    <List
      title="Listening"
      path={LISTENING}
      className="rr"
      state={list.isPending ? 'loading' : list.isError ? 'fault' : watches.length ? 'ready' : 'empty'}
      onRetry={() => void list.refetch()}
      empty={{ title: 'No watches yet', action: add }}
      actions={
        <>
          {add}
          <AddWatch open={adding} onOpenChange={setAdding} />
        </>
      }
      sections={
        <Section name="Watches" count={watches.length} target="section:watches">
          {watches.map((w) => (
            <WatchRow key={w.id} w={w} />
          ))}
        </Section>
      }
    />
  );
}

function WatchRow({ w }: { w: Routine }) {
  const on = isOn(w);
  const at = w.last_run?.started_at ?? w.last_run?.finished_at;
  const name = w.name || 'Watch';
  return (
    <Row className={on ? 'watch' : 'watch is-off'} title={name} href={listeningHref({ watch: w.id })} target={`watch:${w.id}`}>
      <span className="row-title">{name}</span>
      <span className="row-sub">{sourceCount(w.trigger.sources?.length ?? w.listening?.sources?.length ?? 0)}</span>
      <span className="row-fig">
        {on ? (
          limitsWords(w.trigger.limits)
        ) : (
          <span className="row-state" title="Paused">
            <Icon name="pause" />
          </span>
        )}
      </span>
      <Face id={w.owner_id} />
      {at ? <Ago at={at} className="row-age" /> : <span className="row-age" />}
    </Row>
  );
}

function AddWatch({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (!name.trim()) return;
    setBusy(true);
    const ok = await press('listening.add', { name: name.trim() });
    setBusy(false);
    if (ok) {
      setName('');
      onOpenChange(false);
    }
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Add watch"
      actions={
        <>
          <Button variant="quiet" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!name.trim()} onClick={() => void save()}>
            Add
          </Button>
        </>
      }
    >
      <Field label="Name" value={name} onValueChange={setName} placeholder="Mentions of us" autoFocus />
    </Dialog>
  );
}

// ---------------------------------------------------------------- one watch

function WatchPage({ id, tab, match }: { id: string; tab?: 'sources'; match?: string }) {
  const one = useQuery(watchQuery(id));
  const listed = useQuery(watchesQuery).data?.find((w) => w.id === id);
  const w = one.data ?? listed;
  const [adding, setAdding] = useState(false);
  const remove = useConfirmRemove();
  const tabs: readonly PageTab[] = [
    { label: 'Matches', to: LISTENING, search: { watch: id, tab: '' }, target: `tab:matches:${id}` },
    { label: 'Sources', to: LISTENING, search: { watch: id, tab: 'sources' }, target: `tab:sources:${id}` },
  ];
  const tmp = id.startsWith('tmp:');
  const head = {
    title: w?.name || 'Watch',
    path: LISTENING,
    crumb: [{ label: 'Listening', to: LISTENING }],
    className: 'rr',
    tabs,
  };
  if (!w) {
    return <RecordPage {...head} state={one.isPending ? 'loading' : 'fault'} onRetry={() => void one.refetch()} />;
  }
  const on = isOn(w);
  return (
    <RecordPage
      {...head}
      actions={
        <>
          <HeadSwitch label={on ? 'On' : 'Paused'} checked={on} disabled={tmp} onChange={(next) => void press(next ? 'listening.resume' : 'listening.pause', { watch: id })} />
          <Button variant="primary" onClick={() => setAdding(true)}>
            Add source
          </Button>
          <AddSource watch={id} open={adding} onOpenChange={setAdding} />
          {remove.node}
        </>
      }
      more={
        <PageMore>
          <RemoveItem disabled={tmp} onSelect={() => remove.confirm(w.name || 'this watch', () => void press('listening.remove', { watch: id }))} />
        </PageMore>
      }
      sections={tab === 'sources' ? <Sources w={w} /> : <Matches id={id} open={match} />}
    />
  );
}

const DAY = 86_400_000;
function dayOf(iso: string): 'Today' | 'Yesterday' | 'Earlier' {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const t = Date.parse(iso);
  return t >= start.getTime() ? 'Today' : t >= start.getTime() - DAY ? 'Yesterday' : 'Earlier';
}

function Matches({ id, open }: { id: string; open?: string }) {
  const q = useQuery(matchesQuery(id));
  const list = q.data ?? [];
  if (q.isError) return <Word>Matches did not load</Word>;
  if (q.isPending) return null;
  if (!list.length) {
    return (
      <Section name="Matches" count={0}>
        <p className="row-sub rr-none">No matches yet</p>
      </Section>
    );
  }
  const groups = (['Today', 'Yesterday', 'Earlier'] as const).map((d) => [d, list.filter((m) => dayOf(m.posted_at ?? m.created_at) === d)] as const).filter(([, ms]) => ms.length);
  return groups.map(([d, ms]) => (
    <Section key={d} name={d} count={ms.length} target={`section:${d}`}>
      {ms.map((m) => (m.id === open ? <MatchOpen key={m.id} watch={id} m={m} /> : <MatchRow key={m.id} watch={id} m={m} />))}
    </Section>
  ));
}

const srcOf = (m: Match) => m.source || m.site || '';
const textOf = (m: Match) => m.title || m.text || '';
const tagWords = (tag: string | null | undefined) => (tag ? cap(tag.replace(/[_-]+/g, ' ')) : '');

function MatchRow({ watch, m }: { watch: string; m: Match }) {
  const text = textOf(m);
  return (
    <Row className="match" title={text} href={listeningHref({ watch, match: m.id })} target={`match:${m.id}`}>
      <span className="row-sub">{srcOf(m)}</span>
      <span className="row-title">{text}</span>
      {m.tag ? <Pill>{tagWords(m.tag)}</Pill> : <span />}
      <Ago at={m.posted_at ?? m.created_at} className="row-age" />
    </Row>
  );
}

function MatchOpen({ watch, m }: { watch: string; m: Match }) {
  const at = m.posted_at ?? m.created_at;
  // A sent match has no task of its own: the run that sent it started one with the batch (the last 20 runs are read).
  const runs = useQuery({ ...runsQuery(watch), enabled: !!m.run_id }).data;
  const run = m.run_id ? runs?.find((r) => r.id === m.run_id) : undefined;
  const task = run && runLink(run);
  return (
    <article className="match-open" data-target={`match:${m.id}`}>
      <span className="row-sub">{srcOf(m)}</span>
      <p className="match-text t-read">{m.text || m.title}</p>
      <Ago at={at} className="row-age" />
      <div className="match-foot">
        {m.tag && <Pill>{tagWords(m.tag)}</Pill>}
        <span>{[m.author, fmtDate(at)].filter(Boolean).join(', ')}</span>
        <span className="grow" />
        {m.url && (
          <a className="verb" href={m.url} target="_blank" rel="noreferrer">
            Open source
          </a>
        )}
        {task?.startsWith('/task/') && (
          <Link className="verb" href={task}>
            Open task
          </Link>
        )}
        <Link className="verb" href={listeningHref({ watch })}>
          Close
        </Link>
      </div>
    </article>
  );
}

const KINDS: Record<string, string> = { search: 'Search', page: 'Page', github: 'GitHub', push: 'Sent in' };

function Sources({ w }: { w: Routine }) {
  const read = useQuery(sourcesQuery(w.id));
  const remove = useConfirmRemove();
  const byId = new Map((read.data ?? []).map((s) => [s.id, s]));
  const list: (ListenSource & Partial<WatchSource>)[] = (w.trigger.sources ?? []).map((s) => ({ ...byId.get(s.id), ...s }));
  const rules = w.trigger.rules ?? [];
  return (
    <>
      <Section name="Sources" count={list.length} target={`section:sources:${w.id}`}>
        {!list.length && <p className="row-sub rr-none">No sources yet</p>}
        {list.map((s) => {
          const title = s.target || s.site || KINDS[s.kind] || 'Source';
          return (
            <Row key={s.id} className="watch" title={title} target={`source:${s.id}`}>
              <span className="row-title">{title}</span>
              <span className="row-sub">{[KINDS[s.kind] ?? cap(s.kind), s.site && s.site !== title ? s.site : ''].filter(Boolean).join(', ')}</span>
              <span className="row-fig">{s.status && s.status !== 'ok' && s.status !== 'active' ? <Word>{agentWords(cap(s.status))}</Word> : ''}</span>
              <IconButton icon="close" label="Remove source" size="sm" quiet onClick={() => remove.confirm(title, () => void press('listening.removeSource', { watch: w.id, source: s.id }))} />
              {s.last_read_at ? <Ago at={s.last_read_at} className="row-age" /> : <span className="row-age" />}
            </Row>
          );
        })}
      </Section>
      {rules.length > 0 && (
        <Section name="Rules" count={rules.length} target={`section:keeps:${w.id}`}>
          {rules.map((r, i) => (
            <RuleRow
              key={`${i}:${r.text}`}
              how="bot"
              cost
              parts={[agentWords(r.text), r.do === 'drop' ? 'Drop it' : r.tag ? `Keep it as ${tagWords(r.tag).toLowerCase()}` : 'Keep it']}
              fixed
            />
          ))}
        </Section>
      )}
      {remove.node}
    </>
  );
}

function AddSource({ watch, open, onOpenChange }: { watch: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [kind, setKind] = useState<'search' | 'page'>('search');
  const [target, setTarget] = useState('');
  const [site, setSite] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (!target.trim()) return;
    setBusy(true);
    const ok = await press('listening.addSource', { watch, kind, target: target.trim(), ...(kind === 'search' && site.trim() ? { site: site.trim() } : {}) });
    setBusy(false);
    if (ok) {
      setTarget('');
      setSite('');
      onOpenChange(false);
    }
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Add source"
      actions={
        <>
          <Button variant="quiet" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!target.trim()} onClick={() => void save()}>
            Add
          </Button>
        </>
      }
    >
      <div className="rr-form">
        <Segmented
          label="Kind"
          options={[
            { value: 'search', label: 'Search' },
            { value: 'page', label: 'Page' },
          ]}
          value={kind}
          onValueChange={setKind}
        />
        <Field label={kind === 'search' ? 'Words' : 'Address'} value={target} onValueChange={setTarget} placeholder={kind === 'search' ? 'raised bed kit' : 'https://'} autoFocus />
        {kind === 'search' && <Field label="Site" value={site} onValueChange={setSite} placeholder="reddit.com" />}
      </div>
    </Dialog>
  );
}
