import { useQueries, useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Link as To } from '@/kit/Link';
import { Pill } from '@/kit/Pill';
import { Select } from '@/kit/Select';
import { run } from '@/actions/run';
import { refusal } from '@/actions';
import { fmtDate } from '@/lib';
import { toast } from '@/kit/Toast';
import { FiguresStrip, Section } from '@/pagekinds';
import { StateBody } from '@/pagekinds';
import { fixHref } from './fixes';
import { Foot, NO_ACCESS, Verb, Word, act, isForbidden, micros } from './parts';
import { billingFor, changelogQuery, healthQuery, type BillingMonth, type HealthFix } from './queries';
import type { ChangelogEntry, ChangelogList } from '@/api/schema';
import { changelogPage, type HealthCheck, type StuckAgent } from './queries';

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

/* HQ bills by UTC month and day ('2026-11', '2026-11-01'): read in UTC so a day never shifts. */
const utc = (key: string) => new Date(`${key.length === 7 ? `${key}-01` : key.slice(0, 10)}T00:00:00Z`);
const words = (key: string, o: Intl.DateTimeFormatOptions) => (Number.isNaN(utc(key).getTime()) ? key : utc(key).toLocaleDateString('en-US', { timeZone: 'UTC', ...o }));
const monthWords = (m: string) => words(m, { month: 'long', year: 'numeric' });
const dayWords = (d: string) => words(d, { weekday: 'short', month: 'short', day: 'numeric' });
/** Only a plain https address is a place to pay (old app cloud-access.ts cloudPayUrl). */
const payUrl = (u: unknown) => (typeof u === 'string' && /^https:\/\/\S+$/.test(u) ? u : '');

/** The status a figure carries: grace, paused, low, or a cloud company frozen or locked (old billingStatus). */
function status(b: BillingMonth): string | undefined {
  if (b.cloud?.access === 'locked') return 'Locked';
  if (b.cloud?.access === 'frozen') return 'Out of credit';
  if (b.state === 'grace') return b.grace_until ? `Servers stop ${dayWords(b.grace_until)}` : 'Servers stopping';
  if (b.state === 'paused') return 'Tico models paused';
  if (b.state === 'low') return 'Low balance';
  return b.record_only && !b.cloud ? 'Recorded, not charged' : undefined;
}

/** The month's statement and its CSV; the month is picked in its head. */
function Statement({ b, month, onMonth }: { b: BillingMonth; month: string; onMonth: (m: string) => void }) {
  const s = b.statement;
  if (!s) return null;
  const months = b.months?.length ? b.months : [b.month];
  const lines: [string, number][] = [
    ['Opening balance', s.opening_micros],
    ...Object.entries(s.credits_by_source ?? {}).map(([k, m]): [string, number] => [`Credit, ${SOURCE[k] ?? k}`, m]),
    ...Object.entries(s.debits_by_product ?? {}).map(([k, m]): [string, number] => [PRODUCT[k] ?? k, -m]),
    ...(s.expired_micros ? [['Expired credit', -s.expired_micros] as [string, number]] : []),
    ['Closing balance', s.closing_micros],
  ];
  return (
    <Section
      name="Statement"
      target="setting:statement"
      actions={
        <Select
          label="Month"
          labelHidden
          look="quiet"
          value={month || b.month}
          options={months.map((m) => ({ value: m, label: monthWords(m) }))}
          onValueChange={onMonth}
        />
      }
    >
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
    if (isForbidden(billing.error))
      return (
        <StateBody state="no-access" title="Billing" noAccess={NO_ACCESS} skeleton={null}>
          {null}
        </StateBody>
      );
    return (
      <Empty icon="alert" title="Billing did not load">
        <Button onClick={() => billing.refetch()}>Try again</Button>
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
          { label: month ? monthWords(b.month) : 'This month', value: micros(total) },
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
      <Statement b={b} month={month} onMonth={setMonth} />
      {/* Plans, cards and credit change on the billing page, by the person: links, never an action Tico can take. */}
      <Pay b={b} />
    </>
  );
}

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
    <Button busy={busy} onClick={() => check()}>
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
            <Button variant="primary" busy={busy} disabled={!ready} onClick={() => publish()}>
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

/** A version's day: when it was published, else the day it shipped (UTC). */
const versionDay = (e: ChangelogEntry | undefined) => (e ? fmtDate(e.published_at ?? e.shipped_at?.slice(0, 10)) : '');

export function Changelog() {
  const log = useQuery(changelogQuery);
  if (!log.data) return null;
  return <Versions first={log.data} />;
}

function Versions({ first }: { first: ChangelogList }) {
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
              <>
                {version}
                {list.some((e) => e.unread) ? <Pill>New</Pill> : version === first.current_version && i > 0 && <Pill>Yours</Pill>}
              </>
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
          >
            Load older
          </Verb>
        </Foot>
      )}
    </>
  );
}
