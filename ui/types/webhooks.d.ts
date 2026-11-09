// The Settings > Webhooks part's global (ui/app/settings-webhooks.js), read by the TypeScript files through its accessor
// (ui/app/settings-webhooks-state.ts). The part is still JavaScript; it is typed when it converts.
// An incoming webhook is the server's summary of one trigger (backend/triggers.py `summary`). Only the fields the page
// reads are here.

interface WebhookTrigger {
  id: string;
  name: string;
  /** "webhook.<id>": the `on:` a routine listens with. */
  event: string;
  builtin: boolean;
  /** "none" or the preset the sender signs with. */
  preset: string;
  /** "automations" or "finance". When it is empty, the page goes by the preset. */
  consumer?: string | null;
  received_24h: number;
  refused_24h: number;
  /** The page's Gateway pill. The summary in backend/triggers.py does not send it. */
  relay?: boolean;
}
