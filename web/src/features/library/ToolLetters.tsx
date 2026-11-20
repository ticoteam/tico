import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { Link, useRouter } from '@tanstack/react-router';
import { useState } from 'react';
import { refusal, run } from '@/actions';
import type { LetterOut, LetterOutContents, ToolIndexRow } from '@/api/schema';
import { useActors } from '@/data';
import { useFollow } from '@/live/follow';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem } from '@/kit/Menu';
import { toast } from '@/kit/Toast';
import { Face, More, press } from './parts';
import { addressGrantsQuery, lettersInQuery, lettersQuery, olderLettersQuery, postalAddressesQuery, type LetterIn, type PostalAddress } from './queries';
import { ADDRESS_STATE, addressLine, KINDS, PostalAddresses, scopeOf, useLetterReaders, useLetters, usd, type Postal } from './ToolSettings';
import { AddSomeone, bad, ReadFault, ToolPage, UseRow, useAdmin, WhoRow } from './ToolShell';

/* The mailing address (old inboxes-letters.ts, inboxes-outbox.ts) on the one tool page: the company's (`address`:
   every address, the letters' rules, letters in and out) and one address's own (`address:<id>`: its setup, who may
   read it, its letters). A letter in opens its scan or its task, and an admin retries a failed one; a letter out has
   its details, proof and Review, and Stop while it can. */

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

const KIND_WORD: Record<string, string> = { legal: 'Legal', government_tax: 'Government or tax', bill: 'Bill', check: 'Check', financial: 'Financial', junk: 'Junk', other: 'Other' };
const isFile = (url: string | null | undefined) => !!url && /^\/api\/v2\/files\/[A-Za-z0-9_-]+$/.test(url);
const open = (url: string) => void window.open(url, '_blank', 'noopener');

/** The letters that came in: each with its scan, the task it went to, and (an admin's) Retry when it failed. */
function LettersIn({ items }: { items: LetterIn[] }) {
  const admin = useAdmin();
  const router = useRouter();
  return items.map((m) => {
    const title = m.sender || m.summary || 'A letter';
    const scan = isFile(m.file_url) ? m.file_url : null;
    const retry = admin && m.status === 'failed';
    return (
      <UseRow key={m.id} title={title} ctx={[m.kind && KIND_WORD[m.kind], word(m.status)].filter(Boolean).join(', ')} at={m.received_at} lead={<Icon name="download" />}>
        {(scan || m.task_id || retry) && (
          <Menu trigger={<IconButton icon="more" label={`${title} options`} size="sm" />}>
            {scan && <MenuItem label="Open scan" onSelect={() => open(scan)} />}
            {m.task_id && <MenuItem label="Open task" onSelect={() => void router.navigate({ href: `/task/${encodeURIComponent(m.task_id as string)}` })} />}
            {retry && <MenuItem label="Retry" onSelect={() => void press('tools.retryLetter', { letter: m.id }, 'Not retried')} />}
          </Menu>
        )}
      </UseRow>
    );
  });
}

/* ---------------------------------------------------------------- the company's mailing address */

export function CompanyAddress({ row }: { row: ToolIndexRow | undefined }) {
  useFollow({ topic: 'letters' });
  const letters = useQuery(lettersQuery);
  const inbound = useQuery(lettersInQuery);
  const rules = useLetters();
  const readers = useLetterReaders();
  return (
    <ToolPage
      name={row?.name ?? 'Mailing address'}
      kind="address"
      row={row}
      problem={row && bad(row)}
      q={letters}
      target={`tool:${row?.id ?? 'address'}`}
      about={row?.detail && <LabelValue label="Address">{row.detail}</LabelValue>}
      connects={
        <>
          <Facts>
            <LabelValue label="Through">{row?.way || 'Tico'}</LabelValue>
            <LabelValue label="State">{row?.status.text ?? ''}</LabelValue>
          </Facts>
          <PostalAddresses />
        </>
      }
      who={readers.who}
      whoActions={readers.add}
      can={rules.facts}
      canActions={rules.actions}
      recent={
        (!!letters.data?.length || !!inbound.data?.length) && (
          <>
            <LettersOut letters={letters.data ?? []} />
            <LettersIn items={inbound.data ?? []} />
          </>
        )
      }
    />
  );
}

