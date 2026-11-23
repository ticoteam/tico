import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { refusal, run } from '@/actions';
import { configQuery, meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts } from '@/kit/LabelValue';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { Section } from '@/pagekinds';
import { modelName } from '@/features/team/routing';
import { AgentList } from './AgentList';
import { ComputerList } from './ComputerList';
import { installCommands, type Command, type ComputerKind, type RunnerConfig } from './install';
import { Line, SwitchLine, Verb, Word, act, useAdmin } from './parts';
import { hostedQuery, modelsQuery, providersQuery, sleepQuery } from './queries';

export function Agents({ open }: { open?: string }) {
  return (
    <>
      <AgentList open={open} />
      <Defaults />
    </>
  );
}

const KINDS = [
  { value: 'server', label: 'Server' },
  { value: 'mac', label: 'Mac' },
  { value: 'windows', label: 'Windows' },
] as const;

function CommandLine({ c }: { c: Command }) {
  return (
    <div className="set-cmd">
      <span className="set-cmd-note">{c.note}</span>
      <div className="set-cmd-row">
        <code className="fs-code">{c.line}</code>
        <IconButton
          icon="copy"
          label="Copy"
          size="sm"
          quiet
          onClick={() => navigator.clipboard?.writeText(c.line).then(() => toast({ text: 'Copied', kind: 'done' }))}
        />
      </div>
    </div>
  );
}

