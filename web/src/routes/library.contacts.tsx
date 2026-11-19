import { createFileRoute } from '@tanstack/react-router';
import { ContactsPage, contactsSearch, loadContacts } from '@/features/library/exports';

export const Route = createFileRoute('/library/contacts')({
  validateSearch: contactsSearch,
  loaderDeps: ({ search }) => ({ type: search.type, q: search.q, panel: search.panel }),
  loader: ({ deps, context: { queryClient } }) => loadContacts(queryClient, deps),
  component: Contacts,
});

function Contacts() {
  return <ContactsPage search={Route.useSearch()} />;
}
