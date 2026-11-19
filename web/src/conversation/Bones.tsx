import { Skeleton, useDelayed } from '@/kit/Skeleton';

// Fixed, uneven lines per message (threads.js skConv), so the bones never read as a pattern.
const LINES = [[62, 88, 40], [78, 54], [90, 70, 82, 36], [48], [84, 66]];

/** A chat's bones while its messages load, after 150 ms: a face, a name and a few lines a message. */
export function MessageBones({ label = 'Loading messages', className = 'chat-body' }: { label?: string; className?: string }) {
  const shown = useDelayed(150);
  if (!shown) return null;
  return (
    <div className={className} role="status" aria-busy="true" aria-label={label}>
      {LINES.map((lines, i) => (
        <div key={i} className="msg is-big is-skeleton" aria-hidden="true">
          <Skeleton className="sk-av sk-28" />
          <div className="msg-main">
            <Skeleton width={24} />
            {lines.map((w, j) => (
              <Skeleton key={j} width={w} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