/* ---------------------------------------------------------------- one address */

const norm = (s: string | null | undefined) =>
  String(s || '')
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '');

/** Which address a letter came to: the one it names, else the only (or first) open one, so every letter has a page. */
export function homeOf(item: LetterIn, addresses: PostalAddress[]): string {
  if (item.provider === 'email') return 'intake';
  const named = item.address_id && addresses.find((a) => scopeOf(a) === item.address_id || String(a.assignment_id || a.id || '') === item.address_id);
  if (named) return scopeOf(named);
  const want = norm(item.address);
  const hit = want && addresses.find((a) => norm(a.assignment_id || a.id) === want || (norm(a.address) && (norm(a.address).includes(want) || want.includes(norm(a.address)))));
  const home = hit || addresses.find((a) => a.status !== 'closed') || addresses[0];
  return home ? scopeOf(home) : '';
}

/** Ordered, then Form 1583 signed and accepted, then receiving. */
const STEPS = [
  ['ordered', 'Ordered'],
  ['pending_1583', 'Form 1583'],
  ['active', 'Receiving'],
] as const;

/** One mailing address, or an open order for one (`order-<id>`). */
export function AddressOne({ id }: { id: string }) {
  const q = useQuery({ ...postalAddressesQuery, retry: false });
  const letters = useQuery(lettersInQuery);
  const admin = useAdmin();
  const order = id.startsWith('order-') ? q.data?.orders?.find((o) => `order-${o.order_id}` === id) : undefined;
  const all = q.data?.addresses ?? [];
  const a = order ? undefined : all.find((x) => scopeOf(x) === id);
  const it = a ?? order;
  const name = a?.status === 'active' && a.address ? a.address : it ? `${it.state}, ${KINDS[it.kind] ?? it.kind}` : 'Mailing address';
  const step = a?.status === 'closed' ? STEPS.length - 1 : STEPS.findIndex(([k]) => k === (a?.status ?? 'ordered'));
  const readers = useAddressReaders(id, admin && !!a);
  const check = async () => {
    const r = await run('tools.checkAddress', { address: id }, { via: 'click' });
    const out = r.out as { ok: boolean; text: string } | undefined;
    toast(r.ok && out ? { text: out.text, kind: out.ok ? 'done' : 'failed' } : refusal(r, 'Not checked'));
  };
  return (
    <ToolPage
      name={name}
      kind="address"
      problem={a && a.status !== 'active' ? (ADDRESS_STATE[a.status] ?? a.status) : null}
      q={{ ...q, data: q.data && (it ?? null) }}
      target={`tool:address:${id}`}
      about={
        it && (
          <>
            {a?.address && <LabelValue label="Address">{a.address}</LabelValue>}
            <LabelValue label="State">{it.state}</LabelValue>
            <LabelValue label="Type">{KINDS[it.kind] ?? it.kind}</LabelValue>
            {a && <LabelValue label="Registered agent">{(a as PostalAddress & { agent?: boolean }).agent ? 'Yes' : 'No'}</LabelValue>}
          </>
        )
      }
      connects={
        <Facts>
          <LabelValue label="Through">Tico Gateway</LabelValue>
          {STEPS.map(([k, label], n) => (
            <LabelValue key={k} label={label}>
              <span aria-current={n === step ? 'step' : undefined}>{n < step || step === STEPS.length - 1 ? 'Done' : n === step ? 'Now' : 'Not yet'}</span>
            </LabelValue>
          ))}
        </Facts>
      }
      connectsActions={a && <IconButton icon="refresh" label="Check connection" size="sm" onClick={() => check()} />}
      who={readers.who}
      whoActions={readers.add}
      recent={a && <LettersIn items={(letters.data ?? []).filter((m) => homeOf(m, all) === id)} />}
      bare={!it}
    >
      {!it && <Empty title="No such address" />}
    </ToolPage>
  );
}

