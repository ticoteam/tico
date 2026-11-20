import type { PDFDocumentLoadingTask } from 'pdfjs-dist';
import workerSrc from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

/** A PDF read by PDF.js: its page count and each page's drawing. PDF.js itself loads with the first PDF opened. */
export type PdfDoc = Awaited<PDFDocumentLoadingTask['promise']>;

/** Opens the PDF at `src` (its address or a blob: URL) in PDF.js's worker, from this app's own files. `destroy` stops
    the read and frees the worker's copy. */
export async function openPdf(src: string): Promise<{ doc: Promise<PdfDoc>; destroy: () => Promise<void> }> {
  const pdfjs = await import('pdfjs-dist');
  pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;
  const task = pdfjs.getDocument({ url: src.split('#')[0] ?? src });
  return { doc: task.promise, destroy: () => task.destroy() };
}
