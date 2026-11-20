import { Dialog as BaseDialog } from '@base-ui/react/dialog';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { openPdf, type PdfDoc } from './pdf';

/* A PDF wherever it opens (an email's attachment or letter, a task's file, Files): its pages drawn by PDF.js as one
   long scroll, each fit to the width and drawn only as it nears the view, the same in every browser and on a phone.
   Expand shows it over the whole window; Escape or Close brings it back. Where PDF.js cannot read it, it opens in a
   tab of its own. */

/** A page's size in PDF points, as it is shown (its rotation applied). */
export interface PageSize {
  width: number;
  height: number;
}

const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3];
/** A canvas past about 16 million pixels draws blank on iOS: a page is drawn no finer than this. */
const MAX_PIXELS = 16_000_000;

type Read = { src: string; doc: PdfDoc; sizes: PageSize[] } | { src: string; failed: true };

/** The PDF at `src`, read once while it is shown, with each page's size for the scroll's layout. */
export function usePdf(src: string): { doc: PdfDoc; sizes: PageSize[] } | 'failed' | null {
  const [read, setRead] = useState<Read | null>(null);
  useEffect(() => {
    let live = true;
    let destroy: (() => Promise<void>) | undefined;
    openPdf(src)
      .then(async (open) => {
        destroy = open.destroy;
        if (!live) return void open.destroy();
        const doc = await open.doc;
        const sizes: PageSize[] = [];
        for (let n = 1; n <= doc.numPages; n++) {
          const { width, height } = (await doc.getPage(n)).getViewport({ scale: 1 });
          sizes.push({ width, height });
        }
        if (live) setRead({ src, doc, sizes });
      })
      .catch(() => {
        if (live) setRead({ src, failed: true });
      });
    return () => {
      live = false;
      void destroy?.();
    };
  }, [src]);
  if (!read || read.src !== src) return null;
  return 'failed' in read ? 'failed' : read;
}

/** One page: a sheet of the page's shape, drawn while it is near the view and let go when it is far. */
function Page({ doc, n, size, width, root, children }: { doc: PdfDoc; n: number; size: PageSize; width: number; root: HTMLElement | null; children?: ReactNode }) {
  const sheet = useRef<HTMLDivElement>(null);
  // Without IntersectionObserver (an old browser, a test page) every page is near.
  const [near, setNear] = useState(() => typeof IntersectionObserver === 'undefined');
  useEffect(() => {
    const el = sheet.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    const seen = new IntersectionObserver((entries) => setNear(entries.some((e) => e.isIntersecting)), { root, rootMargin: '100% 0px' });
    seen.observe(el);
    return () => seen.disconnect();
  }, [root]);
  useEffect(() => {
    const el = sheet.current;
    if (!el || !near || width <= 0) return;
    let task: { cancel: () => void } | undefined;
    let live = true;
    void (async () => {
      const page = await doc.getPage(n);
      if (!live) return;
      const dpr = window.devicePixelRatio || 1;
      const scale = Math.min((width / size.width) * dpr, Math.sqrt(MAX_PIXELS / (size.width * size.height)));
      const viewport = page.getViewport({ scale });
      const canvas = document.createElement('canvas');
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      const render = page.render({ canvas, viewport });
      task = render;
      await render.promise;
      // Drawn off to the side and swapped in whole, so a zoom never blanks the page while it redraws.
      if (!live) return;
      const old = el.querySelector('canvas');
      if (old) old.replaceWith(canvas);
      else el.prepend(canvas);
    })().catch(() => undefined);
    return () => {
      live = false;
      task?.cancel();
    };
  }, [doc, n, size, width, near]);
  useEffect(() => {
    // Far from the view, a page gives back its pixels: a long PDF never holds every page's drawing at once.
    if (!near) sheet.current?.querySelector('canvas')?.remove();
  }, [near]);
  return (
    <div ref={sheet} className="fv-pdf-page" data-page={n} style={{ width, height: Math.round((width * size.height) / size.width) }}>
      {children}
    </div>
  );
}

