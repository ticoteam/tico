import { useQuery } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { refusal, run } from '@/actions';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { Section } from '@/pagekinds';
import { Foot, Line, SwitchLine, Verb, Word, act } from './parts';
import {
  repositoriesQuery,
  storageMoveQuery,
  storageQuery,
  suggestionsQuery,
  usageCountQuery,
  type ProductRepoPreview,
  type Repo,
  type StorageBucket,
  type StorageView,
} from './queries';

/* General's cards from the old app (ui/app/settings.ts, settings-storage.ts, settings-repositories.ts): the team icon,
   Privacy, Storage and Repositories. Each reads its own call and stays away when the server says it is not yours. */

// ---------------------------------------------------------------- the team icon (owner changes it)

export function TeamIcon({ owner }: { owner: boolean }) {
  const [at, setAt] = useState(0);
  const [none, setNone] = useState(false);
  const pick = useRef<HTMLInputElement>(null);
  const send = async (file: File | undefined) => {
    if (!file) return;
    const r = await run('settings.teamIcon', { file }, { via: 'click' });
    if (pick.current) pick.current.value = '';
    if (!r.ok) return toast(refusal(r, "Couldn't change the icon"));
    setNone(false);
    setAt(Date.now());
  };
  return (
    <Line label="Icon" target="setting:icon">
      {none ? (
        <span className="fact-dim">None</span>
      ) : (
        <img src={`/api/v2/team/icon${at ? `?v=${at}` : ''}`} alt="Team icon" width={22} height={22} onError={() => setNone(true)} />
      )}
      {owner && (
        <>
          <input
            ref={pick}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            aria-label="Choose an icon"
            hidden
            onChange={(e) => void send(e.currentTarget.files?.[0])}
          />
          <Verb onClick={() => pick.current?.click()}>Change</Verb>
        </>
      )}
    </Line>
  );
}

// ---------------------------------------------------------------- Privacy (owner)

export function Privacy() {
  const count = useQuery({ ...usageCountQuery, retry: false });
  const ideas = useQuery({ ...suggestionsQuery, retry: false });
  const [asking, setAsking] = useState<'share' | 'reset' | null>(null);
  const c = count.data;
  const g = ideas.data;
  if (typeof c?.enabled !== 'boolean') return null;
  // Turned off by the server itself (demo, an environment setting): the switch cannot turn it back on.
  const forced = !!c.off_by && c.off_by !== 'setting';
  return (
    <Section name="Privacy" target="setting:privacy">
      <Facts label="Privacy">
        <SwitchLine
          label="Count active installs"
          target="setting:usage-count"
          checked={c.enabled}
          disabled={forced}
          onChange={(on) => void act('settings.usageCount', { on })}
        />
        <Line label="Install ID">
          {c.install_id || <span className="fact-dim">None yet</span>}
          {c.install_id && <Verb onClick={() => setAsking('reset')}>Reset</Verb>}
        </Line>
        {typeof g?.available === 'boolean' && (
          <>
            <SwitchLine
              label="Share counts for suggestions"
              target="setting:share_stats_for_suggestions"
              checked={g.share_stats_for_suggestions}
              disabled={!g.available}
              onChange={(on) => (on ? setAsking('share') : void act('settings.suggestionPrivacy', { field: 'share_stats_for_suggestions', on }))}
            />
            <SwitchLine
              label="Report suggestion outcomes"
              target="setting:report_suggestion_outcomes"
              checked={g.report_suggestion_outcomes}
              disabled={!g.available}
              onChange={(on) => void act('settings.suggestionPrivacy', { field: 'report_suggestion_outcomes', on })}
            />
          </>
        )}
      </Facts>
      <Dialog
        open={asking != null}
        onOpenChange={(o) => !o && setAsking(null)}
        alert
        title={asking === 'share' ? 'Share counts for suggestions?' : 'Reset the install ID?'}
        description={
          asking === 'share'
            ? 'Sends counts only, never message text, to Tico.'
            : 'Earlier counts can no longer be linked to this install.'
        }
        actions={
          <>
            <Button variant="quiet" onClick={() => setAsking(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                if (asking === 'share') void act('settings.suggestionPrivacy', { field: 'share_stats_for_suggestions', on: true });
                else void act('settings.resetInstallId', {});
                setAsking(null);
              }}
            >
              {asking === 'share' ? 'Share' : 'Reset'}
            </Button>
          </>
        }
      />
    </Section>
  );
}

