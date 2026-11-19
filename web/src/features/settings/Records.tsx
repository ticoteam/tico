import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useState } from 'react';
import { taskDetailQuery } from '@/features/task/exports';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Link as To } from '@/kit/Link';
import { Markdown } from '@/kit/Markdown';
import { Select } from '@/kit/Select';
import { run } from '@/actions/run';
import { FiguresStrip, Section } from '@/pagekinds';
import { fixHref } from './fixes';
import { Foot, Verb, Word, act, isForbidden, micros } from './parts';
import { activityQuery, billingDayQuery, billingFor, changelogQuery, healthQuery, type BillingLine, type BillingMonth, type HealthFix } from './queries';

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

export function Health() {
  const health = useQuery(healthQuery);
  const h = health.data;
  if (!h) return null;
  return (
    <Section name="Checks" count={h.checks.length} target="setting:health">
      {h.checks.length === 0 ? (
        <Empty title="No checks yet" />
      ) : (
        h.checks.map((c) => {
          const fine = c.status === 'ok' || c.status === 'info';
          return (
            <div key={c.id} className={fine ? 'row g-hl' : 'row g-hl is-bad'} title={c.label} data-target={`check:${c.id}`}>
              <span className="row-title">{c.label}</span>
              <span className="row-sub">{c.summary}</span>
              <span className="hl-act">{c.fixes?.[0] && <Fix f={c.fixes[0]} />}</span>
              <span className="row-state">
                <Word fine={fine}>{SAY[c.status] ?? c.status}</Word>
              </span>
            </div>
          );
        })
      )}
    </Section>
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

export function Changelog({ open }: { open?: string }) {
  const log = useQuery(changelogQuery);
  const entries = log.data?.entries ?? [];
  if (!log.data) return null;
  const add = log.data.can_add ? <AddUpdate /> : undefined;
  const versions = new Map<string, typeof entries>();
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
          <Section key={version} name={version} count={list.length} actions={i === 0 ? add : undefined} target={`version:${version}`}>
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
      <Activity />
      {open && <Outcome task={open} />}
    </>
  );
}
