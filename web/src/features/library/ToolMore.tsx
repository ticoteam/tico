import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { refusal, run } from '@/actions';
import type { Group, ToolIndexRow } from '@/api/schema';
import { directoryQuery, type Directory } from '@/data';
import { Ago } from '@/kit/Ago';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field, Search } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { LabelValue, Facts } from '@/kit/LabelValue';
import { Markdown } from '@/kit/Markdown';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { Row } from '@/kit/Row';
import { toast } from '@/kit/Toast';
import { FilterRow, PageMore, type FilterChip } from '@/pagekinds';
import { Foot, useGo } from './AddTool';
import { Face, Held, More, press, when } from './parts';
import {
  allMessagesQuery,
  envsOf,
  inboxesQuery,
  inboxMessagesQuery,
  keysOf,
  mailboxMailQuery,
  mailboxQuery,
  mailboxSendsQuery,
  servicePageQuery,
  servicesQuery,
  teamsQuery,
  vaultQuery,
  webhookQuery,
  webhookSendsQuery,
  type InMessage,
  type MessageFilter,
  type StoredKey,
  type Vault,
} from './queries';
import { Confirm, EditInboxDialog, MessageDialog, NewSecretDialog, ReleaseDialog, RevealDialog, WebhookDialog } from './ToolDialogs';
import { MayFacts, NumberRequests, SharedInboxDialog, useGoogle, useSending } from './ToolSettings';
import { AddSomeone, bad, Code, KIND, ToolPage, toolHref, useAdmin, UseRow, WhoRow } from './ToolShell';

/* The kinds of tool beyond an API and a website, each on the one tool page: a stored key no tool holds, a tool an
   agent declares, a mailbox, Email itself, a Tico address or number, phone numbers asked for, an outgoing webhook;
   and Messages, what came in to every address. Plus the Tools list's Who has access filter. */

/** A press whose answer is a line to show (a check): the line, or why it failed. */
async function check(id: string, input: unknown) {
  const r = await run(id, input, { via: 'click' });
  const out = r.out as { ok: boolean; text: string } | undefined;
  toast(r.ok && out ? { text: out.text, kind: out.ok ? 'done' : 'failed' } : refusal(r, 'Not checked'));
}

/* ---------------------------------------------------------------- a stored key no tool holds */

export const KEY_KIND: Record<string, string> = { api_key: 'API key', password: 'Password', token: 'Token', file: 'File', connection: 'Connection' };

/** A grant's subject as a name: a person or an agent the vault or the directory knows, or every computer. */
function subjectName(subject: string, vault: Vault | undefined, dir: Directory | undefined): string {
  if (subject === 'computers') return 'Every computer';
  const [kind, id = ''] = subject.split(':');
  if (kind === 'computer') return 'A computer';
  const known = kind === 'human' ? vault?.people.find((p) => p.id === id) : vault?.bots.find((b) => b.id === id || b.slug === id);
  return known?.name || dir?.byId[subject]?.name || id;
}

