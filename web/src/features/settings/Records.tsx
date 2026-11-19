import { useQueries, useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { meQuery } from '@/data';
import { taskDetailQuery } from '@/features/task/exports';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Link as To } from '@/kit/Link';
import { Markdown } from '@/kit/Markdown';
import { Pill } from '@/kit/Pill';
import { Select } from '@/kit/Select';
import { run } from '@/actions/run';
import { refusal } from '@/actions';
import { fmtAge, fmtDate } from '@/lib';
import { toast } from '@/kit/Toast';
import { FiguresStrip, Section } from '@/pagekinds';
import { fixHref } from './fixes';
import { Foot, SwitchLine, Verb, Word, act, isForbidden, micros } from './parts';
import { activityQuery, billingDayQuery, billingFor, changelogQuery, healthQuery, type BillingLine, type BillingMonth, type HealthFix } from './queries';
import type { ChangelogEntry, ChangelogList } from '@/api/schema';
import { changelogPage, findingsQuery, type HealthCheck, type SetupCheckRow, type StuckAgent } from './queries';

/* The sections that are mostly read: Billing, History, Health, Changelog. */

// backend/company_billing.py: anything but `ok` comes with no figures.
const NOT_OK: Record<string, string> = {
  no_gateway: 'Billing is not set up here',
  unreachable: "Billing can't be reached",
  no_account: 'No billing account yet',
  refused: 'Billing refused this server',
};

const PRODUCT: Record<string, string> = { gateway: 'Tico models', hosting: 'Hosting', support: 'Support', refund: 'Refunds' };
const SOURCE: Record<string, string> = { grant: 'Grant', adjustment: 'From Tico', arrangement: 'From Tico', payment: 'Payment', refund_back: 'Payment' };

/* HQ bills by UTC month, day and hour ('2026-11', '2026-11-01', '2026-11-01T14'): read in UTC so a day never shifts. */
const utc = (key: string) => new Date(`${key.length === 7 ? `${key}-01` : key.slice(0, 10)}T${key.length > 10 ? key.slice(11, 13) : '00'}:00:00Z`);
const words = (key: string, o: Intl.DateTimeFormatOptions) => (Number.isNaN(utc(key).getTime()) ? key : utc(key).toLocaleDateString('en-US', { timeZone: 'UTC', ...o }));
const monthWords = (m: string) => words(m, { month: 'long', year: 'numeric' });
const dayWords = (d: string) => words(d, { weekday: 'short', month: 'short', day: 'numeric' });
const hourWords = (h: string) => (Number.isNaN(utc(h).getTime()) ? h : `${utc(h).toLocaleTimeString('en-US', { timeZone: 'UTC', hour: 'numeric' })} UTC`);
/** Only a plain https address is a place to pay (old app cloud-access.ts cloudPayUrl). */
const payUrl = (u: unknown) => (typeof u === 'string' && /^https:\/\/\S+$/.test(u) ? u : '');
/** Under a cent says so rather than $0.00. */
const line$ = (m: number) => (m !== 0 && Math.abs(m) < 10_000 ? (m < 0 ? '-<$0.01' : '<$0.01') : micros(m));

/** A day's or an hour's charges, by product and (for Tico models) model. */
function split(lines: BillingLine[] = []): [string, number][] {
  const rows = new Map<string, number>();
  for (const l of lines) {
    const model = l.product === 'gateway' ? String(l.sku ?? '').split(':')[0] : (l.sku ?? '');
    const key = [PRODUCT[l.product] ?? l.product, model].filter(Boolean).join(', ');
    rows.set(key, (rows.get(key) ?? 0) + l.amount_micros);
  }
  return [...rows];
}

/** The status a figure carries: grace, paused, low, or a cloud company frozen or locked (old billingStatus). */
function status(b: BillingMonth): string | undefined {
  if (b.cloud?.access === 'locked') return 'Locked';
  if (b.cloud?.access === 'frozen') return 'Out of credit';
  if (b.state === 'grace') return b.grace_until ? `Servers stop ${dayWords(b.grace_until)}` : 'Servers stopping';
  if (b.state === 'paused') return 'Tico models paused';
  if (b.state === 'low') return 'Low balance';
  return b.record_only && !b.cloud ? 'Recorded, not charged' : undefined;
}

