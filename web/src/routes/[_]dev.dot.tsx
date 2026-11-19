import { createFileRoute } from '@tanstack/react-router';
import { lazy, Suspense } from 'react';
import { Sheet } from '@/shell';

// Dev only: every state of the Dot. A production build drops the import, so the gallery never ships.
const DotGallery = import.meta.env.DEV ? lazy(() => import('@/tico/dot/DotGallery')) : () => null;

export const Route = createFileRoute('/_dev/dot')({
  component: () => (
    // in the page's own sheet, which fills its place and scrolls
    <Sheet kind="page" label="The Dot">
      <div className="page-body">
        <Suspense fallback={null}>
          <DotGallery />
        </Suspense>
      </div>
    </Sheet>
  ),
});