export function KeyView({ cid }: { cid: string }) {
  const q = useQuery(vaultQuery);
  const dir = useQuery(directoryQuery).data;
  const vault = q.data;
  const k = vault?.credentials.find((c) => c.id === cid);
  const [dialog, setDialog] = useState<'rotate' | 'reveal' | 'delete' | null>(null);
  const manage = !!vault?.can_manage;
  const taken = new Set(k?.grants.map((g) => g.subject));
  const adds = [
    ...(vault?.people ?? []).map((p) => ({ id: `human:${p.id}`, name: p.name || p.email || p.id })),
    ...(vault?.bots ?? []).map((b) => ({ id: `bot:${b.id}`, name: b.name })),
  ].filter((x) => !taken.has(x.id));
  return (
    <ToolPage
      name={k?.name ?? 'Key'}
      kind="key"
      kindWord={k && (KEY_KIND[k.kind] ?? k.kind)}
      lock={k?.stored}
      problem={k && !k.stored ? 'No value' : null}
      q={{ ...q, data: vault && (k ?? null) }}
      target={`tool:key:${cid}`}
      bare={!k}
      more={
        k && (
          <PageMore>
            {manage && <MenuItem label={k.stored ? 'Rotate' : 'Set value'} onSelect={() => setDialog('rotate')} />}
            {k.stored && k.can_reveal && <MenuItem label="Show key" onSelect={() => setDialog('reveal')} />}
            {(k.can_remove ?? manage) && (
              <>
                <MenuRule />
                <MenuItem label="Delete key" onSelect={() => setDialog('delete')} />
              </>
            )}
          </PageMore>
        )
      }
      about={
        k && (
          <>
            {k.username && <LabelValue label="Username">{k.username}</LabelValue>}
            {k.env && (
              <LabelValue label="Variable">
                <Code>{k.env}</Code>
              </LabelValue>
            )}
            {k.source && <LabelValue label="From">{k.source}</LabelValue>}
          </>
        )
      }
      connects={
        k && (
          <Facts>
            <LabelValue label="Value">
              {k.stored ? (
                <>
                  <Code>{k.preview || '••••••••'}</Code> <Held />
                </>
              ) : (
                'Not set'
              )}
            </LabelValue>
            {k.updated_at && <LabelValue label="Changed">{when(k.updated_at)}</LabelValue>}
            <LabelValue label="Last used">
              {k.last_used_at ? (
                <>
                  {k.last_used_by && <Face actor={`bot:${k.last_used_by}`} />}
                  {when(k.last_used_at)}
                </>
              ) : (
                'Never'
              )}
            </LabelValue>
          </Facts>
        )
      }
      who={k?.grants.map((g) => {
        const name = subjectName(g.subject, vault, dir);
        return (
          <WhoRow
            key={g.id}
            actor={/^(human|bot):/.test(g.subject) ? g.subject : null}
            name={name}
            onRemove={g.can_revoke ? () => void press('tools.revokeKey', { credential: cid, grant: g.id }, 'Not removed') : undefined}
          />
        );
      })}
      whoCount={k?.grants.length || undefined}
      whoActions={manage && !k?.remove_only && <AddSomeone choices={adds} onPick={(subject) => void press('tools.grantKey', { credential: cid, subject }, 'Not added')} />}
    >
      {!k && <Empty title="No key here" />}
      {k && dialog === 'rotate' && <RotateDialog k={k} onClose={() => setDialog(null)} />}
      {k && dialog === 'reveal' && <RevealDialog credential={k.id} title={k.name} onClose={() => setDialog(null)} />}
      {k && dialog === 'delete' && (
        <Confirm
          title={`Delete ${k.name}?`}
          description="Everyone it is given to loses it. This cannot be undone."
          label="Delete"
          onGo={() => press('tools.deleteKey', { credential: k.id }, 'Not deleted')}
          onClose={() => setDialog(null)}
        />
      )}
    </ToolPage>
  );
}

/** A new value, typed here and only sent (a password keeps its spaces). Also a key a declared tool names that the vault
    does not hold yet (`env`). */
function RotateDialog({ k, env, onClose }: { k?: StoredKey; env?: { env: string; name: string; service: string }; onClose: () => void }) {
  const [value, setValue] = useState('');
  const [busy, go] = useGo(onClose);
  const save = () =>
    k
      ? void go('tools.rotateKey', { credential: k.id, value: k.kind === 'password' ? value : value.trim() }, 'Not saved')
      : env && void go('tools.storeKey', { name: env.name, env: env.env, source: env.service, kind: /TOKEN/.test(env.env) ? 'token' : 'api_key', value: value.trim() }, 'Not added');
  const title = k ? (k.stored ? `Rotate ${k.name}` : `Set ${k.name}`) : `Add ${env?.env ?? ''}`;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={title}
      className="connect"
      actions={<Foot onClose={onClose} label={k ? (k.stored ? 'Rotate' : 'Save') : 'Add'} busy={busy} ready={!!value.trim()} onGo={save} />}
    >
      <Field label={k ? 'New value' : 'Key'} type="password" value={value} onValueChange={setValue} autoComplete="off" autoFocus wide />
    </Dialog>
  );
}

/* ---------------------------------------------------------------- a tool an agent declares */

const WRITE: Record<string, string> = { never: 'Read', approval: 'Asks first', allowed: 'Read and write' };
const RANK: Record<string, number> = { never: 0, approval: 1, allowed: 2 };

