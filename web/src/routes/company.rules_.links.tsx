import { createFileRoute } from '@tanstack/react-router';
import { RuleLinks } from '@/features/company/exports';
import { loadRuleLinks } from '@/features/company/routing';

/* Rules › Links: each way an agent's writes leave the company, Enforced, Informed only or Not covered. */
export const Route = createFileRoute('/company/rules_/links')({
  loader: ({ context: { queryClient } }) => loadRuleLinks(queryClient),
  component: RuleLinks,
});
