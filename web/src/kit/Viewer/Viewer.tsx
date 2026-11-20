import { Dialog as BaseDialog } from '@base-ui/react/dialog';
import { useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react';
import { IconButton } from '@/kit/IconButton';
import './Viewer.css';

export interface ViewerProps {
  /** Shown, or closed (null content is fine: nothing draws while closed). */
  open: boolean;
  onClose: () => void;
  /** What it is, by its name: a file's title, a page's, a picture's. Read aloud as the dialog's name. */
  title: string;
  /** Beside the title, quieter: the kind ("PDF"), or where you are in a set ("2 of 5"). */
  meta?: ReactNode;
  /** Icon buttons before Close: Open where it lives, Download, the arrows of a set. */
  actions?: ReactNode;
  /** `sheet`: the thing sits on a sheet of its own (a page, a PDF, a table, text). `bare`: on the dim itself, centred
      (a picture). */
  look?: 'sheet' | 'bare';
  onKeyDown?: (e: KeyboardEvent) => void;
  children?: ReactNode;
}

/** A pull this far down the bar on a phone lets it go; less springs it back. */
const LET_GO = 96;

/**
 * The full-screen layer above the sheets (Tico, the company, the thread, the artifact): a file, a page, a picture or a
 * table at full size, opened from where it shows small. Its name and controls sit on the dim above it; the thing
 * fills the rest. Escape, Close or a click on the dim closes it; on a phone it fills the screen and a pull down on its
 * bar closes it. Focus stays inside while it is open and goes back after.
 */
export function Viewer({ open, onClose, title, meta, actions, look = 'sheet', onKeyDown, children }: ViewerProps) {
  const [pull, setPull] = useState(0);
  const from = useRef<number | null>(null);
  const down = (e: PointerEvent<HTMLDivElement>) => {
    // a mouse drags nothing here; a finger or pen pulls the layer down from its bar
    if (e.pointerType === 'mouse' || (e.target as Element).closest('button, a')) return;
    from.current = e.clientY;
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    if (from.current !== null) setPull(Math.max(0, e.clientY - from.current));
  };
  const up = () => {
    if (from.current === null) return;
    from.current = null;
    if (pull > LET_GO) onClose();
    setPull(0);
  };
  return (
    <BaseDialog.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <BaseDialog.Portal>
        <BaseDialog.Backdrop className="scrim kit-scrim vw-scrim" />
        <BaseDialog.Viewport className="scrim kit-viewport vw-viewport" onClick={(e) => e.target === e.currentTarget && onClose()}>
          <BaseDialog.Popup
            className={`vw is-${look}`}
            aria-label={title}
            onKeyDown={onKeyDown}
            data-pulled={pull > 0 || undefined}
            style={pull ? { transform: `translateY(${pull}px)` } : undefined}
          >
            <div className="vw-bar" onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
              <span className="vw-grab" aria-hidden="true" />
              <BaseDialog.Title className="vw-title">{title}</BaseDialog.Title>
              {meta && <span className="vw-meta">{meta}</span>}
              <span className="grow" />
              {actions}
              <BaseDialog.Close render={<IconButton icon="close" label="Close" />} />
            </div>
            <div className="vw-body">{children}</div>
          </BaseDialog.Popup>
        </BaseDialog.Viewport>
      </BaseDialog.Portal>
    </BaseDialog.Root>
  );
}
