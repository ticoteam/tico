import { createFileRoute } from '@tanstack/react-router';
import { lazy, Suspense } from 'react';

// Dev only: every page kind. A production build drops the import, so the gallery never ships.
const Gallery = import.meta.env.DEV ? lazy(() => import('@/pagekinds/Gallery').then((m) => ({ default: m.Gallery }))) : () => null;

export const Route = createFileRoute('/_dev/kinds')({
  component: () => (
    <Suspense fallback={null}>
      <Gallery />
    </Suspense>
  ),
});
