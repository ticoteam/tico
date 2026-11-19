import { createFileRoute } from '@tanstack/react-router';
import { lazy, Suspense } from 'react';
import { Sheet } from '@/shell';

// Dev only: every page kind. A production build drops the import, so the gallery never ships.
const Gallery = import.meta.env.DEV ? lazy(() => import('@/pagekinds/Gallery').then((m) => ({ default: m.Gallery }))) : () => null;

export const Route = createFileRoute('/_dev/kinds')({
  component: () => (
    // in the page's own sheet, which fills its place and scrolls
    <Sheet kind="page" label="Page kinds">
      <div className="page-body">
        <Suspense fallback={null}>
          <Gallery />
        </Suspense>
      </div>
    </Sheet>
  ),
});
