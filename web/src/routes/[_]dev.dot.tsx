import { createFileRoute } from '@tanstack/react-router';
import { lazy, Suspense } from 'react';

// Dev only: every state of the Dot. A production build drops the import, so the gallery never ships.
const DotGallery = import.meta.env.DEV ? lazy(() => import('@/tico/dot/DotGallery')) : () => null;

export const Route = createFileRoute('/_dev/dot')({
  component: () => (
    <Suspense fallback={null}>
      <DotGallery />
    </Suspense>
  ),
});