export function ServiceView({ row }: { row: ToolIndexRow }) {
  const service = row.service ?? row.id.slice(row.id.indexOf(':') + 1);
  const list = useQuery(servicesQuery);
  const page = useQuery(servicePageQuery(service));
  const vault = useQuery(vaultQuery).data;
  const dir = useQuery(directoryQuery).data;
  const [storing, setStoring] = useState<string | null>(null);
  const s = list.data?.find((x) => x.service === service);
  const live = s && s.status !== 'removed' ? s : undefined;
  const keys = live ? keysOf(live, vault?.credentials ?? []) : [];
  const held = new Set(keys.map((k) => k.env).filter(Boolean));
  const missing = live ? envsOf(live).filter((e) => !held.has(e)) : [];
  // Who has it: each agent at its highest level (with any problem its computer reported), then people a key is given to.
  const agents = new Map<string, { level: string; problem: string }>();
  for (const c of s?.connections ?? []) {
    const level = c.write ?? s?.writes ?? 'never';
    const had = agents.get(c.bot);
    if (!had || (RANK[level] ?? 0) > (RANK[had.level] ?? 0)) agents.set(c.bot, { level, problem: c.problem || had?.problem || '' });
    else if (c.problem && !had.problem) had.problem = c.problem;
  }
  for (const b of s?.bots ?? []) if (!agents.has(b)) agents.set(b, { level: s?.writes ?? 'never', problem: '' });
  const people = new Set(keys.flatMap((k) => k.grants.map((g) => g.subject)).filter((x) => x.startsWith('human:')));
  const body = page.data?.body?.trim();
  return (
    <ToolPage
      name={s?.title || row.name}
      kind={row.kind}
      row={row}
      problem={bad(row)}
      q={list}
      target={`tool:${row.id}`}
      about={(s?.summary || row.detail) && <LabelValue label="About">{s?.summary || row.detail}</LabelValue>}
      connects={
        <>
          <Facts>
            {row.way && <LabelValue label="Through">{row.way}</LabelValue>}
            <LabelValue label="State">{row.status.text}</LabelValue>
          </Facts>
          {keys.map((k) => (
            <Row key={k.id} className="row arow" title={k.name} href={toolHref(`key:${k.id}`)} target={`tool:key:${k.id}`}>
              <span className="row-title">
                {k.name} {k.env && <Code>{k.env}</Code>}
              </span>
              {k.stored ? <Held /> : <Pill>No value</Pill>}
              {k.last_used_at ? <Ago at={k.last_used_at} className="row-age" /> : <span className="row-age" />}
            </Row>
          ))}
          {missing.map((env) => {
            const conns = (s?.connections ?? []).filter((c) => c.env === env);
            return (
              <div key={env} className="row arow" title={env}>
                <span className="row-title">
                  <Code>{env}</Code>
                </span>
                <span className="arow-w">{conns.some((c) => c.problem) ? 'Missing' : conns.some((c) => c.status === 'ready') ? 'On computer' : 'Not set'}</span>
                {vault?.can_manage && <IconButton icon="plus" label={`Add ${env}`} size="sm" onClick={() => setStoring(env)} />}
              </div>
            );
          })}
        </>
      }
      who={[
        ...[...agents].map(([bot, a]) => (
          <WhoRow key={bot} actor={`bot:${bot}`} name={dir?.byId[`bot:${bot}`]?.name ?? bot} right={a.problem && <Pill>{a.problem}</Pill>} level={WRITE[a.level] ?? a.level} />
        )),
        ...[...people].map((p) => <WhoRow key={p} actor={p} name={subjectName(p, vault, dir)} level={WRITE[s?.writes ?? 'never']} />),
      ]}
      whoCount={agents.size + people.size}
      can={body && <Markdown text={body} />}
    >
      {storing && <RotateDialog env={{ env: storing, name: s?.title || row.name, service }} onClose={() => setStoring(null)} />}
    </ToolPage>
  );
}

/* ---------------------------------------------------------------- Email, and a mailbox */