/** The pages as one scroll, fit to its width at zoom 1. `overlay` lays something over a page (a signature placed). */
export function PdfPages({
  doc,
  sizes,
  zoom,
  label,
  overlay,
}: {
  doc: PdfDoc;
  sizes: PageSize[];
  zoom: number;
  label: string;
  overlay?: (n: number, shown: { width: number; height: number }) => ReactNode;
}) {
  const [box, setBox] = useState<HTMLElement | null>(null);
  const [fit, setFit] = useState(0);
  useEffect(() => {
    if (!box) return;
    const measure = () => {
      const pad = parseFloat(getComputedStyle(box).paddingLeft) || 0;
      setFit(Math.max(0, box.clientWidth - 2 * pad));
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const watch = new ResizeObserver(measure);
    watch.observe(box);
    return () => watch.disconnect();
  }, [box]);
  const width = Math.round(fit * zoom);
  return (
    <section ref={setBox} className="fv-pdf-pages" aria-label={label} tabIndex={0}>
      {sizes.map((size, i) => (
        <Page key={i} doc={doc} n={i + 1} size={size} width={width} root={box}>
          {overlay?.(i + 1, { width, height: Math.round((width * size.height) / size.width) })}
        </Page>
      ))}
    </section>
  );
}

/** Zoom out and in, a step at a time, between half and three times the fit. */
export function ZoomButtons({ zoom, onZoom }: { zoom: number; onZoom: (z: number) => void }) {
  const at = ZOOMS.indexOf(zoom);
  return (
    <>
      <IconButton icon="minus" label="Zoom out" size="sm" quiet disabled={at <= 0} onClick={() => onZoom(ZOOMS[at - 1] ?? zoom)} />
      <IconButton icon="plus" label="Zoom in" size="sm" quiet disabled={at >= ZOOMS.length - 1} onClick={() => onZoom(ZOOMS[at + 1] ?? zoom)} />
    </>
  );
}

const pagesOf = (n: number) => (n === 1 ? '1 page' : `${n} pages`);

/** `src` is what PDF.js reads (the file, or its bytes read here); `url` and `name` are what Download saves. */
export function PdfView({ src, title, url, name }: { src: string; title: string; url: string; name?: string | null }) {
  const pdf = usePdf(src);
  const [full, setFull] = useState(false);
  const [zoom, setZoom] = useState(1);
  if (pdf === 'failed')
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
  if (!pdf) return <div className="fv-pdf" aria-busy="true" />;
  const count = pagesOf(pdf.sizes.length);
  return (
    <div className="fv-pdf">
      <PdfPages doc={pdf.doc} sizes={pdf.sizes} zoom={zoom} label={title} />
      <div className="fv-pdf-tools">
        <span className="fv-pdf-count">{count}</span>
        <ZoomButtons zoom={zoom} onZoom={setZoom} />
        <IconButton icon="expand" label="Expand" size="sm" quiet onClick={() => setFull(true)} />
      </div>
      <BaseDialog.Root open={full} onOpenChange={setFull}>
        <BaseDialog.Portal>
          <BaseDialog.Backdrop className="scrim kit-scrim" />
          <BaseDialog.Viewport className="scrim kit-viewport fv-pdf-viewport">
            <BaseDialog.Popup className="fv-pdf-full" aria-label={title}>
              <div className="fv-pdf-bar">
                <span>{title}</span>
                <span className="grow" />
                <span className="fv-pdf-count">{count}</span>
                <ZoomButtons zoom={zoom} onZoom={setZoom} />
                <a className="ib" href={url} download={name || true} aria-label="Download" title="Download">
                  <Icon name="download" />
                </a>
                <BaseDialog.Close render={<IconButton icon="close" label="Close" />} />
              </div>
              {full && <PdfPages doc={pdf.doc} sizes={pdf.sizes} zoom={zoom} label={`${title}, full window`} />}
            </BaseDialog.Popup>
          </BaseDialog.Viewport>
        </BaseDialog.Portal>
      </BaseDialog.Root>
    </div>
  );
}
