import { createFileRoute } from '@tanstack/react-router';
import { ContactsPage } from '@/features/library/exports';
import { contactsSearch, loadContacts } from '@/features/library/routing';

export const Route = createFileRoute('/library/contacts')({
  validateSearch: contactsSearch,
  loaderDeps: ({ search }) => ({ type: search.type, q: search.q, link: search.link, panel: search.panel }),
  loader: ({ deps, context: { queryClient } }) => loadContacts(queryClient, deps),
  component: Contacts,
});

function Contacts() {
  return <ContactsPage search={Route.useSearch()} />;
}