/** Email itself, before or beside any one mailbox: the Google connection, what agents may do and send. */
export function EmailView() {
  const google = useGoogle();
  const sending = useSending();
  return (
    <ToolPage
      name="Email"
      kind="mailbox"
      target="tool:mailbox"
      connects={google.facts ?? <Facts>{null}</Facts>}
      connectsActions={google.actions}
      can={
        <>
          <MayFacts />
          {sending.facts}
        </>
      }
      canActions={sending.actions}
    />
  );
}

const SEND_STATE: Record<string, string> = {
  sent: 'Sent',
  waiting_approval: 'Waiting for OK',
  pending: 'Waiting',
  dispatching: 'Sending',
  unknown: 'Unknown',
  blocked: 'Refused',
  canceled: 'Canceled',
};

export function MailboxView({ id, row }: { id: string; row: ToolIndexRow }) {
  const q = useQuery(mailboxQuery(id));
  const d = q.data;
  const m = d?.mailbox;
  const [stopping, setStopping] = useState(false);
  const agents = (d?.accesses ?? []).filter((a) => a.grantee_type === 'bot').map((a) => ({ id: a.grantee_id, name: a.name }));
  const sending = useSending(agents);
  const reads = m && m.level !== 'none' && m.state === 'connected';
  const mail = useQuery({ ...mailboxMailQuery(m?.address ?? ''), enabled: !!reads });
  const sends = useInfiniteQuery({ ...mailboxSendsQuery(m?.address ?? ''), enabled: !!m });
  const sent = sends.data?.pages.flatMap((p) => p.sends) ?? [];
  const sharable = !!d && !!m && d.can_manage && m.status === 'active' && m.kind === 'personal' && !m.person_id;
  return (
    <ToolPage
      name={m?.address ?? row.name}
      kind="mailbox"
      problem={bad(row)}
      q={q}
      target={`tool:mailbox:${id}`}
      more={
        <PageMore>
          <MenuItem label="Email settings" onSelect={() => void press('tools.open', { tool: 'mailbox' })} />
          {sharable && <MenuItem label="Make shared inbox" onSelect={() => void press('tools.shareMailbox', { mailbox: id }, 'Not shared')} />}
          {d?.can_stop && (
            <>
              <MenuRule />
              <MenuItem label="Stop using" onSelect={() => setStopping(true)} />
            </>
          )}
        </PageMore>
      }
      about={
        m && (
          <>
            <LabelValue label="Address">
              <Code>{m.address}</Code>
            </LabelValue>
            <LabelValue label="Mailbox">{m.kind === 'shared' ? 'Shared' : `${m.person_name}'s`}</LabelValue>
          </>
        )
      }
      connects={
        m && (
          <Facts>
            <LabelValue label="Through">{m.provider}</LabelValue>
            <LabelValue label="State">{row.status.text}</LabelValue>
            {m.synced_at && <LabelValue label="Synced">{when(m.synced_at)}</LabelValue>}
            {m.sync_error && <LabelValue label="Problem">{m.sync_error}</LabelValue>}
          </Facts>
        )
      }
      connectsActions={m?.status === 'active' && <IconButton icon="refresh" label="Check now" size="sm" onClick={() => void check('tools.checkMailbox', { mailbox: id })} />}
      who={d?.accesses.map((a) => (
        <WhoRow
          key={`${a.grantee_type}:${a.grantee_id}`}
          actor={a.grantee_type === 'admins' ? null : `${a.grantee_type === 'person' ? 'human' : 'bot'}:${a.grantee_id}`}
          name={a.name}
          level={a.level}
          onLevel={d.can_manage && !a.implicit && a.id ? (level) => void press('tools.setMailboxAccess', { mailbox: id, access: a.id, level }, 'Not changed') : undefined}
        />
      ))}
      whoCount={d?.accesses.length || undefined}
      whoActions={
        d?.can_manage && (
          <AddSomeone
            choices={d.candidates.map((c) => ({ id: `${c.type}:${c.id}`, name: c.name }))}
            onPick={(pick) => {
              const [type, ...rest] = pick.split(':');
              void press('tools.addMailboxAccess', { mailbox: id, type, who: rest.join(':'), level: 'read' }, 'Not added');
            }}
          />
        )
      }
      can={
        <>
          <MayFacts may={d?.may} />
          {sending.facts}
        </>
      }
      canActions={sending.actions}
      recent={
        (!!mail.data?.length || sent.length > 0) && (
          <>
            {mail.data?.map((x, n) => (
              <Row
                key={x.id ?? x.thread_id ?? n}
                className="row urow"
                title={x.subject || '(No subject)'}
                href={`/threads/mail?box=${encodeURIComponent(m?.address ?? '')}${x.thread_id ? `&open=${encodeURIComponent(x.thread_id)}` : ''}`}
              >
                <span className="urow-lead">
                  <Icon name="download" />
                </span>
                <span className="row-title">{x.subject || '(No subject)'}</span>
                <span className="row-ctx">{x.from_header || x.from_addr || ''}</span>
                {x.epoch ? <Ago at={new Date(x.epoch * 1000)} className="row-age" /> : <span className="row-age" />}
              </Row>
            ))}
            {sent.map((x) => (
              <UseRow key={x.id} title={x.subject || '(No subject)'} ctx={x.recipients.join(', ')} actor={x.bot ? `bot:${x.bot}` : x.actor || null} at={x.created_at} lead={<Icon name="send" />}>
                {x.state !== 'sent' && <Pill>{SEND_STATE[x.state] ?? x.state}</Pill>}
              </UseRow>
            ))}
            {sends.hasNextPage && <More onClick={() => void sends.fetchNextPage()}>More</More>}
          </>
        )
      }
    >
      {m && stopping && (
        <Confirm
          title={`Stop using ${m.address}?`}
          description="Tico stops reading and sending from it."
          label="Stop using"
          onGo={() => press('tools.stopMailbox', { mailbox: id }, 'Not stopped')}
          onClose={() => setStopping(false)}
        />
      )}
    </ToolPage>
  );
}

