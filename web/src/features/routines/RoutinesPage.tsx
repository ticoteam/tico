import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Link } from '@/kit/Link';
import { Row } from '@/kit/Row';
import { toast } from '@/kit/Toast';
import { List, Section, type PageTab } from '@/pagekinds';
import { BUILTINS, ROUTINES, type Builtin } from './actions';
import { BuiltinPanel } from './BuiltinPanel';
import { Face, press, Word } from './parts';
import { heartbeatQuery, hooksQuery, maintenanceQuery, routinesQuery, systemJobsQuery } from './queries';
import { RoutinePanel } from './RoutinePanel';
import type { Hook, Routine } from './types';
import { WebhookPanel } from './WebhookPanel';
import { maintenanceWords, startsWords, triggerWords } from './words';
import './routines.css';

/* Routines (pages/3-company/rules.js routinesScreen, webhooksScreen): a List. Routines in sections by what starts
   them, then the built-in three; the Webhooks tab lists incoming webhooks. One routine, a built-in one or a webhook
   opens in `?panel=`. */

export interface RoutinesSearch {
  tab?: 'webhooks';
  panel?: string;
}

/** `/company/routines?…`: the address a row opens. */
export const routinesHref = (search: RoutinesSearch) => {
  const q = new URLSearchParams(Object.entries(search).filter((e): e is [string, string] => !!e[1])).toString();
  return q ? `${ROUTINES}?${q}` : ROUTINES;
};

export const routinesTabs: readonly PageTab[] = [
  { label: 'Routines', to: ROUTINES, search: { tab: '' }, target: 'tab:routines' },
  { label: 'Webhooks', to: ROUTINES, search: { tab: 'webhooks' }, target: 'tab:webhooks' },
];

const head = { title: 'Routines', path: ROUTINES, tabs: routinesTabs, className: 'rr' };

export function RoutinesPage({ search }: { search: RoutinesSearch }) {
  return search.tab === 'webhooks' ? <WebhooksTab panel={search.panel} /> : <RoutinesTab panel={search.panel} />;
}

const close = () => void press('routines.closePanel', {});

// ---------------------------------------------------------------- routines

const SECTIONS: [string, string][] = [
  ['schedule', 'Scheduled'],
  ['event', 'Events'],
  ['webhook', 'Webhooks'],
  ['due', 'Due dates'],
  ['message', 'Messages'],
  ['task_received', 'Tasks received'],
];

/** Who runs it: the agent that takes the turn, or the owner. */
export const runnerOf = (r: Routine) => r.steps?.find((s) => s.type === 'bot_turn')?.bot ?? r.owner_id ?? null;

function RoutinesTab({ panel }: { panel?: string }) {
  const list = useQuery(routinesQuery);
  const hooks = useQuery(hooksQuery);
  const routines = list.data ?? [];
  const open = panel?.startsWith('routine:') ? panel.slice(8) : null;
  const built = panel?.startsWith('builtin:') ? (BUILTINS.find((b) => `builtin:${b}` === panel) ?? null) : null;
  const hookName = (id: string) => hooks.data?.find((h) => h.id === id)?.name;
  const known = new Set(SECTIONS.map(([t]) => t));
  const groups: [string, Routine[]][] = [
    ...SECTIONS.map(([t, name]): [string, Routine[]] => [name, routines.filter((r) => r.trigger.type === t)]),
    ['Other', routines.filter((r) => !known.has(r.trigger.type))],
  ];
  const add = (variant: 'primary' | 'plain') => (
    <Button variant={variant} onClick={() => void press('routines.new', {})}>
      Add routine
    </Button>
  );
  // The built-in section keeps the page ready while there are no routines, so the page kind cannot see it is empty:
  // the empty state's Add is the one filled button then, and the head's draws plain.
  const none = list.isSuccess && !routines.length;
  return (
    <List
      {...head}
      state={list.isPending ? 'loading' : list.isError ? 'fault' : 'ready'}
      onRetry={() => void list.refetch()}
      actions={add(none ? 'plain' : 'primary')}
      sections={
        <>
          {routines.length ? (
            groups
              .filter(([, rs]) => rs.length)
              .map(([name, rs]) => (
                <Section key={name} name={name} count={rs.length} target={`section:${name}`}>
                  {rs.map((r) => (
                    <RoutineRow key={r.id} r={r} selected={r.id === open} when={triggerWords(r.trigger, { hook: hookName })} />
                  ))}
                </Section>
              ))
          ) : (
            <Empty title="No routines yet">{add('primary')}</Empty>
          )}
          <BuiltInSection selected={built} />
        </>
      }
      panel={open ? <RoutinePanel key={open} id={open} onClose={close} /> : built ? <BuiltinPanel which={built} onClose={close} /> : null}
    />
  );
}

