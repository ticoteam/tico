import { ApiError, api } from '@/api/client';

let off = false;

/** Records a page the person opened (POST /api/v2/page-history). Quiet: a refusal (a server without it answers 404
    or 405) stops the calls for this load, and any other failure is dropped. */
export function recordVisit(page: string, title?: string): void {
  if (off) return;
  api.post('/api/v2/page-history', title ? { page, title } : { page }).catch((e: unknown) => {
    if (e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 429) off = true;
  });
}