function Lines({ lines }: { lines?: BillingLine[] }) {
  return split(lines).map(([what, m]) => (
    <div key={what} className="row g-bday is-line" title={what}>
      <span className="row-title">{what}</span>
      <span className="row-fig">{line$(m)}</span>
    </div>
  ));
}

/** A day opened: its split, then its hours, read when it opens. */
function Hours({ day }: { day: string }) {
  const q = useQuery(billingDayQuery(day));
  if (q.isError)
    return (
      <Foot open>
        <Verb onClick={() => void q.refetch()}>Try again</Verb>
      </Foot>
    );
  const hours = q.data?.state === 'ok' ? (q.data.hours ?? []) : [];
  if (!q.data) return null;
  if (q.data.state !== 'ok') return <p className="u-more">{NOT_OK[q.data.state] ?? "Billing can't be reached"}</p>;
  return hours.map((h) => (
    <div key={h.period} className="row g-bday is-line" title={hourWords(h.period)}>
      <span className="row-title">{hourWords(h.period)}</span>
      <span className="row-fig">{micros(h.total_micros)}</span>
    </div>
  ));
}

function Days({ b, month, onMonth }: { b: BillingMonth; month: string; onMonth: (m: string) => void }) {
  const [open, setOpen] = useState('');
  const months = b.months?.length ? b.months : [b.month];
  const pick = (
    <Select
      label="Month"
      labelHidden
      look="quiet"
      value={month || b.month}
      options={months.map((m) => ({ value: m, label: monthWords(m) }))}
      onValueChange={(m) => {
        setOpen('');
        onMonth(m);
      }}
    />
  );
  return (
    <Section name="By day" count={b.usage.length} actions={pick} target="setting:billing-days">
      {b.usage.length === 0 ? (
        <Empty title="No usage this month" />
      ) : (
        b.usage.map((d) => {
          const isOpen = open === d.period;
          const row = (
            <button key={d.period} type="button" className="row g-bday" aria-expanded={isOpen} title={dayWords(d.period)} onClick={() => setOpen(isOpen ? '' : d.period)}>
              <span className="row-title">{dayWords(d.period)}</span>
              <span className="row-fig is-strong">{micros(d.total_micros)}</span>
            </button>
          );
          return isOpen ? (
            <div key={d.period} className="open">
              {row}
              <Lines lines={d.lines} />
              <Hours day={d.period} />
            </div>
          ) : (
            row
          );
        })
      )}
    </Section>
  );
}

function Statement({ b }: { b: BillingMonth }) {
  const s = b.statement;
  if (!s) return null;
  const lines: [string, number][] = [
    ['Opening balance', s.opening_micros],
    ...Object.entries(s.credits_by_source ?? {}).map(([k, m]): [string, number] => [`Credit, ${SOURCE[k] ?? k}`, m]),
    ...Object.entries(s.debits_by_product ?? {}).map(([k, m]): [string, number] => [PRODUCT[k] ?? k, -m]),
    ...(s.expired_micros ? [['Expired credit', -s.expired_micros] as [string, number]] : []),
    ['Closing balance', s.closing_micros],
  ];
  return (
    <Section name={`Statement, ${monthWords(b.month)}`} target="setting:statement">
      <Facts label="Statement">
        {lines.map(([what, m]) => (
          <LabelValue key={what} label={what} num>
            {micros(m)}
          </LabelValue>
        ))}
      </Facts>
      <Foot>
        <a className="verb" href={`/api/v2/billing/statements/${encodeURIComponent(b.month)}.csv`} download={`tico-statement-${b.month}.csv`}>
          Download CSV
        </a>
      </Foot>
    </Section>
  );
}

/** Where credit is added, by the person on HQ's pages: links, never actions Tico can take. */
function Pay({ b }: { b: BillingMonth }) {
  const cloud = b.cloud;
  const pay = payUrl(cloud?.billing_url);
  const book = payUrl(b.booking_url);
  if (!cloud?.billing_url && !book) return null;
  return (
    <Foot>
      {cloud?.billing_url && (
        <a className="verb" href={cloud.billing_url} target="_blank" rel="noreferrer">
          Change plan
        </a>
      )}
      {cloud && pay && cloud.access === 'frozen' && (
        <a className="verb" href={pay} target="_blank" rel="noreferrer">
          Add credit
        </a>
      )}
      {cloud && pay && cloud.access === 'locked' && (
        <a className="verb" href={pay} target="_blank" rel="noreferrer">
          Pay now
        </a>
      )}
      {!cloud && book && (
        <a className="verb" href={book} target="_blank" rel="noreferrer" title="Talk to us">
          Add credit
        </a>
      )}
    </Foot>
  );
}

