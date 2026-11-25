import { createFileRoute } from '@tanstack/react-router';
import { ProductsPage } from '@/features/company/exports';
import { loadProducts, panelSearch } from '@/features/company/routing';
import { pageSearch } from '@/lib';

export const Route = createFileRoute('/company/products')({
  validateSearch: panelSearch,
  loader: ({ context: { queryClient } }) => loadProducts(queryClient),
  component: Products,
});

function Products() {
  return <ProductsPage search={Route.useSearch({ select: (s) => pageSearch(s), structuralSharing: true })} />;
}
