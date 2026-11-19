import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { refusal, run } from '@/actions';
import { meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { toast } from '@/kit/Toast';
import { useDestination } from './Destination';
import { Line, Verb, Word } from './parts';
import { gatewayQuery, type GatewayStatus, type GatewayView } from './queries';

/* Models › When a plan runs out › the Tico Gateway's key (old app settings-providers.ts gatewayHTML), as lines. The key
   is typed once, sent, and never shown again; only its preview comes back. A new address while a key is saved is held
   by the server and asked here (Keep or Clear). Set by the server's environment, it is not shown. */

export function GatewayLines() {
  const me = useQuery(meQuery);
  const owner = me.data?.role === 'owner';
  const gw = useQuery({ ...gatewayQuery, enabled: owner, retry: false });
  const [editing, setEditing] = useState(false);
  const v = gw.data as GatewayStatus | undefined;
  if (!owner || !v?.editable) return null;
  return (
    <>
      <Line label="Gateway key" target="setting:gateway">
        {v.connected && v.working === false ? <Word>{v.message || 'Not working'}</Word> : v.preview || (v.connected ? 'Saved' : 'None')}
        <Verb onClick={() => setEditing(true)}>Change</Verb>
      </Line>
      {editing && <GatewayDialog v={v} onClose={() => setEditing(false)} />}
    </>
  );
}

function GatewayDialog({ v, onClose }: { v: GatewayView; onClose: () => void }) {
  const { dialog } = useDestination();
  // The key lives in this field only, and leaves it as soon as it is sent.
  const [key, setKey] = useState('');
  const [url, setUrl] = useState(v.url && v.url !== v.default_url ? v.url : '');
  const [busy, setBusy] = useState(false);
  const save = async () => {
    const typed = key.trim();
    setKey('');
    setBusy(true);
    const r = await run('settings.saveGateway', { url: url.trim(), ...(typed ? { key: typed } : {}) }, { via: 'click' });
    setBusy(false);
    if (r.ok) return onClose();
    // Cancel on the held move saved nothing, and says nothing.
    if (!r.error?.includes('Nothing was saved')) toast(refusal(r, "Couldn't save"));
  };
  return (
    <>
      <Dialog
        open
        onOpenChange={(o) => !o && onClose()}
        title="Tico Gateway"
        actions={
          <>
            <Button variant="quiet" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" busy={busy} onClick={() => void save()}>
              Save
            </Button>
          </>
        }
      >
        <div className="set-add">
          <Field
            label="Key"
            type="password"
            value={key}
            onValueChange={setKey}
            autoComplete="off"
            placeholder={v.preview ? `Saved (${v.preview})` : undefined}
            autoFocus
          />
          <Field label="Address" type="url" value={url} onValueChange={setUrl} placeholder={v.default_url} />
        </div>
      </Dialog>
      {dialog}
    </>
  );
}
