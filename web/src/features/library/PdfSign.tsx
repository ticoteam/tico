import { Dialog as BaseDialog } from '@base-ui/react/dialog';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { ApiError } from '@/api/client';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { IconButton } from '@/kit/IconButton';
import { toast } from '@/kit/Toast';
import type { PdfDoc } from './pdf';
import { PdfPages, ZoomButtons, type PageSize } from './PdfView';
import { saveSignature, signatureQuery, signFile, versionBytes } from './queries';
import { burnMarks, TEXT_FILL, today, type Mark } from './signing';

/* Sign mode over a PDF's pages: the person adds their signature (drawn once, kept for next time), a line of text or
   today's date, each placed with a tap on a page, dragged to move and sized by its corner. Save writes them into the
   PDF for good as the file's next version; Cancel or Escape leaves the file as it was. */

export interface SignTarget {
  /** The Tico file and the version shown, which the marks are drawn on. */
  file: string;
  version: number;
  /** The file's name, which the signed version keeps. */
  name: string;
}

type Tool = Mark['kind'];

/** A mark's first width, as a share of the page's width; its height follows. */
const FIRST_WIDTH: Record<Tool, number> = { signature: 0.3, text: 0.35, date: 0.25 };
/** A line of text or a date is this share of the page's height (about 14 pt on a letter page). */
const LINE = 0.026;

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), Math.max(lo, hi));
let seq = 0;

/** The signature pad's drawing cut to the ink, so a placed signature is as big as what was drawn. */
function inked(canvas: HTMLCanvasElement): string {
  const whole = canvas.toDataURL('image/png');
  const ctx = canvas.getContext('2d');
  if (!ctx || !canvas.width || !canvas.height) return whole;
  const { data, width, height } = ctx.getImageData(0, 0, canvas.width, canvas.height);
  let top = height;
  let left = width;
  let right = -1;
  let bottom = -1;
  for (let y = 0; y < height; y++)
    for (let x = 0; x < width; x++)
      if ((data[(y * width + x) * 4 + 3] ?? 0) > 0) {
        if (x < left) left = x;
        if (x > right) right = x;
        if (y < top) top = y;
        if (y > bottom) bottom = y;
      }
  if (right < 0) return whole;
  const pad = 4;
  const cut = document.createElement('canvas');
  cut.width = right - left + 1 + 2 * pad;
  cut.height = bottom - top + 1 + 2 * pad;
  cut.getContext('2d')?.drawImage(canvas, left, top, right - left + 1, bottom - top + 1, pad, pad, right - left + 1, bottom - top + 1);
  return cut.toDataURL('image/png');
}

/** Draw the signature with a mouse, a pen or a finger; Save keeps it on the person's profile for every PDF after. */
function SignatureDraw({ open, onOpenChange, onSaved }: { open: boolean; onOpenChange: (open: boolean) => void; onSaved: () => void }) {
  const [canvas, setCanvas] = useState<HTMLCanvasElement | null>(null);
  const pad = useRef<{ isEmpty: () => boolean; clear: () => void; off: () => void } | null>(null);
  const [empty, setEmpty] = useState(true);
  useEffect(() => {
    if (!canvas) return;
    let live = true;
    void import('signature_pad').then(({ default: SignaturePad }) => {
      if (!live) return;
      const ratio = Math.max(window.devicePixelRatio || 1, 1);
      canvas.width = canvas.offsetWidth * ratio;
      canvas.height = canvas.offsetHeight * ratio;
      canvas.getContext('2d')?.scale(ratio, ratio);
      const p = new SignaturePad(canvas, { penColor: 'rgb(0, 0, 0)', minWidth: 0.8, maxWidth: 2.6 });
      p.addEventListener('endStroke', () => setEmpty(p.isEmpty()));
      pad.current = p;
    });
    return () => {
      live = false;
      pad.current?.off();
      pad.current = null;
    };
  }, [canvas]);
  const clear = () => {
    pad.current?.clear();
    setEmpty(true);
  };
  const save = async () => {
    if (!canvas || !pad.current || pad.current.isEmpty()) return;
    await saveSignature(inked(canvas));
    onSaved();
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Draw your signature"
      className="sg-draw"
      actions={
        <>
          <Button onClick={clear} disabled={empty}>
            Clear
          </Button>
          <Button variant="primary" onClick={save} disabled={empty}>
            Save
          </Button>
        </>
      }
    >
      <canvas ref={setCanvas} className="sg-pad" aria-label="Signature pad" />
    </Dialog>
  );
}

