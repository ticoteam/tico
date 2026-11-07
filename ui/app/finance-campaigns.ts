// Ad campaigns under each ad account on a Google Ads or Meta Ads tool's page (tools-api.js taFinanceCard): the month's
// campaigns of that account with their spend, clicks, conversions and cost per conversion, and an owner picker. Blank
// is the account's owner. A change posts campaign_owner_set and the card reads its campaigns again. Phone width: the
// table scrolls in its container.

interface FcCampaign {
  id: string;
  name: string;
  ad_account: {id: string; name: string};
  owner: {team?: string; teammate?: string; inherited: boolean};
  month: {spend: number; clicks: number; conversions: number; per_conversion: number | null};
  as_of: string;
}

// The picker's value for a campaign's owner: a teammate or a team, or blank for the account's owner.
function fcOwnerValue(c: FcCampaign): string {
  const o = c.owner;
  if (o.inherited || (!o.team && !o.teammate)) return '';
  return o.teammate ? `teammate:${o.teammate}` : `team:${o.team}`;
}

// The campaign rows of one ad account, as one table row under it (none when the account has no campaign this month).
function finCampaignRows(accountId: string, campaigns: FcCampaign[], currency: string): string {
  const mine = campaigns.filter(c => c.ad_account.id === accountId);
  if (!mine.length) return '';
  const body = mine.map(c => `<tr data-fc-campaign="${esc(c.id)}">
      <td>${esc(c.name)}</td>
      <td class="tnum">${esc(tafMoney(c.month.spend, currency))}</td>
      <td class="tnum">${c.month.clicks.toLocaleString('en-US')}</td>
      <td class="tnum">${c.month.conversions.toLocaleString('en-US', {maximumFractionDigits: 2})}</td>
      <td class="tnum">${c.month.per_conversion == null ? '—' : esc(tafMoney(c.month.per_conversion, currency))}</td>
      <td><select data-fc-owner="${esc(c.id)}" aria-label="Campaign owner">${finShareOpts(fcOwnerValue(c), 'Account owner')}</select></td></tr>`).join('');
  return `<tr class="fc-campaigns"><td colspan="3"><div class="scroll"><table class="taf-table fc-table"><thead><tr><th>Campaign</th><th>Spend</th><th>Clicks</th><th>Conv.</th><th>Per conv.</th><th>Owner</th></tr></thead><tbody>${body}</tbody></table></div></td></tr>`;
}

// The owner picker's change: campaign_owner_set with the team or teammate picked (none: back to the account's owner).
// The card then reads its campaigns again, so the picker shows what was saved.
async function finCampaignOwnerChange(el: HTMLSelectElement): Promise<void> {
  const payload: Record<string, string> = {campaign_id: el.dataset.fcOwner ?? ''};
  if (el.value.startsWith('teammate:')) payload.teammate = el.value.slice('teammate:'.length);
  else if (el.value.startsWith('team:')) payload.team = el.value.slice('team:'.length);
  el.disabled = true;
  try { await finAct('campaign_owner_set', payload); }
  catch (e) { toast(e instanceof Error ? e.message : 'Not saved', true); }
  await taCampaignsReload();
}