/* ---------------------------------------------------------------- a Tico address or number, and numbers asked for */

/** Numbers asked for: each waits on a price to accept, or is canceled. */
export function NumbersView() {
  return <ToolPage name="Phone numbers" kind="number" target="tool:number" connects={<NumberRequests always />} />;
}

export function InboxView({ inbox, row, number }: { inbox: string; row: ToolIndexRow | undefined; number: boolean }) {
  const boxes = useQuery(inboxesQuery);
  const messages = useQuery(inboxMessagesQuery(inbox));
  const teams = useQuery(teamsQuery).data ?? [];
  const admin = useAdmin();
  const box = boxes.data?.find((b) => b.inbox_id === inbox);
  const [dialog, setDialog] = useState<'edit' | 'release' | 'join' | null>(null);
  const [reading, setReading] = useState<InMessage | null>(null);
  // As the old page: anyone renames an address; a number, joining the shared Inbox and stopping are an admin's.
  const editable = !number || admin;
  const team = box?.audience === 'team' ? teams.find((t) => t.id === box.group_id) : undefined;
  const readers = box?.audience === 'team' ? (team?.name ?? 'One team') : 'Everyone';
  const pick = (audience: 'company' | 'team', group?: string) => void press('tools.setInboxReaders', { inbox, audience, group }, 'Not saved');
  return (
    <ToolPage
      name={box?.name ?? row?.name ?? (number ? 'Phone number' : 'Tico address')}
      kind={number ? 'number' : 'mailbox'}
      kindWord={number ? KIND.number : KIND.tico}
      problem={row && bad(row)}
      q={boxes}
      target={`tool:${row?.id ?? inbox}`}
      more={
        box &&
        editable && (
          <PageMore>
            <MenuItem label="Edit" onSelect={() => setDialog('edit')} />
            {!number && admin && <MenuItem label="Join shared Inbox" onSelect={() => setDialog('join')} />}
            {admin && (
              <>
                <MenuRule />
                <MenuItem label="Stop using" onSelect={() => setDialog('release')} />
              </>
            )}
          </PageMore>
        )
      }
      about={
        <>
          <LabelValue label={number ? 'Number' : 'Address'}>
            <Code>{box?.address ?? row?.detail ?? ''}</Code>
          </LabelValue>
          {box && (
            <LabelValue label="Reads it">
              {admin ? (
                <Menu
                  trigger={
                    <button type="button" className="lvl" aria-label={`Reads it: ${readers}`}>
                      {readers}
                    </button>
                  }
                >
                  <MenuItem label="Everyone" on={box.audience !== 'team'} onSelect={() => pick('company')} />
                  {teams.length > 0 && <MenuRule />}
                  {teams.map((t) => (
                    <MenuItem key={t.id} label={t.name} on={!!team && t.id === team.id} onSelect={() => pick('team', t.id)} />
                  ))}
                </Menu>
              ) : (
                readers
              )}
            </LabelValue>
          )}
          {box?.used_for && <LabelValue label="Used for">{box.used_for}</LabelValue>}
        </>
      }
      connects={
        <>
          <Facts>
            <LabelValue label="Through">Tico</LabelValue>
            <LabelValue label="State">{row?.status.text ?? box?.state ?? ''}</LabelValue>
          </Facts>
          {number && <NumberRequests />}
        </>
      }
      connectsActions={<IconButton icon="refresh" label="Check connection" size="sm" onClick={() => void check('tools.checkInbox', {})} />}
      recent={!!messages.data?.length && <Messages list={messages.data as unknown as InMessage[]} onOpen={setReading} />}
      recentActions={!!messages.data?.length && <IconButton icon="chevron" label="All messages" size="sm" onClick={() => void press('tools.open', { tool: 'messages' })} />}
    >
      {reading && <MessageDialog message={reading.message_id} title={reading.subject || reading.preview || 'Message'} hasCode={!!reading.has_code} onClose={() => setReading(null)} />}
      {dialog === 'join' && <SharedInboxDialog onClose={() => setDialog(null)} />}
      {box && dialog === 'edit' && <EditInboxDialog box={box} onClose={() => setDialog(null)} />}
      {box && dialog === 'release' && <ReleaseDialog box={box} number={number} onClose={() => setDialog(null)} />}
    </ToolPage>
  );
}

