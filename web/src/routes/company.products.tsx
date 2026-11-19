import { createFileRoute } from '@tanstack/react-router';
import { loadProducts, panelSearch, ProductsPage } from '@/features/company/exports';

export const Route = createFileRoute('/company/products')({
  validateSearch: panelSearch,
  loader: ({ context: { queryClient } }) => loadProducts(queryClient),
  component: Products,
});

function Products() {
  return <ProductsPage search={Route.useSearch()} />;
}
