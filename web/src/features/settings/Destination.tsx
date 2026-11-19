import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import type { DestinationChange, DestinationChoice } from './queries';

/* A save that would send saved credentials to another address is held by the server (409 destination_change, naming
   the old and new address and the credentials by label, never a value). The person keeps them (they go to the new
   address), clears them to type again, or cancels: nothing is saved. Use with `withDestination` from queries. */

export function DestinationDialog({ change, onAnswer }: { change: DestinationChange; onAnswer: (choice: DestinationChoice | null) => void }) {
  const labels = change.credentials.join(', ') || 'The saved credentials';
  const several = change.credentials.length !== 1;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onAnswer(null)}
      title={`Change where ${change.name} sends?`}
      description={`${labels} ${several ? 'were' : 'was'} saved for ${change.old}. This change sends to ${change.new}. Keep ${several ? "them" : "it"} for the new address, or clear and type again.`}
      actions={
        <>
          <Button variant="quiet" onClick={() => onAnswer(null)}>
            Cancel
          </Button>
          <Button onClick={() => onAnswer('clear')}>Clear</Button>
          <Button variant="primary" onClick={() => onAnswer('keep')}>
            Keep
          </Button>
        </>
      }
    />
  );
}

type Ask = (change: DestinationChange) => Promise<DestinationChoice | null>;
let onScreen: Ask | null = null;

/** For an action's `withDestination`: asks through the editor on screen (its `useDestination`); none is Cancel. */
export const askDestination: Ask = (change) => (onScreen ? onScreen(change) : Promise.resolve(null));

/** `ask` for `withDestination`, and the dialog it opens; draw `dialog` where the editor is. While it is drawn, an
    action's `askDestination` asks here too. */
export function useDestination(): { ask: Ask; dialog: ReactNode } {
  const [change, setChange] = useState<DestinationChange | null>(null);
  const answer = useRef<(choice: DestinationChoice | null) => void>(() => {});
  const ask: Ask = (c) =>
    new Promise<DestinationChoice | null>((resolve) => {
      answer.current = (choice) => {
        setChange(null);
        resolve(choice);
      };
      setChange(c);
    });
  useEffect(() => {
    onScreen = ask;
    return () => {
      if (onScreen === ask) onScreen = null;
    };
  });
  return { ask, dialog: change ? <DestinationDialog change={change} onAnswer={(choice) => answer.current(choice)} /> : null };
}
