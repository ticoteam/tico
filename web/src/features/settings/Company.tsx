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
import { Foot, Line, Verb, Word, act } from './parts';
import {
  repositoriesQuery,
  type ProductRepoPreview,
  type Repo,
} from './queries';

/* General's cards from the old app (ui/app/settings.ts, settings-repositories.ts): the team icon and Repositories. Each reads its own call and stays away when the server says it is not yours. */

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
        {/* one quiet line, not an empty page inside the section */}
        <Facts label="Repositories">
          <LabelValue label="GitHub">Not connected</LabelValue>
        </Facts>
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
      </Section>
      {making && <NewRepo onClose={() => setMaking(false)} />}
    </>
  );
}
