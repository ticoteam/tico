import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { Link, useRouter } from '@tanstack/react-router';
import { useState } from 'react';
import { refusal, run } from '@/actions';
import type { LetterOut, LetterOutContents } from '@/api/schema';
import { useActors, type Actor } from '@/data';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem } from '@/kit/Menu';
import { toast } from '@/kit/Toast';
import { Section } from '@/pagekinds';
import { Face, More, press } from './parts';
import { postalAddressesQuery, type PostalAddress } from './queries';
import { ADDRESS_STATE, addressLine, KINDS, scopeOf, usd, type Postal } from './ToolSettings';
import { KIND, SectionFault, Shell, stateOf, useAdmin } from './ToolShell';
import { addressGrantsQuery, lettersInQuery, olderLettersQuery, type LetterIn } from './toolMail';

/* A mailing address and its letters (old inboxes-letters.ts pmDetail, inboxes-outbox.ts): one address's own page
   (`address:<id>`: its setup steps, who may read it, its letters), the letters that came in with their scan, task and
   Retry, and the letters sent with their details, proof, Review and Load more. */

const STATUS_WORD: Record<string, string> = {
  queued: 'Waiting',
  pending: 'Waiting',
  ready: 'Waiting',
  quoted: 'Quoted',
  waiting_approval: 'Waits on approval',
  waiting_for_ok: 'Waits on approval',
  approved: 'Approved',
  submitting: 'Sending',
  submitted: 'Sent to print',
  needs_review: 'Needs review',
  printing: 'Printing',
  mailed: 'Mailed',
  in_transit: 'On its way',
  delivered: 'Delivered',
  returned: 'Returned',
  canceled: 'Stopped',
  cancelled: 'Stopped',
  declined: 'Declined',
  failed: 'Failed',
  filed: 'Filed',
  archived: 'Archived',
  new: 'New',
};
export const word = (s: string) => STATUS_WORD[s] ?? s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

/** Who a letter went to, when the server shows it (a redacted letter shows only its pages). */
export function letterTitle(l: LetterOut): string {
  if (!l.redacted) {
    const r = l.recipient as { name?: unknown; company?: unknown; city?: unknown };
    const who = [r.name, r.company].find((v) => typeof v === 'string' && v) as string | undefined;
    if (who) return typeof r.city === 'string' && r.city ? `${who}, ${r.city}` : who;
  }
  return l.page_count ? `A letter, ${l.page_count} page${l.page_count === 1 ? '' : 's'}` : 'A letter';
}

const short = (actor: string) => actor.slice(actor.indexOf(':') + 1);
/** A grant's teammate by the directory: `bot` grants name an agent, the rest a person. */
const actorOf = (actors: Actor[], type: string, who: string) => actors.find((a) => short(a.id) === who && (a.kind === 'agent') === (type === 'bot'));
const subject = (type: string, who: string) => `${type === 'bot' ? 'bot' : 'human'}:${who}`;

/* ---------------------------------------------------------------- letters in */

const KIND_WORD: Record<string, string> = { legal: 'Legal', government_tax: 'Government or tax', bill: 'Bill', check: 'Check', financial: 'Financial', junk: 'Junk', other: 'Other' };
const isFile = (url: string | null | undefined) => !!url && /^\/api\/v2\/files\/[A-Za-z0-9_-]+$/.test(url);