// ---------------------------------------------------------------- Storage (owner)

const HOSTING: Record<string, string> = { on: 'On', credits_out: 'Credits ran out', stopped: 'Stopped' };
const gb = (n: number) => `${(n / 1e9).toFixed(n < 1e7 ? 3 : 2)} GB`;
const where = (b: StorageBucket) => `s3://${b.location}, ${b.region}`;

function BucketDialog({ now, onClose }: { now: StorageBucket | null; onClose: () => void }) {
  const [bucket, setBucket] = useState(now?.bucket ?? '');
  const [region, setRegion] = useState(now?.region ?? '');
  const [prefix, setPrefix] = useState(now?.prefix ?? '');
  const [keyId, setKeyId] = useState('');
  // The secret key lives here, in this dialog's state, until it is sent once.
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const ready = bucket.trim().length >= 3 && region.trim() && keyId.trim() && (secret || now);
  const save = async () => {
    setBusy(true);
    const r = await run(
      'settings.saveBucket',
      { bucket: bucket.trim(), region: region.trim(), prefix: prefix.trim(), keyId: keyId.trim(), ...(secret ? { secret } : {}) },
      { via: 'click' },
    );
    setBusy(false);
    setSecret('');
    if (!r.ok) return toast(refusal(r, "Couldn't use the bucket"));
    toast({ text: 'New files go to the bucket', kind: 'done' });
    onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Use a bucket"
      description="Keys need s3:PutObject, s3:GetObject, s3:AbortMultipartUpload and s3:DeleteObject."
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => void save()}>
            Use bucket
          </Button>
        </>
      }
    >
      <div className="set-add">
        <Field label="Bucket" value={bucket} onValueChange={setBucket} autoComplete="off" autoFocus />
        <Field label="Region" value={region} onValueChange={setRegion} autoComplete="off" />
        <Field label="Folder" value={prefix} onValueChange={setPrefix} autoComplete="off" />
        <Field label="Access key ID" value={keyId} onValueChange={setKeyId} autoComplete="off" />
        <Field label="Secret access key" type="password" value={secret} onValueChange={setSecret} autoComplete="new-password" />
      </div>
    </Dialog>
  );
}

function MoveLine({ hostingOn }: { hostingOn: boolean }) {
  const m = useQuery({ ...storageMoveQuery, retry: false });
  const [asking, setAsking] = useState(false);
  const v = m.data;
  if (!v?.estimate) return null;
  const running = v.move?.state === 'running';
  const files = v.estimate.files ?? 0;
  const to = hostingOn ? 'Tico file hosting' : "the company's own storage";
  return (
    <>
      {running && v.move && (
        <LabelValue label="Moving">{`${v.move.moved_files} of ${v.move.estimate_files} files`}</LabelValue>
      )}
      {!running && files > 0 && (
        <Line label="Not moved yet">
          {`${files} files, ${gb(v.estimate.bytes)}`}
          <Verb onClick={() => setAsking(true)}>{hostingOn ? 'Move files' : 'Move back'}</Verb>
        </Line>
      )}
      <Dialog
        open={asking}
        onOpenChange={setAsking}
        alert
        title={`Copy ${files} files to ${to}?`}
        description={`Every version is copied and checked; nothing is deleted where it is now.${hostingOn ? ' Tico file hosting bills what it stores in Gateway credits.' : ''}`}
        actions={
          <>
            <Button variant="quiet" onClick={() => setAsking(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setAsking(false);
                void act('settings.moveFiles', { bytes: v.estimate.bytes });
              }}
            >
              Move files
            </Button>
          </>
        }
      />
    </>
  );
}

