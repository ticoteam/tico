import { useState, type ReactNode } from 'react';
import { Icon } from '@/kit/Icon';
import './Thumbs.css';

/** A picture or a video, shown small. */
export interface Thumb {
  id: string;
  name: string;
  video?: boolean;
  /** Its small copy (a picture's /thumb, a video's /poster frame), tried first. */
  src?: string | null;
  /** The picture itself, when its small copy is not there (not made yet, or never). */
  full?: string | null;
  /** A line under it: the task it belongs to. */
  sub?: ReactNode;
}

export interface ThumbsProps {
  items: Thumb[];
  /** What the list is, read out. */
  label: string;
  onOpen: (item: Thumb) => void;
  /** One row that scrolls sideways (at the top of a task), else tiles that wrap. */
  strip?: boolean;
}

/** Pictures and videos as previews, newest first: a press opens one. A video shows its frame and a play mark; one with
    nothing to show is a plain card with its mark. */
export function Thumbs({ items, label, onOpen, strip }: ThumbsProps) {
  // a copy that does not load falls back to the next, then to the plain card: never a broken image
  const [lost, setLost] = useState<ReadonlySet<string>>(new Set());
  if (!items.length) return null;
  return (
    <ul className={strip ? 'thumbs is-strip' : 'thumbs'} aria-label={label}>
      {items.map((t) => {
        const face = [t.src, t.full].find((s): s is string => !!s && !lost.has(s));
        return (
          <li key={t.id} className="thumb-item">
            <button type="button" className={t.video ? 'thumb is-video' : 'thumb'} title={t.name} aria-label={t.name} data-target={`thumb:${t.id}`} onClick={() => onOpen(t)}>
              {face ? (
                <img src={face} alt="" onError={() => setLost((was) => new Set(was).add(face))} />
              ) : (
                !t.video && <Icon name="file" />
              )}
              {t.video && (
                <span className="thumb-play">
                  <Icon name="play" />
                </span>
              )}
            </button>
            {t.sub && <span className="thumb-sub">{t.sub}</span>}
          </li>
        );
      })}
    </ul>
  );
}