/** The letters that came in: each with its scan, the task it went to, and (an admin's) Retry when it failed. */
export function LettersIn({ items, name = 'Letters in' }: { items: LetterIn[]; name?: string }) {
  const admin = useAdmin();
  const router = useRouter();
  return (
    <Section name={name} count={items.length}>
      {!items.length && <Empty title="No letters yet" />}
      {items.map((m) => {
        const title = m.sender || m.summary || 'A letter';
        const scan = isFile(m.file_url) ? m.file_url : null;
        const retry = admin && m.status === 'failed';
        return (
          <div key={m.id} className="row urow" title={title} data-target={`letter-in:${m.id}`}>
            <span className="urow-lead" title="Came in">
              <Icon name="download" />
            </span>
            <span className="row-title">{title}</span>
            <span className="row-ctx">{[m.kind && KIND_WORD[m.kind], word(m.status)].filter(Boolean).join(', ')}</span>
            {(scan || m.task_id || retry) && (
              <Menu trigger={<IconButton icon="more" label={`${title} options`} size="sm" />}>
                {scan && <MenuItem label="Open scan" onSelect={() => void window.open(scan, '_blank', 'noopener')} />}
                {m.task_id && <MenuItem label="Open task" onSelect={() => void router.navigate({ href: `/task/${encodeURIComponent(m.task_id as string)}` })} />}
                {retry && <MenuItem label="Retry" onSelect={() => void press('tools.retryLetter', { letter: m.id }, 'Not retried')} />}
              </Menu>
            )}
            <Ago at={m.received_at} className="row-age" />
          </div>
        );
      })}
    </Section>
  );
}

/* ---------------------------------------------------------------- one address */

const norm = (s: string | null | undefined) => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');

/** Which address a letter came to: the one it names, else the only (or first) open one, so every letter has a page. */
export function homeOf(item: LetterIn, addresses: PostalAddress[]): string {
  if (item.provider === 'email') return 'intake';
  const named = item.address_id && addresses.find((a) => scopeOf(a) === item.address_id || String(a.assignment_id || a.id || '') === item.address_id);
  if (named) return scopeOf(named);
  const want = norm(item.address);
  const hit = want && addresses.find((a) => norm(a.assignment_id || a.id) === want || (norm(a.address) && (norm(a.address).includes(want) || want.includes(norm(a.address)))));
  const open = addresses.filter((a) => a.status !== 'closed');
  const home = hit || open[0] || addresses[0];
  return home ? scopeOf(home) : '';
}

const STEPS = [
  ['ordered', 'Ordered'],
  ['pending_1583', 'Form 1583'],
  ['active', 'Receiving'],
] as const;

/** Ordered, then Form 1583 signed and accepted, then receiving: where this address is. */
function Setup({ status }: { status: string }) {
  const at = STEPS.findIndex(([k]) => k === status);
  return (
    <Section name="Setup" target="tool-address:setup">
      {STEPS.map(([k, label], n) => (
        <div key={k} className="row urow" title={label} aria-current={n === at ? 'step' : undefined}>
          <span className="row-title">{label}</span>
          <span className="row-ctx">{n < at ? 'Done' : n === at ? 'Now' : ''}</span>
        </div>
      ))}
    </Section>
  );
}

/** One mailing address, or an open order for one (`order-<id>`). */
export function AddressOne({ id }: { id: string }) {
  const q = useQuery({ ...postalAddressesQuery, retry: false });
  const letters = useQuery(lettersInQuery);
  const admin = useAdmin();
  const order = id.startsWith('order-') ? q.data?.orders?.find((o) => `order-${o.order_id}` === id) : undefined;
  const all = q.data?.addresses ?? [];
  const a = order ? undefined : all.find((x) => scopeOf(x) === id);
  const name = a ? (a.status === 'active' && a.address ? a.address : `${a.state}, ${KINDS[a.kind] ?? a.kind}`) : order ? `${order.state}, ${KINDS[order.kind] ?? order.kind}` : 'Mailing address';
  const state = stateOf(q);
  const mine = (letters.data ?? []).filter((m) => homeOf(m, all) === id);
  const check = async () => {
    const r = await run('tools.checkAddress', { address: id }, { via: 'click' });
    const out = r.out as { ok: boolean; text: string } | undefined;
    toast(r.ok && out ? { text: out.text, kind: out.ok ? 'done' : 'failed' } : refusal(r, 'Not checked'));
  };
  return (
    <Shell name={name} kind="address" problem={a && a.status !== 'active' ? (ADDRESS_STATE[a.status] ?? a.status) : null} state={state} onRetry={() => void q.refetch()} target={`tool:address:${id}`}>
      {q.data && !a && !order && <Empty title="No such address" />}
      {(a || order) && (
        <Section name="What it is">
          <Facts>
            <LabelValue label="Kind">{KIND.address}</LabelValue>
            {a?.address && <LabelValue label="Address">{a.address}</LabelValue>}
            <LabelValue label="State">{(a ?? order)?.state}</LabelValue>
            <LabelValue label="Type">{KINDS[(a ?? order)?.kind ?? ''] ?? (a ?? order)?.kind}</LabelValue>
            {a && <LabelValue label="Registered agent">{(a as PostalAddress & { agent?: boolean }).agent ? 'Yes' : 'No'}</LabelValue>}
          </Facts>
        </Section>
      )}
      {order && <Setup status="ordered" />}
      {a && a.status !== 'active' && <Setup status={a.status} />}
      {a && (
        <Section name="How it connects">
          <Facts>
            <LabelValue label="Through">Tico Gateway</LabelValue>
            <LabelValue label="State">{ADDRESS_STATE[a.status] ?? a.status}</LabelValue>
          </Facts>
          <More onClick={() => void check()}>Check connection</More>
        </Section>
      )}
      {a && admin && <AddressReaders scope={id} />}
      {a && <LettersIn items={mine} />}
    </Shell>
  );
}