export function AddComputer() {
  const config = useQuery(configQuery);
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<ComputerKind>('server');
  const [label, setLabel] = useState('');
  const [code, setCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const close = () => {
    setOpen(false);
    setCode(null);
    setLabel('');
  };
  const start = async () => {
    setBusy(true);
    const r = await run('settings.addComputer', {}, { via: 'click' });
    setBusy(false);
    if (!r.ok) return toast(refusal(r, "Couldn't make a code"));
    setCode((r.out as { code?: string } | undefined)?.code ?? null);
  };
  const [main, ...more] = code
    ? installCommands((config.data ?? {}) as RunnerConfig, location.origin, kind, code, label.trim())
    : [];
  return (
    <>
      <Button variant="primary" onClick={() => setOpen(true)}>
        Add computer
      </Button>
      <Dialog
        open={open}
        onOpenChange={(o) => !o && close()}
        title="Add a computer"
        description={code ? 'The code works once, for 15 minutes, and is not shown again.' : undefined}
        actions={
          code ? (
            <Button variant="primary" onClick={close}>
              Done
            </Button>
          ) : (
            <>
              <Button variant="quiet" onClick={close}>
                Cancel
              </Button>
              <Button variant="primary" busy={busy} disabled={!label.trim()} onClick={() => start()}>
                Get code
              </Button>
            </>
          )
        }
      >
        {main ? (
          <div className="set-add">
            <CommandLine c={main} />
            {more.length > 0 && (
              <details className="set-more">
                <summary>Other ways</summary>
                {more.map((c) => (
                  <CommandLine key={c.line} c={c} />
                ))}
              </details>
            )}
          </div>
        ) : (
          <div className="set-add">
            <Segmented label="Kind" options={KINDS} value={kind} onValueChange={setKind} />
            <Field label="Name" value={label} onValueChange={setLabel} autoComplete="off" />
          </div>
        )}
      </Dialog>
    </>
  );
}

const HOSTED_KINDS: Record<string, string> = { tico: 'Tico server', bot_computer: 'Agent computer' };

function Hosted() {
  const hosted = useQuery({ ...hostedQuery, retry: false });
  const admin = useAdmin();
  const [said, setSaid] = useState<Record<string, string>>({});
  // A start refused for want of credit: where to ask Tico support for more (old settings-hosted.ts).
  const [credit, setCredit] = useState<Record<string, string>>({});
  const v = hosted.data;
  if (v?.state === 'deactivated')
    return (
      <Section name="Hosted servers" target="setting:hosted">
        <Empty title="Hosting has ended" />
      </Section>
    );
  if (v?.state !== 'ok' || !v.servers?.length) return null;
  const start = async (id: string) => {
    const r = await run('settings.startServer', { server: id }, { via: 'click' });
    const out = r.out as { state?: string; message?: string; credit_url?: string } | undefined;
    if (!r.ok) return toast(refusal(r, "Couldn't start"));
    if (out?.state !== 'started') setSaid((s) => ({ ...s, [id]: out?.message ?? 'Not started' }));
    const url = out?.state === 'paused' && /^(https:\/\/|mailto:)\S+$/.test(out.credit_url ?? '') ? (out.credit_url ?? '') : '';
    setCredit((c) => ({ ...c, [id]: url }));
  };
  return (
    <Section name="Hosted servers" count={v.servers.length} target="setting:hosted">
      {v.servers.map((s) => (
        <div key={s.server_id} className="row g-host" data-target={`hosted:${s.server_id}`} title={said[s.server_id]}>
          <span className="row-title">{HOSTED_KINDS[s.kind] ?? s.kind}</span>
          <span className="row-state">
            <Word fine={s.power === 'on'}>{s.power === 'on' ? 'On' : (said[s.server_id] ?? 'Stopped')}</Word>
          </span>
          <span>
            {credit[s.server_id] ? (
              <a className="verb" href={credit[s.server_id]} target="_blank" rel="noreferrer" title="Contact Tico support for more credit">
                Tico support
              </a>
            ) : (
              admin && s.power !== 'on' && <Verb onClick={() => void start(s.server_id)}>Start</Verb>
            )}
          </span>
        </div>
      ))}
    </Section>
  );
}

const SLEEP_HOURS = [0, 1, 2, 3, 4, 6, 8, 12, 24];
const AWAKE: Record<string, string> = {
  run_queued: 'A run is waiting',
  run_in_progress: 'A run is going',
  task_session: 'A task is waiting',
  runner_job: 'A computer job is running',
  send_pending: 'A send is waiting',
  routine_due: 'A routine is due soon',
};

function Sleep() {
  const sleep = useQuery({ ...sleepQuery, retry: false });
  const admin = useAdmin();
  const v = sleep.data;
  if (!v?.hosted || v.managed) return null;
  const now = v.hours ?? 0;
  const hours = SLEEP_HOURS.includes(now) ? SLEEP_HOURS : [...SLEEP_HOURS, now].sort((a, b) => a - b);
  return (
    <Section name="When idle" target="setting:sleep">
      <Facts label="When idle">
        <Line label="Sleep when idle">
          <Select
            label="Sleep when idle"
            labelHidden
            look="quiet"
            disabled={!admin}
            value={String(now)}
            options={hours.map((h) => ({ value: String(h), label: h ? `After ${h} ${h === 1 ? 'hour' : 'hours'}` : 'Off' }))}
            onValueChange={(h) => void act('settings.setSleep', { hours: Number(h) })}
          />
        </Line>
        {now > 0 && (
          <SwitchLine
            label="Agents' computers sleep too"
            checked={v.runners}
            disabled={!admin}
            onChange={(on) => void act('settings.sleepComputers', { on })}
          />
        )}
        {now > 0 && v.blocker && <Line label="Awake">{AWAKE[v.blocker] ?? v.blocker}</Line>}
      </Facts>
    </Section>
  );
}

export function Computers({ open }: { open?: string }) {
  return (
    <>
      <ComputerList open={open} />
      <Hosted />
      <Sleep />
    </>
  );
}

// ---------------------------------------------------------------- Models

function Defaults() {
  const catalog = useQuery(modelsQuery);
  const names = (catalog.data?.models ?? [])
    .filter((m) => !m.deprecated && (!catalog.data?.enabled_providers || catalog.data.enabled_providers.includes(m.provider)))
    .map((m) => ({ value: m.id, label: modelName(m.label) }));
  const providers = useQuery({ ...providersQuery, retry: false });
  const me = useQuery(meQuery);
  const v = providers.data;
  if (!v?.default) return null;
  // The owner chooses; everyone else sees the choice.
  const owner = me.data?.role === 'owner';
  const model = v.default.model ?? '';
  const meeting = v.meeting_model ?? '';
  const known = (id: string) => (id && !names.some((n) => n.value === id) ? [{ value: id, label: id }] : []);
  return (
    <Section name="Defaults" target="setting:defaults">
      <Facts label="Defaults">
        <Line label="A new agent uses" target="setting:default-model">
          <Select
            label="A new agent uses"
            labelHidden
            look="quiet"
            disabled={!owner}
            value={model || null}
            placeholder="None"
            options={[...known(model), ...names]}
            onValueChange={(m) => void act('settings.defaultModel', { model: m })}
          />
        </Line>
        <Line label="Meetings model" target="setting:meeting-model">
          <Select
            label="Meetings model"
            labelHidden
            look="quiet"
            disabled={!owner}
            value={meeting}
            options={[{ value: '', label: "Agent's own model" }, ...known(meeting), ...names]}
            onValueChange={(m) => void act('settings.meetingModel', { model: m })}
          />
        </Line>
      </Facts>
    </Section>
  );
}
