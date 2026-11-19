import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Section } from '@/pagekinds';
import { Verb, act } from './parts';
import { allTokensQuery, tokensQuery, type CompanyToken } from './queries';

/** How long a new token works (POST /api/v2/me/tokens takes 1 to 365 days; 90 when not said). */
export const EXPIRES = [
  { value: '7', label: '7 days' },
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
  { value: '365', label: '1 year' },
] as const;

const day = (at: string) => new Date(at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

/** Everyone else's live tokens, for the owner and the admins: whose each is, and Revoke (asked first; it is final). */
export function AllTokens() {
  const all = useQuery({ ...allTokensQuery, retry: false });
  const mine = useQuery(tokensQuery);
  const [revoking, setRevoking] = useState<CompanyToken | null>(null);
  if (!Array.isArray(all.data?.tokens)) return null;
  const own = new Set((mine.data?.tokens ?? []).map((t) => t.id));
  const live = all.data.tokens.filter((t) => !t.revoked_at && !own.has(t.id));
  if (live.length === 0) return null;
  return (
    <Section name="Everyone's tokens" count={live.length} defaultOpen={false} target="setting:all-tokens">
      <div className="tbl-head g-tok" aria-hidden="true">
        <span>Name</span>
        <span>Person</span>
        <span>Last used</span>
        <span />
      </div>
      {live.map((t) => (
        <div key={t.id} className="row g-tok" data-target={`token:${t.id}`} title={t.label}>
          <span className="row-title">{t.label}</span>
          <span className="row-cell">{t.name ?? t.email ?? ''}</span>
          <span className="row-cell">{t.last_used_at ? day(t.last_used_at) : 'Never'}</span>
          <span className="hl-act">
            <Verb quiet onClick={() => setRevoking(t)}>
              Revoke
            </Verb>
          </span>
        </div>
      ))}
      <Dialog
        open={revoking != null}
        onOpenChange={(o) => !o && setRevoking(null)}
        alert
        title={`Revoke ${revoking?.label ?? 'token'}?`}
        description={`Anything using it stops at once${revoking?.name ? `, for ${revoking.name}` : ''}. This can't be undone.`}
        actions={
          <>
            <Button variant="quiet" onClick={() => setRevoking(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                if (revoking) void act('settings.revokeAnyToken', { token: revoking.id });
                setRevoking(null);
              }}
            >
              Revoke
            </Button>
          </>
        }
      />
    </Section>
  );
}