/** Who may read every letter and scan at one address, private ones included (an admin's). */
function useAddressReaders(scope: string, on: boolean) {
  const q = useQuery({ ...addressGrantsQuery(scope), enabled: on });
  const actors = useActors();
  if (!on) return { who: undefined, add: null };
  if (q.isError && !q.data) return { who: <ReadFault name="Who may use it" error={q.error} onRetry={() => void q.refetch()} />, add: null };
  const subject = (type: string, who: string) => `${type === 'bot' ? 'bot' : 'human'}:${who}`;
  const grants = q.data ?? [];
  const taken = new Set(grants.map((g) => subject(g.grantee_type, g.grantee_id)));
  const set = (who: string, read: boolean) => press('tools.setAccess', { kind: 'address', id: scope, who, level: read ? 'read' : 'none' }, read ? 'Not added' : 'Not removed');
  return {
    who: grants.map((g) => {
      const actor = subject(g.grantee_type, g.grantee_id);
      const name = actors.find((x) => x.id === actor)?.name ?? g.grantee_id;
      return <WhoRow key={actor} actor={actor} name={name} onRemove={() => void set(actor, false)} />;
    }),
    add: <AddSomeone choices={actors.filter((x) => !taken.has(x.id))} onPick={(id) => void set(id, true)} />,
  };
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
    <UseRow title={letterTitle(l)} ctx={word(l.status)} at={l.created_at} lead={<Icon name="send" />}>
      {l.can_cancel && <IconButton icon="close" label="Stop this letter" size="sm" onClick={() => press('tools.cancelLetter', { letter: l.id }, 'Not stopped')} />}
      {full && (
        <Menu trigger={<IconButton icon="more" label={`${letterTitle(l)} options`} size="sm" />}>
          <MenuItem label="Details" onSelect={() => onDetails(full)} />
          {proof && <MenuItem label="Proof PDF" onSelect={() => open(proof)} />}
          {waits && <MenuItem label="Review" onSelect={() => void router.navigate({ href: '/flow' })} />}
        </Menu>
      )}
    </UseRow>
  );
}

/** The letters sent, newest first; Load more reads the older ones. */
function LettersOut({ letters }: { letters: LetterOut[] }) {
  const [details, setDetails] = useState<LetterOutContents | null>(null);
  const [older, setOlder] = useState(false);
  const last = letters.at(-1)?.id;
  return (
    <>
      {letters.map((l) => (
        <LetterRow key={l.id} l={l} onDetails={setDetails} />
      ))}
      {last && letters.length >= PAGE && (older ? <OlderLetters before={last} onDetails={setDetails} /> : <More onClick={() => setOlder(true)}>Load more</More>)}
      {details && <LetterDetails l={details} onClose={() => setDetails(null)} />}
    </>
  );
}

function OlderLetters({ before, onDetails }: { before: string; onDetails: (l: LetterOutContents) => void }) {
  const q = useInfiniteQuery(olderLettersQuery(before));
  return (
    <>
      {q.data?.pages.flatMap((p) => p.items).map((l) => <LetterRow key={l.id} l={l} onDetails={onDetails} />)}
      {q.isError && <ReadFault name="Older letters" error={q.error} onRetry={() => void q.refetch()} />}
      {q.hasNextPage && !q.isFetchingNextPage && <More onClick={() => q.fetchNextPage()}>Load more</More>}
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
        {l.status === 'needs_review' && <LabelValue label="Review">An admin confirms how it went</LabelValue>}
      </Facts>
      {l.task_id && (
        <Link to="/task/$task" params={{ task: l.task_id }} onClick={onClose}>
          Open task
        </Link>
      )}
    </Dialog>
  );
}
