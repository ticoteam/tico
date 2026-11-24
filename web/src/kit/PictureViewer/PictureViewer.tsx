import type { KeyboardEvent } from 'react';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Viewer } from '@/kit/Viewer';
import './PictureViewer.css';

export interface Picture {
  /** Only the app's own `/api/` addresses are drawn; anything else is left out. */
  src: string;
  /** Read aloud, and shown over the picture. */
  name: string;
  /** Where Download fetches the full file; `src` when left out. */
  download?: string;
}

export interface PictureViewerProps {
  pictures: Picture[];
  /** The one shown; null when the viewer is closed. */
  at: number | null;
  onAtChange: (at: number | null) => void;
  /** Pictures this page made itself (a diagram drawn in a chat) as `blob:` addresses of its own; only those, then. */
  local?: boolean;
}

const OWN = /^\/api\//i;

/** Only pictures this app serves: one from outside would call that server the moment it is drawn. With `local`, only
    the page's own `blob:` addresses, which load nothing from anywhere. */
export const ownPictures = (list: Picture[], local = false) =>
  list.filter((p) => (local ? p.src.startsWith(`blob:${location.origin}/`) : OWN.test(p.src)));

/**
 * A picture full size over the page (a chat's, a task's), in the full-screen layer (kit/Viewer) bare on its dim: the
 * arrow keys and buttons walk the set it was opened from, Download saves the file, Escape or a click on the dim closes.
 */
export function PictureViewer({ pictures, at, onAtChange, local = false }: PictureViewerProps) {
  const list = ownPictures(pictures, local);
  const i = at == null ? -1 : Math.min(at, list.length - 1);
  const p = i >= 0 ? list[i] : undefined;
  const many = list.length > 1;
  const step = (d: number) => {
    if (!many || i < 0) return;
    onAtChange((i + d + list.length) % list.length);
  };
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'ArrowRight') step(1);
    else if (e.key === 'ArrowLeft') step(-1);
    else return;
    e.preventDefault();
  };
  return (
    <Viewer
      open={!!p}
      onClose={() => onAtChange(null)}
      title={p?.name ?? 'Picture'}
      meta={many ? `${i + 1} of ${list.length}` : undefined}
      look="bare"
      onKeyDown={onKeyDown}
      actions={
        <>
          {many && <IconButton icon="chevron" className="flip" label="Previous" onClick={() => step(-1)} />}
          {many && <IconButton icon="chevron" label="Next" onClick={() => step(1)} />}
          {/* A diagram the app drew is an SVG of its own: opened as a page it would run on the app's origin. */}
          {p && !local && (
            <a className="ib" href={p.download ?? p.src} download={p.name} aria-label="Download" title="Download">
              <Icon name="download" />
            </a>
          )}
        </>
      }
    >
      {p && <img className="pv-img" src={p.src} alt={p.name} />}
    </Viewer>
  );
}