function RoutineRow({ r, selected, when }: { r: Routine; selected: boolean; when: string }) {
  const off = r.enabled === false || !!r.paused;
  const failed = r.last_run?.status === 'failed';
  const at = r.last_run?.started_at ?? r.last_run?.finished_at;
  const name = r.name || 'Routine';
  return (
    <Row className={off ? 'auto is-off' : 'auto'} title={name} href={routinesHref({ panel: `routine:${r.id}` })} selected={selected} target={`routine:${r.id}`}>
      <span className="row-title">{name}</span>
      <span className="row-sub">{when}</span>
      <span className="row-state">
        {failed ? (
          <Word>Failed</Word>
        ) : off ? (
          <span title="Paused">
            <Icon name="pause" />
          </span>
        ) : null}
      </span>
      <Face id={runnerOf(r)} />
      {at ? <Ago at={at} className="row-age" /> : <span className="row-age" />}
    </Row>
  );
}

/** Maintenance, Heartbeat and System jobs: Tico's own, each with its switch and timing in its panel. One the person
    may not read is left out. */
function BuiltInSection({ selected }: { selected: Builtin | null }) {
  const m = useQuery(maintenanceQuery);
  const h = useQuery(heartbeatQuery);
  const j = useQuery(systemJobsQuery);
  const rows: { id: Builtin; name: string; when: string; at?: string | null; off?: boolean; gone: boolean }[] = [
    { id: 'maintenance', name: 'Maintenance', when: m.data ? maintenanceWords(m.data.settings.schedule) : '', at: m.data?.last_run?.started_at, off: m.data?.settings.paused, gone: m.isError },
    { id: 'heartbeat', name: 'Heartbeat', when: h.data ? `Every ${h.data.interval_s} seconds` : '', at: h.data?.last_beat_at, gone: h.isError },
    { id: 'system', name: 'System jobs', when: j.data ? `${j.data.length} ${j.data.length === 1 ? 'job' : 'jobs'}` : '', gone: j.isError },
  ];
  const shown = rows.filter((r) => !r.gone);
  if (!shown.length) return null;
  return (
    <Section name="Built in" count={shown.length} target="section:built-in">
      {shown.map((b) => (
        <Row
          key={b.id}
          className={b.off ? 'auto is-off' : 'auto'}
          title={b.name}
          href={routinesHref({ panel: `builtin:${b.id}` })}
          selected={selected === b.id}
          target={`routine:${b.id}`}
        >
          <span className="row-title">{b.name}</span>
          <span className="row-sub">{b.when}</span>
          <span className="row-state">
            {b.off && (
              <span title="Paused">
                <Icon name="pause" />
              </span>
            )}
          </span>
          <span className="row-state auto-lock" title="Built in">
            <Icon name="lock" />
          </span>
          {b.at ? <Ago at={b.at} className="row-age" /> : <span className="row-age" />}
        </Row>
      ))}
    </Section>
  );
}

// ---------------------------------------------------------------- webhooks

function WebhooksTab({ panel }: { panel?: string }) {
  const hooks = useQuery(hooksQuery);
  const [adding, setAdding] = useState(false);
  const list = hooks.data ?? [];
  const open = panel?.startsWith('hook:') ? panel.slice(5) : null;
  const add = (
    <Button variant="primary" onClick={() => setAdding(true)}>
      Add webhook
    </Button>
  );
  return (
    <List
      {...head}
      state={hooks.isPending ? 'loading' : hooks.isError ? 'fault' : list.length ? 'ready' : 'empty'}
      onRetry={() => void hooks.refetch()}
      empty={{ title: 'No webhooks yet', action: add }}
      actions={
        <>
          {add}
          <AddWebhook open={adding} onOpenChange={setAdding} />
        </>
      }
      sections={
        <Section name="Incoming" count={list.length} target="section:incoming">
          {list.map((h) => (
            <HookRow key={h.id} h={h} selected={h.id === open} />
          ))}
        </Section>
      }
      panel={open && <WebhookPanel key={open} id={open} onClose={close} />}
    />
  );
}

function HookRow({ h, selected }: { h: Hook; selected: boolean }) {
  const copy = () =>
    void navigator.clipboard.writeText(h.url).then(
      () => toast({ text: 'Address copied', kind: 'done' }),
      () => toast({ text: "Couldn't copy", kind: 'failed' }),
    );
  return (
    <Row className="hook" title={h.name} selected={selected} target={`hook:${h.id}`}>
      <Link className="row-title" href={routinesHref({ tab: 'webhooks', panel: `hook:${h.id}` })} aria-current={selected ? 'true' : undefined}>
        {h.name}
      </Link>
      <span className="row-sub">{startsWords(h)}</span>
      <span className="row-state">{h.signed === false && !h.builtin && <Word>Unsigned</Word>}</span>
      <span className="row-fig">{`${h.received_24h ?? 0} in 24 h`}</span>
      {h.last_received ? <Ago at={h.last_received} className="row-age" /> : <span className="row-age">Never</span>}
      {h.url ? <IconButton icon="link" label="Copy address" size="sm" quiet onClick={copy} /> : <span />}
    </Row>
  );
}

function AddWebhook({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (!name.trim()) return;
    setBusy(true);
    const ok = await press('webhooks.add', { name: name.trim() });
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
      title="Add webhook"
      description="Its address is shown once, after you add it."
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
      <Field label="Name" value={name} onValueChange={setName} placeholder="Shipping" autoFocus />
    </Dialog>
  );
}