export function Billing() {
  const [month, setMonth] = useState('');
  const billing = useQuery(billingFor(month));
  const b = billing.data as BillingMonth | undefined;
  if (!b) {
    if (!billing.isError) return null;
    if (isForbidden(billing.error)) return <Empty icon="lock" title="Owners and admins only" />;
    return (
      <Empty icon="alert" title="Billing did not load">
        <Button onClick={() => void billing.refetch()}>Try again</Button>
      </Empty>
    );
  }
  if (b.state === 'no_account' && payUrl(b.booking_url))
    return (
      <Empty icon="lock" title={NOT_OK.no_account ?? 'No billing account yet'}>
        <a className="verb" href={payUrl(b.booking_url)} target="_blank" rel="noreferrer" title="Talk to us">
          Add credit
        </a>
      </Empty>
    );
  if (!['ok', 'low', 'paused', 'grace'].includes(b.state) || !Array.isArray(b.usage)) return <Empty icon="lock" title={NOT_OK[b.state] ?? 'Billing is not set up here'} />;
  const total = b.usage.reduce((n, u) => n + u.total_micros, 0);
  const credits = b.credits.filter((c) => c.left_micros > 0 || c.current);
  return (
    <>
      <FiguresStrip
        label="Billing"
        figures={[
          { label: 'Balance', value: micros(b.balance_micros), target: 'setting:balance', sub: status(b) },
          { label: month && month !== b.month ? monthWords(b.month) : 'This month', value: micros(total) },
          { label: 'Plan', value: b.cloud?.plan ?? 'Your own servers', sub: b.cloud?.trial_days_left != null ? `${b.cloud.trial_days_left} days of trial left` : undefined },
        ]}
      />
      <Section name="Credit" count={credits.length} target="setting:credit">
        {credits.length === 0 ? (
          <Empty title="No credit" />
        ) : (
          <>
            <div className="tbl-head g-cred" aria-hidden="true">
              <span>Credit</span>
              <span className="is-num">Given</span>
              <span className="is-num">Left</span>
              <span className="is-num">Ends</span>
            </div>
            {credits.map((c, i) => (
              <div key={`${c.label}-${i}`} className={c.current ? 'row g-cred' : 'row g-cred is-off'} title={c.label}>
                <span className="row-title">{c.label}</span>
                <span className="row-fig">{micros(c.amount_micros)}</span>
                <span className="row-fig is-strong">{micros(c.left_micros)}</span>
                <span className="row-fig">{c.expires ? new Date(c.expires).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : 'Never'}</span>
              </div>
            ))}
          </>
        )}
      </Section>
      <Days b={b} month={month} onMonth={setMonth} />
      <Statement b={b} />
      {/* Plans, cards and credit change on the billing page, by the person: links, never an action Tico can take. */}
      <Pay b={b} />
    </>
  );
}

/* History (log, runs, changes) lives in its own file. */
export { History } from './HistoryViews';

const SAY: Record<string, string> = { ok: 'Fine', info: 'Note', warn: 'Look', bad: 'Broken' };

/** A check's first fix: a link where the new app has the page, else its words. */
function Fix({ f }: { f: HealthFix }) {
  const to = fixHref(f);
  if (!to) return <span className="row-cell">{f.label}</span>;
  return to.startsWith('/') ? (
    <To className="verb" href={to}>
      {f.label}
    </To>
  ) : (
    <a className="verb" href={to} target="_blank" rel="noreferrer">
      {f.label}
    </a>
  );
}

/** "Checked 9:41 AM", with the day when it was not today. */
function checkedWords(at: string | undefined): string {
  const d = at ? new Date(at) : null;
  if (!d || Number.isNaN(d.getTime())) return '';
  const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  return `Checked ${d.toDateString() === new Date().toDateString() ? time : `${fmtDate(d)}, ${time}`}`;
}

