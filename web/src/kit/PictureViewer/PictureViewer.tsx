import { Dialog as BaseDialog } from '@base-ui/react/dialog';
import type { KeyboardEvent } from 'react';
import { IconButton } from '@/kit/IconButton';
import { Icon } from '@/kit/Icon';
import './PictureViewer.css';

export interface Picture {
  /** Only the app's own `/api/` addresses are drawn; anything else is left out. */
  src: string;
  /** Read aloud, and shown under the picture. */
  name: string;
  /** Where Download fetches the full file; `src` when left out. */
  download?: string;
}

export interface PictureViewerProps {
  pictures: Picture[];
  /** The one shown; null when the viewer is closed. */
  at: number | null;
  onAtChange: (at: number | null) => void;
}

const OWN = /^\/api\//i;

/** Only pictures this app serves: one from outside would call that server the moment it is drawn. */
export const ownPictures = (list: Picture[]) => list.filter((p) => OWN.test(p.src));

/**
 * A picture full size over the page (a chat's, a task's): Escape or a click on the dim closes it, the arrow keys walk
 * the set it was opened from, Download saves the file. Focus stays inside while it is open and goes back after.
 */
export function PictureViewer({ pictures, at, onAtChange }: PictureViewerProps) {
  const list = ownPictures(pictures);
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
    <BaseDialog.Root open={!!p} onOpenChange={(open) => !open && onAtChange(null)}>
      <BaseDialog.Portal>
        <BaseDialog.Backdrop className="scrim kit-scrim pv-scrim" />
        <BaseDialog.Viewport className="scrim kit-viewport pv-viewport" onClick={(e) => e.target === e.currentTarget && onAtChange(null)}>
          <BaseDialog.Popup className="pv" onKeyDown={onKeyDown} aria-label={p?.name ?? 'Picture'}>
            {p && <img className="pv-img" src={p.src} alt={p.name} />}
            <div className="pv-bar">
              <span className="pv-name">{p?.name}</span>
              {many && (
                <span className="pv-n">
                  {i + 1} of {list.length}
                </span>
              )}
              <span className="grow" />
              {many && <IconButton icon="chevron" className="flip" label="Previous" onClick={() => step(-1)} />}
              {many && <IconButton icon="chevron" label="Next" onClick={() => step(1)} />}
              {p && (
                <a className="ib" href={p.download ?? p.src} download={p.name} aria-label="Download" title="Download">
                  <Icon name="download" />
                </a>
              )}
              <BaseDialog.Close render={<IconButton icon="close" label="Close" />} />
            </div>
          </BaseDialog.Popup>
        </BaseDialog.Viewport>
      </BaseDialog.Portal>
    </BaseDialog.Root>
  );
}