export function Storage() {
  const q = useQuery({ ...storageQuery, retry: false });
  const [editing, setEditing] = useState(false);
  const [asking, setAsking] = useState(false);
  const v: StorageView | undefined = q.data;
  if (!v?.copy || !v.server) return null;
  const a = v.active;
  const hosting = v.tico && v.tico.state !== 'off' ? v.tico : null;
  const own = a ? where(a) : v.server.mode === 's3' ? `The server's bucket, s3://${v.server.location ?? ''}` : "This server's disk";
  return (
    <Section name="Storage" target="setting:storage">
      <Facts label="Storage">
        <LabelValue label="New files go to">{hosting?.state === 'on' ? (hosting.label ?? 'Tico file hosting') : own}</LabelValue>
        {hosting?.state === 'on' && <LabelValue label="Own storage">{own}</LabelValue>}
        {a && <LabelValue label="Key">{a.access_key_id}</LabelValue>}
        {hosting && (
          <Line label={hosting.label ?? 'Tico file hosting'}>
            {hosting.error ? <Word>{hosting.error}</Word> : (HOSTING[hosting.state] ?? hosting.state)}
            <Verb onClick={() => void act('settings.checkHosting', {})}>Check now</Verb>
          </Line>
        )}
        {hosting && <MoveLine hostingOn={hosting.state === 'on'} />}
        {(v.copy.running || !!v.copy.failed) && (
          <LabelValue label="Copying">{`${v.copy.done} of ${v.copy.total}${v.copy.failed ? `, ${v.copy.failed} failed` : ''}`}</LabelValue>
        )}
        {v.earlier.length > 0 && <LabelValue label="Still read from">{v.earlier.map(where).join('; ')}</LabelValue>}
      </Facts>
      <Foot>
        <Verb onClick={() => setEditing(true)}>{a ? 'Change bucket' : 'Use a bucket'}</Verb>
        {a && <Verb onClick={() => setAsking(true)}>Use the server</Verb>}
        <a className="verb" href="/api/v2/system/storage/export" download>
          Download all
        </a>
      </Foot>
      {editing && <BucketDialog now={a} onClose={() => setEditing(false)} />}
      <Dialog
        open={asking}
        onOpenChange={setAsking}
        alert
        title="Store new files on the server again?"
        description="Files already in the bucket keep opening from it."
        actions={
          <>
            <Button variant="quiet" onClick={() => setAsking(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setAsking(false);
                void act('settings.dropBucket', {});
              }}
            >
              Use server
            </Button>
          </>
        }
      />
    </Section>
  );
}

// ---------------------------------------------------------------- Repositories (owners and admins change them)

const NEW_AGENTS = [
  { value: 'own', label: 'Their own repo' },
  { value: 'all', label: 'All repos in use' },
] as const;
const MANY = 100;

function RepoOpen({ r, admin }: { r: Repo; admin: boolean }) {
  const [command, setCommand] = useState(r.setup_command ?? '');
  return (
    <>
      <Facts label={r.full_name}>
        {r.default_branch && <LabelValue label="Branch">{r.default_branch}</LabelValue>}
        {!r.reachable && <Word>Not reachable</Word>}
        <Line label="Setup command">
          {admin ? (
            <>
              <Field label="Setup command" labelHidden value={command} onValueChange={setCommand} autoComplete="off" />
              {command.trim() !== (r.setup_command ?? '') && (
                <Verb onClick={() => void act('settings.repoSetup', { repo: r.full_name, command })}>Save</Verb>
              )}
            </>
          ) : (
            (r.setup_command ?? <span className="fact-dim">None</span>)
          )}
        </Line>
        {r.setup_source && r.setup_source !== 'settings' && <LabelValue label="From">{r.setup_source}</LabelValue>}
      </Facts>
      {admin && (
        <Foot open>
          <Verb onClick={() => void act('settings.useRepo', { repo: r.full_name, on: false })}>Remove</Verb>
        </Foot>
      )}
    </>
  );
}