/** Messages that came in, each opening in the reader. */
function Messages({ list, onOpen }: { list: InMessage[]; onOpen: (m: InMessage) => void }) {
  return list.map((m) => (
    <UseRow
      key={m.message_id}
      title={m.subject || m.preview || (m.retention_removed ? 'Removed after its time' : 'Message')}
      ctx={m.from ?? ''}
      at={new Date(m.received_at * 1000)}
      lead={<Icon name="download" />}
      onOpen={() => onOpen(m)}
    >
      {m.has_code && <Pill>Code</Pill>}
      {m.spam && <Pill>Spam</Pill>}
    </UseRow>
  ));
}

/* ---------------------------------------------------------------- Messages: every Tico address and number */

const CHANNEL: Record<'email' | 'sms', string> = { email: 'Email', sms: 'Texts' };

/** What came in to every address and number, or the ones picked, narrowed by channel, words, a code or spam. */
export function MessagesView() {
  const boxes = useQuery(inboxesQuery);
  const [pick, setPick] = useState<Omit<MessageFilter, 'q'>>({});
  const [words, setWords] = useState('');
  const [q, setQ] = useState('');
  // Typed words are sent after a short pause, so each key is not a read.
  useEffect(() => {
    const t = setTimeout(() => setQ(words.trim()), 300);
    return () => clearTimeout(t);
  }, [words]);
  const list = useInfiniteQuery(allMessagesQuery({ ...pick, q: q || undefined }));
  const [reading, setReading] = useState<InMessage | null>(null);
  const inboxes = boxes.data ?? [];
  const picked = pick.inboxes ?? [];
  const set = (change: Partial<typeof pick>) => setPick({ ...pick, ...change });
  const toggle = (id: string) => set({ inboxes: picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id] });
  const chips: FilterChip[] = [
    ...inboxes.filter((b) => picked.includes(b.inbox_id)).map((b) => ({ label: b.name || b.address, onClear: () => toggle(b.inbox_id) })),
    ...(pick.kind ? [{ label: CHANNEL[pick.kind], onClear: () => set({ kind: undefined }) }] : []),
    ...(pick.has_code ? [{ label: 'Has a code', onClear: () => set({ has_code: undefined }) }] : []),
    ...(pick.spam ? [{ label: 'Includes spam', onClear: () => set({ spam: undefined }) }] : []),
  ];
  const rows = list.data?.pages.flatMap((p) => p.messages) ?? [];
  const filter = (
    <Menu trigger={<IconButton icon="filter" label="Filter messages" on={chips.length > 0} data-phone />}>
      {inboxes.length > 0 && (
        <MenuSub label="Inbox">
          <MenuItem label="Every inbox" on={!picked.length} onSelect={() => set({ inboxes: undefined })} />
          <MenuRule />
          {inboxes.map((b) => (
            <MenuItem key={b.inbox_id} label={b.name || b.address} meta={b.state === 'released' ? 'Not in use' : undefined} on={picked.includes(b.inbox_id)} onSelect={() => toggle(b.inbox_id)} />
          ))}
        </MenuSub>
      )}
      <MenuSub label="Channel">
        <MenuItem label="Every channel" on={!pick.kind} onSelect={() => set({ kind: undefined })} />
        <MenuItem label="Email" on={pick.kind === 'email'} onSelect={() => set({ kind: 'email' })} />
        <MenuItem label="Texts" on={pick.kind === 'sms'} onSelect={() => set({ kind: 'sms' })} />
      </MenuSub>
      <MenuRule />
      <MenuItem label="Has a code" on={!!pick.has_code} onSelect={() => set({ has_code: !pick.has_code || undefined })} />
      <MenuItem label="Include spam" on={!!pick.spam} onSelect={() => set({ spam: !pick.spam || undefined })} />
    </Menu>
  );
  return (
    // The inboxes feed the filter: when they fail, the page shows its fault rather than "No messages yet".
    <ToolPage name="Messages" kind="mailbox" fill={filter} q={boxes.isError ? boxes : list} target="tool:messages" bare>
      <div className="msg-find">
        <Search label="Find messages" value={words} onValueChange={setWords} placeholder="Words" />
      </div>
      <FilterRow filters={chips} onClearAll={() => setPick({})} />
      {!rows.length && <Empty title={chips.length || q ? 'Nothing matches' : 'No messages yet'} />}
      <Messages list={rows} onOpen={setReading} />
      {list.hasNextPage && !list.isFetchingNextPage && <More onClick={() => void list.fetchNextPage()}>Older messages</More>}
      {reading && <MessageDialog message={reading.message_id} title={reading.subject || reading.preview || 'Message'} hasCode={!!reading.has_code} onClose={() => setReading(null)} />}
    </ToolPage>
  );
}