function CheckRow({ c }: { c: HealthCheck }) {
  const fine = c.status === 'ok' || c.status === 'info';
  return (
    <div className={fine ? 'row g-hl' : 'row g-hl is-bad'} title={c.label} data-target={`check:${c.id}`}>
      <span className="row-title">{c.label}</span>
      <span className="row-sub">{c.summary}</span>
      <span className="hl-act">{c.fixes?.[0] && <Fix f={c.fixes[0]} />}</span>
      <span className="row-state">
        <Word fine={fine}>{SAY[c.status] ?? c.status}</Word>
      </span>
    </div>
  );
}

/** The page head's one button: read every check again now. */
export function CheckNow() {
  const [busy, setBusy] = useState(false);
  const check = async () => {
    setBusy(true);
    const r = await run('settings.checkHealth', {}, { via: 'click' });
    setBusy(false);
    if (!r.ok) toast(refusal(r, "Couldn't check"));
  };
  return (
    <Button busy={busy} onClick={() => void check()}>
      Check now
    </Button>
  );
}

/** An agent's queue as Pipeline splits it (backend/health.py `_queue_words`). */
const queueWords = (r: StuckAgent & { queued?: number; dead?: number; held?: number }) =>
  [r.queued ? `${r.queued} queued` : '', r.dead ? `${r.dead} behind an interrupted run` : '', r.held ? `${r.held} held on purpose` : ''].filter(Boolean);

/** Agents with queued work that is not starting, or left in setup: each opens its own page. */
function Stuck({ rows }: { rows: StuckAgent[] }) {
  if (rows.length === 0) return null;
  return (
    <Section name="Stuck agents" count={rows.length} target="setting:stuck">
      {rows.map((r) => (
        <To key={r.bot} href={`/team/${encodeURIComponent(r.bot)}`} className="row g-hl is-bad" title={r.name} data-target={`agent:${r.bot}`}>
          <span className="row-title">{r.name}</span>
          <span className="row-sub">{[r.why, ...queueWords(r)].join(', ')}</span>
          <span className="hl-act" />
          <span className="row-state">
            <Word>Stuck</Word>
          </span>
        </To>
      ))}
    </Section>
  );
}

/** What Check health still has open: a setup problem BotOps is fixing, or a Tico bug to report to Tico support. */
function Findings() {
  const found = useQuery({ ...findingsQuery, retry: false });
  const me = useQuery(meQuery);
  const owner = me.data?.role === 'owner';
  const list = found.data?.findings ?? [];
  if (!found.data?.findings || (list.length === 0 && !owner)) return null;
  const botops = found.data.botops;
  return (
    <Section name="Findings" count={list.length} target="setting:findings">
      {owner && (
        <Facts label="Findings">
          <SwitchLine
            label="Report Tico bugs without asking"
            checked={!!found.data.reports_without_asking}
            onChange={(on) => void act('settings.reportRule', { on })}
          />
        </Facts>
      )}
      {list.map((f) => {
        const setup = f.kind === 'setup';
        const state = setup
          ? botops === false
            ? 'Not fixed: no BotOps agent'
            : 'Being fixed by BotOps'
          : f.ticket_id
            ? 'Reported to Tico support'
            : f.approval_id
              ? 'Report waits in Needs you'
              : 'Tico bug, not reported';
        const n = Number(f.occurrences) || 1;
        const seen = `seen ${n.toLocaleString('en-US')} ${n === 1 ? 'time' : 'times'}${f.last_seen_at ? `, last ${fmtAge(f.last_seen_at)}` : ''}`;
        return (
          <div key={f.key} className="row g-hl is-bad" title={f.title} data-target={`finding:${f.key}`}>
            <span className="row-title">{f.title}</span>
            <span className="row-sub">{`${state}, ${seen}`}</span>
            <span className="hl-act">
              {!setup && !f.ticket_id && !f.approval_id && <Verb onClick={() => void act('settings.reportFinding', { key: f.key })}>Report</Verb>}
              {setup && f.task_id && (
                <To className="verb" href={`/task/${encodeURIComponent(f.task_id)}`}>
                  Open task
                </To>
              )}
            </span>
            <span className="row-state">
              <Word>{setup ? 'Setup' : 'Bug'}</Word>
            </span>
          </div>
        );
      })}
    </Section>
  );
}