/** One mark on its page: dragged by its body, sized by its corner, removed by its close or Delete. */
function MarkBox({
  m,
  shown,
  on,
  signature,
  onSelect,
  onChange,
  onRemove,
}: {
  m: Mark;
  shown: { width: number; height: number };
  on: boolean;
  signature: string | undefined;
  onSelect: () => void;
  onChange: (m: Mark) => void;
  onRemove: () => void;
}) {
  const field = useRef<HTMLInputElement>(null);
  const drag = useRef<{ x: number; y: number; from: Mark; size: boolean; moved: boolean } | null>(null);
  const down = (e: PointerEvent<HTMLElement>, size: boolean) => {
    // Inside the text of the selected mark, the pointer edits it.
    if (!size && on && e.target === field.current) return;
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, from: m, size, moved: false };
    onSelect();
  };
  const move = (e: PointerEvent<HTMLElement>) => {
    const d = drag.current;
    if (!d || !shown.width || !shown.height) return;
    if (!d.moved && Math.hypot(e.clientX - d.x, e.clientY - d.y) < 3) return;
    d.moved = true;
    const dx = (e.clientX - d.x) / shown.width;
    const dy = (e.clientY - d.y) / shown.height;
    const f = d.from;
    if (!d.size) return onChange({ ...f, x: clamp(f.x + dx, 0, 1 - f.w), y: clamp(f.y + dy, 0, 1 - f.h) });
    // The corner keeps the mark's shape: a signature as drawn, a line of text as long as it was for its height.
    const w = clamp(f.w + dx, 0.04, 1 - f.x);
    onChange({ ...f, w, h: Math.min((f.h * w) / f.w, 1 - f.y) });
  };
  const up = () => {
    const d = drag.current;
    drag.current = null;
    if (d && !d.moved && !d.size) field.current?.focus();
  };
  const key = (e: KeyboardEvent<HTMLDivElement>) => {
    if ((e.key === 'Delete' || e.key === 'Backspace') && e.target === e.currentTarget) {
      e.preventDefault();
      onRemove();
    }
  };
  const tall = m.h * shown.height;
  const label = m.kind === 'signature' ? 'Signature' : m.kind === 'date' ? 'Date' : 'Text';
  return (
    <div
      className={on ? 'sg-mark is-on' : 'sg-mark'}
      role="group"
      aria-label={label}
      tabIndex={0}
      onKeyDown={key}
      style={{ left: m.x * shown.width, top: m.y * shown.height, width: m.w * shown.width, height: tall }}
      onPointerDown={(e) => down(e, false)}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={up}
    >
      {m.kind === 'signature' ? (
        signature && <img src={signature} alt="" draggable={false} />
      ) : (
        <input
          ref={field}
          value={m.text}
          aria-label={label}
          placeholder={label}
          onChange={(e) => onChange({ ...m, text: e.target.value })}
          // Helvetica at TEXT_FILL of a line as tall as the box puts the baseline at about BASELINE of it, where the PDF
          // writes it (signing.ts).
          style={{ fontSize: tall * TEXT_FILL, lineHeight: `${tall}px`, fontFamily: 'Helvetica, Arial, sans-serif' }}
        />
      )}
      {on && (
        <>
          <IconButton icon="close" label="Remove" size="sm" className="sg-remove" onPointerDown={(e) => e.stopPropagation()} onClick={onRemove} />
          <span className="sg-size" aria-hidden="true" onPointerDown={(e) => down(e, true)} onPointerMove={move} onPointerUp={up} onPointerCancel={up} />
        </>
      )}
    </div>
  );
}

/** Sign mode: the PDF's pages over the whole window with the person's marks over them. `onClose` gets the version the
    signing made, or nothing when it was cancelled. */
