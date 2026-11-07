// Types for the inbox kinds' pages under Tools (ui/app/inboxes.ts and one file a kind). Declarations only: no global is
// declared here, so the file that defines a global keeps the definition.

/** The signed-in person as the inbox pages read them: /api/me, plus the actor id a sign-in may carry. */
type InboxMe = SelfPerson & { actor?: string };

/** What an inbox kind's draw is given (inboxesCtx in ui/app/inboxes.ts). */
interface InboxCtx {
  admin: boolean;
  me: InboxMe | null;
  /** The draw's number; a newer draw makes this one stale. */
  run: number;
  /** False once another draw began or the page was left. */
  live: () => boolean;
  /** One load per draw, shared by every kind: a later caller gets the first caller's promise. */
  once: <T>(name: string, load: () => T | PromiseLike<T>) => Promise<T>;
  refresh: () => void;
  go: (view: string) => void;
}

/** The inbox a kind's detail() resolves with: every kind gives these four, plus its own fields. */
interface InboxResolved { kind: string; id: string; name: string; address: string; [field: string]: unknown; }
/** The Add tool item a kind lists under Tools; `admin` shows it to admins only. */
interface InboxAddItem { label: string; admin?: boolean; open: (ctx: InboxCtx) => unknown; }
/** One kind of inbox as inboxesKind is given it. */
interface InboxKindSpec {
  label: string;
  order: number;
  detail: (host: HTMLElement, id: string, ctx: InboxCtx) => Promise<InboxResolved | undefined>;
  /** Its Activity tab. It resolves nothing a panel needs (inboxesDetail returns before the panels), so undefined. */
  activity?: (host: HTMLElement, id: string, ctx: InboxCtx) => Promise<InboxResolved | undefined>;
  add?: InboxAddItem;
}
/** A kind as the registry (INBOX_KINDS) holds it. */
interface InboxKind extends InboxKindSpec { key: string; }
/** A page of its own at #/tools/<key> (inboxesView). */
interface InboxViewSpec { label: string; render: (host: HTMLElement, ctx: InboxCtx) => Promise<void>; }
interface InboxView extends InboxViewSpec { key: string; }
/** One card on Tools › Settings (inboxesSetting). */
/** `host` is `any` until inboxes-email.js is typed: its handler reads host.onchange as an Event with a nullable target, which
    would add findings to its baseline. Tighten it when that file is a .ts. */
interface InboxSettingSpec { section: string; order: number; render: (host: any, ctx: InboxCtx) => Promise<void>; }
interface InboxSetting extends InboxSettingSpec { key: string; }
/** One card added to another kind's page (inboxesPanel). */
interface InboxPanelSpec { kinds: string[]; order: number; render: (host: any, inbox: InboxResolved, ctx: InboxCtx) => Promise<void>; }
interface InboxPanel extends InboxPanelSpec { key: string; }
/** The arguments of inboxesHead. */
interface InboxHead { name: string; status: string; statusText?: string; sub?: string; actions?: string; }
/** One message automation (GET /v2/automations) as an inbox page reads it. */
interface InboxAutomation { id: string; name?: string | null; enabled?: boolean; trigger?: { type?: string; inbox?: string | null } | null; }
