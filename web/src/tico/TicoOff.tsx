import { useState } from 'react';
import { run } from '@/actions';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';

/* Tico is off (the old app's "The Assistant is off"): one Turn on for whoever the server lets turn it on, the plain
   state for everyone else. */
export function TicoOff({ canTurnOn }: { canTurnOn: boolean }) {
  const [pending, setPending] = useState(false);
  const [note, setNote] = useState('');
  const press = async () => {
    setPending(true);
    setNote('');
    const r = await run('tico.turnOn', {}, { via: 'click' });
    setPending(false);
    if (!r.ok) setNote('Could not turn it on. Try again.');
    else if (r.out !== 'active') setNote('Tico needs a computer before it can answer.');
  };
  return (
    <div className="chat-body">
      <Empty icon="asleep" title="Tico is off" text={canTurnOn ? note || undefined : 'Ask the owner to turn it on.'}>
        {canTurnOn ? (
          <Button variant="primary" busy={pending} disabled={pending} onClick={() => press()}>
            Turn on
          </Button>
        ) : null}
      </Empty>
    </div>
  );
}
