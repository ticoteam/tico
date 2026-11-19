import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { directoryQuery } from '@/data';
import { Ago } from '@/kit/Ago';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { Link as To } from '@/kit/Link';
import { Select } from '@/kit/Select';
import { run } from '@/actions/run';
import { FiguresStrip, Section } from '@/pagekinds';
import { fixHref } from './fixes';
import { Foot, Verb, Word, act, micros } from './parts';
import { billingQuery, changelogQuery, healthQuery, historyFilter, historyFor, type HealthFix } from './queries';

/* The sections that are mostly read: Billing, History, Health, Changelog. */

// backend/company_billing.py: anything but `ok` comes with no figures.
const NOT_OK: Record<string, string> = {
  no_gateway: 'Billing is not set up here',
  unreachable: "Billing can't be reached",
  no_account: 'No billing account yet',
  refused: 'Billing refused this server',
};

export function Billing() {
  const billing = useQuery(billingQuery);
  const b = billing.data;
  if (!b) return null;
  if (b.state !== 'ok' || !Array.isArray(b.usage)) return <Empty icon="lock" title={NOT_OK[b.state] ?? 'Billing is not set up here'} />;
  const month = b.usage.find((u) => u.period === b.month) ?? b.usage[0];
  const credits = b.credits.filter((c) => c.left_micros > 0 || c.current);
  return (
    <>
      <FiguresStrip
        label="Billing"
        figures={[
          { label: 'Balance', value: micros(b.balance_micros), target: 'setting:balance' },
          { label: 'This month', value: micros(month?.total_micros ?? 0) },
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
      {b.cloud?.billing_url && (
        <Foot>
          {/* Plans and cards change on the billing page, by the person: a link, never an action Tico can take. */}
          <a className="verb" href={b.cloud.billing_url} target="_blank" rel="noreferrer">
            Change plan
          </a>
        </Foot>
      )}
    </>
  );
}

const KIND: Record<string, string> = {
  run: 'Run',
  task: 'Task',
  'outbound.blocked': 'Outbound blocked',
  'credential.revealed': 'Credential revealed',
  'permission.changed': 'Permission change',
  decision: 'Decision',
};

const EVERYONE = '';

/** `who` (the address's `?who=`): one person's or agent's changes. */
export function History({ who }: { who?: string }) {
  const history = useQuery(historyFor(who));
  const dir = useQuery(directoryQuery);
  const h = history.data;
  if (!h) return null;
  const options = [
    { value: EVERYONE, label: 'Everyone' },
    ...(dir.data?.actors ?? []).map((a) => ({ value: a.id, label: a.name, meta: a.kind === 'agent' ? 'Agent' : undefined })),
  ];
  const pick = (
    <Select
      label="Who"
      labelHidden
      look="quiet"
      value={who ?? EVERYONE}
      options={options}
      onValueChange={(v) => void run('page.filter', { search: { who: v || null } }, { via: 'click' })}
    />
  );
  return (
    <Section name="History" count={h.items.length} actions={pick} target="setting:history">
      {h.items.length === 0 ? (
        <Empty title="Nothing yet" />
      ) : (
        h.items.map((it) => {
          const who = dir.data?.byId[it.actor];
          const name = who?.name ?? h.names[it.actor] ?? it.actor;
          const title = [it.subject, it.result].filter(Boolean).join(': ') || (KIND[it.kind] ?? it.kind);
          return (
            <div key={it.id} className="row g-chg" title={`${name}, ${title}`}>
              <Avatar kind={who?.kind ?? (it.actor.startsWith('bot:') ? 'agent' : 'person')} name={name} hue={who?.hue} decorative />
              <span className="row-title">
                {title}
                <span className="row-sub"> {KIND[it.kind] ?? it.kind}</span>
              </span>
              <Ago at={it.at} className="row-age" />
            </div>
          );
        })
      )}
      <Foot>
        <a className="verb" href={`/api/v2/history.csv${historyFilter(who)}`} download>
          Download CSV
        </a>
      </Foot>
    </Section>
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
      <Foot>
        <Verb onClick={() => setOpen(true)}>Add update</Verb>
      </Foot>
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

export function Changelog() {
  const log = useQuery(changelogQuery);
  const entries = log.data?.entries ?? [];
  if (!log.data) return null;
  const add = log.data.can_add ? <AddUpdate /> : null;
  if (entries.length === 0)
    return (
      <>
        <Empty title="No changes yet" />
        {add}
      </>
    );
  const versions = new Map<string, typeof entries>();
  for (const e of entries) {
    const v = e.version ?? 'Earlier';
    versions.set(v, [...(versions.get(v) ?? []), e]);
  }
  return (
    <>
      {[...versions].map(([version, list]) => (
        <Section key={version} name={version} count={list.length} target={`version:${version}`}>
          <ul className="cl-list">
            {list.map((e) => (
              <li key={e.id}>
                <span className="cl-kind">{/fix/i.test(e.area) ? 'Fixed' : ''}</span>
                <span>{e.title}</span>
              </li>
            ))}
          </ul>
        </Section>
      ))}
      {add}
    </>
  );
}
