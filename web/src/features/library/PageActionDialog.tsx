import { useQuery } from '@tanstack/react-query';
import { useRef, useState, type ReactNode } from 'react';
import type { AccessAudience, PageShown } from '@/api/schema';
import { configQuery } from '@/data';
import { agentsQuery, groupsQuery, peopleQuery, sidebarPinsQuery } from '@/features/team/routing';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { ReadFault } from '@/pagekinds';
import { changePage, changePagePin, deleteLibraryPage, duplicatePage, refreshPages } from './queries';

export type PageAction = 'access' | 'archive' | 'delete' | 'duplicate' | 'pin';

const emptyAudience: AccessAudience = { people: [], teams: [], bots: [] };

type Props = {
  action: PageAction;
  data: PageShown;
  onClose: () => void;
  onLeave: () => Promise<void>;
  onDuplicate: (id: string) => Promise<void>;
};

export function PageActionDialog({ action, data, onClose, onLeave, onDuplicate }: Props) {
  const { page } = data;
  const id = page.file_id;
  const [name, setName] = useState(`Copy of ${page.title}`.slice(0, 200));
  const copyRequest = useRef<{ title: string; key: string } | null>(null);
  const [audience, setAudience] = useState<AccessAudience | null>(page.audience ?? null);
  const [place, setPlace] = useState<string | null>(null);
  const agents = useQuery({ ...agentsQuery, enabled: action === 'pin' });
  const config = useQuery({ ...configQuery, enabled: action === 'pin' });
  const choices = (agents.data ?? []).filter((agent) => agent.can_manage && agent.my_access?.read && agent.id !== config.data?.assistant_bot);
  const sidebar = useQuery({ ...sidebarPinsQuery(place ?? ''), enabled: action === 'pin' && !!place });
  const pin = sidebar.data?.pins?.find((pin) => pin.item === `page:${id}`)?.id;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const archive = page.archived ? 'Restore' : 'Archive';
  const labels: Record<PageAction, string> = { access: 'Save', archive, delete: 'Delete', duplicate: 'Duplicate', pin: pin ? 'Unpin' : 'Pin' };
  const titles: Record<PageAction, string> = { access: 'Who can see it', archive: `${archive} this page?`, delete: 'Delete this page?', duplicate: 'Duplicate page', pin: 'Pin to a sidebar' };
  const ready = action === 'pin' ? sidebar.data?.can_edit === true : action === 'duplicate' ? !!name.trim() : true;

  const save = async () => {
    setBusy(true);
    setError('');
    try {
      if (action === 'access') await changePage(id, { audience });
      if (action === 'archive') await changePage(id, { archived: !page.archived });
      if (action === 'pin' && place) await changePagePin(id, `bot:${place}`, pin);
      if (action === 'delete') {
        await deleteLibraryPage(id);
        await onLeave();
      }
      if (action === 'duplicate') {
        const title = name.trim();
        const request = copyRequest.current?.title === title ? copyRequest.current : { title, key: crypto.randomUUID() };
        copyRequest.current = request;
        const answer = await duplicatePage(id, title, request.key);
        await onDuplicate(answer.page.file_id);
      }
      await refreshPages(id);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The change did not save');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && !busy && onClose()}
      title={titles[action]}
      alert={action === 'delete'}
      description={action === 'delete' ? 'All versions will be permanently deleted.' : undefined}
      actions={
        <>
          <Button disabled={busy} onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={busy || !ready} busy={busy} onClick={save}>{labels[action]}</Button>
        </>
      }
    >
      {action === 'access' && <PageAudience audience={audience} onChange={setAudience} />}
      {action === 'duplicate' && <Field label="Title" value={name} onValueChange={(value) => setName(value.slice(0, 200))} autoFocus />}
      {action === 'pin' && (
        agents.isError || config.isError || sidebar.isError ? (
          <ReadFault what="Sidebars" reads={[agents, config, sidebar]} />
        ) : (
          <Select label="Sidebar" value={place} onValueChange={setPlace} placeholder={agents.isPending || config.isPending ? 'Loading…' : choices.length ? 'Choose a sidebar' : 'No editable sidebars'} options={choices.map((agent) => ({
            value: agent.id,
            label: agent.display_name || agent.slug,
            meta: data.pinned?.some((p) => p.place === `bot:${agent.id}`) ? 'Pinned' : undefined,
          }))} />
        )
      )}
      {error && <p className="f-err" role="alert">{error}</p>}
    </Dialog>
  );
}

function PageAudience({ audience, onChange }: { audience: AccessAudience | null; onChange: (audience: AccessAudience | null) => void }) {
  const people = useQuery(peopleQuery);
  const groups = useQuery(groupsQuery);
  const agents = useQuery(agentsQuery);
  const failed = [people, groups, agents].filter((query) => query.isError);
  const current = audience ?? emptyAudience;
  const choices = [
    ['People', 'people', people.data ?? []],
    ['Groups', 'teams', groups.data ?? []],
    ['Agents', 'bots', (agents.data ?? []).map((a) => ({ id: a.id, name: a.display_name || a.slug }))],
  ] as const;
  const option = (key: 'people' | 'teams' | 'bots', id: string, label: string): ReactNode => {
    const ids = current[key] ?? [];
    return <Checkbox key={id} label={label} checked={ids.includes(id)} onCheckedChange={(on) => onChange({ ...current, [key]: on ? [...ids, id] : ids.filter((value) => value !== id) })} />;
  };
  return (
    <>
      <Segmented label="Access" value={audience ? 'listed' : 'inherited'} options={[{ value: 'inherited', label: 'Inherit access' }, { value: 'listed', label: 'Only listed' }]} onValueChange={(value) => onChange(value === 'listed' ? emptyAudience : null)} />
      {audience && failed.length > 0 && (
        <ReadFault what="Access options" reads={failed} />
      )}
      {audience && choices.map(([label, key, items]) => (
        <fieldset className="page-audience" key={key}>
          <legend>{label}</legend>
          {items.map((item) => option(key, item.id, item.name))}
        </fieldset>
      ))}
    </>
  );
}