function NewRepo({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState('');
  const [seen, setSeen] = useState<ProductRepoPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const look = async () => {
    setBusy(true);
    const r = await run('settings.previewRepo', { name: name.trim() }, { via: 'click' });
    setBusy(false);
    if (!r.ok) return toast(refusal(r, "Couldn't check the name"));
    setSeen(r.out as ProductRepoPreview);
  };
  const make = async () => {
    setBusy(true);
    const r = await run('settings.createRepo', { name: name.trim() }, { via: 'click' });
    setBusy(false);
    if (!r.ok) return toast(refusal(r, "Couldn't make it"));
    toast({ text: r.did ?? 'Made', kind: 'done' });
    onClose();
  };
  const ok = seen?.name === name.trim() && seen.capability === 'available';
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="New repository"
      description={seen?.name === name.trim() ? `${seen.repository}, private, empty. ${seen.capability_detail}` : 'Private and empty.'}
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          {ok ? (
            <Button variant="primary" busy={busy} onClick={() => void make()}>
              Create
            </Button>
          ) : (
            <Button variant="primary" busy={busy} disabled={!name.trim()} onClick={() => void look()}>
              Check name
            </Button>
          )}
        </>
      }
    >
      <Field
        label="Name"
        value={name}
        onValueChange={(x) => {
          setName(x);
          setSeen(null);
        }}
        autoComplete="off"
        autoFocus
      />
    </Dialog>
  );
}

export function Repositories({ admin, owner }: { admin: boolean; owner: boolean }) {
  const q = useQuery({ ...repositoriesQuery, retry: false });
  const [open, setOpen] = useState<string | null>(null);
  const [find, setFind] = useState('');
  const [making, setMaking] = useState(false);
  const v = q.data;
  if (!Array.isArray(v?.repositories)) return null;
  if (!v.github_connected)
    return (
      <Section name="Repositories" target="setting:repositories">
        <Empty title="GitHub is not connected" />
      </Section>
    );
  const byName = (a: Repo, b: Repo) => a.full_name.localeCompare(b.full_name);
  const on = v.repositories.filter((r) => r.enabled).sort(byName);
  const needle = find.trim().toLowerCase();
  const off = v.repositories
    .filter((r) => !r.enabled && !r.archived && !r.bot_repo && (!needle || r.full_name.toLowerCase().includes(needle)))
    .sort(byName);
  return (
    <>
      <Section
        name="Repositories"
        count={on.length}
        target="setting:repositories"
        actions={
          admin ? (
            <>
              <Verb onClick={() => void act('settings.refreshRepos', {})}>Refresh</Verb>
              {owner && <Verb onClick={() => setMaking(true)}>New repository</Verb>}
            </>
          ) : undefined
        }
      >
        <Facts label="Repositories">
          <Line label="New agents get" target="setting:new-agent-repos">
            {admin ? (
              <Select
                label="New agents get"
                labelHidden
                look="quiet"
                value={v.new_bot_default}
                options={NEW_AGENTS}
                onValueChange={(value) => void act('settings.newAgentRepos', { value })}
              />
            ) : (
              NEW_AGENTS.find((x) => x.value === v.new_bot_default)?.label
            )}
          </Line>
        </Facts>
        {on.length === 0 ? (
          <Empty title="None in use" />
        ) : (
          on.map((r) => {
            const row = (
              <button
                key={r.full_name}
                type="button"
                className={`row g-door${r.reachable ? '' : ' is-off'}`}
                data-target={`repo:${r.full_name}`}
                aria-expanded={open === r.full_name}
                title={r.full_name}
                onClick={() => setOpen(open === r.full_name ? null : r.full_name)}
              >
                <span className="row-title">{r.full_name}</span>
                <span className="row-cell">{r.default_branch ?? ''}</span>
              </button>
            );
            return open === r.full_name ? (
              <div key={r.full_name} className="open">
                {row}
                <RepoOpen r={r} admin={admin} />
              </div>
            ) : (
              row
            );
          })
        )}
      </Section>
      <Section name="Not in use" count={off.length} defaultOpen={false} target="setting:repos-off">
        <div className="set-add">
          <Field label="Search repositories" labelHidden placeholder="Search" lead="search" value={find} onValueChange={setFind} />
        </div>
        {off.slice(0, MANY).map((r) => (
          <div key={r.full_name} className="row g-door" data-target={`repo:${r.full_name}`} title={r.full_name}>
            <span className="row-title">{r.full_name}</span>
            <span className="hl-act">{admin && <Verb onClick={() => void act('settings.useRepo', { repo: r.full_name, on: true })}>Add</Verb>}</span>
          </div>
        ))}
        {off.length > MANY && <p className="fact-dim">{`${off.length - MANY} more; search to narrow`}</p>}
      </Section>
      {making && <NewRepo onClose={() => setMaking(false)} />}
    </>
  );
}