/** Who may read every letter and scan here, private ones included. Each letter also goes to whoever reads its task. */
function AddressReaders({ scope }: { scope: string }) {
  const q = useQuery(addressGrantsQuery(scope));
  const actors = useActors();
  if (q.isError && !q.data)
    return (
      <Section name="Who can read">
        <SectionFault name="Who can read" error={q.error} onRetry={() => void q.refetch()} />
      </Section>
    );
  if (!q.data) return null;
  const taken = new Set(q.data.map((g) => subject(g.grantee_type, g.grantee_id)));
  const free = actors.filter((x) => !taken.has(subject(x.kind === 'agent' ? 'bot' : 'person', short(x.id))));
  return (
    <Section
      name="Who can read"
      count={q.data.length}
      target={`tool-who:address:${scope}`}
      actions={
        free.length > 0 && (
          <Menu trigger={<IconButton icon="plus" label="Add someone" size="sm" />}>
            {free.map((x) => (
              <MenuItem
                key={x.id}
                label={x.name}
                lead={<Face actor={x.id} />}
                onSelect={() => void press('tools.setAddressAccess', { address: scope, type: x.kind === 'agent' ? 'bot' : 'person', who: short(x.id), read: true }, 'Not added')}
              />
            ))}
          </Menu>
        )
      }
    >
      {q.data.map((g) => {
        const name = actorOf(actors, g.grantee_type, g.grantee_id)?.name ?? g.grantee_id;
        return (
          <div key={`${g.grantee_type}:${g.grantee_id}`} className="row wrow" title={name}>
            <Face actor={subject(g.grantee_type, g.grantee_id)} name={name} />
            <span className="row-title">{name}</span>
            <IconButton
              icon="close"
              label={`Remove ${name}`}
              size="sm"
              onClick={() =>
                void press('tools.setAddressAccess', { address: scope, type: g.grantee_type === 'bot' ? 'bot' : 'person', who: g.grantee_id, read: false }, 'Not removed')
              }
            />
          </div>
        );
      })}
    </Section>
  );
}

/* ---------------------------------------------------------------- letters out */

const PAGE = 20;
const NOT_CHARGED = ['none', 'void', 'reversed'];

/** One letter sent: who it went to and its state; its ⋯ holds Details, the proof and Review; Stop while it can. */
function LetterRow({ l, onDetails }: { l: LetterOut; onDetails: (l: LetterOutContents) => void }) {
  const router = useRouter();
  const full = l.redacted ? null : l;
  const proof = full && isFile(full.proof) ? full.proof : null;
  const waits = l.status === 'waiting_for_ok' || l.status === 'waiting_approval';
  return (
    <div className="row urow" title={letterTitle(l)} data-target={`letter:${l.id}`}>
      <span className="urow-lead" title="Sent">
        <Icon name="send" />
      </span>
      <span className="row-title">{letterTitle(l)}</span>
      <span className="row-ctx">{word(l.status)}</span>
      {l.can_cancel && <IconButton icon="close" label="Stop this letter" size="sm" onClick={() => void press('tools.cancelLetter', { letter: l.id }, 'Not stopped')} />}
      {full && (
        <Menu trigger={<IconButton icon="more" label={`${letterTitle(l)} options`} size="sm" />}>
          <MenuItem label="Details" onSelect={() => onDetails(full)} />
          {proof && <MenuItem label="Proof PDF" onSelect={() => void window.open(proof, '_blank', 'noopener')} />}
          {waits && <MenuItem label="Review" onSelect={() => void router.navigate({ href: '/flow' })} />}
        </Menu>
      )}
      <Ago at={l.created_at} className="row-age" />
    </div>
  );
}

