import { useQuery } from '@tanstack/react-query';
import { Ago } from '@/kit/Ago';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Row } from '@/kit/Row';
import { Select } from '@/kit/Select';
import { Switch } from '@/kit/Switch';
import { fmtDate } from '@/lib';
import { SidePanel } from '@/pagekinds';
import type { Builtin } from './actions';
import { HeadSwitch, press, Verb, Word } from './parts';
import { heartbeatQuery, maintenanceQuery, maintenanceRunsQuery, systemJobsQuery } from './queries';
import type { JobPreset, SystemJob } from './types';
import { agentWords, heartbeatChoices, MAINTENANCE_EVERY, type MaintenanceEvery } from './words';

/* Maintenance, Heartbeat and System jobs, each as a panel (rules.js builtinPanel): when it runs, its steps or jobs, and
   its last runs. Each keeps its switch; one the person may not change shows it, greyed. */

export function BuiltinPanel({ which, onClose }: { which: Builtin; onClose: () => void }) {
  if (which === 'maintenance') return <MaintenancePanel onClose={onClose} />;
  if (which === 'heartbeat') return <HeartbeatPanel onClose={onClose} />;
  return <SystemPanel onClose={onClose} />;
}

const ms = (n: number | null | undefined) => (n == null ? '' : n < 1000 ? `${n} ms` : n < 60_000 ? `${Math.round(n / 1000)}s` : `${Math.floor(n / 60_000)}m ${Math.round((n % 60_000) / 1000)}s`);

function MaintenancePanel({ onClose }: { onClose: () => void }) {
  const m = useQuery(maintenanceQuery);
  const runs = useQuery(maintenanceRunsQuery);
  const d = m.data;
  const can = !!d?.can_change;
  return (
    <SidePanel
      className="rr"
      title="Maintenance"
      target="routine:maintenance"
      onClose={onClose}
      actions={d && <HeadSwitch label={d.settings.paused ? 'Paused' : 'On'} checked={!d.settings.paused} disabled={!can} onChange={(on) => void press('routines.setMaintenance', { paused: !on })} />}
    >
      {m.isError && <Word>Maintenance did not load</Word>}
      {d && (
        <>
          <section className="rail-sec">
            <Facts label="When">
              <LabelValue label="Runs">
                <Select
                  label="Runs"
                  labelHidden
                  look="quiet"
                  disabled={!can}
                  options={MAINTENANCE_EVERY}
                  value={(MAINTENANCE_EVERY.find((e) => e.value === d.settings.schedule)?.value ?? null) as MaintenanceEvery | null}
                  onValueChange={(schedule) => void press('routines.setMaintenance', { schedule })}
                />
              </LabelValue>
            </Facts>
          </section>
          <section className="rail-sec">
            <h3>Steps</h3>
            {d.steps.map((s) => (
              <div key={s.key} className={s.enabled ? 'row pjob' : 'row pjob is-off'} data-target={`step:${s.key}`}>
                <span className="pjob-main">
                  <span className="row-title">{s.name}</span>
                  <span className="row-sub">{s.last_ran_at ? `Last ran ${fmtDate(s.last_ran_at)}` : 'Not run yet'}</span>
                </span>
                <Switch
                  label={s.name}
                  labelHidden
                  checked={s.enabled}
                  disabled={!can || !!s.fixed}
                  onCheckedChange={(on) => void press('routines.setMaintenance', { step: s.key, stepOn: on })}
                />
              </div>
            ))}
          </section>
        </>
      )}
      <section className="rail-sec">
        <h3>
          Last runs
          {can && <Verb onClick={() => void press('routines.runMaintenance', {})}>Run now</Verb>}
        </h3>
        {(runs.data ?? []).map((r) => {
          const what = r.result ? agentWords(r.result) : r.ok === false ? 'Failed' : r.finished_at ? `${r.steps?.length ?? 0} steps` : 'Running';
          return (
            <Row key={r.id} className="prun" title={what}>
              <span className="run-at">{fmtDate(r.started_at)}</span>
              <span className="row-sub">{what}</span>
              {r.ok === false ? <Word>Failed</Word> : <span className="row-age">{r.finished_at ? ms(Date.parse(r.finished_at) - Date.parse(r.started_at)) : ''}</span>}
            </Row>
          );
        })}
      </section>
    </SidePanel>
  );
}

function HeartbeatPanel({ onClose }: { onClose: () => void }) {
  const h = useQuery(heartbeatQuery);
  const d = h.data;
  return (
    <SidePanel className="rr" title="Heartbeat" target="routine:heartbeat" onClose={onClose}>
      {h.isError && <Word>Heartbeat did not load</Word>}
      {d && (
        <>
          <section className="rail-sec">
            <Facts label="When">
              <LabelValue label="Runs">
                <Select
                  label="Runs"
                  labelHidden
                  look="quiet"
                  disabled={!d.can_change}
                  options={heartbeatChoices(d.interval_min, d.interval_max, d.interval_s)}
                  value={String(d.interval_s)}
                  onValueChange={(v) => void press('routines.setHeartbeat', { seconds: Number(v) })}
                />
              </LabelValue>
              {d.last_beat_at && (
                <LabelValue label="Last beat">
                  <Ago at={d.last_beat_at} />
                </LabelValue>
              )}
            </Facts>
          </section>
          <section className="rail-sec">
            <h3>Steps</h3>
            {d.steps.map((s) => (
              <div key={s.key} className={s.on ? 'row pjob' : 'row pjob is-off'} data-target={`job:${s.key}`}>
                <span className="pjob-main">
                  <span className="row-title">{agentWords(s.name)}</span>
                  <span className="row-sub">{s.failing && s.last_error ? agentWords(s.last_error) : s.last_did ? agentWords(s.last_did) : agentWords(s.every)}</span>
                </span>
                {s.switch ? (
                  <Switch
                    label={agentWords(s.name)}
                    labelHidden
                    checked={s.on}
                    disabled={!s.can_change}
                    onCheckedChange={(on) => void press('routines.setJob', { job: s.key, on })}
                  />
                ) : (
                  <span className="row-age">{s.last_ms != null ? ms(s.last_ms) : ''}</span>
                )}
              </div>
            ))}
          </section>
          <section className="rail-sec">
            <h3>Last runs</h3>
            {d.beats.map((b) => {
              const what = b.failed ? `${b.failed} failed` : b.did ? `${b.did} done` : 'Nothing to do';
              return (
                <Row key={b.id} className="prun" title={what}>
                  <span className="run-at">{fmtDate(b.started_at)}</span>
                  <span className="row-sub">{what}</span>
                  {b.failed ? <Word>Failed</Word> : <span className="row-age">{ms(b.total_ms)}</span>}
                </Row>
              );
            })}
          </section>
        </>
      )}
    </SidePanel>
  );
}

