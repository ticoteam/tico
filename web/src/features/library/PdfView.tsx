import { Dialog as BaseDialog } from '@base-ui/react/dialog';
import { useState } from 'react';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { useCards } from '@/kit/Popover';

/* A PDF wherever it opens (an email's attachment or letter, a task's file, Files): the browser's own viewer with its
   toolbar and page list off, fit to the width, so the pages are one long scroll as tall as the frame. Expand shows it
   over the whole window; Escape or Close brings it back. A phone's browser draws only the first page in a frame, so
   there it opens in a tab of its own. */

/** The viewer's own settings, in the address's fragment (Chromium and Electron read them). */
export const pdfSrc = (src: string) => `${src.split('#')[0]}#toolbar=0&navpanes=0&view=FitH`;

/** `src` is what the frame shows (the file, or its bytes read here); `url` and `name` are what Download saves. */
export function PdfView({ src, title, url, name }: { src: string; title: string; url: string; name?: string | null }) {
  const cards = useCards();
  const [full, setFull] = useState(false);
  if (cards)
    return (
      <div className="fv-none">
        <Empty title={title} icon="file">
          <a className="btn" href={src} target="_blank" rel="noreferrer">
            <Icon name="popout" />
            Open PDF
          </a>
        </Empty>
      </div>
    );
  const frame = pdfSrc(src);
  return (
    <div className="fv-pdf">
      <iframe className="fv-frame" src={frame} title={title} />
      <IconButton icon="expand" label="Expand" size="sm" className="fv-pdf-expand" onClick={() => setFull(true)} />
      <BaseDialog.Root open={full} onOpenChange={setFull}>
        <BaseDialog.Portal>
          <BaseDialog.Backdrop className="scrim kit-scrim" />
          <BaseDialog.Viewport className="scrim kit-viewport fv-pdf-viewport">
            <BaseDialog.Popup className="fv-pdf-full" aria-label={title}>
              <div className="fv-pdf-bar">
                <span>{title}</span>
                <span className="grow" />
                <a className="ib" href={url} download={name || true} aria-label="Download" title="Download">
                  <Icon name="download" />
                </a>
                <BaseDialog.Close render={<IconButton icon="close" label="Close" />} />
              </div>
              {full && <iframe src={frame} title={`${title}, full window`} />}
            </BaseDialog.Popup>
          </BaseDialog.Viewport>
        </BaseDialog.Portal>
      </BaseDialog.Root>
    </div>
  );
}
