// The Settings > Webhooks part's global WH_TRIGGERS (ui/app/settings-webhooks.ts), also read by the other TypeScript
// files through its accessor (ui/app/settings-webhooks-state.ts).
// An incoming webhook is the server's summary of one trigger (backend/triggers.py `summary`). Only the fields the page
// reads are here.

/** A trigger's Tico Gateway address (backend/hook_relay.py `public`), or null when it has none. */
interface WebhookTriggerRelay {
  address_prefix: string;
  created?: string;
  /** Whether a call waiting at the Gateway wakes a hosted server; null or missing is on. */
  wake?: boolean | null;
}

interface WebhookTrigger {
  id: string;
  name: string;
  /** "webhook.<id>": the `on:` a routine listens with. */
  event: string;
  /** The address a sender calls; '' for a built-in one. */
  url: string;
  builtin: boolean;
  created_by: string;
  last_received: string | null;
  /** Whether it has a signing secret. */
  signed: boolean;
  /** "none" or the preset the sender signs with. */
  preset: string;
  /** "automations" or "finance". When it is empty, the page goes by the preset. */
  consumer?: string | null;
  /** Where the event type is read: a header name or a /json/path; '' for none. */
  type_from: string;
  hourly: number;
  keep_days: number;
  /** What a call nobody listens to does: "log" or "note". */
  unheard: string;
  received_24h: number;
  refused_24h: number;
  /** Attached to each listed trigger (backend/hook_relay.py `attach`). */
  relay?: WebhookTriggerRelay | null;
}
