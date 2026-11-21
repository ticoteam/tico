import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useAction } from '@/actions';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { Dialog } from '@/kit/Dialog';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Segmented } from '@/kit/Segmented';
import { Section } from '@/pagekinds';
import { agentReposQuery, teamReposQuery, type ReposChange } from './queries';

/* An agent's repositories (backend/repositories.py, GET/PUT /api/v2/bots/{bot}/repositories): its own one only, every
   ticked one at one level, or chosen ones each at its level. Taking one away here is this agent's alone: the team's
   tick stays (Settings › General › Repositories). */

type Mode = ReposChange['mode'];
type Level = 'read' | 'write';
const MODES: Array<{ value: Mode; label: string }> = [
  { value: 'own', label: 'Its own' },
  { value: 'all', label: 'All ticked' },
  { value: 'chosen', label: 'Chosen' },
];
const LEVELS: Array<{ value: Level; label: string }> = [
  { value: 'read', label: 'Read' },
  { value: 'write', label: 'Write' },
];
const LEVEL: Record<Level, string> = { read: 'Read', write: 'Write' };
const MODE: Record<Mode, string> = { own: 'Its own repository', all: 'All ticked repositories', chosen: 'Chosen repositories' };

/** Read for anyone who sees the agent; the editor for whoever manages it. Nothing when the server says no. */
export function ReposSection({ slug, name, manage }: { slug: string; name: string; manage: boolean }) {
  const q = useQuery({ ...agentReposQuery(slug), retry: false });
  const [editing, setEditing] = useState(false);
  const v = q.data;
  // A read that is not this shape (an older server) shows nothing, as a refusal does.
  if (!Array.isArray(v?.effective)) return null;
  return (
    <Section
      name="Repositories"
      count={v.effective.length || undefined}
      actions={manage && <IconButton icon="edit" size="sm" quiet label="Edit repositories" onClick={() => setEditing(true)} />}
    >
      <Facts label="Repositories">
        <LabelValue label="Reaches">{v.mode === 'all' ? `${MODE.all}, ${LEVEL[v.all_access]}` : MODE[v.mode]}</LabelValue>
        {v.effective.map((r) => (
          <LabelValue key={r.full_name} label={r.full_name}>
            {LEVEL[r.access ?? 'write']}
          </LabelValue>
        ))}
      </Facts>
      {editing && (
        <ReposDialog
          slug={slug}
          name={name}
          now={{ mode: v.mode, all_access: v.all_access, chosen: v.chosen.map((r) => ({ full_name: r.full_name, access: r.access ?? 'write' })) }}
          onClose={() => setEditing(false)}
        />
      )}
    </Section>
  );
}

function ReposDialog({ slug, name, now, onClose }: { slug: string; name: string; now: Required<ReposChange>; onClose: () => void }) {
  const save = useAction('agents.setRepositories');
  const team = useQuery({ ...teamReposQuery, retry: false });
  const [mode, setMode] = useState<Mode>(now.mode);
  const [level, setLevel] = useState<Level>(now.all_access);
  const [chosen, setChosen] = useState(() => new Map(now.chosen.map((r) => [r.full_name, r.access])));
  // The team's ticked repositories, and any this agent holds that are no longer ticked, so saving never drops them.
  const names = [...new Set([...(team.data?.repositories ?? []).filter((r) => r.enabled).map((r) => r.full_name), ...now.chosen.map((r) => r.full_name)])].sort(
    (a, b) => a.localeCompare(b),
  );
  const pick = (full: string, access: Level | null) =>
    setChosen((cur) => {
      const next = new Map(cur);
      if (access) next.set(full, access);
      else next.delete(full);
      return next;
    });
  const change: ReposChange =
    mode === 'all' ? { mode, all_access: level } : mode === 'chosen' ? { mode, chosen: [...chosen].map(([full_name, access]) => ({ full_name, access })) } : { mode };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`${name}'s repositories`}
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            busy={save.pending}
            disabled={save.pending}
            onClick={async () => {
              // closes once the server took it; a refusal keeps the dialog, with its toast
              if ((await save.run({ slug, ...change })).ok) onClose();
            }}
          >
            Save
          </Button>
        </>
      }
    >
      <Segmented label="Reaches" options={MODES} value={mode} onValueChange={setMode} />
      {mode === 'all' && <Segmented label="Level" options={LEVELS} value={level} onValueChange={setLevel} />}
      {mode === 'chosen' &&
        names.map((full) => {
          const access = chosen.get(full);
          return (
            <div key={full} className="repo-pick">
              <Checkbox label={full} checked={!!access} onCheckedChange={(on) => pick(full, on ? 'read' : null)} />
              {access && <Segmented label={`${full} level`} options={LEVELS} value={access} onValueChange={(a) => pick(full, a)} />}
            </div>
          );
        })}
    </Dialog>
  );
}