/** The installer's checks, run by the server against its own address; kept beside the list, not in it. */
function SetupCheck() {
  const [busy, setBusy] = useState(false);
  const [rows, setRows] = useState<SetupCheckRow[] | null>(null);
  const [head, setHead] = useState('');
  const check = async () => {
    setBusy(true);
    const r = await run('settings.setupCheck', {}, { via: 'click' });
    setBusy(false);
    if (!r.ok) return toast(refusal(r, "Couldn't run the checks"));
    setRows((r.out as { checks?: SetupCheckRow[] } | undefined)?.checks ?? []);
    setHead(r.did ?? '');
  };
  return (
    <>
      {rows && (
        <Section name="Setup check" count={rows.length} summary={head} target="setting:setup-check">
          {rows.map((c, i) => {
            const fine = c.status === 'ok' || c.status === 'info';
            return (
              <div key={`${c.name}-${i}`} className={fine ? 'row g-hl' : 'row g-hl is-bad'} title={c.name}>
                <span className="row-title">{c.name}</span>
                <span className="row-sub">{!fine && c.fix ? `${c.detail} Fix: ${c.fix}` : c.detail}</span>
                <span className="hl-act" />
                <span className="row-state">
                  <Word fine={fine}>{SAY[c.status] ?? c.status}</Word>
                </span>
              </div>
            );
          })}
        </Section>
      )}
      <Foot>
        <Verb onClick={() => void (busy ? undefined : check())}>{busy ? 'Checking' : 'Setup check'}</Verb>
      </Foot>
    </>
  );
}

export function Health() {
  const health = useQuery(healthQuery);
  const h = health.data;
  if (!h) return null;
  const isFine = (c: HealthCheck) => c.status === 'ok' || c.status === 'info';
  const bad = h.checks.filter((c) => !isFine(c));
  const ok = h.checks.filter(isFine);
  const checked = checkedWords(h.checked);
  return (
    <>
      {bad.length > 0 && (
        <Section name="Needs a look" count={bad.length} summary={checked} target="setting:health-bad">
          {bad.map((c) => (
            <CheckRow key={c.id} c={c} />
          ))}
        </Section>
      )}
      <Section name={bad.length ? 'Fine' : 'Every part is fine'} count={ok.length} summary={bad.length ? undefined : checked} target="setting:health">
        {h.checks.length === 0 ? (
          <Empty title="No checks yet" />
        ) : (
          ok.map((c) => <CheckRow key={c.id} c={c} />)
        )}
      </Section>
      <Stuck rows={h.stuck ?? []} />
      <Findings />
      <SetupCheck />
    </>
  );
}

/** A product update by hand: a title and its bullets, one a line. */
function AddUpdate() {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const bullets = text
    .split('\n')
    .map((b) => b.replace(/^\s*[-*•]\s*/, '').trim())
    .filter(Boolean);
  const ready = !!title.trim() && title.trim().length <= 90 && bullets.length > 0 && bullets.length <= 12;
  const publish = async () => {
    setBusy(true);
    const ok = await act('settings.productUpdate', { title: title.trim(), bullets });
    setBusy(false);
    if (!ok) return;
    setOpen(false);
    setTitle('');
    setText('');
  };
  return (
    <>
      <IconButton icon="plus" label="Add update" size="sm" quiet onClick={() => setOpen(true)} />
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Product update"
        actions={
          <>
            <Button variant="quiet" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" busy={busy} disabled={!ready} onClick={() => void publish()}>
              Publish
            </Button>
          </>
        }
      >
        <div className="set-add">
          <Field label="Title" value={title} onValueChange={setTitle} autoFocus />
          <Field label="Bullets, one a line" value={text} onValueChange={setText} area />
        </div>
      </Dialog>
    </>
  );
}

/** What a task an agent shipped came to: its title and its outcome note, over the changelog (?open=<task id>). */
function Outcome({ task }: { task: string }) {
  const navigate = useNavigate();
  const t = useQuery({ ...taskDetailQuery(task), retry: false });
  const close = () => void navigate({ to: '/settings/$section', params: { section: 'changelog' }, search: {} });
  const note = (t.data as { note?: string | null } | undefined)?.note?.trim();
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && close()}
      title="Reported task outcome"
      description={t.data?.title ?? (t.isError ? "Couldn't open the task" : undefined)}
      actions={
        <Button variant="quiet" onClick={close}>
          Close
        </Button>
      }
    >
      {t.data && <Markdown text={note || 'No outcome recorded.'} compact />}
    </Dialog>
  );
}

