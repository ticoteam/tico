import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { configQuery, meQuery } from '@/data';
import { accessQuery } from '@/features/settings/routing';
import { Link } from '@/kit/Link';
import { NavNote } from '@/kit/OfflineBar';

function ReleaseNote({ latest, storageKey }: { latest: string; storageKey: string }) {
  const [away, setAway] = useState(() => {
    try {
      return localStorage.getItem(storageKey) === '1';
    } catch {
      return false;
    }
  });
  if (away) return null;
  const dismiss = () => {
    try {
      localStorage.setItem(storageKey, '1');
    } catch {
      // The note can still be put away while it is mounted.
    }
    setAway(true);
  };
  return (
    <NavNote icon="refresh" title={`Tico v${latest} is out`} onDismiss={dismiss}>
      <Link className="btn" href="/settings/general?open=tico-version">
        View update
      </Link>
    </NavNote>
  );
}

export function UpdateNotice() {
  const { data: me } = useQuery(meQuery);
  const { data: update } = useQuery({ ...configQuery, select: (config) => config.update });
  const latest = update?.available && !update.held ? update.latest : '';
  const { data: access } = useQuery({ ...accessQuery, enabled: !!latest && me?.role === 'person', retry: false });
  if (!latest || !me || (me.role !== 'owner' && (me.role !== 'person' || access?.you !== 'admin'))) return null;
  const storageKey = `tico-next:update-dismissed:${me.id}:${latest}`;
  return <ReleaseNote key={storageKey} latest={latest} storageKey={storageKey} />;
}