/** The letters sent, newest first; Load more reads the older ones. */
export function LettersOut({ letters }: { letters: LetterOut[] }) {
  const [details, setDetails] = useState<LetterOutContents | null>(null);
  const [older, setOlder] = useState(false);
  const last = letters.at(-1)?.id;
  return (
    <Section name="Letters out" count={letters.length}>
      {letters.length === 0 && <Empty title="No letters sent yet" />}
      {letters.map((l) => (
        <LetterRow key={l.id} l={l} onDetails={setDetails} />
      ))}
      {last && letters.length >= PAGE && (older ? <OlderLetters before={last} onDetails={setDetails} /> : <More onClick={() => setOlder(true)}>Load more</More>)}
      {details && <LetterDetails l={details} onClose={() => setDetails(null)} />}
    </Section>
  );
}

function OlderLetters({ before, onDetails }: { before: string; onDetails: (l: LetterOutContents) => void }) {
  const q = useInfiniteQuery(olderLettersQuery(before));
  const rows = q.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <>
      {rows.map((l) => (
        <LetterRow key={l.id} l={l} onDetails={onDetails} />
      ))}
      {q.isError && <SectionFault name="Older letters" error={q.error} onRetry={() => void q.refetch()} />}
      {q.hasNextPage && !q.isFetchingNextPage && <More onClick={() => void q.fetchNextPage()}>Load more</More>}
    </>
  );
}

const MAIL_CLASS = (c: string) => c.replace(/_/g, ' ').replace(/^./, (x) => x.toUpperCase());

function LetterDetails({ l, onClose }: { l: LetterOutContents; onClose: () => void }) {
  const by = l.requested_by || (l.acting_bot_id ? `bot:${l.acting_bot_id}` : '');
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={letterTitle(l)} actions={<Button onClick={onClose}>Close</Button>}>
      <Facts>
        <LabelValue label="To">{addressLine(l.recipient as Postal) || 'Not given'}</LabelValue>
        <LabelValue label="From">{addressLine(l.return_address as Postal) || 'Not given'}</LabelValue>
        <LabelValue label="State">{word(l.status)}</LabelValue>
        {l.page_count != null && <LabelValue label="Pages">{String(l.page_count)}</LabelValue>}
        <LabelValue label="Class">{MAIL_CLASS(l.mail_class)}</LabelValue>
        <LabelValue label="Print">{`${l.color ? 'Color' : 'Black and white'}, ${l.duplex ? 'both sides' : 'one side'}`}</LabelValue>
        {by && (
          <LabelValue label="Sent by">
            <Face actor={by} />
          </LabelValue>
        )}
        <LabelValue label="Total">{NOT_CHARGED.includes(l.billing) ? `${usd(l.total_micros)}, not charged` : usd(l.total_micros)}</LabelValue>
        {l.tracking.length > 0 && <LabelValue label="Tracking">{l.tracking.map((t) => (t.status ? `${t.barcode}, ${t.status}` : t.barcode)).join('; ')}</LabelValue>}
        {l.error && <LabelValue label="Problem">{l.error.replace(/_/g, ' ')}</LabelValue>}
        {l.status === 'needs_review' && <LabelValue label="Review">An admin confirms how it went; the total stays held until then.</LabelValue>}
      </Facts>
      {l.task_id && (
        <Link to="/task/$task" params={{ task: l.task_id }} onClick={onClose}>
          Open task
        </Link>
      )}
    </Dialog>
  );
}