/* ---------------------------------------------------------------- an outgoing webhook */

const SENT: Record<string, string> = { ready: 'Waiting', sending: 'Sending', delivered: 'Delivered', failed: 'Failed', canceled: 'Canceled', withheld: 'Held back' };

/** Where it sends and how its sends went; its ⋯ holds Edit, Send test, Pause, New secret and Delete. Tools is its only
    home: Add tool › Webhook adds one. */
export function WebhookView({ id, row }: { id: string; row: ToolIndexRow | undefined }) {
  const hook = useQuery(webhookQuery(id));
  const sends = useQuery(webhookSendsQuery(id));
  const [dialog, setDialog] = useState<'edit' | 'secret' | 'delete' | null>(null);
  const w = hook.data?.webhook;
  const paused = !!w?.paused_reason;
  const name = w?.name ?? row?.name ?? 'Outgoing webhook';
  return (
    <ToolPage
      name={name}
      kind="webhook"
      problem={(row && bad(row)) || w?.disabled_reason || null}
      q={hook}
      target={`tool:webhook:${id}`}
      more={
        w?.can_change && (
          <PageMore>
            <MenuItem label="Edit" onSelect={() => setDialog('edit')} />
            <MenuItem label="Send test" onSelect={() => void press('tools.testWebhook', { webhook: id }, 'The test did not send')} />
            <MenuItem label={paused ? 'Send again' : 'Pause'} onSelect={() => void press('tools.pauseWebhook', { webhook: id, on: paused }, paused ? 'It did not restart' : 'It did not pause')} />
            <MenuItem label="New secret" onSelect={() => setDialog('secret')} />
            <MenuRule />
            <MenuItem label="Delete" onSelect={() => setDialog('delete')} />
          </PageMore>
        )
      }
      about={w && <LabelValue label="Events">{w.events.length ? w.events.join(', ') : 'All'}</LabelValue>}
      connects={
        w && (
          <Facts>
            <LabelValue label="Sends to">
              <Code>{w.url || w.host}</Code>
            </LabelValue>
            <LabelValue label="State">{paused ? w.paused_reason : w.enabled ? 'On' : 'Off'}</LabelValue>
            <LabelValue label="Last 7 days">
              {`${w.last_7_days.delivered} delivered, ${w.last_7_days.failed} failed, ${w.last_7_days.pending} waiting`}
            </LabelValue>
          </Facts>
        )
      }
      recent={sends.data?.map((x) => (
        <UseRow key={x.id} title={x.subject || x.type} ctx={x.last_status ? `${SENT[x.state] ?? x.state} (${x.last_status})` : (SENT[x.state] ?? x.state)} at={x.delivered_at ?? x.created_at}>
          {w?.can_change && (x.state === 'failed' || x.state === 'canceled') && (
            <IconButton icon="refresh" label="Retry" size="sm" onClick={() => void press('tools.retrySend', { webhook: id, send: x.id }, 'Not sent again')} />
          )}
        </UseRow>
      ))}
    >
      {w && dialog === 'edit' && <WebhookDialog hook={w} onClose={() => setDialog(null)} />}
      {dialog === 'secret' && <NewSecretDialog webhook={id} onClose={() => setDialog(null)} />}
      {dialog === 'delete' && (
        <Confirm title={`Delete ${name}?`} description="Nothing more is sent to it." label="Delete" onGo={() => press('tools.deleteWebhook', { webhook: id }, 'Not deleted')} onClose={() => setDialog(null)} />
      )}
    </ToolPage>
  );
}

