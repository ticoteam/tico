import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Search } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts } from '@/kit/LabelValue';
import { Section } from '@/pagekinds';
import { SwitchLine, act } from './parts';
import { accessQuery, feedsQuery, findPlaces, operateQuery, type OurCompany } from './queries';

/* Settings › Contacts, the parts the old app had under the types: where our company operates (the places whose laws
   apply; an owner's or an admin's to change), and which inboxes add every mail sender to Contacts. */

function useAdmin(): boolean {
  const me = useQuery(meQuery);
  const access = useQuery({ ...accessQuery, retry: false });
  return me.data?.role === 'owner' || access.data?.you === 'owner' || access.data?.you === 'admin';
}

/** Find a place by name and add it; what is typed is asked for after a short pause. */
function AddPlace({ us }: { us: OurCompany }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [asked, setAsked] = useState('');
  useEffect(() => {
    const t = setTimeout(() => setAsked(q.trim()), 200);
    return () => clearTimeout(t);
  }, [q]);
  const found = useQuery({ ...findPlaces(asked), enabled: open && !!asked });
  const have = new Set(us.operates_in.map((p) => p.id));
  const rows = (found.data ?? []).filter((p) => !have.has(p.id));
  const add = async (place: string) => {
    if (await act('settings.addPlace', { us: us.id, place })) {
      setOpen(false);
      setQ('');
    }
  };
  return (
    <>
      <IconButton icon="plus" label="Add a place" size="sm" quiet onClick={() => setOpen(true)} />
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Where we operate"
        actions={
          <Button variant="quiet" onClick={() => setOpen(false)}>
            Cancel
          </Button>
        }
      >
        <div className="set-add">
          <Search label="Find a place" placeholder="Find a place" value={q} onValueChange={setQ} autoFocus />
          {found.data &&
            (rows.length === 0 ? (
              <Empty title="No places found" />
            ) : (
              rows.map((p) => (
                <button key={p.id} type="button" className="row set-link" onClick={() => void add(p.id)}>
                  <span className="row-title">{p.name}</span>
                </button>
              ))
            ))}
        </div>
      </Dialog>
    </>
  );
}

export function Operate() {
  const us = useQuery({ ...operateQuery, retry: false });
  const admin = useAdmin();
  const v = us.data;
  if (!v?.operates_in) return null;
  return (
    <Section name="Where we operate" count={v.operates_in.length} actions={admin && <AddPlace us={v} />} target="setting:operate">
      {v.operates_in.length === 0 ? (
        <Empty title="No places yet" />
      ) : (
        v.operates_in.map((p) => (
          <div key={p.id} className="row g-line" title={p.name}>
            <span className="row-title">{p.name}</span>
            {admin && p.link_id ? (
              <IconButton icon="close" label={`Remove ${p.name}`} size="sm" quiet onClick={() => void act('settings.dropPlace', { link: p.link_id })} />
            ) : (
              <span />
            )}
          </div>
        ))
      )}
    </Section>
  );
}

export function MailFeeds() {
  const feeds = useQuery({ ...feedsQuery, retry: false });
  const list = feeds.data ?? [];
  if (list.length === 0) return null;
  return (
    <Section name="Every mail sender" count={list.length} target="setting:mail-feeds">
      <Facts label="Every mail sender">
        {list.map((b) => (
          <SwitchLine
            key={b.id}
            label={b.person_name && b.kind !== 'shared' ? `${b.address}, ${b.person_name}` : b.address}
            target={`mailbox:${b.id}`}
            checked={!!b.feeds_directory}
            onChange={(on) => void act('settings.mailFeed', { box: b.id, on })}
          />
        ))}
      </Facts>
    </Section>
  );
}