export function PdfSign({
  doc,
  sizes,
  title,
  target,
  onClose,
}: {
  doc: PdfDoc;
  sizes: PageSize[];
  title: string;
  target: SignTarget;
  onClose: (version?: number) => void;
}) {
  const saved = useQuery(signatureQuery);
  const signature = saved.data?.image;
  const [marks, setMarks] = useState<Mark[]>([]);
  const [tool, setTool] = useState<Tool | null>(null);
  const [on, setOn] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [drawing, setDrawing] = useState(false);
  const [aspect, setAspect] = useState(0.35);
  useEffect(() => {
    if (!signature) return;
    const img = new Image();
    img.onload = () => img.naturalWidth && setAspect(img.naturalHeight / img.naturalWidth);
    img.src = signature;
  }, [signature]);

  const arm = (t: Tool) => {
    if (t === 'signature' && !signature) return setDrawing(true);
    setTool(tool === t ? null : t);
  };
  const place = (n: number, shown: { width: number; height: number }) => (e: PointerEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    if (!tool || !shown.width || !shown.height) return setOn(null);
    const box = e.currentTarget.getBoundingClientRect();
    const w = FIRST_WIDTH[tool];
    const h = tool === 'signature' ? (w * shown.width * aspect) / shown.height : LINE;
    const x = clamp((e.clientX - box.left) / shown.width - w / 2, 0, 1 - w);
    const y = clamp((e.clientY - box.top) / shown.height - h / 2, 0, 1 - h);
    const m: Mark = { id: `m${++seq}`, kind: tool, page: n, x, y, w, h, text: tool === 'date' ? today() : '' };
    setMarks((all) => [...all, m]);
    setOn(m.id);
    setTool(null);
  };
  const change = (m: Mark) => setMarks((all) => all.map((x) => (x.id === m.id ? m : x)));
  const remove = (id: string) => setMarks((all) => all.filter((x) => x.id !== id));

  const save = async () => {
    try {
      const png = signature ? Uint8Array.from(atob(signature.split(',')[1] ?? ''), (c) => c.charCodeAt(0)) : null;
      const signed = await burnMarks(await versionBytes(target.file, target.version), marks, png);
      const answer = await signFile(target.file, target.version, target.name, signed, marks);
      toast({ text: 'Signed', name: title });
      onClose(answer.version);
    } catch (err) {
      const why = err instanceof ApiError ? err.message : 'The signed PDF could not be made';
      toast({ text: 'Not signed', name: why, kind: 'failed' });
    }
  };

  const placing = tool === 'signature' ? 'Tap the page to place your signature' : tool ? `Tap the page to place the ${tool}` : '';
  return (
    <>
      <BaseDialog.Root open onOpenChange={(open) => !open && onClose()}>
        <BaseDialog.Portal>
          <BaseDialog.Backdrop className="scrim kit-scrim" />
          <BaseDialog.Viewport className="scrim kit-viewport fv-pdf-viewport">
            <BaseDialog.Popup className="fv-pdf-full sg" aria-label={`Sign ${title}`}>
              <div className="fv-pdf-bar sg-bar">
                <span>{title}</span>
                <span className="grow" />
                {placing && <span className="sg-hint">{placing}</span>}
                <Button variant="quiet" icon="plus" className={tool === 'signature' ? 'is-on' : undefined} aria-pressed={tool === 'signature'} onClick={() => arm('signature')}>
                  Signature
                </Button>
                {signature && <IconButton icon="edit" label="Redraw signature" quiet onClick={() => setDrawing(true)} />}
                <Button variant="quiet" icon="plus" className={tool === 'text' ? 'is-on' : undefined} aria-pressed={tool === 'text'} onClick={() => arm('text')}>
                  Text
                </Button>
                <Button variant="quiet" icon="plus" className={tool === 'date' ? 'is-on' : undefined} aria-pressed={tool === 'date'} onClick={() => arm('date')}>
                  Date
                </Button>
                <ZoomButtons zoom={zoom} onZoom={setZoom} />
                <Button onClick={() => onClose()}>Cancel</Button>
                <Button variant="primary" onClick={save} disabled={!marks.length}>
                  Save
                </Button>
              </div>
              <PdfPages
                doc={doc}
                sizes={sizes}
                zoom={zoom}
                label={`${title}, signing`}
                overlay={(n, shown) => (
                  <div className={tool ? 'sg-layer is-armed' : 'sg-layer'} data-page={n} onPointerDown={place(n, shown)}>
                    {marks
                      .filter((m) => m.page === n)
                      .map((m) => (
                        <MarkBox key={m.id} m={m} shown={shown} on={on === m.id} signature={signature} onSelect={() => setOn(m.id)} onChange={change} onRemove={() => remove(m.id)} />
                      ))}
                  </div>
                )}
              />
            </BaseDialog.Popup>
          </BaseDialog.Viewport>
        </BaseDialog.Portal>
      </BaseDialog.Root>
      <SignatureDraw
        open={drawing}
        onOpenChange={setDrawing}
        onSaved={() => {
          setDrawing(false);
          setTool('signature');
        }}
      />
    </>
  );
}