/** Lines agents shipped; one that came from a task opens its outcome. */
function Activity() {
  const log = useQuery(activityQuery);
  const entries = log.data?.entries ?? [];
  if (entries.length === 0) return null;
  return (
    <Section name="Agent activity" count={entries.length} defaultOpen={false} target="setting:activity">
      <ul className="cl-list">
        {entries.map((e) => (
          <li key={e.id} className="cl-act">
            <span>{e.title}</span>
            {e.task_id && <Verb onClick={() => void run('settings.openOutcome', { task: e.task_id }, { via: 'click' })}>View outcome</Verb>}
          </li>
        ))}
      </ul>
    </Section>
  );
}

/** A version's day: when it was published, else the day it shipped (UTC). */
const versionDay = (e: ChangelogEntry | undefined) => (e ? fmtDate(e.published_at ?? e.shipped_at?.slice(0, 10)) : '');

export function Changelog({ open }: { open?: string }) {
  const log = useQuery(changelogQuery);
  if (!log.data) return null;
  return <Versions first={log.data} open={open} />;
}

function Versions({ first, open }: { first: ChangelogList; open?: string }) {
  // Load older adds the next page's offset; each page is its own read.
  const [offsets, setOffsets] = useState<number[]>([]);
  const older = useQueries({ queries: offsets.map((o) => changelogPage(o)) });
  const pages = [first, ...older.flatMap((q) => (q.data ? [q.data] : []))];
  const seen = new Set<string>();
  const entries = pages.flatMap((p) => p.entries).filter((e) => !seen.has(e.id) && !!seen.add(e.id));
  const loading = older.some((q) => q.isPending);
  const next = loading ? (offsets.at(-1) ?? null) : (pages.at(-1)?.next_offset ?? null);
  // Opening the page is reading it: what was unread is acknowledged at once, and stays marked New for this visit
  // (the list is not read again until the next one).
  const sent = useRef(new Set<string>());
  const unread = entries
    .filter((e) => e.kind === 'product' && e.unread)
    .map((e) => e.id)
    .join(' ');
  useEffect(() => {
    const ids = unread.split(' ').filter((id) => id && !sent.current.has(id));
    if (ids.length === 0) return;
    for (const id of ids) sent.current.add(id);
    void run('settings.changelogRead', { ids: ids.slice(0, 250) }, { via: 'click' });
  }, [unread]);
  const add = first.can_add ? <AddUpdate /> : undefined;
  const versions = new Map<string, ChangelogEntry[]>();
  for (const e of entries) {
    const v = e.version ?? 'Earlier';
    versions.set(v, [...(versions.get(v) ?? []), e]);
  }
  // Add update sits in the head of the first section: Changelog while there is nothing yet, else the newest version.
  return (
    <>
      {entries.length === 0 ? (
        <Section name="Changelog" actions={add} target="setting:changelog">
          <div className="row is-off">
            <span className="row-title">No changes yet</span>
          </div>
        </Section>
      ) : (
        [...versions].map(([version, list], i) => (
          <Section
            key={version}
            name={
              list.some((e) => e.unread) ? (
                <>
                  {version} <Pill>New</Pill>
                </>
              ) : (
                version
              )
            }
            label={version}
            count={list.length}
            summary={versionDay(list[0])}
            actions={i === 0 ? add : undefined}
            target={`version:${version}`}
          >
            <ul className="cl-list">
              {list.map((e) => (
                <li key={e.id}>
                  <span className="cl-kind">{/fix/i.test(e.area) ? 'Fixed' : ''}</span>
                  <span>{e.title}</span>
                </li>
              ))}
            </ul>
          </Section>
        ))
      )}
      {next != null && (
        <Foot>
          <Verb
            onClick={() => {
              if (loading) return;
              const failed = older.find((q) => q.isError);
              if (failed) void failed.refetch();
              else setOffsets((o) => (o.includes(next) ? o : [...o, next]));
            }}
          >{loading ? 'Loading' : 'Load older'}</Verb>
        </Foot>
      )}
      <Activity />
      {open && <Outcome task={open} />}
    </>
  );
}