function SystemPanel({ onClose }: { onClose: () => void }) {
  const j = useQuery(systemJobsQuery);
  const jobs = j.data ?? [];
  // Last runs: each job's own last run, newest first (rules.js builtinPanel system runs).
  const runs = jobs
    .filter((job) => job.last?.started_at)
    .sort((a, b) => Date.parse(b.last?.started_at ?? '') - Date.parse(a.last?.started_at ?? ''));
  return (
    <SidePanel className="rr" title="System jobs" target="routine:system" onClose={onClose}>
      {j.isError && <Word>System jobs did not load</Word>}
      <section className="rail-sec">
        <h3>Jobs</h3>
        {jobs.map((job) => (
          <div key={job.key} className={job.on ? 'row pjob' : 'row pjob is-off'} data-target={`job:${job.key}`}>
            <span className="pjob-main">
              <span className="row-title">{agentWords(job.name)}</span>
              <span className="row-sub">
                <JobWhen job={job} />
                {job.next && job.on && <span>{` · Next ${fmtDate(job.next)}`}</span>}
              </span>
            </span>
            <span className="rr-field">
              {job.controllable && job.on && job.can_change && <Verb onClick={() => void press('routines.runJob', { job: job.key })}>Run now</Verb>}
              {job.switch === false ? (
                <span className="row-age">{job.last?.started_at ? <Ago at={job.last.started_at} /> : ''}</span>
              ) : (
                <Switch
                  label={agentWords(job.name)}
                  labelHidden
                  checked={job.on}
                  disabled={job.can_change === false}
                  onCheckedChange={(on) => void press('routines.setJob', { job: job.key, on })}
                />
              )}
            </span>
          </div>
        ))}
      </section>
      {runs.length > 0 && (
        <section className="rail-sec">
          <h3>Last runs</h3>
          {runs.map((job) => {
            const r = job.last;
            const name = agentWords(job.name);
            return (
              <Row key={job.key} className="prun" title={name}>
                <span className="run-at">{fmtDate(r?.started_at ?? '')}</span>
                <span className="row-sub">{name}</span>
                {r?.ok === false ? <Word>Failed</Word> : <span className="row-age">{r?.finished_at && r.started_at ? ms(Date.parse(r.finished_at) - Date.parse(r.started_at)) : ''}</span>}
              </Row>
            );
          })}
        </section>
      )}
    </SidePanel>
  );
}

/** Daily is "Daily at" (Learnings: "Nightly at") with its time beside it (settings-recurring.ts systemRoutineScheduleHTML). */
const presetLabel = (job: SystemJob, p: { key: JobPreset; label: string }) => (p.key === 'daily' ? (job.key === 'learnings' ? 'Nightly at' : 'Daily at') : agentWords(p.label));

/** The day's half hours, and the saved time when it is between them. */
const timesWith = (at: string | null | undefined) => {
  const all = Array.from({ length: 48 }, (_, n) => `${String(Math.floor(n / 2)).padStart(2, '0')}:${n % 2 ? '30' : '00'}`);
  if (at && !all.includes(at)) all.push(at);
  return all.sort().map((t) => ({ value: t, label: t }));
};

/** When a job runs: its words, or for one with presets the person may change, a quiet preset pick and the daily time. */
function JobWhen({ job }: { job: SystemJob }) {
  const presets = job.presets ?? [];
  const words = agentWords(job.schedule ?? job.what ?? '');
  if (!presets.length || !job.can_change) return <>{words}</>;
  const name = agentWords(job.name);
  return (
    <>
      {presets.length > 1 ? (
        <Select
          label={`${name} schedule`}
          labelHidden
          look="quiet"
          options={presets.map((p) => ({ value: p.key, label: presetLabel(job, p) }))}
          value={job.preset ?? null}
          onValueChange={(preset) => void press('routines.setJobSchedule', { job: job.key, preset, ...(preset === 'daily' && !job.at ? { at: '09:00' } : {}) })}
        />
      ) : (
        presets.map((p) => <span key={p.key}>{presetLabel(job, p)}</span>)
      )}
      {job.preset === 'daily' && (
        <Select
          label={`${name} time`}
          labelHidden
          look="quiet"
          options={timesWith(job.at)}
          value={job.at ?? null}
          onValueChange={(at) => void press('routines.setJobSchedule', { job: job.key, at })}
        />
      )}
    </>
  );
}
