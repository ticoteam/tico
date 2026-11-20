import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { meQuery, useActors } from '@/data';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { Switch } from '@/kit/Switch';
import { actLine, applyFlow, destName } from './board';
import { press, pressOut } from './parts';
import { filledQuery, teamsQuery, templatesQuery } from './queries';
import type { Routine } from './types';

/* Inbox templates (automations-new.ts auNewInbox, auApplyTemplate): a new inbox routine from a template, for a person,
   an agent or (for a channel) a team; and an inbox board moved onto a template, keeping its own rules, questions and
   instructions. The server fills the template for the owner and inbox; it refuses an owner who can't hear that inbox. */

type Whose = 'person' | 'agent' | 'team';

/** `human:` ids as the template endpoint reads them. */
const ref = (o: string) => o.replace(/^human:/, 'person:');

export function NewInbox({ onClose }: { onClose: () => void }) {
  const me = useQuery(meQuery).data;
  const actors = useActors();
  const templates = useQuery(templatesQuery);
  const [inbox, setInbox] = useState<string | null>(null);
  const [whose, setWhose] = useState<Whose>('person');
  const [owner, setOwner] = useState<string | null>(null);
  const [template, setTemplate] = useState<string | null>(null);
  const [on, setOn] = useState(true);
  const [busy, setBusy] = useState(false);

  const address = (inbox ?? me?.email ?? '').trim();
  // a team hears a channel, not a mailbox
  const kind: Whose = whose === 'team' && address.includes('@') ? 'person' : whose;
  const teams = useQuery({ ...teamsQuery, enabled: kind === 'team' }).data ?? [];
  const choices =
    kind === 'team'
      ? teams.map((t) => ({ value: `group:${t.id}`, label: t.name }))
      : actors.filter((a) => a.kind === (kind === 'agent' ? 'agent' : 'person')).map((a) => ({ value: a.id, label: a.name }));
  const who = owner && choices.some((c) => c.value === owner) ? owner : kind === 'person' && me ? me.id : null;
  const list = templates.data ?? [];
  const tid = template ?? list[0]?.id ?? null;
  const picked = list.find((t) => t.id === tid);
  const ready = !!address && !!who && !!tid && !busy;

  const create = async () => {
    if (!who || !tid) return;
    setBusy(true);
    const r = await pressOut<string>('automations.newInbox', { inbox: address, owner: who, template: tid, on });
    setBusy(false);
    if (r) onClose();
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="New inbox"
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => create()}>
            Create
          </Button>
        </>
      }
    >
      <div className="rr-form">
        <Field label="Inbox" value={address} onValueChange={setInbox} placeholder="name@company.example" autoComplete="off" autoFocus />
        <Segmented
          label="Whose inbox"
          options={[
            { value: 'person', label: 'Person' },
            { value: 'agent', label: 'Agent' },
            ...(address.includes('@') ? [] : [{ value: 'team' as const, label: 'Team' }]),
          ]}
          value={kind}
          onValueChange={(v) => {
            setWhose(v);
            setOwner(null);
          }}
        />
        <Select label={kind === 'team' ? 'Team' : kind === 'agent' ? 'Agent' : 'Person'} options={choices} value={who} onValueChange={setOwner} placeholder="Choose" />
        <Select
          label="Template"
          options={list.map((t) => ({ value: t.id, label: t.name }))}
          value={tid}
          onValueChange={setTemplate}
          placeholder={templates.isPending ? 'Loading' : templates.isError ? "Couldn't load templates" : 'Choose'}
        />
        {picked && <p className="rr-note">{picked.destinations.join(' · ')}</p>}
        <Switch label="Turn on now" checked={on} onCheckedChange={setOn} />
      </div>
    </Dialog>
  );
}

export function ApplyTemplate({ routine, onClose }: { routine: Routine; onClose: () => void }) {
  const actors = useActors();
  const templates = useQuery(templatesQuery);
  const list = templates.data ?? [];
  const [template, setTemplate] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const tid = template ?? list[0]?.id ?? null;
  const inbox = String(routine.trigger.inbox ?? '');
  const filled = useQuery({ ...filledQuery(tid ?? '', ref(routine.owner_id ?? ''), inbox), enabled: !!tid && !!routine.owner_id });
  const flow = filled.data ? applyFlow(routine, filled.data) : null;
  const named = new Map(actors.flatMap((a) => [[a.id, a.name] as const, [ref(a.id), a.name] as const]));
  const name = (id: string) => named.get(id) ?? id;

  const apply = async () => {
    if (!tid) return;
    setBusy(true);
    const ok = await press('automations.applyTemplate', { routine: routine.id, template: tid });
    setBusy(false);
    if (ok) onClose();
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Apply a template"
      description="Rules, questions and instructions stay. Destinations come from the template."
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!flow || busy} onClick={() => apply()}>
            Apply
          </Button>
        </>
      }
    >
      <div className="rr-form">
        <Select
          label="Template"
          options={list.map((t) => ({ value: t.id, label: t.name }))}
          value={tid}
          onValueChange={setTemplate}
          placeholder={templates.isPending ? 'Loading' : templates.isError ? "Couldn't load templates" : 'Choose'}
        />
        {filled.isError ? (
          <p className="rr-note">{"Couldn't fill this template for this inbox"}</p>
        ) : filled.data && !flow ? (
          <p className="rr-note">{"This template doesn't fit this inbox"}</p>
        ) : (
          flow && (
            <ul className="rr-dests" aria-label="Destinations">
              {flow.destinations.map((d) => (
                <li key={d.id}>
                  <span className="rr-dest">{destName(d.id, flow.destinations)}</span>
                  <span className="rr-note">{(d.then ?? []).map((x) => actLine(x, name)).join(', ') || 'Do nothing'}</span>
                </li>
              ))}
            </ul>
          )
        )}
      </div>
    </Dialog>
  );
}