/* ---------------------------------------------------------------- Tools' Who has access */

/** An index row's teammate as a subject: `human:<id>`, `bot:<id>` or `group:<id>`. */
export const subjectOf = (e: { kind: string; id: string }) => (/^(human|bot|group):/.test(e.id) ? e.id : `${e.kind === 'person' ? 'human' : e.kind}:${e.id}`);

/** Everyone a team holds, as subjects: the team, the teams under it, their people and their agents. */
export function teamSubjects(groups: Group[], gid: string): Set<string> {
  const out = new Set<string>();
  const walk = (id: string, depth: number) => {
    const g = groups.find((x) => x.id === id);
    if (!g || depth > 20 || out.has(`group:${g.id}`)) return;
    out.add(`group:${g.id}`);
    for (const p of g.people ?? []) out.add(p.includes(':') ? p : `human:${p}`);
    for (const b of [...(g.bots ?? []), ...(g.bot_slugs ?? [])]) out.add(b.includes(':') ? b : `bot:${b}`);
    for (const c of groups) if (c.parent === id) walk(c.id, depth + 1);
  };
  walk(gid, 0);
  return out;
}

/** Whether a tool given to these subjects is one the pick has: a team (anyone in it), or a teammate (also through a
    team they are in). `same` makes two spellings of one teammate equal (an agent's slug and its id). */
export function hasWho(subjects: string[], who: string, groups: Group[], same: (s: string) => string): boolean {
  if (who.startsWith('group:')) {
    const team = new Set([...teamSubjects(groups, who.slice(6))].map(same));
    return subjects.some((s) => team.has(same(s)));
  }
  const me = same(who);
  return subjects.some((s) => same(s) === me || (s.startsWith('group:') && [...teamSubjects(groups, s.slice(6))].some((x) => same(x) === me)));
}

/** The Filter menu's Who has access: teams first, then teammates. */
export function WhoMenu({ who, teams, people, onPick }: { who?: string; teams: Group[]; people: { subject: string; name: string }[]; onPick: (who?: string) => void }) {
  if (!teams.length && !people.length) return null;
  return (
    <MenuSub label="Who has access">
      <MenuItem label="Anyone" on={!who} onSelect={() => onPick(undefined)} />
      {teams.map((g) => (
        <MenuItem key={g.id} label={g.name} on={who === `group:${g.id}`} onSelect={() => onPick(`group:${g.id}`)} />
      ))}
      {people.map((p) => (
        <MenuItem key={p.subject} label={p.name} on={who === p.subject} onSelect={() => onPick(p.subject)} />
      ))}
    </MenuSub>
  );
}
